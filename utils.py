"""
utils.py - pure helper functions (no Streamlit, no network).
Kept separate from app.py so they can be unit tested:  python -m unittest discover -v
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
        return LLMError("bad_request", "The AI service rejected the request. Try a shorter snippet or try again.")
    if name in {"APIConnectionError", "APITimeoutError", "InternalServerError"} or (
            isinstance(status, int) and status >= 500):
        return LLMError("temporary", "The AI service is temporarily unavailable. Please try again shortly.")
    return LLMError("unknown", "Something went wrong while contacting the AI service. Please try again.")


# ============================================================
# Prompt helpers
# ============================================================
def canonical_code(code):
    """The ONLY normalisation applied to code before it is compared or numbered:
      * line endings are converted to \\n (a transport detail), and
      * whitespace at the very END of the whole snippet is dropped.
    Blank lines, trailing spaces inside the snippet, indentation and string contents
    are meaningful program data and are never changed."""
    return code.replace("\r\n", "\n").replace("\r", "\n").rstrip()


def split_lines(code):
    """Split code into lines. Leading blank lines are kept so line numbers match
    the student's original snippet."""
    return canonical_code(code).split("\n")


def number_lines(code):
    """Add line numbers (1-based) using the same splitting as validate_fix()."""
    return "\n".join(f"{i}: {line}" for i, line in enumerate(split_lines(code), start=1))


def wrap_untrusted(tag, text):
    """Wrap user text in tags so the model treats it as data, not instructions.
    Any closing tag inside the text is neutralised so it cannot break out."""
    safe = text.replace(f"</{tag}>", f"<\\/{tag}>")
    return f"<{tag}>\n{safe}\n</{tag}>"


# ============================================================
# JSON parsing
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


# ============================================================
# Change-list rules
#
# Every change refers to the ORIGINAL code (1-based line numbers):
#   replace : line 1..N        before = the original line, after = new text (not empty)
#   delete  : line 1..N        before = the original line, after = ""
#   insert  : line 1..N+1      before = "",  after = new text (not empty). The new line is
#                              placed BEFORE original line `line` (N+1 means "add at the end").
# "before" is matched against the original line ignoring spaces at the start and end of that
# line (indentation is still checked by the final comparison below). A line may be replaced or
# deleted only once.
#
# Final comparison: the change list is applied to the original code and the result must equal
# `fixed_code` EXACTLY after canonical_code() (line endings + whitespace at the very end of the
# snippet). Blank lines, trailing spaces inside the snippet, indentation and string contents all
# have to match. With an empty change list, `fixed_code` must therefore be the original code.
# ============================================================
ALLOWED_ACTIONS = ("replace", "insert", "delete")


def apply_changes(original_code, changes):
    """Apply a validated change list to the original code.
    Returns (new_code, None) or (None, reason)."""
    lines = split_lines(original_code)
    total = len(lines)
    replaced = {}   # original line number -> new text, or None when the line is deleted
    inserts = {}    # original line number -> new lines inserted before it

    for change in changes:
        action, line = change["action"], change["line"]
        before, after = change["before"], change["after"]

        if action == "insert":
            if not 1 <= line <= total + 1:
                return None, (f"line {line} is not valid for an insert; the original code has "
                              f"{total} lines (use 1 to {total + 1})")
            if before.strip():
                return None, "an insert must have an empty 'before'"
            if not after.strip():
                return None, "an insert needs text in 'after'"
            inserts.setdefault(line, []).append(after)
            continue

        if not 1 <= line <= total:
            return None, f"line {line} does not exist; the original code has {total} lines"
        if line in replaced:
            return None, f"line {line} is changed more than once"
        if before.strip() != lines[line - 1].strip():
            return None, f"'before' for line {line} does not match the original code on that line"
        if action == "replace":
            if not after.strip():
                return None, f"a replace on line {line} needs text in 'after' (use delete to remove a line)"
            replaced[line] = after
        else:  # delete
            if after.strip():
                return None, f"a delete on line {line} must have an empty 'after'"
            replaced[line] = None

    result = []
    for number in range(1, total + 2):
        result.extend(inserts.get(number, []))
        if number <= total:
            if number not in replaced:
                result.append(lines[number - 1])
            elif replaced[number] is not None:
                result.append(replaced[number])
    return "\n".join(result), None


