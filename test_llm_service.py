"""
Mocked integration tests for llm_service.py (no network, no API key needed).
A fake client plays back scripted replies, so we can test retries, rejected fixes,
streaming failures and chat context exactly as the app uses them.
Run:  python -m unittest discover -v
"""
import json
import unittest
from types import SimpleNamespace

from llm_service import LLMService
from utils import LLMError, summarize_fix, validate_fix

ORIGINAL = "def add(a, b)\n    return a + b\nprint(add(2, 3))"


# ---------------- fake API client ----------------
class APIConnectionError(Exception):
    """Name matches the real openai error so map_error() treats it as temporary."""


class AuthenticationError(Exception):
    status_code = 401


def chunk(text):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


class FakeCompletions:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if kwargs.get("stream"):
            return item() if callable(item) else iter([chunk(p) for p in item])
        message = SimpleNamespace(content=item)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def make_service(script):
    completions = FakeCompletions(script)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return LLMService(client, "test-model", sleep=lambda seconds: None), completions


def fix_json(**overrides):
    data = {
        "error_type": "SyntaxError",
        "reasoning_steps": ["Read the code", "Line 1 has no colon"],
        "root_cause": "The function definition is missing a colon.",
        "changes": [{"line": 1, "action": "replace", "before": "def add(a, b)",
                     "after": "def add(a, b):", "reason": "A header must end with a colon."}],
        "fixed_code": "def add(a, b):\n    return a + b\nprint(add(2, 3))",
        "explanation": "Python needs a colon after the function header.",
    }
    data.update(overrides)
    return json.dumps(data)


def user_prompt(call):
    return call["messages"][-1]["content"]


# ---------------- suggest_fix ----------------
class TestSuggestFix(unittest.TestCase):
    def test_valid_reply_is_returned_with_syntax_check(self):
        service, api = make_service([fix_json()])
        result = service.suggest_fix(ORIGINAL, "SyntaxError: expected ':'", "Python")
        self.assertEqual(len(api.calls), 1)
        self.assertTrue(result["syntax"]["checked"])
        self.assertTrue(result["syntax"]["ok"])

    def test_invalid_json_then_valid_retry(self):
        service, api = make_service(["Sorry, here is some text, no JSON.", fix_json()])
        result = service.suggest_fix(ORIGINAL, "", "Python")
        self.assertEqual(len(api.calls), 2)
        self.assertIn("rejected", user_prompt(api.calls[1]))
        self.assertIn("not valid JSON", user_prompt(api.calls[1]))
        self.assertEqual(result["error_type"], "SyntaxError")

    def test_null_values_are_rejected_then_retried(self):
        service, api = make_service([fix_json(fixed_code=None), fix_json()])
        result = service.suggest_fix(ORIGINAL, "", "Python")
        self.assertEqual(len(api.calls), 2)
        self.assertIn("'fixed_code'", user_prompt(api.calls[1]))
        self.assertTrue(result["fixed_code"].startswith("def add(a, b):"))

    def test_python_code_that_stays_invalid_is_flagged_not_trusted(self):
        # changes=[] and the original code returned: consistent, but the syntax error remains
        bad = fix_json(changes=[], fixed_code=ORIGINAL)
        service, api = make_service([bad, bad, bad])
        result = service.suggest_fix(ORIGINAL, "", "Python")
        self.assertEqual(len(api.calls), 3)            # retried twice
        self.assertIn("syntax error", user_prompt(api.calls[1]))
        self.assertTrue(result["syntax"]["checked"])
        self.assertFalse(result["syntax"]["ok"])       # clearly flagged for the UI

    def test_invalid_python_corrected_on_retry(self):
        bad = fix_json(changes=[], fixed_code=ORIGINAL)
        service, api = make_service([bad, fix_json()])
        result = service.suggest_fix(ORIGINAL, "", "Python")
        self.assertEqual(len(api.calls), 2)
        self.assertTrue(result["syntax"]["ok"])

    def test_empty_changes_with_a_different_program_is_rejected(self):
        wrong = fix_json(changes=[], fixed_code="print('hello')")
        service, api = make_service([wrong, fix_json()])
        result = service.suggest_fix(ORIGINAL, "", "Python")
        self.assertEqual(len(api.calls), 2)
        self.assertIn("'changes' is empty", user_prompt(api.calls[1]))
        self.assertEqual(result["changes"][0]["line"], 1)   # never "no changes needed"

    def test_contradictory_change_lists_never_shown_to_student(self):
        wrong_line = fix_json(changes=[{"line": 999, "action": "replace", "before": "x",
                                        "after": "y", "reason": "r"}])
        service, api = make_service([wrong_line] * 3)
        with self.assertRaises(LLMError) as context:
            service.suggest_fix(ORIGINAL, "", "Python")
        self.assertEqual(context.exception.kind, "invalid_output")
        self.assertEqual(len(api.calls), 3)

    def test_before_text_that_is_not_in_the_original_is_rejected(self):
        invented = fix_json(changes=[{"line": 1, "action": "replace", "before": "def sub(a, b)",
                                      "after": "def add(a, b):", "reason": "r"}])
        service, api = make_service([invented, fix_json()])
        service.suggest_fix(ORIGINAL, "", "Python")
        self.assertIn("does not match", user_prompt(api.calls[1]))

    def test_changes_that_do_not_produce_fixed_code_are_rejected(self):
        mismatch = fix_json(fixed_code="def other():\n    pass")
        service, api = make_service([mismatch, fix_json()])
        service.suggest_fix(ORIGINAL, "", "Python")
        self.assertIn("does not produce", user_prompt(api.calls[1]))

    def test_other_languages_get_no_syntax_check(self):
        java = "int x = \"hello\";"
        reply = fix_json(changes=[{"line": 1, "action": "replace", "before": java,
                                   "after": "String x = \"hello\";", "reason": "type"}],
                         fixed_code="String x = \"hello\";")
        service, _ = make_service([reply])
        result = service.suggest_fix(java, "", "Java")
        self.assertFalse(result["syntax"]["checked"])

    def test_original_code_is_wrapped_as_untrusted_data(self):
        service, api = make_service([fix_json()])
        service.suggest_fix(ORIGINAL, "", "Python")
        self.assertIn("<code>", user_prompt(api.calls[0]))
        self.assertIn("UNTRUSTED", api.calls[0]["messages"][0]["content"])


