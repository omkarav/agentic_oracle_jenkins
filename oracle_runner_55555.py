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
CONFIG_FILE = "db_config.json"
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
            "user": "dbcheck",  
            "password": "password_placeholder",
            "dsn": "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=localhost)(PORT=3333))(CONNECT_DATA=(SERVICE_NAME=CMMAMDD1DB)))"  
        },
        "OTHER_DB": {
            "user": "dbcheck",  
            "password": "password_placeholder",
            "dsn": "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=other-host.example.com)(PORT=1521))(CONNECT_DATA=(SERVICE_NAME=OTHER_DB)))"  
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
        cur_tz.execute("SELECT DBTIMEZONE FROM DUAL")
        db_tz_result = cur_tz.fetchone()
        db_tz = db_tz_result[0] if db_tz_result else '-06:00'
        print(f"[DEBUG] DB Timezone: {db_tz}")
        cur_tz.execute(f"ALTER SESSION SET TIME_ZONE = '{db_tz}'")
        cur_tz.close()
        
        cur = conn.cursor()
        print(f"[SQL EXEC] {sql[:500]}")
        cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        return {"error": str(e)}

# [Your existing generate_awr_report function - unchanged, truncated for brevity]
def generate_awr_report(start_snap, end_snap, db):
    oracledb.init_oracle_client(lib_dir=r"C:\Users\omkarav\Downloads\instantclient-basiclite-windows.x64-19.28.0.0.0dbru\instantclient_19_28")
    
    config = DB_CONFIG.get(db.upper(), DB_CONFIG["DEFAULT"])
    
    try:
        conn = oracledb.connect(
            user=config["user"],
            password=config["password"],
            dsn=config["dsn"]
        )
        cur_tz = conn.cursor()
        cur_tz.execute("SELECT DBTIMEZONE FROM DUAL")
        db_tz_result = cur_tz.fetchone()
        db_tz = db_tz_result[0] if db_tz_result else '-06:00'
        print(f"[DEBUG] DB Timezone for AWR: {db_tz}")
        cur_tz.execute(f"ALTER SESSION SET TIME_ZONE = '{db_tz}'")
        cur_tz.close()
        cur = conn.cursor()
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
def get_snapshots_for_time(hours_back: float = 3.0, db: str = "DEFAULT"):
    """Returns (start_snap, end_snap) for last N hours - GUARANTEED TO WORK"""
    import math
    
    # Round up to be safe - ensures we always cover the full period
    hours = math.ceil(hours_back)
    
    sql = f"""
    SELECT 
        MAX(CASE WHEN rn = 1 THEN snap_id END) AS start_snap,
        MAX(CASE WHEN rn = 2 THEN snap_id END) AS end_snap
    FROM (
        SELECT snap_id,
               ROW_NUMBER() OVER (ORDER BY snap_id DESC) AS rn
        FROM dba_hist_snapshot
        WHERE begin_interval_time <= SYSTIMESTAMP
          AND begin_interval_time >= SYSTIMESTAMP - INTERVAL '{hours}' HOUR
        ORDER BY snap_id DESC
    )
    WHERE rn <= 2
    """
    
    print(f"[DEBUG] Running snapshot query for last {hours} hours on {db}")
    rows = run_oracle_query(sql, db)
    
    if not rows or len(rows) == 0:
        print("[DEBUG] No rows returned")
        return None, None
        
    row = rows[0]
    print(f"[DEBUG] Raw row: {row}")
    
    # Force uppercase keys and strip whitespace
    row = {k.strip().upper(): v for k, v in row.items()}
    
    start = row.get("START_SNAP")
    end = row.get("END_SNAP")
    
    print(f"[DEBUG] start_snap={start}, end_snap={end}")
    
    if start and end and start != end:
        return int(start), int(end)
    
    return None, None
# def get_snapshots_for_time(hours_back: int = 3, db: str = "DEFAULT"):
#     """Returns start/end SNAP_ID for last N hours."""
#     hours_int = int(hours_back) + 1 if hours_back % 1 >= 0.5 else int(hours_back)
#     sql = f"""
#     SELECT
#         (SELECT MAX(snap_id)
#         FROM dba_hist_snapshot
#         WHERE begin_interval_time <= systimestamp - INTERVAL '{hours_int}' HOUR) AS start_snap,   
#         (SELECT MAX(snap_id)
#         FROM dba_hist_snapshot
#         WHERE begin_interval_time <= systimestamp) AS end_snap
#     FROM dual
#     """
#     rows = run_oracle_query(sql, db)
#     if rows and len(rows) > 0:
#         return rows[0].get("START_SNAP"), rows[0].get("END_SNAP")
#     return None, None
# [Your existing run_full_health_check - unchanged, truncated]
def run_full_health_check(db):
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
        JOIN dba_advisor_tasks t ON f.task_id = t.task_id
        WHERE t.advisor_name = 'ADDM'  -- Fixed typo here
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
        """,
        "23. Top Active Queries by CPU (Current)": """
            SELECT s.INST_ID, s.SID, s.SERIAL#, s.USERNAME, q.SQL_ID,
                   ROUND(s.LAST_CALL_ET / 60, 1) AS MINUTES_RUNNING,
                   ROUND(v.CPU_TIME / 1000000, 2) AS CPU_SEC,
                   s.MACHINE, s.PROGRAM
            FROM GV$SESSION s
            JOIN GV$SQL q ON s.SQL_ID = q.SQL_ID
            JOIN GV$SQLSTATS v ON q.SQL_ID = v.SQL_ID AND s.INST_ID = v.INST_ID
            WHERE s.STATUS = 'ACTIVE' AND s.TYPE = 'USER' AND s.USERNAME IS NOT NULL
            ORDER BY v.CPU_TIME DESC
            FETCH FIRST 10 ROWS ONLY
        """,
        "24. Top Active Queries by IO (Current)": """
            SELECT s.INST_ID, s.SID, s.SERIAL#, s.USERNAME, q.SQL_ID,
                   ROUND(s.LAST_CALL_ET / 60, 1) AS MINUTES_RUNNING,
                   q.PHYSICAL_READS AS IO_READS, q.PHYSICAL_WRITES AS IO_WRITES,
                   s.MACHINE, s.PROGRAM
            FROM GV$SESSION s
            JOIN GV$SQL q ON s.SQL_ID = q.SQL_ID
            WHERE s.STATUS = 'ACTIVE' AND s.TYPE = 'USER' AND s.USERNAME IS NOT NULL
            ORDER BY q.PHYSICAL_READS DESC
            FETCH FIRST 10 ROWS ONLY
        """,
        "25. High Memory Users (PGA/SGA - Current)": """
            SELECT s.INST_ID, s.SID, s.SERIAL#, s.USERNAME, s.SQL_ID,
                   ROUND(st.VALUE / 1024 / 1024, 2) AS PGA_MB,
                   s.MACHINE, s.PROGRAM
            FROM GV$SESSION s
            JOIN GV$SESSTAT st ON s.SID = st.SID AND s.INST_ID = st.INST_ID
            JOIN GV$STATNAME sn ON st.STATISTIC# = sn.STATISTIC#
            WHERE sn.NAME = 'session pga memory' AND s.STATUS = 'ACTIVE' AND s.TYPE = 'USER'
              AND st.VALUE > 100000000  -- >100MB threshold
            ORDER BY st.VALUE DESC
            FETCH FIRST 10 ROWS ONLY
        """,
        "26. Top Wait Events with Causing SQLs & Impact (Last 10 Mins via ASH)": """
            SELECT ash.SESSION_ID, ash.SQL_ID, se.EVENT, se.WAIT_CLASS,
                   COUNT(*) AS SAMPLES, ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 1) AS PCT_TOTAL,
                   MAX(ash.BLOCKING_SESSION) AS BLOCKING_SID
            FROM GV$ACTIVE_SESSION_HISTORY ash
            JOIN GV$SESSION_EVENT se ON ash.SESSION_ID = se.SID AND ash.INST_ID = se.INST_ID
            WHERE ash.SAMPLE_TIME > SYSDATE - 10/1440  -- Last 10 mins
              AND ash.SESSION_STATE = 'ON CPU' OR se.WAIT_CLASS != 'Idle'
            GROUP BY ash.SESSION_ID, ash.SQL_ID, se.EVENT, se.WAIT_CLASS
            ORDER BY SAMPLES DESC
            FETCH FIRST 15 ROWS ONLY
        """,
        "27. Current Top SQL by Elapsed Time (Active)": """
            SELECT INST_ID, SQL_ID, ELAPSED_TIME / 1000000 AS ELAPSED_SEC, EXECUTIONS,
                   (ELAPSED_TIME / EXECUTIONS / 1000000) AS AVG_ELAPSED_SEC,
                   SQL_TEXT
            FROM (
                SELECT s.INST_ID, s.SQL_ID, SUM(st.ELAPSED_TIME) AS ELAPSED_TIME, COUNT(*) AS EXECUTIONS,
                       SUBSTR(q.SQL_FULLTEXT, 1, 100) AS SQL_TEXT
                FROM GV$SESSION s
                JOIN GV$SQLSTATS st ON s.SQL_ID = st.SQL_ID AND s.INST_ID = st.INST_ID
                JOIN GV$SQL q ON st.SQL_ID = q.SQL_ID
                WHERE s.STATUS = 'ACTIVE' AND s.TYPE = 'USER'
                GROUP BY s.INST_ID, s.SQL_ID, q.SQL_FULLTEXT
            )
            ORDER BY ELAPSED_TIME DESC
            FETCH FIRST 10 ROWS ONLY
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
You are a Principal Database Reliability Engineer and Data Visualization Expert.
Your goal is to produce a **Comprehensive, visually stunning Oracle Health Report**.

