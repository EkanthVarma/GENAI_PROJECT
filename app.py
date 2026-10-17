# streamlit run app.py
import os
import json
import time

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI


# ============================================================
# 1. Page config + configuration loading
# ============================================================
st.set_page_config(page_title="AI Code Explainer & Debugger",
                   page_icon="🛠️",
                   layout="wide")

load_dotenv()


def get_config_value(key, default=None):
    """Read from environment (.env, local) or st.secrets (Streamlit Cloud)."""
    value = os.getenv(key)
    if value:
        return value
    try:
        return st.secrets[key]
    except Exception:
        return default


GROQ_API_KEY = get_config_value("GROQ_API_KEY")
MODEL_NAME = get_config_value("MODEL_NAME", "openai/gpt-oss-120b")

if not GROQ_API_KEY:
    st.error(
        "GROQ_API_KEY is missing. Add it to a local `.env` file, or on Streamlit "
        "Community Cloud go to **Advanced Settings** and add:\n\n"
        "```\nGROQ_API_KEY = \"gsk_your_key\"\n```"
    )
    st.stop()


# ============================================================
# 2. Create the LLM client once when the app starts
# ============================================================
@st.cache_resource(show_spinner=False)
def get_llm_client():
    # Groq exposes an OpenAI-compatible API
    return OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_API_KEY)


client = get_llm_client()


# ============================================================
# 3. Session state
# ============================================================
if "explanation" not in st.session_state:
    st.session_state.explanation = ""
if "analysis" not in st.session_state:
    st.session_state.analysis = ""
if "fix" not in st.session_state:
    st.session_state.fix = None
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []


# ============================================================
# 4. Helper functions
# ============================================================
def number_lines(code):
    """Add line numbers so the model can refer to exact lines."""
    lines = code.strip("\n").split("\n")
    return "\n".join(f"{i}: {line}" for i, line in enumerate(lines, start=1))


