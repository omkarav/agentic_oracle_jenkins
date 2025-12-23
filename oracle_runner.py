# oracle_runner.py (enhanced for AWR reports - fixed syntax error)
import oracledb
import json
import os
from autogen import AssistantAgent
from dotenv import load_dotenv
from datetime import datetime
import pandas as pd

load_dotenv()
os.environ["REQUESTS_CA_BUNDLE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"
os.environ["SSL_CERT_FILE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"

temp_config = {
    "config_list": [{"model": "gpt-4o-mini", "api_key": os.getenv("OPENAI_API_KEY")}],
    "temperature": 0.2,
}
# Config file path - adjust as needed; can be relative or absolute
CONFIG_FILE = "db_config.json"
# ——————————— AWR Analysis Agent (NEW) ———————————
awr_analyzer_agent = AssistantAgent(
    name="awr_analyzer",
    llm_config=temp_config,
    system_message="""
You are an Oracle Performance Tuning Guru with 25+ years of experience.
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

Reply ONLY with the analysis — no introductions like "Here is my analysis".
""",
    human_input_mode="NEVER",
)
def load_db_config():
    """
    Loads DB configurations from JSON file.
    If file doesn't exist, returns a default config dict.
    """
    default_config = {
        "DEFAULT": {
            "user": "dbcheck",
            "password": "fordbas",
            "dsn": "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=localhost)(PORT=3333))(CONNECT_DATA=(SERVICE_NAME=BSSPLABDB)))"
        },
        "PLAB_AMDD": {
            "user": "dbcheck",  # Placeholder - update with real creds
            "password": "password_placeholder",
            "dsn": "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=localhost)(PORT=3333))(CONNECT_DATA=(SERVICE_NAME=CMMAMDD1DB)))"  # Placeholder DSN
        },
        "OTHER_DB": {
            "user": "dbcheck",  # Placeholder
            "password": "password_placeholder",
            "dsn": "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=other-host.example.com)(PORT=1521))(CONNECT_DATA=(SERVICE_NAME=OTHER_DB)))"  # Placeholder
        }
        # Add more DBs here as needed
    }
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                loaded = json.load(f)
                # Merge with defaults to avoid missing keys
                for db_name, config in default_config.items():
                    if db_name not in loaded:
                        loaded[db_name] = config
                return loaded
        except Exception as e:
            print(f"Warning: Failed to load {CONFIG_FILE}: {e}. Using defaults.")
    
    # If no file, save defaults for user to edit
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(default_config, f, indent=4)
        print(f"Created {CONFIG_FILE} with default configs. Please update credentials and DSNs.")
    except Exception:
        pass  # Non-critical
    
    return default_config

DB_CONFIG = load_db_config()

def get_db_list():
    """Returns list of available DB names."""
    return list(DB_CONFIG.keys())

def run_oracle_query(sql: str, db: str = "DEFAULT"):
    """
    Executes SQL on the specified Oracle DB and returns rows as list of dicts.
    Falls back to DEFAULT if db not found.
    """
    oracledb.init_oracle_client(lib_dir=r"C:\Users\omkarav\Downloads\instantclient-basiclite-windows.x64-19.28.0.0.0dbru\instantclient_19_28")
    
    config = DB_CONFIG.get(db.upper(), DB_CONFIG["DEFAULT"])
    
    try:
        conn = oracledb.connect(
            user=config["user"],
            password=config["password"],
            dsn=config["dsn"]
        )
        # Set session timezone to match manual/SQL*Plus (adjust '-06:00' if your TZ differs)
        cur_tz = conn.cursor()
        cur_tz.execute("ALTER SESSION SET TIME_ZONE = '-06:00'")
        cur_tz.close()
        
        cur = conn.cursor()
        cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        return {"error": str(e)}

# NEW: Function to generate full AWR report (HTML/text) using DBMS_WORKLOAD_REPOSITORY
# oracle_runner.py — FINAL WORKING VERSION
# oracle_runner.py → replace generate_awr_report with THIS exact code
def generate_awr_report(start_snap: int, end_snap: int, db: str = "DEFAULT", format_type: str = "html"):
    oracledb.init_oracle_client(lib_dir=r"C:\Users\omkarav\Downloads\instantclient-basiclite-windows.x64-19.28.0.0.0dbru\instantclient_19_28")
    
    config = DB_CONFIG.get(db.upper(), DB_CONFIG["DEFAULT"])
    
    try:
        conn = oracledb.connect(
            user=config["user"],
            password=config["password"],
            dsn=config["dsn"]
        )
        cur = conn.cursor()
        cur.execute("ALTER SESSION SET TIME_ZONE = '-06:00'")

        # Get DBID & Instance Number
        cur.execute("SELECT DBID FROM V$DATABASE")
        dbid = cur.fetchone()[0]
        cur.execute("SELECT INSTANCE_NUMBER FROM V$INSTANCE")
        inst_num = cur.fetchone()[0]

        # THIS IS THE ONLY CALL THAT WORKS FROM PYTHON ON 19c
        sql = f"""
        SELECT /*+ NO_MERGE */ *
            FROM TABLE(DBMS_WORKLOAD_REPOSITORY.AWR_REPORT_HTML({dbid}, {inst_num}, {start_snap}, {end_snap}, 0))
        """

        cur.execute(sql)
        
        report = ""
        for row in cur:
         line = row[0]
         if line:
          report += str(line)

        
        conn.close()
        
        if not report.strip():
            report = "<h3>AWR Report generated but empty (no activity in period)</h3>"
            
        return {
            "status": "ok", 
            "report": report,
            "filename": f"AWR_Report_{start_snap}_to_{end_snap}.html"   # <-- ADD THIS
        }

    except Exception as e:
        return {"status": "error", "message": f"AWR Error: {str(e)}"}
# NEW: Analyze AWR HTML report using LLM (via local OpenAI call)
# NEW: Compare two AWR reports
def analyze_awr_report(html_content: str, custom_prompt: str = None):
    # Re-use the same config as the rest of the app
    # Truncate if absurdly long (safety)
    if len(html_content) > 100_000:
        html_content = html_content[:100_000] + "\n\n... [Report truncated for analysis]"

    user_message = custom_prompt or "Analyze this AWR report and give me a performance tuning summary with recommendations."
    
    try:
        response = awr_analyzer_agent.generate_reply(
            messages=[{"role": "user", "content": user_message + "\n\nAWR Report:\n" + html_content}]
        )
        return {"status": "ok", "analysis": response}
    except Exception as e:
        return {"status": "error", "message": f"Analysis failed: {str(e)}"}
def compare_awr_reports(report1_html: str, report2_html: str, label1: str = "Baseline", label2: str = "Current"):
    from autogen import AssistantAgent
    import os
    from dotenv import load_dotenv
    load_dotenv()

    llm_config = {
        "config_list": [{"model": "gpt-4o-mini", "api_key": os.getenv("OPENAI_API_KEY")}],
        "temperature": 0.0,
        "timeout": 600,
    }

    # Keep full context but safe size
    r1 = report1_html[:130_000]
    r2 = report2_html[:130_000]

    comparator = AssistantAgent(
        name="awr_master_analyzer",
        llm_config=llm_config,
        system_message="""
You are an Oracle Principal Performance Architect.
Compare two AWR reports and deliver a CONCISE but COMPLETE executive summary.

MANDATORY STRUCTURE (use exactly this order and formatting):

### OVERALL VERDICT
One sentence: "Performance improved / degraded / stable"

### WHAT IMPROVED ↑ (Green)
• Metric → value change

### WHAT DEGRADED ↓ (Red)
• Metric → value change + impact

### STABLE / NORMAL
• Things that are fine

### TOP CHANGES SUMMARY
| Category           | Baseline       | Current        | Delta       | Status     |
|--------------------|----------------|----------------|-------------|------------|

### TOP 10 SQL COMPARISON (real SQL_ID + text)
| Rank | SQL_ID           | SQL Text (first 180 chars...)                     | Exec | Elap(s) | Δ Elap   | Status         |
|------|------------------|---------------------------------------------------|------|---------|----------|----------------|

### REGRESSIONS (>100% worse)
• SQL_ID → reason + recommendation

### NEW HEAVY HITTERS
• SQL_ID → first appeared → possible cause

### RECOMMENDATIONS (prioritized)
1. Highest impact fix first
2. ...
""",
        human_input_mode="NEVER",
    )

    prompt = f"""
COMPARE THESE TWO AWR REPORTS — GIVE EXECUTIVE + DEEP DIVE

BASELINE PERIOD ({label1}):
{r1}

CURRENT PERIOD ({label2}):
{r2}

Follow the exact structure above.
Use real SQL_IDs and real SQL text.
Be concise but ruthless — highlight everything that matters.
"""

    try:
        response = comparator.generate_reply([{"role": "user", "content": prompt}])
        return {"status": "ok", "comparison": response}
    except Exception as e:
        return {"status": "error", "message": f"Comparison failed: {str(e)}"}
# Helper: Get snapshots for time range (for AWR report bounds)
def get_snapshots_for_time(hours_back: int = 3, db: str = "DEFAULT"):
    """Returns start/end SNAP_ID for last N hours."""
    sql = f"""
    SELECT MIN(SNAP_ID) AS start_snap, MAX(SNAP_ID) AS end_snap
    FROM DBA_HIST_SNAPSHOT
    WHERE BEGIN_INTERVAL_TIME >= SYSTIMESTAMP - INTERVAL '{hours_back}' HOUR
    """
    rows = run_oracle_query(sql, db)
    if rows and len(rows) > 0:
        return rows[0].get("START_SNAP"), rows[0].get("END_SNAP")
    return None, None
# ======================= FULL HEALTH CHECK =======================
# ======================= ULTIMATE FULL AI HEALTH CHECK (22 CHECKS) =======================
def run_full_health_check(db: str = "DEFAULT"):
    """Runs critical Oracle health checks + AI executive report"""
    from autogen import AssistantAgent
    import os
    from dotenv import load_dotenv
    load_dotenv()

    queries = {
#         "1. Critical Alert Log Errors (last 24 hours)": """
#     SELECT 
#         INST_ID,
#         TO_CHAR(ORIGINATING_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS') AS TIME,
#         CASE WHEN MESSAGE_LEVEL = 1 THEN '🔴 CRITICAL'
#              WHEN MESSAGE_LEVEL = 2 THEN '🟡 SEVERE'
#              ELSE '⚪ INFO' 
#         END AS SEVERITY,
#         SUBSTR(MESSAGE_TEXT, 1, 200) AS MESSAGE
#     FROM diag_data
#     WHERE ORIGINATING_TIMESTAMP > SYSDATE - 1
#       AND (
#            MESSAGE_TEXT LIKE '%ORA-%'
#         OR MESSAGE_TEXT LIKE '%deadlock%'
#         OR MESSAGE_TEXT LIKE '%archiver%'
#         OR MESSAGE_TEXT LIKE '%error%'
#         OR MESSAGE_LEVEL <= 2
#       )
#     ORDER BY ORIGINATING_TIMESTAMP DESC
#     FETCH FIRST 25 ROWS ONLY
# """,
        "2. Tablespace Usage >90% ( also included temp and undo utilization as well)": """
           set term on feedback off lines 130 pagesize 999 tab off trims on echo off
column MB format 999,999,999  heading "Total MB"
column free format 99,999,999 heading "Free MB"
column used format 99,999,999 heading "Used MB"
column tablespace_name format a24 heading "Tablespace"
column status format a3 truncated
col extent_management           for a1 trunc   head "M"
col segment_space_management    for a1 trunc   head "S"
col allocation_type             for a1 trunc   head "A"
col Ext_Size for a4 trunc head "Init"
column pfree format a3 trunc heading "%Fr"

break on report
compute sum of MB on report
compute sum of free on report
compute sum of used on report

select
  d.tablespace_name,
  decode(d.status, 'ONLINE', 'OLN', 'READ ONLY', 'R/O', d.status) status,
  d.extent_management,
  decode(d.allocation_type, 'USER','', d.allocation_type) allocation_type,
  d.segment_space_management,
  (case
    when initial_extent < 1048576
        then lpad(round(initial_extent/1024,0),3)||'K'
    else lpad(round(initial_extent/1024/1024,0),3)||'M'
  end) Ext_Size,
  NVL (a.bytes / 1024 / 1024, 0) MB,
  NVL (f.bytes / 1024 / 1024, 0) free,
  (NVL (a.bytes / 1024 / 1024, 0) - NVL (f.bytes / 1024 / 1024, 0)) used,
  lpad(round((f.bytes/a.bytes)*100,0),3) pfree,
  (case when round(f.bytes/a.bytes*100,0) >= 20 or f.bytes>=20*1024*1024*1024 then ' ' else '*' end) alrt
FROM sys.dba_tablespaces d,
  (SELECT   tablespace_name, SUM(bytes) bytes
   FROM dba_data_files
   GROUP BY tablespace_name) a,
  (SELECT   tablespace_name, SUM(bytes) bytes
   FROM dba_free_space
   GROUP BY tablespace_name) f,
  (SELECT   tablespace_name, MAX(bytes) large
   FROM dba_free_space
   GROUP BY tablespace_name) l
WHERE d.tablespace_name = a.tablespace_name(+)
  AND d.tablespace_name = f.tablespace_name(+)
  AND d.tablespace_name = l.tablespace_name(+)
  AND NOT (d.extent_management LIKE 'LOCAL' AND d.contents LIKE 'TEMPORARY')
UNION ALL
select
  d.tablespace_name,
  decode(d.status, 'ONLINE', 'OLN', 'READ ONLY', 'R/O', d.status) status,
  d.extent_management,
  decode(d.allocation_type, 'UNIFORM','U', 'SYSTEM','A', 'USER','', d.allocation_type) allocation_type,
  d.segment_space_management,
  (case
    when initial_extent < 1048576
        then lpad(round(initial_extent/1024,0),3)||'K'
    else lpad(round(initial_extent/1024/1024,0),3)||'M'
  end) Ext_Size,
  NVL (a.bytes / 1024 / 1024, 0) MB,
  (NVL (a.bytes / 1024 / 1024, 0) - NVL (t.bytes / 1024 / 1024, 0)) free,
  NVL (t.bytes / 1024 / 1024, 0) used,
  lpad(round(nvl(((a.bytes-t.bytes)/NVL(a.bytes,0))*100,100),0),3) pfree,
  (case when nvl(round(((a.bytes-t.bytes)/NVL(a.bytes,0))*100,0),100) >= 20 or a.bytes-t.bytes>=20*1024*1024*1024 then ' ' else '*' end) alrt
FROM sys.dba_tablespaces d,
  (SELECT   tablespace_name, SUM(bytes) bytes
   FROM dba_temp_files
   GROUP BY tablespace_name order by tablespace_name) a,
  (SELECT   tablespace_name, SUM(bytes_used  ) bytes
   FROM v$temp_extent_pool
   GROUP BY tablespace_name) t,
  (SELECT   tablespace_name, MAX(bytes_cached) large
   FROM v$temp_extent_pool
   GROUP BY tablespace_name order by tablespace_name) l
WHERE d.tablespace_name = a.tablespace_name(+)
  AND d.tablespace_name = t.tablespace_name(+)
  AND d.tablespace_name = l.tablespace_name(+)
  AND d.extent_management LIKE 'LOCAL'
  AND d.contents LIKE 'TEMPORARY'
  ORDER by 1
        """,
        "4. Long Running Operations (>2 hours)": """
           SELECT INST_ID, SID, SERIAL#, USERNAME, SQL_ID,
                   ROUND((SYSDATE - LOGON_TIME)*24, 2) AS HOURS_CONNECTED,
                   PROGRAM
            FROM GV$SESSION
            WHERE LOGON_TIME < SYSDATE - 2/24
              AND TYPE = 'USER'
              AND STATUS = 'ACTIVE'
              AND USERNAME IS NOT NULL
        """,
        "5. Current Blocking Sessions": """
          SELECT
    s.blocking_instance,
    s.blocking_session,
    bs.username AS blocker_user,
    bs.machine  AS blocker_machine,
    bs.program  AS blocker_program,
    s.inst_id   AS blocked_instance,
    s.sid       AS blocked_sid,
    s.username  AS blocked_user,
    s.machine   AS blocked_machine,
    s.program   AS blocked_program,
    s.event     AS blocked_event
FROM gv$session s
LEFT JOIN gv$session bs
       ON s.blocking_session = bs.sid
      AND s.blocking_instance = bs.inst_id
WHERE s.blocking_session IS NOT NULL
ORDER BY s.blocking_instance, s.blocking_session
        """,
        "6. Top Wait Events Right Now": """
          SELECT INST_ID, EVENT, WAIT_CLASS,
                   ROUND(TIME_WAITED_MICRO/1000000, 1) AS WAIT_SEC
            FROM GV$SESSION_EVENT
            WHERE WAIT_CLASS != 'Idle'
              AND SID IN (SELECT SID FROM GV$SESSION WHERE TYPE='USER')
            ORDER BY WAIT_SEC DESC FETCH FIRST 15 ROWS ONLY
        """,
        "7. Invalid Objects": """
            SELECT OWNER, OBJECT_TYPE, COUNT(*) AS COUNT
            FROM DBA_OBJECTS WHERE STATUS = 'INVALID'
            GROUP BY OWNER, OBJECT_TYPE ORDER BY COUNT DESC
        """,
        "8. Failed Scheduler Jobs (last 7d)": """
            SELECT OWNER, JOB_NAME, TO_CHAR(LOG_DATE, 'YYYY-MM-DD HH24:MI') AS FAILED_AT, ERROR#
            FROM DBA_SCHEDULER_JOB_RUN_DETAILS
            WHERE STATUS = 'FAILED' AND LOG_DATE > SYSDATE - 7
            ORDER BY LOG_DATE DESC
        """,
        "9. Last Successful RMAN Backup": """
          SELECT 
    session_recid,
    session_stamp,
    input_type,
    status,
    to_char(start_time,'YYYY-MM-DD HH24:MI:SS') AS start_time,
    to_char(end_time,'YYYY-MM-DD HH24:MI:SS') AS end_time,
    round(elapsed_seconds/60,2) AS minutes
FROM v$rman_backup_job_details
WHERE status = 'COMPLETED'
ORDER BY end_time DESC
FETCH FIRST 1 ROWS ONLY
        """,
        "10. Stale/Missing Statistics": """
            SELECT OWNER, TABLE_NAME, LAST_ANALYZED, STALE_STATS
            FROM DBA_TAB_STATISTICS
            WHERE (STALE_STATS = 'YES' OR LAST_ANALYZED IS NULL)
              AND OWNER NOT IN ('SYS', 'SYSTEM')
              AND NUM_ROWS > 1000000
            ORDER BY LAST_ANALYZED NULLS FIRST FETCH FIRST 20 ROWS ONLY
        """,
        "11. Disabled/Unusable Indexes": """
            SELECT OWNER, INDEX_NAME, STATUS FROM DBA_INDEXES WHERE STATUS NOT IN ('VALID', 'N/A')
        """,
        "12. Risky Parameters": """
            SELECT NAME, VALUE, ISDEFAULT, ISMODIFIED
            FROM gV$PARAMETER
            WHERE NAME IN ('sga_target', 'pga_aggregate_target', 'optimizer_mode', 'db_file_multiblock_read_count',
                           'processes', 'open_cursors', 'cursor_sharing', 'recyclebin')
        """,
        "13. ADDM Findings (last 3)": """
         SELECT
    TO_CHAR(t.execution_end, 'YYYY-MM-DD HH24:MI') AS time,
    f.finding_name,
    f.message,
    f.impact,
    f.impact_type
FROM dba_advisor_findings f
JOIN dba_advisor_tasks t
      ON f.task_id = t.task_id
WHERE t.advisor_name  2   = 'ADDM'
  AND t.execution_end IS NOT NULL
ORDER BY t.execution_end DESC
FETCH FIRST 15 ROWS ONLY
        """,

        "15. High Water Mark Issues (Top 10)": """
            SELECT OWNER, TABLE_NAME, ROUND(BLOCKS * 8 / 1024) AS MB_ALLOCATED,
                   ROUND(NUM_ROWS * AVG_ROW_LEN / 1024 / 1024) AS MB_USED
            FROM DBA_TABLES
            WHERE BLOCKS > 10000 AND NUM_ROWS > 0
            ORDER BY (BLOCKS * 8 / 1024) DESC FETCH FIRST 10 ROWS ONLY
        """,
        "16. Redo for Redo": """
            SELECT GROUP#, STATUS, ARCHIVED, THREAD#, SEQUENCE#
            FROM V$LOG WHERE STATUS = 'CURRENT'
        """,
        "18. Flashback On?": """
            SELECT FLASHBACK_ON FROM V$DATABASE
        """,
        "19. Archive Destination Full": """
            SELECT 
    round(space_limit / 1024 / 1024 / 1024, 2) AS total_gb,
    round(space_used  / 1024 / 1024 / 1024, 2) AS used_gb,
    round((space_used/space_limit)*100, 2) AS pct_used
FROM v$recovery_file_dest

        """,
        "20. ASM Disk Group Usage": """
            SELECT NAME, ROUND(USABLE_FILE_MB / 1024) AS FREE_GB, STATE
            FROM V$ASM_DISKGROUP
        """,
        "21. Sessions Near Limit": """
          SELECT
    (SELECT COUNT(*) FROM gv$session) AS current_sessions,
    (SELECT value FROM gv$parameter WHERE name = 'processes' AND rownum = 1) AS max_processes
FROM dual
        """,
        "22. CPU/IO Saturation": """
            SELECT 'DB CPU' AS METRIC, VALUE/100 AS VALUE FROM gV$SYSMETRIC WHERE METRIC_NAME = 'Database CPU Time Ratio'
            UNION ALL
            SELECT 'Host CPU Utilization (%)', VALUE FROM gV$SYSMETRIC WHERE METRIC_NAME = 'Host CPU Utilization'
            UNION ALL
            SELECT 'I/O Megabytes per Second', VALUE FROM gV$SYSMETRIC WHERE METRIC_NAME = 'I/O Megabytes per Second'
        """
    }

    results = {}
    for name, sql in queries.items():
        try:
            data = run_oracle_query(sql.strip(), db)
            results[name] = data if data else ["No data"]
        except Exception as e:
            results[name] = [f"Error: {str(e)}"]

    # Build raw report text
    raw_text = f"Database: {db.upper()}\nCheck Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    for title, data in results.items():
        raw_text += f"{title}\n{'-' * len(title)}\n"
        if isinstance(data, list) and data and isinstance(data[0], dict):
            df = pd.DataFrame(data)
            raw_text += df.to_string(index=False, na_rep='NULL') + "\n\n"
        else:
            raw_text += str(data) + "\n\n"

    # AI Executive Report
    llm_config = {
        "config_list": [{"model": "gpt-4o-mini", "api_key": os.getenv("OPENAI_API_KEY")}],
        "temperature": 0.0,
        "timeout": 600,
    }

    reporter = AssistantAgent(
        name="oracle_health_master",
        llm_config=llm_config,
        system_message="""
You are an Oracle Principal DBA performing an emergency health assessment.
Produce a CRISP, executive-level report with:

# ORACLE FULL HEALTH CHECK
**Database:** CURRENT_DB  | **Date:** TODAY

### OVERALL STATUS: 🟢 GOOD | 🟡 DEGRADED | 🔴 CRITICAL

###  CRITICAL (Fix in next hour)
###  WARNING (Fix today/tomorrow)
###  ALL GOOD

### DETAILED FINDINGS (tables where useful)

### IMMEDIATE ACTIONS (numbered by priority)
1. ...
""",
        human_input_mode="NEVER",
    )

    try:
        report = reporter.generate_reply([{
            "role": "user",
            "content": f"Generate beautiful health check report from this data:\n\n{raw_text}"
        }])
        return {"status": "ok", "report": report}
    except Exception as e:
        return {"status": "error", "message": f"AI report failed: {str(e)}"}
# oracle_runner.py - ADD THESE TWO FUNCTIONS

def generate_ash_report(minutes: int = 30, db: str = "DEFAULT"):
    """Generates real Oracle ASH Report (best for last 5 mins to 2 hours)"""
    oracledb.init_oracle_client(lib_dir=r"C:\Users\omkarav\Downloads\instantclient-basiclite-windows.x64-19.28.0.0.0dbru\instantclient_19_28")
    config = DB_CONFIG.get(db.upper(), DB_CONFIG["DEFAULT"])
    try:
        conn = oracledb.connect(user=config["user"], password=config["password"], dsn=config["dsn"])
        cur = conn.cursor()
        cur.execute("ALTER SESSION SET TIME_ZONE = '-06:00'")

        cur.execute("SELECT DBID FROM V$DATABASE")
        dbid = cur.fetchone()[0]
        cur.execute("SELECT INSTANCE_NUMBER FROM V$INSTANCE")
        inst_num = cur.fetchone()[0]

        sql = f"""
        SELECT OUTPUT 
        FROM TABLE(DBMS_WORKLOAD_REPOSITORY.ASH_REPORT_HTML(
            :dbid, :inst_num,
            SYSDATE - ({minutes}/1440), SYSDATE,
            0
        ))
        """
        cur.execute(sql, dbid=dbid, inst_num=inst_num)
        
        report = ""
        for row in cur:
            if row[0]:
                report += str(row[0])
        conn.close()

        if not report.strip():
            report = "<h3>No significant activity in the selected period</h3>"

        return {
            "status": "ok",
            "report": report,
            "filename": f"ASH_Report_Last_{minutes}_Minutes.html"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
def generate_ash_report(minutes: int = 30, db: str = "DEFAULT"):
    """Generates real Oracle ASH Report (best for last 5 mins to 2 hours)"""
    oracledb.init_oracle_client(lib_dir=r"C:\Users\omkarav\Downloads\instantclient-basiclite-windows.x64-19.28.0.0.0dbru\instantclient_19_28")
    config = DB_CONFIG.get(db.upper(), DB_CONFIG["DEFAULT"])
    try:
        conn = oracledb.connect(user=config["user"], password=config["password"], dsn=config["dsn"])
        cur = conn.cursor()
        cur.execute("ALTER SESSION SET TIME_ZONE = '-06:00'")

        cur.execute("SELECT DBID FROM V$DATABASE")
        dbid = cur.fetchone()[0]
        cur.execute("SELECT INSTANCE_NUMBER FROM V$INSTANCE")
        inst_num = cur.fetchone()[0]

        sql = f"""
        SELECT OUTPUT 
        FROM TABLE(DBMS_WORKLOAD_REPOSITORY.ASH_REPORT_HTML(
            :dbid, :inst_num,
            SYSDATE - ({minutes}/1440), SYSDATE,
            0
        ))
        """
        cur.execute(sql, dbid=dbid, inst_num=inst_num)
        
        report = ""
        for row in cur:
            if row[0]:
                report += str(row[0])
        conn.close()

        if not report.strip():
            report = "<h3>No significant activity in the selected period</h3>"

        return {
            "status": "ok",
            "report": report,
            "filename": f"ASH_Report_Last_{minutes}_Minutes.html"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}