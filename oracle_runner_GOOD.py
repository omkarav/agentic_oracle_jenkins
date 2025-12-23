# oracle_runner.py (enhanced for AWR reports - fixed syntax error)
import oracledb
import json
import os

# Config file path - adjust as needed; can be relative or absolute
CONFIG_FILE = "db_config.json"

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