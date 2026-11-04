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
| Fix suggestion generator | Validated JSON output: error type, reasoning, before/after table, corrected code, download button |
| Chat interface | Follow-up questions that remember the code, the error and the last 6 messages. Streamed live |

## Course concepts used
- **Prompt engineering** for technical explanations (role, strict format, "copy lines exactly")
- **Few-shot prompting**: a worked example is included in the explanation and fix prompts
- **Chain-of-thought** debugging (`reasoning_steps` and the four-step analysis)
- **Structured JSON output** with strict validation of values and types, and automatic retry
- **Streaming** responses for explanation, analysis and chat
- **Prompt-injection defence**: code and errors are wrapped in tags and treated as data

## Project structure
```
app.py            Streamlit interface + LLM calls
utils.py          Pure helpers (validation, error mapping, syntax check) - unit tested
test_utils.py     32 unit tests for utils.py
TEST_CASES.md     Manual test evidence (10+ cases)
requirements.txt  Dependencies
.env.example      Template for your API key
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

### Configuration
| Setting | Required | Default |
|---|---|---|
| `GROQ_API_KEY` | yes | none |
| `MODEL_NAME` | no | `openai/gpt-oss-120b` |

Settings are read from `.env` locally or from Streamlit secrets when deployed.

## Run the unit tests
```
python -m unittest test_utils -v
```

## Deploy on Streamlit Community Cloud
1. Push `app.py`, `utils.py`, `requirements.txt` and `.gitignore` to a GitHub repository. Do **not** push `.env`.
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
- Input limits: 6000 characters of code, 3000 characters of error message.
- Changing the code, error message or language clears the previous results and chat on purpose.