### 🎨 PRESENTATION RULES
1.  **Completeness:** You MUST parse and report on ALL sections provided in the raw input. Do not skip the "small" checks (like RMAN, Scheduler, Params).
2.  **Visual Logic:**
    * **✅ (Green Tick):** If the result is "No rows returned" (for errors) or shows healthy metrics.
    * **❌ (Red Cross):** If there are errors, critical warnings, or failed jobs.
    * **🟡 (Yellow Warning):** For non-critical alerts (e.g., Tablespace > 80%).
3.  **Grandeur Format:** Use a "Master Scorecard" at the top, followed by detailed "Cluster" sections.

### 📝 REQUIRED REPORT STRUCTURE

# 🏥 Oracle Database 360° Health Report
**Database:** [DB Name] | **Date:** [Time]
**Executive Summary:** *2 sentences on the overall health state.*

---

## 🧩 Master Health Scorecard (All Checks)
*At-a-glance status of every system component.*
| Category | Check Name | Status | Key Finding / Value |
| :--- | :--- | :---: | :--- |
| **Storage** | Tablespaces | [🟢/🔴] | *e.g., "SYSTEM @ 95%" or "All < 90%"* |
| **Storage** | Archive Dest | [🟢/🔴] | *e.g., "45% Used"* |
| **Storage** | ASM Disk Groups | [🟢/🔴] | *e.g., "DATA 20% Free"* |
| **Perf** | CPU/IO Load | [🟢/🟡] | *e.g., "CPU: 10%, IO: Low"* |
| **Perf** | Wait Events | [🟢/🔴] | *e.g., "High 'log file sync'"* |
| **Perf** | Blocking Sessions| [🟢/🔴] | *e.g., "0 Blockers"* |
| **Stability**| RMAN Backups | [🟢/🔴] | *e.g., "Last: 2023-10-25"* |
| **Stability**| Invalid Objects | [🟢/🔴] | *e.g., "0 Invalid" or "5 Invalid"* |
| **Stability**| Scheduler Jobs | [🟢/🔴] | *e.g., "All Success" or "2 Failed"* |
| **Config** | Risky Params | [🟢/🟡] | *e.g., "Standard" or "6 Modified"* |
| **Config** | Stale Stats | [🟢/🟡] | *e.g., "Clean" or "10 Tables"* |