# ============================================================
# Fix validation
# ============================================================
def _clean_text(value):
    return value.strip() if isinstance(value, str) else ""


def validate_fix(data, original_code):
    """Check the VALUES and TYPES of a fix suggestion AND check it against the
    student's original code. Returns (cleaned_dict, None) or (None, reason)."""
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
    cleaned["fixed_code"] = canonical_code(fixed)

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
        action = item.get("action")
        if not isinstance(action, str) or action not in ALLOWED_ACTIONS:
            return None, "'action' in each change must be one of: replace, insert, delete"
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
        cleaned_changes.append({"line": line, "action": action, "before": before,
                                "after": after, "reason": reason.strip()})
    cleaned["changes"] = cleaned_changes

    # --- check against the ORIGINAL code (strict: meaningful whitespace is preserved) ---
    new_code, problem = apply_changes(original_code, cleaned_changes)
    if problem:
        return None, problem
    if canonical_code(new_code) != cleaned["fixed_code"]:
        if not cleaned_changes:
            return None, ("'changes' is empty but 'fixed_code' is not identical to the original code "
                          "(blank lines, spaces and indentation must match too). List every change, "
                          "or return the original code unchanged if it is already correct")
        return None, ("applying the listed 'changes' to the original code does not produce "
                      "'fixed_code' exactly (blank lines, spaces and indentation must match too); "
                      "the change list is incomplete or wrong")
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


# ============================================================
# What the interface may tell the student about a fix
# ============================================================
def summarize_fix(fix):
    """Decide which status messages are consistent with the validation outcome.
    Returns {"kind": "invalid" | "verified" | "unverified", "message": str, "no_changes_note": str | None}.
    "No changes were needed" is only ever produced when it does not contradict the syntax check."""
    syntax = fix["syntax"]
    no_changes = not fix["changes"]

    if syntax["checked"] and not syntax["ok"]:
        return {
            "kind": "invalid",
            "message": ("The assistant could not produce a valid correction: the code it suggested "
                        f"still has a Python syntax error ({syntax['message']}). "
                        "Please try again, or ask in the Chat tab."),
            "no_changes_note": None,
        }
    if syntax["checked"]:
        return {
            "kind": "verified",
            "message": ("Python syntax check passed (the code parses). This is NOT a functional test - "
                        "run it to confirm it behaves as you expect."),
            "no_changes_note": ("No changes were needed according to the AI. The code parses, but this "
                                "does not prove it behaves correctly, so it may still contain logic "
                                "errors.") if no_changes else None,
        }
    return {
        "kind": "unverified",
        "message": f"{syntax['message']} Review and test the code yourself.",
        "no_changes_note": ("The AI judged that this code needs no changes. This is the model's "
                            "assessment only; it has not been verified by a compiler or tests.")
        if no_changes else None,
    }


# ============================================================
# Context for follow-up chat
# ============================================================
def build_chat_context(explanation="", analysis="", fix=None, limit=2500):
    """Text describing what the app has already shown the student, so the chat can
    answer questions such as 'Why did you change this line?'. Returns "" if nothing yet."""
    parts = []
    if explanation and explanation.strip():
        parts.append("EXPLANATION SHOWN TO THE STUDENT:\n" + explanation.strip()[:limit])
    if analysis and analysis.strip():
        parts.append("ERROR ANALYSIS SHOWN TO THE STUDENT:\n" + analysis.strip()[:limit])
    if fix:
        lines = ["SUGGESTED FIX SHOWN TO THE STUDENT:",
                 f"Error type: {fix.get('error_type', '')}",
                 f"Root cause: {fix.get('root_cause', '')}",
                 "Changes (line numbers refer to the ORIGINAL code):"]
        changes = fix.get("changes") or []
        if not changes:
            lines.append("- none (the AI said the code needed no changes)")
        for change in changes:
            lines.append(f"- line {change.get('line')} ({change.get('action')}): "
                         f"'{change.get('before')}' -> '{change.get('after')}' "
                         f"because {change.get('reason')}")
        lines.append("Corrected code:\n" + str(fix.get("fixed_code", ""))[:limit])
        lines.append("Explanation: " + str(fix.get("explanation", "")))
        parts.append("\n".join(lines))
    return "\n\n".join(parts)