# app.py
# Oracle + Jenkins AI Console — Chat-style UI, inline editable SQL, DB switch (LLM-only)
# Keep your agents and system messages unchanged.

import time
import os
import json
import pandas as pd
import streamlit as st
import jenkins
from dotenv import load_dotenv
import difflib
from difflib import get_close_matches

# Autogen (unchanged)
from autogen import AssistantAgent, ConversableAgent

# oracle runner - must provide run_oracle_query(sql, db=None) and load_db_config()
from oracle_runner_ORIG import run_oracle_query, load_db_config

# -------------------------
# Environment & page config
# -------------------------
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")

# Optional certs (keep if you need them)
os.environ.setdefault("REQUESTS_CA_BUNDLE", r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt")
os.environ.setdefault("SSL_CERT_FILE", r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt")

st.set_page_config(page_title="Oracle + Jenkins AI Console", layout="wide")

# =========================
# LLM config (unchanged)
# =========================
common_llm_config = {
    "config_list": [{"model": "gpt-4o-mini", "api_key": openai_api_key}],
    "temperature": 0,
    "timeout": 120,
}

# Intent classifier agent (unchanged)
intent_agent = ConversableAgent(
    name="intent_classifier",
    llm_config=common_llm_config,
    human_input_mode="NEVER",
)

# Oracle SQL generator agent (unchanged)
code_writer_agent = AssistantAgent(
    name="code_writer_agent",
    llm_config=common_llm_config,
    code_execution_config=False,
    system_message=(
        "You are an expert Oracle SQL and PLSQL generator. "
        "The user will describe what they want in natural language. "
        "You must output **only** a valid Oracle SQL query — "
        "no markdown, no Python, no explanations, no comments. "
        "Output plain SQL and no semicolon (;)."
    ),
    human_input_mode="NEVER",
)

code_executor_agent = ConversableAgent(
    name="code_executor_agent",
    llm_config=False,
    human_input_mode="ALWAYS",
    default_auto_reply="Please continue. If done, reply TERMINATE."
)

# wrapper: accepts optional db param in JSON
def approve_and_run_sql_wrapper(arguments_json: str):
    try:
        args = json.loads(arguments_json)
        sql = args.get("sql", "")
        db = args.get("db", None)
    except Exception:
        return {"status": "error", "message": "Invalid SQL payload"}

    try:
        try:
            result = run_oracle_query(sql, db=db) if db else run_oracle_query(sql)
        except TypeError:
            result = run_oracle_query(sql)
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}


code_executor_agent.register_for_execution(name="approve_and_run_sql")(approve_and_run_sql_wrapper)

# -------------------------
# Jenkins (kept)
# -------------------------
jenkins_server = jenkins.Jenkins("http://localhost:9020", username="dba", password="113bb934053435f19fa62d94f8c79a108c")

# -------------------------
# Jenkins helpers (kept)
# -------------------------
@st.cache_data(show_spinner=False)
def fetch_jobs_recursive(_client, folder=""):
    if _client is None:
        return []
    try:
        items = _client.get_jobs(folder) if folder else _client.get_jobs()
    except Exception:
        return []
    out = []
    for it in items:
        name = it.get("name")
        if not name:
            continue
        full = f"{folder}/{name}" if folder else name
        cls = it.get("_class", "")
        if "Folder" in cls:
            out.extend(fetch_jobs_recursive(_client, full))
        else:
            out.append(full)
    return out


@st.cache_data(show_spinner=False)
def fetch_all_job_details(_client):
    if _client is None:
        return []
    out = []
    for full in fetch_jobs_recursive(_client):
        try:
            info = _client.get_job_info(full)
        except Exception:
            continue
        desc = info.get("description", "") or "(no description)"
        params = []
        for a in info.get("actions", []):
            if "parameterDefinitions" in a:
                for p in a["parameterDefinitions"]:
                    params.append({
                        "name": p["name"],
                        "type": p.get("_class", ""),
                        "default": p.get("defaultParameterValue", {}).get("value", "")
                    })
        out.append({"name": full, "description": desc, "parameters": params})
    return out


def find_jenkins_job_by_name(job_name, client):
    for j in fetch_all_job_details(_client=client):
        if j["name"] == job_name:
            return j
    return None


def run_jenkins_job(job_name, params, client):
    return client.build_job(job_name, params)


# LLM agents for Jenkins (kept)
job_summary_agent = ConversableAgent(name="job_summary", llm_config=common_llm_config)
job_selector_agent = ConversableAgent(name="job_selector", llm_config=common_llm_config)


def run_jenkins_job_and_get_output(job_name, params, client, poll_interval=3):
    queue_id = client.build_job(job_name, params)
    return_data = {"queue_id": queue_id, "build_number": None, "status": None, "console": "", "error": None}

    build_num = None
    for _ in range(60):
        try:
            qitem = client.get_queue_item(queue_id)
            if "executable" in qitem and qitem["executable"]:
                build_num = qitem["executable"]["number"]
                break
        except Exception:
            pass
        time.sleep(poll_interval)

    if not build_num:
        return_data["error"] = "Timeout: Jenkins did not start the build."
        return return_data

    return_data["build_number"] = build_num

    console_text = ""
    build_status = None

    while True:
        try:
            info = client.get_build_info(job_name, build_num)
            build_status = info.get("result")
            new_console = client.get_build_console_output(job_name, build_num)
            if new_console != console_text:
                console_text = new_console
            if build_status is not None:
                break
        except Exception as e:
            return_data["error"] = f"Error reading build output: {e}"
            return return_data

        time.sleep(poll_interval)

    return_data["status"] = build_status
    return_data["console"] = console_text
    return return_data


def analyze_jenkins_failure(console_text):
    prompt = f"""
You are a Jenkins CI failure analysis expert.

Analyze the following Jenkins console log and identify:
1. The most likely root cause of the failure.
2. What exact line or command caused the failure.
3. What fix or action the user should take.
4. Keep the output short, clear, and actionable.

Console Log:
\"\"\"{console_text}\"\"\"

Return a JSON object:
{{"root_cause":"...","failed_line":"...","suggestion":"..."}}
"""
    try:
        reply = job_selector_agent.generate_reply([
            {"role": "system", "content": "Return JSON only. No explanations."},
            {"role": "user", "content": prompt}
        ]).strip()
        import re
        m = re.search(r"(\{.*\})", reply, flags=re.DOTALL)
        if m:
            return json.loads(m.group(1))
        return None
    except Exception:
        return None


# -------------------------
# Matching logic (kept)
# -------------------------
def llm_fast_match(query, client, top_n=10, min_accept=3):
    if not query or not query.strip():
        return []
    jobs = fetch_all_job_details(_client=client)
    if not jobs:
        return []
    valid_names = [j["name"] for j in jobs]
    job_list_text = "\n".join(f"{j['name']} :: {j['description']}" for j in jobs)
    prompt = f"""
You are a precise Jenkins job selector. DO NOT output chain-of-thought. Follow steps and return only one JSON:

1) Read the JOB list (JobName :: Description).
2) Extract intent & keywords from USER REQUEST.
3) Score & return top {top_n} jobs considering names+descriptions.

Return: {{ "matches": [...], "scores": {{job:score,...}}, "reasons": {{job:reason,...}} }}
JOBS:
{job_list_text}

USER REQUEST:
\"\"\"{query}\"\"\" 
"""
    try:
        reply = job_selector_agent.generate_reply([
            {"role": "system", "content": "Return a single JSON object only. No explanations."},
            {"role": "user", "content": prompt}
        ]).strip()
        parsed = None
        try:
            parsed = json.loads(reply)
        except Exception:
            import re
            m = re.search(r"(\{.*\})", reply, flags=re.DOTALL)
            if m:
                try:
                    parsed = json.loads(m.group(1))
                except Exception:
                    parsed = None
        if isinstance(parsed, dict) and "matches" in parsed and isinstance(parsed["matches"], list):
            cleaned = [m for m in parsed["matches"] if isinstance(m, str) and m in valid_names][:top_n]
            if len(cleaned) >= min_accept:
                return cleaned
    except Exception:
        parsed = None

    # fallback local scoring
    q = query.lower().strip()
    candidates = []
    for j in jobs:
        name = (j.get("name") or "").lower()
        desc = (j.get("description") or "").lower()
        score = 0.0
        if q in name:
            score += 4.0
        if q in desc:
            score += 2.5
        for tok in q.split():
            if tok in name:
                score += 1.5
            if tok in desc:
                score += 1.0
        try:
            sim = difflib.SequenceMatcher(None, q, name).ratio()
            score += sim * 1.5
        except Exception:
            pass
        if score > 0:
            candidates.append((score, j["name"]))
    if not candidates:
        all_names = [j["name"] for j in jobs]
        close = get_close_matches(q, all_names, n=top_n, cutoff=0.3)
        return close[:top_n]
    candidates.sort(key=lambda x: x[0], reverse=True)
    result = []
    seen = set()
    for _, name in candidates:
        if name not in seen:
            result.append(name)
            seen.add(name)
        if len(result) >= top_n:
            break
    return result


# -------------------------
# Intent classification via LLM ONLY
# -------------------------
def classify_intent_via_llm(text):
    classifier_prompt = f"""
You are an intent classifier. The user may ask about EITHER:

1) ORACLE DATABASE queries
2) JENKINS CI JOBS
3) CONNECT / SWITCH DATABASE

VERY IMPORTANT RULES:
- If user intent is to change the connected database, return intent = "connect_db" and also include a 'db' field with the desired DB NAME (prefer UPPERCASE short name).
- Otherwise return intent = "oracle" or "jenkins".
- Output MUST be strict JSON only.

Return examples:
{{ "intent": "oracle" }}
{{ "intent": "jenkins" }}
{{ "intent": "connect_db", "db": "PLAB_CM" }}

User request:
\"\"\"{text}\"\"\"
"""
    try:
        reply = intent_agent.generate_reply([{"role": "system", "content": classifier_prompt},
                                              {"role": "user", "content": text}]).strip()
        # parse JSON
        parsed = None
        try:
            parsed = json.loads(reply)
        except Exception:
            import re
            m = re.search(r"(\{.*\})", reply, flags=re.DOTALL)
            if m:
                try:
                    parsed = json.loads(m.group(1))
                except Exception:
                    parsed = None
        if isinstance(parsed, dict) and "intent" in parsed:
            return parsed
    except Exception:
        pass
    # fallback textual
    low = text.lower()
    if "job" in low or "jenkins" in low or "build" in low:
        return {"intent": "jenkins"}
    return {"intent": "oracle"}


# -------------------------
# Session state init
# -------------------------
if "chat" not in st.session_state:
    st.session_state["chat"] = []
if "dbs" not in st.session_state:
    st.session_state["dbs"] = ["DEFAULT"]  # updated by load_db_config if present
if "current_db" not in st.session_state:
    st.session_state["current_db"] = st.session_state["dbs"][0]

# Attempt to load db config file and merge names
try:
    db_conf = load_db_config()
    if isinstance(db_conf, dict) and db_conf:
        # add keys to session db list if missing (uppercased)
        for k in db_conf.keys():
            kn = k.upper()
            if kn not in [d.upper() for d in st.session_state["dbs"]]:
                st.session_state["dbs"].append(kn)
except Exception:
    db_conf = {}

# -------------------------
# Styling (chat-like)
# -------------------------
st.markdown("""
<style>
.chat-area { max-height: 62vh; overflow: auto; padding: 12px; display: flex; flex-direction: column; gap: 14px; }
.user-bubble { background: #d1e7ff; align-self: flex-end; padding: 12px 16px; border-radius: 12px; max-width: 86%; white-space: pre-wrap; }
.assistant-block { background: #f6f6f6; align-self: flex-start; padding: 12px 16px; border-radius: 12px; max-width: 86%; white-space: pre-wrap; font-family: monospace; }
.intent-badge { display:inline-block; padding:6px 10px; background:#efefef; border-radius:8px; margin:8px 0; color:#333; font-weight:600; }
.meta { font-size:12px; color:#666; margin-bottom:6px; }
.header-row { display:flex; align-items:center; justify-content:space-between; gap:16px; }
.db-badge { padding:6px 10px; background:#e9f7f0; border-radius:8px; color:#066; font-weight:700; }
.small-note { color:#666; font-size:13px; margin-top:6px; }
textarea { font-family: monospace; }
</style>
""", unsafe_allow_html=True)

# -------------------------
# append helper
# -------------------------
MAX_CHAT = 8
def append_chat_entry(entry):
    st.session_state["chat"].append(entry)
    while len(st.session_state["chat"]) > MAX_CHAT:
        st.session_state["chat"].pop(0)


# -------------------------
# Process request callback (uses LLM-only classifier)
# -------------------------
def process_request_callback():
    task = st.session_state.get("task_input", "") or ""
    task = task.strip()
    if not task:
        return

    # Ask the LLM-only classifier
    intent_data = classify_intent_via_llm(task)
    intent = intent_data.get("intent", "oracle")
    db_name = intent_data.get("db") or intent_data.get("db_name") or intent_data.get("database") or None

    entry = {
        "id": int(time.time() * 1000),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "request": task,
        "intent": intent,
        "oracle_sql": None,
        "oracle_result": None,
        "jenkins_matches": None,
        "jenkins_run": None,
    }

    # handle connect_db (LLM told us to connect)
    if intent == "connect_db":
        if db_name:
            norm = db_name.upper().replace(" ", "_")
            # try best match (close match to available dbs from config or session)
            candidates = [d.upper() for d in st.session_state.get("dbs", [])]
            matches = get_close_matches(norm, candidates, n=1, cutoff=0.1)
            if matches:
                chosen = matches[0]
                st.session_state["current_db"] = chosen
                entry["oracle_result"] = {"status": "ok", "message": f"Connected to {chosen}"}
            else:
                # If config has keys, try matching config keys
                conf_keys = [k.upper() for k in (db_conf.keys() if isinstance(db_conf, dict) else [])]
                matches = get_close_matches(norm, conf_keys, n=1, cutoff=0.1)
                if matches:
                    chosen = matches[0]
                    st.session_state["current_db"] = chosen
                    entry["oracle_result"] = {"status": "ok", "message": f"Connected to {chosen}"}
                    if chosen not in st.session_state["dbs"]:
                        st.session_state["dbs"].append(chosen)
                else:
                    entry["oracle_result"] = {"status": "error", "message": f"Unknown DB '{db_name}'"}
        else:
            entry["oracle_result"] = {"status": "error", "message": "No DB name parsed from intent."}

        append_chat_entry(entry)
        st.session_state["task_input"] = ""
        return

    # For oracle intent -> ask SQL generator agent
    if intent == "oracle":
        try:
            sql = code_writer_agent.generate_reply([{"role": "user", "content": task}]).strip()
        except Exception as e:
            sql = f"-- SQL generation failed: {e}"
        if sql and not sql.endswith(";"):
            sql += ";"
        entry["oracle_sql"] = sql

    # Jenkins intent -> find matches
    elif intent == "jenkins":
        try:
            matches = llm_fast_match(task, jenkins_server)
        except Exception:
            matches = []
        entry["jenkins_matches"] = matches or []

    else:
        # fallback treat as oracle
        try:
            sql = code_writer_agent.generate_reply([{"role": "user", "content": task}]).strip()
        except Exception as e:
            sql = f"-- SQL generation failed: {e}"
        if sql and not sql.endswith(";"):
            sql += ";"
        entry["oracle_sql"] = sql
        entry["intent"] = "oracle"

    append_chat_entry(entry)
    st.session_state["task_input"] = ""


# -------------------------
# Header + show connected DB (no manual dropdown)
# -------------------------
left_col, right_col = st.columns([0.7, 0.3])
with left_col:
    st.title("Oracle + Jenkins AI Console")
    st.write("Latest messages appear at the bottom. Edit generated SQL inline and Execute from the same block.")
with right_col:
    st.markdown("**Connected DB**")
    st.markdown(f"<div class='db-badge'> {st.session_state.get('current_db','DEFAULT')} </div>", unsafe_allow_html=True)

st.markdown("---")

# -------------------------
# Chat area (render messages)
# -------------------------
chat_holder = st.container()
with chat_holder:
    st.markdown("<div class='chat-area'>", unsafe_allow_html=True)
    for entry in st.session_state["chat"]:
        uid = entry["id"]
        # user bubble
        st.markdown(f"<div class='user-bubble'><div class='meta'>You · {entry['ts']}</div><div>{entry['request']}</div></div>", unsafe_allow_html=True)

        # show detected intent
        st.markdown(f"<div class='intent-badge'>Detected intent: {entry.get('intent','')}</div>", unsafe_allow_html=True)

        # connect_db entry status
        if entry.get("intent") == "connect_db":
            if entry.get("oracle_result") and entry["oracle_result"].get("status") == "ok":
                st.success(entry["oracle_result"].get("message"))
            elif entry.get("oracle_result") and entry["oracle_result"].get("status") == "error":
                st.error(entry["oracle_result"].get("message"))
            else:
                st.info("Database connection change recorded.")
            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            continue

        # Oracle flow
        if entry.get("intent") == "oracle" and entry.get("oracle_sql") is not None:
            sql = entry.get("oracle_sql", "")
            edit_key = f"edit_sql_{uid}"
            if edit_key not in st.session_state:
                st.session_state[edit_key] = sql

            # single editable area
            st.text_area("Generated SQL (edit before executing):", value=st.session_state[edit_key], key=edit_key, height=160)

            exec_key = f"exec_sql_{uid}"
            if st.button("Execute SQL", key=exec_key):
                edited_sql = st.session_state.get(edit_key, sql)
                with st.spinner("Executing SQL..."):
                    payload = {"sql": edited_sql, "db": st.session_state.get("current_db")}
                    exec_res = approve_and_run_sql_wrapper(json.dumps(payload))

                entry["oracle_sql"] = edited_sql
                entry["oracle_result"] = exec_res
                entry["_last_exec_ts"] = time.time()

                # display result now
                if isinstance(exec_res, dict) and exec_res.get("status") == "ok":
                    out = exec_res.get("result")
                    if isinstance(out, list) and out and isinstance(out[0], dict):
                        st.success("Query executed successfully — showing results below.")
                        st.dataframe(pd.DataFrame(out))
                    else:
                        st.success("Query executed successfully.")
                        st.write(out)
                else:
                    # show structured error if present
                    if isinstance(exec_res, dict) and exec_res.get("result") and isinstance(exec_res.get("result"), dict) and exec_res["result"].get("error"):
                        st.error(exec_res["result"]["error"])
                    else:
                        st.error(exec_res.get("message", str(exec_res)))

            # Show previous execution only once (avoid duplicate)
            if entry.get("oracle_result") and "_last_exec_ts" in entry:
                if time.time() - entry["_last_exec_ts"] > 0.6:
                    res = entry["oracle_result"]
                    st.markdown("**Previous Execution Result:**")
                    if isinstance(res, dict) and res.get("status") == "error":
                        st.error(res.get("message"))
                    else:
                        out = res.get("result")
                        if isinstance(out, list) and out and isinstance(out[0], dict):
                            st.dataframe(pd.DataFrame(out))
                        else:
                            st.write(out)

        # Jenkins flow
        elif entry.get("intent") == "jenkins":
            matches = entry.get("jenkins_matches") or []
            st.markdown("<div class='assistant-block'><div class='meta'>Jenkins Matches</div>", unsafe_allow_html=True)
            if matches:
                st.markdown("<ol>" + "".join(f"<li>{m}</li>" for m in matches) + "</ol>", unsafe_allow_html=True)
            else:
                st.markdown("<div>(no matches)</div>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.info("No action for this message (unsupported intent).")

        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

# -------------------------
# Bottom input box
# -------------------------
st.markdown("---")
if "task_input" not in st.session_state:
    st.session_state["task_input"] = ""

input_col, btn_col = st.columns([0.85, 0.15])
with input_col:
    st.text_input("Type your request here:", key="task_input", placeholder="Example: show all tablespaces OR run cleanup job")
with btn_col:
    st.button("Process Request", on_click=process_request_callback)

st.caption("Tip: Generated SQL is editable inline; click Execute to run against the connected DB.")
