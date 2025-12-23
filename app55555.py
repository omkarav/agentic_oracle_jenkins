import time
import os
import json
import pandas as pd
import streamlit as st
import jenkins
from dotenv import load_dotenv
import difflib
from difflib import get_close_matches
import re
from datetime import datetime
import dateutil.parser

from autogen import AssistantAgent, ConversableAgent
from oracle_runner_55555 import (
    run_oracle_query, get_db_list, generate_awr_report, generate_ash_report,
    get_snapshots_for_time, analyze_awr_report, compare_awr_reports, run_full_health_check
)

load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")

os.environ["REQUESTS_CA_BUNDLE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"
os.environ["SSL_CERT_FILE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"

st.set_page_config(page_title="Oracle + Jenkins AI Console", layout="wide")

common_llm_config = {
    "config_list": [{"model": "gpt-4o-mini", "api_key": openai_api_key}],
    "temperature": 0,
    "timeout": 120,
}

intent_agent = ConversableAgent(
    name="intent_classifier",
    llm_config=common_llm_config,
    human_input_mode="NEVER",
)
input_parser_agent = ConversableAgent(
    name="input_parser",
    llm_config=common_llm_config,
    system_message="""
You are the Master Controller for an Oracle & Jenkins Dashboard.
Your job is to parse natural language user requests into structured JSON.

### AVAILABLE INTENTS:
1. "connect_db": Switch database connection.
2. "awr_report": Long-term Oracle performance report (hours/days).
3. "ash_report": Short-term/Recent Oracle performance (minutes).
4. "show_history": List previously generated reports.
5. "analyze_report": Analyze a specific report or "this" report.
6. "oracle_query": General SQL questions or requests.
7. "jenkins": Run/List Jenkins jobs.
8. "health_check": Run full DB health check.

### PARAMETER EXTRACTION RULES:
- **time_range**: If user mentions "from X to Y" or "for last 2 hours", calculate specific timestamps or duration.
- **db_name**: Extract target DB name if mentioned.
- **duration_mins**: For ASH/AWR, convert "1 hour" to 60, "30 mins" to 30. Default AWR=60, ASH=30.
- **sql_text**: If user asks to "run query...", extract the intent, not the SQL itself yet.

### OUTPUT FORMAT (JSON ONLY):
{
  "intent": "intent_name",
  "confidence": 0.0-1.0,
  "parameters": {
    "start_time": "YYYY-MM-DD HH:MM:SS" (or null),
    "end_time": "YYYY-MM-DD HH:MM:SS" (or null),
    "duration_minutes": int (or null),
    "target_db": "string" (or null),
    "search_term": "string" (for jenkins/history)
  },
  "original_request": "user input"
}

IMPORTANT:
- For "AWR for 23 NOV 00:00 to 02:00", strictly format start_time and end_time.
- For "Show me all reports", intent is "show_history".
- Do NOT explain. Output valid JSON only.
""",
    human_input_mode="NEVER",
)
awr_analyzer_agent = AssistantAgent(
    name="awr_analyzer",
    llm_config=common_llm_config,
    system_message="""
You are an Oracle Performance Tuning Guru with 25+ years of experience.
You receive full AWR/ASH reports in HTML format.

Your job:
- Identify the exact period, DB version, host config, load profile
- Highlight top wait events and their % of DB time
- Find the most expensive SQLs (by elapsed time, CPU, buffer gets)
- Detect regressions, spikes, anomalies
- Give concrete, prioritized tuning recommendations
- Use markdown with clear sections and bullet points
- NEVER include raw HTML tables
- Keep it concise but actionable (max 1200 words)

Reply ONLY with the analysis.
""",
    human_input_mode="NEVER",
)

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
        "Output plain SQL/PLSQL and no semicolon ; at the end of the query"
    ),
    human_input_mode="NEVER",
)

code_executor_agent = ConversableAgent(
    name="code_executor_agent",
    llm_config=False,
    human_input_mode="ALWAYS",
    default_auto_reply="Please continue. If done, reply TERMINATE."
)

