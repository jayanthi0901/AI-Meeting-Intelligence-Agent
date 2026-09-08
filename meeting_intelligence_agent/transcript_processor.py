"""
Transcript Processing (spec section 4).

Supports the Version 1 input sources (spec section 3, MVP):
  - Microsoft Teams transcript (.docx)
  - Microsoft Teams transcript (.vtt)
  - Plain .txt file
  - Copy / paste transcript
"""
import io
import logging
import re
from dataclasses import dataclass, field
from typing import List

import docx  # python-docx

logger = logging.getLogger("meeting_intelligence_agent")

# Matches lines like "John (Customer):" or "Mary (Project Manager):" or "John:"
# The name must look like a proper name (1-4 capitalized words) so that inline
# labels such as "Action item:" or "Decision:" inside a sentence are not
# mistaken for a new speaker.
SPEAKER_LINE_RE = re.compile(
    r"^\s*([A-Z][A-Za-z'-]*(?:\s[A-Z][A-Za-z'-]*){0,3})(?:\s*\(([^)]{1,60})\))?\s*:\s*(.*)$"
)

# WEBVTT cue timing line, e.g. 00:00:01.000 --> 00:00:04.000
VTT_TIMING_RE = re.compile(r"\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[.,]\d{3}")

# Native Microsoft Teams .vtt cue identifier, e.g.
# "879ed542-0509-481d-a2b7-3a928cd25fd2/6-0" -- appears on its own line
# immediately before the timing line for each cue.
VTT_CUE_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/\d+-\d+$"
)

# Native Microsoft Teams .vtt speaker tag, e.g. <v Angel Palacios>Sorry, let me
# share my screen.</v>  -- no role information, just the display name.
VTT_VOICE_CLOSED_RE = re.compile(r"^<v\s+([^>]+)>(.*)</v>\s*$", re.IGNORECASE)
VTT_VOICE_OPEN_RE = re.compile(r"^<v\s+([^>]+)>(.*)$", re.IGNORECASE)


# WebVTT metadata header lines Teams sometimes emits right after the WEBVTT
# tag, e.g. "Kind: captions" / "Language: en-us" -- not real speaker lines.
VTT_METADATA_HEADER_RE = re.compile(r"^(Kind|Language)\s*:\s*.*$", re.IGNORECASE)

# WebVTT block keywords that start a multi-line comment/style/region block
# terminated by a blank line -- none of these are speaker content.
VTT_BLOCK_KEYWORDS = ("NOTE", "STYLE", "REGION")


@dataclass
class TranscriptTurn:
    speaker: str
    role: str
    text: str


@dataclass
class ParsedTranscript:
    raw_text: str
    turns: List[TranscriptTurn] = field(default_factory=list)

    @property
    def participants(self) -> List[str]:
        seen = []
        for t in self.turns:
            if t.speaker and t.speaker not in seen:
                seen.append(t.speaker)
        return seen


def _extract_voice_tag(line: str):
    """If the line uses Teams' <v Speaker Name>text</v> format, return
    (speaker, text). Otherwise return None."""
    stripped = line.strip()
    match = VTT_VOICE_CLOSED_RE.match(stripped)
    if not match:
        match = VTT_VOICE_OPEN_RE.match(stripped)
    if not match:
        return None
    speaker = match.group(1).strip()
    text = match.group(2).strip()
    return speaker, text


def _lines_to_turns(lines: List[str]) -> List[TranscriptTurn]:
    turns: List[TranscriptTurn] = []
    current = None
    for line in lines:
        line = line.rstrip()
        if not line.strip():
            continue

        voice = _extract_voice_tag(line)
        if voice:
            speaker, text = voice
            # Teams .vtt exports carry no role information -- leave role
            # blank here; it can be filled in later by role_inference.py.
            current = TranscriptTurn(speaker=speaker, role="", text=text)
            turns.append(current)
            continue

        match = SPEAKER_LINE_RE.match(line)
        if match:
            speaker, role, text = match.groups()
            current = TranscriptTurn(speaker=speaker.strip(), role=(role or "").strip(), text=text.strip())
            turns.append(current)
        elif current is not None:
            # Continuation of the previous speaker's turn
            current.text = (current.text + " " + line.strip()).strip()
        else:
            # No speaker detected yet and nothing to attach this line to --
            # drop it rather than inventing a spurious "Unknown" speaker.
            # This typically means the line is leftover file metadata/noise
            # (e.g. a stray header or comment) that earlier filtering missed.
            logger.debug("Dropping unattributed line before any speaker was found: %r", line.strip())
    return turns


