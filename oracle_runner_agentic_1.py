# oracle_runner.py (enhanced for AWR reports - fixed syntax error)
import oracledb
import json
import os
from autogen import AssistantAgent
from dotenv import load_dotenv
from datetime import datetime
import pandas as pd
from datetime import datetime
from bs4 import BeautifulSoup

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
You are a **Principal Performance Architect** with 20+ years of Oracle experience.
Your task is to transform raw AWR HTML data into a **Multi-Tiered Executive & Technical Report**.

### 🎨 Report Structure & Visual Style
1.  **Executive Summary (The "30-Second View"):**
    * Use a "Traffic Light" header: 🟢 (Healthy), 🟡 (Warning), 🔴 (Critical).
    * One concise paragraph explaining *Business Impact* (e.g., "End-user latency increased by 15% due to...").
    * **Vital Signs Table:** DB Time, CPU Load, IOPS.

2.  **Top Bottlenecks (The "Why"):**
    * List the top 3 Wait Events.
    * **Visuals:** Use Progress Bars for % DB Time (e.g., `██████░░░░ 60%`).

3.  **Detailed Expert Analysis (The "Deep Dive"):**
    * **CRITICAL:** You MUST use `<details>` and `<summary>` HTML tags to hide raw technical details.
    * Inside the `<details>` block, provide the **SQL_ID**, **Plan Hash**, **P1/P2/P3** values, and **Segment Names**.

### 📝 Required Markdown Output Format:

# 📊 Oracle Performance Master Report
**Verdict:** [🟢/🟡/🔴] **[Short Verdict Title]**

> **Executive Summary:** [2-3 sentences explaining the overall health and user impact.]

---

## 🚦 Vital Signs
| Metric | Value | Status | Interpretation |
| :--- | :--- | :---: | :--- |
| **DB Time** | [Value] | [Emoji] | *[Context]* |
| **CPU Load** | [Value] | [Emoji] | *[Context]* |
| **Avg Active Sessions** | [Value] | [Emoji] | *[Context]* |

---

## 🔥 Top Bottlenecks (Wait Events)
| Event | Class | % DB Time | Avg Wait | Impact |
| :--- | :--- | :--- | :--- | :--- |
| **[Event Name]** | [Class] | **[XX%]** | [XXms] | [Brief Explanation] |

<details>
<summary>🔍 <b>DBA Technical Deep Dive (Wait Params & Histograms)</b></summary>

* **[Event Name]:** P1 (File)=[Val], P2 (Block)=[Val]. Indicates contention on [Segment].
* **[Event Name]:** Wait Class [Class]. Frequent in OLTP during commits.
</details>

---

## 🚀 High-Load SQL Tuning Targets
| Rank | SQL_ID | Plan Hash | Execs | Ela/Exec | Top Wait |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 🥇 | `[SQL_ID]` | [Hash] | [Count] | **[Time]** | [Event] |
| 🥈 | `[SQL_ID]` | [Hash] | [Count] | **[Time]** | [Event] |

<details>
<summary>🛠️ <b>View Full SQL Text & Tuning Advice</b></summary>

* **SQL 1 (`[SQL_ID]`):**
    * **Module:** [Module Name]
    * **Problem:** High CPU due to Full Table Scan on [Table].
    * **Recommendation:** Create index on column X or use SQL Profile.
    * **Text:** `SELECT ... (truncated)`
</details>

---

## 💡 Expert Recommendations
1.  **[Immediate Fix]** [Actionable command/advice]
2.  **[Long Term]** [Architectural advice]

**Reply ONLY in this Markdown format.**
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
        if cur.description:
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            conn.close()
            return rows
        else:
            conn.commit()
            row_count = cur.rowcount
            conn.close()
            return f"Success. Statement executed. Rows affected: {row_count}"
    except Exception as e:
        return {"error": str(e)}