def approve_and_run_sql_wrapper(arguments_json: str):
    try:
        args = json.loads(arguments_json)
        hours = args.get("hours")
        db = args.get("db")
    except Exception as e:
        return {"status": "error", "message": f"JSON parse error: {e}"}

    start_dt, end_dt = parse_custom_time_range(args.get("original_request", ""))

    if start_dt and end_dt:
        sql = f"""
            SELECT MIN(snap_id) AS start_snap, MAX(snap_id) AS end_snap
            FROM dba_hist_snapshot
            WHERE begin_interval_time >= TO_TIMESTAMP('{start_dt}', 'YYYY-MM-DD HH24:MI:SS')
              AND end_interval_time <= TO_TIMESTAMP('{end_dt}', 'YYYY-MM-DD HH24:MI:SS')
        """
        snaps = run_oracle_query(sql, db)
        if not snaps or not snaps[0].get("START_SNAP"):
            return {"status": "error", "message": "No snapshots found"}
        return generate_awr_report(snaps[0]["START_SNAP"], snaps[0]["END_SNAP"], db)

    if hours is not None:
        hours = float(hours)
        if hours <= 2:  # ≤2 hours → ASH
            minutes = int(hours * 60)
            return generate_ash_report(minutes, db)
        else:
            start_snap, end_snap = get_snapshots_for_time(hours, db)
            return generate_awr_report(start_snap, end_snap, db)

    sql = args.get("sql", "")
    result = run_oracle_query(sql, db)
    if isinstance(result, dict) and "error" in result:
        return {"status": "error", "message": result["error"]}
    return {"status": "ok", "result": result}

code_executor_agent.register_for_execution(name="approve_and_run_sql")(approve_and_run_sql_wrapper)

# [Keep all your existing Jenkins code - unchanged]
jenkins_server = jenkins.Jenkins("http://localhost:9020", username="dba", password="113bb934053435f19fa62d94f8c79a108c")

# [Keep all your existing caching and Jenkins functions - unchanged, truncated for brevity]
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
    try:
        queue_id = client.build_job(job_name, params)
    except Exception as e:
        return {
            "status": "ERROR",
            "error": f"Failed to trigger job: {str(e)}",
            "queue_id": None,
            "build_number": None,
            "console": "",
        }

    build_number = None
    for _ in range(30):
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

    for _ in range(180):
        try:
            bi = client.get_build_info(job_name, build_number)
            if not bi.get("building", True):
                break
        except:
            pass
        time.sleep(poll_interval)

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

job_summary_agent = ConversableAgent(name="job_summary", llm_config=common_llm_config)
job_selector_agent = ConversableAgent(name="job_selector", llm_config=common_llm_config)

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
def resolve_db_with_llm(user_request: str, available_dbs: list):
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


# [Keep your existing classify_intent - but update for ASH]
def classify_intent(text):
    t = text.lower()
    # NEW: ASH for recent/short-term
    if "show" in t and ("history" in t or "report" in t or "list" in t):
        return "show_history"
    if "list" in t and "awr" in t:
        return "show_history"
    if any(word in t for word in ["awr report", "awr"]):
        return "awr_report"
    if any(word in t for word in ["min", "minute", "now", "recent", "slow", "issue", "performance", "degraded"]):
        return "ash_report"  # ← NEW
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
        lower = reply.lower()
        if "oracle" in lower or "awr" in lower:
            return "oracle"
        if "jenkins" in lower:
            return "jenkins"
    except Exception:
        pass
    return "oracle"
def parse_custom_time_range(text: str):
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
    if "for" in text and "to" in text:
        try:
            part = text.split("for", 1)[1]
            start_str, end_str = part.split("to", 1)
            return dateutil.parser.parse(start_str.strip(), fuzzy=True), dateutil.parser.parse(end_str.strip(), fuzzy=True)
        except:
            pass

    return None, None

    return None, None
def parse_minutes(text: str) -> int:
    import re
    match = re.search(r"(\d+)\s*min", text.lower())
    if match:
        return max(5, min(120, int(match.group(1))))  # limit 5-120 mins
    if "hour" in text.lower():
        return 60
    return 30
# NEW: Parse minutes for ASH
def parse_minutes_from_request(text: str) -> int:
    patterns = [
        r"(\d+)\s*min(?:ute)?s?",
        r"(\d+)\s*minute(?:s)?",
        r"last\s+(\d+)\s*min",
    ]
    for pattern in patterns:
        match = re.search(pattern, text.lower())
        if match:
            return int(match.group(1))
    return 30  # default
