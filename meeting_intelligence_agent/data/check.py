"""
Thin wrapper around a locally-running Ollama instance.

Security note (spec section 15): all inference happens against
http://localhost by default. No transcript content is ever sent to a
third-party API, and no API key is required.
"""
import json
import re
import logging
import requests

import config

logger = logging.getLogger("meeting_intelligence_agent")


class LLMError(RuntimeError):
    pass


def _post_generate(prompt: str, system: str = "", json_schema: dict = None) -> str:
    """Call Ollama's /api/generate endpoint and return the raw text response."""
    url = f"{config.OLLAMA_HOST}/api/generate"
    payload = {
        "model": config.OLLAMA_MODEL,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {"temperature": 0.1},
        # Passing a JSON Schema object (rather than just the string "json")
        # activates Ollama's structured-outputs mode: it constrains sampling
        # to match the given property names and types, not just "some valid
        # JSON". Plain format="json" only guarantees valid JSON *syntax* --
        # smaller local models will happily emit syntactically valid JSON
        # with completely made-up keys, ignoring the requested shape
        # entirely. A schema fixes that. Falls back to plain JSON mode if no
        # schema is supplied.
        "format": json_schema if json_schema is not None else "json",
    }
    try:
        resp = requests.post(url, json=payload, timeout=config.LLM_REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.exceptions.Timeout as exc:
        logger.error("LLM request timed out (transport-level, no content logged): %s", type(exc).__name__)
        raise LLMError(
            f"The local model did not respond within {config.LLM_REQUEST_TIMEOUT_SECONDS}s. "
            f"Ollama is reachable, but '{config.OLLAMA_MODEL}' is taking longer than that to generate "
            f"a response (common on CPU-only hardware for larger models). Try a smaller/faster model, "
            f"or raise LLM_REQUEST_TIMEOUT_SECONDS in config.py."
        ) from exc
    except requests.exceptions.RequestException as exc:
        logger.error("LLM request failed (transport-level, no content logged): %s", type(exc).__name__)
        raise LLMError(
            f"Could not reach local Ollama server at {config.OLLAMA_HOST}. "
            f"Make sure Ollama is running and the model '{config.OLLAMA_MODEL}' is pulled."
        ) from exc

    data = resp.json()
    raw = data.get("response", "")

    # Opt-in debug aid: logs a short, truncated preview of the raw model
    # output to help diagnose formatting failures. OFF by default because
    # the preview can contain fragments of transcript content -- only
    # enable this locally while troubleshooting (see config.py).
    if getattr(config, "DEBUG_LOG_RAW_LLM_OUTPUT", False):
        preview = raw[:500].replace("\n", " ")
        logger.info("[DEBUG_LOG_RAW_LLM_OUTPUT] Raw LLM output preview (first 500 chars): %s", preview)

    return raw


def _strip_reasoning_blocks(text: str) -> str:
    """Some local models (especially reasoning-tuned ones) wrap chain-of-thought
    in <think>...</think> tags before the actual answer. Strip these so JSON
    extraction isn't confused by braces that might appear inside them."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


def extract_json(text: str) -> dict:
    """
    LLMs frequently wrap JSON in markdown fences, add commentary, or (for
    reasoning-tuned models) prepend a <think> block. This strips known
    wrappers and then pulls out the first valid top-level JSON object found.
    """
    cleaned = _strip_reasoning_blocks(text.strip())
    cleaned = re.sub(r"^```(json)?", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Fallback: find the widest {...} span and try that.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = cleaned[start:end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Model output was not valid JSON: {exc}") from exc

    raise LLMError(
        "Model output did not contain a JSON object. If this keeps happening, "
        "try a different model, or enable DEBUG_LOG_RAW_LLM_OUTPUT in config.py "
        "temporarily to see what the model is actually returning."
    )


def call_llm_json(prompt: str, system: str = "") -> dict:
    """Call the LLM and parse its response as JSON, raising LLMError on failure."""
    raw = _post_generate(prompt, system=system)
    return extract_json(raw)