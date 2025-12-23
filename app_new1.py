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
from patch_forstreamlit import download_oracle_patch

from autogen import AssistantAgent, ConversableAgent
# Ensure oracle_runner_55555.py is in the same directory and has your DB config/functions
from oracle_runner_55555 import (
    run_oracle_query, get_db_list, generate_awr_report, generate_ash_report,
    get_snapshots_for_time, analyze_awr_report, compare_awr_reports, run_full_health_check
)
from patch_downloader import download_patches

# 1. Configuration & Setup
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")

# Adjust these paths to your specific environment if needed
os.environ["REQUESTS_CA_BUNDLE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"
os.environ["SSL_CERT_FILE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"

st.set_page_config(page_title="Oracle + Jenkins AI Console", layout="wide")

if "chat" not in st.session_state:
    st.session_state["chat"] = []
if "awr_history" not in st.session_state:
    st.session_state["awr_history"] = []
if "job_map" not in st.session_state:
    st.session_state["job_map"] = ""
if "dbs" not in st.session_state:
    st.session_state["dbs"] = get_db_list()
if "current_db" not in st.session_state:
    st.session_state["current_db"] = st.session_state["dbs"][0] if st.session_state["dbs"] else "DEFAULT"
if "health_report" not in st.session_state:
    st.session_state["health_report"] = None
if "health_error" not in st.session_state:
    st.session_state["health_error"] = None
if "awr_compare" not in st.session_state:
    st.session_state["awr_compare"] = None
if "patch_processes" not in st.session_state:
    st.session_state["patch_processes"] = {} 
    
common_llm_config = {
    "config_list": [{"model": "gpt-4o-mini", "api_key": openai_api_key}],
    "temperature": 0,
    "timeout": 120,
}

# 2. Define AI Agents

# --- NEW MASTER INTENT AGENT ---
input_parser_agent = ConversableAgent(
    name="input_parser",
    llm_config=common_llm_config,
    system_message="""
You are the Master Controller for an Oracle & Jenkins Dashboard.
Your job is to parse natural language user requests into structured JSON.
1. If user says anything like:
   - "this report", "the report", "generated report", "last report", "that ASH", "the one above", "from the report"
   → intent MUST be "report_analysis" (highest priority)

2. Only trigger "ash_report" or "awr_report" when user clearly asks for a NEW time range:
   - "last 30 mins", "past hour", "from 10 to 12", etc.

- If user asks for performance in last 5-120 minutes → use "ash_report"
- If user asks for hours/days or specific clock times → use "awr_report"
Examples:
  • "last 20 mins", "past hour", "recent activity" → ash_report
  • "last 4 hours", "yesterday", "from 10 to 12" → awr_report
  • "from NOV 25 1AM to NOV 25 2AM", "generate AWR report from 2025-11-25 01:00 to 2025-11-25 02:00" → awr_report
  • For time extraction: Always format as 'YYYY-MM-DD HH:MM:SS' (assume current year if not specified, e.g., NOV 25 → 2025-11-25).
### AVAILABLE INTENTS:
1. "connect_db": Switch database connection.
2. "awr_report": Long-term Oracle performance report (hours/days).
3. "ash_report": Short-term/Recent Oracle performance (minutes).
4. "show_history": List previously generated reports.
5. "report_analysis": Analyze a specific report, "this" report, or compare reports.
6. "oracle_query": General SQL questions or requests.
7. "jenkins": Run/List Jenkins jobs.
8. "health_check": Run full DB health check.
10. "status_check": Simple inquiries like "where am I?", "what time is it?", or "who am I connected to?".
11. "patch_download": Download Oracle PSU patches like "download 19.28 DB RU" or "latest OPATCH".
### PARAMETER EXTRACTION RULES:
- **time_range**: If user mentions "from X to Y" or "for last 2 hours", calculate specific timestamps or duration.
- **db_name**: Extract target DB name if mentioned.
- **duration_minutes**: For ASH/AWR, convert "1 hour" to 60, "30 mins" to 30. Default AWR=60 (1 hr), ASH=30.
- **search_term**: For jenkins or history searches.
- **start_time_str**: If exact time given (e.g. "23 NOV 00:00"), format as 'YYYY-MM-DD HH:MM:SS'.
- **end_time_str**: If exact time given, format as 'YYYY-MM-DD HH:MM:SS'.
- **patch_type**: Extract type like "db ru", "grid ru", "opatch", "ojvm", "full" (or null).
- **patch_version**: Extract version like "19.28" (or null for latest).
### OUTPUT FORMAT (JSON ONLY):
{
  "intent": "intent_name",
  "confidence": 0.0-1.0,
  "parameters": {
    "start_time": "string" (or null),
    "end_time": "string" (or null),
    "duration_minutes": int (or null),
    "target_db": "string" (or null),
    "search_term": "string" (or null)
    "is_greeting": true (for hi/hello/status_check variants)
    "patch_type": "string" (or null),
    "patch_version": "string" (or null)
  },
  "original_request": "user input"
}
""",
    human_input_mode="NEVER",
)

# Standard Agents
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

code_writer_agent = AssistantAgent(
    name="code_writer_agent",
    llm_config=common_llm_config,
    code_execution_config=False,
    system_message=(
        "You are an expert Oracle SQL and PLSQL generator. "
        "The user will describe what they want in natural language. "
        "For raw queries, output **only** a valid Oracle SQL query. "
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

job_summary_agent = ConversableAgent(name="job_summary", llm_config=common_llm_config)
job_selector_agent = ConversableAgent(name="job_selector", llm_config=common_llm_config)


# 3. Helper Functions

def parse_custom_time_range(text: str):
    """Fallback manual parser, though Agent handles most now."""
    text = text.lower()
    try:
        if "between" in text and "and" in text:
            part = text.split("between", 1)[1]
            start_str, end_str = part.split("and", 1)
            return dateutil.parser.parse(start_str.strip(), fuzzy=True), dateutil.parser.parse(end_str.strip(), fuzzy=True)
        if "from" in text and "to" in text:
            part = text.split("from", 1)[1]
            start_str, end_str = part.split("to", 1)
            return dateutil.parser.parse(start_str.strip(), fuzzy=True), dateutil.parser.parse(end_str.strip(), fuzzy=True)
    except:
        return None, None
    return None, None

def approve_and_run_sql_wrapper(arguments_json: str):
    try:
        args = json.loads(arguments_json)
        db = args.get("db")
        hours = args.get("hours")
        # Direct time handling from Agent
        start_t_agent = args.get("start_time")
        end_t_agent = args.get("end_time")
        
    except Exception as e:
        return {"status": "error", "message": f"JSON parse error: {e}"}

    # Case A: Exact start/end times provided by Agent
    if start_t_agent and end_t_agent:
        sql = f"""
            SELECT MIN(snap_id) AS start_snap, MAX(snap_id) AS end_snap
            FROM dba_hist_snapshot
            WHERE begin_interval_time >= TO_TIMESTAMP('{start_t_agent}', 'YYYY-MM-DD HH24:MI:SS')
              AND end_interval_time <= TO_TIMESTAMP('{end_t_agent}', 'YYYY-MM-DD HH24:MI:SS')
        """
        snaps = run_oracle_query(sql, db)
        if not snaps or not snaps[0].get("START_SNAP"):
            return {"status": "error", "message": "No snapshots found for that time range."}
        return generate_awr_report(snaps[0]["START_SNAP"], snaps[0]["END_SNAP"], db)

    # Case B: Fallback to manual parsing if agent sent "original_request"
    if args.get("original_request"):
        s_dt, e_dt = parse_custom_time_range(args.get("original_request", ""))
        if s_dt and e_dt:
            # Re-run logic similar to above using formatted dates
            sql = f"""
                SELECT MIN(snap_id) AS start_snap, MAX(snap_id) AS end_snap
                FROM dba_hist_snapshot
                WHERE begin_interval_time >= TO_TIMESTAMP('{s_dt.strftime('%Y-%m-%d %H:%M:%S')}', 'YYYY-MM-DD HH24:MI:SS')
                  AND end_interval_time <= TO_TIMESTAMP('{e_dt.strftime('%Y-%m-%d %H:%M:%S')}', 'YYYY-MM-DD HH24:MI:SS')
            """
            snaps = run_oracle_query(sql, db)
            if snaps and snaps[0].get("START_SNAP"):
                return generate_awr_report(snaps[0]["START_SNAP"], snaps[0]["END_SNAP"], db)

    # Case C: Relative Time (Hours/Minutes)
    if hours is not None:
        hours = float(hours)
        if hours <= 0.1: # very small duration? Default to ASH logic usually handled before call, but safe check
            return {"status": "error", "message": "Duration too short."}
            
        # Decision: ASH vs AWR based on duration inside this wrapper?
        # The prompt says "if hours <= 2 -> ASH".
        if hours <= 2.0: 
            minutes = int(hours * 60)
            return generate_ash_report(minutes, db)
        else:
            start_snap, end_snap = get_snapshots_for_time(hours, db)
            if not start_snap:
                return {"status": "error", "message": f"Could not find snapshots for last {hours} hours"}
            return generate_awr_report(start_snap, end_snap, db)

    # Case D: Raw SQL
    sql = args.get("sql", "")
    result = run_oracle_query(sql, db)
    if isinstance(result, dict) and "error" in result:
        return {"status": "error", "message": result["error"]}
    return {"status": "ok", "result": result}

code_executor_agent.register_for_execution(name="approve_and_run_sql")(approve_and_run_sql_wrapper)

# --- Jenkins Setup ---
try:
    jenkins_server = jenkins.Jenkins("http://localhost:9020", username="dba", password="113bb934053435f19fa62d94f8c79a108c")
except:
    jenkins_server = None

@st.cache_data(show_spinner=False)
def fetch_jobs_recursive(_client, folder=""):
    if _client is None: return []
    try:
        items = _client.get_jobs(folder) if folder else _client.get_jobs()
    except: return []
    out = []
    for it in items:
        name = it.get("name")
        if not name: continue
        full = f"{folder}/{name}" if folder else name
        cls = it.get("_class", "")
        if "Folder" in cls:
            out.extend(fetch_jobs_recursive(_client, full))
        else:
            out.append(full)
    return out

@st.cache_data(show_spinner=False)
def fetch_all_job_details(_client):
    if _client is None: return []
    out = []
    for full in fetch_jobs_recursive(_client):
        try:
            info = _client.get_job_info(full)
        except: continue
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
    system_msg = f"Summarize these Jenkins jobs:\n{text}"
    try:
        return job_summary_agent.generate_reply([{"role": "user", "content": system_msg}])
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
        return {"status": "ERROR", "error": f"Failed to trigger: {e}"}

    build_number = None
    for _ in range(30):
        try:
            qi = client.get_queue_item(queue_id)
            if "executable" in qi and qi["executable"]:
                build_number = qi["executable"]["number"]
                break
        except: pass
        time.sleep(poll_interval)

    if build_number is None:
        return {"status": "ERROR", "error": "Timed out waiting for build start"}

    for _ in range(180):
        try:
            bi = client.get_build_info(job_name, build_number)
            if not bi.get("building", True):
                break
        except: pass
        time.sleep(poll_interval)

    try:
        bi = client.get_build_info(job_name, build_number)
        status = bi.get("result", "UNKNOWN")
        console = client.get_build_console_output(job_name, build_number)
        return {"status": status, "build_number": build_number, "console": console, "error": None}
    except Exception as e:
        return {"status": "ERROR", "error": f"Error reading output: {e}"}

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

def llm_fast_match(query, client, top_n=10):
    if not query: return []
    jobs = fetch_all_job_details(_client=client)
    valid_names = [j["name"] for j in jobs]
    
    # Simple fuzzy match fallback if LLM unnecessary or complex
    close = get_close_matches(query, valid_names, n=top_n, cutoff=0.3)
    return close

def analyze_interactive_report(query: str, report_html: str, report_type: str = "AWR/ASH"):
    # NEW: Smart truncation – keep the most important sections
    # We extract text content and prioritize key sections instead of blind [:130000]
    from bs4 import BeautifulSoup
    
    try:
        soup = BeautifulSoup(report_html, 'html.parser')
        
        # Remove scripts/styles to reduce noise
        for script in soup(["script", "style"]):
            script.decompose()
        
        text = soup.get_text()
        
        # Keep only the juicy parts – prioritize known AWR/ASH section headers
        lines = text.splitlines()
        prioritized = []
        current_section = []
        capture = False
        
        important_sections = [
            "Load Profile", "Instance Efficiency", "Top 10 Foreground Events",
            "Wait Events", "SQL Statistics", "SQL ordered by", 
            "Top Timed Events", "Time Model", "Operating System Statistics",
            "Foreground Wait Events", "Background Wait Events"
        ]
        
        for line in lines:
            stripped = line.strip()
            if any(sec in stripped for sec in important_sections):
                if current_section:
                    prioritized.extend(current_section[:100])  # cap per section
                current_section = [line]
                capture = True
            elif capture:
                current_section.append(line)
                if len(current_section) > 200:  # prevent runaway
                    prioritized.extend(current_section)
                    current_section = []
                    capture = False
            # Always keep some header info
            if "Report" in line or "Host Name" in line or "DB Name" in line or "Snapshot" in line:
                prioritized.append(line)
        
        if current_section:
            prioritized.extend(current_section)
        
        # Final fallback: include raw text up to safe limit
        context = "\n".join(prioritized) if prioritized else text
        
        # Safe limit: ~120k chars → fits comfortably in gpt-4o-mini context
        context = context[:120_000]
        
        prompt = f"""
You are an Oracle Performance Expert.
User Question: {query}

Here is the full relevant content from the {report_type} report (prioritized sections):
{context}

Answer directly and accurately using only the data above.
Use bullet points, tables, and bold key findings.
""".strip()

        response = awr_analyzer_agent.generate_reply([{"role": "user", "content": prompt}])
        return {"status": "ok", "analysis": response}

    except Exception as e:
        return {"status": "error", "message": f"Analysis failed: {str(e)}"}

# Initialize Job Map if needed
if not st.session_state["job_map"] and jenkins_server:
    try:
        st.session_state["job_map"] = build_job_map(_client=jenkins_server)
    except: pass

# 5. UI & Styling
st.markdown(
    """
<style>
.chat-container { background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%); border-radius: 16px; padding: 20px; margin: 20px 0; border: 1px solid rgba(255, 255, 255, 0.2); }
.chat-area { max-height: 60vh; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 16px; background: white; border-radius: 12px; }
.user-bubble { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; align-self: flex-end; padding: 16px 20px; border-radius: 20px 20px 5px 20px; max-width: 80%; }
.assistant-block { background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); color: white; align-self: flex-start; padding: 16px 20px; border-radius: 20px 20px 20px 5px; max-width: 80%; }
.intent-badge { display: inline-block; padding: 8px 12px; background: #667eea; border-radius: 20px; margin: 8px auto; color: white; font-size: 0.8em; }
.db-badge { padding: 12px 16px; background: #4facfe; border-radius: 25px; color: white; font-weight: 700; text-align: center; }
.stButton > button { width: 100%; border-radius: 20px; }
</style>
""", unsafe_allow_html=True
)

# 6. Sidebar
st.sidebar.markdown("### Emergency Tools")
if st.sidebar.button("Health Check", type="primary"):
    with st.spinner("Running full health check..."):
        h_data = run_full_health_check(db=st.session_state["current_db"])
        if h_data.get("status") == "ok":
            st.session_state["health_report"] = h_data["report"]
            st.session_state["health_error"] = None
        else:
            st.session_state["health_error"] = h_data.get("message", "Unknown error")

st.sidebar.markdown("---")
st.sidebar.markdown(f"<div class='db-badge'> Connected to: {st.session_state['current_db']} </div>", unsafe_allow_html=True)

st.sidebar.markdown("### Compare AWR Periods")
if len(st.session_state["awr_history"]) >= 2:
    valid_hist = [h for h in st.session_state["awr_history"] if h.get("report_html")]
    opt1 = st.sidebar.selectbox("Baseline", options=valid_hist, format_func=lambda x: x["label"], key="cmp_base")
    opt2 = st.sidebar.selectbox("Comparison", options=valid_hist, format_func=lambda x: x["label"], key="cmp_curr")
    if opt1["id"] != opt2["id"]:
        if st.sidebar.button("Compare Reports"):
            with st.spinner("AI Comparing..."):
                comp = compare_awr_reports(opt1["report_html"], opt2["report_html"], opt1["period_str"], opt2["period_str"])
                if comp["status"] == "ok":
                    st.session_state["awr_compare"] = comp["comparison"]
                else:
                    st.sidebar.error(comp.get("message"))

# 7. Main Chat Logic
def append_chat_entry(entry):
    st.session_state["chat"].append(entry)
    if len(st.session_state["chat"]) > 10:
        st.session_state["chat"].pop(0)

def process_request_callback():
    task = st.session_state.get("task_input", "").strip()
    if not task: return

    # --- 1. Master Agent Analysis ---
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prompt = f"Current Time: {current_time}\nAvailable DBs: {st.session_state['dbs']}\nUser Request: {task}"
    
    with st.spinner("Analyzing intent..."):
        try:
            # Call the Master Parser Agent
            reply = input_parser_agent.generate_reply([{"role": "user", "content": prompt}])
            # Clean extraction of JSON
            m = re.search(r"(\{.*\})", reply, flags=re.DOTALL)
            parsed = json.loads(m.group(1)) if m else json.loads(reply)
        except:
            # Fallback
            parsed = {"intent": "oracle_query", "parameters": {}, "original_request": task}

    intent = parsed.get("intent")
    params = parsed.get("parameters", {})
    
    entry = {
        "id": int(time.time() * 1000),
        "ts": time.strftime("%H:%M:%S"),
        "request": task,
        "intent": intent,
        "oracle_sql": None,
        "oracle_result": None,
        "jenkins_matches": None,
        "parsed": parsed # debug info
    }

    # --- 2. Routing Logic ---

    if intent == "connect_db":
        target = params.get("target_db", "").upper()
        if target in st.session_state["dbs"]:
            st.session_state["current_db"] = target
            entry["oracle_result"] = {"status": "ok", "message": f"Connected to {target}"}
        else:
            # Fuzzy match fallback
            matches = get_close_matches(target, st.session_state["dbs"], n=1)
            if matches:
                st.session_state["current_db"] = matches[0]
                entry["oracle_result"] = {"status": "ok", "message": f"Connected to {matches[0]} (assumed from '{target}')"}
            else:
                entry["oracle_result"] = {"status": "warning", "message": f"DB '{target}' not found. Stayed on {st.session_state['current_db']}"}

    elif intent == "show_history":
        hist = st.session_state["awr_history"]
        if not hist:
            entry["oracle_result"] = {"status": "info", "message": "No report history found."}
        else:
            msg = "### Generated Reports\n" + "\n".join([f"- **{h['label']}** ({h['type']})" for h in hist])
            entry["oracle_result"] = {"status": "ok", "message": msg}
    elif intent in ["awr_report", "ash_report"] or ("performance" in task.lower() and any(x in task.lower() for x in ["last", "past", "recent", "minute", "hour"])):

        start_t = params.get("start_time")
        end_t = params.get("end_time")
        mins = params.get("duration_minutes")
        db = st.session_state["current_db"]
        duration_mins = mins or 30  
        payload = {"db": st.session_state["current_db"]}

        # Case 1: User gave exact start/end times → always AWR
        if start_t and end_t:
        # Parse to datetime for duration calc (fallback if needed)
         try:
            from dateutil import parser
            start_dt = parser.parse(start_t)
            end_dt = parser.parse(end_t)
            duration_mins_calc = int((end_dt - start_dt).total_seconds() / 60)
         except:
            duration_mins_calc = 60  # Safe default for 1hr ranges
        
         if duration_mins_calc > 30:  # Anything >30 mins = AWR (covers your 1hr example)
            msg_str = f"Generating AWR from {start_t} to {end_t} ({duration_mins_calc} mins)..."
            entry["oracle_sql"] = msg_str
            entry["intent"] = "awr_report"  # Force AWR intent
            
            with st.spinner(msg_str):
                res = approve_and_run_sql_wrapper(json.dumps({
                    "db": db,
                    "start_time": start_t,
                    "end_time": end_t
                }))
            entry["oracle_result"] = res
         else:
            # Rare: Very short exact range → ASH
            msg_str = f"Generating ASH from {start_t} to {end_t}..."
            entry["oracle_sql"] = msg_str
            entry["intent"] = "ash_report"
            with st.spinner(msg_str):
                res = generate_ash_report(duration_mins_calc, db)
            entry["oracle_result"] = res
        # Skip rest—handled above
         append_chat_entry(entry)  # Early exit to avoid double-processing
         return  # NEW: Prevent falling t

        # Case 2: Duration given or vague recent request → decide ASH vs AWR
        
        duration_mins = mins or 30  # default 30 mins
        payload = {"db": db}
            # ≤ 120 minutes → ASH is the right tool (and avoids snapshot issue)
        if duration_mins <= 120:
                msg_str = f"Generating ASH report for last {duration_mins} minutes..."
                entry["oracle_sql"] = msg_str
                entry["intent"] = "ash_report"

                with st.spinner(msg_str):
                    res = generate_ash_report(duration_mins, st.session_state["current_db"])

                entry["oracle_result"] = res

                # Add to history so user can compare/analyze later
                if res.get("status") == "ok" and res.get("report"):
                    if not any(h["id"] == entry["id"] for h in st.session_state["awr_history"]):
                        st.session_state["awr_history"].append({
                            "id": entry["id"],
                            "ts": datetime.now(),
                            "label": f"ASH Last {duration_mins} mins",
                            "type": "ASH",
                            "report_html": res["report"],
                            "period_str": f"Last {duration_mins} minutes"
                        })

        else:
                # > 2 hours → use AWR via snapshot logic
                payload["hours"] = duration_mins / 60.0
                msg_str = f"Generating AWR for last {duration_mins} minutes ({payload['hours']:.1f} hours)..."
                entry["oracle_sql"] = msg_str
                with st.spinner(msg_str):
                    res = approve_and_run_sql_wrapper(json.dumps(payload))
                entry["oracle_result"] = res

        # Do NOT continue here — just let the normal flow proceed

    elif intent == "report_analysis":
        # Interactive chat with last report
        if st.session_state["awr_history"]:
            last = st.session_state["awr_history"][-1]
            res = analyze_interactive_report(task, last["report_html"], last["type"])
            entry["oracle_result"] = res
            entry["reference_report"] = last["label"]
        else:
            entry["oracle_result"] = {"status": "error", "message": "No reports to analyze yet."}
    elif intent == "patch_download":
        full_request = task.strip()

        # 1. Add the user message to chat history (appears immediately at bottom)
        user_entry = {
            "id": len(st.session_state["chat"]) + 1,
            "request": full_request,
            "ts": datetime.now().strftime("%H:%M:%S"),
            "intent": "patch_download",
            "oracle_result": {"status": "info", "message": "AI is analyzing your patch request..."}
        }
        st.session_state["chat"].append(user_entry)

        # 2. Run the patch download in background
        with st.spinner("Downloading Oracle patches... (2–10 mins)"):
            result = download_oracle_patch(full_request)

        # 3. Add the FINAL AI response as a new chat entry (appears right below user message)
        status = result.get("status", "info")
        message = result.get("message", "No response from agent.")

        ai_entry = {
            "id": len(st.session_state["chat"]) + 1,
            "request": None,  # AI response, not user
            "ts": datetime.now().strftime("%H:%M:%S"),
            "intent": "patch_download",
            "oracle_result": {
                "status": "success" if status == "success" else "error",
                "message": message
            }
        }
        st.session_state["chat"].append(ai_entry)

        # No st.rerun() needed — Streamlit auto-refreshes after this block
        # No st.rerun() needed — Streamlit auto-refreshes after this block
    elif intent == "status_check":
        # Handle the "to which db am i connected to" query here
        current_db = st.session_state.get("current_db", "UNKNOWN")
        is_greeting = params.get("is_greeting", False)
        msg = f"You are currently connected to the database: **{current_db}**."
        
        # Optionally, check time/date if the user asked that
        if any(word in task.lower() for word in ["time", "date", "now"]):
            msg += f"\n\nThe current system time is **{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}**."
        if is_greeting:
         msg = f"Hi there!  {msg}\n\nHow can I help with Oracle performance, Jenkins jobs, or a quick health check today?"
        entry["oracle_result"] = {"status": "ok", "message": msg}
    elif intent == "jenkins":
        term = params.get("search_term") or task
        matches = llm_fast_match(term, jenkins_server)
        entry["jenkins_matches"] = matches

    elif intent == "health_check":
        # Usually triggered by sidebar, but chat can do it too
        with st.spinner("Running Health Check..."):
             h_res = run_full_health_check(st.session_state["current_db"])
        if h_res["status"] == "ok":
            st.session_state["health_report"] = h_res["report"]
            entry["oracle_result"] = {"status": "ok", "message": "Health Check Generated (see main view)"}
        else:
            entry["oracle_result"] = {"status": "error", "message": h_res.get("message")}

    else:
        # Default: SQL Query
        try:
            sql = code_writer_agent.generate_reply([{"role": "user", "content": task}]).strip()
            sql = sql.strip()
            if sql.endswith(';'):
             sql = sql[:-1].strip()
            # if not sql.endswith(";"): sql += ";"
            entry["oracle_sql"] = sql
            entry["intent"] = "oracle_query"
        except Exception as e:
            entry["oracle_result"] = {"status": "error", "message": str(e)}

    append_chat_entry(entry)
    st.session_state["task_input"] = ""

# 8. Render Main View
st.title("Oracle + Jenkins AI Console")
st.caption("Latest updates: AI Parser enabled. Supports 'ASH for 10 mins', 'AWR for specific time', 'Show History', 'Connect to DB'.")

# Show Health Report if active
if st.session_state["health_report"]:
    st.subheader("Health Check Result")
    st.markdown(st.session_state["health_report"])
    if st.button("Close Health Report"):
        st.session_state["health_report"] = None
        st.rerun()

# Show Comparison if active
if st.session_state["awr_compare"]:
    st.subheader("AWR Comparison Analysis")
    st.markdown(st.session_state["awr_compare"])
    if st.button("Close Comparison"):
        st.session_state["awr_compare"] = None
        st.rerun()

# Render Chat
chat_holder = st.container()
with chat_holder:
    st.markdown("<div class='chat-container'><div class='chat-area'>", unsafe_allow_html=True)
    for entry in st.session_state["chat"]:
        uid = entry["id"]
        # User Bubble
        # User Bubble + Intent badge only for real user messages
        if entry.get("request"):  # Only show for actual user input
            st.markdown(f"<div class='user-bubble'><div>{entry['request']}</div><div style='font-size:0.7em; opacity:0.7'>{entry['ts']}</div></div>", unsafe_allow_html=True)
            st.markdown(f"<div class='intent-badge'>Intent: {entry.get('intent', 'unknown')}</div>", unsafe_allow_html=True)
        else:
            # AI response bubble (no intent badge again)
            st.markdown(f"<div class='ai-bubble'><div>{entry['ts']}</div></div>", unsafe_allow_html=True)
        # st.markdown(f"<div class='user-bubble'><div>{entry['request']}</div><div style='font-size:0.7em; opacity:0.7'>{entry['ts']}</div></div>", unsafe_allow_html=True)
        # # Intent Badge
        # st.markdown(f"<div class='intent-badge'>Intent: {entry.get('intent', 'unknown')}</div>", unsafe_allow_html=True)
        
        # Responses
        res = entry.get("oracle_result", {})
        
        # 1. Simple Messages (History, Connection, Errors)
        if res and "message" in res and "report" not in res:
             color = "green" if res.get("status") == "ok" else "red"
             st.markdown(f"<div style='color:{color}; padding:10px; background:white; border-radius:10px; border:1px solid #eee'>{res['message']}</div>", unsafe_allow_html=True)

        # 2. SQL Execution
        if entry.get("intent") == "oracle_query" and entry.get("oracle_sql"):
            st.code(entry["oracle_sql"], language="sql")
            if st.button(f"Execute SQL {uid}", key=f"ex_{uid}"):
                with st.spinner("Executing..."):
                    exec_res = approve_and_run_sql_wrapper(json.dumps({"sql": entry["oracle_sql"], "db": st.session_state["current_db"]}))
                if exec_res.get("status") == "ok":
                    st.success("Executed.")
                    st.dataframe(pd.DataFrame(exec_res["result"]))
                else:
                    st.error(exec_res.get("message"))

        # 3. Report Generation (AWR/ASH)
        if entry.get("intent") in ["awr_report", "ash_report"] and res.get("status") == "ok":
            rpt = res.get("report", "")
            fname = res.get("filename", "report.html")
            st.success("Report Generated!")
            
            # Add to history if new
            if not any(x["id"] == uid for x in st.session_state["awr_history"]):
                st.session_state["awr_history"].append({
                    "id": uid,
                    "ts": datetime.now(),
                    "label": entry.get("oracle_sql", "Report"),
                    "type": "AWR" if "AWR" in entry.get("intent").upper() else "ASH",
                    "report_html": rpt,
                    "period_str": entry.get("oracle_sql")
                })
            
            col1, col2 = st.columns([1,3])
            with col1:
                st.download_button("Download HTML", rpt, file_name=fname, mime="text/html", key=f"dl_{uid}")
            with col2:
                if st.button("Analyze with AI", key=f"an_{uid}"):
                    with st.spinner("Analyzing..."):
                        ai_an = analyze_awr_report(rpt)
                    st.markdown(ai_an.get("analysis", "No analysis returned"))

        # 4. Interactive Analysis Response
        if entry.get("intent") == "report_analysis":
             st.markdown(f"**Analysis:**\n{res.get('analysis', '')}")
        # 6. Patch Download Streaming
        if entry.get("intent") == "patch_download" and entry.get("request") is None:
            # This is the AI patch response
            if entry["oracle_result"]["status"] == "success":
                st.success("Patch Download Completed!")
                # st.markdown(entry["oracle_result"]["message"])
            else:
                st.error("Patch Download Failed")
                # st.code(entry["oracle_result"]["message"])
        # if entry.get("intent") == "patch_download":
        #     res = entry.get("oracle_result", {})
        #     if res.get("status") == "ok" and entry["id"] in st.session_state["patch_processes"]:
        #         proc = st.session_state["patch_processes"][entry["id"]]
        #         if proc.poll() is None:  # Still running
        #             st.info("Download in progress... (streaming logs below)")
        #             log_output = ""
        #             for line in iter(proc.stdout.readline, ''):
        #                 if line:  # Stream lines
        #                     st.text(line.strip())
        #                     log_output += line
        #                 if proc.poll() is not None:  # Finished
        #                     break
        #             if log_output:
        #                 with open(os.path.join(LOG_DIR, f"patch_download_{entry['id']}.log"), 'w') as f:
        #                     f.write(log_output)
        #             del st.session_state["patch_processes"][entry["id"]]  # Cleanup
        #             st.success("Download complete! Check files in " + AUTUPGRADE_DIR)
        #         else:
        #             st.warning("Process ended unexpectedly.")
        #     else:
        #         color = "green" if res.get("status") == "ok" else "red"
        #         st.markdown(f"<div style='color:{color}; padding:10px; background:white; border-radius:10px; border:1px solid #eee'>{res['message']}</div>", unsafe_allow_html=True)
                # 5. Jenkins Matches
        if entry.get("intent") == "jenkins":
            matches = entry.get("jenkins_matches", [])
            if matches:
                 # 1. Select job
                 sel = st.selectbox("Select Job", matches, key=f"jsel_{uid}")
                 
                 # 2. Get full job details for parameters/context
                 job_details = find_jenkins_job_by_name(sel, jenkins_server)
                 param_values = {}
                 
                 if job_details and job_details.get("parameters"):
                     st.subheader(f"Parameters for {sel}")
                     st.write(f"Description: {job_details.get('description', 'N/A')}")
                     
                     # 3. Dynamically create input fields for each parameter
                     for param in job_details["parameters"]:
                         p_name = param["name"]
                         p_default = param["default"]
                         
                         param_values[p_name] = st.text_input(
                             label=f"**{p_name}** (Type: {param['type'].split('.')[-1]})",
                             value=p_default,
                             key=f"param_{uid}_{p_name}" # Unique key required by Streamlit
                         )
                 
                 # 4. Run Button Logic
                 if st.button("Run Job", key=f"jrun_{uid}"):
                     st.info(f"Triggering {sel} with parameters: {param_values}")
                     # Pass the collected parameter dictionary
                     run_res = run_jenkins_job_and_get_output(sel, param_values, jenkins_server)
                     
                     if run_res["status"] == "SUCCESS":
                         st.success("Job Success")
                         st.code(run_res["console"][-500:])
                     else:
                         st.error(f"Job Failed ({run_res['status']})")
                         
                         # 5. Call the improved analysis function
                         with st.spinner("AI Analyzing failure with enhanced context..."):
                             ai_help = analyze_jenkins_failure(
                                 run_res.get("console", ""))
                                #  job_details=job_details # Pass the job context!
                            
                         
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
                st.write("No matching jobs found.")

    st.markdown("</div></div>", unsafe_allow_html=True)

# 9. Input Area
st.markdown("---")
st.text_input("Ask Oracle/Jenkins AI:", key="task_input", placeholder="e.g., 'Connect to PROD', 'ASH for last 15 mins', 'AWR from 10:00 to 11:00', 'Show history'")
st.button("Process Request", on_click=process_request_callback)