# ---------------- API failures ----------------
class TestApiFailures(unittest.TestCase):
    def test_invalid_key_is_not_retried(self):
        service, api = make_service([AuthenticationError("bad key")])
        with self.assertRaises(LLMError) as context:
            service.call("system", "user")
        self.assertEqual(context.exception.kind, "auth")
        self.assertEqual(len(api.calls), 1)

    def test_temporary_error_is_retried(self):
        service, api = make_service([APIConnectionError("down"), "second try works"])
        self.assertEqual(service.call("system", "user"), "second try works")
        self.assertEqual(len(api.calls), 2)

    def test_gives_up_after_three_temporary_errors(self):
        service, api = make_service([APIConnectionError("x")] * 3)
        with self.assertRaises(LLMError) as context:
            service.call("system", "user")
        self.assertEqual(context.exception.kind, "temporary")
        self.assertEqual(len(api.calls), 3)

    def test_whitespace_only_reply_is_rejected_then_retried(self):
        service, api = make_service(["   \n ", "real answer"])
        self.assertEqual(service.call("system", "user"), "real answer")
        self.assertEqual(len(api.calls), 2)


# ---------------- streaming ----------------
class TestStreaming(unittest.TestCase):
    def test_pieces_are_yielded_in_order(self):
        service, _ = make_service([["Hel", "lo ", "world"]])
        self.assertEqual("".join(service.stream("system", "user")), "Hello world")

    def test_failure_after_partial_output_raises_a_clear_error(self):
        def broken_stream():
            yield chunk("Partial answer ")
            raise APIConnectionError("connection lost")

        service, _ = make_service([broken_stream])
        received = []
        with self.assertRaises(LLMError) as context:
            for piece in service.stream("system", "user"):
                received.append(piece)
        self.assertEqual(received, ["Partial answer "])      # partial text was delivered
        self.assertEqual(context.exception.kind, "temporary")  # then a clear error, not silence

    def test_empty_stream_raises(self):
        service, _ = make_service([["", "  "]])
        with self.assertRaises(LLMError) as context:
            list(service.stream("system", "user"))
        self.assertEqual(context.exception.kind, "empty")

    def test_chunks_without_choices_are_skipped(self):
        def stream_with_usage_chunk():
            yield SimpleNamespace(choices=[])
            yield chunk("ok")

        service, _ = make_service([stream_with_usage_chunk])
        self.assertEqual("".join(service.stream("system", "user")), "ok")


