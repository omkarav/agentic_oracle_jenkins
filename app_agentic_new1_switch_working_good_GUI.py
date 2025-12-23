import time
import os
import json
import pandas as pd
import streamlit as st
import jenkins
import re
import uuid
from datetime import datetime
from difflib import get_close_matches
from dotenv import load_dotenv

# --- AutoGen Imports ---
from autogen import AssistantAgent, UserProxyAgent, register_function

# --- Local Module Imports ---
from oracle_runner_55555 import (
    run_oracle_query, get_db_list, generate_awr_report, generate_ash_report,
    get_snapshots_for_time, run_full_health_check
)
from patch_forstreamlit import download_oracle_patch

# --- 1. Configuration & Setup ---
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")

if not openai_api_key:
    st.error("CRITICAL: OPENAI_API_KEY not found in .env")
    st.stop()

os.environ["REQUESTS_CA_BUNDLE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"
os.environ["SSL_CERT_FILE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"

st.set_page_config(page_title="Oracle + Jenkins Agentic Console", layout="wide", page_icon="🤖")

# --- 2. Session State Initialization ---
if "dbs" not in st.session_state: st.session_state["dbs"] = get_db_list()
if "current_db" not in st.session_state: st.session_state["current_db"] = st.session_state["dbs"][0] if st.session_state["dbs"] else "DEFAULT"
if "messages" not in st.session_state: st.session_state["messages"] = []
if "awr_history" not in st.session_state: st.session_state["awr_history"] = []
if "health_report" not in st.session_state: st.session_state["health_report"] = None

# Jenkins Specific State
if "job_map" not in st.session_state: st.session_state["job_map"] = {} 
if "jenkins_matches" not in st.session_state: st.session_state["jenkins_matches"] = []
if "active_job_selection" not in st.session_state: st.session_state["active_job_selection"] = None
if "polling_active" not in st.session_state: st.session_state["polling_active"] = False
if "polling_job" not in st.session_state: st.session_state["polling_job"] = None
if "polling_build" not in st.session_state: st.session_state["polling_build"] = None

# --- 3. Helper Functions & Cache ---

@st.cache_resource
def get_jenkins_server():
    try:
        # ⚠️ Verify these credentials are correct and accessible
        server = jenkins.Jenkins("http://localhost:9020", username="dba", password="113bb934053435f19fa62d94f8c79a108c")
        user = server.get_whoami() 
        return server
    except Exception as e:
        print(f"Jenkins Connection Error: {e}")
        return None

@st.cache_data(show_spinner="Fetching Jenkins Jobs...")
def fetch_all_jobs_recursive():
    server = get_jenkins_server()
    if not server: return []
    
    job_list = []
    def recursive_fetch(folder=""):
        try:
            jobs = server.get_jobs(folder) if folder else server.get_jobs()
            for j in jobs:
                fullname = f"{folder}/{j['name']}" if folder else j['name']
                if "Folder" in j.get("_class", ""):
                    recursive_fetch(fullname)
                else:
                    job_list.append(fullname)
        except: pass
    
    recursive_fetch()
    return job_list

# Initialize Job List
if not st.session_state["job_map"]:
    all_jobs = fetch_all_jobs_recursive()
    st.session_state["job_map"] = {j: j for j in all_jobs}

# --- 4. Define Agent Tools (The "Skills") ---

def tool_change_database(target_name: str) -> str:
    """Switches the active database connection."""
    available = st.session_state["dbs"]
    target_name = target_name.upper().strip()
    
    # Exact match
    if target_name in available:
        st.session_state["current_db"] = target_name
        return f"SUCCESS: Switched context to database '{target_name}'."
    
    # Fuzzy match
    matches = get_close_matches(target_name, available, n=1, cutoff=0.4)
    if matches:
        st.session_state["current_db"] = matches[0]
        return f"SUCCESS: Switched to closest match: '{matches[0]}'."
    
    return f"FAILURE: Database '{target_name}' not found. Available: {available}"

def tool_run_sql(sql_query: str) -> str:
    """Executes Oracle SQL query."""
    db = st.session_state["current_db"]
    try:
        sql_query = sql_query.strip().rstrip(";")
        result = run_oracle_query(sql_query, db)
        
        if isinstance(result, list):
            if not result: return "Query executed. No rows returned."
            df = pd.DataFrame(result)
            return f"**SQL Result ({len(df)} rows):**\n{df.head(10).to_markdown(index=False)}"
        elif isinstance(result, dict) and "error" in result:
            return f"SQL Error: {result['error']}"
        return str(result)
    except Exception as e:
        return f"Exception: {str(e)}"

def tool_generate_report(report_type: str, duration_minutes: int = 30) -> str:
    db = st.session_state["current_db"]
    try:
        if report_type.upper() == "ASH":
            res = generate_ash_report(duration_minutes, db)
            label = f"ASH Last {duration_minutes} mins"
        else:
            hours = duration_minutes / 60.0
            start_snap, end_snap = get_snapshots_for_time(hours, db)
            if not start_snap: return "No snapshots found."
            res = generate_awr_report(start_snap, end_snap, db)
            label = f"AWR Last {hours:.1f} Hours"

        if res.get("status") == "ok":
            st.session_state["awr_history"].append({
                "id": str(uuid.uuid4()),
                "ts": datetime.now(),
                "label": label,
                "path": res.get("filename", "report.html")
            })
            return f"SUCCESS: Generated {label}."
        else:
            return f"FAILURE: {res.get('message')}"
    except Exception as e:
        return f"Error: {str(e)}"

def tool_search_jenkins_jobs(search_term: str) -> str:
    """Searches Jenkins jobs and updates the UI."""
    all_jobs = list(st.session_state["job_map"].keys())
    matches = [j for j in all_jobs if search_term.lower() in j.lower()]
    
    st.session_state["jenkins_matches"] = matches
    
    # Auto-select first match to prevent 'None' selection issues
    st.session_state["active_job_selection"] = matches[0] if matches else None
    
    if not matches:
        return f"No jobs found matching '{search_term}'."
    
    return f"I found {len(matches)} jobs matching '{search_term}'. I have displayed the 'Job Execution Panel' below. Please configure and run it there."

def tool_run_health_check() -> str:
    res = run_full_health_check(st.session_state["current_db"])
    if res["status"] == "ok":
        st.session_state["health_report"] = res["report"]
        return "Health Check Completed. Report displayed."
    return f"Health Check Failed: {res.get('message')}"

# --- 5. Agent Configuration ---

llm_config = {
    "config_list": [{"model": "gpt-4o-mini", "api_key": openai_api_key}],
    "temperature": 0,
}

oracle_admin = AssistantAgent(
    name="Oracle_Admin",
    llm_config=llm_config,
    system_message="""
You are an Oracle DBA and Jenkins Admin assistant.
- User will ask you to switch DBs, run SQL, check health, or find Jenkins jobs.
- **Jenkins:** ALWAYS use `tool_search_jenkins_jobs` first. Once found, tell the user to use the UI panel.
- **Database:** Use `tool_change_database` to switch context.
Reply "TERMINATE" when the task is done.
    """
)

# === FIX 1: Robust Termination Logic ===
user_proxy = UserProxyAgent(
    name="User_Proxy",
    human_input_mode="NEVER",
    max_consecutive_auto_reply=5,
    code_execution_config=False,
    # Prevents TypeError: argument of type 'NoneType' is not iterable
    is_termination_msg=lambda x: x.get("content") is not None and "TERMINATE" in x.get("content")
)

register_function(tool_change_database, caller=oracle_admin, executor=user_proxy, name="switch_db", description="Switch active database")
register_function(tool_run_sql, caller=oracle_admin, executor=user_proxy, name="run_sql", description="Run SQL Query")
register_function(tool_generate_report, caller=oracle_admin, executor=user_proxy, name="generate_report", description="Generate ASH/AWR report")
register_function(tool_search_jenkins_jobs, caller=oracle_admin, executor=user_proxy, name="search_jenkins", description="Search Jenkins jobs")
register_function(tool_run_health_check, caller=oracle_admin, executor=user_proxy, name="health_check", description="Run DB Health Check")


# --- 6. UI Logic ---

def render_jenkins_execution_area():
    """Renders the Jenkins panel directly below the chat."""
    if not st.session_state["jenkins_matches"]:
        return

    # Use a container to visually separate it
    with st.container(border=True):
        st.subheader("🛠️ Job Execution Panel")
        
        col1, col2 = st.columns([1, 2])
        
        with col1:
            # FIX 2: Ensure options list is valid and key is unique
            selected_job = st.selectbox(
                "Select Job", 
                options=st.session_state["jenkins_matches"],
                key="active_job_selection_box"
            )

        # Get server (Fresh instance to avoid stale connection)
        server = get_jenkins_server()
        if not server:
            st.error("Jenkins server is unreachable.")
            return

        # FIX 3: Robust Error Handling for "Could not fetch job info"
        try:
            if not selected_job:
                st.info("No job selected.")
                return
            info = server.get_job_info(selected_job)
        except Exception as e:
            st.error(f"⚠️ Could not fetch details for job: '{selected_job}'.\nError: {e}")
            return

        # Extract Parameters
        params = []
        seen = set()
        definitions = info.get('property', []) + info.get('actions', [])
        for d in definitions:
            if 'parameterDefinitions' in d:
                for p in d['parameterDefinitions']:
                    if p['name'] not in seen:
                        params.append(p)
                        seen.add(p['name'])
        
        with col2:
            with st.form(key=f"jenkins_form_{selected_job}"):
                st.markdown(f"**Configure: {selected_job}**")
                
                form_data = {}
                for p in params:
                    label = p['name']
                    default = p.get('defaultParameterValue', {}).get('value', '')
                    choices = p.get('choices', [])
                    p_type = p.get('type', '')
                    
                    if 'Boolean' in p_type:
                        form_data[label] = st.checkbox(label, value=(str(default).lower()=='true'))
                    elif ('Choice' in p_type or 'Selection' in p_type) and choices:
                        form_data[label] = st.selectbox(label, choices)
                    else:
                        form_data[label] = st.text_input(label, value=str(default))
                
                submitted = st.form_submit_button("🚀 Run Job", type="primary")

        if submitted:
            try:
                final_params = {k: str(v).lower() if isinstance(v, bool) else v for k, v in form_data.items()}
                queue_id = server.build_job(selected_job, final_params)
                st.success(f"Job Triggered! Queue ID: {queue_id}")
                
                st.session_state["polling_active"] = True
                st.session_state["polling_job"] = selected_job
                st.session_state["polling_build"] = None 
                st.rerun()
            except Exception as e:
                st.error(f"Failed to trigger: {e}")

# --- 7. Main Layout ---

# Sidebar
with st.sidebar:
    st.header("🔌 Connection")
    # This will now update immediately after reruns
    st.info(f"Connected to: **{st.session_state['current_db']}**")
    
    st.markdown("---")
    st.header("📂 History")
    for r in st.session_state["awr_history"]:
        if os.path.exists(r['path']):
            with open(r['path'], "rb") as f:
                st.download_button(r['label'], f, file_name=r['path'], key=r['id'])
    
    if st.button("Clear History"):
        st.session_state["messages"] = []
        st.session_state["jenkins_matches"] = []
        st.rerun()

# Main Header
st.title("🤖 Oracle + Jenkins Agentic Console")

# Health Report
if st.session_state["health_report"]:
    with st.expander("🏥 Active Health Report", expanded=True):
        st.markdown(st.session_state["health_report"])
        if st.button("Close Report"):
            st.session_state["health_report"] = None
            st.rerun()

# Polling Logic
if st.session_state["polling_active"]:
    server = get_jenkins_server()
    job = st.session_state["polling_job"]
    if not st.session_state["polling_build"]:
        try:
            info = server.get_job_info(job)
            st.session_state["polling_build"] = info['nextBuildNumber'] - 1 
        except: pass
        time.sleep(1)
        st.rerun()
    
    try:
        b_info = server.get_build_info(job, st.session_state["polling_build"])
        res = b_info.get("result")
        if res:
            st.session_state["polling_active"] = False
            if res == "SUCCESS":
                st.success(f"✅ {job} #{st.session_state['polling_build']} Finished!")
            else:
                st.error(f"❌ {job} #{st.session_state['polling_build']} Failed!")
        else:
            st.info(f"🔨 {job} #{st.session_state['polling_build']} Running...")
            time.sleep(2)
            st.rerun()
    except:
        time.sleep(2)
        st.rerun()

# Chat History
for msg in st.session_state["messages"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# FIX 4: Render Jenkins Panel HERE, immediately after chat history, before input
render_jenkins_execution_area()

# User Input
user_input = st.chat_input("Ask me to run SQL, check health, or find Jenkins jobs...")

if user_input:
    st.session_state["messages"].append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)
    
    with st.chat_message("assistant"):
        with st.spinner("Agent processing..."):
            try:
                # Capture previous DB state for comparison
                prev_db = st.session_state["current_db"]
                
                ctx = f"Context: Connected to {st.session_state['current_db']}."
                oracle_admin.update_system_message(oracle_admin.system_message + "\n" + ctx)
                
                chat_res = user_proxy.initiate_chat(
                    oracle_admin, 
                    message=user_input, 
                    clear_history=False
                )
                
                # Extract clean response
                final_response = "Task Completed."
                if chat_res.chat_history:
                    for m in reversed(chat_res.chat_history):
                        if m.get('role') == 'user': continue
                        content = m.get('content', '')
                        if not content: continue
                        if "TERMINATE" in content:
                            content = content.replace("TERMINATE", "").strip()
                        if content:
                            final_response = content
                            break
                
                st.markdown(final_response)
                st.session_state["messages"].append({"role": "assistant", "content": final_response})
                
                # FIX 5: Immediate Sidebar Sync & UI Refresh
                # If DB changed OR Jenkins jobs found, force rerun
                if st.session_state["current_db"] != prev_db:
                    st.toast(f"Switched to {st.session_state['current_db']}")
                    time.sleep(0.5)
                    st.rerun()
                elif "Job Execution Panel" in final_response:
                    st.rerun()
                    
            except Exception as e:
                st.error(f"Agent Error: {e}")