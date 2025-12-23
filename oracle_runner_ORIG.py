# oracle_runner.py
import json
import oracledb
import os

# --------------------------------------------------
# FORCE THICK MODE AT IMPORT TIME
# --------------------------------------------------
# Ensure this path is correct on your machine!
try:
    oracledb.init_oracle_client(
        lib_dir=r"C:\Users\omkarav\Downloads\instantclient-basiclite-windows.x64-19.28.0.0.0dbru\instantclient_19_28"
    )
except Exception as e:
    print(f"Warning: Oracle Instant Client init failed (might be already initialized): {e}")

# --------------------------------------------------
# Load DB config file
# --------------------------------------------------
def load_db_config(path="db_config.json"):
    # Check if file exists relative to the script
    if not os.path.exists(path):
        # Try absolute path if running from different context
        current_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(current_dir, "db_config.json")
        
    if not os.path.exists(path):
        return {}
        
    with open(path, "r") as f:
        return json.load(f)

# --------------------------------------------------
# Run SQL on correct DB
# --------------------------------------------------
def run_oracle_query(sql: str, db="DEFAULT"):
    dbs = load_db_config()
    
    if db not in dbs:
        return {"error": f"DB '{db}' not found in db_config.json. Available: {list(dbs.keys())}"}

    cfg = dbs[db]

    try:
        conn = oracledb.connect(
            user=cfg["user"],
            password=cfg["password"],
            dsn=cfg["dsn"]
        )
        cur = conn.cursor()
        cur.execute(sql)
        
        if cur.description:
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            conn.close()
            return rows
        else:
            # For INSERT/UPDATE/DELETE statements
            conn.commit()
            conn.close()
            return [{"status": "Success", "message": "Statement executed, no rows returned."}]

    except Exception as e:
        return {"error": str(e)}