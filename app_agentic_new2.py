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
from oracle_runner_agentic_1 import (
    run_oracle_query, get_db_list, generate_awr_report, generate_ash_report,
    get_snapshots_for_time, run_full_health_check, get_snapshots_by_date_range, 
     analyze_awr_report, compare_awr_reports
)
from patch_forstreamlit import download_oracle_patch

# --- 1. Configuration & Setup ---
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")

if not openai_api_key:
    st.error("CRITICAL: OPENAI_API_KEY not found in .env")
    st.stop()

# SSL Config
os.environ["REQUESTS_CA_BUNDLE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"
os.environ["SSL_CERT_FILE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"

st.set_page_config(page_title="Oracle + Jenkins Agentic Console", layout="wide", page_icon="🤖")

# --- 2. Session State Initialization ---
if "dbs" not in st.session_state: st.session_state["dbs"] = get_db_list()
if "current_db" not in st.session_state: st.session_state["current_db"] = st.session_state["dbs"][0] if st.session_state["dbs"] else "DEFAULT"
if "messages" not in st.session_state: st.session_state["messages"] = []
if "awr_history" not in st.session_state: st.session_state["awr_history"] = []
if "health_report" not in st.session_state: st.session_state["health_report"] = None
if "awr_compare" not in st.session_state: st.session_state["awr_compare"] = None
if "artifacts" not in st.session_state: st.session_state["artifacts"] = {}
# Jenkins State
if "job_map" not in st.session_state: st.session_state["job_map"] = []
if "jenkins_matches" not in st.session_state: st.session_state["jenkins_matches"] = []
if "polling_active" not in st.session_state: st.session_state["polling_active"] = False
if "polling_job" not in st.session_state: st.session_state["polling_job"] = None
if "polling_queue_id" not in st.session_state: st.session_state["polling_queue_id"] = None
if "polling_build" not in st.session_state: st.session_state["polling_build"] = None

# --- 3. Helper Functions ---

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

# --- 4. Tool Definitions (Agentic) ---

def tool_change_database(target_name: str) -> str:
    available = st.session_state["dbs"]
    target_name = target_name.upper().strip()
    if target_name in available:
        st.session_state["current_db"] = target_name
        return f"SUCCESS: Switched to '{target_name}'."
    matches = get_close_matches(target_name, available, n=1, cutoff=0.4)
    if matches:
        st.session_state["current_db"] = matches[0]
        return f"SUCCESS: Switched to closest match '{matches[0]}'."
    return f"FAILURE: DB '{target_name}' not found."

def tool_run_sql(sql_query: str) -> str:
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
def tool_performance_report(start_time: str = None, end_time: str = None, hours_back: float = None) -> str:
    """
    Generates AWR/ASH reports and UPDATES session state so GUI can render download buttons.
    """
    db = st.session_state["current_db"]
    res = None
    report_type = "AWR"
    period_str = ""

    # Logic: Choose ASH vs AWR
    try:
        # A. Relative Time
        if hours_back is not None:
            period_str = f"Last {hours_back} Hours"
            if hours_back < 2.0:
                report_type = "ASH"
                res = generate_ash_report(int(hours_back * 60), db)
            else:
                s_snap, e_snap = get_snapshots_for_time(hours_back, db)
                if not s_snap: return f"FAILURE: No snapshots found for last {hours_back} hours on {db}. Check if DB is gathering stats."
                res = generate_awr_report(s_snap, e_snap, db)

        # B. Specific Range
        elif start_time and end_time:
            period_str = f"{start_time} to {end_time}"
            fmt = "%Y-%m-%d %H:%M:%S"
            t1 = datetime.strptime(start_time, fmt)
            t2 = datetime.strptime(end_time, fmt)
            duration = (t2 - t1).total_seconds() / 60.0
            
            if duration < 30.0:
                report_type = "ASH"
                res = generate_ash_report_specific_range(start_time, end_time, db)
            else:
                snaps = get_snapshots_by_date_range(start_time, end_time, db)
                if not snaps: return f"FAILURE: No snapshots found between {start_time} and {end_time}."
                res = generate_awr_report(snaps[0]['snap_id'], snaps[1]['snap_id'], db)

        else:
            return "FAILURE: Provide hours_back OR start_time/end_time."

        # Handle Result
        if res and res.get("status") == "ok":
            # *** CRITICAL: Update Session State for GUI ***
            entry = {
                "id": len(st.session_state["awr_history"]) + 1,
                "ts": datetime.now(),
                "label": f"{report_type} - {period_str}",
                "type": report_type,
                "report_html": res["report"],
                "filename": res["filename"],
                "period_str": period_str,
                "db": db
            }
            st.session_state["awr_history"].append(entry)
            
            return f"SUCCESS: Generated {report_type} Report ({period_str}). File: {res['filename']}. buttons_rendered_below"
        
        return f"FAILURE: {res.get('message')}"

    except Exception as e:
        return f"ERROR in tool: {str(e)}"

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
def analyze_jenkins_failure(console_log):
    """
    Sends the last chunk of the console log to the AI to determine root cause.
    """
    # 1. Truncate Log (AI token limits) - Focus on the end where errors usually are
    max_chars = 12000 
    truncated_log = console_log[-max_chars:] if len(console_log) > max_chars else console_log

    # 2. Define the Agent for Analysis
    analyzer = AssistantAgent(
        name="Jenkins_Debugger",
        llm_config=llm_config, # Reuse your existing config
        system_message="""
        You are a DevOps Jenkins Expert. 
        Analyze the provided console log failure.
        Return ONLY a JSON object (no markdown, no other text) with these 3 keys:
        {
            "root_cause": "Brief explanation of what went wrong",
            "failed_line": "The specific line or command that caused the error",
            "suggestion": "Concrete fix (e.g., 'Update parameter X', 'Check disk space')"
        }
        """
    )
    prompt = f"""
You are a Jenkins CI failure analysis expert.

Analyze the following Jenkins console log and identify:

1. The most likely root cause of the failure.
2. What exact line or command caused the failure.
3. What fix or action the user should take.
4. Keep the output short, clear, and actionable.

Console Log:
\"\"\"{truncated_log}\"\"\"

Return a JSON object:
{{"root_cause":"...","failed_line":"...","suggestion":"..."}}
"""
    try:
        reply = analyzer.generate_reply([
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

   
def monitor_jenkins_build(server, queue_id):
    """
    Blocks (waits) until the job finishes, updating the UI via a status container.
    Returns the build_info dict.
    """
    status_box = st.status("Job Triggered... Waiting for Queue...", expanded=True)
    
    try:
        # 1. Wait for Queue to assign a Build Number
        build_number = None
        job_name = None
        
        max_retries = 30 # 30 seconds wait for queue
        for _ in range(max_retries):
            try:
                q_item = server.get_queue_item(queue_id)
                if "executable" in q_item:
                    build_number = q_item["executable"]["number"]
                    job_name = st.session_state["polling_job"] # Logic assumes single job context
                    break
            except:
                pass
            time.sleep(1)
        
        if not build_number:
            status_box.update(label=" Timed out waiting for Queue", state="error")
            return None

        status_box.write(f" Build Started: #{build_number}")
        
        # 2. Poll Build Status
        while True:
            build_info = server.get_build_info(job_name, build_number)
            res = build_info.get("result")
            
            if res: # SUCCESS, FAILURE, ABORTED, UNSTABLE
                if res == "SUCCESS":
                    status_box.update(label=f" Build #{build_number} SUCCESS", state="complete", expanded=False)
                elif res == "ABORTED":
                    status_box.update(label=f" Build #{build_number} ABORTED", state="error")
                else:
                    status_box.update(label=f" Build #{build_number} FAILED", state="error")
                return build_info
            
            status_box.write("⚙️ Building... (Monitoring status)")
            time.sleep(2) # Poll every 2 seconds
            
    except Exception as e:
        status_box.write(f"Error polling: {e}")
        status_box.update(state="error")
        return None
# --- 5. Agent Setup ---

llm_config = {
    "config_list": [{"model": "gpt-4o-mini", "api_key": openai_api_key}],
    "temperature": 0,
}

oracle_admin = AssistantAgent(
    name="Oracle_Admin",
    llm_config=llm_config,
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

Reply "TERMINATE" when done.
"""
)

user_proxy = UserProxyAgent(
    name="User_Proxy",
    human_input_mode="NEVER",
    max_consecutive_auto_reply=10,
    code_execution_config=False,
    is_termination_msg=lambda x: "TERMINATE" in str(x.get("content", ""))
)

# Register Tools
register_function(tool_change_database, caller=oracle_admin, executor=user_proxy, name="switch_db", description="Switch DB")
register_function(tool_run_sql, caller=oracle_admin, executor=user_proxy, name="run_sql", description="Run SQL")
register_function(tool_search_jenkins_jobs, caller=oracle_admin, executor=user_proxy, name="search_jenkins", description="Search Jobs")
register_function(tool_run_health_check, caller=oracle_admin, executor=user_proxy, name="health_check", description="Run Health Check")
register_function(tool_performance_report, caller=oracle_admin, executor=user_proxy, name="generate_performance_report", description="Generates AWR/ASH")
register_function(tool_analyze_report_content, caller=oracle_admin, executor=user_proxy, name="analyze_report", description="Analyze last report")

# --- 6. UI Implementation ---
def handle_agent_execution(prompt):
    """Handles the full flow: UI updates -> Agent run -> Response -> Rerun"""
    
    # 1. Add User Message to History
    st.session_state["messages"].append({"role": "user", "content": prompt})
    
    # 2. Update System Message with Context
    ctx = f"Context: Connected to {st.session_state['current_db']}. Time: {datetime.now()}."
    oracle_admin.update_system_message(oracle_admin.system_message + "\n" + ctx)
    
    # 3. Run Agent
    user_messages = [m for m in st.session_state["messages"] if m["role"] == "user"]
    last_user_message = user_messages[-1]["content"] if user_messages else None
    if last_user_message:
     st.write("Last user message:", last_user_message)


    with st.spinner("Agent processing..."):
        try:
            chat_res = user_proxy.initiate_chat(
                oracle_admin, 
                message=prompt, 
                clear_history=False
            )
            # final_response = None
            # # 4. Extract Final Response
            final_response = "Task Completed."
            for m in reversed(chat_res.chat_history):
                if m.get('role') == 'user': continue
                c = m.get('content','')
                if c and "TERMINATE" not in c:
                    final_response = c
                    break
                if "TERMINATE" in c:
                    final_response = c.replace("TERMINATE", "").strip()
                    break
            
            if not final_response: final_response = "Done."

            # 5. Add Assistant Message
            st.session_state["messages"].append({"role": "assistant", "content": final_response})
            
            # 6. Force Rerun (To update buttons/charts/state)
            st.rerun()
            
        except Exception as e:
            st.error(f"Agent Error: {e}")
# SIDEBAR
with st.sidebar:
    st.header("🔌 Connection")
    st.info(f"DB: **{st.session_state['current_db']}**")
    
    st.markdown("---")
    st.subheader("🛠️ Tools")
    
    # Health Check Button
    if st.button("🏥 Full Health Check", type="primary"):
       handle_agent_execution("Run a full health check on the database.")

    # Compare AWR Section
    st.markdown("---")
    st.subheader("📊 Compare AWR")
    if len(st.session_state["awr_history"]) >= 2:
        # Filter only AWRs
        awrs = [h for h in st.session_state["awr_history"] if h["type"] == "AWR"]
        if len(awrs) >= 2:
            base = st.selectbox("Baseline", awrs, format_func=lambda x: f"{x['id']}: {x['label']}")
            curr = st.selectbox("Current", awrs, format_func=lambda x: f"{x['id']}: {x['label']}")
            
            if st.button("Compare Reports",key="btn_compare_start"):
                status_box = st.status("Processing Comparison...", expanded=True)
                try:
                    status_box.write("1. Parsing HTML files...")
                # with st.spinner("Comparing..."):
                    res = compare_awr_reports(base["report_html"], curr["report_html"], base["label"], curr["label"])
                    if res["status"] == "ok":
                        status_box.write("2. AI Analysis complete.")
                        comp_id = str(uuid.uuid4())
                        st.session_state["artifacts"][comp_id] = {
                            "type": "COMPARE",
                            "content": res["comparison"],
                            "title": f"Comparison: {base['id']} vs {curr['id']}"
                        }
                        st.session_state["messages"].append({
                            "role": "assistant", 
                            "content": f"Comparison generated for **{base['label']}** vs **{curr['label']}**. ::ARTIFACT_COMPARE:{comp_id}::"
                        })
                        status_box.update(label="✅ Comparison Complete!", state="complete", expanded=False)
                        # st.session_state["awr_compare"] = res["comparison"]
                        # st.success("Comparison Generated! Check the Main Panel.")
                        st.rerun()
                    else:
                        status_box.update(label="❌ Analysis Failed", state="error")
                        st.error(res["message"])
                except Exception as e:
                    status_box.update(label="❌ System Error", state="error")
                    st.error(f"UI Error: {str(e)}")
        else:
            st.caption("Need at least 2 AWR reports generated.")
    else:
        st.caption("Generate reports to enable comparison.")

    if st.button("Clear History"):
        st.session_state["messages"] = []
        st.session_state["awr_history"] = []
        st.rerun()

st.title("Oracle + Jenkins Agentic Console")

# Display Health Report if Active
if st.session_state["health_report"]:
    with st.expander("🏥 Database Health Report", expanded=True):
        st.markdown(st.session_state["health_report"])
        if st.button("Close Report"):
            st.session_state["health_report"] = None
            st.rerun()

# Display Comparison if Active
# if st.session_state["awr_compare"]:
#     st.markdown("---")
#     with st.expander("📊 AWR Comparison Analysis", expanded=True):
#         st.markdown(st.session_state["awr_compare"])
#         if st.button("Close Comparison"):
#             st.session_state["awr_compare"] = None
#             st.rerun()

# --- CHAT RENDERING ---
for i, msg in enumerate(st.session_state["messages"]):
    with st.chat_message(msg["role"]):
        content = msg["content"]
        if "::ARTIFACT_JENKINS:" in content:
            match = re.search(r"::ARTIFACT_JENKINS:(.*?)::", content)
            display_text = content.replace(match.group(0), "") if match else content
            st.markdown(display_text)
            
            if match:
                art_id = match.group(1)
                artifact = st.session_state["artifacts"].get(art_id)
                
                if artifact and artifact["type"] == "JENKINS_SELECT":
                    st.markdown("---")
                    col_left, col_right = st.columns([1, 2.5], gap="large")
                    with col_left:
                        st.caption(f"🔍 Job Search Results ({artifact['timestamp']})")
                        
                        # 1. Job Selection Dropdown
                        # unique key using art_id ensures it doesn't conflict
                        selected_job = st.selectbox(
                            "Select a Job to Run:", 
                            artifact["matches"], 
                            key=f"job_sel_{art_id}"
                        )
                    
                    # 2. Get Job Details
                        job_details = next((j for j in st.session_state["job_map"] if j["name"] == selected_job), None)
                        if job_details:
                            st.info(job_details.get('description', 'No description'), icon="ℹ️")
                    with col_right:
                     if job_details:
                        with st.container(border=True):
                            st.write(f"**Configure: {selected_job}**")
                            
                            # 3. Dynamic Form Generation
                            form_params = {}
                            # Use a container, not a st.form, to allow real-time interactions if needed
                            # But st.form is safer for loop rendering
                            with st.form(key=f"form_{art_id}"):
                                if job_details.get("parameters"):
                                    for p in job_details["parameters"]:
                                        p_name = p["name"]
                                        if "Boolean" in p["type"]:
                                            form_params[p_name] = st.checkbox(p_name, value=(str(p["default"]).lower()=="true"))
                                        elif "Choice" in p["type"]:
                                             form_params[p_name] = st.selectbox(p_name, p["choices"])
                                        else:
                                            form_params[p_name] = st.text_input(p_name, value=str(p["default"]))
                                else:
                                        st.caption("No parameters required.")
                                st.write("")
                                run_submitted = st.form_submit_button("Run Job ",type="primary")
                            
                            # 4. Execution Logic (Inline)
                            if run_submitted:
                                server = get_jenkins_server()
                                if server:
                                    final_params = {k: str(v).lower() if isinstance(v, bool) else v for k,v in form_params.items()}
                                    st.session_state["polling_job"] = selected_job
                                    
                                    try:
                                        # Trigger
                                        qid = server.build_job(selected_job, final_params)
                                        
                                        # Monitor (Block UI inside the loop - acceptable for agent flow)
                                        final_build = monitor_jenkins_build(server, qid)
                                        
                                        if final_build:
                                            res_status = final_build.get('result')
                                            b_num = final_build.get('number')
                                            
                                            if res_status == "SUCCESS":
                                                st.balloons()
                                                st.success(f"Job #{b_num} Succeeded!")
                                            elif res_status == "FAILURE":
                                                st.error(f"Job #{b_num} Failed.") 
                                                console = server.get_build_console_output(selected_job, b_num)
                                                st.code(console[-500:])
                                                # Trigger AI Analysis
                                                with st.spinner("Analyzing Failure..."):
                                                    console = server.get_build_console_output(selected_job, b_num)
                                                    analysis = analyze_jenkins_failure(console)
                                                    st.markdown("### Root Cause Analysis")
                                                    st.error(analysis.get("root_cause"))
                                                    st.code(analysis.get("failed_line"))
                                                    st.info(analysis.get("suggestion"))
                                    except Exception as e:
                                        st.error(f"Error: {e}")
        if "::ARTIFACT_HEALTH:" in content:
            # Extract ID
            match = re.search(r"::ARTIFACT_HEALTH:(.*?)::", content)
            display_text = content.replace(match.group(0), "") if match else content
            st.markdown(display_text)
            
            if match:
                art_id = match.group(1)
                artifact = st.session_state["artifacts"].get(art_id)
                if artifact:
                    with st.expander(f"🏥 Database Health Report ({artifact['timestamp']})", expanded=True):
                        st.markdown(artifact["content"])
                        # Close Button with UNIQUE KEY using index 'i'
                        if st.button("Close Report", key=f"close_health_{i}"):
                            # Remove from text to "hide" it permanently or just collapse
                            # Here we simply don't render it next time if we removed the artifact, 
                            # but simpler is just letting the user collapse the expander.
                            # If you strictly want a close button to remove it:
                            del st.session_state["artifacts"][art_id]
                            st.rerun()
        elif "::ARTIFACT_COMPARE:" in content:
            match = re.search(r"::ARTIFACT_COMPARE:(.*?)::", content)
            display_text = content.replace(match.group(0), "") if match else content
            st.markdown(display_text)
            
            if match:
                art_id = match.group(1)
                artifact = st.session_state["artifacts"].get(art_id)
                if artifact:
                    with st.expander(f"📊 {artifact['title']}", expanded=True):
                        st.markdown(artifact["content"])
                        if st.button("Close Comparison", key=f"close_comp_{i}"):
                            del st.session_state["artifacts"][art_id]
                            st.rerun()
        else:
            st.markdown(content)
        # Check if this message was a Success Response for a Report
        # Logic: If content says "SUCCESS" and "Generated", find the latest report
        if msg["role"] == "assistant" and "SUCCESS" in content and "Generated" in content and "filename" in st.session_state.get("awr_history", [{}])[-1]:
            # Find the report that matches this context (usually the last one)
            # For simplicity in agentic flow, we assume the latest one corresponds to the latest success message
            if st.session_state["awr_history"]:
                rpt = st.session_state["awr_history"][-1]
                if i == len(st.session_state["messages"]) - 1:
                # Render Buttons
                    col1, col2 = st.columns([1, 2])
                    with col1:
                        st.download_button(
                            label="⬇️ Download HTML",
                            data=rpt["report_html"],
                            file_name=rpt["filename"],
                            mime="text/html",
                            key=f"dl_btn_{i}"
                        )
                    with col2:
                        if st.button("Analyze with AI", key=f"an_btn_{i}"):
                            # Append a user message to trigger the analyzer tool
                            handle_agent_execution("Analyze the report generated above. Highlight top wait events and SQLs.")
# if st.session_state["awr_compare"]:
#             st.markdown("---")
#             with st.expander("📊 AWR Comparison Analysis", expanded=True):
#                 st.markdown(st.session_state["awr_compare"])
#                 if st.button("Close Comparison"):
#                     st.session_state["awr_compare"] = None
#                     st.rerun()
# if st.session_state["health_report"]:
#     with st.expander("🏥 Database Health Report", expanded=True):
#         st.markdown(st.session_state["health_report"])
#         if st.button("Close Report"):
#             st.session_state["health_report"] = None
#             st.rerun()

# --- JENKINS PANEL ---
# if st.session_state["jenkins_matches"]:
#     st.markdown("---")
#     st.subheader("🛠️ Job Execution Panel")
    
#     # ... [Keep existing Jenkins Panel Logic from previous snippet] ...
#     # (Included briefly for completeness)
#     col1, col2 = st.columns([1, 2])
#     options = st.session_state["jenkins_matches"]
#     selected_job_name = col1.selectbox("Select Job", options)
    
#     job_details = next((j for j in st.session_state["job_map"] if j["name"] == selected_job_name), None)
    
#     if job_details:
#         col1.info(f"{job_details.get('description')}")
#         form_params = {}
#         with col2:
#             with st.form(key=f"form_{selected_job_name}"):
#                 if job_details.get("parameters"):
#                     for p in job_details["parameters"]:
#                         p_name = p["name"]
#                         if "Boolean" in p["type"]:
#                             form_params[p_name] = st.checkbox(p_name, value=(str(p["default"]).lower()=="true"))
#                         elif "Choice" in p["type"]:
#                              form_params[p_name] = st.selectbox(p_name, p["choices"])
#                         else:
#                             form_params[p_name] = st.text_input(p_name, value=str(p["default"]))
#                 submitted=st.form_submit_button("Run Job")  
#                 if submitted:
#                     server = get_jenkins_server()
#                     if server:
#                         # 1. Trigger Job
#                         final_params = {k: str(v).lower() if isinstance(v, bool) else v for k,v in form_params.items()}
#                         st.session_state["polling_job"] = selected_job_name
                        
#                         try:
#                             qid = server.build_job(selected_job_name, final_params)
                            
#                             # 2. Call the Monitoring Helper (This blocks UI until done)
#                             final_build = monitor_jenkins_build(server, qid)
                            
#                             # 3. Handle Result
#                             if final_build:
#                                 result = final_build.get('result')
#                                 build_num = final_build.get('number')
                                
#                                 if result == "SUCCESS":
#                                     st.balloons()
#                                     st.success(f"Job {selected_job_name} #{build_num} completed successfully.")
                                
#                                 elif result == "FAILURE":
#                                     st.error(f"Job {selected_job_name} #{build_num} FAILED.")
                                    
#                                     # --- AI FAILURE ANALYSIS ---
#                                     with st.spinner("🤖 AI is analyzing the console output for root cause..."):
#                                         console_out = server.get_build_console_output(selected_job_name, build_num)
#                                         ai_help = analyze_jenkins_failure(console_out)
                                    
#                                     if ai_help:
#                                         # Use an expander or the artifact system to show analysis
#                                         fail_id = str(uuid.uuid4())
                                        
#                                         # (Optional) Save to artifacts if you want it persistent
#                                         st.session_state["artifacts"][fail_id] = {
#                                             "type": "JENKINS_FAIL",
#                                             "content": f"**Root Cause:** {ai_help.get('root_cause')}\n\n**Fix:** {ai_help.get('suggestion')}"
#                                         }

#                                         # Render immediate results
#                                         st.subheader("🤖 AI-Detected Root Cause")
#                                         st.error(ai_help.get("root_cause", "Unknown"))
                                        
#                                         st.subheader("🔍 Failing Line / Command")
#                                         st.code(ai_help.get("failed_line", "N/A"), language="bash")
                                        
#                                         st.subheader("💡 Suggested Fix")
#                                         st.markdown(ai_help.get("suggestion", "Check logs manually."))
                                    
#                         except Exception as e:
#                             st.error(f"Execution Error: {str(e)}")         
#                 # if st.form_submit_button("🚀 Run Job"):
#                 #     server = get_jenkins_server()
#                 #     if server:
#                 #         final = {k: str(v).lower() if isinstance(v, bool) else v for k,v in form_params.items()}
#                 #         qid = server.build_job(selected_job_name, final)
#                 #         st.success(f"Triggered! Queue ID: {qid}")
#                 #         st.session_state["polling_active"] = True
#                 #         st.session_state["polling_job"] = selected_job_name
#                 #         st.session_state["polling_queue_id"] = qid
#                 #         try:
#                 #             info = server.get_job_info(selected_job_name)
#                 #             st.session_state["polling_build"] = info['nextBuildNumber'] - 1 
#                 #         except: pass
#                 #         st.rerun()
#                 #     try:
#                 #         b_info = server.get_build_info(selected_job_name, st.session_state["polling_build"])
#                 #         res = b_info.get("result")
#                 #         if res:
#                 #             st.session_state["polling_active"] = False
#                 #             if res == "SUCCESS":
#                 #                 st.success(f"✅ {selected_job_name} #{st.session_state['polling_build']} Finished!")
#                 #             else:
#                 #                 st.error(f"❌ {selected_job_name} #{st.session_state['polling_build']} Failed!")
#                 #         else:
#                 #             st.info(f"🔨 {selected_job_name} #{st.session_state['polling_build']} Running...")
#                 #             time.sleep(2)
#                 #             st.rerun()
#                 #     except:
#                 #         time.sleep(2)
#                 #         st.rerun()
# # --- INPUT ---
user_input = st.chat_input("Ask: 'Run SQL...', 'Find jobs...', 'AWR last 3 hours'")

if user_input:
    handle_agent_execution(user_input)
    # st.session_state["jenkins_matches"] = []
    # st.session_state["messages"].append({"role": "user", "content": user_input})
    
    # with st.chat_message("user"):
    #     st.markdown(user_input)
    
    # with st.chat_message("assistant"):
    #     with st.spinner("Agent processing..."):
    #         try:
    #             # Provide context to the agent
    #             ctx = f"Context: Connected to {st.session_state['current_db']}. Time: {datetime.now()}."
    #             oracle_admin.update_system_message(oracle_admin.system_message + "\n" + ctx)
                
    #             chat_res = user_proxy.initiate_chat(
    #                 oracle_admin, 
    #                 message=user_input, 
    #                 clear_history=False
    #             )
                
    #             # Get final response
    #             final_response = "Task Completed."
    #             for m in reversed(chat_res.chat_history):
    #                 if m.get('role') == 'user': continue
    #                 c = m.get('content','')
    #                 if c and "TERMINATE" not in c:
    #                     final_response = c
    #                     break
    #                 if "TERMINATE" in c:
    #                     final_response = c.replace("TERMINATE", "")
    #                     break
                
    #             st.markdown(final_response)
    #             st.session_state["messages"].append({"role": "assistant", "content": final_response})
    #             st.rerun() # Rerun to render buttons if report was generated
                
    #         except Exception as e:
    #             st.error(f"Error: {e}")