def call_llm(system_prompt, user_prompt, max_tokens=900, temperature=0.2, attempts=3):
    """Send a request to the LLM, retrying temporary failures."""
    for attempt in range(1, attempts + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            answer = response.choices[0].message.content
            if not answer:
                raise ValueError("The model returned an empty response.")
            return answer.strip()
        except Exception as error:
            if attempt < attempts:
                time.sleep(3)
            else:
                return f"The AI service is temporarily unavailable. Please try again.\n\nDetails: {error}"


def extract_json(text):
    """Pull a JSON object out of the model reply (handles ```json fences)."""
    text = text.strip().replace("```json", "").replace("```", "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


# ============================================================
# 5. Feature: Line-by-line explanation (prompt engineering)
# ============================================================
def explain_code(code, language):
    system_prompt = (
        "You are a friendly programming tutor for complete beginners. "
        "Explain code in simple, plain English. Avoid jargon; if you must use a "
        "technical word, explain it in brackets. Never skip a line."
    )
    user_prompt = f"""
Explain this {language} code LINE BY LINE.

Format:
**Overall purpose:** one or two simple sentences.

**Line-by-line:**
Line 1: `<the line copied EXACTLY as written>` -> what it does in plain English
Line 2: ...
(blank lines can be skipped)

IMPORTANT: Copy every line exactly as it appears. Do NOT silently correct
mistakes. If a line has a mistake (for example a missing colon or bracket),
write "⚠️ Problem:" after that line and explain what is wrong.

**Key concepts used:** short bullet list of the ideas a beginner should learn.

CODE (with line numbers):
{number_lines(code)}
""".strip()
    return call_llm(system_prompt, user_prompt, max_tokens=2000)


# ============================================================
# 6. Feature: Error message analyzer (chain-of-thought)
# ============================================================
def analyze_error(code, error_message, language):
    system_prompt = (
        "You are a patient debugging tutor for beginners. "
        "Think step by step before concluding, and explain each step simply."
    )
    user_prompt = f"""
A beginner got an error while running this {language} code.

CODE (with line numbers):
{number_lines(code)}

ERROR MESSAGE:
{error_message.strip() or "None provided. Find the most likely mistake by reading the code yourself, and say if you find no mistake."}

Reason step by step using exactly these headings:
**Step 1 - What the error message says:** translate it into plain English (if none was provided, say what problem you found in the code).
**Step 2 - Where it happened:** which line number and why that line.
**Step 3 - Why it happened (root cause):** the real mistake, not just the symptom.
**Step 4 - How to avoid it next time:** one beginner-friendly tip.
""".strip()
    return call_llm(system_prompt, user_prompt, max_tokens=1800)


# ============================================================
# 7. Feature: Fix suggestion generator (structured JSON output)
# ============================================================
FIX_SCHEMA = """{
  "error_type": "short name, e.g. SyntaxError",
  "reasoning_steps": ["step 1 ...", "step 2 ...", "step 3 ..."],
  "root_cause": "one plain-English sentence",
  "changes": [
    {"line": 3, "before": "old line", "after": "new line", "reason": "why this fixes it"}
  ],
  "fixed_code": "the COMPLETE corrected code as a single string, use \\n for new lines",
  "explanation": "2-3 beginner-friendly sentences explaining the fix"
}"""


def suggest_fix(code, error_message, language):
    """Returns a dict with the keys from FIX_SCHEMA (or an 'error' key)."""
    system_prompt = (
        "You are a debugging assistant. You reply with ONE valid JSON object only. "
        "No markdown, no text before or after the JSON."
    )
    user_prompt = f"""
Debug this {language} code. Think step by step in "reasoning_steps" first,
then give the fix.

CODE (with line numbers):
{number_lines(code)}

ERROR MESSAGE (may be empty - if so, look for bugs yourself):
{error_message or "None provided"}

Return JSON in exactly this shape:
{FIX_SCHEMA}
""".strip()

    raw = ""
    for _ in range(2):  # one retry if the JSON is malformed
        raw = call_llm(system_prompt, user_prompt, max_tokens=2500, temperature=0.1)
        data = extract_json(raw)
        if data and "fixed_code" in data:
            data.setdefault("error_type", "Unknown")
            data.setdefault("reasoning_steps", [])
            data.setdefault("root_cause", "")
            data.setdefault("changes", [])
            data.setdefault("explanation", "")
            return data
    return {"error": "Could not get a valid JSON fix from the model.", "raw": raw}


# ============================================================
# 8. Feature: Chat interface (follow-up questions)
# ============================================================
def chat_reply(question, code, error_message, language):
    system_prompt = (
        "You are a friendly coding tutor helping a beginner. "
        "Use simple language and short examples. "
        f"The student is working on this {language} code:\n"
        f"{number_lines(code) if code.strip() else '(no code pasted yet)'}\n\n"
        f"Error message: {error_message.strip() or 'none'}\n"
        "Answer follow-up questions about this code or general programming concepts. "
        "If you suggest code, put it in a code block."
    )
    messages = [{"role": "system", "content": system_prompt}]
    messages += st.session_state.chat_history[-6:]  # keep only recent turns
    messages.append({"role": "user", "content": question})

    for attempt in range(1, 4):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                max_tokens=1500,
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()
        except Exception as error:
            if attempt < 3:
                time.sleep(3)
            else:
                return f"The AI service is temporarily unavailable.\n\nDetails: {error}"


def clear_session():
    st.session_state.explanation = ""
    st.session_state.analysis = ""
    st.session_state.fix = None
    st.session_state.chat_history = []


# ============================================================
# 9. Build the Streamlit interface
# ============================================================
st.title("🛠️ AI Code Explainer and Debugging Assistant")
st.caption("Paste your code and (optionally) the error message. Get a plain-language "
           "explanation, the reason for the error, and a corrected version.")

left_col, right_col = st.columns([1, 2])

with left_col:
    language = st.selectbox("Language", ["Python", "Java", "C", "C++", "JavaScript"])
    code = st.text_area("Code Input", height=280, placeholder="Paste your code here...")
    error_message = st.text_area("Error Message (optional)", height=120,
                                 placeholder="Paste the error / traceback here...")

    explain_clicked = st.button("Explain Code (line by line)", use_container_width=True)
    analyze_clicked = st.button("Analyze Error", use_container_width=True)
    fix_clicked = st.button("Suggest Fix", type="primary", use_container_width=True)
    clear_clicked = st.button("Clear Everything", use_container_width=True)

    if clear_clicked:
        clear_session()
        st.rerun()

    if explain_clicked or analyze_clicked or fix_clicked:
        if not code.strip():
            st.warning("Please paste some code first.")
        else:
            if explain_clicked:
                with st.spinner("Explaining your code..."):
                    st.session_state.explanation = explain_code(code, language)
            if analyze_clicked:
                with st.spinner("Analyzing the error step by step..."):
                    st.session_state.analysis = analyze_error(code, error_message, language)
            if fix_clicked:
                with st.spinner("Generating a fix..."):
                    st.session_state.fix = suggest_fix(code, error_message, language)

with right_col:
    tab_explain, tab_error, tab_fix, tab_chat = st.tabs(
        ["📖 Explanation", "🔍 Error Analysis", "✅ Fix Suggestion", "💬 Chat"]
    )

    with tab_explain:
        if st.session_state.explanation:
            st.markdown(st.session_state.explanation)
        else:
            st.info("Click **Explain Code** to get a line-by-line explanation.")

    with tab_error:
        if st.session_state.analysis:
            st.markdown(st.session_state.analysis)
        else:
            st.info("Paste an error message and click **Analyze Error**.")

    with tab_fix:
        fix = st.session_state.fix
        if not fix:
            st.info("Click **Suggest Fix** to get a corrected version of your code.")
        elif "error" in fix:
            st.error(fix["error"])
            with st.expander("Raw model output"):
                st.text(fix.get("raw", ""))
        else:
            st.subheader(f"Error type: {fix['error_type']}")
            st.markdown(f"**Root cause:** {fix['root_cause']}")

            with st.expander("Step-by-step reasoning", expanded=True):
                for i, step in enumerate(fix["reasoning_steps"], start=1):
                    st.markdown(f"{i}. {step}")

            if fix["changes"]:
                st.markdown("**What changed:**")
                st.table([
                    {
                        "Line": c.get("line", ""),
                        "Before": c.get("before", ""),
                        "After": c.get("after", ""),
                        "Why": c.get("reason", ""),
                    }
                    for c in fix["changes"] if isinstance(c, dict)
                ])

            st.markdown("**Corrected code:**")
            st.code(fix["fixed_code"], language=language.lower())
            st.markdown(f"**Explanation:** {fix['explanation']}")
            st.download_button("Download fixed code", fix["fixed_code"],
                               file_name="fixed_code.txt")

            with st.expander("View structured JSON output"):
                st.json(fix)

    with tab_chat:
        chat_container = st.container(height=380, border=True)
        with chat_container:
            for message in st.session_state.chat_history:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])

        question = st.chat_input("Ask a follow-up, e.g. 'Why do we need a colon here?'")
        if question:
            reply = chat_reply(question, code, error_message, language)
            st.session_state.chat_history.append({"role": "user", "content": question})
            st.session_state.chat_history.append({"role": "assistant", "content": reply})
            st.rerun()
