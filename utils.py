"""
utils.py - pure helper functions (no Streamlit, no network).
Kept separate from app.py so they can be unit tested: python -m unittest test_utils -v
"""
import ast
import json


# ============================================================
# Error handling
# ============================================================
class LLMError(Exception):
    """A clear, user-facing error from the AI service."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind          # auth | rate_limit | model | bad_request | temporary | empty | invalid_output | unknown
        self.message = message


RETRYABLE = {"temporary", "empty"}


def map_error(error):
    """Convert any exception from the API client into an LLMError.
    Uses class names / status codes so it needs no import of the openai package."""
    if isinstance(error, LLMError):
        return error
    name = type(error).__name__
    status = getattr(error, "status_code", None)

    if name in {"AuthenticationError", "PermissionDeniedError"} or status in (401, 403):
        return LLMError("auth", "The API key is invalid or has no access. "
                                "Check GROQ_API_KEY in your .env file or Streamlit secrets.")
    if name == "RateLimitError" or status == 429:
        return LLMError("rate_limit", "Rate limit reached. Please wait a minute and try again.")
    if name == "NotFoundError" or status == 404:
        return LLMError("model", "The model was not found. Check MODEL_NAME.")
    if name == "BadRequestError" or status == 400:
        return LLMError("bad_request", f"The request was rejected by the AI service: {error}")
    if name in {"APIConnectionError", "APITimeoutError", "InternalServerError"} or (
            isinstance(status, int) and status >= 500):
        return LLMError("temporary", "The AI service is temporarily unavailable. Please try again shortly.")
    return LLMError("unknown", f"Unexpected error: {error}")


# ============================================================
# Prompt helpers
# ============================================================
def number_lines(code):
    """Add line numbers. Leading blank lines are kept so numbers match the
    student's original snippet (only trailing whitespace is removed)."""
    lines = code.replace("\r\n", "\n").rstrip().split("\n")
    return "\n".join(f"{i}: {line}" for i, line in enumerate(lines, start=1))


def wrap_untrusted(tag, text):
    """Wrap user text in tags so the model treats it as data, not instructions.
    Any closing tag inside the text is neutralised so it cannot break out."""
    safe = text.replace(f"</{tag}>", f"<\\/{tag}>")
    return f"<{tag}>\n{safe}\n</{tag}>"


# ============================================================
# JSON parsing + validation
# ============================================================
def extract_json(text):
    """Pull a JSON object out of a model reply (handles ```json fences).
    Returns a dict, or None if nothing valid was found."""
    if not isinstance(text, str):
        return None
    text = text.strip().replace("```json", "").replace("```", "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _clean_text(value):
    return value.strip() if isinstance(value, str) else ""


def validate_fix(data):
    """Check the VALUES and TYPES of a fix suggestion, not just the keys.
    Returns (cleaned_dict, None) if valid, or (None, reason) if not."""
    if not isinstance(data, dict):
        return None, "the reply is not a JSON object"

    cleaned = {}
    for key in ("error_type", "root_cause", "explanation"):
        text = _clean_text(data.get(key))
        if not text:
            return None, f"'{key}' must be non-empty text"
        cleaned[key] = text

    fixed = data.get("fixed_code")
    if not isinstance(fixed, str) or not fixed.strip():
        return None, "'fixed_code' must be non-empty text"
    cleaned["fixed_code"] = fixed.strip("\n")

    steps = data.get("reasoning_steps")
    if not isinstance(steps, list) or not steps:
        return None, "'reasoning_steps' must be a non-empty list"
    cleaned_steps = [s.strip() for s in steps if isinstance(s, str) and s.strip()]
    if len(cleaned_steps) != len(steps):
        return None, "every item in 'reasoning_steps' must be non-empty text"
    cleaned["reasoning_steps"] = cleaned_steps

    changes = data.get("changes")
    if not isinstance(changes, list):
        return None, "'changes' must be a list (it may be empty)"
    cleaned_changes = []
    for item in changes:
        if not isinstance(item, dict):
            return None, "every item in 'changes' must be an object"
        line = item.get("line")
        if isinstance(line, bool) or not isinstance(line, (int, str)):
            return None, "'line' in each change must be a number"
        try:
            line = int(line)
        except ValueError:
            return None, "'line' in each change must be a number"
        before, after, reason = item.get("before"), item.get("after"), item.get("reason")
        if not isinstance(before, str) or not isinstance(after, str) or not _clean_text(reason):
            return None, "each change needs text for 'before', 'after' and 'reason'"
        cleaned_changes.append({"line": line, "before": before, "after": after,
                                "reason": reason.strip()})
    cleaned["changes"] = cleaned_changes
    return cleaned, None


# ============================================================
# Syntax check for suggested Python code
# ============================================================
def check_python_syntax(code):
    """Returns (ok, message). This only checks that the code PARSES;
    it does not run it or prove it behaves correctly."""
    try:
        ast.parse(code)
        return True, "No syntax errors found."
    except SyntaxError as error:
        return False, f"line {error.lineno}: {error.msg}"
    except (ValueError, MemoryError, RecursionError) as error:
        return False, str(error)
