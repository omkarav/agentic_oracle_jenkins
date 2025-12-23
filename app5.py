# app.py (enhanced for AWR reports - full integration)
# Oracle + Jenkins AI Console — Chat-style UI, inline editable SQL, DB switch
# Single-file Streamlit app (replace credentials / adapt run_oracle_query if needed)

import time
import os
import json
import pandas as pd
import streamlit as st
import jenkins
from dotenv import load_dotenv
import difflib
from difflib import get_close_matches
import re  # For parsing time from user input
from datetime import datetime
import dateutil.parser



# Autogen
from autogen import AssistantAgent, ConversableAgent

# Oracle query runner (your module) - must accept either run_oracle_query(sql) or run_oracle_query(sql, db=...)
from oracle_runner import run_oracle_query, get_db_list, generate_awr_report, get_snapshots_for_time

# ------------------------- 
# Environment & page config
# -------------------------
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")

# Optional certs (kept from your environment)
os.environ["REQUESTS_CA_BUNDLE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"
os.environ["SSL_CERT_FILE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"

st.set_page_config(page_title="Oracle + Jenkins AI Console", layout="wide")

# =========================
# LLM config (unchanged)
# =========================
common_llm_config = {
    "config_list": [{"model": "gpt-4o-mini", "api_key": openai_api_key}],
    "temperature": 0,
    "timeout": 120,
}

# Intent classifier agent (enhanced for AWR)
intent_agent = ConversableAgent(
    name="intent_classifier",
    llm_config=common_llm_config,
    human_input_mode="NEVER",
)

# New: DB resolver agent for fuzzy/LLM-based DB matching
db_resolver_agent = AssistantAgent(
    name="db_resolver",
    llm_config=common_llm_config,
    system_message=(
        "You are a DB connection resolver. Given a user request to connect/switch DB "
        "and a list of available DBs, suggest the best match. If exact match, return it. "
        "If close, suggest with reason. If none, suggest 'DEFAULT'. "
        "Output ONLY JSON: {'suggested_db': 'DB_NAME', 'confidence': 0-1, 'reason': 'brief explanation'}"
    ),
    human_input_mode="NEVER",
)

# Oracle SQL generator agent (enhanced for AWR awareness)
code_writer_agent = AssistantAgent(
    name="code_writer_agent",
    llm_config=common_llm_config,
    code_execution_config=False,
    system_message=(
        "You are an expert Oracle SQL and PLSQL generator. "
        "The user will describe what they want in natural language. "
        "If the request mentions 'AWR report', generate PL/SQL to call DBMS_WORKLOAD_REPOSITORY.AWR_REPORT_HTML "
        "with appropriate snapshot bounds based on time (e.g., last 3 hours). "
        "Extract time from request (e.g., '3 hours' → INTERVAL '3' HOUR). Default to last 1 hour if unspecified. "
        "For raw queries, output **only** a valid Oracle SQL query. "
        "For AWR reports, output PL/SQL block that returns HTML report as CLOB. "
        "No markdown, no Python, no explanations, no comments. "
        "Output plain SQL/PLSQL and no semicolon (;)."
    ),
    human_input_mode="NEVER",
)

code_executor_agent = ConversableAgent(
    name="code_executor_agent",
    llm_config=False,
    human_input_mode="ALWAYS",
    default_auto_reply="Please continue. If done, reply TERMINATE."
)

# wrapper: accepts optional db param in JSON (enhanced for AWR)
# Updated approve_and_run_sql_wrapper in app.py (with detailed exception handling)
# In app.py — replace approve_and_run_sql_wrapper
def approve_and_run_sql_wrapper(arguments_json: str):
    try:
        args = json.loads(arguments_json)
        awr_hours = args.get("awr_hours")
        db = args.get("db")
    except Exception as e:
        return {"status": "error", "message": f"JSON parse error: {e}"}
    # 1) Check for custom time range (start_time → end_time)
    start_dt, end_dt = parse_custom_time_range(args.get("original_request", ""))

    if start_dt and end_dt:
        sql = f"""
            SELECT MIN(snap_id) AS start_snap,
                MAX(snap_id) AS end_snap
            FROM dba_hist_snapshot
            WHERE begin_interval_time >= TO_TIMESTAMP('{start_dt}', 'YYYY-MM-DD HH24:MI:SS')
            AND end_interval_time   <= TO_TIMESTAMP('{end_dt}', 'YYYY-MM-DD HH24:MI:SS')
        """
        snaps = run_oracle_query(sql, db)
        if not snaps or not snaps[0].get("START_SNAP") or not snaps[0].get("END_SNAP"):
            return {"status":"error","message":"No snapshots found for given time range"}

        start_snap = snaps[0]["START_SNAP"]
        end_snap = snaps[0]["END_SNAP"]

        return generate_awr_report(start_snap, end_snap, db)

    # 2) Fallback: last X hours (existing)
    if awr_hours is not None:
        hours = int(awr_hours)
        start_snap, end_snap = get_snapshots_for_time(hours, db)
        return generate_awr_report(start_snap, end_snap, db)

    # if awr_hours is not None:
    #     try:
    #         hours = int(awr_hours)
    #         start_snap, end_snap = get_snapshots_for_time(hours, db)
    #         if not start_snap or not end_snap:
    #             return {"status": "error", "message": f"No snapshots found in last {hours} hour(s). Try 'last 3 hours' or 'last 24 hours'."}
    #         return generate_awr_report(start_snap, end_snap, db, format_type="html")
    #     except Exception as e:
    #         return {"status": "error", "message": f"AWR failed: {str(e)}"}
    # else:
    #     sql = args.get("sql", "")
    #     return {"status": "ok", "result": run_oracle_query(sql, db=db)}
code_executor_agent.register_for_execution(name="approve_and_run_sql")(approve_and_run_sql_wrapper)

# -------------------------
# Jenkins (kept, but not changed)
# -------------------------
jenkins_server = jenkins.Jenkins("http://localhost:9020", username="dba", password="113bb934053435f19fa62d94f8c79a108c")

# Caching helpers (use leading _client to avoid Streamlit hashing problems)
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
@st.cache_resource
def build_job_map(_client):
    jobs = fetch_all_job_details(_client=_client)
    text = "\n".join(f"{j['name']} :: {j['description']}" for j in jobs)

    system_msg = f"""
You are summarizing Jenkins jobs to make selection easier.

Rewrite the list below into grouped bullet points.
Focus on purpose only, remove noise.
Keep under 1200 tokens.

Jobs:
{text}
"""
    try:
        return job_summary_agent.generate_reply([
            {"role": "system", "content": system_msg},
            {"role": "user", "content": "Return grouped summary text only."}
        ])
    except:
        return ""

def find_jenkins_job_by_name(job_name, client):
    for j in fetch_all_job_details(_client=client):
        if j["name"] == job_name:
            return j
    return None

def run_jenkins_job_and_get_output(job_name, params, client, poll_interval=2):
    """
    Trigger a Jenkins job, wait for it to start, then wait for it to finish,
    and finally return console output + status.
    """

    try:
        # 1. Trigger job
        queue_id = client.build_job(job_name, params)
    except Exception as e:
        return {
            "status": "ERROR",
            "error": f"Failed to trigger job: {str(e)}",
            "queue_id": None,
            "build_number": None,
            "console": "",
        }

    # 2. Wait for Jenkins to assign a build number
    build_number = None
    for _ in range(30):  # up to ~60 sec
        try:
            qi = client.get_queue_item(queue_id)
            if "executable" in qi and qi["executable"]:
                build_number = qi["executable"]["number"]
                break
        except:
            pass
        time.sleep(poll_interval)

    if build_number is None:
        return {
            "status": "ERROR",
            "error": "Timed out waiting for Jenkins to start the build",
            "queue_id": queue_id,
            "build_number": None,
            "console": "",
        }

    # 3. Wait for build to complete
    for _ in range(180):  # up to 6 min
        try:
            bi = client.get_build_info(job_name, build_number)
            if not bi.get("building", True):
                break
        except:
            pass
        time.sleep(poll_interval)

    # 4. Fetch status and console output
    try:
        bi = client.get_build_info(job_name, build_number)
        status = bi.get("result", "UNKNOWN")
        console = client.get_build_console_output(job_name, build_number)
    except Exception as e:
        return {
            "status": "ERROR",
            "error": f"Failed while reading build output: {str(e)}",
            "queue_id": queue_id,
            "build_number": build_number,
            "console": "",
        }

    return {
        "status": status,
        "error": None,
        "queue_id": queue_id,
        "build_number": build_number,
        "console": console,
    }

def run_jenkins_job(job_name, params, client):
    return client.build_job(job_name, params)

# LLM agents for Jenkins matching / analysis (kept)
job_summary_agent = ConversableAgent(name="job_summary", llm_config=common_llm_config)
job_selector_agent = ConversableAgent(name="job_selector", llm_config=common_llm_config)

# Simplified failure analyzer that uses LLM
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
# Matching logic (LLM-first, fallback)
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

    # local fallback scoring
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

# New: LLM-based DB resolver
def resolve_db_with_llm(user_request: str, available_dbs: list):
    """Uses LLM to suggest the best DB match for the user request."""
    db_list_text = ", ".join(available_dbs)
    prompt = f"Available DBs: {db_list_text}\nUser wants to connect to: {user_request}"
    try:
        reply = db_resolver_agent.generate_reply([{"role": "user", "content": prompt}]).strip()
        parsed = None
        try:
            parsed = json.loads(reply)
        except Exception:
            import re
            m = re.search(r"(\{.*\})", reply, flags=re.DOTALL)
            if m:
                parsed = json.loads(m.group(1))
        if isinstance(parsed, dict) and "suggested_db" in parsed:
            suggested = parsed["suggested_db"].upper()
            if suggested in [d.upper() for d in available_dbs]:
                return suggested, parsed.get("confidence", 0), parsed.get("reason", "")
    except Exception:
        pass
    return None, 0, "LLM resolution failed"

# -------------------------
# Intent classifier fallback (enhanced for AWR)
# -------------------------
def classify_intent(text):
    t = text.lower()
    if "awr report" in t or "awr" in t:
        return "awr_report"  # NEW: Dedicated intent
    if "job" in t or "pipeline" in t or "deploy" in t or "build" in t or "jenkins" in t:
        return "jenkins"
    if "connect to " in t or "switch to " in t:
        return "connect_db"
    if "select " in t or " from " in t or "table" in t or "schema" in t:
        return "oracle"
    classifier_prompt = f"""
You are an intent classifier. The user may ask about EITHER:
[ORIGINAL RULES YOU PROVIDED]
User request:
\"\"\"{text}\"\"\"
"""
    try:
        reply = intent_agent.generate_reply([{"role": "system", "content": classifier_prompt}])
        parsed = None
        try:
            parsed = json.loads(reply)
        except Exception:
            parsed = None
        if parsed and isinstance(parsed, dict) and "intent" in parsed:
            return parsed["intent"]
        # fallback textual
        lower = reply.lower()
        if "oracle" in lower or "awr" in lower:
            return "oracle"
        if "jenkins" in lower:
            return "jenkins"
    except Exception:
        pass
    return "oracle"

# NEW: Parse time from user request (e.g., "last 3 hours" → 3)
def parse_custom_time_range(text: str):
    """
    Detect phrases like:
    'between NOV 20 1:00 AM and NOV 20 3:00 AM'
    'from 2025-11-20 01:00 to 2025-11-20 03:00'
    Returns (start_time, end_time) as datetime or (None, None)
    """
    text = text.lower()

    if "between" in text and "and" in text:
        try:
            part = text.split("between", 1)[1]
            start_str, end_str = part.split("and", 1)

            start_dt = dateutil.parser.parse(start_str.strip(), fuzzy=True)
            end_dt = dateutil.parser.parse(end_str.strip(), fuzzy=True)

            return start_dt, end_dt
        except:
            return None, None

    if "from" in text and "to" in text:
        try:
            part = text.split("from", 1)[1]
            start_str, end_str = part.split("to", 1)

            start_dt = dateutil.parser.parse(start_str.strip(), fuzzy=True)
            end_dt = dateutil.parser.parse(end_str.strip(), fuzzy=True)

            return start_dt, end_dt
        except:
            return None, None

    return None, None

def parse_time_from_request(text: str) -> int:
    """Extract hours from phrases like 'last X hours/days'. Defaults to 1."""
    patterns = [
        r"(\d+)\s*hour(?:s?)?",
        r"(\d+)\s*hr",
        r"last\s+(\d+)\s*hour(?:s?)?",
        r"(\d+)\s*day(?:s?)?",  # Convert days to hours: 24 * days
    ]
    for pattern in patterns:
        match = re.search(pattern, text.lower())
        if match:
            num = int(match.group(1))
            if "day" in pattern:
                return num * 24
            return num
    return 1  # Default

# -------------------------
# Session state initialization (safe BEFORE widgets)
# -------------------------
if "chat" not in st.session_state:
    st.session_state["chat"] = []  # oldest first; newest appended to end
if "job_map" not in st.session_state:
    st.session_state["job_map"] = ""  # will be built lazily

# DB list + current DB - now loaded dynamically from oracle_runner
if "dbs" not in st.session_state:
    st.session_state["dbs"] = get_db_list()
if "current_db" not in st.session_state:
    st.session_state["current_db"] = st.session_state["dbs"][0]

# ensure job_map cached resource is ready (build lazily)
try:
    if not st.session_state["job_map"]:
        st.session_state["job_map"] = build_job_map(_client=jenkins_server)
except Exception:
    st.session_state["job_map"] = ""

# -------------------------
# Enhanced Styling (chat-like with boxes and shadows)
# -------------------------
st.markdown(
    """
<style>
.chat-container {
  background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
  border-radius: 16px;
  padding: 20px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
  margin: 20px 0;
  border: 1px solid rgba(255, 255, 255, 0.2);
}
.chat-area {
  max-height: 60vh;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  background: white;
  border-radius: 12px;
  box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.05);
}
.user-bubble {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  align-self: flex-end;
  padding: 16px 20px;
  border-radius: 20px 20px 5px 20px;
  max-width: 80%;
  white-space: pre-wrap;
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
  position: relative;
}
.user-bubble::after {
  content: '';
  position: absolute;
  bottom: 0;
  right: 10px;
  width: 0;
  height: 0;
  border-left: 10px solid transparent;
  border-right: 0;
  border-bottom: 10px solid transparent;
  border-top: 10px solid #667eea;
}
.assistant-block {
  background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
  color: white;
  align-self: flex-start;
  padding: 16px 20px;
  border-radius: 20px 20px 20px 5px;
  max-width: 80%;
  white-space: pre-wrap;
  box-shadow: 0 4px 12px rgba(240, 147, 251, 0.3);
  font-family: monospace;
  position: relative;
}
.assistant-block::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 10px;
  width: 0;
  height: 0;
  border-left: 0;
  border-right: 10px solid transparent;
  border-bottom: 10px solid transparent;
  border-top: 10px solid #f093fb;
}
.intent-badge {
  display: inline-block;
  padding: 8px 12px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 20px;
  margin: 8px auto;
  color: white;
  font-weight: 600;
  text-align: center;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}
.meta { 
  font-size: 12px; 
  color: rgba(255, 255, 255, 0.8); 
  margin-bottom: 8px; 
  font-style: italic;
}
.db-badge { 
  padding: 12px 16px; 
  background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); 
  border-radius: 25px; 
  color: white; 
  font-weight: 700; 
  text-align: center;
  box-shadow: 0 4px 15px rgba(79, 172, 254, 0.4);
  display: inline-block;
}
.confidence-bar { 
  background: rgba(255, 255, 255, 0.2); 
  height: 6px; 
  border-radius: 3px; 
  margin: 6px 0; 
}
.confidence-fill { 
  background: linear-gradient(90deg, #00b09b, #96c93d); 
  height: 100%; 
  border-radius: 3px; 
  transition: width 0.3s ease;
}
.awr-report {
  background: #f9f9f9;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 16px;
  max-height: 60vh;
  overflow-y: auto;
  font-family: monospace;
  font-size: 12px;
}
textarea { 
  font-family: monospace; 
  border: 1px solid #ddd;
  border-radius: 8px;
  box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.05);
}
.stButton > button {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border: none;
  border-radius: 20px;
  padding: 10px 20px;
  font-weight: 600;
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
  transition: all 0.2s;
  width: 100%;
}
.stButton > button:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4);
}
</style>
""", unsafe_allow_html=True
)

# -------------------------
# helper: keep chat to last N
# -------------------------
MAX_CHAT = 5
def append_chat_entry(entry):
    st.session_state["chat"].append(entry)
    while len(st.session_state["chat"]) > MAX_CHAT:
        st.session_state["chat"].pop(0)

# -------------------------
# process request callback (enhanced for AWR)
# -------------------------
def process_request_callback():
    task = st.session_state.get("task_input", "") or ""
    task = task.strip()
    if not task:
        return

    # handle quick "connect to X" command (explicit)
    lower = task.lower()
    if lower.startswith("connect to ") or lower.startswith("switch to "):
        # parse name after
        name = task.split(None, 2)[-1].strip().upper()
        available_dbs = st.session_state["dbs"]
        
        # First, try exact or fuzzy match
        matches = get_close_matches(name, available_dbs, n=1, cutoff=0.6)  # Higher cutoff for better matches
        if matches:
            selected_db = matches[0]
            confidence = 1.0
            reason = "Exact or close fuzzy match"
        else:
            # Fallback to LLM resolver
            suggested, conf, reason = resolve_db_with_llm(task, available_dbs)
            if suggested:
                selected_db = suggested
                confidence = conf
            else:
                selected_db = "DEFAULT"
                confidence = 0
                reason = "No match found; defaulting"
        
        st.session_state["current_db"] = selected_db
        # add a chat entry noting the connection change
        entry = {
            "id": int(time.time() * 1000),
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "request": task,
            "intent": "connect_db",
            "oracle_sql": None,
            "oracle_result": {
                "status": "ok" if confidence > 0.5 else "warning",
                "message": f"Switched to {selected_db} (confidence: {confidence:.2f} - {reason})"
            },
            "jenkins_matches": None,
            "jenkins_run": None,
            "_db_confidence": confidence,
            "_db_reason": reason
        }
        append_chat_entry(entry)
        st.session_state["task_input"] = ""
        return

    # regular classification
    intent = classify_intent(task)
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
    if intent == "awr_report":

    # Detect hours OR from–to range
        hours = parse_time_from_request(task)
        start_dt, end_dt = parse_custom_time_range(task)

        # Build spinner text
        if start_dt and end_dt:
            spinner_msg = (
                f"Generating AWR report from "
                f"{start_dt.strftime('%d-%b-%Y %I:%M %p')} to "
                f"{end_dt.strftime('%d-%b-%Y %I:%M %p')}..."
            )
        else:
            spinner_msg = f"Generating AWR report for last {hours} hour(s)..."

        # Build payload BEFORE we call the wrapper
        payload = {
            "awr_hours": hours,
            "db": st.session_state["current_db"],
            "original_request": task
        }

        # Execute with spinner
        with st.spinner(spinner_msg):
            exec_res = approve_and_run_sql_wrapper(json.dumps(payload))

        # Store results
        entry["oracle_result"] = exec_res
        entry["oracle_sql"] = spinner_msg
        entry["_last_exec_ts"] = time.time()

        append_chat_entry(entry)
        st.session_state["task_input"] = ""
        return

    # if intent == "awr_report":
    #     # NEW: Parse time and generate AWR (executes immediately)
    #     hours = parse_time_from_request(task)
    #     # Detect custom range or hours
    #     start_dt, end_dt = parse_custom_time_range(task)

    #     if start_dt and end_dt:
    #         spinner_msg = f"Generating AWR report from {start_dt.strftime('%d-%b %Y %I:%M %p')} to {end_dt.strftime('%d-%b %Y %I:%M %p')}..."
    #     else:
    #         spinner_msg = f"Generating AWR report for last {hours} hour(s)..."

    #     with st.spinner(spinner_msg):
    #         exec_res = approve_and_run_sql_wrapper(json.dumps(payload))

    #     # with st.spinner(f"Generating AWR report for last {hours} hours..."):
    #         try:
    #             payload = {
    #                     "awr_hours": hours,
    #                     "db": st.session_state["current_db"],
    #                     "original_request": task
    #                 }

    #             # payload = {"awr_hours": hours, "db": st.session_state["current_db"]}
    #             exec_res = approve_and_run_sql_wrapper(json.dumps(payload))
    #             entry["oracle_result"] = exec_res
    #             entry["oracle_sql"] = f"AWR Report for last {hours} hours (snapshots auto-detected)"
    #             # Mark as executed
    #             entry["_last_exec_ts"] = time.time()
    #         except Exception as e:
    #             entry["oracle_result"] = {"status": "error", "message": str(e)}
    elif intent == "oracle":
        try:
            sql = code_writer_agent.generate_reply([{"role": "user", "content": task}]).strip()
        except Exception as e:
            sql = f"-- SQL generation failed: {e}"
        if sql and not sql.endswith(";"):
            sql += ";"
        entry["oracle_sql"] = sql
    elif intent == "jenkins":
        try:
            matches = llm_fast_match(task, jenkins_server)
        except Exception:
            matches = []
        entry["jenkins_matches"] = matches or []
    else:
        # fallback -> treat as oracle for now
        try:
            sql = code_writer_agent.generate_reply([{"role": "user", "content": task}]).strip()
        except Exception as e:
            sql = f"-- SQL generation failed: {e}"
        if sql and not sql.endswith(";"):
            sql += ";"
        entry["oracle_sql"] = sql
        entry["intent"] = "oracle"

    append_chat_entry(entry)
    # clear input safely inside callback
    st.session_state["task_input"] = ""

# -------------------------
# Header: title only (DB selector moved to bottom)
# -------------------------
st.title("Oracle + Jenkins AI Console")
st.write("Latest messages appear at the bottom. Edit generated SQL inline and Execute from the same block.")

st.markdown("---")

# -------------------------
# Chat area: oldest -> newest (newest at bottom) - enhanced for AWR
# -------------------------
chat_holder = st.container()
with chat_holder:
    st.markdown("<div class='chat-container'>", unsafe_allow_html=True)
    st.markdown("<div class='chat-area'>", unsafe_allow_html=True)
    for entry in st.session_state["chat"]:
        uid = entry["id"]
        # user bubble
        st.markdown(f"<div class='user-bubble'><div class='meta'>You · {entry['ts']}</div><div>{entry['request']}</div></div>", unsafe_allow_html=True)

        # show detected intent
        st.markdown(f"<div class='intent-badge'>Detected intent: {entry.get('intent','')}</div>", unsafe_allow_html=True)

        # handle connect_db entries (show status message)
        if entry.get("intent") == "connect_db":
            result = entry.get("oracle_result", {})
            status = result.get("status", "info")
            message = result.get("message", "Database connection change recorded.")
            if status == "ok":
                st.success(message)
            elif status == "warning":
                st.warning(message)
            else:
                st.error(message)
            
            # Show confidence if available (from LLM)
            if "_db_confidence" in entry:
                conf = entry["_db_confidence"]
                reason = entry.get("_db_reason", "")
                st.markdown(f"**LLM Confidence:** {conf:.2f}")
                st.markdown(f"<div class='confidence-bar'><div class='confidence-fill' style='width: {conf*100}%'></div></div>", unsafe_allow_html=True)
                if reason:
                    st.caption(reason)
            
            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            continue

        # NEW: AWR report flow
        elif entry.get("intent") == "awr_report" and entry.get("oracle_result"):
            result = entry.get("oracle_result")
            st.markdown(f"**{entry.get('oracle_sql', 'AWR Report')}**")
            if result.get("status") == "ok":
                report = result.get("report", "Report generated.")
                # st.markdown(f"<div class='awr-report'>{report}</div>", unsafe_allow_html=True)
                st.success("AWR report generated successfully.")
                file_name = f"awr_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
                st.download_button(
                label="Download AWR Report (HTML)",
                data=report,
                file_name=file_name,
                mime="text/html",
                key=f"download_awr_{uid}"
            )

            else:
                st.error(result.get("message", "AWR generation failed."))
            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            continue

        # ORACLE flow: show single editable SQL area only (no duplicate)
        if entry.get("intent") == "oracle" and entry.get("oracle_sql") is not None:
            sql = entry.get("oracle_sql", "")
            edit_key = f"edit_sql_{uid}"
            # ensure initial value exists in session_state BEFORE widget created
            if edit_key not in st.session_state:
                st.session_state[edit_key] = sql

            # show single text_area bound to session_state (user edits here)
            st.text_area("Generated SQL (edit before executing):", value=st.session_state[edit_key], key=edit_key, height=160)

            exec_key = f"exec_sql_{uid}"
            if st.button("Execute SQL", key=exec_key):
                edited_sql = st.session_state.get(edit_key, sql)
                with st.spinner("Executing SQL..."):
                    # pass selected DB context
                    payload = {"sql": edited_sql, "db": st.session_state["current_db"]}
                    exec_res = approve_and_run_sql_wrapper(json.dumps(payload))
                # update the entry in place (persist result)
                entry["oracle_sql"] = edited_sql
                entry["oracle_result"] = exec_res
                # mark that this entry had a recent execution so UI shows result below
                entry["_last_exec_ts"] = time.time()

                # display the result right away
                if isinstance(exec_res, dict) and exec_res.get("status") == "ok":
                    out = exec_res.get("result")
                    if isinstance(out, list) and len(out) == 0:
                        st.warning("Query executed successfully, but no rows were returned. This could be due to no matching data, permissions, or an empty result set.")
                    elif isinstance(out, list) and out and isinstance(out[0], dict):
                        st.success("Query executed successfully — showing results below.")
                        st.dataframe(pd.DataFrame(out))
                    else:
                        st.success("Query executed successfully.")
                        st.write(out)
                else:
                    st.error(exec_res.get("message", str(exec_res)))

            # If there's a previously stored execution result and we didn't just display it (avoid double show),
            # show it once (previous execution). We'll show it if it exists and we are not in the same render that just executed:
            if entry.get("oracle_result") and "_last_exec_ts" in entry:
                # if last_exec_ts is older than 0.5s we treat it as previous execution and show it (this avoids showing twice in same run)
                if time.time() - entry["_last_exec_ts"] > 0.6:
                    res = entry["oracle_result"]
                    st.markdown("**Previous Execution Result:**")
                    if isinstance(res, dict) and res.get("status") == "ok":
                        out = res.get("result")
                        if isinstance(out, list) and len(out) == 0:
                            st.warning("No rows returned in previous execution.")
                        elif isinstance(out, list) and out and isinstance(out[0], dict):
                            st.dataframe(pd.DataFrame(out))
                        else:
                            st.write(out)
                    elif isinstance(res, dict) and "error" in res:
                        st.error(res.get("error"))
                    else:
                        out = res
                        if isinstance(out, list) and out and isinstance(out[0], dict):
                            st.dataframe(pd.DataFrame(out))
                        else:
                            st.write(out)

        # JENKINS flow
        elif entry.get("intent") == "jenkins":
            matches = entry.get("jenkins_matches") or []
            st.markdown("<div class='assistant-block'><div class='meta'>Jenkins Matches</div>", unsafe_allow_html=True)
            if matches:
                st.markdown("<ol>" + "".join(f"<li>{m}</li>" for m in matches) + "</ol>", unsafe_allow_html=True)
            else:
                st.markdown("<div>(no matches)</div>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)

            if matches:
                sel_key = f"sel_job_{uid}"
                if sel_key not in st.session_state:
                    st.session_state[sel_key] = matches[0]
                selected_job = st.selectbox("Select job to inspect / run:", options=matches, key=sel_key)
                job_def = find_jenkins_job_by_name(selected_job, jenkins_server)
                if job_def:
                    st.markdown(f"**Job:** `{job_def['name']}`")
                    st.write(job_def.get("description", "(no description)"))
                    params = {}
                    for p in job_def.get("parameters", []):
                        pname = p["name"]
                        wkey = f"param_{uid}_{pname}"
                        if wkey not in st.session_state:
                            st.session_state[wkey] = p.get("default", "")
                        if "Boolean" in p.get("type", ""):
                            params[pname] = st.checkbox(pname, key=wkey)
                        else:
                            params[pname] = st.text_input(pname, value=st.session_state[wkey], key=wkey)
                    run_key = f"run_job_{uid}"
                    if st.button("Run Job Now", key=run_key):
                        with st.spinner("Running Jenkins job..."):
                            run_result = run_jenkins_job_and_get_output(job_def["name"], params, jenkins_server)
                        entry["jenkins_run"] = {
                            "job": job_def["name"],
                            "params": params,
                            "queue_id": run_result.get("queue_id"),
                            "build_number": run_result.get("build_number"),
                            "status": run_result.get("status"),
                            "console": run_result.get("console"),
                            "error": run_result.get("error"),
                        }
                        if run_result.get("error"):
                            st.error(run_result.get("error"))
                        else:
                            st.success(f"Build {run_result.get('build_number')} finished with status {run_result.get('status')}")
                            st.subheader("Console Output")
                            st.code(run_result.get("console", ""))
                            if run_result.get("status") == "FAILURE":
                                ai_help = analyze_jenkins_failure(run_result.get("console", ""))
                                if ai_help:
                                    st.subheader("AI-Detected Root Cause")
                                    st.write(ai_help.get("root_cause", ""))
                                    st.subheader("Failing Line / Command")
                                    st.code(ai_help.get("failed_line", ""))
                                    st.subheader("Suggested Fix")
                                    st.markdown(ai_help.get("suggestion", ""))
                                else:
                                    st.warning("AI could not analyze this failure. Check console for errors.")

        else:
            st.info("No action for this message (unsupported intent).")

        # small spacer
        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

# -------------------------
# Bottom input box (like ChatGPT) - button below input
# -------------------------
st.markdown("---")
# ensure key exists first (avoid post-creation set)
if "task_input" not in st.session_state:
    st.session_state["task_input"] = ""

st.text_input("Type your request here:", key="task_input", placeholder="Example: show all tablespaces OR run cleanup job OR connect to PLAB_CM OR generate AWR report for last 3 hours")
st.button(" Process Request", on_click=process_request_callback)

# -------------------------
# Connected DB display only (simplified)
# -------------------------
st.markdown("---")
st.markdown(f"<div class='db-badge'> Connected to: {st.session_state['current_db']} </div>", unsafe_allow_html=True)

st.caption("Tip: Generated SQL is editable inline; click Execute to run against the connected DB. Use 'connect to <DB>' to switch (LLM helps resolve fuzzy names). Edit db_config.json for new DBs. Say 'generate AWR report for last X hours' for full reports.")

# END OF FILE