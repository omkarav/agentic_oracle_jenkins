# oracle_runner.py
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
        "PLAB_VES": {
            "user": "dbcheck",  # Placeholder - update with real creds
            "password": "password_placeholder",
            "dsn": "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=plab-host.example.com)(PORT=1521))(CONNECT_DATA=(SERVICE_NAME=PLAB_CM)))"  # Placeholder DSN
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