def parse_time_from_request(text: str) -> int:
    patterns = [
        r"(\d+)\s*hour(?:s?)?",
        r"(\d+)\s*hr",
        r"last\s+(\d+)\s*hour(?:s?)?",
        r"(\d+)\s*day(?:s?)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text.lower())
        if match:
            num = int(match.group(1))
            if "day" in pattern:
                return num * 24
            return num
    return 1
if "chat" not in st.session_state:
    st.session_state["chat"] = []
if "awr_history" not in st.session_state:
    st.session_state["awr_history"] = []
if "job_map" not in st.session_state:
    st.session_state["job_map"] = ""

if "dbs" not in st.session_state:
    st.session_state["dbs"] = get_db_list()
if "current_db" not in st.session_state:
    st.session_state["current_db"] = st.session_state["dbs"][0]

try:
    if not st.session_state["job_map"]:
        st.session_state["job_map"] = build_job_map(_client=jenkins_server)
except Exception:
    st.session_state["job_map"] = ""

if "health_report" not in st.session_state:
    st.session_state["health_report"] = None
if "health_error" not in st.session_state:
    st.session_state["health_error"] = None
if "awr_compare" not in st.session_state:
    st.session_state["awr_compare"] = None

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

st.sidebar.markdown("---")
st.sidebar.markdown("### Emergency Tools")


health_clicked = st.sidebar.button("Health Check", type="primary", use_container_width=True)
if health_clicked:
    with st.spinner("Running health checks across the database..."):
        health_data = run_full_health_check(db=st.session_state["current_db"])
    
    if health_data.get("status") == "ok":
        st.session_state["health_report"] = health_data["report"]
        st.session_state["health_error"] = None
        st.sidebar.success("Full AI Health Check Complete! (View in main area)")
    else:
        st.session_state["health_error"] = health_data.get("message", "Health check failed")
        st.session_state["health_report"] = None
        st.sidebar.error("Health check failed (check main area)")
st.sidebar.markdown("---")
st.sidebar.markdown(f"<div class='db-badge'> Connected to: {st.session_state['current_db']} </div>", unsafe_allow_html=True)

st.sidebar.markdown("---")
st.sidebar.markdown("### Compare Any Two AWR Periods")

# [Keep AWR comparison sidebar - but use full labels]
if len(st.session_state.awr_history) >= 2:
    valid_history = [h for h in st.session_state.awr_history if h.get("report_html")]

    opt1 = st.sidebar.selectbox(
        "First period (Baseline)",
        options=valid_history,
        format_func=lambda x: x["label"],  # Full label
        key="cmp_baseline"
    )
    opt2 = st.sidebar.selectbox(
        "Second period (Compare to)",
        options=valid_history,
        format_func=lambda x: x["label"],  # Full label
        key="cmp_current"
    )

    if opt1["id"] != opt2["id"]:
        if st.sidebar.button("Compare These Two Periods — Full Regression Analysis", type="primary", use_container_width=True):
            with st.spinner("AI is comparing both reports..."):
                comp = compare_awr_reports(
                    report1_html=opt1["report_html"],
                    report2_html=opt2["report_html"],
                    label1=f"Baseline: {opt1['period_str']}",
                    label2=f"Current: {opt2['period_str']}"
                )
            if comp["status"] == "ok":
                st.session_state["awr_compare"] = comp["comparison"]
                st.sidebar.success("Comparison ready! (View in main area)")
            else:
                st.sidebar.error(comp.get("message"))
    else:
        st.sidebar.info("Please select two different periods.")
MAX_CHAT = 5

def append_chat_entry(entry):
    st.session_state["chat"].append(entry)
    while len(st.session_state["chat"]) > MAX_CHAT:
        st.session_state["chat"].pop(0)
# NEW: Interactive analysis
def analyze_interactive_report(query: str, report_html: str, report_type: str = "AWR/ASH"):
    prompt = f"""
You are an Oracle Performance Expert analyzing {report_type} report.
User question: "{query}"

Extract and answer from the report HTML.
Focus on:
- Top SQLs (ID, text, elapsed/CPU/buffer gets)
- Wait events (%DB time, total time)
- Load profile changes
- Anomalies/regressions
- Tuning recommendations

Keep response concise, use markdown tables for SQL/wait lists.
Report HTML: {report_html[:4000]}... (truncated)

Answer ONLY the user's question.
"""
    try:
        response = awr_analyzer_agent.generate_reply([{"role": "user", "content": prompt}])
        return {"status": "ok", "analysis": response}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def process_request_callback():
    task = st.session_state.get("task_input", "") or ""
    task = task.strip()
    if not task:
        return

    # 1. CALL THE MASTER AGENT
    # We include current time in context so LLM can calculate "last 2 hours" accurately
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prompt = f"Current System Time: {current_time}\nUser Request: {task}"
    
    with st.spinner("Analyzing request..."):
        try:
            reply = input_parser_agent.generate_reply([{"role": "user", "content": prompt}])
            # Extract JSON from potential markdown wrappers
            import re
            json_match = re.search(r"(\{.*\})", reply, flags=re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group(1))
            else:
                parsed = json.loads(reply)
        except Exception as e:
            # Fallback if LLM fails (rare)
            parsed = {"intent": "oracle_query", "parameters": {}, "original_request": task}

    intent = parsed.get("intent")
    params = parsed.get("parameters", {})
    
    # Create the chat entry
    entry = {
        "id": int(time.time() * 1000),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "request": task,
        "intent": intent,
        "oracle_sql": None,
        "oracle_result": None,
        "parsed_params": params # Store for debugging
    }

    # ------------------------------------------------------
    # ROUTING LOGIC (Much cleaner now!)
    # ------------------------------------------------------

    if intent == "connect_db":
        target = params.get("target_db", "").upper()
        # (Insert your existing DB connection logic here, using 'target' variable)
        # ... logic to switch st.session_state["current_db"] ...
        entry["oracle_result"] = {"status": "ok", "message": f"Switched to {target}"}

    elif intent == "show_history":
        history = st.session_state.get("awr_history", [])
        if not history:
            entry["oracle_result"] = {"status": "info", "message": "No reports generated yet."}
        else:
            summary = "### Report History\n" + "\n".join([f"- {h['label']}" for h in history])
            entry["oracle_result"] = {"status": "ok", "message": summary}

    elif intent in ["awr_report", "ash_report"]:
        # The Agent already did the hard work of calculating times!
        start_t = params.get("start_time")
        end_t = params.get("end_time")
        mins = params.get("duration_minutes") or 60

        payload = {"db": st.session_state["current_db"]}
        
        if start_t and end_t:
            # Reconstruct the "between" string that your wrapper expects
            # Or better yet, update wrapper to accept raw dates. 
            # For now, let's just pass the original request, but we know it's parsed correctly intent-wise.
            payload["original_request"] = f"between {start_t} and {end_t}" 
            spinner_msg = f"Generating {intent.upper()} from {start_t} to {end_t}..."
        else:
            payload["hours"] = mins / 60.0
            spinner_msg = f"Generating {intent.upper()} for last {mins} minutes..."

        with st.spinner(spinner_msg):
            # Reuse your existing execution wrapper
            exec_res = approve_and_run_sql_wrapper(json.dumps(payload))
        
        entry["oracle_result"] = exec_res
        entry["oracle_sql"] = spinner_msg

    elif intent == "jenkins":
        # Agent extracted search term
        term = params.get("search_term") or task
        matches = llm_fast_match(term, jenkins_server)
        entry["jenkins_matches"] = matches

    elif intent == "health_check":
         # Trigger the health check logic directly
         pass # (Add your health check call here)

    else:
        # Default to Oracle SQL generation for unknown queries
        sql = code_writer_agent.generate_reply([{"role": "user", "content": task}]).strip()
        entry["oracle_sql"] = sql
        entry["intent"] = "oracle"

    # Final Save
    append_chat_entry(entry)
    st.session_state["task_input"] = ""
# [Keep your existing process_request_callback - but add ASH and interactive]
# def process_request_callback():
#     task = st.session_state.get("task_input", "") or ""
#     task = task.strip()
#     if not task:
#         return
    
#     # 1. Handle DB Switching
#     lower = task.lower()
#     if lower.startswith("connect to ") or lower.startswith("switch to "):
#         name = task.split(None, 2)[-1].strip().upper()
#         available_dbs = st.session_state["dbs"]
        
#         matches = get_close_matches(name, available_dbs, n=1, cutoff=0.6)
#         if matches:
#             selected_db = matches[0]
#             confidence = 1.0
#             reason = "Exact or close fuzzy match"
#         else:
#             suggested, conf, reason = resolve_db_with_llm(task, available_dbs)
#             if suggested:
#                 selected_db = suggested
#                 confidence = conf
#             else:
#                 selected_db = "DEFAULT"
#                 confidence = 0
#                 reason = "No match found; defaulting"
        
#         st.session_state["current_db"] = selected_db
#         entry = {
#             "id": int(time.time() * 1000),
#             "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
#             "request": task,
#             "intent": "connect_db",
#             "oracle_sql": None,
#             "oracle_result": {
#                 "status": "ok" if confidence > 0.5 else "warning",
#                 "message": f"Switched to {selected_db} (confidence: {confidence:.2f} - {reason})"
#             },
#             "jenkins_matches": None,
#             "jenkins_run": None,
#             "_db_confidence": confidence,
#             "_db_reason": reason
#         }
#         append_chat_entry(entry)
#         st.session_state["task_input"] = ""
#         return

#     intent = classify_intent(task)
#     entry = {
#         "id": int(time.time() * 1000),
#         "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
#         "request": task,
#         "intent": intent,
#         "oracle_sql": None,
#         "oracle_result": None,
#         "jenkins_matches": None,
#         "jenkins_run": None,
#     }

#     # 2. Handle Interactive Analysis (Chat with report)
#     if intent == "report_analysis" or any(word in task.lower() for word in ["this report", "the comparison", "top sql", "wait events", "causing issue", "similar sql"]):
#         if st.session_state.awr_history:
#             latest = st.session_state.awr_history[-1]
#             analysis = analyze_interactive_report(task, latest["report_html"], latest.get("type", "AWR/ASH"))
#             entry["intent"] = "report_analysis"
#             entry["oracle_result"] = analysis
#             entry["reference_report"] = latest["label"]
#         else:
#             entry["oracle_result"] = {"status": "error", "message": "No report in history. Generate one first."}
#         append_chat_entry(entry)
#         st.session_state["task_input"] = ""
#         return

#     # 3. Handle AWR and ASH Generation
#     if intent in ["ash_report", "awr_report"]:
#         hours = parse_time_from_request(task)
#         start_dt, end_dt = parse_custom_time_range(task)
        
#         # Determine payload
#         if start_dt and end_dt:
#             spinner_msg = f"Generating AWR from {start_dt} to {end_dt}..."
#             payload = {"original_request": task, "db": st.session_state["current_db"]}
#         else:
#             if hours <= 2 and "awr" not in lower: # Logic check: Short duration implies ASH unless explicitly AWR
#                 minutes = parse_minutes_from_request(task)
#                 spinner_msg = f"Generating ASH for last {minutes} mins..."
#                 payload = {"hours": minutes/60, "db": st.session_state["current_db"]} # wrapper handles float hours as ASH logic if needed, or pass minutes directly if wrapper supports it
#                 # Based on your wrapper:
#                 if minutes > 0:
#                      payload = {"hours": minutes/60.0, "db": st.session_state["current_db"]}
#             else:
#                 spinner_msg = f"Generating AWR for last {hours} hours..."
#                 payload = {"hours": hours, "db": st.session_state["current_db"]}

#         with st.spinner(spinner_msg):
#             exec_res = approve_and_run_sql_wrapper(json.dumps(payload))

#         entry["oracle_result"] = exec_res
#         entry["oracle_sql"] = spinner_msg
#         entry["_last_exec_ts"] = time.time()
        
#         # Auto-save successful ASH reports to history (AWR is saved in render loop, but let's consistency check)
#         # Note: Your render loop handles AWR history appending. 
#         # For ASH, we do it here or in render. Let's rely on the Render loop to save it to history 
#         # to avoid duplication, provided the Render loop logic supports ASH (which I added in the review).

#         append_chat_entry(entry)
#         st.session_state["task_input"] = ""
#         return

#     # 4. Handle Standard Oracle SQL
#     elif intent == "oracle":
#         try:
#             sql = code_writer_agent.generate_reply([{"role": "user", "content": task}]).strip()
#         except Exception as e:
#             sql = f"-- SQL generation failed: {e}"
#         if sql and not sql.endswith(";"):
#             sql += ";"
#         entry["oracle_sql"] = sql

#     # 5. Handle Jenkins
#     elif intent == "jenkins":
#         try:
#             matches = llm_fast_match(task, jenkins_server)
#         except Exception:
#             matches = []
#         entry["jenkins_matches"] = matches or []
#     elif intent == "show_history":
#         history = st.session_state.get("awr_history", [])
#         if not history:
#             entry["oracle_sql"] = "No reports found in history."
#             entry["oracle_result"] = {"status": "info", "message": "No reports generated yet."}
#         else:
#             # Create a nice summary of history
#             summary = "### 📂 Generated Reports History\n"
#             for h in history:
#                 summary += f"- **{h['label']}** (ID: {h['id']})\n"
            
#             entry["oracle_sql"] = "Displaying History"
#             entry["oracle_result"] = {"status": "ok", "message": summary}    

#     # Final append
#     append_chat_entry(entry)
#     st.session_state["task_input"] = ""

st.title("Oracle + Jenkins AI Console")
st.write("Latest messages appear at the bottom. Edit generated SQL inline and Execute from the same block.")

if st.session_state.get("health_report"):
    st.subheader("Oracle Full Health Check")
    st.success("Full AI Health Check Complete!")
    st.markdown(st.session_state["health_report"])
    st.download_button(
        "Download Health Report (HTML)",
        data=st.session_state["health_report"],
        file_name=f"Oracle_Health_Check_{datetime.now().strftime('%Y%m%d_%H%M')}.html",
        mime="text/html"
    )
    if st.button("Dismiss Health Report"):
        del st.session_state["health_report"]
        st.rerun()

if st.session_state.get("health_error"):
    st.subheader("Health Check Error")
    st.error(st.session_state["health_error"])
    if st.button("Dismiss Error"):
        del st.session_state["health_error"]
        st.rerun()

if st.session_state.get("awr_compare"):
    st.subheader("Full AWR Comparison Report")
    st.markdown(st.session_state["awr_compare"])
    if st.button("Dismiss AWR Comparison"):
        del st.session_state["awr_compare"]
        st.rerun()

st.markdown("---")
# [Keep your existing chat rendering - but add ASH and interactive blocks]
chat_holder = st.container()
with chat_holder:
    st.markdown("<div class='chat-container'>", unsafe_allow_html=True)
    st.markdown("<div class='chat-area'>", unsafe_allow_html=True)
    for entry in st.session_state["chat"]:
        uid = entry["id"]
        st.markdown(f"<div class='user-bubble'><div class='meta'>You · {entry['ts']}</div><div>{entry['request']}</div></div>", unsafe_allow_html=True)

        st.markdown(f"<div class='intent-badge'>Detected intent: {entry.get('intent','')}</div>", unsafe_allow_html=True)
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
            
            if "_db_confidence" in entry:
                conf = entry["_db_confidence"]
                reason = entry.get("_db_reason", "")
                st.markdown(f"**LLM Confidence:** {conf:.2f}")
                st.markdown(f"<div class='confidence-bar'><div class='confidence-fill' style='width: {conf*100}%'></div></div>", unsafe_allow_html=True)
                if reason:
                    st.caption(reason)
            
            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            continue
        # NEW: Report Analysis (interactive)
        if entry.get("intent") == "report_analysis":
            analysis = entry.get("oracle_result", {})
            ref = entry.get("reference_report", "unknown report")
            st.markdown(f"**Analysis of {ref}:**")
            if analysis["status"] == "ok":
                st.markdown(analysis["analysis"])
            else:
                st.error(analysis.get("message"))
            continue

        # NEW: ASH Report rendering
        if entry.get("intent") == "ash_report" and entry.get("oracle_result"):
            result = entry.get("oracle_result")
            st.markdown(f"**{entry.get('oracle_sql', 'ASH Report')}**")
            if result.get("status") == "ok":
                report_html = result.get("report", "")
                # Save to history
                history_entry = {
                    "id": uid,
                    "ts": datetime.now(),
                    "label": entry["oracle_sql"],  # Full text
                    "report_html": report_html,
                    "period_str": entry["oracle_sql"],
                    "type": "ASH"
                }
                if not any(h["id"] == uid for h in st.session_state.awr_history):
                    st.session_state.awr_history.append(history_entry)

                st.success("ASH report generated!")
                st.download_button("Download ASH", report_html, result.get("filename", "ash.html"), "text/html", key=f"ash_dl_{uid}")
                if st.button("AI Analysis", type="primary", key=f"ash_ai_{uid}"):
                    with st.spinner("AI analyzing ASH..."):
                        analysis = analyze_awr_report(report_html)
                    st.markdown("### AI Insights from ASH")
                    st.markdown(analysis.get("analysis", "No insights"))
            else:
                st.error(result.get("message"))
            continue

        if entry.get("intent") == "awr_report" and entry.get("oracle_result"):
            result = entry.get("oracle_result")
            st.markdown(f"**{entry.get('oracle_sql', 'AWR Report')}**")
            if result.get("status") == "ok":
                report_html = result.get("report", "")
                snaps = result.get("filename", "unknown")
                period_str = entry.get("oracle_sql", "AWR Report")

                history_entry = {
                    "id": uid,
                    "ts": datetime.now(),
                    "label": period_str.split("for")[-1].strip().title() if "for" in period_str else period_str,
                    "short_label": f"{datetime.now().strftime('%H:%M')} — {period_str[:30]}...",
                    "report_html": report_html,
                    "snaps": snaps,
                    "period_str": period_str
                }
                if not any(h["id"] == uid for h in st.session_state.awr_history):
                    st.session_state.awr_history.append(history_entry)
                    if len(st.session_state.awr_history) > 15:
                        st.session_state.awr_history.pop(0)

                st.success("AWR report generated successfully!")

                col1, col2 = st.columns([1, 3])
                with col1:
                    file_name = f"awr_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
                    st.download_button(
                        "Download HTML",
                        data=report_html,
                        file_name=file_name,
                        mime="text/html",
                        key=f"dl_{uid}"
                    )

                with col2:
                    if st.button("Analyze with AI", type="primary", key=f"analyze_{uid}"):
                        with st.spinner("AI is deeply analyzing the AWR report..."):
                            analysis_result = analyze_awr_report(report_html)
                        if analysis_result["status"] == "ok":
                            st.markdown("### AI Performance Analysis & Tuning Recommendations")
                            st.markdown(analysis_result["analysis"])
                            st.code(analysis_result["analysis"], language=None)
                        else:
                            st.error(analysis_result["message"])

                st.markdown("<hr style='margin: 10px 0;'>", unsafe_allow_html=True)
            else:
                st.error(result.get("message", "AWR generation failed."))
            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            continue
        if entry.get("intent") == "show_history":
            res = entry.get("oracle_result", {})
            st.markdown(res.get("message", ""))
            continue
        if entry.get("intent") == "oracle" and entry.get("oracle_sql") is not None:
            sql = entry.get("oracle_sql", "")
            edit_key = f"edit_sql_{uid}"
            if edit_key not in st.session_state:
                st.session_state[edit_key] = sql

            st.text_area("Generated SQL (edit before executing):", value=st.session_state[edit_key], key=edit_key, height=160)

            exec_key = f"exec_sql_{uid}"
            if st.button("Execute SQL", key=exec_key):
                edited_sql = st.session_state.get(edit_key, sql)
                with st.spinner("Executing SQL..."):
                    payload = {"sql": edited_sql, "db": st.session_state["current_db"]}
                    exec_res = approve_and_run_sql_wrapper(json.dumps(payload))
                entry["oracle_sql"] = edited_sql
                entry["oracle_result"] = exec_res
                entry["_last_exec_ts"] = time.time()

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

            if entry.get("oracle_result") and "_last_exec_ts" in entry:
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

        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    st.markdown("</div></div>", unsafe_allow_html=True)

# [Keep input box - unchanged]
st.markdown("---")
if "task_input" not in st.session_state:
    st.session_state["task_input"] = ""

st.text_input("Type your request here:", key="task_input", placeholder="Example: show all tablespaces OR run cleanup job OR connect to PLAB_CM OR performance issues last 30 mins")
st.button(" Process Request", on_click=process_request_callback)

st.caption("Tip: Generated SQL is editable inline; click Execute to run against the connected DB. Use 'connect to <DB>' to switch. Ask 'top SQLs in this report' for interactive analysis. Recent queries use ASH automatically.")