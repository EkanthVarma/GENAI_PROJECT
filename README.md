# 🛠️ AI Code Explainer and Debugging Assistant

A Streamlit web app that helps beginner programmers understand code and fix errors.
The student pastes a code snippet (and optionally an error message) and receives a
plain-language explanation, an error analysis, and a corrected version of the code.

**Live demo:** https://genaiproject-aicodeexplainer-debuggerassistant.streamlit.app
**Author:** Seera Ekanth Varma

## Features
| Module | What it does |
|---|---|
| Code input | Paste code, choose a language (Python, Java, C, C++, JavaScript), optional error message, 7 built-in examples |
| Line-by-line explanation | Beginner-friendly explanation that quotes each line exactly and flags mistakes with ⚠️. Streamed live |
| Error message analyzer | Four-step chain-of-thought analysis. Works even when no error message is given |
| Fix suggestion generator | Validated JSON output: error type, reasoning, before/after table, corrected code, download button. Every change is checked against your original code |
| Chat interface | Follow-up questions that know your code, the error, the explanation, the analysis and the suggested fix shown to you, plus the last 6 messages. Streamed live |

## Course concepts used
- **Prompt engineering** for technical explanations (role, strict format, "copy lines exactly")
- **Few-shot prompting**: a worked example is included in the explanation and fix prompts
- **Chain-of-thought** debugging (`reasoning_steps` and the four-step analysis)
- **Structured JSON output** with strict validation of values and types, and automatic retry
- **Change-list validation**: line numbers, `before` text and the final code must agree with the original snippet (see below)
- **Streaming** responses for explanation, analysis and chat
- **Prompt-injection defence**: code and errors are wrapped in tags and treated as data

## Project structure
```
app.py                Streamlit interface (layout, buttons, session state)
llm_service.py        All model calls, prompts, retries, streaming and fix generation
utils.py              Pure helpers: change-list validation, error mapping, syntax check
test_utils.py         Unit tests for utils.py
test_llm_service.py   Mocked integration tests for llm_service.py (no API key needed)
TEST_CASES.md         Manual test evidence and test summary
requirements.txt      Dependencies
.env.example          Template for your API key
```

## Setup
1. Install Python 3.10 or newer.
2. Create a free Groq API key at https://console.groq.com/keys
3. In the project folder:
   ```
   python -m venv .venv
   .venv\Scripts\activate          # Windows   (Mac/Linux: source .venv/bin/activate)
   pip install -r requirements.txt
   ```
4. Copy `.env.example` to `.env` and put your key in it:
   ```
   GROQ_API_KEY=gsk_your_key_here
   ```
5. Run the app:
   ```
   streamlit run app.py
   ```
   It opens at http://localhost:8501

Tested with Python 3.13, streamlit 1.61.1, openai 3.33.0 and python-dotenv 1.1.1 on Windows (the exact versions are pinned in `requirements.txt`). Deployed on Streamlit Community Cloud, which ran Python 3.14 at deployment time.

### Configuration
| Setting | Required | Default |
|---|---|---|
| `GROQ_API_KEY` | yes | none |
| `MODEL_NAME` | no | `openai/gpt-oss-120b` |

Settings are read from `.env` locally or from Streamlit secrets when deployed.

## Run the tests
```
python -m unittest discover -v
```
No API key or internet is needed. `test_llm_service.py` uses a fake model client to test retries,
rejected fixes, streaming failures and chat context.

## How a suggested fix is checked
Every change the AI lists refers to the **original** code (1-based line numbers):

| Action | Rule |
|---|---|
| `replace` | line 1..N, `before` = the original line, `after` = new text (not empty) |
| `delete` | line 1..N, `before` = the original line, `after` is empty |
| `insert` | line 1..N+1, `before` is empty, `after` = new text (not empty). The new line goes **before** original line `line` (N+1 = add at the end) |

Rules in detail:
- A line can be replaced or deleted only once. Line numbers outside the snippet (0, negative, too large) are rejected.
- `before` is matched against the original line **ignoring spaces at the start and end of that line**. Indentation is still verified by the final comparison.
- The app then applies the change list to your original code and compares the result with `fixed_code`.
  **The two must be identical.** The only normalisation is: line endings are converted to `\n`, and whitespace at the
  very end of the whole snippet is ignored. Blank lines, trailing spaces inside the snippet, indentation and the contents of
  multi-line strings (Python `"""..."""`, JavaScript template literals, docstrings) are meaningful and must match exactly.
- With an empty change list, `fixed_code` must therefore be your original code, unchanged.
- A reply that breaks a rule is rejected and the AI is asked again with the reason (up to 3 tries). If no valid reply arrives,
  the student sees an error instead of a convincing but wrong table.
- Because blank lines count, a "fix" that only adds or removes blank lines (pure reformatting) cannot be expressed and is not offered.

What is checked and what is not:

| Checked by the app | Still only a model suggestion |
|---|---|
| Change list matches the original code and produces `fixed_code` exactly | Whether the fix keeps the intended behaviour of your program |
| Python only: `fixed_code` parses (syntax check) | The error type, root cause and explanations |
| | "No changes needed" for non-Python code (not compiler-verified) |

Status messages follow the validation result: if the Python code is still syntactically invalid after the retries, the app says
the assistant could not produce a valid correction, and never says "no changes needed".

## Deploy on Streamlit Community Cloud
1. Push `app.py`, `llm_service.py`, `utils.py`, `requirements.txt` and `.gitignore` to a GitHub repository. Do **not** push `.env`.
2. Go to https://share.streamlit.io, sign in with GitHub and click **Create app**.
3. Select the repository, branch `main` and main file `app.py`.
4. Open **Advanced settings → Secrets** and add: `GROQ_API_KEY = "gsk_your_key_here"`
5. Click **Deploy**.

## Limitations
- **Fixes are suggestions, not guarantees.** For Python the app only checks that the corrected code *parses* (syntax check). It does not run the code, so it cannot prove the fix keeps the intended behaviour.
- The syntax check is available for Python only. Other languages are not checked.
- AI explanations can be wrong. Always read and test the result yourself.
- Free Groq limits apply (requests and tokens per minute and per day). On a public deployment all visitors share them.
- The fix feature is not streamed because JSON must be complete before it can be validated.
- The change-list check is strict on purpose (blank lines, spaces and string contents must be preserved), so "Suggest Fix" may occasionally fail after 3 tries with a clear message. Click it again.
- The check proves the change list matches the suggested code. It cannot prove the suggested code keeps the program's intended behaviour.
- Technical details of failed AI requests are written to the server log; students only see a short message.
- Input limits: 6000 characters of code, 3000 characters of error message.
- Changing the code, error message or language clears the previous results and chat on purpose.