def parse_txt(file_bytes: bytes) -> ParsedTranscript:
    # utf-8-sig transparently strips a leading UTF-8 BOM if present (common
    # in files exported on Windows), and behaves identically to plain utf-8
    # when no BOM exists.
    text = file_bytes.decode("utf-8-sig", errors="replace")
    turns = _lines_to_turns(text.splitlines())
    return ParsedTranscript(raw_text=text, turns=turns)


def parse_pasted_text(text: str) -> ParsedTranscript:
    turns = _lines_to_turns(text.splitlines())
    return ParsedTranscript(raw_text=text, turns=turns)


def parse_docx(file_bytes: bytes) -> ParsedTranscript:
    document = docx.Document(io.BytesIO(file_bytes))
    lines = [p.text for p in document.paragraphs]
    text = "\n".join(lines)
    turns = _lines_to_turns(lines)
    return ParsedTranscript(raw_text=text, turns=turns)


def parse_vtt(file_bytes: bytes) -> ParsedTranscript:
    """
    WEBVTT parser supporting two Microsoft Teams export styles:

    1. Native Teams export (cue ID + voice tag), e.g.:
        879ed542-0509-481d-a2b7-3a928cd25fd2/6-0
        00:00:12.536 --> 00:00:13.696
        <v Angel Palacios>Sorry, let me share my screen.</v>

    2. Plain-text style some tools export, e.g.:
        00:00:01.000 --> 00:00:04.000
        Speaker Name: Spoken text here

    Also tolerates common non-speaker WebVTT content that isn't part of the
    spec but shows up in real exports: a leading UTF-8 BOM, "Kind:"/"Language:"
    metadata headers, and NOTE/STYLE/REGION comment blocks.
    """
    # utf-8-sig transparently strips a leading UTF-8 BOM if present.
    text = file_bytes.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    content_lines = []
    in_block = False  # inside a NOTE/STYLE/REGION block, which ends at a blank line
    for line in lines:
        stripped = line.strip()
        if not stripped:
            in_block = False
            continue
        if in_block:
            continue
        if stripped.upper().startswith("WEBVTT"):
            continue
        if any(stripped == kw or stripped.startswith(kw + " ") for kw in VTT_BLOCK_KEYWORDS):
            in_block = True
            continue
        if VTT_METADATA_HEADER_RE.match(stripped):  # e.g. "Kind: captions"
            continue
        if VTT_TIMING_RE.search(stripped):
            continue
        if stripped.isdigit():  # legacy numeric cue index
            continue
        if VTT_CUE_ID_RE.match(stripped):  # native Teams cue identifier
            continue
        content_lines.append(stripped)
    turns = _lines_to_turns(content_lines)
    return ParsedTranscript(raw_text="\n".join(content_lines), turns=turns)


def parse_uploaded_file(filename: str, file_bytes: bytes) -> ParsedTranscript:
    lower = filename.lower()
    if lower.endswith(".docx"):
        return parse_docx(file_bytes)
    if lower.endswith(".vtt"):
        return parse_vtt(file_bytes)
    if lower.endswith(".txt"):
        return parse_txt(file_bytes)
    raise ValueError(f"Unsupported file type: {filename}")


def format_turns_for_prompt(turns: List[TranscriptTurn], max_chars: int = 12000) -> str:
    """Render turns back into 'Speaker (Role): text' lines for use in LLM prompts
    that need to know who said what (CRM/PMO extraction agents)."""
    rendered = []
    for t in turns:
        label = f"{t.speaker} ({t.role})" if t.role else t.speaker
        rendered.append(f"{label}: {t.text}")
    joined = "\n".join(rendered)
    if len(joined) > max_chars:
        joined = joined[:max_chars] + "\n...[transcript truncated for length]"
    return joined


def extract_spoken_text(turns: List[TranscriptTurn], max_chars: int = 12000) -> str:
    """Returns only what was actually said -- no speaker names, no role
    labels. Use this for anything that scans for content keywords (e.g.
    intent classification): a participant's ROLE label (e.g. "Customer")
    can otherwise collide with a keyword and skew scoring based on who's
    in the room rather than what was discussed."""
    joined = "\n".join(t.text for t in turns)
    if len(joined) > max_chars:
        joined = joined[:max_chars] + "\n...[transcript truncated for length]"
    return joined