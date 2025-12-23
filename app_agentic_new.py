import time
import os
import json
import pandas as pd
import streamlit as st
import jenkins
import traceback
import re
from datetime import datetime
from dotenv import load_dotenv

# --- AutoGen Imports ---
from autogen import AssistantAgent, UserProxyAgent, register_function

# --- Local Module Imports ---
from oracle_runner_55555 import (
    run_oracle_query, get_db_list, generate_awr_report, generate_ash_report,
    get_snapshots_for_time, analyze_awr_report, compare_awr_reports, run_full_health_check
)
from patch_forstreamlit import download_oracle_patch

# --- 1. Configuration & Environment ---
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")

if not openai_api_key:
    st.error("CRITICAL: OPENAI_API_KEY not found.")
    st.stop()

# Cert setup
os.environ["REQUESTS_CA_BUNDLE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"
os.environ["SSL_CERT_FILE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"

st.set_page_config(page_title="Oracle + Jenkins Agentic Console", layout="wide", page_icon="🤖")

# --- 2. Initialization & Caching ---

@st.cache_resource
def get_jenkins_server():
    """Establishes Jenkins connection once."""
    try:
        server = jenkins.Jenkins("http://localhost:9020", username="dba", password="113bb934053435f19fa62d94f8c79a108c")
        _ = server.get_whoami() 
        return server
    except Exception as e:
        print(f"Jenkins Connection Error: {e}")
        return None

@st.cache_data(show_spinner="Fetching Jenkins Jobs...")
def build_job_map():
    """Pre-loads full job list (names only for speed)."""
    server = get_jenkins_server()
    if not server: return {}
    
    job_map = {}
    try:
        def fetch_recursive(folder=""):
            try:
                jobs = server.get_jobs(folder) if folder else server.get_jobs()
            except: return []
            
            for j in jobs:
                fullname = f"{folder}/{j['name']}" if folder else j['name']
                if "Folder" in j.get("_class", ""):
                    fetch_recursive(fullname)
                else:
                    job_map[fullname] = {"url": j['url']}
        fetch_recursive()
    except Exception as e:
        print(f"Job Fetch Error: {e}")
    return job_map

# Load Session State
if "dbs" not in st.session_state: st.session_state["dbs"] = get_db_list() or ["DEFAULT"]
if "current_db" not in st.session_state: st.session_state["current_db"] = st.session_state["dbs"][0]
if "job_map" not in st.session_state: st.session_state["job_map"] = build_job_map()
if "messages" not in st.session_state: st.session_state["messages"] = []
if "awr_history" not in st.session_state: st.session_state["awr_history"] = []
if "health_report" not in st.session_state: st.session_state["health_report"] = None
if "compare_result" not in st.session_state: st.session_state["compare_result"] = None

# NEW STATE VARIABLES FOR UI INTERACTION
if "jenkins_matches" not in st.session_state: st.session_state["jenkins_matches"] = []
if "selected_job_name" not in st.session_state: st.session_state["selected_job_name"] = None
if "last_console" not in st.session_state: st.session_state["last_console"] = None
if "last_build_analysis" not in st.session_state: st.session_state["last_build_analysis"] = None  # For agent to share UI results
if "current_triggered_job" not in st.session_state: st.session_state["current_triggered_job"] = None
if "current_triggered_build" not in st.session_state: st.session_state["current_triggered_build"] = None
# NEW: Polling State (for live status updates across reruns)
if "polling_active" not in st.session_state: st.session_state["polling_active"] = False
if "polling_job" not in st.session_state: st.session_state["polling_job"] = None
if "polling_build" not in st.session_state: st.session_state["polling_build"] = None
if "polling_status" not in st.session_state: st.session_state["polling_status"] = "IDLE"
# --- 3. Agent Tool Definitions ---

def tool_list_jenkins_jobs(search_term: str = "") -> str:
    """
    1. Searches for jobs.
    2. POPULATES SESSION STATE so the UI can render the dropdown.
    3. Returns a text summary to the agent.
    """
    all_jobs = list(st.session_state["job_map"].keys())
    
    if not search_term:
        matches = all_jobs[:20]
        msg = "Found top 20 jobs."
    else:
        matches = [j for j in all_jobs if search_term.lower() in j.lower()]
        msg = f"Found {len(matches)} jobs matching '{search_term}'."

    # CRITICAL: Update state to trigger UI rendering
    st.session_state["jenkins_matches"] = matches
    
    if not matches:
        return f"No jobs found matching '{search_term}'."
    
    return f"{msg} I have updated the 'Job Execution Panel' below with the dropdown list."

def tool_run_sql(sql_query: str) -> str:
    target = st.session_state["current_db"]
    try:
        sql_query = sql_query.strip().rstrip(";")
        result = run_oracle_query(sql_query, target)
        if isinstance(result, list):
            if not result: return "Query executed. No rows returned."
            df = pd.DataFrame(result)
            return f"**Results from {target}:**\n{df.to_markdown(index=False)}"
        elif isinstance(result, dict) and "error" in result:
            return f"**SQL Error:** {result['error']}"
        else:
            return str(result)
    except Exception as e:
        return f"Execution Error: {str(e)}"

def tool_generate_report(time_desc: str, duration_minutes: int = 30) -> str:
    db = st.session_state["current_db"]
    if duration_minutes <= 120:
        res = generate_ash_report(duration_minutes, db)
        rtype = "ASH"
    else:
        hours = duration_minutes / 60.0
        start_snap, end_snap = get_snapshots_for_time(hours, db)
        if not start_snap: return f"Could not find snapshots for last {hours} hours."
        res = generate_awr_report(start_snap, end_snap, db)
        rtype = "AWR"
        
    if res.get("status") == "ok":
        path = res.get("filename", f"{rtype}_{int(time.time())}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(res.get("report", ""))
        st.session_state["awr_history"].append({
            "label": f"{rtype} - {time_desc} ({datetime.now().strftime('%H:%M')})",
            "path": path, "type": rtype, "html": res.get("report", "")
        })
        return f"SUCCESS: Generated {rtype} report at `{path}`."
    return f"FAILED: {res.get('message')}"

def tool_health_check() -> str:
    res = run_full_health_check(st.session_state["current_db"])
    if res["status"] == "ok":
        st.session_state["health_report"] = res["report"]
        return f"**Health Check Summary:**\n{res['report'][:500]}..."
    return f"Health Check Failed: {res.get('message')}"

def tool_compare_reports(idx1: int = -2, idx2: int = -1) -> str:
    try:
        h = st.session_state["awr_history"]
        res = compare_awr_reports(h[idx1]['html'], h[idx2]['html'], h[idx1]['label'], h[idx2]['label'])
        if res.get("status") == "ok":
            st.session_state["compare_result"] = res["comparison"]
            return "**Comparison Ready** (See display above)."
        return f"Compare Error: {res.get('message')}"
    except: return "Compare failed."

def tool_get_build_console(job_name: str, build_number: int = None) -> str:
    if st.session_state.get("current_triggered_job") != job_name or (build_number and st.session_state.get("current_triggered_build") != build_number):
        return f"Analysis limited to freshly triggered jobs from this GUI session. Current session job: {st.session_state.get('current_triggered_job', 'None')}. Cannot fetch past builds."
    server = get_jenkins_server()
    if not server:
        return "Jenkins server unavailable."
    try:
        if build_number is None:
            build_number = st.session_state.get("current_triggered_build")
        console = server.get_build_console_output(job_name, build_number)
        st.session_state["last_console"] = console  # Store for analysis
        return f"Fetched console for {job_name} #{build_number} (length: {len(console)} chars). Ready for analysis."
    except Exception as e:
        return f"Error fetching console: {str(e)}"
def tool_download_patch(req: str) -> str:
    res = download_oracle_patch(req)
    return f"Patch: {res.get('message')}"
def tool_prepare_failure_analysis(console_text: str = None) -> str:
    if console_text is None:
        console_text = st.session_state.get("last_console", "")
    if not console_text:
        return "No console log available. Fetch one first with get_build_console."
    st.session_state["last_console"] = console_text
    return f"Prepared console log for analysis (length: {len(console_text)} chars). The agent will analyze it next."
def tool_analyze_failure(job_name: str, build_number: int) -> str:
    """
    Called by the agent (AutoGen) when a build failed.
    Must set st.session_state['ai_analysis_result'] to a dict:
      { "root_cause": "...", "failed_line": "...", "suggestion": "..." }
    and set st.session_state['last_analyzed_job'] = "<job> #<build>".
    Return a serializable dict (AutoGen sees this as the tool output).
    Robust failure analysis:
    1. Fetch console for job_name#build_number
    2. Try deterministic heuristics (stack traces, ERROR lines)
    3. If heuristics insufficient, call LLM with strict JSON output request
    4. Robustly parse JSON or gracefully fallback to heuristic summary
    Stores result to st.session_state['ai_analysis_result'] and returns short status.
    """
    import json, re, textwrap
    server = get_jenkins_server()
    if not server:
        return "Jenkins not available."

    try:
        console = server.get_build_console_output(job_name, build_number)
    except Exception as e:
        st.session_state["ai_analysis_result"] = {
            "root_cause": "Could not fetch console",
            "failed_line": "N/A",
            "suggestion": str(e)
        }
        return f"Error fetching console: {e}"

    if not console or not console.strip():
        st.session_state["ai_analysis_result"] = {
            "root_cause": "Empty console log",
            "failed_line": "N/A",
            "suggestion": "Ensure the build executed and console is accessible."
        }
        return "Empty console."

    # Save raw console for UI
    st.session_state["last_console"] = console
    # Take the most relevant tail (bottom of the log), but also keep some preceding context
    tail_chars = 20000
    log_tail = console[-tail_chars:] if len(console) > tail_chars else console

    # ------------------------------------------------------------------
    # Heuristic extraction (fast, often enough)
    # ------------------------------------------------------------------
    lines = log_tail.splitlines()
    # Try to find the last non-empty ERROR / Exception / Traceback block
    error_lines = []
    # look for typical patterns in reverse to find the most recent error
    for i in range(len(lines)-1, -1, -1):
        ln = lines[i]
        if re.search(r"\b(ERROR|FAILED|FATAL|Exception|Traceback|Traceback \(most recent call last\))\b", ln, re.I):
            # capture a small window around the match
            start = max(0, i-10)
            end = min(len(lines), i+6)
            error_lines = lines[start:end]
            break

    if error_lines:
        # Compose heuristic summary
        failed_line = next((l for l in error_lines if re.search(r"\b(ERROR|FAILED|Exception|Traceback)\b", l, re.I)), error_lines[len(error_lines)//2])
        heuristic = {
            "root_cause": "Detected likely failure from log snippet (heuristic).",
            "failed_line": failed_line.strip(),
            "suggestion": "Inspect surrounding log lines and stack trace. Common fixes: missing dependency, permission or environment variable, failing test or missing file."
        }
        # If heuristic is very clear (contains Exception name or ERROR with stack trace), store and return
        if re.search(r"Exception|Traceback|Error:", "\n".join(error_lines), re.I) or re.search(r"\b(ERROR|FAILED|FATAL)\b", "\n".join(error_lines), re.I):
            st.session_state["ai_analysis_result"] = heuristic
            return "Analysis complete (heuristic)."

    # ------------------------------------------------------------------
    # If heuristics not decisive, ask LLM with strict JSON-only instruction
    # ------------------------------------------------------------------
    # Build a concise prompt (no huge logs). Use the last ~12k chars to keep context reasonable.
    snippet = log_tail[-12000:] if len(log_tail) > 12000 else log_tail
    system_prompt = (
        "You are a Jenkins build failure diagnosis assistant. "
        "Read the provided log snippet and return ONLY valid JSON (no explanation). "
        "JSON keys must be: root_cause, failed_line, suggestion. "
        "Keep values short (max 200 chars each)."
    )
    user_prompt = f"LOG_SNIPPET:\n{snippet}\n\nReturn valid JSON now."

    # Use a short-lived analyzer agent to avoid mutating global personas
    try:
        analyzer = AssistantAgent(name="jenkins_analyzer_temp",
                                  llm_config=llm_config,
                                  system_message=system_prompt)
        reply = analyzer.generate_reply([{"role": "user", "content": user_prompt}])
    except Exception as e:
        # LLM call failed — fallback to heuristic
        st.session_state["ai_analysis_result"] = heuristic if error_lines else {
            "root_cause": "LLM call failed and heuristic could not find obvious error",
            "failed_line": "N/A",
            "suggestion": f"LLM error: {str(e)}"
        }
        return "LLM error — used fallback."

    # Try robust JSON extraction
    m = re.search(r"(\{[\s\S]*\})", reply, flags=re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(1))
            st.session_state["ai_analysis_result"] = {
                "root_cause": parsed.get("root_cause", "Unknown"),
                "failed_line": parsed.get("failed_line", "Unknown"),
                "suggestion": parsed.get("suggestion", "Check logs and environment")
            }
            return "Analysis complete (LLM)."
        except json.JSONDecodeError:
            # Bad JSON — fall back
            st.session_state["ai_analysis_result"] = heuristic if error_lines else {
                "root_cause": "LLM returned invalid JSON",
                "failed_line": "N/A",
                "suggestion": f"LLM reply: {reply[:500]}"
            }
            return "LLM returned invalid JSON; used fallback."

    # Final fallback: if no JSON and no heuristic, return top few lines
    if error_lines:
        st.session_state["ai_analysis_result"] = {
            "root_cause": "Heuristic found probable issue",
            "failed_line": error_lines[len(error_lines)//2].strip(),
            "suggestion": "Investigate stack trace / failing command in the log."
        }
        return "Analysis complete (heuristic fallback)."

    # Nothing found
    st.session_state["ai_analysis_result"] = {
        "root_cause": "No obvious failure found in recent log tail",
        "failed_line": "N/A",
        "suggestion": "Fetch a larger portion of console log or check job workspace and upstream logs."
    }
    return "Analysis complete (no clear failure)."

# --- NEW TOOL: Pure AutoGen failure analysis ---
# 1. NEW TOOL — pure AutoGen, stable, no disappearing
llm_config = {
    "config_list": [{"model": "gpt-4o-mini", "api_key": openai_api_key}],
    "temperature": 0, "timeout": 120,
}

oracle_admin = AssistantAgent(
    name="Oracle_Admin", llm_config=llm_config, system_message="Placeholder"
)

user_proxy = UserProxyAgent(
    name="User_Proxy", human_input_mode="NEVER", max_consecutive_auto_reply=10, code_execution_config=False
)

register_function(tool_run_sql, caller=oracle_admin, executor=user_proxy, name="run_sql", description="Run Oracle SQL")
register_function(tool_generate_report, caller=oracle_admin, executor=user_proxy, name="create_report", description="Create performance report")
register_function(tool_list_jenkins_jobs, caller=oracle_admin, executor=user_proxy, name="list_jobs", description="Search Jenkins jobs. Saves matches to UI.")
register_function(tool_health_check, caller=oracle_admin, executor=user_proxy, name="health_check", description="Run Health Check")
register_function(tool_compare_reports, caller=oracle_admin, executor=user_proxy, name="compare_reports", description="Compare reports")
register_function(tool_download_patch, caller=oracle_admin, executor=user_proxy, name="download_patch", description="Download Patch")
register_function(tool_get_build_console, caller=oracle_admin, executor=user_proxy, name="get_build_console", description="Fetch Jenkins build console log by job and optional build number (defaults to latest).")
register_function(tool_prepare_failure_analysis, caller=oracle_admin, executor=user_proxy, name="prepare_failure_analysis", description="Prepare the last fetched console for analysis (stores in state).")
register_function(
    tool_analyze_failure,
    caller=oracle_admin,
    executor=user_proxy,
    name="analyze_failure",
    description="Analyzes a failed Jenkins build or job and returns JSON with root cause, failed line, and fix.( use this for jenkins related failure analysis)")
# --- 5. Helper: Render Jenkins UI ---
def render_jenkins_ui():
    """Renders the Dropdown + Parameters + Run Button logic."""
    if not st.session_state.get("jenkins_matches"):
        return

    st.markdown("---")
    st.subheader("🛠️ Job Execution Panel")
    
    # --- 1. DISPLAY ANALYSIS RESULTS (PERSISTENT) ---
    # This block is now at the top so it renders regardless of polling status
    if st.session_state.get("ai_analysis_result"):
        result = st.session_state["ai_analysis_result"]
        
        st.error(f"Analysis Result for {st.session_state.get('last_analyzed_job', 'Unknown Job')}")
        
        c1, c2 = st.columns([1, 2])
        with c1:
            st.markdown(f"**Root Cause**\n\n{result.get('root_cause', 'N/A')}")
            st.markdown(f"**Failed Line**\n\n`{result.get('failed_line', 'N/A')}`")
        with c2:
            st.markdown(f"**Suggested Fix**\n\n{result.get('suggestion', 'N/A')}")
            
        with st.expander("View Analyzed Console Log"):
            st.code(st.session_state.get("last_console", "No log available"), language="text")
            
        if st.button("Clear Analysis"):
            st.session_state["ai_analysis_result"] = None
            st.session_state["last_console"] = None
            st.rerun()
        st.markdown("---")

    # --- 2. JOB SELECTION & FORM ---
    selected_job = st.selectbox(
        "Select Job to Configure", 
        st.session_state["jenkins_matches"],
        key="job_selector_ui"
    )

    if not selected_job: return

    # Fetch Params logic (Same as before)
    server = get_jenkins_server()
    if not server: 
        st.error("Jenkins disconnected.")
        return

    # ... (Keep your existing parameter fetching logic here) ...
    # For brevity, assuming the standard fetching logic exists or is cached
    # If you need that block repeated, let me know, but typically it's unchanged.
    
    # [Restoring the fetching logic for completeness to avoid copy-paste errors]
    with st.spinner("Fetching parameters..."):
        try:
            job_info = server.get_job_info(selected_job)
        except Exception as e:
            st.error(f"Could not load job details: {e}")
            return

    # Render Form
    defs = job_info.get('property', []) + job_info.get('actions', [])
    param_defs = []
    _seen_param_names = set()
    param_defs = []
    _seen_param_names = set()
    for d in job_info.get('property', []) + job_info.get('actions', []):
     if isinstance(d, dict) and 'parameterDefinitions' in d:
        for p in d['parameterDefinitions']:
            if p['name'] in _seen_param_names:
                continue
            _seen_param_names.add(p['name'])
            param_defs.append(p)

            
    # Fallback search
    if not param_defs:
        for action in job_info.get('actions', []):
            if 'parametersDefinitions' in action:
                param_defs = action['parametersDefinitions']
                break

    form_values = {}
    seen = set()
    if param_defs:
        with st.form(key=f"form_{selected_job}"):
            st.markdown(f"**Configure: {selected_job}**")
            for i,p in enumerate(param_defs):
                p_name = p['name']
                if p_name in seen:
                        continue
                seen.add(p_name)
                p_default = p.get('defaultParameterValue', {}).get('value', '')
                unique_key = f"{selected_job}_{p_name}_param_{i}"
                p_type = p.get('type', '') or p.get('_class', '')
                if p_type and 'Boolean' in p_type:
                   form_values[p_name] = st.checkbox(p_name, value=(str(p_default).lower()=='true'), key=unique_key)
                elif p_type and 'Choice' in p_type:
         # normalize choices: sometimes choices is a dict/list of strings
                 options = p.get('choices', [])
                 if isinstance(options, dict) and 'values' in options:
                       options = options['values']
                 form_values[p_name] = st.selectbox(p_name, options=options or [p_default], key=unique_key)
                else:
                   form_values[p_name] = st.text_input(p_name, value=str(p_default), key=unique_key)
            submit = st.form_submit_button("Run Job")
    else:
        st.info(f"Job '{selected_job}' has no parameters.")
        if st.button("Run Job Now", key=f"run_button_{selected_job}"):
            submit = True
            form_values = {}
        else:
            submit = False

    # --- 3. TRIGGER & POLLING LOGIC ---
    if submit:
        try:
            queue_id = server.build_job(selected_job, form_values)
            st.success(f"Job triggered! Queue ID: {queue_id}")
            
            # Wait for build number
            build_number = None
            with st.spinner("Waiting for build to start..."):
                for _ in range(10): # Try for 20 seconds
                    try:
                        q_item = server.get_queue_item(queue_id)
                        if 'executable' in q_item:
                            build_number = q_item['executable']['number']
                            break
                    except: pass
                    time.sleep(2)
            
            if build_number:
                st.session_state["polling_active"] = True
                st.session_state["polling_job"] = selected_job
                st.session_state["polling_build"] = build_number
                st.session_state["polling_status"] = "BUILDING"
                st.rerun()
            else:
                st.warning("Job queued but build number not yet assigned. Check Jenkins manually.")

        except Exception as e:
            st.error(f"Failed to trigger: {e}")

    # --- 4. POLLING HANDLER ---
    if st.session_state.get("polling_active", False):
        p_job = st.session_state["polling_job"]
        p_build = st.session_state["polling_build"]
        
        try:
            b_info = server.get_build_info(p_job, p_build)
            status = b_info.get('result')
            
            if status is None: # RUNNING
                st.info(f"🔨 {p_job} #{p_build} is BUILDING...")
                time.sleep(3)
                st.rerun()
                
            elif status == 'SUCCESS':
                st.success(f"{p_job} #{p_build} SUCCEEDED!")
                st.session_state["polling_active"] = False
                
            elif status in ['FAILURE', 'ABORTED']:
                st.error(f"{p_job} #{p_build} {status}")
                
                # --- TRIGGER ANALYSIS ---
                st.session_state["last_analyzed_job"] = f"{p_job} #{p_build}"
                with st.spinner("🤖 AI is analyzing the failure..."):
                    instruction = (
        f"ERROR: Jenkins job '{p_job}' build {p_build} finished with status {status}.\n"
        "You must call the registered tool of jenkins failure \n "
        "with the job name and build number to perform failure analysis and store the result in session state. "
        "Do not do any other long-running actions. Terminate after the tool returns."
    )
                    
                    # Call the tool function DIRECTLY to avoid agent round-trips if possible, 
                    # or keep using the agent if preferred. 
                    # Using the agent trigger:
                    user_proxy.initiate_chat(
                        oracle_admin,
                        message=instruction,
                        clear_history=True
                    )
                
                # Stop polling, but the RESULT code at the top of the function
                # will pick up the 'ai_analysis_result' on the next refresh.
                st.session_state["polling_active"] = False
                st.rerun()
                analysis = st.session_state.get("ai_analysis_result")
                job_label = st.session_state.get("last_analyzed_job", f"{p_job} #{p_build}")
                if analysis:
                    st.subheader(f"AI Analysis for {job_label}")
                    st.markdown("**Root cause**")
                    st.write(analysis.get("root_cause", "—"))
                    st.markdown("**Failing line / snippet**")
                    st.code(analysis.get("failed_line", "—"))
                    st.markdown("**Suggested fix**")
                    st.write(analysis.get("suggestion", "—"))
            else:
                st.warning("Agent did not produce analysis. Check logs or tool implementation.")
                
        except Exception as e:
            st.error(f"Polling error: {e}")
            st.session_state["polling_active"] = False
            st.rerun()
# --- 6. Main Streamlit Layout ---

with st.sidebar:
    st.header("🔌 Connection")
    selected_db = st.selectbox("Active Database", st.session_state["dbs"], 
                               index=st.session_state["dbs"].index(st.session_state["current_db"]))
    if selected_db != st.session_state["current_db"]:
        st.session_state["current_db"] = selected_db
        st.rerun()
    
    if st.button("🏥 Run Health Check", type="primary"):
        res = tool_health_check()
        st.success("Done") if "Summary" in res else st.error(res)

    if st.session_state["awr_history"]:
        st.divider()
        st.caption("Recent Reports")
        for item in reversed(st.session_state["awr_history"][-5:]):
            if os.path.exists(item['path']):
                with open(item['path'], "rb") as f:
                    st.download_button(f"📥 {item['label']}", f, file_name=item['path'])

# Main Page
st.title("🤖 Oracle + Jenkins Agentic Console")
st.caption(f"Context: **{st.session_state['current_db']}**")

# Expanders for Reports
if st.session_state["health_report"]:
    with st.expander("🏥 Active Health Report", expanded=True):
        st.markdown(st.session_state["health_report"])
        if st.button("Close Report"): st.session_state["health_report"] = None; st.rerun()

if st.session_state["compare_result"]:
    with st.expander("⚖️ Comparison", expanded=True):
        st.markdown(st.session_state["compare_result"])
        if st.button("Close Comparison"): st.session_state["compare_result"] = None; st.rerun()

# Chat History
for msg in st.session_state["messages"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Chat Input
prompt = st.chat_input("Ex: 'Show couchbase jobs', 'Run AWR for last hour', 'Analyze last failure of AI_TEST_from_PYTHON_SCRIPT'")
if prompt:
    st.session_state["messages"].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    sys_msg = f"""
    You are an Oracle DBA & Jenkins Admin.
    **DB Context:** {st.session_state['current_db']}
    
    **INSTRUCTIONS:**
    1. **Jenkins:** If user asks to find/list jobs, use `list_jobs`. Tell the user to "Select from the panel below".
    2. **Performance:** For AWR/ASH, use `create_report`.
    3. **Health:** Use `health_check`.
    4. **Failures:** When prompted about a JOB failure (e.g., "analyze last failure"), call `analyze_failure` with the job and build number. It will fetch and diagnose automatically.    Reply with TERMINATE when done.
    """
    oracle_admin.update_system_message(sys_msg)

    with st.chat_message("assistant"):
        with st.spinner("Agent working..."):
            try:
                chat_res = user_proxy.initiate_chat(oracle_admin, message=prompt, clear_history=True)
                
                # Logic to extract clean response
                final_text = "Task completed."
                if chat_res.chat_history:
                    for msg in reversed(chat_res.chat_history):
                        if msg.get("content"):
                            txt = str(msg.get("content"))
                            if "TERMINATE" not in txt:
                                final_text = txt
                                break
                                
                clean_msg = final_text.replace("TERMINATE", "").strip()
                st.markdown(clean_msg)
                st.session_state["messages"].append({"role": "assistant", "content": clean_msg})
                m = re.search(r"(\{.*\})", clean_msg, flags=re.DOTALL)
                if m:
                    try:
                        parsed = json.loads(m.group(1))
                        st.session_state["last_build_analysis"] = f"**Root Cause:** {parsed['root_cause']}\n**Failed Line:** {parsed['failed_line']}\n**Suggestion:** {parsed['suggestion']}"
                    except:
                        pass
                if "updated the" in clean_msg or "Found" in clean_msg:
                    st.rerun() # Refresh to show the new UI
            except Exception as e:
                st.error(f"Error: {e}")

# --- 7. RENDER DYNAMIC UI (The "Original Way" Logic) ---
# This runs after the chat logic. If the agent found jobs, this panel appears.
render_jenkins_ui()