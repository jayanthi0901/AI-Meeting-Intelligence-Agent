# AI Meeting Intelligence Agent — Version 1

Converts meeting transcripts into structured CRM and PMO business artifacts
using a locally-run LLM. Built from the product specification's Version 1
scope, with the **Dashboard** and **Team Roster** features (originally
Version 2 or later) pulled forward as requested, and hardened through
real-world testing against actual Microsoft Teams transcripts.

## What's in Version 1

- Upload a Teams transcript (`.docx`, `.vtt`, `.txt`) or paste transcript text
- Native Microsoft Teams `.vtt` export support (`<v Speaker>` tags, UUID cue IDs)
- Intent Classification (routes to CRM / PMO) — **keyword-based**, not LLM-based (see Design Notes)
- Team Roster — configure known team members' roles; unmatched names default to Customer
- CRM Intelligence Agent
- PMO Intelligence Agent
- Human Review screen (edit, approve, reject)
- Export to Excel (`data/CRM.xlsx`, `data/PMO.xlsx`)
- Executive Dashboard (metrics + charts)

Not included in this version (per the spec's Version 2 / Version 3 scope):
the Agile Requirements Agent, PDF support, search over historical meetings,
and any external integrations (Microsoft Graph, Jira, Salesforce, etc.).

## Architecture

```
Frontend (Streamlit)
      |
Backend (Python)
      |
Agent Orchestrator (app.py)
      |
 +---------------------+------------------------+
 |                      |                        |
Transcript Parser   Intent Classifier      Role Resolution
(.docx/.vtt/.txt)   (keyword scoring,      (team_roster.json
                     no LLM call)           lookup, no LLM call)
                          |
                 +--------+--------+
                 |                 |
             CRM Agent         PMO Agent
             (local LLM,       (local LLM,
              JSON Schema       JSON Schema
              structured        structured
              output)           output)
                 |                 |
                 +--------+--------+
                          |
              Human Review (edit/approve/reject)
                          |
              Storage Layer (Excel + JSON)
                          |
              Dashboard (Plotly)
```

**Design note:** Intent classification and role resolution do **not** call
the LLM, by design — see "Why keyword/roster-based, not LLM-based?" below.
Only the CRM and PMO extraction agents call the local model.

## Prerequisites

1. **Python 3.10+**
2. **Ollama**, running locally, with a model pulled:
   ```bash
   # Install Ollama: https://ollama.com/download
   ollama pull qwen2.5
   ```
   On Windows/Mac, Ollama normally runs as a background service automatically
   after installation — you generally do **not** need to run `ollama serve`
   yourself. Confirm it's running with:
   ```powershell
   Invoke-RestMethod -Uri http://localhost:11434
   ```
   This should print `Ollama is running`. If not, launch "Ollama" from your
   Start Menu / Applications once to start the background service.

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```
(If `streamlit` isn't recognized as a command, use `python -m streamlit run app.py` instead.)

The app opens in your browser. Use the sidebar to move between
**Upload & Process**, **Review**, **Dashboard**, and **Team Roster**.

## Configuring your team roster

Native Teams `.vtt` exports only include speaker names, not roles. Rather
than guess roles from conversation tone (unreliable — see Design Notes),
roles are resolved from `team_roster.json`, editable either directly or via
the app's **Team Roster** tab:

```json
{
  "Project Manager": ["Jayanthi Palanisamy"],
  "Developer": ["Angel Palacios", "Priya Nandan"],
  "Sales Representative": ["Sarah Mitchell"]
}
```

Any transcript participant **not** listed here is assumed to be a
**Customer**. Matching is case-insensitive and whitespace-tolerant.

## Tuning intent classification

CRM vs. PMO routing is decided by keyword matching against
`intent_keywords.json`, not by asking the LLM. If a meeting is being
mis-routed, add a phrase or two for the vocabulary your meetings actually
use — no code changes needed. Multi-word phrases (e.g. `"acceptance
criteria"`) count more heavily than generic single words (e.g.
`"customer"`), since they're a more specific signal.

## Configuration

Environment variables (all optional, see `config.py`):

| Variable                      | Default                    | Purpose                                          |
|--------------------------------|-----------------------------|---------------------------------------------------|
| `OLLAMA_HOST`                  | `http://localhost:11434`   | Ollama server address                              |
| `OLLAMA_MODEL`                 | `qwen2.5`                  | Model used for CRM/PMO extraction                  |
| `LLM_REQUEST_TIMEOUT_SECONDS`  | `600`                      | Max wait per LLM call (raise for slower/CPU-only hardware) |
| `DEBUG_LOG_RAW_LLM_OUTPUT`     | `false`                    | Log a preview of raw model output to `data/app.log` for troubleshooting bad JSON |
| `TEAM_ROSTER_PATH`             | `team_roster.json`         | Path to the team roster config file                |
| `INTENT_KEYWORDS_PATH`         | `intent_keywords.json`     | Path to the intent-scoring keyword config file      |

## Performance tips (especially on low-end / CPU-only hardware)

- You do **not** need `ollama run <model>` open in a terminal — that's just
  an interactive chat client. Only the background Ollama service
  (`ollama serve`, usually automatic) needs to be running; the app talks to
  it over HTTP.
- A 7B model on CPU-only hardware can take 1–5+ minutes per call. This is
  expected. `LLM_REQUEST_TIMEOUT_SECONDS` defaults to 600s (10 min) to
  accommodate this.
