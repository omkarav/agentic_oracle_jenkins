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
from oracle_runner import (
    run_oracle_query, get_db_list, generate_awr_report,
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

intent_agent = ConversableAgent(name="intent_classifier", llm_config=common_llm_config, human_input_mode="NEVER")

awr_analyzer_agent = AssistantAgent(
    name="awr_analyzer",
    llm_config=common_llm_config,
    system_message="""You are an Oracle Performance Tuning Guru with 25+ years of experience.
You receive full AWR reports in HTML format (sometimes huge).

Your job:
- Identify the exact period, DB version, host config, load profile
- Highlight top wait events and their % of DB time
- Find the most expensive SQLs (by elapsed time, CPU, buffer gets)
- Detect regressions, spikes, anomalies
- Give concrete, prioritized tuning recommendations
- Use markdown with clear sections and bullet points
- NEVER include raw HTML tables in your reply
- Keep it concise but actionable (max 1200 words)

Reply ONLY with the analysis — no introductions like "Here is my analysis".""",
    human_input_mode="NEVER",
)

db_resolver_agent = AssistantAgent(
    name="db_resolver",
    llm_config=common_llm_config,
    system_message="You are a DB connection resolver. Given a user request to connect/switch DB and a list of available DBs, suggest the best match. Output ONLY JSON: {'suggested_db': 'DB_NAME', 'confidence': 0-1, 'reason': 'brief explanation'}",
    human_input_mode="NEVER",
)

code_writer_agent = AssistantAgent(
    name="code_writer_agent",
    llm_config=common_llm_config,
    code_execution_config=False,
    system_message="You are an expert Oracle SQL and PLSQL generator. The user will describe what they want in natural language. If the request mentions 'AWR report', generate PL/SQL to call DBMS_WORKLOAD_REPOSITORY.AWR_REPORT_HTML with appropriate snapshot bounds. For raw queries, output **only** a valid Oracle SQL query. No markdown, no explanations. Output plain SQL/PLSQL and no semicolon (;).",
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
        awr_hours = args.get("awr_hours")
        db = args.get("db")
    except Exception as e:
        return {"status": "error", "message": f"JSON parse error: {e}"}

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

    if awr_hours is not None:
        hours = int(awr_hours)
        start_snap, end_snap = get_snapshots_for_time(hours, db)
        return generate_awr_report(start_snap, end_snap, db)

    sql = args.get("sql", "")
    result = run_oracle_query(sql, db)
    if isinstance(result, dict) and "error" in result:
        return {"status": "error", "message": result["error"]}
    return {"status": "ok", "result": result}  # ← Fixed: added missing } and )

code_executor_agent.register_for_execution(name="approve_and_run_sql")(approve_and_run_sql_wrapper)

jenkins_server = jenkins.Jenkins("http://localhost:9020", username="dba", password="113bb934053435f19fa62d94f8c79a108c")

if "awr_history" not in st.session_state:
    st.session_state["awr_history"] = []

if "health_report" not in st.session_state:
    st.session_state["health_report"] = None
if "health_error" not in st.session_state:
    st.session_state["health_error"] = None
if "awr_compare" not in st.session_state:
    st.session_state["awr_compare"] = None

if "chat" not in st.session_state:
    st.session_state["chat"] = []
if "dbs" not in st.session_state:
    st.session_state["dbs"] = get_db_list()
if "current_db" not in st.session_state:
    st.session_state["current_db"] = st.session_state["dbs"][0]

st.markdown(
    """
<style>
.chat-container {background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%); border-radius: 16px; padding: 20px; box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1); margin: 20px 0; border: 1px solid rgba(255, 255, 255, 0.2);}
.chat-area {max-height: 60vh; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 16px; background: white; border-radius: 12px; box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.05);}
.user-bubble {background: linear-gradient(135deg, #667eea 0%, #764ba2); color: white; align-self: flex-end; padding: 16px 20px; border-radius: 20px 20px 5px 20px; max-width: 80%; white-space: pre-wrap; box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);}
.assistant-block {background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); color: white; align-self: flex-start; padding: 16px 20px; border-radius: 20px 20px 20px 5px; max-width: 80%; white-space: pre-wrap; box-shadow: 0 4px 12px rgba(240, 147, 251, 0.3);}
.intent-badge {display: inline-block; padding: 8px 12px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 20px; margin: 8px auto; color: white; font-weight: 600;}
.db-badge {padding: 12px 16px; background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); border-radius: 25px; color: white; font-weight: 700; text-align: center; box-shadow: 0 4px 15px rgba(79, 172, 254, 0.4); display: inline-block;}
.stButton > button {background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; border-radius: 20px; padding: 10px 20px; font-weight: 600; box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3); transition: all 0.2s; width: 100%;}
.stButton > button:hover {transform: translateY(-2px); box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4);}
</style>
""", unsafe_allow_html=True
)

# === SIDEBAR ===
st.sidebar.markdown("---")
st.sidebar.markdown("### Emergency Tools")

if st.sidebar.button("Health Check", type="primary", use_container_width=True):
    with st.spinner("Running health checks..."):
        health_data = run_full_health_check(db=st.session_state["current_db"])
    if health_data.get("status") == "ok":
        st.session_state["health_report"] = health_data["report"]
        st.session_state["health_error"] = None
        st.sidebar.success("Health check complete! (report in main area)")
    else:
        st.session_state["health_error"] = health_data.get("message")
        st.session_state["health_report"] = None
        st.sidebar.error("Health check failed")

st.sidebar.markdown("---")
st.sidebar.markdown(f"<div class='db-badge'> Connected to: {st.session_state['current_db']} </div>", unsafe_allow_html=True)

st.sidebar.markdown("---")
st.sidebar.markdown("### Compare Any Two AWR Periods")

if len(st.session_state.awr_history) >= 2:
    valid_history = [h for h in st.session_state.awr_history if h.get("report_html")]

    opt1 = st.sidebar.selectbox("First period (Baseline)", valid_history, format_func=lambda x: x["label"], key="cmp_baseline")
    opt2 = st.sidebar.selectbox("Second period (Compare to)", valid_history, format_func=lambda x: x["label"], key="cmp_current")

    if opt1["id"] != opt2["id"]:
        if st.sidebar.button("Compare These Two Periods — Full Regression Analysis", type="primary", use_container_width=True):
            with st.spinner("Comparing AWR reports..."):
                comp = compare_awr_reports(
                    report1_html=opt1["report_html"],
                    report2_html=opt2["report_html"],
                    label1=f"Baseline: {opt1['period_str']}",
                    label2=f"Current: {opt2['period_str']}"
                )
            if comp["status"] == "ok":
                st.session_state["awr_compare"] = comp["comparison"]
                st.sidebar.success("Comparison ready (view in main area)")
            else:
                st.session_state["awr_compare"] = None
                st.sidebar.error(comp.get("message"))
else:
    st.sidebar.info("Generate at least 2 AWR reports to enable comparison.")

# === MAIN AREA - REPORTS ===
if st.session_state.get("health_report"):
    st.subheader("Oracle Full Health Check")
    st.success("Full AI Health Check Complete!")
    st.markdown(st.session_state["health_report"])
    st.download_button(
        "📄 Download Health Report (HTML)",
        data=st.session_state["health_report"],
        file_name=f"Oracle_Health_Check_{datetime.now().strftime('%Y%m%d_%H%M')}.html",
        mime="text/html"
    )
    if st.button("Dismiss Health Report"):
        del st.session_state["health_report"]
        st.rerun()

if st.session_state.get("health_error"):
    st.error(st.session_state["health_error"])
    if st.button("Dismiss Error"):
        del st.session_state["health_error"]
        st.rerun()

if st.session_state.get("awr_compare"):
    st.subheader("Full AWR Comparison Report")
    st.markdown(st.session_state["awr_compare"])
    if st.button("Dismiss Comparison"):
        del st.session_state["awr_compare"]
        st.rerun()

st.markdown("---")

# === CHAT ===
chat_holder = st.container()
with chat_holder:
    st.markdown("<div class='chat-container'>", unsafe_allow_html=True)
    st.markdown("<div class='chat-area'>", unsafe_allow_html=True)

    for entry in st.session_state["chat"]:
        uid = entry["id"]

        st.markdown(f"<div class='user-bubble'><div class='meta'>You · {entry['ts']}</div><div>{entry['request']}</div></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='intent-badge'>Intent: {entry.get('intent','unknown')}</div>", unsafe_allow_html=True)

        # Connect DB message
        if entry.get("intent") == "connect_db":
            res = entry.get("oracle_result", {})
            if res.get("status") == "ok":
                st.success(res["message"])
            else:
                st.warning(res["message"])
            continue

        # AWR Report
        if entry.get("intent") == "awr_report" and entry.get("oracle_result"):
            result = entry["oracle_result"]
            if result.get("status") == "ok":
                report_html = result.get("report", "")
                user_request = entry["request"]

                history_entry = {
                    "id": uid,
                    "period_str": user_request,
                    "label": f"{datetime.now().strftime('%H:%M')} - {user_request}",
                    "report_html": report_html,
                }
                if not any(h["id"] == uid for h in st.session_state.awr_history):
                    st.session_state.awr_history.append(history_entry)
                    if len(st.session_state.awr_history) > 20:
                        st.session_state.awr_history.pop(0)

                st.success("AWR report generated!")

                c1, c2 = st.columns([1, 3])
                with c1:
                    st.download_button(
                        "Download AWR (HTML)",
                        data=report_html,
                        file_name=f"AWR_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html",
                        mime="text/html",
                        key=f"dl_{uid}"
                    )
                with c2:
                    if st.button("Analyze with AI", type="primary", key=f"ai_{uid}"):
                        with st.spinner("Analyzing AWR..."):
                            analysis = analyze_awr_report(report_html)
                        if analysis["status"] == "ok":
                            st.markdown("### AI Tuning Recommendations")
                            st.markdown(analysis["analysis"])
                        else:
                            st.error(analysis.get("message"))
                st.markdown("<hr>", unsafe_allow_html=True)
            else:
                st.error(result.get("message", "AWR generation failed"))
            continue

        # Regular Oracle queries
        if entry.get("intent") == "oracle" and entry.get("oracle_sql"):
            sql = entry["oracle_sql"]
            edit_key = f"sql_{uid}"
            if edit_key not in st.session_state:
                st.session_state[edit_key] = sql
            st.text_area("SQL (edit & re-execute):", st.session_state[edit_key], key=edit_key, height=160)

            if st.button("Execute SQL", key=f"exec_{uid}"):
                payload = {"sql": st.session_state[edit_key], "db": st.session_state["current_db"]}
                res = approve_and_run_sql_wrapper(json.dumps(payload))
                entry["oracle_result"] = res
                entry["_last_exec_ts"] = time.time()

            if entry.get("oracle_result"):
                res = entry["oracle_result"]
                if res.get("status") == "ok":
                    df = pd.DataFrame(res["result"]) if res["result"] else pd.DataFrame()
                    st.dataframe(df) if not df.empty else st.info("Query succeeded - no rows returned")
                else:
                    st.error(res.get("message", "Execution failed"))

        # Jenkins
        elif entry.get("intent") == "jenkins":
            matches = entry.get("jenkins_matches", [])
            if matches:
                st.markdown("<ol>" + "".join(f"<li>{m}</li>" for m in matches) + "</ol>", unsafe_allow_html=True)
                selected = st.selectbox("Select job:", matches, key=f"job_{uid}")
                job = find_jenkins_job_by_name(selected, jenkins_server)
                if job:
                    st.write(f"**{job['name']}** - {job.get('description', 'No description')}")
                    params = {}
                    for p in job.get("parameters", []):
                        k = f"p_{uid}_{p['name']}"
                        if "Boolean" in p.get("type", ""):
                            params[p["name"]] = st.checkbox(p["name"], key=k)
                        else:
                            params[p["name"]] = st.text_input(p["name"], value=p.get("default", ""), key=k)
                    if st.button("Run Job", key=f"run_{uid}"):
                        with st.spinner("Running..."):
                            out = run_jenkins_job_and_get_output(job["name"], params, jenkins_server)
                        st.code(out["console"])
                        if out["status"] == "FAILURE":
                            ai = analyze_jenkins_failure(out["console"])
                            if ai:
                                st.error(f"Root cause: {ai['root_cause']}")
                                st.code(ai['failed_line'])
                                st.markdown(ai['suggestion'])
            else:
                st.info("No matching Jenkins jobs found")

        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

# === INPUT ===
st.markdown("---")
st.text_input("Type your request here:", key="task_input", placeholder="e.g. show tablespaces, generate AWR last 4 hours, connect to PROD")
st.button("Process Request", on_click=process_request_callback)

st.caption("Tip: SQL is editable inline. 'connect to DB' switches database. Full user request is now shown in AWR history labels.")