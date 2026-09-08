# Development Log: AI Meeting Intelligence Agent (Version 1)

A chronological record of what was built, what broke during real-world
testing, and how each issue was fixed. Kept for context if you (or someone
else) needs to understand *why* the code looks the way it does.

## 1. Initial build

Built Version 1 per the product specification: transcript upload → Intent
Classification Agent → CRM Agent → PMO Agent → human Review screen → Excel
export. The **Dashboard** feature (spec'd for Version 2) was pulled forward
and included from the start, per request.

Stack: Streamlit (UI), local Ollama LLM (no external API calls), Pydantic
(schema validation), openpyxl/pandas (Excel storage), python-docx (Word
transcript parsing), Plotly (dashboard charts).

## 2. Transcript parsing bugs found during self-testing

Before handing anything over, three sample transcripts were built to
exercise `.docx`, `.vtt`, and `.txt` inputs. This surfaced a real bug
immediately:

- **Inline labels misread as speakers.** The speaker-detection regex was
  too permissive — a sentence like *"Good to know. Action item: David to
  follow up..."* was parsed as a new speaker named "Good to know. Action
  item". **Fix:** tightened the regex to require a proper-name shape (1–4
  capitalized words) before treating a line as a new speaker.

## 3. Dashboard NaN-filtering bug

Found while testing the dashboard with sample data: a meeting with 1 risk
and 1 action item was being displayed as 2 risk rows and 2 action rows.
**Root cause:** `.astype(str)` on a pandas column containing real `NaN`
doesn't always convert it to the string `"nan"` — it can stay a float `NaN`,
which then fails string comparisons silently. **Fix:** switched to
`.fillna("")` before string operations everywhere in `dashboard.py`.

## 4. Environment/setup friction (Windows)

A series of local setup issues, each diagnosed and resolved:

- `streamlit` not recognized as a command → PATH issue; pip installed it to
  a `Scripts` folder not on PATH. Fix: use `python -m streamlit run app.py`,
  or add the Scripts folder to PATH.
- Confusion between `ollama run <model>` (interactive chat client) and
  `ollama serve` (the background HTTP service the app actually talks to).
  Clarified that on Windows, Ollama runs as an auto-starting background
  service — neither command needs to be run manually in the common case.
- `ReadTimeout` errors in `data/app.log` — the model was reachable but
  slower than the original 120-second timeout on CPU-only hardware. Fixed
  by raising the default timeout to 600s and giving timeouts their own,
  clearer error message (previously it incorrectly suggested "Ollama isn't
  running" for what was actually a slow-response situation).

## 5. Real Microsoft Teams `.vtt` format support

The original `.vtt` parser assumed a generic `Speaker: text` format. Real
Teams exports look different:

```
879ed542-0509-481d-a2b7-3a928cd25fd2/6-0
00:00:12.536 --> 00:00:13.696
<v Angel Palacios>Sorry, let me share my screen.</v>
```

**Fix:** added support for `<v Speaker Name>text</v>` voice tags and UUID
cue-ID lines. Since this format carries no role information at all, a
**role resolution** step was added (see §7 for how this evolved).

Later, three more real-world `.vtt` quirks surfaced during testing and were
each fixed:

- **UTF-8 BOM** at the start of the file (common on Windows-exported files)
  broke the `WEBVTT` header check, causing a phantom `"Unknown"` speaker.
  Fixed by decoding with `utf-8-sig` instead of `utf-8`.
- **`NOTE` comment blocks** (multi-line metadata some exports include)
  weren't filtered and also produced a phantom `"Unknown"` speaker. Fixed
  by skipping `NOTE`/`STYLE`/`REGION` blocks until the next blank line.
- **`Kind: captions` / `Language: en-us` header lines** right after
  `WEBVTT` were being misread as two fake speakers named "Kind" and
  "Language". Fixed with an explicit metadata-header filter.

## 6. Pydantic shape-mismatch crashes

Twice, the local model returned data in a different *shape* than requested
— e.g. putting action-item-like objects into the `decisions` field (which
expects plain strings), causing an unhandled `pydantic.ValidationError` and
crashing the Review screen.

**Fix:** added "schema-repair" validators (`field_validator(mode="before")`)
directly on the Pydantic models in `models.py`. If a string field receives
an object, it's flattened into a readable string instead of crashing; if an
object field receives a plain string, it's wrapped into a minimal object.
This makes the models self-healing regardless of which agent calls them.

## 7. Role inference: LLM-based → deterministic

**Original approach:** ask the local LLM to guess each speaker's role
(Customer / PM / Developer / etc.) from conversation tone.

**Problem found:** it reliably returned `"Unknown"` for every participant,
regardless of transcript content — the model wasn't conditioning on content
at all.

**Fix:** replaced with `team_roster.json` — a simple, configurable
name → role lookup (case/whitespace-insensitive), with any unmatched name
defaulting to `"Customer"`, exactly as requested. Instant, deterministic,
and fully transparent. A **Team Roster** tab was added to the app for
managing this without hand-editing JSON. The original LLM-based version is
kept in the code as `infer_roles_llm()` for reference, but isn't called by
default.

## 8. Streamlit runtime errors

- `StreamlitDuplicateElementId` (radio, then file_uploader) — added
  explicit `key=` arguments to every interactive widget. When the *same*
  error kept recurring across different widget types with no crash the
  second time (just doubled UI), the real root cause was identified:
  **duplicated code in `app.py`** from an incomplete file replacement
  during a copy-paste. Confirmed via line counts and duplicate function
  definitions, then fixed by replacing the file cleanly.
- `AttributeError: module 'role_inference' has no attribute 'infer_roles'`
  — traced to the local file not actually containing the function (a
  partial/failed save), confirmed via line count and `grep` for the
  function definition, then fixed by re-saving the full file.

## 9. "Model output did not contain a JSON object"

The local model was occasionally returning no JSON at all — likely
conversational preamble instead of the requested structure.

**Fix (first pass):** enabled Ollama's `format: "json"` mode (constrains
sampling to valid JSON syntax) and added stripping of `<think>...</think>`
reasoning blocks some models emit before their answer. Also added an
opt-in `DEBUG_LOG_RAW_LLM_OUTPUT` flag to log a truncated preview of raw
model output for future diagnosis, without doing so by default (privacy).

