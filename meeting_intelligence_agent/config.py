"""
Central configuration for the AI Meeting Intelligence Agent (Version 1).

Per the spec's Security Requirements section, the system:
  - Runs the LLM locally (Ollama) -- no transcript ever leaves the device
  - Requires no external API keys
  - Never logs raw transcript / customer content
"""
import os

# --- Local LLM (Ollama) ---
# Ollama must be installed and running locally: https://ollama.com
# Pull a model first, e.g.:  ollama pull qwen2.5
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5")
# CPU-only inference on a 7B model can comfortably take several minutes per
# call, especially for the first request after the model loads into memory.
# 120s is too tight for that case, so default to a much longer ceiling.
LLM_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("LLM_REQUEST_TIMEOUT_SECONDS", "600"))

# Set to True temporarily if you need to debug "Model output did not contain
# a JSON object" errors -- logs a short, truncated preview of the model's
# raw output to data/app.log. Leave False normally: the preview can contain
# fragments of transcript content, which we otherwise never log (see spec
# section 15, Security Requirements).
DEBUG_LOG_RAW_LLM_OUTPUT = os.environ.get("DEBUG_LOG_RAW_LLM_OUTPUT", "false").lower() == "true"

# --- Storage (MVP = Excel, per spec section 11) ---
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CRM_XLSX = os.path.join(DATA_DIR, "CRM.xlsx")
PMO_XLSX = os.path.join(DATA_DIR, "PMO.xlsx")
REQUIREMENTS_XLSX = os.path.join(DATA_DIR, "Requirements.xlsx")
BUSINESS_OBJECTS_JSONL = os.path.join(DATA_DIR, "business_objects.jsonl")
APP_LOG_FILE = os.path.join(DATA_DIR, "app.log")

# --- Intent classification ---
# Version 1 activates CRM + PMO agents only (Agile is Version 2 scope).
V1_SUPPORTED_WORKFLOWS = ["CRM", "PMO"]
INTENT_CONFIDENCE_THRESHOLD = 0.5  # below this, a workflow's agent is not run

# --- Supported MVP input types (spec section 3) ---
SUPPORTED_UPLOAD_EXTENSIONS = [".docx", ".vtt", ".txt"]

# --- Team roster / role resolution ---
# Native Teams .vtt exports carry no role info, and asking a small local LLM
# to *guess* roles from conversation tone is unreliable. Instead, roles are
# resolved deterministically from this configurable roster file: any
# transcript participant not listed here is assumed to be a Customer.
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
TEAM_ROSTER_PATH = os.environ.get("TEAM_ROSTER_PATH", os.path.join(PROJECT_ROOT, "team_roster.json"))
KNOWN_ROLE_OPTIONS = [
    "Project Manager", "Developer", "Sales Representative",
    "Business Analyst", "Executive Sponsor", "QA/Tester", "Other", "Customer",
]

# --- Intent classification keywords ---
# Local LLMs proved unreliable at scoring CRM vs PMO confidence (observed:
# fixating on the same score regardless of transcript content). Intent
# classification is therefore keyword-based by default -- deterministic,
# instant, and fully configurable without touching code. The LLM-based
# approach is kept in intent_classifier.py as an optional reference.
INTENT_KEYWORDS_PATH = os.environ.get("INTENT_KEYWORDS_PATH", os.path.join(PROJECT_ROOT, "intent_keywords.json"))