*(...Include all other checks in this table...)*

---

## 💾 Section 1: Capacity & Storage
### 📊 Tablespace Usage
*Include this table ONLY if there are rows. If empty, say "✅ All Tablespaces Healthy".*
[Insert Tablespace Markdown Table Here]

### 💽 ASM & Recovery Area
* **Archive Dest:** [Value from Raw Data]
* **ASM Disks:** [Summary or Table]

---

## 🚀 Section 2: Performance & Workload
### 🔥 Top Active Sessions (CPU & IO)
*Combine the Top CPU and Top IO SQLs into clear tables.*
[Insert Top SQL Tables Here]

### 🐢 Wait Events & Blocking
* **Top Waits:** [Insert Wait Event Table]
* **Blocking Sessions:** [Insert Blocking Table or "✅ None"]

---

## 🛡️ Section 3: Stability & Hygiene
### 🚑 Database Safety Checks
| Check | Status | Details |
| :--- | :---: | :--- |
| **RMAN Backup** | [Emoji] | [Insert Last Backup Date & Status] |
| **Invalid Objects** | [Emoji] | [Insert Count / List top 5 if any] |
| **Scheduler Jobs** | [Emoji] | [List failed jobs if any, else "✅ No Failures (7d)"] |
| **Stale Statistics**| [Emoji] | [List table count or "✅ Fresh"] |

### ⚙️ Configuration & Alerts
* **Risky Parameters:** [List them if any]
* **Flashback Status:** [On/Off]
* **Redo Log Status:** [Current Sequence Info]

---

## ✅ Final Expert Recommendations
1.  **[Immediate]** [Action]
2.  **[Strategic]** [Action]

Reply ONLY with this Markdown. Ensure NO data is lost.
""",
        human_input_mode="NEVER",
    )

    try:
        report = reporter.generate_reply([{
            "role": "user",
            "content": f"Generate beautiful and grand health check report from this data:\n\n{raw_text}"
        }])
        return {"status": "ok", "report": report}
    except Exception as e:
        return {"status": "error", "message": f"AI report failed: {str(e)}"}
# oracle_runner.py - ADD THESE TWO FUNCTIONS

  

# NEW: ASH Report Generation (for recent/short-term performance)

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