**Finding this didn't fully solve:** `format: "json"` only guarantees
*syntactically valid* JSON — it does **not** guarantee the model uses your
requested property names. The PMO agent was observed returning a
completely different structure (grouped by speaker role: `{"Project
Manager": {...}, "Developer": {...}}`) instead of the requested
`{project, decisions, risks, ...}` shape, despite being valid JSON.

**Fix (second pass):** switched to Ollama's **structured outputs** mode —
passing a full JSON Schema object (not just the string `"json"`) via the
`format` field. This constrains the model's *property names and types*,
not just JSON syntax. Applied to the intent classifier, CRM agent, and PMO
agent, each with an explicit schema matching its Pydantic model.

## 10. Intent classification: LLM-based → deterministic (again)

Even with structured outputs (§9) constraining the *shape*, the intent
classifier's *content* was still unreliable: it was observed returning
`CRM: 0.8` as a fixed value regardless of transcript content, including on
clearly PMO-only meetings. Prompt engineering (very explicit scoring
rubrics, evidence requirements) did not fix it — the model simply wasn't
conditioning on the transcript.

**Fix:** applied the same philosophy as the role-inference fix (§7):
replaced LLM-based intent classification with **deterministic keyword
scoring** against a configurable `intent_keywords.json`. Match counts map
to confidence tiers (0 matches → 0%, 1 → 30%, 3+ → 50%, 6+ → 70%, 10+ →
90%). No LLM call — instant, transparent, and physically cannot produce the
same score regardless of input. The LLM-based version is kept as
`classify_llm()` for reference, not called by default.

Two refinements followed from continued testing:

- **Role-label contamination:** the text fed to the classifier included
  formatted labels like `"Daniel Jorge (Customer): ..."` for every turn.
  Since `"Customer"` was a CRM keyword, a customer who simply spoke often
  inflated the CRM score regardless of what they actually said — a support/
  bug-testing call was being scored as CRM-heavy purely because a customer
  participant had many turns. **Fix:** added `extract_spoken_text()`,
  which strips speaker names and role labels before scoring, so only actual
  spoken content is counted. (Role labels are still included in the text
  sent to the CRM/PMO *extraction* agents, where knowing who's who
  genuinely helps.)

- **Weighting and vocabulary gaps:** a bug-reproduction/testing transcript
  scored a 30%/30% tie because each side matched only one keyword, and the
  PMO keyword list was too thin for testing/debugging vocabulary (no
  entries for "console output", "reproduce the issue", "expected behavior",
  etc.). **Fix:** matches are now weighted by phrase word-count (a 3-word
  phrase like "steps to reproduce" counts 3x a single generic word), and
  both keyword lists — especially PMO's testing/debugging vocabulary — were
  substantially expanded.

## 11. Scope simplification

Per request, the intent taxonomy was simplified from five categories (CRM,
PMO, AGILE_REQUIREMENT, SUPPORT, MANAGEMENT) down to two (CRM, PMO), with
PMO's definition absorbing what would have been the other three categories.
This only touched the classifier's schema/keyword categories — no other
files needed changes, since `config.py`'s supported-workflow list and the
app's routing logic already only handled CRM/PMO.

## Summary: what changed from the original design, and why

| Subsystem | Original design | Final design | Why |
|---|---|---|---|
| Role resolution | LLM guesses role from tone | `team_roster.json` lookup, default Customer | LLM always returned "Unknown" |
| Intent classification | LLM scores CRM/PMO confidence | Keyword scoring, `intent_keywords.json` | LLM fixated on one score regardless of content |
| CRM/PMO extraction JSON shape | Prompt instructions only | Ollama JSON Schema (structured outputs) | Prompt instructions alone let the model invent its own structure |
| Transcript speaker detection | Simple regex | Proper-name-shaped regex + Teams `<v>` tag support + BOM/NOTE/metadata filtering | Real transcripts have more format variance than initially assumed |

The throughline across nearly every fix: **local LLMs are good at
open-ended extraction (summarizing what happened) but unreliable at
fine-grained scoring/classification decisions.** Wherever a decision could
be made deterministically (which role, which category) instead of
generatively (what did they discuss), the deterministic approach turned out
to be both more reliable and easier to debug/tune — the same lesson applied
twice, independently, to two different subsystems.
