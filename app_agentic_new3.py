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
from bs4 import BeautifulSoup

from autogen import AssistantAgent, UserProxyAgent, register_function

# --- Import Custom Oracle/Patch Modules ---
from oracle_runner_agentic_1 import (
    run_oracle_query, get_db_list, generate_awr_report, generate_ash_report,
    get_snapshots_for_time, run_full_health_check, get_snapshots_by_date_range, 
    analyze_awr_report, compare_awr_reports
)
from patch_forstreamlit import download_oracle_patch

# --- 1. Environment & Config ---
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")

if not openai_api_key:
    st.error("CRITICAL: OPENAI_API_KEY not found in .env")
    st.stop()

# Amdocs SSL / Environment Settings
os.environ["REQUESTS_CA_BUNDLE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"
os.environ["SSL_CERT_FILE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"

st.set_page_config(page_title="Oracle + Jenkins Agentic Console", layout="wide", page_icon="🤖")

# --- 2. "Fantastic" GUI CSS ---
st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .stChatMessage { border-radius: 15px; margin-bottom: 10px; border: 1px solid #30363d; }
    .report-card {
        background-color: #161b22;
        border-radius: 10px;
        padding: 15px;
        border-left: 5px solid #238636;
        margin: 10px 0;
    }
    .analysis-box {
        background-color: #0d1117;
        color: #c9d1d9;
        padding: 20px;
        border-radius: 8px;
        border: 1px solid #30363d;
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        line-height: 1.6;
    }
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; transition: 0.3s; }
    .stButton>button:hover { border-color: #238636; color: #238636; }
    </style>
""", unsafe_allow_html=True)

# --- 3. Session State Initialization ---
if "dbs" not in st.session_state: st.session_state["dbs"] = get_db_list()
if "current_db" not in st.session_state: st.session_state["current_db"] = st.session_state["dbs"][0] if st.session_state["dbs"] else "DEFAULT"
if "messages" not in st.session_state: st.session_state["messages"] = []
if "awr_history" not in st.session_state: st.session_state["awr_history"] = []
if "health_report" not in st.session_state: st.session_state["health_report"] = None
if "awr_compare" not in st.session_state: st.session_state["awr_compare"] = None
if "artifacts" not in st.session_state: st.session_state["artifacts"] = {}
if "processing" not in st.session_state: st.session_state["processing"] =None
# Jenkins State
if "job_map" not in st.session_state: st.session_state["job_map"] = []
if "jenkins_matches" not in st.session_state: st.session_state["jenkins_matches"] = []
if "polling_active" not in st.session_state: st.session_state["polling_active"] = False
if "polling_job" not in st.session_state: st.session_state["polling_job"] = None
if "polling_queue_id" not in st.session_state: st.session_state["polling_queue_id"] = None
if "polling_build" not in st.session_state: st.session_state["polling_build"] = None

# --- 4. Tool Definitions ---
@st.cache_resource
def get_jenkins_server():
    try:
        return jenkins.Jenkins("http://localhost:9020", username="dba", password="113bb934053435f19fa62d94f8c79a108c")
    except Exception as e:
        print(f"Jenkins Connection Error: {e}")
        return None

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

@st.cache_data(show_spinner="Fetching Jenkins Jobs into cache...")
def fetch_all_job_details_robust():
    _client = get_jenkins_server()
    if _client is None: return []
    out = []
    job_names = fetch_jobs_recursive(_client)
    
    for full in job_names:
        try:
            info = _client.get_job_info(full)
        except: continue
            
        desc = info.get("description", "") or "(no description)"
        params = []
        seen = set()
        
        all_sections = (
            info.get("actions", []) +
            info.get("properties", []) +
            info.get("property", [])
        )
        for section in all_sections:
            if "parameterDefinitions" in section:
                for p in section["parameterDefinitions"]:
                    p_name = p["name"]
                    if p_name in seen: continue
                    seen.add(p_name)
                    choices = p.get("choices", [])
                    if not choices: choices = p.get("allValue", p.get("values", []))
                    
                    params.append({
                        "name": p["name"],
                        "type": p.get("_class", "") or p.get("type", ""),
                        "default": p.get("defaultParameterValue", {}).get("value", ""),
                        "choices": choices
                    })
        out.append({"name": full, "description": desc, "parameters": params})
    return out

if not st.session_state["job_map"]:
    st.session_state["job_map"] = fetch_all_job_details_robust()


def tool_change_database(target_name: str) -> str:
    target_name = target_name.upper().strip()
    if target_name in st.session_state["dbs"]:
        st.session_state["current_db"] = target_name
        return f"SUCCESS: Context is now {target_name}."
    return f"FAILURE: DB {target_name} not found."

def tool_run_sql(sql_query: str) -> str:
    db = st.session_state["current_db"]
    try:
        sql_query = sql_query.strip().rstrip(";")
        result = run_oracle_query(sql_query, db)
        if isinstance(result, list):
            if not result: return "Query executed successfully. 0 rows returned."
            df = pd.DataFrame(result)
            return f"**SQL Result ({len(df)} rows):**\n{df.to_markdown(index=False)}"
        return str(result)
    except Exception as e: return f"Error: {str(e)}"
def tool_run_health_check() -> str:
    res = run_full_health_check(st.session_state["current_db"])
    if res["status"] == "ok":
        report_id = str(uuid.uuid4())
        st.session_state["artifacts"][report_id] = {
            "type": "HEALTH", 
            "content": res["report"],
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }
        # st.session_state["health_report"] = res["report"]
        return f"Health Check Completed successfully. ::ARTIFACT_HEALTH:{report_id}::"
    return f"Health Check Failed: {res.get('message')}"

def tool_performance_report(hours_back: float = 2.0) -> str:
    db = st.session_state["current_db"]
    try:
        if hours_back < 1.0:
            res = generate_ash_report(int(hours_back * 60), db)
            rtype = "ASH"
        else:
            s_snap, e_snap = get_snapshots_for_time(hours_back, db)
            res = generate_awr_report(s_snap, e_snap, db)
            rtype = "AWR"
        
        if res and res.get("status") == "ok":
            rpt_id = len(st.session_state["awr_history"]) + 1
            entry = {
                "id": rpt_id,
                "label": f"{rtype} - Last {hours_back}h ({db})",
                "type": rtype,
                "report_html": res["report"],
                "filename": res["filename"]
            }
            st.session_state["awr_history"].append(entry)
            return f"SUCCESS: Generated {rtype} Report. ID: {rpt_id} | File: {res['filename']}"
        return "FAILURE: Snapshot range not found."
    except Exception as e: return f"Error: {str(e)}"
def tool_search_jenkins_jobs(search_term: str) -> str:
    query = search_term.lower()
    matches = []
    for j in st.session_state["job_map"]:
        if query in j["name"].lower():
            matches.append(j["name"])
    
    if not matches: return f"No jobs found matching '{search_term}'."
    search_id = str(uuid.uuid4())
    st.session_state["artifacts"][search_id] = {
        "type": "JENKINS_SELECT",
        "matches": matches[:15], # Limit to top 15
        "timestamp": datetime.now().strftime("%H:%M")
    }
    return f"I found {len(matches)} jobs. Please select one below. ::ARTIFACT_JENKINS:{search_id}::"
def tool_compare_reports(id1: int, id2: int) -> str:
    r1 = next((r for r in st.session_state["awr_history"] if r["id"] == id1), None)
    r2 = next((r for r in st.session_state["awr_history"] if r["id"] == id2), None)
    if not r1 or not r2: return "Error: Report IDs not found."
    
    res = compare_awr_reports(r1["report_html"], r2["report_html"], r1["label"], r2["label"])
    comp_id = str(uuid.uuid4())
    st.session_state["artifacts"][comp_id] = {"type": "COMPARE", "content": res.get("comparison", "No data"), "title": f"Comparison: {id1} vs {id2}"}
    return f"Comparison complete. ::ARTIFACT_COMPARE:{comp_id}::"
def tool_analyze_report_content(user_question: str) -> str:
    """Analyzes the most recently generated report in history."""
    if not st.session_state["awr_history"]:
        return "No report found in history to analyze. Please generate one first."
    
    last_report = st.session_state["awr_history"][-1]
    
    # Use the parsing logic from analyze_interactive_report
    from bs4 import BeautifulSoup
    try:
        soup = BeautifulSoup(last_report["report_html"], 'html.parser')
        text = soup.get_text()[:120000] # Truncate for token limits
        
        # We invoke the AWR Analyzer agent directly here as a sub-call
        prompt = f"""
        User Question: {user_question}
        
        Context (From {last_report['type']} Report):
        {text}
        
        Answer the user's question concisely based on the data.
        """
        
        llm_config = {"config_list": [{"model": "gpt-4o-mini", "api_key": openai_api_key}], "temperature": 0}
        analyzer = AssistantAgent("analyzer", llm_config=llm_config, system_message="You are an Oracle Expert.")
        
        reply = analyzer.generate_reply([{"role": "user", "content": prompt}])
        return f"**Analysis of {last_report['label']}:**\n\n{reply}"
        
    except Exception as e:
        return f"Analysis failed: {e}"
def tool_download_patch_wrapper(patch_description: str) -> str:
    """
    Downloads Oracle patches based on natural language description.
    e.g. "Download 19.23 RU", "Get OJVM patch", "Download GI patch"
    """
    res = download_oracle_patch(patch_description)
    
    # Generate Artifact for GUI
    patch_id = str(uuid.uuid4())
    st.session_state["artifacts"][patch_id] = {
        "type": "PATCH_RESULT",
        "content": res.get("message", "No details returned."),
        "status": res.get("status", "info"),
        "timestamp": datetime.now().strftime("%H:%M")
    }
    
    if res["status"] == "success":
        return f"Patch download initiated successfully. ::ARTIFACT_PATCH:{patch_id}::"
    else:
        return f"Patch download failed or encountered an issue. ::ARTIFACT_PATCH:{patch_id}::"

# --- 5. Agent Setup (DOCKER FIX INCLUDED) ---
def initialize_agents():
    if "oracle_admin" not in st.session_state:
        config = {"config_list": [{"model": "gpt-4o-mini", "api_key": openai_api_key}], "temperature": 0}
        
        st.session_state.oracle_admin = AssistantAgent(
            name="Oracle_Admin",
            llm_config=config,
            system_message="""
You are an Oracle DBA and Jenkins Admin assistant.
- **Database:** Use `switch_db` to change target.
- **Query against the DB:** Use 'tool_run_sql' to run oracle queries , but if you face any error , rething the query is required analyze the failed thing and re-write the query.
- **Performance:** Use `generate_performance_report`. 
  - If user says "last 3 hours", use `hours_back=3`.
  - If user says "10am to 12pm", use `start_time` and `end_time` (YYYY-MM-DD HH:MM:SS).
- **Analysis:** If user asks questions about the report ("why is cpu high?", "analyze it"), use `analyze_report`.
- **Health:** Use `health_check`.
- **Jenkins:** Use `search_jenkins`.
- **Patches:** Use `download_patch` when user asks to download Oracle patches (e.g., RU, OJVM, GI).
Reply "TERMINATE" when done.
"""
        )
        
        st.session_state.user_proxy = UserProxyAgent(
            name="User_Proxy",
            human_input_mode="NEVER",
            code_execution_config={"use_docker": False}, # <--- DOCKER FIX
            is_termination_msg=lambda x: "TERMINATE" in str(x.get("content", ""))
        )
        
        register_function(tool_change_database, caller=st.session_state.oracle_admin, executor=st.session_state.user_proxy, name="switch_db", description="Switch DB")
        register_function(tool_run_sql, caller=st.session_state.oracle_admin, executor=st.session_state.user_proxy, name="run_sql", description="Run SQL")
        register_function(tool_search_jenkins_jobs, caller=st.session_state.oracle_admin, executor=st.session_state.user_proxy, name="search_jenkins", description="Search Jobs")
        register_function(tool_run_health_check, caller=st.session_state.oracle_admin, executor=st.session_state.user_proxy, name="health_check", description="Run Health Check")
        register_function(tool_performance_report, caller=st.session_state.oracle_admin, executor=st.session_state.user_proxy, name="generate_performance_report", description="Generates AWR/ASH")
        register_function(tool_analyze_report_content, caller=st.session_state.oracle_admin, executor=st.session_state.user_proxy, name="analyze_report", description="Analyze last report")
        register_function(tool_download_patch_wrapper, caller=st.session_state.oracle_admin, executor=st.session_state.user_proxy, name="download_patch", description="Download Oracle Patches based on description")
       

initialize_agents()

# --- 6. UI Logic ---

# Sidebar
with st.sidebar:
    st.title("⚙️ Controls")
    db_choice = st.selectbox("Current Database", st.session_state["dbs"], index=st.session_state["dbs"].index(st.session_state["current_db"]))
    if db_choice != st.session_state["current_db"]:
        st.session_state["current_db"] = db_choice
        st.rerun()
    
    st.divider()
    if st.button("🗑️ Clear Chat History"):
        st.session_state["messages"] = []
        st.rerun()

# Main Chat Display
for i, m in enumerate(st.session_state["messages"]):
    with st.chat_message(m["role"]):
        # Artifact check
        art_match = re.search(r"::ARTIFACT_(.*?):(.*?)::", m["content"])
        clean_text = m["content"].replace(art_match.group(0), "") if art_match else m["content"]
        st.markdown(clean_text)

        if art_match:
            art_type, art_id = art_match.group(1), art_match.group(2)
            art = st.session_state["artifacts"].get(art_id)
            if art:
                with st.expander(f"📊 {art.get('title', 'Result Details')}", expanded=True):
                    st.markdown(f"<div class='analysis-box'>{art['content']}</div>", unsafe_allow_html=True)

        # Performance Buttons (Download & Analyze)
        if "File:" in m["content"] and "SUCCESS" in m["content"]:
            fname = re.search(r"File:\s*([\w\.-]+)", m["content"]).group(1)
            rpt = next((r for r in st.session_state["awr_history"] if r["filename"] == fname), None)
            if rpt:
                col1, col2 = st.columns(2)
                col1.download_button("📥 Download Report", rpt["report_html"], file_name=fname, key=f"dl_{i}")
                if col2.button("🔍 AI Deep Analysis", key=f"an_{i}"):
                    with st.status("Analyzing Report Content...", expanded=True):
                        # Show analysis right here in the flow
                        analysis = analyze_awr_report(rpt["report_html"])
                        st.markdown(f"<div class='analysis-box'><b>AWR Insights:</b><br>{analysis}</div>", unsafe_allow_html=True)

# Input Handler
if user_input := st.chat_input("Ex: 'Run a health check', 'Show SQL for active sessions', 'Compare reports 1 and 2'"):
    # Show user message immediately
    st.session_state["messages"].append({"role": "user", "content": user_input})
    st.session_state["processing"] = True
    st.rerun()

# Agent Execution (Triggered after Rerun)
if st.session_state["processing"]:
    st.session_state["processing"] = False
    with st.chat_message("assistant"):
        with st.status("Agent is working...", expanded=True) as status:
            # Inject context manually for "Memory"
            history = "\n".join([f"{msg['role']}: {msg['content'][:200]}" for msg in st.session_state["messages"][-3:]])
            prompt = f"Session History:\n{history}\n\nUser Question: {st.session_state['messages'][-1]['content']}"
            
            res = st.session_state.user_proxy.initiate_chat(st.session_state.oracle_admin, message=prompt, clear_history=False)
            status.update(label="Analysis Complete", state="complete")

        # Extract final answer
        ans = "Done."
        for m in reversed(res.chat_history):
            if m.get("content") and m.get("role") != "user":
                ans = m["content"].replace("TERMINATE", "").strip()
                break
        
        st.session_state["messages"].append({"role": "assistant", "content": ans})
        st.rerun()