# [Your existing generate_awr_report function - unchanged, truncated for brevity]
def generate_awr_report(start_snap, end_snap, db):
    oracledb.init_oracle_client(lib_dir=r"C:\Users\omkarav\Downloads\instantclient-basiclite-windows.x64-19.28.0.0.0dbru\instantclient_19_28")
    config = DB_CONFIG.get(db.upper(), DB_CONFIG["DEFAULT"])
    try:
        conn = oracledb.connect(user=config["user"], password=config["password"], dsn=config["dsn"])
        cur = conn.cursor()
        
        # Get DBID/Inst
        cur.execute("SELECT DBID FROM V$DATABASE")
        dbid = cur.fetchone()[0]
        cur.execute("SELECT INSTANCE_NUMBER FROM V$INSTANCE")
        inst_num = cur.fetchone()[0]

        sql = f"""
        SELECT * FROM TABLE(DBMS_WORKLOAD_REPOSITORY.AWR_REPORT_HTML({dbid}, {inst_num}, {start_snap}, {end_snap}, 0))
        """
        cur.execute(sql)
        report = "".join([str(row[0]) for row in cur if row[0]])
        conn.close()
        
        if not report: report = "<h3>Empty Report</h3>"
        return {"status": "ok", "report": report, "filename": f"AWR_{start_snap}_{end_snap}.html"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
def analyze_awr_report(html_content: str, custom_prompt: str = None):
    if len(html_content) > 100_000: html_content = html_content[:100_000] + "..."
    user_message = custom_prompt or "Analyze this report and give performance tuning summary."
    try:
        response = awr_analyzer_agent.generate_reply(
            messages=[{"role": "user", "content": user_message + "\n\nReport:\n" + html_content}]
        )
        return {"status": "ok", "analysis": response}
    except Exception as e:
        return {"status": "error", "message": str(e)}
def compare_awr_reports(report1_html: str, report2_html: str, label1: str = "Baseline", label2: str = "Current"):
    import traceback
    print("--- [DEBUG] Starting Compare AWR ---")    
    def clean_html_content(html_content, max_chars=60000):
        try:
            print(f"--- [DEBUG] Cleaning HTML (Size: {len(html_content)} chars) ---")
            soup = BeautifulSoup(html_content, "html.parser")
            # get_text with separator ensures tables don't mash together
            text = soup.get_text(separator="\n")
            # Remove excessive whitespace
            clean_text = "\n".join([line.strip() for line in text.splitlines() if line.strip()])
            print(f"--- [DEBUG] Cleaned Text Size: {len(clean_text)} chars ---")
            return clean_text[:max_chars]
        except Exception:
            print(f"--- [DEBUG] HTML Parsing Error: {e} ---")
            return html_content[:max_chars] # Fallback

    r1_text = clean_html_content(report1_html)
    r2_text = clean_html_content(report2_html)
    print("--- [DEBUG] Initializing Agent ---")
    # --- FIX END ---

    llm_config = {
        "config_list": [{"model": "gpt-4o-mini", "api_key": os.getenv("OPENAI_API_KEY")}],
        "temperature": 0.0,
        "timeout": 600,
    }

    comparator = AssistantAgent(
        name="awr_master_analyzer",
        llm_config=llm_config,
        system_message="""
You are a **Forensic Database Analyst**.
Compare two AWR periods and produce a **Differential Analysis Report** focusing on regressions.

### 🎨 Presentation Style
* **Side-by-Side Comparison:** Use Markdown tables with columns for Baseline, Current, and **Delta**.
* **Highlighting:** Use **BOLD** for regressions > 20%. Use 🔴 for degradation, 🟢 for improvement.
* **Root Cause:** Don't just list numbers; explain *why* the number changed (e.g., "Doubled executions caused increased CPU").

### 📝 Required Output Format:

# ⚖️ AWR Differential Analysis
**Baseline:** {label1} | **Current:** {label2}

## 🏆 Verdict: [IMPROVED / DEGRADED / STABLE]
> **Summary:** [1-2 sentences on the primary shift in workload or performance.]

---

## 📉 Key Metrics Delta
| Metric | Baseline | Current | Delta | Status |
| :--- | :--- | :--- | :--- | :---: |
| **DB Time (min)** | [Val] | [Val] | [+/- %] | [🟢/🔴] |
| **IOPS** | [Val] | [Val] | [+/- %] | [➖] |
| **Logons/Sec** | [Val] | [Val] | [+/- %] | [Info] |

---

## 🐢 Wait Event Changes (Top Regressions)
*Events that consumed significantly more time in the Current period.*

| Event Name | Base %DBT | Curr %DBT | Change |
| :--- | :--- | :--- | :--- |
| **[Event A]** | [XX%] | [XX%] | ⬆️ **[Massive Increase]** |

---

## 💥 SQL Regressions (The Culprits)
*SQLs where performance degraded significantly.*

| SQL_ID | Base Ela/Exec | Curr Ela/Exec | Degraded By |
| :--- | :--- | :--- | :--- |
| `[SQL_ID]` | [Time] | [Time] | 🔴 **[XX%]** |

<details>
<summary>🕵️ <b>Technical Root Cause Analysis</b></summary>
* **[SQL_ID]:** Execution count increased by [X]x. Plan hash changed from [Old] to [New].
</details>
""",
        human_input_mode="NEVER",
    )

    prompt = f"""
COMPARE THESE TWO ORACLE AWR REPORT PERIODS.

BASELINE PERIOD ({label1}):
{r1_text}

CURRENT PERIOD ({label2}):
{r2_text}

Follow the exact structure above. Focus on Wait Events, Top SQL by Elapsed Time, and Load Profile.
"""

    try:
        print("--- [DEBUG] Sending to LLM... ---")
        response = comparator.generate_reply([{"role": "user", "content": prompt}])
        print(f"--- [DEBUG] LLM Response Received (Type: {type(response)}) ---")
        # Safety check: Ensure we got a string back
        if not response:
            return {"status": "error", "message": "AI returned empty response."}
            
        return {"status": "ok", "comparison": str(response)}
    except Exception as e:
        print(f"--- [DEBUG] CRITICAL FAILURE: {e} ---")
        return {"status": "error", "message": f"Comparison failed: {str(e)}"}
def get_snapshots_for_time(hours_back: float = 3.0, db: str = "DEFAULT"):
    """
    Returns (start_snap, end_snap) covering the full duration.
    Uses SYSDATE to calculate the target start time relative to Server Time.
    """
    try:
        val = float(hours_back)
    except:
        val = 1.0

    print(f"[DEBUG] Fetching snapshots for last {val} hours on {db}")

    # 1. Get the LATEST Snapshot (End Point)
    sql_end = "SELECT MAX(snap_id) FROM dba_hist_snapshot"
    rows_end = run_oracle_query(sql_end, db)
    if not rows_end or not rows_end[0].get('MAX(SNAP_ID)'):
        return None, None
    end_snap = rows_end[0]['MAX(SNAP_ID)']

    # 2. Get the START Snapshot (Closest to Now - Hours)
    # We find the snapshot that ended just before our target start time.
    sql_start = f"""
    SELECT MAX(snap_id) 
    FROM dba_hist_snapshot 
    WHERE end_interval_time <= (SYSDATE - ({int(val)}/24))
    """
    rows_start = run_oracle_query(sql_start, db)
    start_snap = rows_start[0].get('MAX(SNAP_ID)')

    # 3. Fallback: If DB was restarted or no history that far back, take the oldest available
    if not start_snap:
        print("[DEBUG] No snapshot found older than target. Using oldest available.")
        sql_min = "SELECT MIN(snap_id) FROM dba_hist_snapshot"
        rows_min = run_oracle_query(sql_min, db)
        start_snap = rows_min[0].get('MIN(SNAP_ID)')

    # 4. Safety: Start must be < End
    if start_snap and end_snap and start_snap >= end_snap:
        # If collision (e.g. asking for 5 mins ago but snaps are 15 mins), just grab previous one
        start_snap = end_snap - 1

    print(f"[DEBUG] Final Range: {start_snap} -> {end_snap}")

    if start_snap and end_snap and start_snap < end_snap:
        return int(start_snap), int(end_snap)
    
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
        "1. Tablespace Usage >90% ( also included temp and undo utilization as well)": """
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
        "2. Long Running Operations (>2 hours)": """
           SELECT INST_ID, SID, SERIAL#, USERNAME, SQL_ID,
                   ROUND((SYSDATE - LOGON_TIME)*24, 2) AS HOURS_CONNECTED,
                   PROGRAM
            FROM GV$SESSION
            WHERE LOGON_TIME < SYSDATE - 2/24
              AND TYPE = 'USER'
              AND STATUS = 'ACTIVE'
              AND USERNAME IS NOT NULL
        """,
        "3. Current Blocking Sessions": """
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
        "4. Top Wait Events Right Now": """
          SELECT INST_ID, EVENT, WAIT_CLASS,
                   ROUND(TIME_WAITED_MICRO/1000000, 1) AS WAIT_SEC
            FROM GV$SESSION_EVENT
            WHERE WAIT_CLASS != 'Idle'
              AND SID IN (SELECT SID FROM GV$SESSION WHERE TYPE='USER')
            ORDER BY WAIT_SEC DESC FETCH FIRST 15 ROWS ONLY
        """,
        "5. Invalid Objects": """
            SELECT OWNER, OBJECT_TYPE, COUNT(*) AS COUNT
            FROM DBA_OBJECTS WHERE STATUS = 'INVALID'
            GROUP BY OWNER, OBJECT_TYPE ORDER BY COUNT DESC
        """,
        "6. Failed Scheduler Jobs (last 7d)": """
            SELECT OWNER, JOB_NAME, TO_CHAR(LOG_DATE, 'YYYY-MM-DD HH24:MI') AS FAILED_AT, ERROR#
            FROM DBA_SCHEDULER_JOB_RUN_DETAILS
            WHERE STATUS = 'FAILED' AND LOG_DATE > SYSDATE - 7
            ORDER BY LOG_DATE DESC
        """,
        "7. Last Successful RMAN Backup": """
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
        "8. Stale/Missing Statistics": """
            SELECT OWNER, TABLE_NAME, LAST_ANALYZED, STALE_STATS
            FROM DBA_TAB_STATISTICS
            WHERE (STALE_STATS = 'YES' OR LAST_ANALYZED IS NULL)
              AND OWNER NOT IN ('SYS', 'SYSTEM')
              AND NUM_ROWS > 1000000
            ORDER BY LAST_ANALYZED NULLS FIRST FETCH FIRST 20 ROWS ONLY
        """,
        "9. Disabled/Unusable Indexes": """
            SELECT OWNER, INDEX_NAME, STATUS FROM DBA_INDEXES WHERE STATUS NOT IN ('VALID', 'N/A')
        """,
        "10. Risky Parameters": """
            SELECT NAME, VALUE, ISDEFAULT, ISMODIFIED
            FROM gV$PARAMETER
            WHERE NAME IN ('sga_target', 'pga_aggregate_target', 'optimizer_mode', 'db_file_multiblock_read_count',
                           'processes', 'open_cursors', 'cursor_sharing', 'recyclebin')
        """,
        "11. ADDM Findings (last 3)": """
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
        "12. High Water Mark Issues (Top 10)": """
            SELECT OWNER, TABLE_NAME, ROUND(BLOCKS * 8 / 1024) AS MB_ALLOCATED,
                   ROUND(NUM_ROWS * AVG_ROW_LEN / 1024 / 1024) AS MB_USED
            FROM DBA_TABLES
            WHERE BLOCKS > 10000 AND NUM_ROWS > 0
            ORDER BY (BLOCKS * 8 / 1024) DESC FETCH FIRST 10 ROWS ONLY
        """,
        "13. Redo for Redo": """
            SELECT GROUP#, STATUS, ARCHIVED, THREAD#, SEQUENCE#
            FROM V$LOG WHERE STATUS = 'CURRENT'
        """,
        "14. Flashback On?": """
            SELECT FLASHBACK_ON FROM V$DATABASE
        """,
        "15. Archive Destination Full": """
            SELECT 
    round(space_limit / 1024 / 1024 / 1024, 2) AS total_gb,
    round(space_used  / 1024 / 1024 / 1024, 2) AS used_gb,
    round((space_used/space_limit)*100, 2) AS pct_used
FROM v$recovery_file_dest
        """,
        "16. ASM Disk Group Usage": """
            SELECT NAME, ROUND(USABLE_FILE_MB / 1024) AS FREE_GB, STATE
            FROM V$ASM_DISKGROUP
        """,
        "17. Sessions Near Limit": """
          SELECT
    (SELECT COUNT(*) FROM gv$session) AS current_sessions,
    (SELECT value FROM gv$parameter WHERE name = 'processes' AND rownum = 1) AS max_processes
FROM dual
        """,
        "18. CPU/IO Saturation": """
            SELECT 'DB CPU' AS METRIC, VALUE/100 AS VALUE FROM gV$SYSMETRIC WHERE METRIC_NAME = 'Database CPU Time Ratio'
            UNION ALL
            SELECT 'Host CPU Utilization (%)', VALUE FROM gV$SYSMETRIC WHERE METRIC_NAME = 'Host CPU Utilization'
            UNION ALL
            SELECT 'I/O Megabytes per Second', VALUE FROM gV$SYSMETRIC WHERE METRIC_NAME = 'I/O Megabytes per Second'
        """,
        "19. Top Active Queries by CPU (Current)": """
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
        "20. Top Active Queries by IO (Current)": """
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
        "21. High Memory Users (PGA/SGA - Current)": """
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
        "22. Top Wait Events with Causing SQLs & Impact (Last 10 Mins via ASH)": """
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
        "23. Current Top SQL by Elapsed Time (Active)": """
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
            results[name] = data if data else "STATUS: Healthy (No issues found)"
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
        You are a **Senior Principal Oracle Architect** generating a comprehensive health audit.
Your output must be **Visually Stunning**, **Technically Precise**, and **Formatted for Perfect Markdown Rendering**.

### 🎨 Report Guidelines
1.  **Format:** Use strict Markdown. **IMPORTANT:** Every table must be preceded and followed by a blank line to ensure it renders correctly.
2.  **Professional Findings:** * If a check is 🟢 (Healthy), do **NOT** write "No data" or "Empty". 
    * Instead, write professional confirmations like: "No blocking sessions detected," "All tablespaces within limits," or "System clear; no failed jobs in 7 days."
3.  **Completeness:** You MUST include the "Complete Health Matrix" for all 11 checks.

### 📝 Required Markdown Structure:

# 🏥 System Health Dashboard: [DB Name]
**Date:** [Date] | **Overall Verdict:** [🟢/🟡/🔴]
> **Executive Summary:** *[2 sentences on major risks or confirming clean health.]*

---

## 🚦 Complete Health Matrix (All Checks)
| Check Category | Status | Findings |
| :--- | :---: | :--- |
| **1. Tablespace Usage** | [Emoji] | [Descriptive finding] |
| **2. Long Running Ops** | [Emoji] | [Descriptive finding] |
| **3. Blocking Sessions** | [Emoji] | [Descriptive finding] |
| **4. Top Wait Events** | [Emoji] | [Descriptive finding] |
| **5. Invalid Objects** | [Emoji] | [Descriptive finding] |
| **6. Failed Jobs** | [Emoji] | [Descriptive finding] |
| **7. RMAN Backup** | [Emoji] | [Descriptive finding] |
| **8. Stale Statistics** | [Emoji] | [Descriptive finding] |
| **9. Disabled Indexes** | [Emoji] | [Descriptive finding] |
| **10. Risky Parameters** | [Emoji] | [Descriptive finding] |
| **11. ADDM Findings** | [Emoji] | [Descriptive finding] |
| **12. HWM Issues** | [Emoji] | [Finding] |
| **13. Redo Status** | [Emoji] | [Finding] |
| **14. Flashback Status**| [Emoji] | [Finding] |
| **15. Archive Dest Full**| [Emoji] | [Finding] |
| **16. ASM Disk Usage** | [Emoji] | [Finding] |
| **17. Session Limits** | [Emoji] | [Finding] |
| **18. CPU/IO Saturation**| [Emoji] | [Finding] |
| **19. Resource Hogs** | [Emoji] | [Finding] |
| **20. PGA/Memory Usage**| [Emoji] | [Finding] |
---

## 🚨 Critical Action Items
*Only list items with status 🔴 or 🟡.*
* **[Category Name]**
    * **Issue:** [Specific detail]
    * **Fix:** `[Code/Command]`

---

## 📊 Deep Dive & Evidence

### 1. 💾 Storage analysis
[Insert Markdown table for Tablespace & ASM data. Ensure blank lines around it.]

### 2. ⚡ Recent Performance (Last 10-25 Mins) 
*Based on ASH and real-time metrics (Queries 19-23):*

### 🚀 Top CPU/IO Consumers
[Insert Markdown table showing Top SQL_ID, CPU_SEC, and IO_READS from the data.]

### 🔍 Active Wait Events (ASH)
[Insert Markdown table showing Event, SAMPLES, and PCT_TOTAL from Query 22.]

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

  

# NEW: ASH Report Generation (for recent/short-term performance)

def generate_ash_report(minutes: int = 30, db: str = "DEFAULT"):
    oracledb.init_oracle_client(lib_dir=r"C:\Users\omkarav\Downloads\instantclient-basiclite-windows.x64-19.28.0.0.0dbru\instantclient_19_28")
    config = DB_CONFIG.get(db.upper(), DB_CONFIG["DEFAULT"])
    try:
        conn = oracledb.connect(user=config["user"], password=config["password"], dsn=config["dsn"])
        cur = conn.cursor()
        
        cur.execute("SELECT DBID FROM V$DATABASE")
        dbid = cur.fetchone()[0]
        cur.execute("SELECT INSTANCE_NUMBER FROM V$INSTANCE")
        inst_num = cur.fetchone()[0]

        sql = f"""
        SELECT OUTPUT FROM TABLE(DBMS_WORKLOAD_REPOSITORY.ASH_REPORT_HTML(:dbid, :inst_num, SYSDATE - ({minutes}/1440), SYSDATE, 0))
        """
        cur.execute(sql, dbid=dbid, inst_num=inst_num)
        report = "".join([str(row[0]) for row in cur if row[0]])
        conn.close()
        return {"status": "ok", "report": report, "filename": f"ASH_{minutes}m.html"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def get_snapshots_by_date_range(start_time, end_time, db):
    sql = f"""
    SELECT MIN(snap_id), MAX(snap_id) FROM dba_hist_snapshot
    WHERE begin_interval_time >= TO_TIMESTAMP('{start_time}', 'YYYY-MM-DD HH24:MI:SS')
      AND end_interval_time <= TO_TIMESTAMP('{end_time}', 'YYYY-MM-DD HH24:MI:SS')
    """
    rows = run_oracle_query(sql, db)
    if rows and rows[0].get("MIN(SNAP_ID)"):
        return [{'snap_id': rows[0]["MIN(SNAP_ID)"]}, {'snap_id': rows[0]["MAX(SNAP_ID)"]}]
    return None


    # 1. Construct SQL to find min/max snap_id within the window