# ---------------- chat context ----------------
class TestChatContext(unittest.TestCase):
    def latest_fix(self):
        return validate_fix(json.loads(fix_json()), ORIGINAL)[0]

    def test_chat_receives_the_latest_suggested_fix(self):
        service, api = make_service([["Because Python needs a colon."]])
        results = {"explanation": "", "analysis": "", "fix": self.latest_fix()}
        reply = "".join(service.chat_stream("Why did you change this line?", ORIGINAL,
                                            "SyntaxError: expected ':'", "Python", [], results))
        system_prompt = api.calls[0]["messages"][0]["content"]
        self.assertIn("SUGGESTED FIX SHOWN TO THE STUDENT", system_prompt)
        self.assertIn("def add(a, b):", system_prompt)
        self.assertIn("A header must end with a colon.", system_prompt)
        self.assertIn("(replace)", system_prompt)
        self.assertEqual(api.calls[0]["messages"][-1]["content"], "Why did you change this line?")
        self.assertEqual(reply, "Because Python needs a colon.")

    def test_chat_receives_explanation_and_analysis(self):
        service, api = make_service([["ok"]])
        results = {"explanation": "EXPLANATION TEXT", "analysis": "ANALYSIS TEXT", "fix": None}
        list(service.chat_stream("question", ORIGINAL, "", "Python", [], results))
        system_prompt = api.calls[0]["messages"][0]["content"]
        self.assertIn("EXPLANATION TEXT", system_prompt)
        self.assertIn("ANALYSIS TEXT", system_prompt)

    def test_chat_without_results_says_nothing_generated_yet(self):
        service, api = make_service([["ok"]])
        list(service.chat_stream("question", ORIGINAL, "", "Python", [], None))
        self.assertIn("nothing has been generated yet", api.calls[0]["messages"][0]["content"])

    def test_only_recent_history_is_sent(self):
        history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
                   for i in range(10)]
        service, api = make_service([["ok"]])
        list(service.chat_stream("new question", ORIGINAL, "", "Python", history, None))
        messages = api.calls[0]["messages"]
        self.assertEqual(len(messages), 1 + 6 + 1)   # system + last 6 + question
        self.assertEqual(messages[1]["content"], "m4")

    def test_results_are_marked_as_not_instructions(self):
        service, api = make_service([["ok"]])
        results = {"explanation": "x", "analysis": "", "fix": None}
        list(service.chat_stream("q", ORIGINAL, "", "Python", [], results))
        system_prompt = api.calls[0]["messages"][0]["content"]
        self.assertIn("<results>", system_prompt)
        self.assertIn("not instructions", system_prompt)


# ---------------- review 3: whitespace, status messages, logging ----------------
class TestMeaningfulWhitespaceIntegration(unittest.TestCase):
    def test_model_that_drops_a_blank_line_inside_a_string_is_rejected_then_corrected(self):
        original = 'message = """first\n\nlast"""\nprint(message)'
        damaged = fix_json(changes=[], fixed_code='message = """first\nlast"""\nprint(message)')
        intact = fix_json(changes=[], fixed_code=original)
        service, api = make_service([damaged, intact])
        result = service.suggest_fix(original, "", "Python")
        self.assertEqual(len(api.calls), 2)
        self.assertIn("'changes' is empty", user_prompt(api.calls[1]))
        self.assertEqual(result["fixed_code"], original)   # the string content is untouched

    def test_prompt_tells_the_model_to_keep_whitespace(self):
        service, api = make_service([fix_json()])
        service.suggest_fix(ORIGINAL, "", "Python")
        self.assertIn("multi-line strings", user_prompt(api.calls[0]))


class TestStatusMessageConsistency(unittest.TestCase):
    def test_empty_changes_with_invalid_python_is_reported_as_invalid_not_no_changes(self):
        bad = fix_json(changes=[], fixed_code=ORIGINAL)     # original still has the syntax error
        service, _ = make_service([bad, bad, bad])
        result = service.suggest_fix(ORIGINAL, "", "Python")
        summary = summarize_fix(result)
        self.assertEqual(result["changes"], [])
        self.assertEqual(summary["kind"], "invalid")
        self.assertIsNone(summary["no_changes_note"])        # the contradictory message cannot appear
        self.assertIn("could not produce a valid correction", summary["message"])

    def test_empty_changes_for_other_language_is_labelled_as_unverified(self):
        java = "int x = 1;"
        service, _ = make_service([fix_json(changes=[], fixed_code=java)])
        summary = summarize_fix(service.suggest_fix(java, "", "Java"))
        self.assertEqual(summary["kind"], "unverified")
        self.assertIn("not been verified", summary["no_changes_note"])


class TestErrorDetailsAreLoggedNotShown(unittest.TestCase):
    def test_provider_details_go_to_the_log_only(self):
        class BadRequestError(Exception):
            status_code = 400

        service, _ = make_service([BadRequestError("provider-detail-789")])
        with self.assertLogs("llm_service", level="WARNING") as logs:
            with self.assertRaises(LLMError) as context:
                service.call("system", "user")
        self.assertIn("provider-detail-789", "\n".join(logs.output))
        self.assertNotIn("provider-detail-789", context.exception.message)


class TestRejectionsAreLogged(unittest.TestCase):
    def test_rejection_reason_is_written_to_the_log(self):
        wrong = fix_json(fixed_code="def other():\n    pass")
        service, _ = make_service([wrong, fix_json()])
        with self.assertLogs("llm_service", level="WARNING") as logs:
            service.suggest_fix(ORIGINAL, "", "Python")
        joined = "\n".join(logs.output)
        self.assertIn("Fix reply rejected (attempt 1 of 3)", joined)
        self.assertIn("does not produce", joined)


if __name__ == "__main__":
    unittest.main()