- Consider a smaller model on weak hardware: `ollama pull qwen2.5:3b`, then
  set `OLLAMA_MODEL=qwen2.5:3b`.
- Set `OLLAMA_KEEP_ALIVE=30m` (environment variable, before starting Ollama)
  to avoid the model being unloaded from RAM between the CRM and PMO calls.

## Design notes: why keyword/roster-based, not LLM-based?

Two subsystems were originally LLM-based and were **deliberately replaced**
with deterministic logic after real-world testing showed the local model
was unreliable for these specific decisions:

1. **Role inference** — asking the LLM to guess each speaker's role from
   conversation tone consistently returned "Unknown" for everyone,
   regardless of transcript content. Replaced with `team_roster.json`, a
   simple configured lookup (unmatched names default to Customer).

2. **Intent classification (CRM vs. PMO)** — even with a JSON Schema
   forcing the correct output shape, the local model would "fixate" and
   return the same confidence score regardless of transcript content (e.g.
   CRM at a fixed 0.8 on every meeting, including clearly PMO-only ones).
   Replaced with `intent_keywords.json`, a transparent, tunable keyword
   scorer with no LLM call — instant, deterministic, and content-sensitive
   by construction.

**CRM and PMO extraction still use the local LLM** (open-ended
summarization/extraction genuinely needs a language model), but with
Ollama's structured-outputs mode (a JSON Schema passed via the `format`
field) to constrain the model to the correct property names — plain
`format: "json"` alone only guarantees valid JSON *syntax*, not that the
model uses your requested field names.

If your model is powerful enough to condition reliably on transcript
content, the original LLM-based implementations are kept in the codebase
(`intent_classifier.classify_llm()`, `role_inference.infer_roles_llm()`)
for reference/optional use — they are just not called by default.

## Security notes (per spec section 15)

- All inference runs against a local Ollama server — no transcript content
  is sent to any third-party API.
- No API keys are required.
- Intent classification and role resolution never call the LLM at all (see
  Design Notes) — they're pure local text/file operations.
- Application logs (`data/app.log`) record only metadata (e.g. meeting IDs,
  timestamps, keyword match counts) — never transcript or customer content,
  *unless* `DEBUG_LOG_RAW_LLM_OUTPUT` is explicitly turned on for
  troubleshooting, in which case a short preview of raw model output may be
  logged locally.
- Nothing is written to Excel/JSON storage until a human explicitly clicks
  **Approve** on the Review screen.

## Project layout

```
app.py                   Streamlit UI + orchestration
config.py                Settings (LLM, storage paths, thresholds, roster/keyword paths)
models.py                Pydantic schemas (Intent, CRM, PMO, Business Object) + schema-repair validators
llm_client.py            Ollama wrapper: structured-outputs (JSON Schema), timeouts, JSON extraction
transcript_processor.py  Parses .docx / .vtt / .txt / pasted text (native Teams .vtt supported)
intent_classifier.py     Keyword-based CRM/PMO scoring (LLM version kept as classify_llm, unused)
role_inference.py        Roster-based role resolution (LLM version kept as infer_roles_llm, unused)
team_roster.json         Editable: known team members -> role
intent_keywords.json     Editable: CRM/PMO scoring keyword lists
agents/
  crm_agent.py           CRM Intelligence Agent (LLM + JSON Schema)
  pmo_agent.py           PMO Intelligence Agent (LLM + JSON Schema)
storage.py               Excel + JSON storage layer
dashboard.py             Executive Dashboard (Plotly charts)
data/                    Generated at runtime: CRM.xlsx, PMO.xlsx, app.log, etc.
```

## Known limitations (MVP)

- Keyword-based intent classification is a heuristic, not a semantic
  understanding of the meeting — tune `intent_keywords.json` if a meeting
  type is being mis-routed. Generic overlap words (e.g. "customer",
  "timeline") can appear in both categories' vocabulary.
- CRM/PMO extraction quality depends on the local model you choose; smaller
  models will need more review/editing on the Review screen even with
  structured-output constraints in place.
- "Overdue actions" and issue-trend charts rely on the LLM populating due
  dates consistently — treat these as directional, not exact.
- Excel storage is append-only with no de-duplication; re-approving the
  same meeting twice will create duplicate rows.
- `team_roster.json` and `intent_keywords.json` are local files, not
  synced/shared across machines — copy them if you set up the app
  elsewhere.

## Troubleshooting quick reference

| Symptom | Likely cause | Fix |
|---|---|---|
| `streamlit: command not found` | PATH issue | Use `python -m streamlit run app.py` |
| `ReadTimeout` in `data/app.log` | Model too slow for the default timeout | Already raised to 600s; lower further or use a smaller model if needed |
| `StreamlitDuplicateElementId` | Duplicated code in `app.py` (common after partial copy-paste) | Check line counts / duplicate function defs; replace the whole file cleanly |
| "Model output did not contain a JSON object" | Model ignoring instructions | Structured outputs (JSON Schema) now constrain this; enable `DEBUG_LOG_RAW_LLM_OUTPUT` if it still happens |
| PMO/CRM Review tab empty, no error | Intent score below threshold for both workflows | Check the "Intent scores" caption shown after processing; tune `intent_keywords.json` |
| A phantom "Unknown" speaker appears | UTF-8 BOM or `NOTE`/`Kind:`/`Language:` metadata in the `.vtt` file | Already handled — re-check you're on the latest `transcript_processor.py` |
