"""
Oracle + Jenkins Agentic Console v4.0
Enhanced with security fixes, new DBA features, and improved UI
"""

import time
import os
import json
import pandas as pd
import streamlit as st
import jenkins
import re
import uuid
import hashlib
import threading
from datetime import datetime, timedelta
from difflib import get_close_matches
from dotenv import load_dotenv
from typing import Dict, List, Optional, Tuple
from bs4 import BeautifulSoup

# Multi-user support: File locking for concurrent access (cross-platform)
# Uses threading locks (universal) + fcntl file locks (Unix/Linux if available)
# Threading locks provide thread-safety, fcntl adds file-level protection on Unix/Linux
try:
    import fcntl  # Available on Unix/Linux systems
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False  # Windows or other systems without fcntl - threading locks are sufficient

# Global lock for database config file operations
_config_lock = threading.Lock()

from autogen import AssistantAgent, UserProxyAgent, register_function

from oracle_runner_agentic_1 import (
    run_oracle_query, get_db_list, generate_awr_report, generate_ash_report,
    get_snapshots_for_time, run_full_health_check, get_snapshots_by_date_range, 
    analyze_awr_report, compare_awr_reports, load_db_config
)
import oracle_runner_agentic_1
from patch_forstreamlit import download_oracle_patch

# ============================================================================
# 1. CONFIGURATION & INITIALIZATION
# ============================================================================

load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")

if not openai_api_key:
    st.error("CRITICAL: OPENAI_API_KEY not found in .env")
    st.stop()

# Security: Move credentials to environment variables
JENKINS_URL = os.getenv("JENKINS_URL", "http://localhost:9020")
JENKINS_USERNAME = os.getenv("JENKINS_USERNAME", "dba")
JENKINS_TOKEN = os.getenv("JENKINS_API_TOKEN", os.getenv("JENKINS_PASSWORD", "113bb934053435f19fa62d94f8c79a108c"))

# SSL Certificates (make configurable)
SSL_CERT_PATH = os.getenv("SSL_CERT_PATH", r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt")
if os.path.exists(SSL_CERT_PATH):
    os.environ["REQUESTS_CA_BUNDLE"] = SSL_CERT_PATH
    os.environ["SSL_CERT_FILE"] = SSL_CERT_PATH

st.set_page_config(page_title="Oracle + Jenkins Agentic Console v4.0", layout="wide", page_icon="🤖")

# ============================================================================
# 2. SESSION STATE INITIALIZATION
# ============================================================================

if "dbs" not in st.session_state: 
    st.session_state["dbs"] = get_db_list()
if "current_db" not in st.session_state: 
    st.session_state["current_db"] = st.session_state["dbs"][0] if st.session_state["dbs"] else "DEFAULT"
if "messages" not in st.session_state: 
    st.session_state["messages"] = []
if "awr_history" not in st.session_state: 
    st.session_state["awr_history"] = []
if "health_report" not in st.session_state: 
    st.session_state["health_report"] = None
if "awr_compare" not in st.session_state: 
    st.session_state["awr_compare"] = None
if "artifacts" not in st.session_state: 
    st.session_state["artifacts"] = {}
if "saved_queries" not in st.session_state: 
    st.session_state["saved_queries"] = []
if "baselines" not in st.session_state: 
    st.session_state["baselines"] = []
if "alerts" not in st.session_state: 
    st.session_state["alerts"] = []
if "historical_metrics" not in st.session_state: 
    st.session_state["historical_metrics"] = None
if "sql_perf_data" not in st.session_state:
    st.session_state["sql_perf_data"] = None
if "table_sql_data" not in st.session_state:
    st.session_state["table_sql_data"] = None
if "top_tables_data" not in st.session_state:
    st.session_state["top_tables_data"] = None
if "welcome_message_seen" not in st.session_state:
    st.session_state["welcome_message_seen"] = False
if "perf_time_range_selected" not in st.session_state:
    st.session_state["perf_time_range_selected"] = "1 month"
if "show_awr_compare_form" not in st.session_state:
    st.session_state["show_awr_compare_form"] = False
if "show_perf_report_form" not in st.session_state:
    st.session_state["show_perf_report_form"] = False
if "audit_log" not in st.session_state: 
    st.session_state["audit_log"] = []

# Jenkins State
if "job_map" not in st.session_state: 
    st.session_state["job_map"] = []
if "jenkins_matches" not in st.session_state: 
    st.session_state["jenkins_matches"] = []
if "polling_active" not in st.session_state: 
    st.session_state["polling_active"] = False
if "polling_job" not in st.session_state: 
    st.session_state["polling_job"] = None
if "polling_queue_id" not in st.session_state: 
    st.session_state["polling_queue_id"] = None
if "polling_build" not in st.session_state: 
    st.session_state["polling_build"] = None
if "show_jenkins_build_history" not in st.session_state:
    st.session_state["show_jenkins_build_history"] = False
if "jenkins_build_history_data" not in st.session_state:
    st.session_state["jenkins_build_history_data"] = None
# Jenkins form states
if "show_search_jenkins_form" not in st.session_state:
    st.session_state["show_search_jenkins_form"] = False
if "show_build_info_form" not in st.session_state:
    st.session_state["show_build_info_form"] = False
if "show_build_console_form" not in st.session_state:
    st.session_state["show_build_console_form"] = False
if "show_trigger_build_form" not in st.session_state:
    st.session_state["show_trigger_build_form"] = False
if "show_build_history_form" not in st.session_state:
    st.session_state["show_build_history_form"] = False
if "show_analyze_failure_form" not in st.session_state:
    st.session_state["show_analyze_failure_form"] = False
if "show_job_config_form" not in st.session_state:
    st.session_state["show_job_config_form"] = False
if "show_download_patch_form" not in st.session_state:
    st.session_state["show_download_patch_form"] = False
if "show_build_artifacts_form" not in st.session_state:
    st.session_state["show_build_artifacts_form"] = False

# UI State
if "current_tab" not in st.session_state: 
    st.session_state["current_tab"] = "Chat"
if "system_message_base" not in st.session_state: 
    st.session_state["system_message_base"] = None

# ============================================================================
# 3. SECURITY & VALIDATION FUNCTIONS
# ============================================================================

def validate_sql_query(sql: str) -> Dict[str, any]:
    """Validates SQL for dangerous operations - Security Fix"""
    sql_upper = sql.upper().strip()
    
    # Only allow SELECT, WITH, EXPLAIN PLAN queries
    allowed_starters = ['SELECT', 'WITH', 'EXPLAIN']
    if not any(sql_upper.startswith(starter) for starter in allowed_starters):
        return {"valid": False, "reason": "Only SELECT, WITH, and EXPLAIN PLAN queries are allowed"}
    
    # Check for dangerous keywords
    dangerous_keywords = ['DROP', 'TRUNCATE', 'DELETE', 'ALTER', 'GRANT', 'REVOKE', 
                         'INSERT', 'UPDATE', 'CREATE', 'EXEC', 'EXECUTE', 'CALL']
    for keyword in dangerous_keywords:
        if keyword in sql_upper:
            return {"valid": False, "reason": f"Dangerous keyword detected: {keyword}. Only read-only queries allowed."}
    
    return {"valid": True}

def handle_oracle_error(error: Exception) -> str:
    """Parse Oracle errors and provide actionable feedback"""
    error_str = str(error)
    
    error_messages = {
        "ORA-00942": "❌ Table or view does not exist. Check table name and schema.",
        "ORA-00904": "❌ Invalid column name. Verify column exists in the table.",
        "ORA-00054": "⚠️ Resource busy. Table is locked. Try again in a moment.",
        "ORA-01017": "🔒 Invalid username/password. Check credentials.",
        "ORA-00933": "❌ SQL command not properly ended. Check syntax.",
        "ORA-00936": "❌ Missing expression. Check your SQL syntax.",
    }
    
    for ora_code, message in error_messages.items():
        if ora_code in error_str:
            return message
    
    return f"❌ Database Error: {error_str}"

def audit_log(action: str, db: str, details: Dict) -> None:
    """Log all database operations for compliance"""
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "database": db,
        "details": details
    }
    st.session_state["audit_log"].append(log_entry)
    # Keep only last 1000 entries
    if len(st.session_state["audit_log"]) > 1000:
        st.session_state["audit_log"] = st.session_state["audit_log"][-1000:]

# ============================================================================
# 4. HELPER FUNCTIONS
# ============================================================================

def read_db_config_safe(config_file: str = "db_config.json") -> dict:
    """Thread-safe read of database config file with retry logic for multi-user support"""
    max_retries = 5
    retry_delay = 0.1
    
    for attempt in range(max_retries):
        try:
            with _config_lock:
                if not os.path.exists(config_file):
                    return {}
                
                # Try to acquire file lock and read
                with open(config_file, 'r') as f:
                    # Apply file-level locking if available (Unix/Linux)
                    # Threading lock already provides thread-safety on all platforms
                    if HAS_FCNTL:
                        fcntl.flock(f.fileno(), fcntl.LOCK_SH)  # Shared lock for reading
                    
                    try:
                        config = json.load(f)
                    finally:
                        if HAS_FCNTL:
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    
                    return config
        except (IOError, OSError, json.JSONDecodeError) as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))  # Exponential backoff
                continue
            else:
                st.warning(f"Could not read config file after {max_retries} attempts: {e}")
                return {}
    
    return {}

def save_database_config(db_name: str, username: str, password: str, host: str, port: str, service_name: str = None, sid: str = None):
    """Save database configuration to db_config.json file with thread-safe locking for multi-user support"""
    config_file = "db_config.json"
    
    # Build DSN string
    if service_name:
        connect_data = f"(CONNECT_DATA=(SERVICE_NAME={service_name}))"
    elif sid:
        connect_data = f"(CONNECT_DATA=(SID={sid}))"
    else:
        return {"success": False, "message": "Either Service Name or SID must be provided"}
    
    dsn = f"(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST={host})(PORT={port})){connect_data})"
    
    # Thread-safe read existing config with retry
    existing_config = read_db_config_safe(config_file)
    
    # Add new database config
    existing_config[db_name.upper()] = {
        "user": username,
        "password": password,
        "dsn": dsn
    }
    
    # Thread-safe write with file locking
    max_retries = 5
    retry_delay = 0.1
    
    for attempt in range(max_retries):
        try:
            with _config_lock:
                # Write to temporary file first, then rename (atomic operation - cross-platform)
                temp_file = f"{config_file}.tmp"
                with open(temp_file, 'w') as f:
                    # Apply file-level locking if available (Unix/Linux)
                    # Threading lock already provides thread-safety on all platforms
                    if HAS_FCNTL:
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # Exclusive lock for writing
                    
                    try:
                        json.dump(existing_config, f, indent=4)
                        f.flush()
                        os.fsync(f.fileno())  # Force write to disk
                    finally:
                        if HAS_FCNTL:
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                
                # Atomic rename (cross-platform: os.replace on Python 3.3+, os.rename otherwise)
                if os.path.exists(config_file):
                    os.replace(temp_file, config_file)  # Atomic on both Unix and Windows
                else:
                    os.rename(temp_file, config_file)  # Fallback for older Python versions
                
                # Reload database list directly from the JSON file (not from cached DB_CONFIG)
                # This ensures we get the latest databases
                st.session_state["dbs"] = list(existing_config.keys())
                
                # Also reload the DB_CONFIG in oracle_runner_agentic_1 module so queries work immediately
                try:
                    oracle_runner_agentic_1.DB_CONFIG = load_db_config()
                except Exception as e:
                    # Non-critical - the dropdown will still work, just queries might need app restart
                    pass
                
                return {"success": True, "message": f"Database '{db_name.upper()}' added successfully", "db_list": list(existing_config.keys())}
                
        except (IOError, OSError) as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))  # Exponential backoff
                # Clean up temp file if it exists
                if os.path.exists(f"{config_file}.tmp"):
                    try:
                        os.remove(f"{config_file}.tmp")
                    except:
                        pass
                continue
            else:
                return {"success": False, "message": f"Failed to save config after {max_retries} attempts: {str(e)}"}
    
    return {"success": False, "message": "Failed to save config: Maximum retries exceeded"}

def refresh_database_list():
    """Refresh database list from config file (for multi-user support - see databases added by others)"""
    try:
        # Read directly from file (thread-safe)
        config = read_db_config_safe()
        if config:
            st.session_state["dbs"] = list(config.keys())
            # Also update the oracle_runner module's DB_CONFIG
            try:
                oracle_runner_agentic_1.DB_CONFIG = load_db_config()
            except:
                pass
            return True
    except Exception as e:
        st.error(f"Error refreshing database list: {e}")
        return False
    return False

@st.cache_resource
def get_jenkins_server():
    """Get Jenkins server connection - Security Fix: Uses env vars"""
    try:
        return jenkins.Jenkins(JENKINS_URL, username=JENKINS_USERNAME, password=JENKINS_TOKEN)
    except Exception as e:
        st.error(f"Jenkins Connection Error: {e}")
        return None

def fetch_jobs_recursive(_client, folder=""):
    if _client is None: 
        return []
    try:
        items = _client.get_jobs(folder) if folder else _client.get_jobs()
    except: 
        return []
    out = []
    for it in items:
        name = it.get("name")
        if not name: 
            continue
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
    if _client is None: 
        return []
    out = []
    job_names = fetch_jobs_recursive(_client)
    
    for full in job_names:
        try:
            info = _client.get_job_info(full)
        except: 
            continue
            
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
                    if p_name in seen: 
                        continue
                    seen.add(p_name)
                    choices = p.get("choices", [])
                    if not choices: 
                        choices = p.get("allValue", p.get("values", []))
                    
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

# Helper function for ASH report with specific time range
def generate_ash_report_specific_range(start_time: str, end_time: str, db: str):
    """Generate ASH report for specific time range - Fix for missing function"""
    try:
        fmt = "%Y-%m-%d %H:%M:%S"
        t1 = datetime.strptime(start_time, fmt)
        t2 = datetime.strptime(end_time, fmt)
        duration_minutes = int((t2 - t1).total_seconds() / 60.0)
        
        # Use the existing generate_ash_report function with calculated minutes
        return generate_ash_report(duration_minutes, db)
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ============================================================================
# 5. TOOL DEFINITIONS (AGENTIC) - ENHANCED
# ============================================================================

def tool_get_database_info(show_details: bool = False) -> str:
    """Get current database information - returns just name by default"""
    db = st.session_state["current_db"]
    sql = """
    SELECT 
        name as database_name,
        dbid,
        created,
        log_mode,
        open_mode,
        database_role,
        platform_name
    FROM v$database
    """
    try:
        result = run_oracle_query(sql, db)
        if isinstance(result, list) and result:
            info = result[0]
            # Handle both uppercase and lowercase column names
            db_name = info.get('DATABASE_NAME') or info.get('database_name') or info.get('NAME') or info.get('name')
            
            if show_details:
                return f"""**Database Information:**
- **Name:** {db_name}
- **DBID:** {info.get('DBID', info.get('dbid', 'N/A'))}
- **Created:** {info.get('CREATED', info.get('created', 'N/A'))}
- **Log Mode:** {info.get('LOG_MODE', info.get('log_mode', 'N/A'))}
- **Open Mode:** {info.get('OPEN_MODE', info.get('open_mode', 'N/A'))}
- **Role:** {info.get('DATABASE_ROLE', info.get('database_role', 'N/A'))}
- **Platform:** {info.get('PLATFORM_NAME', info.get('platform_name', 'N/A'))}
- **Current Context:** {db}"""
            else:
                # Just return the database name
                return f"The database name is: **{db_name}**"
        return f"Current database context: {db}"
    except Exception as e:
        return f"Error getting database info: {handle_oracle_error(e)}"

def tool_change_database(target_name: str) -> str:
    available = st.session_state["dbs"]
    target_name = target_name.upper().strip()
    
    found_db = None
    if target_name in available:
        found_db = target_name
    else:
        matches = get_close_matches(target_name, available, n=1, cutoff=0.4)
        if matches:
            found_db = matches[0]

    if found_db:
        st.session_state["current_db"] = found_db
        audit_log("DB_SWITCH", found_db, {"from": st.session_state.get("previous_db"), "to": found_db})
        return f"SUCCESS: Switched the current database context to '{found_db}'. Please re-run your query."
        
    return f"FAILURE: DB '{target_name}' not found. Available databases: {available}"

def tool_run_sql(sql_query: str) -> str:
    """Run SQL query with validation - Security Fix"""
    db = st.session_state["current_db"]
    
    # Validate SQL
    validation = validate_sql_query(sql_query)
    if not validation["valid"]:
        return f"SECURITY ERROR: {validation['reason']}"
    
    try:
        sql_query = sql_query.strip().rstrip(";")
        
        # Audit log
        sql_hash = hashlib.md5(sql_query.encode()).hexdigest()[:8]
        audit_log("SQL_QUERY", db, {"sql_hash": sql_hash, "query_preview": sql_query[:100]})
        
        result = run_oracle_query(sql_query, db)
        if isinstance(result, list):
            if not result: 
                return "Query executed. No rows returned."
            df = pd.DataFrame(result)
            return f"**SQL Result ({len(df)} rows):**\n{df.head(100).to_markdown(index=False)}"
        elif isinstance(result, dict) and "error" in result:
            error_msg = handle_oracle_error(Exception(result['error']))
            return f"SQL Error: {error_msg}"
        return str(result)
    except Exception as e:
        error_msg = handle_oracle_error(e)
        return f"Exception: {error_msg}"

def tool_search_jenkins_jobs(search_term: str) -> str:
    query = search_term.lower()
    matches = []
    for j in st.session_state["job_map"]:
        if query in j["name"].lower():
            matches.append(j["name"])
    
    if not matches: 
        return f"No jobs found matching '{search_term}'."
    search_id = str(uuid.uuid4())
    st.session_state["artifacts"][search_id] = {
        "type": "JENKINS_SELECT",
        "matches": matches[:15],
        "timestamp": datetime.now().strftime("%H:%M")
    }
    return f"I found {len(matches)} jobs matching '{search_term}'. ::ARTIFACT_JENKINS:{search_id}:: TERMINATE"

def tool_get_build_info(job_name: str, build_number: int = None) -> str:
    """Get detailed information about a Jenkins build. If build_number is not provided, gets the latest build."""
    try:
        server = get_jenkins_server()
        if server is None:
            return "FAILURE: Could not connect to Jenkins server."
        
        if build_number is None:
            # Get latest build number
            job_info = server.get_job_info(job_name)
            if not job_info.get("builds"):
                return f"FAILURE: No builds found for job '{job_name}'."
            build_number = job_info["builds"][0]["number"]
        
        build_info = server.get_build_info(job_name, build_number)
        
        result = f"**Build Information for {job_name} #{build_number}**\n\n"
        result += f"- **Status:** {build_info.get('result', 'IN PROGRESS')}\n"
        result += f"- **Duration:** {build_info.get('duration', 0) / 1000:.2f} seconds\n"
        result += f"- **Timestamp:** {datetime.fromtimestamp(build_info.get('timestamp', 0) / 1000).strftime('%Y-%m-%d %H:%M:%S')}\n"
        result += f"- **Built By:** {', '.join(build_info.get('actions', [{}])[0].get('causes', [{}])[0].get('userName', ['Unknown'])) if build_info.get('actions') else 'Unknown'}\n"
        result += f"- **URL:** {build_info.get('url', 'N/A')}\n"
        
        # Store as artifact
        artifact_id = str(uuid.uuid4())
        st.session_state["artifacts"][artifact_id] = {
            "type": "JENKINS_BUILD_INFO",
            "job_name": job_name,
            "build_number": build_number,
            "build_info": build_info,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }
        
        return f"SUCCESS: {result} ::ARTIFACT_JENKINS_BUILD:{artifact_id}::"
    except Exception as e:
        return f"FAILURE: Error getting build info: {str(e)}"

def tool_get_build_console(job_name: str, build_number: int = None) -> str:
    """Get console output for a Jenkins build. If build_number is not provided, gets the latest build."""
    try:
        server = get_jenkins_server()
        if server is None:
            return "FAILURE: Could not connect to Jenkins server."
        
        if build_number is None:
            job_info = server.get_job_info(job_name)
            if not job_info.get("builds"):
                return f"FAILURE: No builds found for job '{job_name}'."
            build_number = job_info["builds"][0]["number"]
        
        console_output = server.get_build_console_output(job_name, build_number)
        
        # Store as artifact
        artifact_id = str(uuid.uuid4())
        st.session_state["artifacts"][artifact_id] = {
            "type": "JENKINS_CONSOLE",
            "job_name": job_name,
            "build_number": build_number,
            "console_output": console_output,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }
        
        # Return summary (last 500 chars) to avoid overwhelming the chat
        console_preview = console_output[-500:] if len(console_output) > 500 else console_output
        return f"SUCCESS: Console output retrieved for {job_name} #{build_number} ({len(console_output)} chars). Last 500 chars:\n\n```\n{console_preview}\n```\n\nFull console available in artifacts. ::ARTIFACT_JENKINS_CONSOLE:{artifact_id}::"
    except Exception as e:
        return f"FAILURE: Error getting console output: {str(e)}"

def tool_trigger_build(job_name: str, parameters: str = None) -> str:
    """Trigger a Jenkins build. Parameters should be a JSON string like '{"param1": "value1", "param2": "value2"}'."""
    try:
        server = get_jenkins_server()
        if server is None:
            return "FAILURE: Could not connect to Jenkins server."
        
        params = {}
        if parameters:
            try:
                params = json.loads(parameters)
            except json.JSONDecodeError:
                return f"FAILURE: Invalid JSON format for parameters: {parameters}"
        
        queue_id = server.build_job(job_name, params) if params else server.build_job(job_name)
        
        return f"SUCCESS: Build triggered for '{job_name}'. Queue ID: {queue_id}. Monitor the build in Jenkins UI or use get_build_info to check status."
    except Exception as e:
        return f"FAILURE: Error triggering build: {str(e)}"

def tool_get_build_history(job_name: str, limit: int = 10) -> str:
    """Get build history for a Jenkins job. Returns last N builds (default 10)."""
    try:
        server = get_jenkins_server()
        if server is None:
            return "FAILURE: Could not connect to Jenkins server."
        
        job_info = server.get_job_info(job_name)
        builds = job_info.get("builds", [])[:limit]
        
        if not builds:
            return f"FAILURE: No builds found for job '{job_name}'."
        
        result = f"**Build History for {job_name} (Last {len(builds)} builds):**\n\n"
        for build in builds:
            build_num = build["number"]
            try:
                build_info = server.get_build_info(job_name, build_num)
                status = build_info.get("result", "IN PROGRESS")
                duration = build_info.get("duration", 0) / 1000
                timestamp = datetime.fromtimestamp(build_info.get("timestamp", 0) / 1000).strftime('%Y-%m-%d %H:%M:%S')
                result += f"- **Build #{build_num}:** {status} | Duration: {duration:.2f}s | {timestamp}\n"
            except:
                result += f"- **Build #{build_num}:** Status unknown\n"
        
        artifact_id = str(uuid.uuid4())
        st.session_state["artifacts"][artifact_id] = {
            "type": "JENKINS_BUILD_HISTORY",
            "job_name": job_name,
            "builds": builds,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }
        
        return f"SUCCESS: {result} ::ARTIFACT_JENKINS_HISTORY:{artifact_id}::"
    except Exception as e:
        return f"FAILURE: Error getting build history: {str(e)}"

def tool_analyze_build_failure(job_name: str, build_number: int = None) -> str:
    """Analyze why a Jenkins build failed. If build_number is not provided, analyzes the latest build."""
    try:
        server = get_jenkins_server()
        if server is None:
            return "FAILURE: Could not connect to Jenkins server."
        
        if build_number is None:
            job_info = server.get_job_info(job_name)
            if not job_info.get("builds"):
                return f"FAILURE: No builds found for job '{job_name}'."
            build_number = job_info["builds"][0]["number"]
        
        build_info = server.get_build_info(job_name, build_number)
        status = build_info.get("result", "IN PROGRESS")
        
        if status == "SUCCESS":
            return f"INFO: Build #{build_number} for '{job_name}' was successful. No failure to analyze."
        
        # Get console output
        console_output = server.get_build_console_output(job_name, build_number)
        
        # Use LLM to analyze the failure
        llm_config = {"config_list": [{"model": "gpt-4o-mini", "api_key": openai_api_key}], "temperature": 0}
        analyzer = AssistantAgent(
            "build_failure_analyzer",
            llm_config=llm_config,
            system_message="""You are a Jenkins build failure analysis expert. Analyze console output and identify:
1. Root cause of the failure
2. Error messages and stack traces
3. Possible solutions
4. Related issues or patterns
Be concise but thorough."""
        )
        
        analysis_prompt = f"""
        Analyze this Jenkins build failure:
        
        Job: {job_name}
        Build: #{build_number}
        Status: {status}
        
        Console Output (last 3000 chars):
        {console_output[-3000:]}
        
        Provide a structured analysis of the failure.
        """
        
        analysis = analyzer.generate_reply([{"role": "user", "content": analysis_prompt}])
        
        artifact_id = str(uuid.uuid4())
        st.session_state["artifacts"][artifact_id] = {
            "type": "JENKINS_FAILURE_ANALYSIS",
            "job_name": job_name,
            "build_number": build_number,
            "status": status,
            "console_output": console_output,
            "analysis": analysis,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }
        
        return f"SUCCESS: Failure analysis completed for {job_name} #{build_number}.\n\n{analysis}\n\n::ARTIFACT_JENKINS_ANALYSIS:{artifact_id}::"
    except Exception as e:
        return f"FAILURE: Error analyzing build failure: {str(e)}"

def tool_compare_builds(job_name: str, build_number1: int, build_number2: int) -> str:
    """Compare two builds of the same Jenkins job to identify differences."""
    try:
        server = get_jenkins_server()
        if server is None:
            return "FAILURE: Could not connect to Jenkins server."
        
        build1_info = server.get_build_info(job_name, build_number1)
        build2_info = server.get_build_info(job_name, build_number2)
        
        build1_console = server.get_build_console_output(job_name, build_number1)
        build2_console = server.get_build_console_output(job_name, build_number2)
        
        # Use LLM to compare builds
        llm_config = {"config_list": [{"model": "gpt-4o-mini", "api_key": openai_api_key}], "temperature": 0}
        comparator = AssistantAgent(
            "build_comparator",
            llm_config=llm_config,
            system_message="""You are a Jenkins build comparison expert. Compare two builds and identify:
1. Status differences (SUCCESS vs FAILURE)
2. Duration differences
3. Configuration or parameter differences
4. Console output differences (errors, warnings, changes)
5. Performance differences
Be structured and specific."""
        )
        
        comparison_prompt = f"""
        Compare these two Jenkins builds:
        
        Build #{build_number1}:
        - Status: {build1_info.get('result', 'UNKNOWN')}
        - Duration: {build1_info.get('duration', 0) / 1000:.2f}s
        - Timestamp: {datetime.fromtimestamp(build1_info.get('timestamp', 0) / 1000).strftime('%Y-%m-%d %H:%M:%S')}
        - Console (last 2000 chars): {build1_console[-2000:]}
        
        Build #{build_number2}:
        - Status: {build2_info.get('result', 'UNKNOWN')}
        - Duration: {build2_info.get('duration', 0) / 1000:.2f}s
        - Timestamp: {datetime.fromtimestamp(build2_info.get('timestamp', 0) / 1000).strftime('%Y-%m-%d %H:%M:%S')}
        - Console (last 2000 chars): {build2_console[-2000:]}
        
        Provide a detailed comparison.
        """
        
        comparison = comparator.generate_reply([{"role": "user", "content": comparison_prompt}])
        
        artifact_id = str(uuid.uuid4())
        st.session_state["artifacts"][artifact_id] = {
            "type": "JENKINS_BUILD_COMPARISON",
            "job_name": job_name,
            "build_number1": build_number1,
            "build_number2": build_number2,
            "build1_info": build1_info,
            "build2_info": build2_info,
            "comparison": comparison,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }
        
        return f"SUCCESS: Build comparison completed for {job_name} (Build #{build_number1} vs Build #{build_number2}).\n\n{comparison}\n\n::ARTIFACT_JENKINS_COMPARE:{artifact_id}::"
    except Exception as e:
        return f"FAILURE: Error comparing builds: {str(e)}"

def tool_get_job_config(job_name: str) -> str:
    """Get the configuration XML for a Jenkins job."""
    try:
        server = get_jenkins_server()
        if server is None:
            return "FAILURE: Could not connect to Jenkins server."
        
        config_xml = server.get_job_config(job_name)
        
        artifact_id = str(uuid.uuid4())
        st.session_state["artifacts"][artifact_id] = {
            "type": "JENKINS_JOB_CONFIG",
            "job_name": job_name,
            "config_xml": config_xml,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }
        
        # Return summary (first 1000 chars)
        config_preview = config_xml[:1000] if len(config_xml) > 1000 else config_xml
        return f"SUCCESS: Job configuration retrieved for '{job_name}' ({len(config_xml)} chars). Preview:\n\n```xml\n{config_preview}\n```\n\nFull config available in artifacts. ::ARTIFACT_JENKINS_CONFIG:{artifact_id}::"
    except Exception as e:
        return f"FAILURE: Error getting job config: {str(e)}"

def tool_get_build_artifacts(job_name: str, build_number: int = None) -> str:
    """List artifacts produced by a Jenkins build. If build_number is not provided, gets artifacts from the latest build."""
    try:
        server = get_jenkins_server()
        if server is None:
            return "FAILURE: Could not connect to Jenkins server."
        
        if build_number is None:
            job_info = server.get_job_info(job_name)
            if not job_info.get("builds"):
                return f"FAILURE: No builds found for job '{job_name}'."
            build_number = job_info["builds"][0]["number"]
        
        build_info = server.get_build_info(job_name, build_number)
        artifacts = build_info.get("artifacts", [])
        
        if not artifacts:
            return f"INFO: No artifacts found for {job_name} #{build_number}."
        
        result = f"**Artifacts for {job_name} #{build_number}:**\n\n"
        for artifact in artifacts:
            result += f"- **{artifact.get('fileName', 'Unknown')}** ({artifact.get('size', 0)} bytes)\n"
            result += f"  - Relative Path: {artifact.get('relativePath', 'N/A')}\n"
        
        artifact_id = str(uuid.uuid4())
        st.session_state["artifacts"][artifact_id] = {
            "type": "JENKINS_BUILD_ARTIFACTS",
            "job_name": job_name,
            "build_number": build_number,
            "artifacts": artifacts,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }
        
        return f"SUCCESS: {result} ::ARTIFACT_JENKINS_ARTIFACTS:{artifact_id}::"
    except Exception as e:
        return f"FAILURE: Error getting build artifacts: {str(e)}"

def get_comprehensive_build_history(job_name: str, limit: int = 10) -> dict:
    """Get comprehensive build history with parameters and console output for a Jenkins job.
    Works for both freestyle and pipeline jobs."""
    try:
        server = get_jenkins_server()
        if server is None:
            return {"status": "error", "message": "Could not connect to Jenkins server."}
        
        # Get job info to check if it exists
        try:
            job_info = server.get_job_info(job_name)
        except Exception as e:
            return {"status": "error", "message": f"Job '{job_name}' not found: {str(e)}"}
        
        builds = job_info.get("builds", [])[:limit]
        if not builds:
            return {"status": "not_found", "message": f"No builds found for job '{job_name}'."}
        
        build_history = []
        
        for build in builds:
            build_num = build["number"]
            try:
                build_info = server.get_build_info(job_name, build_num)
                
                # Extract basic info
                status = build_info.get("result", "IN PROGRESS")
                duration = build_info.get("duration", 0) / 1000  # Convert to seconds
                timestamp = datetime.fromtimestamp(build_info.get("timestamp", 0) / 1000)
                building = build_info.get("building", False)
                url = build_info.get("url", "")
                
                # Extract parameters from actions
                parameters = {}
                for action in build_info.get("actions", []):
                    if "parameters" in action:
                        for param in action["parameters"]:
                            param_name = param.get("name", "")
                            param_value = param.get("value", "")
                            if param_name:
                                parameters[param_name] = param_value
                
                # Extract causes (who triggered it)
                causes = []
                for action in build_info.get("actions", []):
                    if "causes" in action:
                        for cause in action["causes"]:
                            cause_type = cause.get("_class", "").split(".")[-1] if cause.get("_class") else "Unknown"
                            user = cause.get("userName", "")
                            if user:
                                causes.append(f"{cause_type} by {user}")
                            else:
                                causes.append(cause_type)
                
                # Get console output (limit to last 10000 chars to avoid memory issues)
                console_output = ""
                try:
                    full_console = server.get_build_console_output(job_name, build_num)
                    # Keep last 10000 chars for display, but store full length
                    console_output = full_console[-10000:] if len(full_console) > 10000 else full_console
                    console_length = len(full_console)
                except Exception as e:
                    console_output = f"Error retrieving console output: {str(e)}"
                    console_length = 0
                
                # Determine job type (freestyle or pipeline)
                job_type = "Unknown"
                if "workflowRun" in str(build_info.get("_class", "")) or "pipeline" in str(build_info.get("_class", "")).lower():
                    job_type = "Pipeline"
                else:
                    job_type = "Freestyle"
                
                build_data = {
                    "build_number": build_num,
                    "status": status,
                    "building": building,
                    "duration_seconds": round(duration, 2),
                    "timestamp": timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                    "url": url,
                    "parameters": parameters,
                    "causes": causes,
                    "console_output": console_output,
                    "console_length": console_length,
                    "job_type": job_type
                }
                
                build_history.append(build_data)
                
            except Exception as e:
                # If we can't get details for a specific build, add minimal info
                build_history.append({
                    "build_number": build_num,
                    "status": "ERROR",
                    "error": str(e),
                    "timestamp": "N/A",
                    "parameters": {},
                    "console_output": "",
                    "job_type": "Unknown"
                })
        
        return {
            "status": "success",
            "job_name": job_name,
            "build_count": len(build_history),
            "builds": build_history
        }
        
    except Exception as e:
        return {"status": "error", "message": f"Error getting build history: {str(e)}"}

def format_build_history_for_chat(result: dict) -> str:
    """Format build history result for chat display"""
    if result.get("status") != "success":
        return f"❌ {result.get('message', 'Failed to get build history')}"
    
    job_name = result.get("job_name", "Unknown")
    builds = result.get("builds", [])
    build_count = result.get("build_count", 0)
    show_console = result.get("show_console", True)
    
    if not builds:
        return f"⚠️ No builds found for job '{job_name}'."
    
    # Start building the formatted message
    formatted = f"✅ **Build History for: {job_name}**\n\n"
    formatted += f"Found **{build_count}** build(s):\n\n"
    
    # Add summary table
    formatted += "| Build # | Status | Duration | Timestamp | Job Type |\n"
    formatted += "|---------|--------|----------|-----------|----------|\n"
    
    for build in builds[:10]:  # Show first 10 in summary
        build_num = build.get("build_number", "N/A")
        status = build.get("status", "UNKNOWN")
        duration = build.get("duration_seconds", 0)
        timestamp = build.get("timestamp", "N/A")
        job_type = build.get("job_type", "Unknown")
        
        # Status emoji
        status_emoji = {
            "SUCCESS": "🟢",
            "FAILURE": "🔴",
            "UNSTABLE": "🟡",
            "ABORTED": "⚫",
            "IN PROGRESS": "🔵",
            "ERROR": "❌"
        }.get(status, "⚪")
        
        formatted += f"| #{build_num} | {status_emoji} {status} | {duration:.2f}s | {timestamp} | {job_type} |\n"
    
    if build_count > 10:
        formatted += f"\n*... and {build_count - 10} more builds. See details below.*\n"
    
    formatted += "\n**📋 Detailed build information available in the artifact below.**\n"
    
    return formatted

def tool_run_health_check() -> str:
    db = st.session_state["current_db"]
    
    # Get real-time metrics data (CPU/IO/Memory and top SQLs)
    metrics_data = get_realtime_metrics_data(db)
    
    # Run the standard health check
    res = run_full_health_check(db)
    if res["status"] == "ok":
        report_id = str(uuid.uuid4())
        
        # Enhance the health report with real-time metrics
        enhanced_report = res["report"]
        
        # Add real-time metrics section to the report
        metrics_section = "\n\n---\n\n## ⚡ Real-Time Resource Utilization\n\n"
        
        # CPU Utilization
        if metrics_data['current_utilization'].get('CPU'):
            cpu_val = metrics_data['current_utilization']['CPU'].get('VALUE', 'N/A')
            cpu_unit = metrics_data['current_utilization']['CPU'].get('UNIT', '%')
            metrics_section += f"**CPU Utilization:** {cpu_val} {cpu_unit}\n\n"
        
        # I/O Utilization
        if metrics_data['current_utilization'].get('IO'):
            io_val = metrics_data['current_utilization']['IO'].get('VALUE', 'N/A')
            io_unit = metrics_data['current_utilization']['IO'].get('UNIT', 'IOPS')
            metrics_section += f"**I/O Operations:** {io_val} {io_unit}\n\n"
        
        # Memory Utilization
        if metrics_data['current_utilization'].get('MEMORY'):
            mem = metrics_data['current_utilization']['MEMORY']
            mem_val = mem.get('VALUE', 'N/A')
            mem_unit = mem.get('UNIT', 'GB')
            sga = mem.get('SGA_ACTUAL_GB', 0)
            pga = mem.get('PGA_ALLOCATED_GB', 0)
            metrics_section += f"**Total Memory Usage:** {mem_val} {mem_unit}\n"
            metrics_section += f"- SGA Actual: {sga:.2f} GB\n"
            metrics_section += f"- PGA Allocated: {pga:.2f} GB\n\n"
        
        # Top SQLs
        if metrics_data['top_sql_cpu'] or metrics_data['top_sql_io'] or metrics_data['top_sql_memory']:
            metrics_section += "### 🔥 Top SQL Consumers (Last 5 Minutes)\n\n"
            
            if metrics_data['top_sql_cpu']:
                metrics_section += "**Top SQL by CPU:**\n"
                for i, sql in enumerate(metrics_data['top_sql_cpu'][:5], 1):
                    sql_id = sql.get('SQL_ID', 'N/A')
                    value = sql.get('VALUE', 0)
                    unit = sql.get('UNIT', '')
                    metrics_section += f"{i}. SQL_ID: {sql_id} - {value} {unit}\n"
                metrics_section += "\n"
            
            if metrics_data['top_sql_io']:
                metrics_section += "**Top SQL by I/O:**\n"
                for i, sql in enumerate(metrics_data['top_sql_io'][:5], 1):
                    sql_id = sql.get('SQL_ID', 'N/A')
                    value = sql.get('VALUE', 0)
                    unit = sql.get('UNIT', '')
                    metrics_section += f"{i}. SQL_ID: {sql_id} - {value} {unit}\n"
                metrics_section += "\n"
            
            if metrics_data['top_sql_memory']:
                metrics_section += "**Top SQL by Memory:**\n"
                for i, sql in enumerate(metrics_data['top_sql_memory'][:5], 1):
                    sql_id = sql.get('SQL_ID', 'N/A')
                    value = sql.get('VALUE', 0)
                    unit = sql.get('UNIT', '')
                    metrics_section += f"{i}. SQL_ID: {sql_id} - {value} {unit}\n"
                metrics_section += "\n"
        
        enhanced_report += metrics_section
        
        st.session_state["artifacts"][report_id] = {
            "type": "HEALTH", 
            "content": enhanced_report,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "metrics_data": metrics_data  # Store for potential UI rendering
        }
        audit_log("HEALTH_CHECK", db, {"status": "success"})
        return f"Health Check Completed successfully. ::ARTIFACT_HEALTH:{report_id}::"
    audit_log("HEALTH_CHECK", db, {"status": "failed", "message": res.get('message')})
    return f"Health Check Failed: {res.get('message')}"

def tool_performance_report(start_time: str = None, end_time: str = None, hours_back: float = None) -> str:
    """Generates AWR/ASH reports - Fixed missing import issue"""
    db = st.session_state["current_db"]
    res = None
    report_type = "AWR"
    period_str = ""

    try:
        # A. Relative Time
        if hours_back is not None:
            period_str = f"Last {hours_back} Hours"
            if hours_back < 2.0:
                report_type = "ASH"
                res = generate_ash_report(int(hours_back * 60), db)
            else:
                s_snap, e_snap = get_snapshots_for_time(hours_back, db)
                if not s_snap: 
                    return f"FAILURE: No snapshots found for last {hours_back} hours on {db}. Check if DB is gathering stats."
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
                # FIX: Use helper function instead of missing import
                res = generate_ash_report_specific_range(start_time, end_time, db)
            else:
                snaps = get_snapshots_by_date_range(start_time, end_time, db)
                if not snaps: 
                    return f"FAILURE: No snapshots found between {start_time} and {end_time}."
                res = generate_awr_report(snaps[0]['snap_id'], snaps[1]['snap_id'], db)

        else:
            return "FAILURE: Provide hours_back OR start_time/end_time."

        # Handle Result
        if res and res.get("status") == "ok":
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
            audit_log("PERFORMANCE_REPORT", db, {"type": report_type, "period": period_str})
            return f"SUCCESS: Generated {report_type} Report ({period_str}). File: {res['filename']}. buttons_rendered_below"
        
        return f"FAILURE: {res.get('message')}"

    except Exception as e:
        return f"ERROR in tool: {str(e)}"

def tool_analyze_report_content(user_question: str) -> str:
    """Analyzes the most recently generated report in history. Can answer questions about the report."""
    if not st.session_state["awr_history"]:
        return "No report found in history to analyze. Please generate one first."
    
    last_report = st.session_state["awr_history"][-1]
    
    try:
        soup = BeautifulSoup(last_report["report_html"], 'html.parser')
        text = soup.get_text()[:120000]
        
        # Check if this is a general analysis request or a specific question
        # Remove "Question about the AWR report:" prefix if present
        clean_question = user_question.replace("Question about the AWR report:", "").replace("Question about the AWR report", "").strip()
        
        is_general_analysis = any(phrase in clean_question.lower() for phrase in [
            "analyze", "analysis", "summary", "overview", "report", "highlight", "show me", "give me"
        ]) and len(clean_question.split()) < 10  # General analysis requests are usually short
        
        if is_general_analysis:
            # General analysis - provide focused summary with STRICT formatting
            prompt = f"""
            Analyze the AWR report and provide a CONCISE, STRUCTURED analysis with ONLY the following sections.
            IMPORTANT: Use EXACT formatting as shown below for proper parsing:
            
            1. **Load Profile**
            DB Time: [numeric value]
            DB CPU: [numeric value]
            Redo size: [numeric value]
            Logical reads: [numeric value]
            Physical reads: [numeric value]
            User calls: [numeric value]
            Parses: [numeric value]
            Hard parses: [numeric value]
            Transactions: [numeric value]
            
            2. **Top 3 SQLs by Elapsed Time**
            SQL ID: [id] Elapsed Time: [value] seconds Executions: [value] % DB Time: [value]% % CPU: [value]% % I/O: [value]% SQL Text: [first 100 characters]
            (Repeat for top 3 SQLs, each on a separate line)
            
            3. **Top 3 SQLs by CPU**
            SQL ID: [id] CPU Time: [value] seconds Executions: [value] % CPU: [value]% SQL Text: [first 100 characters]
            (Repeat for top 3 SQLs, each on a separate line)
            
            4. **Top 3 SQLs by I/O**
            SQL ID: [id] Physical Reads: [value] Buffer Gets: [value] % I/O: [value]% SQL Text: [first 100 characters]
            (Repeat for top 3 SQLs, each on a separate line)
            
            5. **Top 3 SQLs by Memory**
            SQL ID: [id] Memory Usage: [value] Executions: [value] % Memory: [value]% SQL Text: [first 100 characters]
            (Repeat for top 3 SQLs, each on a separate line)
            
            6. **Top Wait Events**
            Format each event EXACTLY as follows (one event per line):
            1. **Event Name:** [name] Total Waits: [value] Time Waited (sec): [value]% Total Wait Time: [value]%
            2. **Event Name:** [name] Total Waits: [value] Time Waited (sec): [value]% Total Wait Time: [value]%
            (Continue for top 5 wait events, each numbered and on a separate line. Use consistent format for all events.)
            
            7. **Recommendations**
            1. [First recommendation]
            2. [Second recommendation]
            3. [Third recommendation]
            (Numbered list, max 5 recommendations)
            
            Context (From {last_report['type']} Report):
            {text}
            
            CRITICAL: Use the EXACT format above. Each section must start with the section name in bold. Each SQL entry must be on a single line with all metrics separated by spaces. Do not use commas between metrics, use spaces only.
            """
        else:
            # Specific question - answer based on report content
            prompt = f"""
            User Question: {clean_question}
            
            Context (From {last_report['type']} Report):
            {text}
            
            Answer the user's question based on the AWR report data. Be specific and reference actual numbers/metrics from the report.
            If the question cannot be answered from the report, say so clearly.
            Provide a direct, concise answer.
            """
        
        llm_config = {"config_list": [{"model": "gpt-4o-mini", "api_key": openai_api_key}], "temperature": 0}
        analyzer = AssistantAgent("analyzer", llm_config=llm_config, system_message="You are an Oracle Expert DBA. Provide clear, concise, and actionable analysis.")
        
        reply = analyzer.generate_reply([{"role": "user", "content": prompt}])
        
        # Store formatted analysis in artifact for better rendering
        analysis_id = str(uuid.uuid4())
        st.session_state["artifacts"][analysis_id] = {
            "type": "AWR_ANALYSIS",
            "content": reply,
            "report_label": last_report['label'],
            "report_html": last_report["report_html"],  # Store full report for Q&A
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "is_question": not is_general_analysis
        }
        
        # Return minimal text - the artifact will render the full analysis
        if is_general_analysis:
            return f"Analysis of {last_report['label']} completed. ::ARTIFACT_AWR_ANALYSIS:{analysis_id}::"
        else:
            return f"Answer to your question about {last_report['label']}. ::ARTIFACT_AWR_ANALYSIS:{analysis_id}::"
        
    except Exception as e:
        return f"Analysis failed: {e}"

def tool_compare_awr_reports(baseline_start_time: str, baseline_end_time: str, target_start_time: str, target_end_time: str) -> str:
    """Compare AWR reports for baseline and target time periods. 
    Requires 2 time periods for baseline (start/end) and 2 for target (start/end).
    Generates detailed LLM analysis."""
    db = st.session_state["current_db"]
    
    try:
        # Parse times - expected format: "YYYY-MM-DD HH:MM:SS"
        # Find snapshots for baseline time range
        baseline_start_dt = datetime.strptime(baseline_start_time, "%Y-%m-%d %H:%M:%S")
        baseline_end_dt = datetime.strptime(baseline_end_time, "%Y-%m-%d %H:%M:%S")
        
        # Find snapshots for target time range
        target_start_dt = datetime.strptime(target_start_time, "%Y-%m-%d %H:%M:%S")
        target_end_dt = datetime.strptime(target_end_time, "%Y-%m-%d %H:%M:%S")
        
        # Validate time ranges
        if baseline_start_dt >= baseline_end_dt:
            return f"FAILURE: Baseline start time must be before baseline end time."
        if target_start_dt >= target_end_dt:
            return f"FAILURE: Target start time must be before target end time."
        
        # Get snapshots for baseline period
        baseline_snaps = get_snapshots_by_date_range(baseline_start_time, baseline_end_time, db)
        # Get snapshots for target period
        target_snaps = get_snapshots_by_date_range(target_start_time, target_end_time, db)
        
        if not baseline_snaps or len(baseline_snaps) < 2:
            return f"FAILURE: Need at least 2 snapshots in baseline period ({baseline_start_time} to {baseline_end_time}). Found: {len(baseline_snaps) if baseline_snaps else 0}."
        if not target_snaps or len(target_snaps) < 2:
            return f"FAILURE: Need at least 2 snapshots in target period ({target_start_time} to {target_end_time}). Found: {len(target_snaps) if target_snaps else 0}."
        
        # Generate AWR reports for both time ranges
        baseline_report = generate_awr_report(baseline_snaps[0]['snap_id'], baseline_snaps[-1]['snap_id'], db)
        target_report = generate_awr_report(target_snaps[0]['snap_id'], target_snaps[-1]['snap_id'], db)
        
        if baseline_report.get("status") != "ok" or target_report.get("status") != "ok":
            return f"FAILURE: Could not generate AWR reports. Baseline: {baseline_report.get('message', 'Unknown')}, Target: {target_report.get('message', 'Unknown')}"
        
        # Compare the reports
        baseline_label = f"Baseline ({baseline_start_time} to {baseline_end_time})"
        target_label = f"Target ({target_start_time} to {target_end_time})"
        comparison_result = compare_awr_reports(
            baseline_report["report"], 
            target_report["report"], 
            baseline_label, 
            target_label
        )
        
        if comparison_result.get("status") != "ok":
            return f"FAILURE: Comparison failed: {comparison_result.get('message', 'Unknown error')}"
        
        # Generate detailed LLM analysis
        llm_config = {"config_list": [{"model": "gpt-4o-mini", "api_key": openai_api_key}], "temperature": 0}
        analyzer = AssistantAgent(
            "awr_comparison_analyst", 
            llm_config=llm_config, 
            system_message="""You are an Oracle Expert DBA analyzing AWR report comparisons. 
            Provide a DETAILED, STRUCTURED analysis with:
            1. **Executive Summary** - Overall performance comparison
            2. **Key Metrics Changes** - DB Time, CPU, I/O, Wait Events changes
            3. **Top SQL Regressions** - SQLs that degraded with details
            4. **Top SQL Improvements** - SQLs that improved
            5. **Wait Event Analysis** - Significant wait event changes
            6. **Resource Utilization Changes** - CPU, Memory, I/O trends
            7. **Critical Findings** - Issues requiring immediate attention
            8. **Recommendations** - Actionable steps to address issues
            
            Be specific with numbers, percentages, and SQL IDs. Format clearly with sections."""
        )
        
        comparison_text = comparison_result.get("comparison", "")
        analysis_prompt = f"""
        Analyze this AWR comparison report and provide a comprehensive detailed analysis:
        
        {comparison_text[:150000]}
        
        Focus on:
        - Performance degradation or improvement
        - Root causes of changes
        - Specific SQLs and wait events
        - Actionable recommendations
        """
        
        detailed_analysis = analyzer.generate_reply([{"role": "user", "content": analysis_prompt}])
        
        # Store as artifact
        comp_id = str(uuid.uuid4())
        st.session_state["artifacts"][comp_id] = {
            "type": "COMPARE",
            "content": detailed_analysis,
            "title": f"AWR Comparison: Baseline ({baseline_start_time} to {baseline_end_time}) vs Target ({target_start_time} to {target_end_time})",
            "baseline_start_time": baseline_start_time,
            "baseline_end_time": baseline_end_time,
            "target_start_time": target_start_time,
            "target_end_time": target_end_time,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }
        
        audit_log("AWR_COMPARE", db, {
            "baseline_start": baseline_start_time, 
            "baseline_end": baseline_end_time,
            "target_start": target_start_time,
            "target_end": target_end_time
        })
        return f"SUCCESS: AWR comparison completed for Baseline ({baseline_start_time} to {baseline_end_time}) vs Target ({target_start_time} to {target_end_time}). Detailed analysis generated. ::ARTIFACT_COMPARE:{comp_id}::"
        
    except Exception as e:
        return f"ERROR in AWR comparison: {str(e)}"

def tool_analyze_health_report(user_question: str) -> str:
    """Analyzes the most recently generated health report. Can answer questions about the report."""
    # Find the most recent health report artifact
    health_artifacts = [
        (art_id, art) for art_id, art in st.session_state["artifacts"].items()
        if art.get("type") == "HEALTH"
    ]
    
    if not health_artifacts:
        return "No health report found to analyze. Please run a health check first."
    
    # Get the most recent health report
    last_health_id, last_health = sorted(health_artifacts, key=lambda x: x[1].get("timestamp", ""), reverse=True)[0]
    
    try:
        health_content = last_health["content"]
        
        # Check if this is a general analysis request or a specific question
        # Remove "Question about the health report:" prefix if present
        clean_question = user_question.replace("Question about the health report:", "").replace("Question about the health report", "").strip()
        
        is_general_analysis = any(phrase in clean_question.lower() for phrase in [
            "analyze", "analysis", "summary", "overview", "report", "highlight", "show me", "give me"
        ]) and len(clean_question.split()) < 10  # General analysis requests are usually short
        
        if is_general_analysis:
            # General analysis - provide focused summary
            prompt = f"""
            Analyze the health report and provide a CONCISE, STRUCTURED analysis with ONLY the following sections:
            
            1. **Overall Status** - Database health status (GOOD/DEGRADED/CRITICAL)
            
            2. **Critical Issues** - Any critical issues that need immediate attention
            
            3. **Warnings** - Warning-level issues that should be addressed soon
            
            4. **Resource Utilization** - CPU, I/O, Memory utilization summary
            
            5. **Top Concerns** - Top 3-5 most important findings or concerns
            
            6. **Recommendations** - Actionable recommendations based on the findings (max 5 recommendations)
            
            Context (From Health Report):
            {health_content}
            
            Be concise. Use clear section headers. Format data in tables where possible.
            """
        else:
            # Specific question - answer based on report content
            prompt = f"""
            User Question: {clean_question}
            
            Context (From Health Report):
            {health_content}
            
            Answer the user's question based on the health report data. Be specific and reference actual numbers/metrics from the report.
            If the question cannot be answered from the report, say so clearly.
            Provide a direct, concise answer.
            """
        
        llm_config = {"config_list": [{"model": "gpt-4o-mini", "api_key": openai_api_key}], "temperature": 0}
        analyzer = AssistantAgent("analyzer", llm_config=llm_config, system_message="You are an Oracle Expert DBA. Provide clear, concise, and actionable analysis.")
        
        reply = analyzer.generate_reply([{"role": "user", "content": prompt}])
        
        # Store formatted analysis in artifact for better rendering
        analysis_id = str(uuid.uuid4())
        st.session_state["artifacts"][analysis_id] = {
            "type": "HEALTH_ANALYSIS",
            "content": reply,
            "report_label": f"Health Report ({last_health.get('timestamp', 'N/A')})",
            "health_content": health_content,  # Store full report for Q&A
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "is_question": not is_general_analysis
        }
        
        # Return minimal text - the artifact will render the full analysis
        if is_general_analysis:
            return f"Analysis of health report completed. ::ARTIFACT_HEALTH_ANALYSIS:{analysis_id}::"
        else:
            return f"Answer to your question about the health report. ::ARTIFACT_HEALTH_ANALYSIS:{analysis_id}::"
        
    except Exception as e:
        return f"Health report analysis failed: {e}"

def tool_download_patch_wrapper(patch_description: str) -> str:
    """Downloads Oracle patches based on natural language description."""
    res = download_oracle_patch(patch_description)
    
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

# ============================================================================
# 6. NEW DBA FEATURES - SESSION MANAGEMENT
# ============================================================================

def tool_list_active_sessions() -> str:
    """List active sessions with blocking info"""
    db = st.session_state["current_db"]
    sql = """
    SELECT 
        s.sid, s.serial#, s.username, s.program, s.status,
        s.sql_id, s.event, s.seconds_in_wait,
        s.blocking_session,
        CASE WHEN s.blocking_session IS NOT NULL THEN '🔴 BLOCKED' ELSE '🟢' END as blocking_status
    FROM v$session s
    WHERE s.status = 'ACTIVE' AND s.username IS NOT NULL
    ORDER BY s.seconds_in_wait DESC
    """
    try:
        result = run_oracle_query(sql, db)
        if isinstance(result, list) and result:
            df = pd.DataFrame(result)
            session_id = str(uuid.uuid4())
            st.session_state["artifacts"][session_id] = {
                "type": "SESSION_LIST",
                "data": result,
                "timestamp": datetime.now().strftime("%H:%M:%S")
            }
            return f"Found {len(df)} active sessions. ::ARTIFACT_SESSIONS:{session_id}::"
        return "No active sessions found."
    except Exception as e:
        return f"Error listing sessions: {handle_oracle_error(e)}"

def tool_kill_session(sid: int, serial: int, immediate: bool = False) -> str:
    """Kill problematic sessions - requires confirmation"""
    db = st.session_state["current_db"]
    kill_id = str(uuid.uuid4())
    st.session_state["artifacts"][kill_id] = {
        "type": "KILL_SESSION_CONFIRM",
        "sid": sid,
        "serial": serial,
        "immediate": immediate,
        "db": db,
        "timestamp": datetime.now().strftime("%H:%M:%S")
    }
    return f"⚠️ Session kill requested for SID={sid}, SERIAL#={serial}. Please confirm below. ::ARTIFACT_KILL:{kill_id}::"

# ============================================================================
# 7. NEW DBA FEATURES - TABLESPACE MONITORING
# ============================================================================

def tool_check_tablespaces() -> str:
    """Check tablespace usage and alerts - handles both permanent and temp tablespaces"""
    db = st.session_state["current_db"]
    # Comprehensive query that handles both permanent and temporary tablespaces
    sql = """
    SELECT
        d.TABLESPACE_NAME,
        DECODE(d.STATUS, 'ONLINE', 'OLN', 'READ ONLY', 'R/O', d.STATUS) as STATUS,
        d.EXTENT_MANAGEMENT,
        DECODE(d.ALLOCATION_TYPE, 'USER','', d.ALLOCATION_TYPE) as ALLOCATION_TYPE,
        d.SEGMENT_SPACE_MANAGEMENT,
        (CASE
            WHEN INITIAL_EXTENT < 1048576
                THEN LPAD(ROUND(INITIAL_EXTENT/1024,0),3)||'K'
            ELSE LPAD(ROUND(INITIAL_EXTENT/1024/1024,0),3)||'M'
        END) as EXT_SIZE,
        NVL(a.BYTES / 1024 / 1024, 0) as MB,
        NVL(f.BYTES / 1024 / 1024, 0) as FREE,
        (NVL(a.BYTES / 1024 / 1024, 0) - NVL(f.BYTES / 1024 / 1024, 0)) as USED,
        LPAD(ROUND((f.BYTES/a.BYTES)*100,0),3) as PFREE,
        (CASE 
            WHEN ROUND(f.BYTES/a.BYTES*100,0) >= 20 OR f.BYTES>=20*1024*1024*1024 THEN ' ' 
            ELSE '*' 
        END) as ALRT
    FROM sys.dba_tablespaces d,
        (SELECT TABLESPACE_NAME, SUM(BYTES) as BYTES
         FROM dba_data_files
         GROUP BY TABLESPACE_NAME) a,
        (SELECT TABLESPACE_NAME, SUM(BYTES) as BYTES
         FROM dba_free_space
         GROUP BY TABLESPACE_NAME) f,
        (SELECT TABLESPACE_NAME, MAX(BYTES) as LARGE
         FROM dba_free_space
         GROUP BY TABLESPACE_NAME) l
    WHERE d.TABLESPACE_NAME = a.TABLESPACE_NAME(+)
      AND d.TABLESPACE_NAME = f.TABLESPACE_NAME(+)
      AND d.TABLESPACE_NAME = l.TABLESPACE_NAME(+)
      AND NOT (d.EXTENT_MANAGEMENT LIKE 'LOCAL' AND d.CONTENTS LIKE 'TEMPORARY')
    UNION ALL
    SELECT
        d.TABLESPACE_NAME,
        DECODE(d.STATUS, 'ONLINE', 'OLN', 'READ ONLY', 'R/O', d.STATUS) as STATUS,
        d.EXTENT_MANAGEMENT,
        DECODE(d.ALLOCATION_TYPE, 'UNIFORM','U', 'SYSTEM','A', 'USER','', d.ALLOCATION_TYPE) as ALLOCATION_TYPE,
        d.SEGMENT_SPACE_MANAGEMENT,
        (CASE
            WHEN INITIAL_EXTENT < 1048576
                THEN LPAD(ROUND(INITIAL_EXTENT/1024,0),3)||'K'
            ELSE LPAD(ROUND(INITIAL_EXTENT/1024/1024,0),3)||'M'
        END) as EXT_SIZE,
        NVL(a.BYTES / 1024 / 1024, 0) as MB,
        (NVL(a.BYTES / 1024 / 1024, 0) - NVL(t.BYTES / 1024 / 1024, 0)) as FREE,
        NVL(t.BYTES / 1024 / 1024, 0) as USED,
        LPAD(ROUND(NVL(((a.BYTES-t.BYTES)/NVL(a.BYTES,0))*100,100),0),3) as PFREE,
        (CASE 
            WHEN NVL(ROUND(((a.BYTES-t.BYTES)/NVL(a.BYTES,0))*100,0),100) >= 20 
                 OR a.BYTES-t.BYTES>=20*1024*1024*1024 
            THEN ' ' 
            ELSE '*' 
        END) as ALRT
    FROM sys.dba_tablespaces d,
        (SELECT TABLESPACE_NAME, SUM(BYTES) as BYTES
         FROM dba_temp_files
         GROUP BY TABLESPACE_NAME) a,
        (SELECT TABLESPACE_NAME, SUM(BYTES_USED) as BYTES
         FROM v$temp_extent_pool
         GROUP BY TABLESPACE_NAME) t,
        (SELECT TABLESPACE_NAME, MAX(BYTES_CACHED) as LARGE
         FROM v$temp_extent_pool
         GROUP BY TABLESPACE_NAME) l
    WHERE d.TABLESPACE_NAME = a.TABLESPACE_NAME(+)
      AND d.TABLESPACE_NAME = t.TABLESPACE_NAME(+)
      AND d.TABLESPACE_NAME = l.TABLESPACE_NAME(+)
      AND d.EXTENT_MANAGEMENT LIKE 'LOCAL'
      AND d.CONTENTS LIKE 'TEMPORARY'
    ORDER BY 1
    """
    
    try:
        result = run_oracle_query(sql, db)
        if isinstance(result, list) and result:
            # Convert MB to GB and add calculated columns
            normalized_result = []
            for row in result:
                normalized_row = {}
                for key, value in row.items():
                    key_upper = key.upper()
                    # Convert MB to GB for consistency
                    if key_upper == 'MB':
                        try:
                            mb_val = float(value) if value is not None else 0.0
                            normalized_row['SIZE_GB'] = round(mb_val / 1024, 2)
                        except (ValueError, TypeError):
                            normalized_row['SIZE_GB'] = 0.0
                    elif key_upper == 'USED':
                        try:
                            mb_val = float(value) if value is not None else 0.0
                            normalized_row['USED_GB'] = round(mb_val / 1024, 2)
                        except (ValueError, TypeError):
                            normalized_row['USED_GB'] = 0.0
                    elif key_upper == 'FREE':
                        try:
                            mb_val = float(value) if value is not None else 0.0
                            normalized_row['FREE_GB'] = round(mb_val / 1024, 2)
                        except (ValueError, TypeError):
                            normalized_row['FREE_GB'] = 0.0
                    elif key_upper == 'PFREE':
                        try:
                            pct_val = float(value) if value is not None else 0.0
                            normalized_row['PCT_USED'] = round(100.0 - pct_val, 2)
                            # Add status based on percentage
                            if normalized_row['PCT_USED'] > 90:
                                normalized_row['STATUS'] = '🔴 CRITICAL'
                            elif normalized_row['PCT_USED'] > 80:
                                normalized_row['STATUS'] = '🟡 WARNING'
                            else:
                                normalized_row['STATUS'] = '🟢 OK'
                        except (ValueError, TypeError):
                            normalized_row['PCT_USED'] = 0.0
                            normalized_row['STATUS'] = '🟢 OK'
                    else:
                        normalized_row[key_upper] = value
                
                # Keep original columns too for reference
                normalized_result.append(normalized_row)
            
            ts_id = str(uuid.uuid4())
            st.session_state["artifacts"][ts_id] = {
                "type": "TABLESPACE_STATUS",
                "data": normalized_result,
                "timestamp": datetime.now().strftime("%H:%M:%S")
            }
            return f"Tablespace status retrieved ({len(normalized_result)} tablespaces). ::ARTIFACT_TABLESPACE:{ts_id}::"
        return "No tablespace data available."
    except Exception as e:
        return f"Error checking tablespaces: {handle_oracle_error(e)}"
    
    try:
        result = run_oracle_query(sql, db)
        if isinstance(result, list) and result:
            # Normalize column names to uppercase and ensure numeric values
            normalized_result = []
            for row in result:
                normalized_row = {}
                for key, value in row.items():
                    key_upper = key.upper()
                    # Ensure numeric columns are properly formatted
                    if key_upper in ['SIZE_GB', 'USED_GB', 'FREE_GB', 'PCT_USED']:
                        try:
                            val = float(value) if value is not None else 0.0
                            normalized_row[key_upper] = round(val, 2) if val >= 0 else 0.0
                        except (ValueError, TypeError):
                            normalized_row[key_upper] = 0.0
                    else:
                        normalized_row[key_upper] = value
                normalized_result.append(normalized_row)
            
            ts_id = str(uuid.uuid4())
            st.session_state["artifacts"][ts_id] = {
                "type": "TABLESPACE_STATUS",
                "data": normalized_result,
                "timestamp": datetime.now().strftime("%H:%M:%S")
            }
            return f"Tablespace status retrieved ({len(normalized_result)} tablespaces). ::ARTIFACT_TABLESPACE:{ts_id}::"
        return "No tablespace data available."
    except Exception as e:
        return f"Error checking tablespaces: {handle_oracle_error(e)}"

# ============================================================================
# 8. NEW DBA FEATURES - REAL-TIME METRICS
# ============================================================================

def get_realtime_metrics_data(db: str) -> dict:
    """Helper function to get real-time metrics data (CPU/IO/Memory and top SQLs) - used by health check"""
    current_utilization = {}
    top_sql_cpu = []
    top_sql_io = []
    top_sql_memory = []
    
    # Get CPU Utilization
    try:
        sql_cpu_util = """
        SELECT 
            'Current CPU Utilization' as METRIC_NAME,
            ROUND(VALUE, 2) as VALUE,
            '%' as UNIT
        FROM V$SYSMETRIC
        WHERE METRIC_NAME = 'CPU Usage Per Sec'
           AND GROUP_ID = 2
        UNION ALL
        SELECT 
            'Current CPU Utilization (Alt)' as METRIC_NAME,
            ROUND(VALUE * 100, 2) as VALUE,
            '%' as UNIT
        FROM V$SYSMETRIC
        WHERE METRIC_NAME LIKE '%CPU%' 
           AND (METRIC_NAME LIKE '%Usage%' OR METRIC_NAME LIKE '%Utilization%')
           AND GROUP_ID = 2
        FETCH FIRST 1 ROWS ONLY
        """
        result_cpu = run_oracle_query(sql_cpu_util, db)
        if isinstance(result_cpu, list) and result_cpu:
            current_utilization['CPU'] = result_cpu[0]
    except:
        try:
            sql_cpu_os = """
            SELECT 
                'Current CPU Utilization' as METRIC_NAME,
                ROUND((BUSY_TIME / (BUSY_TIME + IDLE_TIME)) * 100, 2) as VALUE,
                '%' as UNIT
            FROM (
                SELECT 
                    SUM(CASE WHEN STAT_NAME = 'BUSY_TIME' THEN VALUE ELSE 0 END) as BUSY_TIME,
                    SUM(CASE WHEN STAT_NAME = 'IDLE_TIME' THEN VALUE ELSE 0 END) as IDLE_TIME
                FROM V$OSSTAT
                WHERE STAT_NAME IN ('BUSY_TIME', 'IDLE_TIME')
            )
            """
            result_cpu_os = run_oracle_query(sql_cpu_os, db)
            if isinstance(result_cpu_os, list) and result_cpu_os:
                current_utilization['CPU'] = result_cpu_os[0]
        except:
            pass
    
    # Get I/O Utilization
    try:
        sql_io_util = """
        SELECT 
            'Current I/O Operations/sec' as METRIC_NAME,
            ROUND(VALUE, 2) as VALUE,
            'IOPS' as UNIT
        FROM V$SYSMETRIC
        WHERE (METRIC_NAME LIKE '%I/O%' OR METRIC_NAME LIKE '%Physical Read%' OR METRIC_NAME LIKE '%Physical Write%')
           AND GROUP_ID = 2
        ORDER BY VALUE DESC
        FETCH FIRST 1 ROWS ONLY
        """
        result_io = run_oracle_query(sql_io_util, db)
        if isinstance(result_io, list) and result_io:
            current_utilization['IO'] = result_io[0]
    except:
        pass
    
    # Get Memory Utilization
    try:
        sql_mem_util = """
        SELECT 
            'SGA Actual Size' AS memory_type,
            ROUND(SUM(value) / 1024 / 1024 / 1024, 2) AS size_gb
        FROM v$sga
        UNION ALL
        SELECT 
            'PGA Actual Allocated',
            ROUND(value / 1024 / 1024 / 1024, 2)
        FROM v$pgastat
        WHERE name = 'total PGA allocated'
        UNION ALL
        SELECT 
            'TOTAL Memory Usage',
            ROUND(( (SELECT SUM(value) FROM v$sga) + 
                    (SELECT value FROM v$pgastat WHERE name = 'total PGA allocated') ) 
                  / 1024 / 1024 / 1024, 2)
        FROM DUAL
        """
        result_mem = run_oracle_query(sql_mem_util, db)
        if isinstance(result_mem, list) and result_mem:
            sga_actual = 0
            pga_allocated = 0
            total_memory = 0
            for row in result_mem:
                memory_type = str(row.get('MEMORY_TYPE', row.get('memory_type', ''))).upper()
                size_gb = float(row.get('SIZE_GB', row.get('size_gb', 0)))
                if 'SGA ACTUAL' in memory_type:
                    sga_actual = size_gb
                elif 'PGA ACTUAL' in memory_type:
                    pga_allocated = size_gb
                elif 'TOTAL' in memory_type:
                    total_memory = size_gb
            current_utilization['MEMORY'] = {
                'METRIC_NAME': 'Total Memory Usage',
                'VALUE': total_memory if total_memory > 0 else (sga_actual + pga_allocated),
                'UNIT': 'GB',
                'SGA_ACTUAL_GB': sga_actual,
                'PGA_ALLOCATED_GB': pga_allocated,
                'TOTAL_MEMORY_GB': total_memory if total_memory > 0 else (sga_actual + pga_allocated)
            }
    except:
        pass
    
    # Get Top SQL by CPU
    try:
        sql_cpu_ash = """
        SELECT 
            'SQL_CPU' as METRIC_TYPE,
            SQL_ID,
            ROUND(SUM(CPU_TIME_DELTA)/1000000, 2) as VALUE,
            'seconds (last 5 min)' as UNIT,
            COUNT(*) as EXECUTIONS,
            ROUND(SUM(CPU_TIME_DELTA)/1000000/NULLIF(COUNT(*), 0), 4) as AVG_CPU_PER_EXEC,
            SUBSTR(MAX(SQL_TEXT), 1, 50) as SQL_TEXT_PREVIEW
        FROM (
            SELECT 
                ash.SQL_ID,
                ash.CPU_TIME_DELTA,
                SUBSTR(q.SQL_TEXT, 1, 50) as SQL_TEXT
            FROM V$ACTIVE_SESSION_HISTORY ash
            LEFT JOIN V$SQL q ON ash.SQL_ID = q.SQL_ID
            WHERE ash.SAMPLE_TIME > SYSDATE - 5/1440
              AND ash.SQL_ID IS NOT NULL
              AND ash.CPU_TIME_DELTA > 0
        )
        GROUP BY SQL_ID
        ORDER BY VALUE DESC
        FETCH FIRST 10 ROWS ONLY
        """
        result_cpu = run_oracle_query(sql_cpu_ash, db)
        if isinstance(result_cpu, list) and result_cpu:
            top_sql_cpu = result_cpu
        else:
            sql_cpu_stats = """
            SELECT 
                'SQL_CPU' as METRIC_TYPE,
                SQL_ID,
                ROUND(CPU_TIME/1000000, 2) as VALUE,
                'seconds (recent)' as UNIT,
                EXECUTIONS,
                ROUND(CPU_TIME/1000000/NULLIF(EXECUTIONS, 0), 4) as AVG_CPU_PER_EXEC,
                SUBSTR(SQL_TEXT, 1, 50) as SQL_TEXT_PREVIEW
            FROM (
                SELECT s.SQL_ID, s.CPU_TIME, s.EXECUTIONS, q.SQL_TEXT
                FROM V$SQLSTATS s
                LEFT JOIN V$SQL q ON s.SQL_ID = q.SQL_ID
                WHERE s.CPU_TIME > 0
                ORDER BY s.CPU_TIME DESC
                FETCH FIRST 10 ROWS ONLY
            )
            """
            result_cpu = run_oracle_query(sql_cpu_stats, db)
            if isinstance(result_cpu, list) and result_cpu:
                top_sql_cpu = result_cpu
    except:
        pass
    
    # Get Top SQL by I/O
    try:
        sql_io_ash = """
        SELECT 
            'SQL_IO' as METRIC_TYPE,
            SQL_ID,
            ROUND(SUM(DISK_READS_DELTA + BUFFER_GETS_DELTA), 0) as VALUE,
            'blocks (last 5 min)' as UNIT,
            COUNT(*) as EXECUTIONS,
            ROUND(SUM(DISK_READS_DELTA + BUFFER_GETS_DELTA)/NULLIF(COUNT(*), 0), 2) as AVG_BLOCKS_PER_EXEC,
            SUBSTR(MAX(SQL_TEXT), 1, 50) as SQL_TEXT_PREVIEW
        FROM (
            SELECT 
                ash.SQL_ID,
                ash.DISK_READS_DELTA,
                ash.BUFFER_GETS_DELTA,
                SUBSTR(q.SQL_TEXT, 1, 50) as SQL_TEXT
            FROM V$ACTIVE_SESSION_HISTORY ash
            LEFT JOIN V$SQL q ON ash.SQL_ID = q.SQL_ID
            WHERE ash.SAMPLE_TIME > SYSDATE - 5/1440
              AND ash.SQL_ID IS NOT NULL
              AND (ash.DISK_READS_DELTA > 0 OR ash.BUFFER_GETS_DELTA > 0)
        )
        GROUP BY SQL_ID
        ORDER BY VALUE DESC
        FETCH FIRST 10 ROWS ONLY
        """
        result_io = run_oracle_query(sql_io_ash, db)
        if isinstance(result_io, list) and result_io:
            top_sql_io = result_io
        else:
            sql_io_stats = """
            SELECT 
                'SQL_IO' as METRIC_TYPE,
                SQL_ID,
                ROUND(DISK_READS + BUFFER_GETS, 0) as VALUE,
                'blocks (recent)' as UNIT,
                EXECUTIONS,
                ROUND((DISK_READS + BUFFER_GETS)/NULLIF(EXECUTIONS, 0), 2) as AVG_BLOCKS_PER_EXEC,
                SUBSTR(SQL_TEXT, 1, 50) as SQL_TEXT_PREVIEW
            FROM (
                SELECT s.SQL_ID, s.DISK_READS, s.BUFFER_GETS, s.EXECUTIONS, q.SQL_TEXT
                FROM V$SQLSTATS s
                LEFT JOIN V$SQL q ON s.SQL_ID = q.SQL_ID
                WHERE (s.DISK_READS + s.BUFFER_GETS) > 0
                ORDER BY (s.DISK_READS + s.BUFFER_GETS) DESC
                FETCH FIRST 10 ROWS ONLY
            )
            """
            result_io = run_oracle_query(sql_io_stats, db)
            if isinstance(result_io, list) and result_io:
                top_sql_io = result_io
    except:
        pass
    
    # Get Top SQL by Memory
    try:
        sql_mem_ash = """
        SELECT 
            'SQL_MEMORY' as METRIC_TYPE,
            SQL_ID,
            ROUND(SUM(BUFFER_GETS_DELTA)/1000000, 2) as VALUE,
            'M blocks (last 5 min)' as UNIT,
            COUNT(*) as EXECUTIONS,
            ROUND(SUM(BUFFER_GETS_DELTA)/1000000/NULLIF(COUNT(*), 0), 4) as AVG_MBLOCKS_PER_EXEC,
            SUBSTR(MAX(SQL_TEXT), 1, 50) as SQL_TEXT_PREVIEW
        FROM (
            SELECT 
                ash.SQL_ID,
                ash.BUFFER_GETS_DELTA,
                SUBSTR(q.SQL_TEXT, 1, 50) as SQL_TEXT
            FROM V$ACTIVE_SESSION_HISTORY ash
            LEFT JOIN V$SQL q ON ash.SQL_ID = q.SQL_ID
            WHERE ash.SAMPLE_TIME > SYSDATE - 5/1440
              AND ash.SQL_ID IS NOT NULL
              AND ash.BUFFER_GETS_DELTA > 0
        )
        GROUP BY SQL_ID
        ORDER BY VALUE DESC
        FETCH FIRST 10 ROWS ONLY
        """
        result_mem = run_oracle_query(sql_mem_ash, db)
        if isinstance(result_mem, list) and result_mem:
            top_sql_memory = result_mem
        else:
            sql_mem_stats = """
            SELECT 
                'SQL_MEMORY' as METRIC_TYPE,
                SQL_ID,
                ROUND(BUFFER_GETS/1000000, 2) as VALUE,
                'M blocks (recent)' as UNIT,
                EXECUTIONS,
                ROUND(BUFFER_GETS/1000000/NULLIF(EXECUTIONS, 0), 4) as AVG_MBLOCKS_PER_EXEC,
                SUBSTR(SQL_TEXT, 1, 50) as SQL_TEXT_PREVIEW
            FROM (
                SELECT s.SQL_ID, s.BUFFER_GETS, s.EXECUTIONS, q.SQL_TEXT
                FROM V$SQLSTATS s
                LEFT JOIN V$SQL q ON s.SQL_ID = q.SQL_ID
                WHERE s.BUFFER_GETS > 0
                ORDER BY s.BUFFER_GETS DESC
                FETCH FIRST 10 ROWS ONLY
            )
            """
            result_mem = run_oracle_query(sql_mem_stats, db)
            if isinstance(result_mem, list) and result_mem:
                top_sql_memory = result_mem
    except:
        pass
    
    return {
        'current_utilization': current_utilization,
        'top_sql_cpu': top_sql_cpu,
        'top_sql_io': top_sql_io,
        'top_sql_memory': top_sql_memory
    }

def get_historical_metrics(db: str, days: int = 30) -> dict:
    """Get historical metrics for last N days (CPU, I/O, Memory, AAS) from AWR snapshots"""
    try:
        # Single comprehensive query for CPU, I/O, Memory, and AAS - grouped by hour
        sql = f"""
        SELECT 
            TO_CHAR(TRUNC(s.end_interval_time, 'HH'), 'YYYY-MM-DD HH24:MI') AS snap_time,
            -- 1. CPU (Average across all nodes, per hour)
            ROUND(AVG(sys.cpu_util), 2) AS avg_cluster_cpu_pct,
            -- 2. AAS (Sum of all work across the cluster, per hour)
            ROUND(AVG(sys.aas), 2) AS total_cluster_aas,
            -- 3. I/O (Total Throughput: Reads + Writes, per hour)
            ROUND(AVG(sys.phys_reads + sys.phys_writes) / 1024 / 1024, 2) AS total_io_mb_sec,
            -- 4. Memory (SGA + PGA Combined, per hour)
            ROUND(AVG(mem.total_pga + mem.total_sga) / 1024 / 1024 / 1024, 2) AS total_db_memory_gb,
            -- Breakdown of Memory (Optional, for reference)
            ROUND(AVG(mem.total_sga) / 1024 / 1024 / 1024, 2) AS sga_gb,
            ROUND(AVG(mem.total_pga) / 1024 / 1024 / 1024, 2) AS pga_gb
        FROM dba_hist_snapshot s
        -- JOIN METRICS (CPU, I/O, AAS)
        JOIN (
            SELECT 
                snap_id, dbid, instance_number,
                MAX(CASE WHEN metric_name = 'Host CPU Utilization (%)' THEN average END) AS cpu_util,
                MAX(CASE WHEN metric_name = 'Average Active Sessions' THEN average END) AS aas,
                MAX(CASE WHEN metric_name = 'Physical Read Total Bytes Per Sec' THEN average END) AS phys_reads,
                MAX(CASE WHEN metric_name = 'Physical Write Total Bytes Per Sec' THEN average END) AS phys_writes
            FROM dba_hist_sysmetric_summary
            WHERE metric_name IN ('Host CPU Utilization (%)', 'Average Active Sessions', 
                                  'Physical Read Total Bytes Per Sec', 'Physical Write Total Bytes Per Sec')
            GROUP BY snap_id, dbid, instance_number
        ) sys ON s.snap_id = sys.snap_id AND s.dbid = sys.dbid AND s.instance_number = sys.instance_number
        -- JOIN MEMORY (SGA + PGA)
        LEFT JOIN (
            SELECT 
                snap_id, dbid, instance_number,
                -- Get PGA
                (SELECT MAX(value) FROM dba_hist_pgastat p 
                 WHERE p.snap_id = main.snap_id AND p.dbid = main.dbid 
                 AND p.instance_number = main.instance_number 
                 AND p.name = 'total PGA allocated') AS total_pga,
                -- Get SGA (Sum of components)
                (SELECT SUM(value) FROM dba_hist_sga g 
                 WHERE g.snap_id = main.snap_id AND g.dbid = main.dbid 
                 AND g.instance_number = main.instance_number) AS total_sga
            FROM dba_hist_snapshot main
            GROUP BY snap_id, dbid, instance_number
        ) mem ON s.snap_id = mem.snap_id AND s.dbid = mem.dbid AND s.instance_number = mem.instance_number
        WHERE s.end_interval_time >= SYSDATE - {days}
        GROUP BY TRUNC(s.end_interval_time, 'HH')
        ORDER BY TRUNC(s.end_interval_time, 'HH') ASC
        """
        
        # Execute query
        result = run_oracle_query(sql, db)
        
        if isinstance(result, list) and result:
            # Parse the results into separate series for each metric
            cpu_data = []
            aas_data = []
            io_data = []
            memory_data = []
            
            for row in result:
                timestamp = row.get('SNAP_TIME', row.get('snap_time', ''))
                cpu_data.append({
                    'timestamp': timestamp,
                    'cpu_utilization': row.get('AVG_CLUSTER_CPU_PCT', row.get('avg_cluster_cpu_pct', 0))
                })
                aas_data.append({
                    'timestamp': timestamp,
                    'aas': row.get('TOTAL_CLUSTER_AAS', row.get('total_cluster_aas', 0))
                })
                io_data.append({
                    'timestamp': timestamp,
                    'io_operations': row.get('TOTAL_IO_MB_SEC', row.get('total_io_mb_sec', 0))
                })
                memory_data.append({
                    'timestamp': timestamp,
                    'memory_gb': row.get('TOTAL_DB_MEMORY_GB', row.get('total_db_memory_gb', 0)),
                    'sga_gb': row.get('SGA_GB', row.get('sga_gb', 0)),
                    'pga_gb': row.get('PGA_GB', row.get('pga_gb', 0))
                })
            
            return {
                'cpu': cpu_data,
                'aas': aas_data,
                'io': io_data,
                'memory': memory_data
            }
        else:
            return {'cpu': [], 'aas': [], 'io': [], 'memory': [], 'error': 'No data returned'}
    except Exception as e:
        return {'cpu': [], 'aas': [], 'io': [], 'memory': [], 'error': str(e)}

def get_sql_id_performance(sql_id: str, time_range: str, db: str) -> dict:
    """Get historical performance metrics for a specific SQL ID"""
    try:
        # Convert time range to days
        time_map = {
            '10 mins': 1,  # Minimum 1 day for AWR data
            '1 hour': 1,
            '24 hours': 1,
            '3 days': 3,
            '7 days': 7,
            '1 month': 30
        }
        days = time_map.get(time_range, 30)
        
        # Query for SQL performance from AWR history
        sql = f"""
        SELECT 
            TO_CHAR(s.begin_interval_time, 'YYYY-MM-DD HH24:MI') AS start_time,
            h.instance_number AS inst_id,
            h.plan_hash_value,
            h.executions_delta AS execs,
            -- Elapsed Time (converted to seconds)
            ROUND(h.elapsed_time_delta / 1000000, 2) AS total_elapsed_sec,
            ROUND((h.elapsed_time_delta / NULLIF(h.executions_delta, 0)) / 1000000, 4) AS avg_elapsed_sec,
            -- CPU Time (seconds)
            ROUND((h.cpu_time_delta / NULLIF(h.executions_delta, 0)) / 1000000, 4) AS avg_cpu_sec,
            -- I/O (Disk Reads + Buffer Gets)
            ROUND((h.disk_reads_delta / NULLIF(h.executions_delta, 0)), 2) AS avg_phys_reads,
            -- Memory (Buffer Gets are the best proxy for memory usage in SQL stats)
            ROUND((h.buffer_gets_delta / NULLIF(h.executions_delta, 0)), 2) AS avg_buffer_gets,
            -- I/O Wait Time (seconds)
            ROUND((h.iowait_delta / NULLIF(h.executions_delta, 0)) / 1000000, 4) AS avg_io_wait_sec
        FROM 
            dba_hist_sqlstat h
        JOIN 
            dba_hist_snapshot s ON h.snap_id = s.snap_id 
            AND h.dbid = s.dbid 
            AND h.instance_number = s.instance_number
        WHERE 
            UPPER(h.sql_id) = UPPER('{sql_id}')
            AND s.begin_interval_time >= SYSDATE - {days}
            AND h.executions_delta > 0
        ORDER BY 
            s.begin_interval_time DESC, h.instance_number
        """
        
        result = run_oracle_query(sql, db)
        
        # Check if result is an error dict
        if isinstance(result, dict) and "error" in result:
            return {
                'sql_id': sql_id,
                'status': 'error',
                'error': result.get('error', 'Unknown error'),
                'message': f'Error querying SQL ID {sql_id}: {result.get("error", "Unknown error")}'
            }
        
        # Check if result is a valid list with data
        if isinstance(result, list):
            if result:
                # Get execution plan info - try multiple sources
                sql_text = 'N/A'
                plan_hash = 'N/A'
                
                # Try 1: dba_hist_sqltext (AWR historical data)
                plan_sql = f"""
                SELECT DISTINCT
                    plan_hash_value,
                    SUBSTR(sql_text, 1, 500) AS sql_text
                FROM dba_hist_sqltext
                WHERE UPPER(sql_id) = UPPER('{sql_id}')
                FETCH FIRST 1 ROWS ONLY
                """
                plan_result = run_oracle_query(plan_sql, db)
                
                # Check if result is valid (not an error dict)
                if isinstance(plan_result, list) and plan_result:
                    sql_text = plan_result[0].get('SQL_TEXT', plan_result[0].get('sql_text', 'N/A'))
                    plan_hash = plan_result[0].get('PLAN_HASH_VALUE', plan_result[0].get('plan_hash_value', 'N/A'))
                
                # Try 2: v$sqlarea (current shared pool - for recent SQL IDs)
                if (sql_text == 'N/A' or plan_hash == 'N/A'):
                    plan_sql_vsql = f"""
                    SELECT DISTINCT
                        plan_hash_value,
                        SUBSTR(sql_text, 1, 500) AS sql_text
                    FROM v$sqlarea
                    WHERE UPPER(sql_id) = UPPER('{sql_id}')
                    AND ROWNUM = 1
                    """
                    plan_result_vsql = run_oracle_query(plan_sql_vsql, db)
                    
                    # Check if result is valid (not an error dict)
                    if isinstance(plan_result_vsql, list) and plan_result_vsql:
                        if sql_text == 'N/A':
                            sql_text = plan_result_vsql[0].get('SQL_TEXT', plan_result_vsql[0].get('sql_text', 'N/A'))
                        if plan_hash == 'N/A':
                            plan_hash = plan_result_vsql[0].get('PLAN_HASH_VALUE', plan_result_vsql[0].get('plan_hash_value', 'N/A'))
                
                # Try 3: v$sql (alternative view)
                if (sql_text == 'N/A' or plan_hash == 'N/A'):
                    plan_sql_vsql2 = f"""
                    SELECT DISTINCT
                        plan_hash_value,
                        SUBSTR(sql_text, 1, 500) AS sql_text
                    FROM v$sql
                    WHERE UPPER(sql_id) = UPPER('{sql_id}')
                    AND ROWNUM = 1
                    """
                    plan_result_vsql2 = run_oracle_query(plan_sql_vsql2, db)
                    
                    # Check if result is valid (not an error dict)
                    if isinstance(plan_result_vsql2, list) and plan_result_vsql2:
                        if sql_text == 'N/A':
                            sql_text = plan_result_vsql2[0].get('SQL_TEXT', plan_result_vsql2[0].get('sql_text', 'N/A'))
                        if plan_hash == 'N/A':
                            plan_hash = plan_result_vsql2[0].get('PLAN_HASH_VALUE', plan_result_vsql2[0].get('plan_hash_value', 'N/A'))
                
                # Try 4: Get plan_hash from the performance data if available
                if plan_hash == 'N/A' and result:
                    # Check if plan_hash_value is in the performance data
                    # Try multiple column name variations
                    first_row = result[0]
                    plan_hash_from_data = (
                        first_row.get('PLAN_HASH_VALUE') or 
                        first_row.get('plan_hash_value') or
                        first_row.get('PLAN_HASH') or
                        first_row.get('plan_hash')
                    )
                    if plan_hash_from_data and plan_hash_from_data != 'N/A' and str(plan_hash_from_data).strip():
                        plan_hash = str(plan_hash_from_data)
                
                return {
                    'sql_id': sql_id,
                    'sql_text': sql_text,
                    'plan_hash_value': plan_hash,
                    'performance_data': result,
                    'status': 'success'
                }
            else:
                return {
                    'sql_id': sql_id,
                    'status': 'not_found',
                    'message': f'No historical data found for SQL ID {sql_id} in the last {time_range}. Try a longer time range or verify the SQL ID exists.'
                }
        else:
            return {
                'sql_id': sql_id,
                'status': 'error',
                'error': f'Unexpected result type: {type(result)}',
                'message': f'Unexpected result format when querying SQL ID {sql_id}'
            }
    except Exception as e:
        return {'sql_id': sql_id, 'status': 'error', 'error': str(e)}

def get_table_sql_history(table_name: str, time_range: str, db: str) -> dict:
    """Get all SQL IDs and SQLs executed using a specific table"""
    try:
        # Convert time range to days
        time_map = {
            '10 mins': 30,  # Use 30 days minimum for AWR data
            '1 hour': 30,
            '7 days': 7,
            '1 month': 30
        }
        days = time_map.get(time_range, 30)
        
        # Normalize table name to uppercase for case-insensitive search
        table_name_upper = table_name.upper().strip()
        
        # Query for table usage from AWR history
        sql = f"""
        SELECT
            t.sql_id,
            -- Aggregated Stats
            SUM(h.executions_delta) AS total_execs,
            ROUND(SUM(h.elapsed_time_delta) / 1000000, 2) AS total_elapsed_sec,
            -- Averages per Execution
            ROUND(SUM(h.elapsed_time_delta) / NULLIF(SUM(h.executions_delta), 0) / 1000000, 4) AS avg_elapsed_sec,
            ROUND(SUM(h.cpu_time_delta) / NULLIF(SUM(h.executions_delta), 0) / 1000000, 4) AS avg_cpu_sec,
            -- I/O Metrics
            ROUND(SUM(h.disk_reads_delta) / NULLIF(SUM(h.executions_delta), 0), 2) AS avg_physical_reads,
            -- Memory Metric (Logical I/O)
            ROUND(SUM(h.buffer_gets_delta) / NULLIF(SUM(h.executions_delta), 0), 2) AS avg_buffer_gets,
            MIN(TO_CHAR(s.begin_interval_time, 'YYYY-MM-DD')) as first_seen,
            MAX(TO_CHAR(s.begin_interval_time, 'YYYY-MM-DD')) as last_seen,
            -- Preview of the SQL Text (Using MAX to avoid GROUP BY CLOB error)
            MAX(DBMS_LOB.SUBSTR(t.sql_text, 100, 1)) AS sql_text_preview
        FROM
            dba_hist_sqltext t
        JOIN
            dba_hist_sqlstat h ON t.sql_id = h.sql_id AND t.dbid = h.dbid
        JOIN
            dba_hist_snapshot s ON h.snap_id = s.snap_id
            AND h.dbid = s.dbid
            AND h.instance_number = s.instance_number
        WHERE
            UPPER(DBMS_LOB.SUBSTR(t.sql_text, 4000, 1)) LIKE '%{table_name_upper}%'
            AND s.begin_interval_time >= SYSDATE - {days}
            AND h.executions_delta > 0
        GROUP BY
            t.sql_id
        ORDER BY
            total_elapsed_sec DESC
        """
        
        result = run_oracle_query(sql, db)
        
        # Check if result is an error dict
        if isinstance(result, dict) and "error" in result:
            return {
                'table_name': table_name,
                'time_range': time_range,
                'status': 'error',
                'error': result.get('error', 'Unknown error'),
                'message': f'Error querying table {table_name}: {result.get("error", "Unknown error")}'
            }
        
        # Check if result is a valid list with data
        if isinstance(result, list):
            if result:
                return {
                    'table_name': table_name,
                    'time_range': time_range,
                    'sql_count': len(result),
                    'sqls': result,
                    'status': 'success'
                }
            else:
                return {
                    'table_name': table_name,
                    'time_range': time_range,
                    'status': 'not_found',
                    'message': f'No SQLs found using table {table_name} in the last {time_range}. Try a longer time range or verify the table name exists.'
                }
        else:
            return {
                'table_name': table_name,
                'time_range': time_range,
                'status': 'error',
                'error': f'Unexpected result type: {type(result)}',
                'message': f'Unexpected result format when querying table {table_name}'
            }
    except Exception as e:
        return {'table_name': table_name, 'status': 'error', 'error': str(e)}

def get_top_heavily_used_tables(time_range: str, db: str) -> dict:
    """Get top 10 heavily used tables based on I/O and access patterns"""
    try:
        # Convert time range to days
        time_map = {
            '24 hours': 1,
            '3 days': 3,
            '7 days': 7,
            '1 month': 30
        }
        days = time_map.get(time_range, 7)
        
        # Query to find top tables by I/O and access patterns
        # Exclude default Oracle schemas
        sql = f"""
        SELECT 
            obj.owner,
            obj.object_name AS table_name,
            SUM(seg.logical_reads_delta) AS total_logical_reads,
            SUM(seg.physical_reads_delta) AS total_physical_reads,
            SUM(seg.physical_writes_delta) AS total_physical_writes,
            SUM(seg.physical_reads_delta + seg.physical_writes_delta) AS total_io,
            COUNT(DISTINCT seg.snap_id) AS snapshots_count,
            MAX(s.end_interval_time) AS last_accessed
        FROM dba_hist_seg_stat seg
        JOIN dba_hist_snapshot s ON seg.snap_id = s.snap_id 
            AND seg.dbid = s.dbid 
            AND seg.instance_number = s.instance_number
        JOIN dba_hist_seg_stat_obj obj ON seg.obj# = obj.obj# 
            AND seg.dataobj# = obj.dataobj#
            AND seg.dbid = obj.dbid
        WHERE s.end_interval_time >= SYSDATE - {days}
          AND obj.owner IS NOT NULL
          AND obj.object_name IS NOT NULL
          AND seg.logical_reads_delta > 0
          AND obj.object_type = 'TABLE'
          AND obj.owner NOT IN ('SYS', 'SYSTEM', 'SYSAUX', 'OUTLN', 'DBSNMP', 'XDB', 
                               'CTXSYS', 'MDSYS', 'OLAPSYS', 'ORDDATA', 'ORDSYS', 
                               'WMSYS', 'EXFSYS', 'LBACSYS', 'ODM', 'ODM_MTR', 
                               'ORDPLUGINS', 'SI_INFORMTN_SCHEMA', 'FLOWS_FILES', 
                               'APEX_030200', 'APEX_040000', 'APEX_050000', 'APEX_180100',
                               'APEX_190100', 'APEX_200100', 'APEX_210100', 'APEX_220100',
                               'ANONYMOUS', 'APPQOSSYS', 'AUDSYS', 'GSMADMIN_INTERNAL',
                               'GSMCATUSER', 'GSMUSER', 'OJVMSYS', 'SYSBACKUP', 'SYSDG',
                               'SYSKM', 'SYSRAC', 'WMSYS')
        GROUP BY obj.owner, obj.object_name
        ORDER BY total_io DESC
        FETCH FIRST 10 ROWS ONLY
        """
        
        result = run_oracle_query(sql, db)
        
        if isinstance(result, list) and result:
            return {
                'time_range': time_range,
                'tables': result,
                'status': 'success'
            }
        else:
            return {
                'time_range': time_range,
                'status': 'not_found',
                'message': f'No table usage data found for the last {time_range}'
            }
    except Exception as e:
        return {'time_range': time_range, 'status': 'error', 'error': str(e)}

# Removed tool_get_realtime_metrics - functionality moved to health check
# Real-time metrics are now included in health check reports

# ============================================================================
# 9. NEW FEATURES - SAVED QUERIES
# ============================================================================

def tool_save_query(name: str, sql: str, description: str = "") -> str:
    """Save frequently used queries"""
    query_entry = {
        "id": str(uuid.uuid4()),
        "name": name,
        "sql": sql,
        "description": description,
        "created": datetime.now().isoformat()
    }
    st.session_state["saved_queries"].append(query_entry)
    return f"Query '{name}' saved successfully. You can access it from the sidebar."

def tool_load_saved_query(query_id: str) -> str:
    """Load a saved query"""
    query = next((q for q in st.session_state["saved_queries"] if q["id"] == query_id), None)
    if query:
        return f"Loaded query '{query['name']}':\n\n```sql\n{query['sql']}\n```"
    return "Query not found."

# ============================================================================
# 10. JENKINS FUNCTIONS
# ============================================================================

def analyze_jenkins_failure(console_log):
    """Analyzes Jenkins console log failures"""
    max_chars = 12000 
    truncated_log = console_log[-max_chars:] if len(console_log) > max_chars else console_log

    llm_config = {
        "config_list": [{"model": "gpt-4o-mini", "api_key": openai_api_key}],
        "temperature": 0,
    }
    analyzer = AssistantAgent(
        name="Jenkins_Debugger",
        llm_config=llm_config,
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
        m = re.search(r"(\{.*\})", reply, flags=re.DOTALL)
        if m:
            return json.loads(m.group(1))
        return None
    except Exception:
        return None

def monitor_jenkins_build(server, queue_id):
    """Monitors Jenkins build until completion"""
    status_box = st.status("Job Triggered... Waiting for Queue...", expanded=True)
    
    try:
        build_number = None
        job_name = None
        
        max_retries = 30
        for _ in range(max_retries):
            try:
                q_item = server.get_queue_item(queue_id)
                if "executable" in q_item:
                    build_number = q_item["executable"]["number"]
                    job_name = st.session_state["polling_job"]
                    break
            except:
                pass
            time.sleep(1)
        
        if not build_number:
            status_box.update(label=" Timed out waiting for Queue", state="error")
            return None

        status_box.write(f" Build Started: #{build_number}")
        
        while True:
            build_info = server.get_build_info(job_name, build_number)
            res = build_info.get("result")
            
            if res:
                if res == "SUCCESS":
                    status_box.update(label=f" Build #{build_number} SUCCESS", state="complete", expanded=False)
                elif res == "ABORTED":
                    status_box.update(label=f" Build #{build_number} ABORTED", state="error")
                else:
                    status_box.update(label=f" Build #{build_number} FAILED", state="error")
                return build_info
            
            status_box.write("⚙️ Building... (Monitoring status)")
            time.sleep(2)
            
    except Exception as e:
        status_box.write(f"Error polling: {e}")
        status_box.update(state="error")
        return None

# ============================================================================
# 11. AGENT SETUP - FIXED SYSTEM MESSAGE BUG
# ============================================================================

llm_config = {
    "config_list": [{"model": "gpt-4o-mini", "api_key": openai_api_key}],
    "temperature": 0,
}

# Store base system message to prevent accumulation
base_system_message = """
You are an Oracle DBA and Jenkins Admin assistant.
- You can answer simple questions directly without using tools. For example:
  - "What is the name of the database?" → Use `get_database_info` tool, then reply with the answer and "TERMINATE"
  - "What database am I connected to?" → Answer directly: "You are connected to [current_db]. TERMINATE"
  - General Oracle questions → Answer based on your knowledge, then "TERMINATE"
- **CRITICAL TERMINATION RULE:** After providing ANY answer (whether using a tool or answering directly), you MUST end your response with "TERMINATE". Do NOT continue the conversation unless the user asks a follow-up question.
  - When asking for required information (like time ranges, patch descriptions, etc.), ask the question clearly, then immediately add "TERMINATE" at the end. The UI will handle collecting the inputs and send a new message when ready.
- **IMPORTANT:** When answering, provide ONLY your response. Do NOT repeat or echo back the conversation history, context prompts, or system messages. Answer directly and concisely.
- **CRITICAL - USE CONVERSATION HISTORY:** Always review the provided conversation history to understand context, recent tasks, generated reports, or previous results before deciding on actions.
- **CONTEXT AWARENESS:** 
  - If the user asks about a previous report (health, AWR, comparison) mentioned in the conversation history, reference that report and use the appropriate analysis tool (analyze_health_report, analyze_awr_report) with the report context.
  - If the query relates to prior discussions (e.g., follow-up on a report, database state, or job outcome), reference or build on that information directly if possible, without re-executing tools unnecessarily.
  - If the user asks "What did you say about X?" or "Tell me more about Y", look in the conversation history for the previous answer.
- **TOOL USAGE:** Only use tools when new execution is required; otherwise, reason step-by-step from history. If a report was already generated and the user asks questions about it, use the analysis tools rather than regenerating.
- **Never include "Conversation History" or "New User Query" in your responses - these are internal context only.**
- **Database Info:** Use `get_database_info` to get database name. After getting the result, provide the answer and immediately reply "TERMINATE". Do NOT call the tool multiple times.
- **Database:** Use `switch_db` to change target. After switching, confirm and "TERMINATE".
- **Query against the DB:** Use 'run_sql' to run oracle queries. If you face any error, rethink the query, analyze the failed thing and re-write the query. After showing results, "TERMINATE".
- **Performance:** Use `generate_performance_report`. 
  - **CRITICAL:** If the user mentions "performance report", "generate report", "AWR report", "ASH report", or asks to "generate a performance report" (with or without phrases like "I need you to ask me for the start time and end time"), but hasn't provided time information (no start_time, end_time, or hours_back mentioned), you MUST immediately ask: "Please provide the start time and end time for the performance report. You can either specify a time range (e.g., '2024-01-15 10:00:00 to 2024-01-15 11:00:00') or say 'last N hours' (e.g., 'last 3 hours')." Then immediately reply "TERMINATE" - do NOT wait for a response or call any tools. The UI will handle collecting the time inputs.
  - If user says "last 3 hours" or "last N hours", use `hours_back=3` (or the specified number).
  - If user provides specific times like "10am to 12pm" or "2024-01-15 10:00:00 to 2024-01-15 11:00:00", use `start_time` and `end_time` (format: YYYY-MM-DD HH:MM:SS).
  - After generating the report, "TERMINATE".
- **Compare AWR Reports:** Use `compare_awr_reports` to compare two AWR report periods.
  - **IMPORTANT:** If the user says "I want to compare AWR reports" or "compare AWR reports" but hasn't provided time information yet, you MUST ask them: "Please provide the baseline period (start time and end time) and target period (start time and end time) for the AWR comparison. Format: YYYY-MM-DD HH:MM:SS (e.g., Baseline: 2024-01-15 10:00:00 to 2024-01-15 11:00:00, Target: 2024-01-15 14:00:00 to 2024-01-15 15:00:00)." Then immediately reply "TERMINATE" - do NOT wait for a response or call any tools. The UI will handle collecting the time inputs.
  - Requires: `baseline_start_time`, `baseline_end_time`, `target_start_time`, `target_end_time` (format: YYYY-MM-DD HH:MM:SS).
  - After generating the comparison, "TERMINATE".
- **Analysis:** If user asks questions about the report ("why is cpu high?", "analyze it"), use `analyze_report`. After analysis, "TERMINATE".
- **Health:** Use `health_check`. After showing results, "TERMINATE".
- **Sessions:** Use `list_sessions` to see active sessions, `kill_session` to terminate problematic ones. After showing results, "TERMINATE".
- **Tablespaces:** Use `check_tablespaces` to monitor space usage. After showing results, "TERMINATE".
- **Health Check:** The `health_check` tool now includes real-time CPU/IO/Memory utilization and top SQLs. Use it for comprehensive database health assessment.
- **Jenkins Tools:**
  - Use `search_jenkins` to find jobs by name. When it returns `::ARTIFACT_JENKINS::`, reply "TERMINATE" immediately.
  - Use `get_build_info` to get detailed information about a build (job_name, optional build_number).
  - Use `get_build_console` to get console output for a build (job_name, optional build_number).
  - Use `trigger_build` to start a new build (job_name, optional parameters as JSON string).
  - Use `get_build_history` to see recent builds for a job (job_name, optional limit).
  - Use `analyze_build_failure` to analyze why a build failed (job_name, optional build_number).
  - Use `compare_builds` to compare two builds (job_name, build_number1, build_number2).
  - Use `get_job_config` to get job configuration XML (job_name).
  - Use `get_build_artifacts` to list build artifacts (job_name, optional build_number).
  When any Jenkins tool returns `::ARTIFACT_::`, your task is complete - reply with "TERMINATE" immediately.
- **Patches:** Use `download_patch` when user asks to download Oracle patches (e.g., RU, OJVM, GI).
  - **IMPORTANT:** If the user says "I want to download an Oracle patch" or "download Oracle patch" but hasn't provided the patch description yet, you MUST ask them: "Please provide the patch description. For example: 'Oracle Database Release Update 19.20.0.0.0 for Linux x86-64' or 'OJVM patch for Oracle 19c'." Then immediately reply "TERMINATE" - do NOT wait for a response or call any tools. The user will provide the description in the chat.
  - After showing results, "TERMINATE".
- **Saved Queries:** Use `save_query` to save frequently used queries. After saving, "TERMINATE".
- **TERMINATION RULE:** When a tool returns a message containing `::ARTIFACT_::` (like `::ARTIFACT_JENKINS::`, `::ARTIFACT_HEALTH::`, `::ARTIFACT_METRICS::`, etc.), your task is complete. Reply with "TERMINATE" immediately. Do NOT ask the user to select or do anything - the UI will handle the artifact display.
- **NEVER call the same tool multiple times for the same question. If you already have the answer, provide it and TERMINATE.**
- Always provide helpful, clear answers. After providing an answer, ALWAYS end with "TERMINATE".
"""

if st.session_state["system_message_base"] is None:
    st.session_state["system_message_base"] = base_system_message

oracle_admin = AssistantAgent(
    name="Oracle_Admin",
    llm_config=llm_config,
    system_message=st.session_state["system_message_base"]
)

def is_termination_message(msg):
    """Enhanced termination detection - checks for TERMINATE, artifacts, and prevents infinite loops"""
    content = str(msg.get("content", "")).strip()
    
    # Standard termination markers
    if "TERMINATE" in content or "::ARTIFACT_" in content:
        return True
    
    # Additional check: if message is very short and just repeats the same info, consider it done
    # This helps prevent loops where agent keeps saying the same thing
    if len(content) < 50 and any(phrase in content.lower() for phrase in [
        "the database name is", "you are connected to", "database is", "connected to"
    ]):
        # If it's a simple answer, it should have TERMINATE, but if not, we'll accept it as done
        # This is a safety net
        pass
    
    return False

user_proxy = UserProxyAgent(
    name="User_Proxy",
    human_input_mode="NEVER",
    max_consecutive_auto_reply=5,  # Reduced from 10 to prevent long loops
    code_execution_config=False,
    is_termination_msg=is_termination_message
)

# Register Tools
register_function(tool_get_database_info, caller=oracle_admin, executor=user_proxy, name="get_database_info", description="Get database name and information")
register_function(tool_change_database, caller=oracle_admin, executor=user_proxy, name="switch_db", description="Switch DB")
register_function(tool_run_sql, caller=oracle_admin, executor=user_proxy, name="run_sql", description="Run SQL")
register_function(tool_search_jenkins_jobs, caller=oracle_admin, executor=user_proxy, name="search_jenkins", description="Search Jenkins jobs by name")
register_function(tool_get_build_info, caller=oracle_admin, executor=user_proxy, name="get_build_info", description="Get detailed information about a Jenkins build. Provide job_name and optional build_number (defaults to latest)")
register_function(tool_get_build_console, caller=oracle_admin, executor=user_proxy, name="get_build_console", description="Get console output for a Jenkins build. Provide job_name and optional build_number (defaults to latest)")
register_function(tool_trigger_build, caller=oracle_admin, executor=user_proxy, name="trigger_build", description="Trigger a Jenkins build. Provide job_name and optional parameters as JSON string")
register_function(tool_get_build_history, caller=oracle_admin, executor=user_proxy, name="get_build_history", description="Get build history for a Jenkins job. Provide job_name and optional limit (default 10)")
register_function(tool_analyze_build_failure, caller=oracle_admin, executor=user_proxy, name="analyze_build_failure", description="Analyze why a Jenkins build failed. Provide job_name and optional build_number (defaults to latest failed build)")
register_function(tool_compare_builds, caller=oracle_admin, executor=user_proxy, name="compare_builds", description="Compare two builds of the same Jenkins job. Provide job_name, build_number1, and build_number2")
register_function(tool_get_job_config, caller=oracle_admin, executor=user_proxy, name="get_job_config", description="Get the configuration XML for a Jenkins job. Provide job_name")
register_function(tool_get_build_artifacts, caller=oracle_admin, executor=user_proxy, name="get_build_artifacts", description="List artifacts produced by a Jenkins build. Provide job_name and optional build_number (defaults to latest)")
register_function(tool_run_health_check, caller=oracle_admin, executor=user_proxy, name="health_check", description="Run Health Check")
register_function(tool_performance_report, caller=oracle_admin, executor=user_proxy, name="generate_performance_report", description="Generates AWR/ASH")
register_function(tool_analyze_report_content, caller=oracle_admin, executor=user_proxy, name="analyze_report", description="Analyze last report")
register_function(tool_analyze_health_report, caller=oracle_admin, executor=user_proxy, name="analyze_health_report", description="Analyze last health report")
register_function(tool_download_patch_wrapper, caller=oracle_admin, executor=user_proxy, name="download_patch", description="Download Oracle Patches")
register_function(tool_list_active_sessions, caller=oracle_admin, executor=user_proxy, name="list_sessions", description="List active sessions")
register_function(tool_kill_session, caller=oracle_admin, executor=user_proxy, name="kill_session", description="Kill session (requires confirmation)")
register_function(tool_check_tablespaces, caller=oracle_admin, executor=user_proxy, name="check_tablespaces", description="Check tablespace usage")
register_function(tool_compare_awr_reports, caller=oracle_admin, executor=user_proxy, name="compare_awr_reports", description="Compare AWR reports for baseline and target time periods. Requires baseline_start_time, baseline_end_time, target_start_time, target_end_time (format: YYYY-MM-DD HH:MM:SS) with detailed LLM analysis")
register_function(tool_save_query, caller=oracle_admin, executor=user_proxy, name="save_query", description="Save SQL query")

# ============================================================================
# 12. UI IMPLEMENTATION - FIXED SYSTEM MESSAGE ACCUMULATION
# ============================================================================

def show_datetime_picker(label: str, key: str, default_date=None, default_time=None):
    """Helper function to show date and time pickers in chat"""
    if default_date is None:
        default_date = datetime.now().date()
    if default_time is None:
        default_time = datetime.now().time().replace(second=0, microsecond=0)
    
    col1, col2 = st.columns(2)
    with col1:
        selected_date = st.date_input(f"{label} - Date", value=default_date, key=f"{key}_date")
    with col2:
        selected_time = st.time_input(f"{label} - Time", value=default_time, key=f"{key}_time")
    
    # Combine date and time into datetime string
    datetime_str = f"{selected_date} {selected_time.strftime('%H:%M:%S')}"
    return datetime_str

def handle_agent_execution(prompt):
    """Handles the full flow: UI updates -> Agent run -> Response -> Rerun"""
    
    # Note: User message is already added before calling this function
    # This function only processes the agent response
    
    # Early check: If user asked for performance report without time info, prepare to intercept generic response
    user_msg_lower = prompt.lower() if prompt else ""
    session_msgs = st.session_state.get("messages", [])
    last_user_msg = ""
    if session_msgs:
        for msg in reversed(session_msgs[-5:]):
            if msg.get('role') == 'user':
                last_user_msg = msg.get('content', '').lower()
                break
    
    all_user_text_check = (user_msg_lower + " " + last_user_msg).lower()
    is_perf_report_request = (
        ("performance report" in all_user_text_check or ("generate" in all_user_text_check and "report" in all_user_text_check)) and
        ("start time" not in all_user_text_check and "end time" not in all_user_text_check and "hours" not in all_user_text_check and "last" not in all_user_text_check)
    )
    
    # Enhanced chat memory: Use last 20 messages for better context
    recent_history = st.session_state["messages"][-20:]
    
    # Build comprehensive context including:
    # 1. Recent conversation history
    # 2. Recent artifacts/reports that might be relevant
    # 3. Current database state
    
    history_context_parts = []
    
    # Add conversation history (last 20 messages, but summarize longer ones)
    for msg in recent_history[:-1]:  # Exclude the current message
        role = msg.get('role', 'user').title()
        content = msg.get('content', '')
        
        # Summarize very long messages but keep important parts
        if len(content) > 500:
            # Keep first 200 chars and last 100 chars for context
            content = content[:200] + "...[truncated]..." + content[-100:]
        elif len(content) > 300:
            # Keep first 300 chars
            content = content[:300] + "..."
        
        # Skip internal context markers
        if any(pattern in content for pattern in ["Conversation History", "New User Query:", "System Context:"]):
            continue
            
        history_context_parts.append(f"{role}: {content}")
    
    history_context = "\n".join(history_context_parts)
    
    # Add context about recent artifacts/reports
    artifact_context = []
    recent_artifacts = []
    for art_id, artifact in list(st.session_state.get("artifacts", {}).items())[-5:]:  # Last 5 artifacts
        art_type = artifact.get("type", "UNKNOWN")
        timestamp = artifact.get("timestamp", "N/A")
        
        if art_type == "JENKINS_BUILD_HISTORY":
            job_name = artifact.get("job_name", "Unknown")
            artifact_context.append(f"- Recent Jenkins build history for job: {job_name} (timestamp: {timestamp})")
        elif art_type == "HEALTH_REPORT":
            artifact_context.append(f"- Recent health report available (timestamp: {timestamp})")
        elif art_type == "AWR_REPORT":
            artifact_context.append(f"- Recent AWR report available (timestamp: {timestamp})")
        elif art_type == "COMPARE_AWR":
            artifact_context.append(f"- Recent AWR comparison report available (timestamp: {timestamp})")
    
    artifact_summary = "\n".join(artifact_context) if artifact_context else "No recent reports or artifacts."
    
    # Detect if this is a direct button click (these should ALWAYS execute the tool, not reference previous results)
    button_queries = [
        "List active database sessions.",
        "Check tablespace usage.",
        "Run a full health check on the database.",
        "Analyze the report generated above. Highlight top wait events and SQLs."
    ]
    is_button_click = prompt.strip() in button_queries
    
    # Build the prompt with special handling for button clicks
    if is_button_click:
        # For button clicks, explicitly instruct to execute the tool, not reference previous results
        button_instruction = """
CRITICAL: This is a direct button click. You MUST execute the tool to get fresh data. 
DO NOT reference previous results or say "based on this context". 
ALWAYS run the appropriate tool to get current, up-to-date information.
After executing the tool and showing results, reply with "TERMINATE"."""
        full_prompt = f"""Conversation History (for context - use this to understand what we've discussed):
{history_context}

Recent Reports/Artifacts Available:
{artifact_summary}

Current Database: {st.session_state.get('current_db', 'Unknown')}

New User Query: {prompt}

{button_instruction}"""
    else:
        # For regular queries, use the normal context-aware instructions
        full_prompt = f"""Conversation History (for context - use this to understand what we've discussed):
{history_context}

Recent Reports/Artifacts Available:
{artifact_summary}

Current Database: {st.session_state.get('current_db', 'Unknown')}

New User Query: {prompt}

IMPORTANT: Use the conversation history above to understand context. If the user is asking about:
- A previous report (health, AWR, comparison) → Reference that report and use appropriate analysis tools
- A previous conversation topic → Build on that context
- A follow-up question → Reference the previous answer

Consider the history above to inform your response. Reference past results, reports, or states if relevant. Use tools only if necessary based on this context."""
    
    # EARLY CHECK: If user asked for performance report, prepare to intercept generic responses
    # Do this BEFORE agent runs so we can use it later
    user_request_lower = prompt.lower() if prompt else ""
    session_messages = st.session_state.get("messages", [])
    recent_user_msg = [msg.get('content', '').lower() for msg in session_messages[-3:] if msg.get('role') == 'user']
    all_user_text_early = (user_request_lower + " " + " ".join(recent_user_msg)).lower()
    user_wants_perf_report = (("performance report" in all_user_text_early or 
                               ("generate" in all_user_text_early and "report" in all_user_text_early)) and
                              ("ask me for" in all_user_text_early or "need you to ask" in all_user_text_early or
                               "before generating" in all_user_text_early or
                               ("start time" not in all_user_text_early and "end time" not in all_user_text_early and 
                                not any(char.isdigit() for char in all_user_text_early if "hour" in all_user_text_early))))
    
    # FIX: Reset system message to base instead of appending
    ctx = f"\n[System Context: Connected to {st.session_state['current_db']}. Time: {datetime.now()}]"
    oracle_admin.update_system_message(st.session_state["system_message_base"] + ctx)
    
    # Show user input immediately (it's already in messages, but ensure it's visible)
    # The message is already added before this function is called, so it will show in the chat
    # Note: Spinner is shown in the chat message area, not here
    
    try:
        chat_res = user_proxy.initiate_chat(
            oracle_admin, 
            message=full_prompt, 
            clear_history=False
        )
        
        # Improved response extraction - find the actual assistant response
        # Filter out ALL internal context and system messages
        final_response = None
        assistant_responses = []
        tool_responses_with_artifacts = []
        
        # Collect all assistant responses (skip user messages and context)
        for m in chat_res.chat_history:
            content = str(m.get('content', '')).strip()
            role = m.get('role', '')
            name = str(m.get('name', ''))
            
            # Skip empty messages
            if not content:
                continue
            
            # STRICT FILTERING: Skip any message containing internal context markers
            skip_patterns = [
                "Conversation History (for context):",
                "Conversation History (for context - use this",
                "New User Query:",
                "Consider the history above",
                "Reference past results",
                "Use tools only if necessary",
                "based on this context",
                "System Context: Connected to",
                "Recent Reports/Artifacts Available:",
                "IMPORTANT: Use the conversation history",
                "If the user is asking about:",
                "A previous report",
                "A previous conversation topic",
                "A follow-up question"
            ]
            
            if any(pattern in content for pattern in skip_patterns):
                continue
            
            # PRIORITY: Look for tool responses that contain artifacts (these are the actual results)
            # Also check for tool responses from User_Proxy (these are the actual tool outputs)
            if "::ARTIFACT_" in content:
                # This is a tool response with an artifact - use it!
                clean_artifact_content = content.replace("TERMINATE", "").strip()
                if clean_artifact_content:
                    tool_responses_with_artifacts.append(clean_artifact_content)
            # Also check if this is a User_Proxy message (tool execution result)
            if name == 'User_Proxy' and "::ARTIFACT_" in content:
                # Tool execution result from User_Proxy - this has the correct artifact ID
                # This is CRITICAL - User_Proxy messages contain the actual tool output with correct artifact IDs
                clean_artifact_content = content.replace("TERMINATE", "").strip()
                if clean_artifact_content and clean_artifact_content not in tool_responses_with_artifacts:
                    tool_responses_with_artifacts.append(clean_artifact_content)
            
            # Skip user role messages
            if role == 'user':
                continue
            
            # Skip messages that are just the prompt being echoed back
            if content.startswith("Conversation History") or "New User Query:" in content:
                continue
            
            # Collect assistant responses - prioritize Oracle_Admin responses
            if role == 'assistant' or 'Oracle_Admin' in name:
                clean_content = content.replace("TERMINATE", "").strip()
                
                # Skip if it's still the context prompt
                if any(pattern in clean_content for pattern in skip_patterns):
                    continue
                
                # Don't skip short responses if they contain SQL results or important info
                # Also don't skip questions asking for user input (these are important!)
                is_input_question = any(phrase in clean_content.lower() for phrase in ["please provide", "provide the", "ask me for", "need you to ask"])
                if clean_content and (len(clean_content) > 5 or "SQL Result" in clean_content or "**" in clean_content or "::ARTIFACT_" in clean_content or is_input_question):
                    assistant_responses.append(clean_content)
        
        # PRIORITY 1: Use tool responses with artifacts (these contain the actual results)
        # CRITICAL: Tool responses have the correct artifact IDs - always use them if available
        if tool_responses_with_artifacts:
            # Get the most recent tool response with artifact - these have the correct artifact IDs
            # Don't let agent responses override tool responses with artifacts
            final_response = tool_responses_with_artifacts[-1]
            # Ensure artifact IDs are preserved - the tool response already has the correct format
        
        # PRIORITY 2: Get the last meaningful assistant response (only if no tool response with artifact)
        elif assistant_responses:
            # FIRST: Check for questions asking for user input (these are critical and should be shown immediately)
            for resp in reversed(assistant_responses):
                if resp and resp != "TERMINATE":
                    # Skip if it contains context patterns
                    if any(pattern in resp for pattern in skip_patterns):
                        continue
                    # Check if this is a question asking for input (like time ranges, patch descriptions, etc.)
                    resp_lower = resp.lower()
                    if any(phrase in resp_lower for phrase in ["please provide", "provide the", "ask me for", "need you to ask", "provide the start time", "provide the baseline", "start time", "end time", "time range", "baseline period", "target period"]):
                        final_response = resp
                        break
            
            # SECOND: If no input question found, prefer responses with SQL results, artifacts, or detailed content
            if not final_response:
                for resp in reversed(assistant_responses):
                    if resp and resp != "TERMINATE":
                        # Skip if it contains context patterns
                        if any(pattern in resp for pattern in skip_patterns):
                            continue
                        # If assistant response has artifact but missing ID, try to find it from tool responses
                        if "::ARTIFACT_" in resp:
                            # Check if artifact ID is missing (empty between colons)
                            if "::ARTIFACT_" in resp and "::" in resp.split("::ARTIFACT_")[1] and not resp.split("::ARTIFACT_")[1].split("::")[0]:
                                # Artifact ID is missing - try to get it from tool responses
                                if tool_responses_with_artifacts:
                                    # Extract artifact ID from tool response
                                    tool_resp = tool_responses_with_artifacts[-1]
                                    artifact_match = re.search(r"::ARTIFACT_(\w+):([^:]+)::", tool_resp)
                                    if artifact_match:
                                        artifact_type = artifact_match.group(1)
                                        artifact_id = artifact_match.group(2)
                                        # Reconstruct response with correct artifact ID
                                        final_response = resp.replace(f"::ARTIFACT_{artifact_type}::", f"::ARTIFACT_{artifact_type}:{artifact_id}::")
                                        break
                        # Prioritize responses with SQL results, artifacts, or detailed content
                        if "SQL Result" in resp or "::ARTIFACT_" in resp or "**" in resp or len(resp) > 20:
                            final_response = resp
                            break
            
            # If no prioritized response, get the last non-TERMINATE response
            if not final_response:
                for resp in reversed(assistant_responses):
                    if resp and resp != "TERMINATE" and len(resp) > 5:
                        # Final check for context patterns
                        if not any(pattern in resp for pattern in skip_patterns):
                            final_response = resp
                            break
        
        # Fallback: try to get any response from the last few messages (including tool responses)
        if not final_response:
            # FIRST PRIORITY: Look for questions asking for user input
            for m in reversed(chat_res.chat_history[-10:]):  # Check last 10 messages
                content = str(m.get('content', '')).strip()
                # Skip context patterns first
                if any(pattern in content for pattern in skip_patterns):
                    continue
                # Prioritize questions asking for user input (these are critical!)
                if content and m.get('role') != 'user':
                    content_lower = content.lower()
                    if any(phrase in content_lower for phrase in ["please provide", "provide the", "ask me for", "need you to ask", "provide the start time", "provide the baseline", "provide the patch", "start time", "end time", "time range", "baseline period", "target period"]):
                        clean_content = content.replace("TERMINATE", "").strip()
                        if clean_content and len(clean_content) > 5:
                            final_response = clean_content
                            break
            
            # SECOND PRIORITY: Look for artifacts or other important messages
            if not final_response:
                for m in reversed(chat_res.chat_history[-10:]):  # Check last 10 messages
                    content = str(m.get('content', '')).strip()
                    # Skip context patterns first
                    if any(pattern in content for pattern in skip_patterns):
                        continue
                    # Prioritize messages with artifacts
                    if content and "::ARTIFACT_" in content:
                        clean_content = content.replace("TERMINATE", "").strip()
                        if clean_content:
                            final_response = clean_content
                            break
                    elif content and m.get('role') != 'user' and len(content) > 5:
                        # Remove TERMINATE but keep the rest of the message
                        clean_content = content.replace("TERMINATE", "").strip()
                        if clean_content and len(clean_content) > 5:
                            final_response = clean_content
                            break
        
        # Check if final_response is the generic fallback message and user asked for performance report
        # This handles the case where agent generates the generic message instead of asking for time
        # Use case-insensitive matching and check for key phrases
        final_response_lower = final_response.lower() if final_response else ""
        db_name = st.session_state.get('current_db', '').upper()
        
        # More flexible pattern matching - check for key phrases
        # Check multiple patterns to catch variations
        is_generic_message = False
        if final_response:
            # Pattern 1: Contains "connected to database" and "how can i help"
            if ("connected to database" in final_response_lower or "i'm connected" in final_response_lower) and "how can i help" in final_response_lower:
                is_generic_message = True
            # Pattern 2: Contains DB name and "how can i help" and is short (likely generic greeting)
            elif db_name and db_name in final_response.upper() and "how can i help" in final_response_lower and len(final_response) < 150:
                is_generic_message = True
            # Pattern 3: Just "how can i help you" as standalone response (very short)
            elif final_response_lower.strip() in ["how can i help you?", "how can i help you"]:
                is_generic_message = True
        
        # Use the early check we did at the start of the function, or re-check here
        if is_generic_message:
            # Get the most recent user message from session state (most reliable)
            recent_user_messages = [msg.get('content', '').lower() for msg in st.session_state.get("messages", [])[-5:] if msg.get('role') == 'user']
            # Also check chat history
            chat_user_messages = [msg.get('content', '').lower() for msg in chat_res.chat_history[-5:] if msg.get('role') == 'user']
            # Combine all sources
            user_request = prompt.lower() if prompt else ""
            all_user_text = (user_request + " " + " ".join(recent_user_messages) + " " + " ".join(chat_user_messages)).lower()
            
            # Check if this is a performance report request (more lenient check)
            has_perf_keywords = ("performance report" in all_user_text or 
                                ("generate" in all_user_text and "report" in all_user_text) or
                                "awr report" in all_user_text or
                                "ash report" in all_user_text)
            
            # Check if user is ASKING to be asked for time (not providing time info)
            is_asking_to_be_asked = ("ask me for" in all_user_text or 
                                    "need you to ask" in all_user_text or
                                    "please ask" in all_user_text or
                                    "before generating" in all_user_text)
            
            # Check if user actually PROVIDED time info (not just asking to be asked)
            # Exclude cases where user says "ask me for start time" - that's not providing it
            has_time_info = False
            if not is_asking_to_be_asked:
                # Only check for time info if user isn't asking to be asked
                has_time_info = (("start time" in all_user_text and ":" in all_user_text) or  # Has actual time like "10:00"
                               ("end time" in all_user_text and ":" in all_user_text) or
                               ("last" in all_user_text and any(char.isdigit() for char in all_user_text)) or  # "last 3 hours"
                               ("hours" in all_user_text and any(char.isdigit() for char in all_user_text)) or  # "3 hours"
                               ("2024" in all_user_text or "2025" in all_user_text or "2026" in all_user_text))  # Has a date
            
            # If user is asking to be asked, or has perf keywords without time info, it's a perf request
            is_perf_request = has_perf_keywords and (is_asking_to_be_asked or not has_time_info)
            
            # If user asked for performance report but agent gave generic response, replace it
            # Use both the detailed check and the early check we did at the start
            if is_perf_request or user_wants_perf_report:
                final_response = "Please provide the start time and end time for the performance report. You can either specify a time range (e.g., '2024-01-15 10:00:00 to 2024-01-15 11:00:00') or say 'last N hours' (e.g., 'last 3 hours'). TERMINATE"
            elif ("compare" in all_user_text and "awr" in all_user_text) and \
                 ("baseline" not in all_user_text and "target" not in all_user_text):
                final_response = "Please provide the baseline period (start time and end time) and target period (start time and end time) for the AWR comparison. Format: YYYY-MM-DD HH:MM:SS (e.g., Baseline: 2024-01-15 10:00:00 to 2024-01-15 11:00:00, Target: 2024-01-15 14:00:00 to 2024-01-15 15:00:00). TERMINATE"
        
        # Final fallback - but check if user is asking for performance report first
        if not final_response or final_response == "Task Completed.":
            # Check if user's request was about performance report or compare AWR
            user_request = prompt.lower() if prompt else ""
            recent_user_messages = [msg.get('content', '').lower() for msg in chat_res.chat_history[-5:] if msg.get('role') == 'user']
            user_context = " ".join(recent_user_messages)
            
            # If user asked for performance report but we don't have a response, 
            # generate the appropriate question instead of generic fallback
            if ("performance report" in user_request or ("generate" in user_request and "report" in user_request)) and \
               ("start time" not in user_context and "end time" not in user_context and "hours" not in user_context):
                final_response = "Please provide the start time and end time for the performance report. You can either specify a time range (e.g., '2024-01-15 10:00:00 to 2024-01-15 11:00:00') or say 'last N hours' (e.g., 'last 3 hours'). TERMINATE"
            elif ("compare" in user_request and "awr" in user_request) and \
                 ("baseline" not in user_context and "target" not in user_context):
                final_response = "Please provide the baseline period (start time and end time) and target period (start time and end time) for the AWR comparison. Format: YYYY-MM-DD HH:MM:SS (e.g., Baseline: 2024-01-15 10:00:00 to 2024-01-15 11:00:00, Target: 2024-01-15 14:00:00 to 2024-01-15 15:00:00). TERMINATE"
            else:
                # Try to get current DB info as fallback
                final_response = f"I'm connected to database: **{st.session_state['current_db']}**. How can I help you?"

        # CRITICAL: Clean the final response to remove any conversation history that might have leaked through
        # This is essential to prevent showing conversation history when buttons are clicked multiple times
        
        # Store original length to check if we removed too much
        original_length = len(final_response)
        
        # Remove conversation history patterns (more aggressive)
        # Pattern 1: Full conversation history block
        final_response = re.sub(
            r"Conversation History.*?New User Query:.*?IMPORTANT:.*?Use tools only if necessary.*?",
            "",
            final_response,
            flags=re.DOTALL | re.IGNORECASE
        )
        
        # Pattern 2: Recent Reports/Artifacts section
        final_response = re.sub(
            r"Recent Reports/Artifacts Available:.*?Current Database:.*?",
            "",
            final_response,
            flags=re.DOTALL | re.IGNORECASE
        )
        
        # Pattern 3: Individual context markers
        final_response = re.sub(r"Conversation History.*?New User Query:", "", final_response, flags=re.DOTALL | re.IGNORECASE)
        final_response = re.sub(r"Recent Reports/Artifacts Available:.*?Current Database:", "", final_response, flags=re.DOTALL | re.IGNORECASE)
        final_response = re.sub(r"IMPORTANT:.*?Use tools only if necessary", "", final_response, flags=re.DOTALL | re.IGNORECASE)
        final_response = re.sub(r"Consider the history above.*?Reference past results", "", final_response, flags=re.DOTALL | re.IGNORECASE)
        final_response = re.sub(r"If the user is asking about:.*?A follow-up question", "", final_response, flags=re.DOTALL | re.IGNORECASE)
        
        # Remove any lines that are just context markers
        lines = final_response.split('\n')
        filtered_lines = []
        skip_next = False
        for i, line in enumerate(lines):
            line_lower = line.lower().strip()
            # Skip lines that are context markers
            if any(marker in line_lower for marker in [
                "conversation history",
                "new user query:",
                "recent reports/artifacts",
                "current database:",
                "important: use the conversation",
                "if the user is asking about",
                "a previous report",
                "a previous conversation topic",
                "a follow-up question",
                "consider the history above",
                "reference past results",
                "use tools only if necessary"
            ]):
                continue
            # Skip empty lines after context markers
            if not line.strip() and i > 0 and any(marker in lines[i-1].lower() for marker in ["conversation history", "new user query", "important:"]):
                continue
            filtered_lines.append(line)
        
        final_response = '\n'.join(filtered_lines)
        
        # Clean up extra whitespace
        final_response = re.sub(r"\n{3,}", "\n\n", final_response)
        final_response = final_response.strip()
        
        # If after cleaning, the response is empty or just contains artifacts, use a simpler message
        if not final_response or (len(final_response) < 10 and "::ARTIFACT_" in final_response):
            # Extract just the artifact part if it exists
            artifact_match = re.search(r"(.*?::ARTIFACT_[^:]+:[^:]+::.*?)", final_response)
            if artifact_match:
                final_response = artifact_match.group(1)
            elif "::ARTIFACT_" in final_response:
                # Keep the artifact marker but add a simple message
                artifact_type_match = re.search(r"::ARTIFACT_(\w+):", final_response)
                if artifact_type_match:
                    artifact_type = artifact_type_match.group(1)
                    if artifact_type == "TABLESPACE":
                        final_response = f"✅ Tablespace status retrieved successfully. {final_response}"
                    elif artifact_type == "SESSIONS":
                        final_response = f"✅ Active sessions retrieved successfully. {final_response}"
                    elif artifact_type == "HEALTH":
                        final_response = f"✅ Health check completed successfully. {final_response}"
                    else:
                        final_response = f"✅ Task completed successfully. {final_response}"
                else:
                    final_response = f"✅ Task completed successfully. {final_response}"
        
        st.session_state["messages"].append({"role": "assistant", "content": final_response})
        st.rerun()
        
    except Exception as e:
        st.error(f"Agent Error: {e}")

# ============================================================================
# 13. UI RENDERING - ENHANCED WITH TABS
# ============================================================================

# Sidebar
with st.sidebar:
    st.header("🔌 Connection")
    try:
        curr_idx = st.session_state["dbs"].index(st.session_state["current_db"])
    except ValueError:
        curr_idx = 0
    
    def on_db_change():
        st.session_state["previous_db"] = st.session_state["current_db"]
        st.session_state["current_db"] = st.session_state["sb_db_selector"]
    
    col_db, col_refresh = st.columns([3, 1])
    with col_db:
        selected_db = st.selectbox(
            "Select Database",
            options=st.session_state["dbs"],
            index=curr_idx,
            key="sb_db_selector",
            on_change=on_db_change,
            label_visibility="visible"
        )
    with col_refresh:
        st.write("")  # Spacing
        if st.button("🔄", key="refresh_db_list", help="Refresh database list (see databases added by other users)", width='stretch'):
            if refresh_database_list():
                st.success("✅ Database list refreshed!")
                st.rerun()
            else:
                st.error("❌ Failed to refresh database list")
    
    # Connection status - simplified (no real-time metrics cache)
    st.info(f"DB: **{st.session_state['current_db']}**")
    
    # Add new database option
    with st.expander("➕ Add Database", expanded=False):
        with st.form("add_database_form", clear_on_submit=True):
            new_db_name = st.text_input("Database Name *", key="new_db_name", placeholder="e.g., PROD_DB", help="Unique identifier for this database")
            username = st.text_input("Username *", key="new_db_username", placeholder="e.g., dbcheck", help="Oracle database username")
            password = st.text_input("Password *", key="new_db_password", type="password", placeholder="Enter password", help="Oracle database password")
            
            col_host, col_port = st.columns(2)
            with col_host:
                host = st.text_input("Host *", key="new_db_host", placeholder="e.g., localhost or 192.168.1.100", help="Database server hostname or IP")
            with col_port:
                port = st.text_input("Port *", key="new_db_port", placeholder="e.g., 1521", value="1521", help="Database listener port (default: 1521)")
            
            connection_type = st.radio("Connection Type *", ["Service Name", "SID"], key="new_db_conn_type", help="Choose Service Name (recommended) or SID")
            
            if connection_type == "Service Name":
                service_name = st.text_input("Service Name *", key="new_db_service", placeholder="e.g., ORCLDB", help="Oracle service name")
                sid = None
            else:
                sid = st.text_input("SID *", key="new_db_sid", placeholder="e.g., ORCL", help="Oracle System Identifier")
                service_name = None
            
            submitted = st.form_submit_button("➕ Add Database", type="primary", width='stretch')
            
            if submitted:
                # Validation
                if not all([new_db_name.strip(), username.strip(), password, host.strip(), port.strip()]):
                    st.error("❌ Please fill in all required fields (marked with *)")
                elif connection_type == "Service Name" and not service_name.strip():
                    st.error("❌ Please enter Service Name")
                elif connection_type == "SID" and not sid.strip():
                    st.error("❌ Please enter SID")
                else:
                    # Check if database already exists
                    db_name_upper = new_db_name.strip().upper()
                    if db_name_upper in st.session_state["dbs"]:
                        st.warning(f"⚠️ Database '{db_name_upper}' already exists. Updating configuration...")
                    
                    # Save configuration
                    result = save_database_config(
                        db_name=db_name_upper,
                        username=username.strip(),
                        password=password,
                        host=host.strip(),
                        port=port.strip(),
                        service_name=service_name.strip() if service_name else None,
                        sid=sid.strip() if sid else None
                    )
                    
                    if result["success"]:
                        st.success(f"✅ {result['message']}")
                        # Database list is already updated in save_database_config()
                        # Optionally switch to the new database
                        if st.checkbox(f"Switch to '{db_name_upper}'", key="switch_to_new_db", value=True):
                            st.session_state["current_db"] = db_name_upper
                        st.rerun()
                    else:
                        st.error(f"❌ {result['message']}")
    
    st.markdown("---")
    st.subheader("Oracle --tools")
    
    if st.button("🏥 Full Health Check", width='stretch', key="btn_full_health_check"):
        st.session_state["messages"].append({
            "role": "user", 
            "content": "Run a full health check on the database."
        })
        st.session_state["_pending_user_input"] = "Run a full health check on the database."
        st.session_state["_processing"] = True
        st.rerun()
    
    if st.button("💾 Check Tablespaces", width='stretch', key="btn_check_tablespaces"):
        st.session_state["messages"].append({
            "role": "user", 
            "content": "Check tablespace usage."
        })
        st.session_state["_pending_user_input"] = "Check tablespace usage."
        st.session_state["_processing"] = True
        st.rerun()
    
    if st.button("👥 List Active Sessions", width='stretch', key="btn_list_sessions"):
        st.session_state["messages"].append({
            "role": "user", 
            "content": "List active database sessions."
        })
        st.session_state["_pending_user_input"] = "List active database sessions."
        st.session_state["_processing"] = True
        st.rerun()
    
    # Generate Performance Report - send prompt to chat
    if st.button("📊 Generate Performance Report", width='stretch'):
        # Send a message to chat asking for inputs - be explicit
        prompt = "Generate a performance report. I need you to ask me for the start time and end time before generating the report."
        st.session_state["messages"].append({"role": "user", "content": prompt})
        st.session_state["_pending_user_input"] = prompt
        st.session_state["_processing"] = True
        st.rerun()
    
    # Compare AWR Reports - send prompt to chat
    if st.button("📊 Compare AWR Reports", width='stretch'):
        # Send a message to chat asking for inputs - be explicit
        prompt = "Compare AWR reports. I need you to ask me for the baseline start time, baseline end time, target start time, and target end time before comparing."
        st.session_state["messages"].append({"role": "user", "content": prompt})
        st.session_state["_pending_user_input"] = prompt
        st.session_state["_processing"] = True
        st.rerun()
    
    # Download Oracle Patch - send prompt to chat
    if st.button("💉 Download Oracle Patch", width='stretch'):
        # Send a message to chat asking for inputs - be explicit
        prompt = "Download an Oracle patch. I need you to ask me for the patch description before downloading (e.g., Oracle Database Release Update 19.20.0.0.0 for Linux x86-64)."
        st.session_state["messages"].append({"role": "user", "content": prompt})
        st.session_state["_pending_user_input"] = prompt
        st.session_state["_processing"] = True
        st.rerun()
    
    st.markdown("---")
    st.subheader("Jenkins --tools")
    
    # Search Jenkins Jobs
    if not st.session_state.get("show_search_jenkins_form", False):
        if st.button("🔍 Search Jenkins Jobs", width='stretch'):
            st.session_state["show_search_jenkins_form"] = True
    
    if st.session_state.get("show_search_jenkins_form", False):
        with st.expander("🔍 Search Jenkins Jobs", expanded=True):
            with st.form("search_jenkins_form", clear_on_submit=False):
                search_term = st.text_input("Search Term *", key="jenkins_search_term", placeholder="e.g., my-job or pipeline")
                col_submit, col_cancel = st.columns(2)
                with col_submit:
                    search_submitted = st.form_submit_button("🔍 Search", type="primary", width='stretch')
                with col_cancel:
                    if st.form_submit_button("❌ Cancel", width='stretch'):
                        st.session_state["show_search_jenkins_form"] = False
                        st.rerun()
                if search_submitted and search_term.strip():
                    st.session_state["show_search_jenkins_form"] = False
                    with st.spinner("Searching Jenkins jobs..."):
                        handle_agent_execution(f"Search for Jenkins jobs matching: {search_term.strip()}")
                elif search_submitted:
                    st.warning("⚠️ Please enter a search term")
    
    # Get Build Info
    if not st.session_state.get("show_build_info_form", False):
        if st.button("📋 Get Build Info", width='stretch'):
            st.session_state["show_build_info_form"] = True
    
    if st.session_state.get("show_build_info_form", False):
        with st.expander("📋 Get Build Info", expanded=True):
            with st.form("build_info_form", clear_on_submit=False):
                job_name = st.text_input("Job Name *", key="build_info_job", placeholder="e.g., my-freestyle-job")
                build_number = st.number_input("Build Number (optional, leave 0 for latest)", min_value=0, value=0, key="build_info_number")
                col_submit, col_cancel = st.columns(2)
                with col_submit:
                    info_submitted = st.form_submit_button("🔍 Get Info", type="primary", width='stretch')
                with col_cancel:
                    if st.form_submit_button("❌ Cancel", width='stretch'):
                        st.session_state["show_build_info_form"] = False
                        st.rerun()
                if info_submitted and job_name.strip():
                    st.session_state["show_build_info_form"] = False
                    with st.spinner("Getting build info..."):
                        if build_number > 0:
                            handle_agent_execution(f"Get build info for Jenkins job '{job_name.strip()}' build number {build_number}")
                        else:
                            handle_agent_execution(f"Get build info for Jenkins job '{job_name.strip()}' (latest build)")
                elif info_submitted:
                    st.warning("⚠️ Please enter a job name")
    
    # Get Build Console
    if not st.session_state.get("show_build_console_form", False):
        if st.button("📜 Get Build Console", width='stretch'):
            st.session_state["show_build_console_form"] = True
    
    if st.session_state.get("show_build_console_form", False):
        with st.expander("📜 Get Build Console", expanded=True):
            with st.form("build_console_form", clear_on_submit=False):
                job_name = st.text_input("Job Name *", key="build_console_job", placeholder="e.g., my-freestyle-job")
                build_number = st.number_input("Build Number (optional, leave 0 for latest)", min_value=0, value=0, key="build_console_number")
                col_submit, col_cancel = st.columns(2)
                with col_submit:
                    console_submitted = st.form_submit_button("🔍 Get Console", type="primary", width='stretch')
                with col_cancel:
                    if st.form_submit_button("❌ Cancel", width='stretch'):
                        st.session_state["show_build_console_form"] = False
                        st.rerun()
                if console_submitted and job_name.strip():
                    st.session_state["show_build_console_form"] = False
                    with st.spinner("Getting console output..."):
                        if build_number > 0:
                            handle_agent_execution(f"Get console output for Jenkins job '{job_name.strip()}' build number {build_number}")
                        else:
                            handle_agent_execution(f"Get console output for Jenkins job '{job_name.strip()}' (latest build)")
                elif console_submitted:
                    st.warning("⚠️ Please enter a job name")
    
    # Trigger Build
    if not st.session_state.get("show_trigger_build_form", False):
        if st.button("🚀 Trigger Build", width='stretch'):
            st.session_state["show_trigger_build_form"] = True
    
    if st.session_state.get("show_trigger_build_form", False):
        with st.expander("🚀 Trigger Build", expanded=True):
            with st.form("trigger_build_form", clear_on_submit=False):
                job_name = st.text_input("Job Name *", key="trigger_build_job", placeholder="e.g., my-freestyle-job")
                parameters = st.text_area("Parameters (JSON format, optional)", key="trigger_build_params", placeholder='{"param1": "value1", "param2": "value2"}', help="Leave empty if no parameters needed")
                col_submit, col_cancel = st.columns(2)
                with col_submit:
                    trigger_submitted = st.form_submit_button("🚀 Trigger", type="primary", width='stretch')
                with col_cancel:
                    if st.form_submit_button("❌ Cancel", width='stretch'):
                        st.session_state["show_trigger_build_form"] = False
                        st.rerun()
                if trigger_submitted and job_name.strip():
                    st.session_state["show_trigger_build_form"] = False
                    with st.spinner("Triggering build..."):
                        if parameters.strip():
                            handle_agent_execution(f"Trigger Jenkins build for job '{job_name.strip()}' with parameters: {parameters.strip()}")
                        else:
                            handle_agent_execution(f"Trigger Jenkins build for job '{job_name.strip()}'")
                elif trigger_submitted:
                    st.warning("⚠️ Please enter a job name")
    
    # Get Build History
    if not st.session_state.get("show_build_history_form", False):
        if st.button("📊 Get Build History", width='stretch'):
            st.session_state["show_build_history_form"] = True
    
    if st.session_state.get("show_build_history_form", False):
        with st.expander("📊 Get Build History", expanded=True):
            with st.form("build_history_form", clear_on_submit=False):
                job_name = st.text_input("Job Name *", key="build_history_job", placeholder="e.g., my-freestyle-job")
                limit = st.number_input("Number of Builds", min_value=1, max_value=50, value=10, key="build_history_limit")
                col_submit, col_cancel = st.columns(2)
                with col_submit:
                    history_submitted = st.form_submit_button("🔍 Get History", type="primary", width='stretch')
                with col_cancel:
                    if st.form_submit_button("❌ Cancel", width='stretch'):
                        st.session_state["show_build_history_form"] = False
                        st.rerun()
                if history_submitted and job_name.strip():
                    st.session_state["show_build_history_form"] = False
                    with st.spinner("Getting build history..."):
                        handle_agent_execution(f"Get build history for Jenkins job '{job_name.strip()}' (last {limit} builds)")
                elif history_submitted:
                    st.warning("⚠️ Please enter a job name")
    
    # Analyze Build Failure
    if not st.session_state.get("show_analyze_failure_form", False):
        if st.button("🔍 Analyze Build Failure", width='stretch'):
            st.session_state["show_analyze_failure_form"] = True
    
    if st.session_state.get("show_analyze_failure_form", False):
        with st.expander("🔍 Analyze Build Failure", expanded=True):
            with st.form("analyze_failure_form", clear_on_submit=False):
                job_name = st.text_input("Job Name *", key="analyze_failure_job", placeholder="e.g., my-freestyle-job")
                build_number = st.number_input("Build Number (optional, leave 0 for latest failed)", min_value=0, value=0, key="analyze_failure_number")
                col_submit, col_cancel = st.columns(2)
                with col_submit:
                    analyze_submitted = st.form_submit_button("🔍 Analyze", type="primary", width='stretch')
                with col_cancel:
                    if st.form_submit_button("❌ Cancel", width='stretch'):
                        st.session_state["show_analyze_failure_form"] = False
                        st.rerun()
                if analyze_submitted and job_name.strip():
                    st.session_state["show_analyze_failure_form"] = False
                    with st.spinner("Analyzing build failure..."):
                        if build_number > 0:
                            handle_agent_execution(f"Analyze why Jenkins build failed for job '{job_name.strip()}' build number {build_number}")
                        else:
                            handle_agent_execution(f"Analyze why Jenkins build failed for job '{job_name.strip()}' (latest failed build)")
                elif analyze_submitted:
                    st.warning("⚠️ Please enter a job name")
    
    # Get Job Config
    if not st.session_state.get("show_job_config_form", False):
        if st.button("⚙️ Get Job Config", width='stretch'):
            st.session_state["show_job_config_form"] = True
    
    if st.session_state.get("show_job_config_form", False):
        with st.expander("⚙️ Get Job Config", expanded=True):
            with st.form("job_config_form", clear_on_submit=False):
                job_name = st.text_input("Job Name *", key="job_config_name", placeholder="e.g., my-freestyle-job")
                col_submit, col_cancel = st.columns(2)
                with col_submit:
                    config_submitted = st.form_submit_button("🔍 Get Config", type="primary", width='stretch')
                with col_cancel:
                    if st.form_submit_button("❌ Cancel", width='stretch'):
                        st.session_state["show_job_config_form"] = False
                        st.rerun()
                if config_submitted and job_name.strip():
                    st.session_state["show_job_config_form"] = False
                    with st.spinner("Getting job configuration..."):
                        handle_agent_execution(f"Get configuration for Jenkins job '{job_name.strip()}'")
                elif config_submitted:
                    st.warning("⚠️ Please enter a job name")
    
    # Get Build Artifacts
    if not st.session_state.get("show_build_artifacts_form", False):
        if st.button("📦 Get Build Artifacts", width='stretch'):
            st.session_state["show_build_artifacts_form"] = True
    
    if st.session_state.get("show_build_artifacts_form", False):
        with st.expander("📦 Get Build Artifacts", expanded=True):
            with st.form("build_artifacts_form", clear_on_submit=False):
                job_name = st.text_input("Job Name *", key="build_artifacts_job", placeholder="e.g., my-freestyle-job")
                build_number = st.number_input("Build Number (optional, leave 0 for latest)", min_value=0, value=0, key="build_artifacts_number")
                col_submit, col_cancel = st.columns(2)
                with col_submit:
                    artifacts_submitted = st.form_submit_button("🔍 Get Artifacts", type="primary", width='stretch')
                with col_cancel:
                    if st.form_submit_button("❌ Cancel", width='stretch'):
                        st.session_state["show_build_artifacts_form"] = False
                        st.rerun()
                if artifacts_submitted and job_name.strip():
                    st.session_state["show_build_artifacts_form"] = False
                    with st.spinner("Getting build artifacts..."):
                        if build_number > 0:
                            handle_agent_execution(f"Get build artifacts for Jenkins job '{job_name.strip()}' build number {build_number}")
                        else:
                            handle_agent_execution(f"Get build artifacts for Jenkins job '{job_name.strip()}' (latest build)")
                elif artifacts_submitted:
                    st.warning("⚠️ Please enter a job name")
    
    
    st.markdown("---")
    st.subheader("💾 Saved Queries")
    saved_queries = st.session_state.get("saved_queries", [])
    for q in saved_queries[-5:]:  # Show last 5
        query_sql = q['sql']
        query_name = q['name']
        st.button(f"📝 {query_name}", key=f"load_{q['id']}", width='stretch', on_click=lambda sql=query_sql: handle_agent_execution(f"Run this saved query: {sql}"))
    
    st.markdown("---")
    
    def clear_history():
        st.session_state["messages"] = []
        st.session_state["awr_history"] = []
        st.rerun()
    
    st.button("🗑️ Clear History", width='stretch', on_click=clear_history)

# Main Area
st.title("Oracle + Jenkins Agentic Console v4.0")

# Add CSS for Gemini-like interface
st.markdown("""
<style>
    /* Position tabs at bottom, starting after sidebar */
    .stTabs [data-baseweb="tab-list"] {
        position: fixed;
        bottom: 0;
        left: 21rem; /* Start after sidebar (sidebar is typically ~20rem wide) */
        right: 0;
        background: white;
        z-index: 999;
        padding: 0.5rem 1rem;
        border-top: 1px solid #e0e0e0;
        box-shadow: 0 -2px 10px rgba(0,0,0,0.1);
    }
    
    /* Add padding to content to prevent overlap with bottom tabs and chat input */
    .main .block-container {
        padding-bottom: 120px; /* Increased for chat input */
    }
    
    /* Ensure chat input stays at bottom and is always visible (ChatGPT-style) */
    .stChatInput {
        position: sticky;
        bottom: 0;
        z-index: 1000;
        background: white;
        padding: 1rem 0;
        margin-top: auto;
    }
    
    /* Make chat input container stick to bottom */
    [data-testid="stChatInputContainer"] {
        position: sticky;
        bottom: 0;
        background: white;
        padding: 1rem 0;
        border-top: 1px solid #e0e0e0;
    }
    
    /* Ensure tabs are visible on smaller screens */
    @media (max-width: 768px) {
        .stTabs [data-baseweb="tab-list"] {
            left: 0; /* Full width on mobile */
        }
    }
    
    /* Smooth transitions for messages */
    .stChatMessage {
        animation: fadeIn 0.3s ease-in;
    }
    
    @keyframes fadeIn {
        from {
            opacity: 0;
            transform: translateY(10px);
        }
        to {
            opacity: 1;
            transform: translateY(0);
        }
    }
    
    /* Chat message layout: User on right, Assistant on left */
    
    /* Streamlit chat messages container */
    div[data-testid="stChatMessage"] {
        margin-bottom: 1.5rem;
        width: 100%;
    }
    
    /* User messages container - align to right */
    /* Target by checking for user avatar emoji in the structure */
    div[data-testid="stChatMessage"] {
        position: relative;
    }
    
    /* Fallback CSS for browsers that support :has() */
    @supports selector(:has(*)) {
        div[data-testid="stChatMessage"]:has(img[alt*="👤"]) {
            display: flex;
            justify-content: flex-end;
            margin-left: auto;
        }
        
        div[data-testid="stChatMessage"]:has(img[alt*="🤖"]) {
            display: flex;
            justify-content: flex-start;
            margin-right: auto;
        }
        
        div[data-testid="stChatMessage"]:has(img[alt*="👤"]) > div > div:last-child {
            max-width: 70%;
            margin-left: auto;
            margin-right: 0;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 0.75rem 1rem;
            border-radius: 1.25rem 1.25rem 0.25rem 1.25rem;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        
        div[data-testid="stChatMessage"]:has(img[alt*="🤖"]) > div > div:last-child {
            max-width: 70%;
            margin-left: 0;
            margin-right: auto;
            background-color: #f8f9fa;
            color: #212529;
            padding: 0.75rem 1rem;
            border-radius: 1.25rem 1.25rem 1.25rem 0.25rem;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
            border: 1px solid #e9ecef;
        }
    }
</style>
<script>
    // Function to style chat messages - user on right, assistant on left
    function styleChatMessages() {
        const messages = document.querySelectorAll('[data-testid="stChatMessage"]');
        messages.forEach(msg => {
            // Check if it contains user avatar (👤) or assistant avatar (🤖)
            const userAvatar = msg.querySelector('img[alt*="👤"], img[alt*="user"]');
            const assistantAvatar = msg.querySelector('img[alt*="🤖"], img[alt*="assistant"]');
            
            if (userAvatar) {
                // User message - align right
                msg.style.display = 'flex';
                msg.style.justifyContent = 'flex-end';
                msg.style.marginLeft = 'auto';
                msg.style.maxWidth = '100%';
                
                // Style the message bubble
                const msgContent = msg.querySelector('div > div:last-child');
                if (msgContent) {
                    msgContent.style.maxWidth = '70%';
                    msgContent.style.marginLeft = 'auto';
                    msgContent.style.marginRight = '0';
                    msgContent.style.background = 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)';
                    msgContent.style.color = 'white';
                    msgContent.style.padding = '0.75rem 1rem';
                    msgContent.style.borderRadius = '1.25rem 1.25rem 0.25rem 1.25rem';
                    msgContent.style.boxShadow = '0 2px 8px rgba(0,0,0,0.1)';
                }
            } else if (assistantAvatar) {
                // Assistant message - align left
                msg.style.display = 'flex';
                msg.style.justifyContent = 'flex-start';
                msg.style.marginRight = 'auto';
                msg.style.maxWidth = '100%';
                
                // Style the message bubble
                const msgContent = msg.querySelector('div > div:last-child');
                if (msgContent) {
                    msgContent.style.maxWidth = '70%';
                    msgContent.style.marginLeft = '0';
                    msgContent.style.marginRight = 'auto';
                    msgContent.style.backgroundColor = '#f8f9fa';
                    msgContent.style.color = '#212529';
                    msgContent.style.padding = '0.75rem 1rem';
                    msgContent.style.borderRadius = '1.25rem 1.25rem 1.25rem 0.25rem';
                    msgContent.style.boxShadow = '0 2px 8px rgba(0,0,0,0.05)';
                    msgContent.style.border = '1px solid #e9ecef';
                }
            }
        });
    }
    
    // Run on page load
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', styleChatMessages);
    } else {
        styleChatMessages();
    }
    
    // Also run after a short delay to catch dynamically added messages
    setTimeout(styleChatMessages, 100);
    
    // Use MutationObserver to style new messages as they're added
    const observer = new MutationObserver(function(mutations) {
        styleChatMessages();
    });
    
    observer.observe(document.body, {
        childList: true,
        subtree: true
    });
</script>
""", unsafe_allow_html=True)

# Tabs - positioned at bottom using CSS
tab1, tab2, tab3, tab4 = st.tabs(["💬 Chat", "⚡ Performance", "🔍 SQL Explorer", "⚙️ Settings"])

with tab1:
    # Welcome message for first-time users
    if not st.session_state.get("welcome_message_seen", False):
        st.markdown("""
        <style>
        .welcome-container {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 20px;
            padding: 3rem 2rem;
            margin-bottom: 2rem;
            color: white;
            box-shadow: 0 10px 40px rgba(102, 126, 234, 0.3);
        }
        .welcome-title {
            font-size: 2.5rem;
            font-weight: 700;
            margin-bottom: 0.5rem;
            text-align: center;
        }
        .welcome-subtitle {
            font-size: 1.2rem;
            text-align: center;
            opacity: 0.95;
            margin-bottom: 2rem;
        }
        .feature-card {
            background: rgba(255, 255, 255, 0.15);
            backdrop-filter: blur(10px);
            border-radius: 15px;
            padding: 1.5rem;
            margin: 0.5rem;
            border: 1px solid rgba(255, 255, 255, 0.2);
            transition: transform 0.2s;
        }
        .feature-card:hover {
            transform: translateY(-5px);
            background: rgba(255, 255, 255, 0.2);
        }
        .feature-icon {
            font-size: 3rem;
            margin-bottom: 0.5rem;
        }
        .feature-title {
            font-size: 1.3rem;
            font-weight: 600;
            margin-bottom: 0.5rem;
        }
        .feature-desc {
            font-size: 0.95rem;
            opacity: 0.9;
            line-height: 1.5;
        }
        </style>
        """, unsafe_allow_html=True)
        
        st.markdown("""
        <div class="welcome-container">
            <div class="welcome-title">👋 Welcome!</div>
            <div class="welcome-subtitle">Oracle + Jenkins Agentic Console</div>
        </div>
        """, unsafe_allow_html=True)
        
        # High-level features in cards
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.markdown("""
            <div class="feature-card">
                <div class="feature-icon">🗄️</div>
                <div class="feature-title">Database</div>
                <div class="feature-desc">Health checks, AWR reports, SQL analysis</div>
            </div>
            """, unsafe_allow_html=True)
        
        with col2:
            st.markdown("""
            <div class="feature-card">
                <div class="feature-icon">🔧</div>
                <div class="feature-title">Jenkins</div>
                <div class="feature-desc">Build management, job search, CI/CD</div>
            </div>
            """, unsafe_allow_html=True)
        
        with col3:
            st.markdown("""
            <div class="feature-card">
                <div class="feature-icon">🤖</div>
                <div class="feature-title">AI Assistant</div>
                <div class="feature-desc">Natural language queries & analysis</div>
            </div>
            """, unsafe_allow_html=True)
        
        with col4:
            st.markdown("""
            <div class="feature-card">
                <div class="feature-icon">📊</div>
                <div class="feature-title">Analytics</div>
                <div class="feature-desc">Performance metrics & insights</div>
            </div>
            """, unsafe_allow_html=True)
        
        # Quick start section
        st.markdown("<br>", unsafe_allow_html=True)
        col_start1, col_start2 = st.columns([1, 1])
        
        with col_start1:
            st.markdown("""
            <div style='background: #f8f9fa; padding: 1.5rem; border-radius: 15px; border-left: 4px solid #667eea;'>
                <h3 style='margin-top: 0; color: #333;'>💬 Chat with AI</h3>
                <p style='color: #666; margin-bottom: 0;'>Type natural language commands in the chat box below</p>
            </div>
            """, unsafe_allow_html=True)
        
        with col_start2:
            st.markdown("""
            <div style='background: #f8f9fa; padding: 1.5rem; border-radius: 15px; border-left: 4px solid #764ba2;'>
                <h3 style='margin-top: 0; color: #333;'>⚡ Quick Actions</h3>
                <p style='color: #666; margin-bottom: 0;'>Use sidebar buttons for common tasks</p>
            </div>
            """, unsafe_allow_html=True)
        
        st.markdown("<br>", unsafe_allow_html=True)
        
        # Get started button
        col_btn1, col_btn2, col_btn3 = st.columns([1, 2, 1])
        with col_btn2:
            if st.button("🚀 Get Started", type="primary", width='stretch'):
                st.session_state["welcome_message_seen"] = True
                st.rerun()
    
    # Chat messages displayed at top in Chat tab
    messages_to_show = st.session_state["messages"][-20:]  # Show last 20
    
    # Show all messages
    for i, msg in enumerate(messages_to_show):
        # Custom avatars for user and assistant
        avatar = "👤" if msg["role"] == "user" else "🤖"
        
        with st.chat_message(msg["role"], avatar=avatar):
            content = msg["content"]
            
            # For user messages, display content immediately and skip artifact handling
            if msg["role"] == "user":
                st.markdown(content)
            else:
                # For assistant messages, handle artifacts
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
                                selected_job = st.selectbox(
                                    "Select a Job to Run:", 
                                    artifact["matches"], 
                                    key=f"job_sel_{art_id}"
                                )
                                job_details = next((j for j in st.session_state["job_map"] if j["name"] == selected_job), None)
                                if job_details:
                                    st.info(job_details.get('description', 'No description'), icon="ℹ️")
                            with col_right:
                                if job_details:
                                    with st.container(border=True):
                                        st.write(f"**Configure: {selected_job}**")
                                        with st.form(key=f"form_{art_id}"):
                                            form_params = {}
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
                                            run_submitted = st.form_submit_button("Run Job", type="primary")
                                        
                                        if run_submitted:
                                            server = get_jenkins_server()
                                            if server:
                                                final_params = {k: str(v).lower() if isinstance(v, bool) else v for k,v in form_params.items()}
                                                st.session_state["polling_job"] = selected_job
                                                
                                                try:
                                                    qid = server.build_job(selected_job, final_params)
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
                                                            with st.spinner("Analyzing Failure..."):
                                                                analysis = analyze_jenkins_failure(console)
                                                                if analysis:
                                                                    st.markdown("### Root Cause Analysis")
                                                                    st.error(analysis.get("root_cause"))
                                                                    st.code(analysis.get("failed_line"))
                                                                    st.info(analysis.get("suggestion"))
                                                except Exception as e:
                                                    st.error(f"Error: {e}")
                
                elif "::ARTIFACT_JENKINS_BUILD_HISTORY:" in content:
                    match = re.search(r"::ARTIFACT_JENKINS_BUILD_HISTORY:(.*?)::", content)
                    display_text = content.replace(match.group(0), "") if match else content
                    st.markdown(display_text)
                    
                    if match:
                        art_id = match.group(1)
                        artifact = st.session_state["artifacts"].get(art_id)
                        
                        if artifact and artifact["type"] == "JENKINS_BUILD_HISTORY":
                            history_data = artifact.get("build_history", {})
                            job_name = artifact.get("job_name", "Unknown")
                            builds = history_data.get("builds", [])
                            show_console = history_data.get("show_console", True)
                            
                            st.markdown("---")
                            with st.expander(f"🔨 Build History Details: {job_name} ({len(builds)} builds)", expanded=True):
                                for build in builds:
                                    build_num = build.get("build_number", "N/A")
                                    status = build.get("status", "UNKNOWN")
                                    job_type = build.get("job_type", "Unknown")
                                    
                                    # Status color coding
                                    status_color = {
                                        "SUCCESS": "🟢",
                                        "FAILURE": "🔴",
                                        "UNSTABLE": "🟡",
                                        "ABORTED": "⚫",
                                        "IN PROGRESS": "🔵",
                                        "ERROR": "❌"
                                    }.get(status, "⚪")
                                    
                                    with st.expander(
                                        f"{status_color} Build #{build_num} - {status} | {build.get('timestamp', 'N/A')} | {job_type}",
                                        expanded=(build_num == builds[0].get("build_number") if builds else False)
                                    ):
                                        col1, col2, col3 = st.columns(3)
                                        with col1:
                                            st.metric("Status", status)
                                        with col2:
                                            st.metric("Duration", f"{build.get('duration_seconds', 0):.2f}s")
                                        with col3:
                                            st.metric("Job Type", job_type)
                                        
                                        # Build URL
                                        if build.get("url"):
                                            st.markdown(f"🔗 **Build URL:** [{build['url']}]({build['url']})")
                                        
                                        # Parameters
                                        parameters = build.get("parameters", {})
                                        if parameters:
                                            st.markdown("**📋 Parameters:**")
                                            param_df = pd.DataFrame([
                                                {"Parameter": k, "Value": str(v)} for k, v in parameters.items()
                                            ])
                                            st.dataframe(param_df, hide_index=True, width='stretch')
                                        else:
                                            st.info("No parameters for this build")
                                        
                                        # Causes (who triggered it)
                                        causes = build.get("causes", [])
                                        if causes:
                                            st.markdown("**👤 Triggered By:**")
                                            for cause in causes:
                                                st.write(f"- {cause}")
                                        
                                        # Console Output
                                        if show_console:
                                            console_output = build.get("console_output", "")
                                            console_length = build.get("console_length", 0)
                                            
                                            if console_output:
                                                st.markdown(f"**📜 Console Output** ({console_length:,} characters):")
                                                # Show truncated version in expander, full version in code block
                                                if console_length > 10000:
                                                    st.warning(f"⚠️ Console output is large ({console_length:,} chars). Showing last 10,000 characters. Full output available in Jenkins UI.")
                                                
                                                st.code(console_output, language="text")
                                                
                                                # Download button for console output
                                                st.download_button(
                                                    label="📥 Download Console Output",
                                                    data=console_output,
                                                    file_name=f"{job_name}_build_{build_num}_console.txt",
                                                    mime="text/plain",
                                                    key=f"download_console_{art_id}_{build_num}"
                                                )
                                            elif "Error" in console_output:
                                                st.error(console_output)
                                            else:
                                                st.info("No console output available")
                                        else:
                                            st.info("💡 Console output was not included. Enable it when fetching build history.")
                                        
                                        # Error info if any
                                        if build.get("error"):
                                            st.error(f"❌ Error: {build.get('error')}")
                
                elif "::ARTIFACT_HEALTH:" in content:
                    match = re.search(r"::ARTIFACT_HEALTH:(.*?)::", content)
                    display_text = content.replace(match.group(0), "") if match else content
                    st.markdown(display_text)
                    
                    if match:
                        art_id = match.group(1)
                        artifact = st.session_state["artifacts"].get(art_id)
                        if artifact:
                            with st.expander(f"🏥 Database Health Report ({artifact['timestamp']})", expanded=True):
                                # Clean the content - remove any comparison artifacts that might have been mixed in
                                content = artifact["content"]
                                
                                # Remove SQL Regressions and comparison-related sections (these belong to AWR comparison, not health check)
                                import re as re_module
                                
                                # Pattern 1: Remove "SQL Regressions (The Culprits)" section with table and details
                                content = re_module.sub(
                                    r'##?\s*\*?\s*SQL Regressions.*?The Culprits.*?\*?\s*\n.*?SQLs where performance degraded.*?\n.*?\|.*?SQL_ID.*?\|.*?Base Ela/Exec.*?\|.*?Curr Ela/Exec.*?\|.*?Degraded By.*?\|.*?\n.*?\|.*?---.*?\|.*?\n.*?Technical Root Cause Analysis.*?</details>.*?Close Comparison.*?\n',
                                    '',
                                    content,
                                    flags=re_module.DOTALL | re_module.IGNORECASE
                                )
                                
                                # Pattern 2: Remove any remaining SQL Regressions sections
                                content = re_module.sub(
                                    r'##?\s*SQL Regressions.*?(?=\n##|\Z)',
                                    '',
                                    content,
                                    flags=re_module.DOTALL | re_module.IGNORECASE
                                )
                                
                                # Pattern 3: Remove "Close Comparison" buttons that might be embedded
                                content = re_module.sub(
                                    r'<button[^>]*>Close Comparison</button>',
                                    '',
                                    content,
                                    flags=re_module.IGNORECASE
                                )
                                content = re_module.sub(
                                    r'\[Close Comparison\]\([^\)]+\)',
                                    '',
                                    content,
                                    flags=re_module.IGNORECASE
                                )
                                
                                # Pattern 4: Remove any comparison-related HTML/details blocks
                                content = re_module.sub(
                                    r'<details>.*?Close Comparison.*?</details>',
                                    '',
                                    content,
                                    flags=re_module.DOTALL | re_module.IGNORECASE
                                )
                                
                                st.markdown(content)
                                
                                # Add Q&A section for asking questions about the health report
                                st.markdown("---")
                                with st.expander("💬 Chat about this health report", expanded=False):
                                    st.caption("Ask questions about the health report. The AI will use the report context to answer.")
                                    question = st.text_input(
                                        "Your question:", 
                                        key=f"health_q_{i}_{art_id}", 
                                        placeholder="e.g., What are the critical issues? What should I prioritize? Why is CPU high?"
                                    )
                                    if st.button("Ask", key=f"health_ask_{i}_{art_id}", width='stretch'):
                                        if question:
                                            # Route through main agentic system for full context and tool access
                                            # Add user message and process through main agent
                                            st.session_state["messages"].append({
                                                "role": "user", 
                                                "content": f"Question about the health report: {question}"
                                            })
                                            st.session_state["_pending_user_input"] = f"Question about the health report: {question}"
                                            st.rerun()
                                        else:
                                            st.warning("Please enter a question")
                                
                                if st.button("Close Report", key=f"close_health_{i}"):
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
                                
                                # Add Q&A section for asking questions about the comparison report
                                st.markdown("---")
                                with st.expander("💬 Ask a question about this AWR comparison"):
                                    question = st.text_input(
                                        "Your question:", 
                                        key=f"compare_awr_q_{i}_{art_id}", 
                                        placeholder="e.g., What are the main differences? Why did performance degrade?"
                                    )
                                    if st.button("Ask", key=f"compare_awr_ask_{i}_{art_id}"):
                                        if question:
                                            # Route through main agentic system for full context and tool access
                                            st.session_state["messages"].append({
                                                "role": "user", 
                                                "content": f"Question about the AWR comparison report: {question}"
                                            })
                                            st.session_state["_pending_user_input"] = f"Question about the AWR comparison report: {question}"
                                            st.rerun()
                                
                                if st.button("Close Comparison", key=f"close_comp_{i}"):
                                    del st.session_state["artifacts"][art_id]
                                    st.rerun()
                
                elif "::ARTIFACT_PATCH:" in content:
                    match = re.search(r"::ARTIFACT_PATCH:(.*?)::", content)
                    display_text = content.replace(match.group(0), "") if match else content
                    st.markdown(display_text)
                    
                    if match:
                        art_id = match.group(1)
                        artifact = st.session_state["artifacts"].get(art_id)
                        if artifact:
                            icon = "✅" if artifact["status"] == "success" else "❌" if artifact["status"] == "error" else "ℹ️"
                            with st.expander(f"📦 Patch Download Details {icon} ({artifact['timestamp']})", expanded=True):
                                if artifact["status"] == "success":
                                    st.success("Download Successful")
                                elif artifact["status"] == "error":
                                    st.error("Download Failed")
                                else:
                                    st.info("Status Info")
                                st.markdown(artifact["content"])
                
                elif "::ARTIFACT_SESSIONS:" in content:
                    match = re.search(r"::ARTIFACT_SESSIONS:(.*?)::", content)
                    if match:
                        art_id = match.group(1)
                        display_text = content.replace(match.group(0), "").strip()
                        # Only show display text if it's meaningful (not just "TERMINATE" or empty)
                        if display_text and display_text != "TERMINATE" and len(display_text) > 10:
                            st.markdown(display_text)
                        
                        artifact = st.session_state["artifacts"].get(art_id)
                        if artifact and artifact.get("data"):
                            st.markdown("### 👥 Active Database Sessions")
                            with st.container(border=True):
                                df = pd.DataFrame(artifact["data"])
                                st.dataframe(df, width='stretch', hide_index=True)
                                
                                # Add summary metrics
                                if len(df) > 0:
                                    col1, col2, col3 = st.columns(3)
                                    with col1:
                                        total_sessions = len(df)
                                        st.metric("Total Active Sessions", total_sessions)
                                    with col2:
                                        blocked_sessions = len(df[df.get('BLOCKING_STATUS', pd.Series()).str.contains('BLOCKED', na=False)]) if 'BLOCKING_STATUS' in df.columns else 0
                                        st.metric("Blocked Sessions", blocked_sessions, delta=None if blocked_sessions == 0 else f"{blocked_sessions} sessions blocked")
                                    with col3:
                                        unique_users = df.get('USERNAME', pd.Series()).nunique() if 'USERNAME' in df.columns else 0
                                        st.metric("Unique Users", unique_users)
                        elif not art_id:
                            # Artifact ID is missing - try to find the most recent SESSION_LIST artifact
                            st.markdown(display_text if display_text else "Active sessions retrieved.")
                            # Find the most recent SESSION_LIST artifact
                            recent_session_artifact = None
                            for art_key, art_data in reversed(list(st.session_state["artifacts"].items())):
                                if art_data.get("type") == "SESSION_LIST":
                                    recent_session_artifact = (art_key, art_data)
                                    break
                            
                            if recent_session_artifact:
                                art_id, artifact = recent_session_artifact
                                if artifact.get("data"):
                                    st.markdown("### 👥 Active Database Sessions")
                                    with st.container(border=True):
                                        df = pd.DataFrame(artifact["data"])
                                        st.dataframe(df, width='stretch', hide_index=True)
                                        
                                        # Add summary metrics
                                        if len(df) > 0:
                                            col1, col2, col3 = st.columns(3)
                                            with col1:
                                                total_sessions = len(df)
                                                st.metric("Total Active Sessions", total_sessions)
                                            with col2:
                                                blocked_sessions = len(df[df.get('BLOCKING_STATUS', pd.Series()).str.contains('BLOCKED', na=False)]) if 'BLOCKING_STATUS' in df.columns else 0
                                                st.metric("Blocked Sessions", blocked_sessions, delta=None if blocked_sessions == 0 else f"{blocked_sessions} sessions blocked")
                                            with col3:
                                                unique_users = df.get('USERNAME', pd.Series()).nunique() if 'USERNAME' in df.columns else 0
                                                st.metric("Unique Users", unique_users)
                            else:
                                st.warning("⚠️ Could not find session data. Please try again.")
                    else:
                        # No artifact ID found - try to find most recent session artifact
                        st.markdown(content.replace("::ARTIFACT_SESSIONS::", "").strip())
                        # Find the most recent SESSION_LIST artifact
                        recent_session_artifact = None
                        for art_key, art_data in reversed(list(st.session_state["artifacts"].items())):
                            if art_data.get("type") == "SESSION_LIST":
                                recent_session_artifact = (art_key, art_data)
                                break
                        
                        if recent_session_artifact:
                            art_id, artifact = recent_session_artifact
                            if artifact.get("data"):
                                st.markdown("### 👥 Active Database Sessions")
                                with st.container(border=True):
                                    df = pd.DataFrame(artifact["data"])
                                    st.dataframe(df, width='stretch', hide_index=True)
                                    
                                    # Add summary metrics
                                    if len(df) > 0:
                                        col1, col2, col3 = st.columns(3)
                                        with col1:
                                            total_sessions = len(df)
                                            st.metric("Total Active Sessions", total_sessions)
                                        with col2:
                                            blocked_sessions = len(df[df.get('BLOCKING_STATUS', pd.Series()).str.contains('BLOCKED', na=False)]) if 'BLOCKING_STATUS' in df.columns else 0
                                            st.metric("Blocked Sessions", blocked_sessions, delta=None if blocked_sessions == 0 else f"{blocked_sessions} sessions blocked")
                                        with col3:
                                            unique_users = df.get('USERNAME', pd.Series()).nunique() if 'USERNAME' in df.columns else 0
                                            st.metric("Unique Users", unique_users)
                        else:
                            st.warning("⚠️ Could not extract session artifact ID. Please try again.")
                
                elif "::ARTIFACT_TABLESPACE:" in content:
                    match = re.search(r"::ARTIFACT_TABLESPACE:(.*?)::", content)
                    if match:
                        art_id = match.group(1)
                        display_text = content.replace(match.group(0), "").strip()
                        # Only show display text if it's meaningful (not just "TERMINATE" or empty)
                        if display_text and display_text != "TERMINATE" and len(display_text) > 10:
                            st.markdown(display_text)
                        
                        artifact = st.session_state["artifacts"].get(art_id)
                        if artifact and artifact.get("data"):
                            st.markdown("### 💾 Tablespace Status")
                            with st.container(border=True):
                                df = pd.DataFrame(artifact["data"])
                                st.dataframe(df, width='stretch', hide_index=True)
                                
                                # Add summary metrics
                                if len(df) > 0:
                                    col1, col2, col3 = st.columns(3)
                                    with col1:
                                        total_size = df.get('SIZE_GB', pd.Series()).sum() if 'SIZE_GB' in df.columns else 0
                                        st.metric("Total Size (GB)", f"{total_size:.2f}")
                                    with col2:
                                        total_used = df.get('USED_GB', pd.Series()).sum() if 'USED_GB' in df.columns else 0
                                        st.metric("Total Used (GB)", f"{total_used:.2f}")
                                    with col3:
                                        avg_pct = df.get('PCT_USED', pd.Series()).mean() if 'PCT_USED' in df.columns else 0
                                        st.metric("Avg % Used", f"{avg_pct:.1f}%")
                        elif not art_id:
                            # Artifact ID is missing - try to find the most recent TABLESPACE_STATUS artifact
                            st.markdown(display_text if display_text else "Tablespace status retrieved.")
                            # Find the most recent TABLESPACE_STATUS artifact
                            recent_tablespace_artifact = None
                            for art_key, art_data in reversed(list(st.session_state["artifacts"].items())):
                                if art_data.get("type") == "TABLESPACE_STATUS":
                                    recent_tablespace_artifact = (art_key, art_data)
                                    break
                            
                            if recent_tablespace_artifact:
                                art_id, artifact = recent_tablespace_artifact
                                if artifact.get("data"):
                                    st.markdown("### 💾 Tablespace Status")
                                    with st.container(border=True):
                                        df = pd.DataFrame(artifact["data"])
                                        st.dataframe(df, width='stretch', hide_index=True)
                                        
                                        # Add summary metrics
                                        if len(df) > 0:
                                            col1, col2, col3 = st.columns(3)
                                            with col1:
                                                total_size = df.get('SIZE_GB', pd.Series()).sum() if 'SIZE_GB' in df.columns else 0
                                                st.metric("Total Size (GB)", f"{total_size:.2f}")
                                            with col2:
                                                total_used = df.get('USED_GB', pd.Series()).sum() if 'USED_GB' in df.columns else 0
                                                st.metric("Total Used (GB)", f"{total_used:.2f}")
                                            with col3:
                                                avg_pct = df.get('PCT_USED', pd.Series()).mean() if 'PCT_USED' in df.columns else 0
                                                st.metric("Avg % Used", f"{avg_pct:.1f}%")
                            else:
                                st.warning("⚠️ Could not find tablespace data. Please try again.")
                    else:
                        # No artifact ID found - try to find most recent tablespace artifact
                        st.markdown(content.replace("::ARTIFACT_TABLESPACE::", "").strip())
                        # Find the most recent TABLESPACE_STATUS artifact
                        recent_tablespace_artifact = None
                        for art_key, art_data in reversed(list(st.session_state["artifacts"].items())):
                            if art_data.get("type") == "TABLESPACE_STATUS":
                                recent_tablespace_artifact = (art_key, art_data)
                                break
                        
                        if recent_tablespace_artifact:
                            art_id, artifact = recent_tablespace_artifact
                            if artifact.get("data"):
                                st.markdown("### 💾 Tablespace Status")
                                with st.container(border=True):
                                    df = pd.DataFrame(artifact["data"])
                                    st.dataframe(df, width='stretch', hide_index=True)
                                    
                                    # Add summary metrics
                                    if len(df) > 0:
                                        col1, col2, col3 = st.columns(3)
                                        with col1:
                                            total_size = df.get('SIZE_GB', pd.Series()).sum() if 'SIZE_GB' in df.columns else 0
                                            st.metric("Total Size (GB)", f"{total_size:.2f}")
                                        with col2:
                                            total_used = df.get('USED_GB', pd.Series()).sum() if 'USED_GB' in df.columns else 0
                                            st.metric("Total Used (GB)", f"{total_used:.2f}")
                                        with col3:
                                            avg_pct = df.get('PCT_USED', pd.Series()).mean() if 'PCT_USED' in df.columns else 0
                                            st.metric("Avg % Used", f"{avg_pct:.1f}%")
                        else:
                            st.warning("⚠️ Could not extract tablespace artifact ID. Please try again.")
                
                elif "::ARTIFACT_METRICS:" in content:
                    match = re.search(r"::ARTIFACT_METRICS:(.*?)::", content)
                    display_text = content.replace(match.group(0), "") if match else content
                    st.markdown(display_text)
                    
                    if match:
                        art_id = match.group(1)
                        artifact = st.session_state["artifacts"].get(art_id)
                        if artifact and artifact.get("data"):
                            df = pd.DataFrame(artifact["data"])
                            st.dataframe(df, width='stretch')
                
                elif "::ARTIFACT_SQL_METRICS:" in content:
                    match = re.search(r"::ARTIFACT_SQL_METRICS:(.*?)::", content)
                    display_text = content.replace(match.group(0), "") if match else content
                    st.markdown(display_text)
                    
                    if match:
                        art_id = match.group(1)
                        artifact = st.session_state["artifacts"].get(art_id)
                        if artifact and artifact.get("data"):
                            sql_metrics = artifact["data"]
                            
                            # Show note about cumulative values
                            if sql_metrics.get("NOTE"):
                                st.info(f"ℹ️ {sql_metrics.get('NOTE')}")
                            
                            # Display Top SQL by CPU
                            if sql_metrics.get("CPU"):
                                st.subheader("🔥 Top SQL by CPU Consumption (Real-Time)")
                                st.caption("Values are from the last 5 minutes (real-time activity). Check AVG_CPU_PER_EXEC for per-execution average.")
                                df_cpu = pd.DataFrame(sql_metrics["CPU"])
                                st.dataframe(df_cpu, width='stretch')
                            
                            # Display Top SQL by I/O
                            if sql_metrics.get("IO"):
                                st.subheader("💾 Top SQL by I/O Consumption (Real-Time)")
                                st.caption("Values are from the last 5 minutes (real-time activity). Check AVG_BLOCKS_PER_EXEC for per-execution average.")
                                df_io = pd.DataFrame(sql_metrics["IO"])
                                st.dataframe(df_io, width='stretch')
                            
                            # Display Top SQL by Memory
                            if sql_metrics.get("MEMORY"):
                                st.subheader("🧠 Top SQL by Memory (Buffer Gets) - Real-Time")
                                st.caption("Values are from the last 5 minutes (real-time activity). Check AVG_MBLOCKS_PER_EXEC for per-execution average.")
                                df_mem = pd.DataFrame(sql_metrics["MEMORY"])
                                st.dataframe(df_mem, width='stretch')
                
                elif "::ARTIFACT_UTILIZATION:" in content:
                    match = re.search(r"::ARTIFACT_UTILIZATION:(.*?)::", content)
                    display_text = content.replace(match.group(0), "") if match else content
                    st.markdown(display_text)
                    
                    if match:
                        art_id = match.group(1)
                        artifact = st.session_state["artifacts"].get(art_id)
                        if artifact and artifact.get("data"):
                            util = artifact["data"]
                        st.subheader("⚡ Current Database Resource Utilization")
                        
                        util_cols = st.columns(3)
                        
                        # CPU Utilization
                        with util_cols[0]:
                            cpu_data = util.get('CPU', {})
                            cpu_value = cpu_data.get('VALUE', 'N/A')
                            cpu_unit = cpu_data.get('UNIT', '%')
                            if isinstance(cpu_value, (int, float)):
                                cpu_status = '🔴' if cpu_value > 80 else '🟡' if cpu_value > 60 else '🟢'
                                st.metric("CPU Utilization", f"{cpu_value} {cpu_unit}", delta=None)
                                st.caption(f"{cpu_status} {'High' if cpu_value > 80 else 'Moderate' if cpu_value > 60 else 'Normal'}")
                            else:
                                st.metric("CPU Utilization", "N/A")
                                st.caption("Data not available")
                        
                        # I/O Utilization
                        with util_cols[1]:
                            io_data = util.get('IO', {})
                            io_value = io_data.get('VALUE', 'N/A')
                            io_unit = io_data.get('UNIT', 'IOPS')
                            io_thru = util.get('IO_THROUGHPUT', {})
                            io_thru_value = io_thru.get('VALUE', None)
                            
                            if isinstance(io_value, (int, float)):
                                st.metric("I/O Operations", f"{io_value} {io_unit}")
                                if io_thru_value:
                                    st.caption(f"Throughput: {io_thru_value} {io_thru.get('UNIT', 'MB/s')}")
                                else:
                                    st.caption("Current I/O activity")
                            else:
                                st.metric("I/O Operations", "N/A")
                                st.caption("Data not available")
                        
                        # Memory Utilization
                        with util_cols[2]:
                            mem_data = util.get('MEMORY', {})
                            mem_value = mem_data.get('VALUE', 'N/A')
                            mem_unit = mem_data.get('UNIT', 'GB')
                            sga_actual = mem_data.get('SGA_ACTUAL_GB', None)
                            pga_allocated = mem_data.get('PGA_ALLOCATED_GB', None)
                            total_memory = mem_data.get('TOTAL_MEMORY_GB', None)
                            
                            if isinstance(mem_value, (int, float)):
                                # Display total memory usage
                                st.metric("Total Memory Usage", f"{mem_value} {mem_unit}", delta=None)
                                if sga_actual is not None:
                                    st.caption(f"🟦 SGA Actual: {sga_actual:.2f}GB")
                                if pga_allocated is not None:
                                    st.caption(f"🟩 PGA Allocated: {pga_allocated:.2f}GB")
                                if total_memory and total_memory > 0:
                                    st.caption(f"📊 Total: {total_memory:.2f}GB")
                            else:
                                st.metric("Memory Utilization", "N/A")
                                st.caption("Data not available")
                
                elif "::ARTIFACT_KILL:" in content:
                    match = re.search(r"::ARTIFACT_KILL:(.*?)::", content)
                    display_text = content.replace(match.group(0), "") if match else content
                    st.markdown(display_text)
                    
                    if match:
                        art_id = match.group(1)
                        artifact = st.session_state["artifacts"].get(art_id)
                        if artifact:
                            st.warning(f"⚠️ Kill Session: SID={artifact['sid']}, SERIAL#={artifact['serial']}")
                            col1, col2 = st.columns(2)
                            with col1:
                                if st.button("✅ Confirm Kill", key=f"confirm_kill_{art_id}", type="primary"):
                                    db = artifact["db"]
                                    immediate = artifact["immediate"]
                                    sql = f"ALTER SYSTEM KILL SESSION '{artifact['sid']},{artifact['serial']}'"
                                    if immediate:
                                        sql += " IMMEDIATE"
                                    try:
                                        result = run_oracle_query(sql, db)
                                        st.success(f"Session {artifact['sid']},{artifact['serial']} killed successfully.")
                                        del st.session_state["artifacts"][art_id]
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"Failed to kill session: {handle_oracle_error(e)}")
                            with col2:
                                if st.button("❌ Cancel", key=f"cancel_kill_{art_id}"):
                                    del st.session_state["artifacts"][art_id]
                                    st.rerun()
                
                elif "::ARTIFACT_HEALTH_ANALYSIS:" in content:
                    match = re.search(r"::ARTIFACT_HEALTH_ANALYSIS:(.*?)::", content)
                    
                    if match:
                        art_id = match.group(1)
                        artifact = st.session_state["artifacts"].get(art_id)
                        if artifact:
                            # Format health analysis with tables and boxes
                            if artifact.get("is_question"):
                                st.subheader(f"💬 Answer: {artifact['report_label']}")
                            else:
                                st.subheader(f"📊 Analysis: {artifact['report_label']}")
                            
                            analysis_text = artifact["content"]
                            
                            # If it's a question, show the answer directly
                            if artifact.get("is_question"):
                                with st.container(border=True):
                                    st.markdown(analysis_text)
                            else:
                                # Parse structured analysis (Overall Status, Critical Issues, Warnings, Recommendations)
                                import re as re_module
                                
                                # Extract Overall Status section
                                status_match = re_module.search(r'Overall Status|Status\s*(.*?)(?=Critical|Warnings|Resource|Top|Recommendations|$)', analysis_text, re_module.DOTALL | re_module.IGNORECASE)
                                if status_match:
                                    status_text = status_match.group(1)
                                    st.markdown("### 🎯 Overall Status")
                                    with st.container(border=True):
                                        st.markdown(status_text)
                                
                                # Extract Critical Issues section
                                critical_match = re_module.search(r'Critical Issues|Critical\s*(.*?)(?=Warnings|Resource|Top|Recommendations|$)', analysis_text, re_module.DOTALL | re_module.IGNORECASE)
                                if critical_match:
                                    critical_text = critical_match.group(1)
                                    st.markdown("### 🔴 Critical Issues")
                                    with st.container(border=True):
                                        st.markdown(critical_text)
                                
                                # Extract Warnings section
                                warnings_match = re_module.search(r'Warnings|Warning\s*(.*?)(?=Resource|Top|Recommendations|$)', analysis_text, re_module.DOTALL | re_module.IGNORECASE)
                                if warnings_match:
                                    warnings_text = warnings_match.group(1)
                                    st.markdown("### 🟡 Warnings")
                                    with st.container(border=True):
                                        st.markdown(warnings_text)
                                
                                # Extract Resource Utilization section
                                resource_match = re_module.search(r'Resource Utilization|Resource\s*(.*?)(?=Top|Recommendations|$)', analysis_text, re_module.DOTALL | re_module.IGNORECASE)
                                if resource_match:
                                    resource_text = resource_match.group(1)
                                    st.markdown("### ⚡ Resource Utilization")
                                    with st.container(border=True):
                                        st.markdown(resource_text)
                                
                                # Extract Top Concerns section
                                top_match = re_module.search(r'Top Concerns|Top\s*(.*?)(?=Recommendations|$)', analysis_text, re_module.DOTALL | re_module.IGNORECASE)
                                if top_match:
                                    top_text = top_match.group(1)
                                    st.markdown("### 🔥 Top Concerns")
                                    with st.container(border=True):
                                        st.markdown(top_text)
                                
                                # Extract Recommendations
                                recommendations_match = re_module.search(r'Recommendations\s*(.*?)$', analysis_text, re_module.DOTALL | re_module.IGNORECASE)
                                if recommendations_match:
                                    recommendations_text = recommendations_match.group(1)
                                    st.markdown("### 💡 Recommendations")
                                    with st.container(border=True):
                                        # Format as bullet points
                                        lines = recommendations_text.split('\n')
                                        for line in lines:
                                            line = line.strip()
                                            if line and (line.startswith('-') or line.startswith('•') or line[0].isdigit() or 'recommend' in line.lower()):
                                                st.markdown(f"- {line.lstrip('- •1234567890. ')}")
                                            elif line:
                                                st.markdown(line)
                                
                                # If no structured sections found, show the full analysis
                                if not status_match and not critical_match and not warnings_match and not resource_match and not top_match and not recommendations_match:
                                    with st.container(border=True):
                                        st.markdown(analysis_text)
                                
                                # Add Q&A section for asking more questions about the health report
                                st.markdown("---")
                                with st.expander("💬 Chat about this health report", expanded=False):
                                    st.caption("Ask follow-up questions about the health report analysis.")
                                    question = st.text_input(
                                        "Your question:", 
                                        key=f"health_analysis_q_{art_id}", 
                                        placeholder="e.g., What should I prioritize? Can you explain this issue in more detail?"
                                    )
                                    if st.button("Ask", key=f"health_analysis_ask_{art_id}", width='stretch'):
                                        if question:
                                            st.session_state["messages"].append({
                                                "role": "user", 
                                                "content": f"Question about the health report: {question}"
                                            })
                                            st.session_state["_pending_user_input"] = f"Question about the health report: {question}"
                                            st.rerun()
                                        else:
                                            st.warning("Please enter a question")
                
                elif "::ARTIFACT_AWR_ANALYSIS:" in content:
                    match = re.search(r"::ARTIFACT_AWR_ANALYSIS:(.*?)::", content)
                    # Don't show the display text - only show the formatted artifact
                    # display_text = content.replace(match.group(0), "") if match else content
                    # st.markdown(display_text)  # REMOVED to prevent duplicate
                    
                    if match:
                        art_id = match.group(1)
                        artifact = st.session_state["artifacts"].get(art_id)
                        if artifact:
                            # Format AWR analysis with tables and boxes
                            if artifact.get("is_question"):
                                st.subheader(f"💬 Answer: {artifact['report_label']}")
                            else:
                                st.subheader(f"📊 Analysis: {artifact['report_label']}")
                            
                            analysis_text = artifact["content"]
                            
                            # If it's a question, show the answer directly without complex parsing
                            if artifact.get("is_question"):
                                with st.container(border=True):
                                    st.markdown(analysis_text)
                            else:
                                # Parse structured analysis (Load Profile, Top SQLs, Wait Events, Recommendations)
                                import re as re_module
                                
                                # Extract Load Profile section and format as table
                                load_profile_match = re_module.search(r'Load Profile\s*(.*?)(?=Top \d+ SQLs|Recommendations|Top Wait Events|$)', analysis_text, re_module.DOTALL | re_module.IGNORECASE)
                                if load_profile_match:
                                    load_profile_text = load_profile_match.group(1)
                                    st.markdown("### 📈 Load Profile")
                                    with st.container(border=True):
                                        # Parse Load Profile metrics into a table
                                        metrics = {}
                                        metric_patterns = {
                                            'DB Time': r'DB Time[:\s]+([\d,\.]+)',
                                            'DB CPU': r'DB CPU[:\s]+([\d,\.]+)',
                                            'Redo size': r'Redo size[:\s]+([\d,\.]+)',
                                            'Logical reads': r'Logical reads[:\s]+([\d,\.]+)',
                                            'Physical reads': r'Physical reads[:\s]+([\d,\.]+)',
                                            'User calls': r'User calls[:\s]+([\d,\.]+)',
                                            'Parses': r'Parses[:\s]+([\d,\.]+)',
                                            'Hard parses': r'Hard parses[:\s]+([\d,\.]+)',
                                            'Transactions': r'Transactions[:\s]+([\d,\.]+)'
                                        }
                                        for metric_name, pattern in metric_patterns.items():
                                            match = re_module.search(pattern, load_profile_text, re_module.IGNORECASE)
                                            if match:
                                                metrics[metric_name] = match.group(1)
                                        
                                        if metrics:
                                            # Display as two columns for better layout
                                            col1, col2 = st.columns(2)
                                            with col1:
                                                for i, (key, value) in enumerate(list(metrics.items())[:5]):
                                                    st.metric(key, value)
                                            with col2:
                                                for i, (key, value) in enumerate(list(metrics.items())[5:]):
                                                    st.metric(key, value)
                                        else:
                                            st.markdown(load_profile_text)
                                
                                # Extract Top 3 SQLs by Elapsed Time - More flexible parsing
                                elapsed_sqls_match = re_module.search(r'Top \d+ SQLs by Elapsed Time\s*(.*?)(?=Top \d+ SQLs by CPU|Top \d+ SQLs by I/O|Top \d+ SQLs by Memory|Top Wait Events|Recommendations|$)', analysis_text, re_module.DOTALL | re_module.IGNORECASE)
                                if elapsed_sqls_match:
                                    elapsed_sqls_text = elapsed_sqls_match.group(1)
                                    st.markdown("### ⏱️ Top 3 SQLs by Elapsed Time")
                                    with st.container(border=True):
                                        # Try multiple patterns to be more flexible
                                        sql_data = []
                                        # Pattern 1: Full format with all fields
                                        sql_pattern1 = r'SQL ID[:\s]+(\w+)[,\s]+(?:Elapsed Time|Elapsed)[:\s]+([\d,\.]+)\s*(?:seconds?|sec)?[,\s]+(?:Executions|Exec)[:\s]+([\d,\.]+)[,\s]+(?:% DB Time|DB Time)[:\s]+([\d,\.]+)%?[,\s]+(?:% CPU|CPU)[:\s]+([\d,\.]+)%?[,\s]+(?:% I/O|I/O)[:\s]+([\d,\.]+)%?[,\s]+SQL Text[:\s]+(.*?)(?=SQL ID|Top|$)'
                                        sqls1 = re_module.findall(sql_pattern1, elapsed_sqls_text, re_module.DOTALL | re_module.IGNORECASE)
                                        
                                        if sqls1:
                                            for sql in sqls1[:3]:
                                                sql_text_preview = sql[6].strip()[:100] + "..." if len(sql[6].strip()) > 100 else sql[6].strip()
                                                sql_data.append({
                                                    "SQL ID": sql[0],
                                                    "Elapsed (sec)": sql[1].replace(',', ''),
                                                    "Executions": sql[2].replace(',', ''),
                                                    "% DB Time": f"{sql[3]}%",
                                                    "% CPU": f"{sql[4]}%",
                                                    "% I/O": f"{sql[5]}%",
                                                    "SQL Text": sql_text_preview
                                                })
                                        else:
                                            # Pattern 2: Simpler format - split by lines and parse each
                                            lines = elapsed_sqls_text.split('\n')
                                            for line in lines[:10]:  # Check first 10 lines
                                                if 'SQL ID' in line or 'sql id' in line.lower():
                                                    # Try to extract SQL ID and basic info
                                                    sql_id_match = re_module.search(r'SQL ID[:\s]+(\w+)', line, re_module.IGNORECASE)
                                                    if sql_id_match:
                                                        sql_id = sql_id_match.group(1)
                                                        # Try to extract other metrics
                                                        elapsed_match = re_module.search(r'Elapsed[:\s]+([\d,\.]+)', line, re_module.IGNORECASE)
                                                        exec_match = re_module.search(r'Executions?[:\s]+([\d,\.]+)', line, re_module.IGNORECASE)
                                                        db_time_match = re_module.search(r'DB Time[:\s]+([\d,\.]+)', line, re_module.IGNORECASE)
                                                        cpu_match = re_module.search(r'CPU[:\s]+([\d,\.]+)', line, re_module.IGNORECASE)
                                                        io_match = re_module.search(r'I/O[:\s]+([\d,\.]+)', line, re_module.IGNORECASE)
                                                        
                                                        sql_data.append({
                                                            "SQL ID": sql_id,
                                                            "Elapsed (sec)": elapsed_match.group(1) if elapsed_match else "N/A",
                                                            "Executions": exec_match.group(1) if exec_match else "N/A",
                                                            "% DB Time": f"{db_time_match.group(1)}%" if db_time_match else "N/A",
                                                            "% CPU": f"{cpu_match.group(1)}%" if cpu_match else "N/A",
                                                            "% I/O": f"{io_match.group(1)}%" if io_match else "N/A",
                                                            "SQL Text": "See analysis text"
                                                        })
                                                        if len(sql_data) >= 3:
                                                            break
                                        
                                        if sql_data:
                                            st.dataframe(pd.DataFrame(sql_data), width='stretch', hide_index=True)
                                        else:
                                            # Fallback: show raw text but try to format it better
                                            st.markdown("**Raw Analysis:**")
                                            st.text(elapsed_sqls_text[:500] + "..." if len(elapsed_sqls_text) > 500 else elapsed_sqls_text)
                                
                                # Extract Top 3 SQLs by CPU - More flexible parsing
                                cpu_sqls_match = re_module.search(r'Top \d+ SQLs by CPU\s*(.*?)(?=Top \d+ SQLs by I/O|Top \d+ SQLs by Memory|Top Wait Events|Recommendations|$)', analysis_text, re_module.DOTALL | re_module.IGNORECASE)
                                if cpu_sqls_match:
                                    cpu_sqls_text = cpu_sqls_match.group(1)
                                    st.markdown("### 🔥 Top 3 SQLs by CPU")
                                    with st.container(border=True):
                                        sql_data = []
                                        # Try multiple patterns
                                        sql_pattern1 = r'SQL ID[:\s]+(\w+)[,\s]+(?:CPU Time|CPU)[:\s]+([\d,\.]+)\s*(?:seconds?|sec)?[,\s]+(?:Executions|Exec)[:\s]+([\d,\.]+)[,\s]+(?:% CPU|CPU)[:\s]+([\d,\.]+)%?[,\s]+SQL Text[:\s]+(.*?)(?=SQL ID|Top|$)'
                                        sqls1 = re_module.findall(sql_pattern1, cpu_sqls_text, re_module.DOTALL | re_module.IGNORECASE)
                                        
                                        if sqls1:
                                            for sql in sqls1[:3]:
                                                sql_text_preview = sql[4].strip()[:100] + "..." if len(sql[4].strip()) > 100 else sql[4].strip()
                                                sql_data.append({
                                                    "SQL ID": sql[0],
                                                    "CPU Time (sec)": sql[1].replace(',', ''),
                                                    "Executions": sql[2].replace(',', ''),
                                                    "% CPU": f"{sql[3]}%",
                                                    "SQL Text": sql_text_preview
                                                })
                                        else:
                                            # Fallback: parse line by line
                                            lines = cpu_sqls_text.split('\n')
                                            for line in lines[:10]:
                                                if 'SQL ID' in line or 'sql id' in line.lower():
                                                    sql_id_match = re_module.search(r'SQL ID[:\s]+(\w+)', line, re_module.IGNORECASE)
                                                    if sql_id_match:
                                                        sql_id = sql_id_match.group(1)
                                                        cpu_match = re_module.search(r'CPU Time[:\s]+([\d,\.]+)', line, re_module.IGNORECASE)
                                                        exec_match = re_module.search(r'Executions?[:\s]+([\d,\.]+)', line, re_module.IGNORECASE)
                                                        cpu_pct_match = re_module.search(r'% CPU[:\s]+([\d,\.]+)', line, re_module.IGNORECASE)
                                                        
                                                        sql_data.append({
                                                            "SQL ID": sql_id,
                                                            "CPU Time (sec)": cpu_match.group(1) if cpu_match else "N/A",
                                                            "Executions": exec_match.group(1) if exec_match else "N/A",
                                                            "% CPU": f"{cpu_pct_match.group(1)}%" if cpu_pct_match else "N/A",
                                                            "SQL Text": "See analysis text"
                                                        })
                                                        if len(sql_data) >= 3:
                                                            break
                                        
                                        if sql_data:
                                            st.dataframe(pd.DataFrame(sql_data), width='stretch', hide_index=True)
                                        else:
                                            st.text(cpu_sqls_text[:500] + "..." if len(cpu_sqls_text) > 500 else cpu_sqls_text)
                                
                                # Extract Top 3 SQLs by I/O - More flexible parsing
                                io_sqls_match = re_module.search(r'Top \d+ SQLs by I/O\s*(.*?)(?=Top \d+ SQLs by Memory|Top Wait Events|Recommendations|$)', analysis_text, re_module.DOTALL | re_module.IGNORECASE)
                                if io_sqls_match:
                                    io_sqls_text = io_sqls_match.group(1)
                                    st.markdown("### 💾 Top 3 SQLs by I/O")
                                    with st.container(border=True):
                                        sql_data = []
                                        sql_pattern1 = r'SQL ID[:\s]+(\w+)[,\s]+(?:Physical Reads|I/O)[:\s]+([\d,\.]+)[,\s]+(?:Buffer Gets|Buffer)[:\s]+([\d,\.]+)[,\s]+(?:% I/O|I/O)[:\s]+([\d,\.]+)%?[,\s]+SQL Text[:\s]+(.*?)(?=SQL ID|Top|$)'
                                        sqls1 = re_module.findall(sql_pattern1, io_sqls_text, re_module.DOTALL | re_module.IGNORECASE)
                                        
                                        if sqls1:
                                            for sql in sqls1[:3]:
                                                sql_text_preview = sql[4].strip()[:100] + "..." if len(sql[4].strip()) > 100 else sql[4].strip()
                                                sql_data.append({
                                                    "SQL ID": sql[0],
                                                    "Physical Reads": sql[1].replace(',', ''),
                                                    "Buffer Gets": sql[2].replace(',', ''),
                                                    "% I/O": f"{sql[3]}%",
                                                    "SQL Text": sql_text_preview
                                                })
                                        else:
                                            # Fallback parsing
                                            lines = io_sqls_text.split('\n')
                                            for line in lines[:10]:
                                                if 'SQL ID' in line or 'sql id' in line.lower():
                                                    sql_id_match = re_module.search(r'SQL ID[:\s]+(\w+)', line, re_module.IGNORECASE)
                                                    if sql_id_match:
                                                        sql_id = sql_id_match.group(1)
                                                        phys_reads_match = re_module.search(r'Physical Reads[:\s]+([\d,\.]+)', line, re_module.IGNORECASE)
                                                        buf_gets_match = re_module.search(r'Buffer Gets[:\s]+([\d,\.]+)', line, re_module.IGNORECASE)
                                                        io_pct_match = re_module.search(r'% I/O[:\s]+([\d,\.]+)', line, re_module.IGNORECASE)
                                                        
                                                        sql_data.append({
                                                            "SQL ID": sql_id,
                                                            "Physical Reads": phys_reads_match.group(1) if phys_reads_match else "N/A",
                                                            "Buffer Gets": buf_gets_match.group(1) if buf_gets_match else "N/A",
                                                            "% I/O": f"{io_pct_match.group(1)}%" if io_pct_match else "N/A",
                                                            "SQL Text": "See analysis text"
                                                        })
                                                        if len(sql_data) >= 3:
                                                            break
                                        
                                        if sql_data:
                                            st.dataframe(pd.DataFrame(sql_data), width='stretch', hide_index=True)
                                        else:
                                            st.text(io_sqls_text[:500] + "..." if len(io_sqls_text) > 500 else io_sqls_text)
                                
                                # Extract Top 3 SQLs by Memory
                                memory_sqls_match = re_module.search(r'Top \d+ SQLs by Memory\s*(.*?)(?=Top Wait Events|Recommendations|$)', analysis_text, re_module.DOTALL | re_module.IGNORECASE)
                                if memory_sqls_match:
                                    memory_sqls_text = memory_sqls_match.group(1)
                                    st.markdown("### 🧠 Top 3 SQLs by Memory")
                                    with st.container(border=True):
                                        sql_pattern = r'SQL ID[:\s]+(\w+)[,\s]+(?:Memory Usage|Memory)[:\s]+([\d,\.]+)[,\s]+(?:Executions|Exec)[:\s]+([\d,\.]+)[,\s]+(?:% Memory|Memory)[:\s]+([\d,\.]+)%?[,\s]+SQL Text[:\s]+(.*?)(?=SQL ID|$)'
                                        sqls = re_module.findall(sql_pattern, memory_sqls_text, re_module.DOTALL | re_module.IGNORECASE)
                                        
                                        if sqls:
                                            sql_data = []
                                            for sql in sqls[:3]:
                                                sql_text_preview = sql[4].strip()[:100] + "..." if len(sql[4].strip()) > 100 else sql[4].strip()
                                                sql_data.append({
                                                    "SQL ID": sql[0],
                                                    "Memory Usage": sql[1].replace(',', ''),
                                                    "Executions": sql[2].replace(',', ''),
                                                    "% Memory": f"{sql[3]}%",
                                                    "SQL Text": sql_text_preview
                                                })
                                            st.dataframe(pd.DataFrame(sql_data), width='stretch', hide_index=True)
                                        else:
                                            st.markdown(memory_sqls_text)
                                
                                # Extract Top Wait Events
                                wait_events_match = re_module.search(r'Top Wait Events\s*(.*?)(?=Recommendations|$)', analysis_text, re_module.DOTALL | re_module.IGNORECASE)
                                if wait_events_match:
                                    wait_events_text = wait_events_match.group(1)
                                    st.markdown("### ⏳ Top Wait Events")
                                    with st.container(border=True):
                                        # More flexible pattern to match wait events in various formats
                                        # Try multiple patterns to handle different AI output formats
                                        event_data = []
                                        
                                        # Pattern 1: Standard format with all fields on one line
                                        pattern1 = r'(?:^|\n)\s*(\d+)\.\s*\*\*Event Name:\s*([^*\n]+?)\*\*[^\n]*?Total Waits:\s*([\d,\.]+)[^\n]*?Time Waited[^:]*:\s*([\d,\.]+)\s*%?[^\n]*?Total Wait Time:\s*([\d,\.]+)\s*%'
                                        
                                        # Pattern 2: Multi-line format with Event Name on separate line
                                        pattern2 = r'(?:^|\n)\s*(\d+)\.\s*\*\*Event Name:\s*([^*\n]+?)\*\*\s*\n[^\n]*?Total Waits:\s*([\d,\.]+)[^\n]*?Time Waited[^:]*:\s*([\d,\.]+)\s*%?[^\n]*?Total Wait Time:\s*([\d,\.]+)\s*%'
                                        
                                        # Pattern 3: Simple format without numbering
                                        pattern3 = r'Event Name[:\s]+\*\*?([^*\n]+?)\*\*?[^\n]*?Total Waits[:\s]+([\d,\.]+)[^\n]*?Time Waited[^:]*[:\s]+([\d,\.]+)\s*%?[^\n]*?(?:% Total Wait Time|Total Wait Time)[:\s]+([\d,\.]+)\s*%'
                                        
                                        # Pattern 4: Most flexible - match any format with key-value pairs
                                        pattern4 = r'(?:^|\n)\s*(\d+)\.\s*\*\*Event Name:\s*([^*\n]+?)\*\*[^\n]*?(?:Total Waits|Waits)[:\s]+([\d,\.]+)[^\n]*?(?:Time Waited|Time)[^:]*[:\s]+([\d,\.]+)\s*%?[^\n]*?(?:% Total Wait Time|Total Wait Time)[:\s]+([\d,\.]+)\s*%'
                                        
                                        # Try each pattern
                                        for pattern in [pattern1, pattern2, pattern3, pattern4]:
                                            events = re_module.findall(pattern, wait_events_text, re_module.DOTALL | re_module.IGNORECASE | re_module.MULTILINE)
                                            if events:
                                                break
                                        
                                        if events:
                                            for event in events[:5]:  # Top 5 wait events
                                                # Handle different pattern group structures
                                                if len(event) >= 5:
                                                    # Pattern with numbering (pattern1, pattern2, pattern4)
                                                    event_name = event[1].strip() if len(event) > 1 else ""
                                                    total_waits = event[2].replace(',', '').strip() if len(event) > 2 else "N/A"
                                                    time_waited = event[3].replace(',', '').strip() if len(event) > 3 else "N/A"
                                                    total_wait_time = event[4].replace(',', '').strip() if len(event) > 4 else "N/A"
                                                elif len(event) >= 4:
                                                    # Pattern without numbering (pattern3)
                                                    event_name = event[0].strip() if len(event) > 0 else ""
                                                    total_waits = event[1].replace(',', '').strip() if len(event) > 1 else "N/A"
                                                    time_waited = event[2].replace(',', '').strip() if len(event) > 2 else "N/A"
                                                    total_wait_time = event[3].replace(',', '').strip() if len(event) > 3 else "N/A"
                                                else:
                                                    continue
                                                
                                                # Only add if event_name is not empty
                                                if event_name:
                                                    event_data.append({
                                                        "Event Name": event_name,
                                                        "Total Waits": total_waits,
                                                        "Time Waited (sec)": time_waited,
                                                        "% Total Wait Time": f"{total_wait_time}%"
                                                    })
                                        
                                        # If no pattern matched, try line-by-line parsing
                                        if not event_data:
                                            lines = wait_events_text.split('\n')
                                            current_event = {}
                                            event_num = 0
                                            
                                            for line in lines:
                                                line = line.strip()
                                                if not line:
                                                    continue
                                                
                                                # Match event name - check first before skipping lines starting with **
                                                name_match = re_module.search(r'Event Name[:\s]+\*\*?([^*\n]+?)\*\*?|Event Name[:\s]+([^*\n:]+?)(?:\s|$|:|\n)', line, re_module.IGNORECASE)
                                                if name_match:
                                                    if current_event:
                                                        event_data.append(current_event)
                                                    # Get event name from either capture group
                                                    event_name = (name_match.group(1) or name_match.group(2) or "").strip()
                                                    if event_name:
                                                        current_event = {"Event Name": event_name}
                                                        event_num += 1
                                                        if event_num > 5:
                                                            break
                                                        continue
                                                
                                                # Skip lines that are just formatting (but not Event Name lines)
                                                if line.startswith('**') and 'Event Name' not in line:
                                                    continue
                                                
                                                # Match total waits
                                                waits_match = re_module.search(r'(?:Total Waits|Waits)[:\s]+([\d,\.]+)', line, re_module.IGNORECASE)
                                                if waits_match and 'Total Waits' not in current_event:
                                                    current_event["Total Waits"] = waits_match.group(1).replace(',', '').strip()
                                                    continue
                                                
                                                # Match time waited
                                                time_match = re_module.search(r'Time Waited[^:]*[:\s]+([\d,\.]+)\s*%?', line, re_module.IGNORECASE)
                                                if time_match and 'Time Waited (sec)' not in current_event:
                                                    current_event["Time Waited (sec)"] = time_match.group(1).replace(',', '').strip()
                                                    continue
                                                
                                                # Match total wait time
                                                total_match = re_module.search(r'(?:% Total Wait Time|Total Wait Time)[:\s]+([\d,\.]+)\s*%', line, re_module.IGNORECASE)
                                                if total_match and '% Total Wait Time' not in current_event:
                                                    current_event["% Total Wait Time"] = f"{total_match.group(1).replace(',', '').strip()}%"
                                                    continue
                                            
                                            if current_event and ("Event Name" in current_event or len(current_event) > 1):
                                                # Ensure Event Name exists even if other fields are missing
                                                if "Event Name" not in current_event:
                                                    current_event["Event Name"] = "N/A"
                                                event_data.append(current_event)
                                        
                                        if event_data:
                                            # Ensure all events have all required fields
                                            for event in event_data:
                                                if "Event Name" not in event or not event.get("Event Name") or event.get("Event Name") == "N/A":
                                                    event["Event Name"] = "N/A"
                                                if "Total Waits" not in event:
                                                    event["Total Waits"] = "N/A"
                                                if "Time Waited (sec)" not in event:
                                                    event["Time Waited (sec)"] = "N/A"
                                                if "% Total Wait Time" not in event:
                                                    event["% Total Wait Time"] = "N/A"
                                            
                                            # Create dataframe with Event Name as first column
                                            df_events = pd.DataFrame(event_data)
                                            # Ensure Event Name is the first column
                                            column_order = ["Event Name", "Total Waits", "Time Waited (sec)", "% Total Wait Time"]
                                            # Only include columns that exist in the dataframe
                                            column_order = [col for col in column_order if col in df_events.columns]
                                            # Add any remaining columns that weren't in the order
                                            remaining_cols = [col for col in df_events.columns if col not in column_order]
                                            df_events = df_events[column_order + remaining_cols]
                                            
                                            st.dataframe(df_events, width='stretch', hide_index=True)
                                        else:
                                            # Fallback: display raw text if parsing fails
                                            st.markdown(wait_events_text)
                                
                                # Extract Recommendations
                                recommendations_match = re_module.search(r'Recommendations\s*(.*?)$', analysis_text, re_module.DOTALL | re_module.IGNORECASE)
                                if recommendations_match:
                                    recommendations_text = recommendations_match.group(1)
                                    st.markdown("### 💡 Recommendations")
                                    with st.container(border=True):
                                        # Format as numbered list
                                        lines = recommendations_text.split('\n')
                                        rec_num = 1
                                        for line in lines:
                                            line = line.strip()
                                            if line:
                                                # Check if it's already numbered or bulleted
                                                if line[0].isdigit() or line.startswith('-') or line.startswith('•'):
                                                    st.markdown(f"{rec_num}. {line.lstrip('0123456789.- • ')}")
                                                    rec_num += 1
                                                elif 'recommend' in line.lower() or len(line) > 20:
                                                    st.markdown(f"{rec_num}. {line}")
                                                    rec_num += 1
                                
                                # If no structured sections found, show the full analysis
                                if not load_profile_match and not elapsed_sqls_match and not cpu_sqls_match and not io_sqls_match and not memory_sqls_match and not wait_events_match and not recommendations_match:
                                    with st.container(border=True):
                                        st.markdown(analysis_text)
                                
                                # Add Q&A section for asking questions about the report - Make it more visible
                                st.markdown("---")
                                st.markdown("### 💬 Ask Questions About This Report")
                                with st.container(border=True):
                                    st.caption("💡 Ask specific questions about the AWR report. The AI will analyze the report data to provide detailed answers.")
                                    question = st.text_input(
                                        "Your question:", 
                                        key=f"awr_q_{i}_{art_id}", 
                                        placeholder="e.g., What is the buffer cache hit ratio? What are the top wait events? Why is performance slow? Which SQL is consuming the most CPU?"
                                    )
                                    col_ask1, col_ask2 = st.columns([3, 1])
                                    with col_ask1:
                                        if st.button("🤔 Ask Question", key=f"awr_ask_{i}_{art_id}", width='stretch', type="primary"):
                                            if question:
                                                # Route through main agentic system for full context and tool access
                                                # Add user message and process through main agent
                                                st.session_state["messages"].append({
                                                    "role": "user", 
                                                    "content": f"Question about the AWR report: {question}"
                                                })
                                                st.session_state["_pending_user_input"] = f"Question about the AWR report: {question}"
                                                st.session_state["_processing"] = True
                                                st.rerun()
                                            else:
                                                st.warning("⚠️ Please enter a question")
                                    with col_ask2:
                                        if st.button("📋 View Full Report", key=f"awr_view_{art_id}", width='stretch'):
                                            st.info("💡 Use the 'Download HTML' button above to view the complete AWR report.")
                
                else:
                    # Final check: Don't display internal context messages
                    # Check for conversation history patterns more aggressively
                    has_conversation_history = (
                        "Conversation History (for context)" in content or
                        "Conversation History (for context - use this" in content or
                        "New User Query:" in content or
                        "Recent Reports/Artifacts Available:" in content or
                        "IMPORTANT: Use the conversation history" in content or
                        "If the user is asking about:" in content or
                        "A previous report" in content and "A previous conversation topic" in content
                    )
                    
                    if not has_conversation_history:
                        # Check if this is a SUCCESS message for AWR report - show buttons immediately
                        if "SUCCESS" in content and "Generated" in content and "buttons_rendered_below" in content and st.session_state.get("awr_history"):
                            st.markdown(content.replace("buttons_rendered_below", ""))
                            # Show buttons right after the success message
                            rpt = st.session_state["awr_history"][-1]
                            col1, col2 = st.columns([1, 2])
                            with col1:
                                st.download_button(
                                    label="⬇️ Download HTML",
                                    data=rpt["report_html"],
                                    file_name=rpt["filename"],
                                    mime="text/html",
                                    key=f"dl_btn_{i}_{rpt['id']}"
                                )
                            with col2:
                                if st.button("🤖 Analyze with AI", key=f"an_btn_{i}_{rpt['id']}"):
                                    # Add user message and show spinner immediately
                                    st.session_state["messages"].append({
                                        "role": "user", 
                                        "content": "Analyze the report generated above. Highlight top wait events and SQLs."
                                    })
                                    st.session_state["_pending_user_input"] = "Analyze the report generated above. Highlight top wait events and SQLs."
                                    st.session_state["_processing"] = True
                                    st.rerun()
                        else:
                            # Clean content before displaying - remove any remaining history patterns
                            cleaned_content = content
                            # Remove conversation history blocks
                            cleaned_content = re.sub(r"Conversation History.*?New User Query:.*?IMPORTANT:", "", cleaned_content, flags=re.DOTALL | re.IGNORECASE)
                            cleaned_content = re.sub(r"Recent Reports/Artifacts Available:.*?Current Database:", "", cleaned_content, flags=re.DOTALL | re.IGNORECASE)
                            # Only show if there's meaningful content left
                            if cleaned_content.strip() and len(cleaned_content.strip()) > 10:
                                st.markdown(cleaned_content)
                            elif "::ARTIFACT_" in content:
                                # If only artifact marker, show a simple message
                                artifact_match = re.search(r"::ARTIFACT_(\w+):([^:]+)::", content)
                                if artifact_match:
                                    artifact_type = artifact_match.group(1)
                                    if artifact_type == "TABLESPACE":
                                        st.success("✅ Tablespace status retrieved successfully. See details below.")
                                    elif artifact_type == "SESSIONS":
                                        st.success("✅ Active sessions retrieved successfully. See details below.")
                                    elif artifact_type == "HEALTH":
                                        st.success("✅ Health check completed successfully. See details below.")
                                    else:
                                        st.success("✅ Task completed successfully. See details below.")
                    else:
                        # If conversation history got through, extract just the meaningful part
                        # Try to find the actual response after the history
                        parts = re.split(r"Conversation History.*?New User Query:.*?IMPORTANT:", content, flags=re.DOTALL | re.IGNORECASE)
                        if len(parts) > 1:
                            # Use the part after the history
                            meaningful_part = parts[-1].strip()
                            if meaningful_part and len(meaningful_part) > 10:
                                st.markdown(meaningful_part)
                            else:
                                st.info("Processing your request...")
                        else:
                            st.info("Processing your request...")
    
    # Show processing spinner as an assistant message right after the last user message (ChatGPT-style)
    if messages_to_show and messages_to_show[-1]["role"] == "user" and (st.session_state.get("_pending_user_input") or st.session_state.get("_processing")):
        with st.chat_message("assistant", avatar="🤖"):
            # Show "Agent processing..." with spinner (ChatGPT-style)
            st.markdown("""
            <div style="display: flex; align-items: center; gap: 8px;">
                <div style="width: 16px; height: 16px; border: 2px solid #e0e0e0; border-top: 2px solid #666; border-radius: 50%; animation: spin 0.8s linear infinite;"></div>
                <span>Agent processing...</span>
            </div>
            <style>
            @keyframes spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }
            </style>
            """, unsafe_allow_html=True)
    # Clear processing flag if assistant has responded
    elif messages_to_show and messages_to_show[-1]["role"] == "assistant" and st.session_state.get("_processing"):
        st.session_state["_processing"] = False
    
    # Check if we need to show date/time pickers for time inputs
    # This happens when the agent asks for time ranges (performance report, compare AWR, etc.)
    show_time_pickers = False
    time_picker_context = None
    
    if messages_to_show and messages_to_show[-1]["role"] == "assistant" and not st.session_state.get("_processing"):
        # Get last message and remove TERMINATE for detection
        last_message_raw = messages_to_show[-1]["content"]
        last_message = last_message_raw.lower().replace("terminate", "").strip()
        # Check if the assistant is asking for time inputs
        # Look for questions about time or explicit requests for time ranges
        time_keywords = ["start time", "end time", "time range", "baseline", "target", "performance report", "compare awr", "time period", "what time", "which time", "please provide", "need the time", "select the time", "provide the start", "provide the baseline", "baseline period", "target period"]
        
        # Check context from recent messages to determine what we're asking for
        recent_context = " ".join([msg.get("content", "").lower() for msg in messages_to_show[-5:]])
        
        # Check user's original request too
        user_messages = [msg.get("content", "").lower() for msg in messages_to_show[-5:] if msg.get("role") == "user"]
        user_context = " ".join(user_messages)
        
        # More robust detection - check for the exact phrase pattern
        has_time_request = any(keyword in last_message for keyword in time_keywords)
        has_performance_request = "performance report" in recent_context or ("generate" in recent_context and "report" in recent_context) or ("performance report" in user_context)
        has_compare_request = ("compare" in recent_context and "awr" in recent_context) or ("compare" in user_context and "awr" in user_context)
        
        if has_time_request:
            # Check if we're asking for performance report
            if has_performance_request and ("start time" in last_message or "end time" in last_message or "time range" in last_message):
                show_time_pickers = True
                time_picker_context = "performance_report"
            # Check if we're asking for compare AWR
            elif has_compare_request or ("baseline" in last_message and "target" in last_message):
                show_time_pickers = True
                time_picker_context = "compare_awr"
            # Fallback: if asking for start/end time in general, assume performance report
            elif "start time" in last_message and "end time" in last_message and "baseline" not in last_message:
                show_time_pickers = True
                time_picker_context = "performance_report"
        # Also check if user asked for performance report but agent didn't ask for time yet
        elif has_performance_request and not any(keyword in last_message for keyword in ["start time", "end time", "hours"]):
            # User asked for performance report but agent hasn't asked for time - show picker anyway
            show_time_pickers = True
            time_picker_context = "performance_report"
    
    # Show date/time pickers if needed
    if show_time_pickers and not st.session_state.get("_time_picker_submitted", False):
        with st.container(border=True):
            st.markdown("### 📅 Select Date and Time")
            
            if time_picker_context == "performance_report":
                # Performance report needs start and end time or hours back
                time_option = st.radio("Time Range Option", ["Specific Time Range", "Last N Hours"], key="perf_time_option_radio")
                
                if time_option == "Specific Time Range":
                    col1, col2 = st.columns(2)
                    with col1:
                        start_date = st.date_input("Start Date", value=datetime.now().date(), key="perf_start_date")
                        start_time = st.time_input("Start Time", value=datetime.now().time().replace(second=0, microsecond=0), key="perf_start_time")
                    with col2:
                        end_date = st.date_input("End Date", value=datetime.now().date(), key="perf_end_date")
                        end_time = st.time_input("End Time", value=datetime.now().time().replace(second=0, microsecond=0), key="perf_end_time")
                    
                    start_datetime = f"{start_date} {start_time.strftime('%H:%M:%S')}"
                    end_datetime = f"{end_date} {end_time.strftime('%H:%M:%S')}"
                    
                    col_submit, col_cancel = st.columns(2)
                    with col_submit:
                        if st.button("✅ Submit Times", key="submit_perf_times", type="primary"):
                            st.session_state["_time_picker_submitted"] = True
                            prompt = f"Generate AWR/ASH performance report from {start_datetime} to {end_datetime}."
                            st.session_state["messages"].append({"role": "user", "content": prompt})
                            st.session_state["_pending_user_input"] = prompt
                            st.session_state["_processing"] = True
                            st.rerun()
                    with col_cancel:
                        if st.button("❌ Cancel", key="cancel_perf_times"):
                            st.session_state["_time_picker_submitted"] = True
                            st.rerun()
                else:
                    # Last N Hours option
                    hours_back = st.number_input("Hours Back", min_value=0.1, max_value=168.0, value=1.0, step=0.1, key="perf_hours_back")
                    col_submit, col_cancel = st.columns(2)
                    with col_submit:
                        if st.button("✅ Submit", key="submit_perf_hours", type="primary"):
                            st.session_state["_time_picker_submitted"] = True
                            prompt = f"Generate AWR/ASH performance report for the last {hours_back} hours."
                            st.session_state["messages"].append({"role": "user", "content": prompt})
                            st.session_state["_pending_user_input"] = prompt
                            st.session_state["_processing"] = True
                            st.rerun()
                    with col_cancel:
                        if st.button("❌ Cancel", key="cancel_perf_hours"):
                            st.session_state["_time_picker_submitted"] = True
                            st.rerun()
                        
            elif time_picker_context == "compare_awr":
                # Compare AWR needs baseline and target periods
                st.markdown("**Baseline Period:**")
                col1, col2 = st.columns(2)
                with col1:
                    baseline_start_date = st.date_input("Baseline Start Date", value=datetime.now().date(), key="baseline_start_date")
                    baseline_start_time = st.time_input("Baseline Start Time", value=datetime.now().time().replace(second=0, microsecond=0), key="baseline_start_time")
                with col2:
                    baseline_end_date = st.date_input("Baseline End Date", value=datetime.now().date(), key="baseline_end_date")
                    baseline_end_time = st.time_input("Baseline End Time", value=datetime.now().time().replace(second=0, microsecond=0), key="baseline_end_time")
                
                st.markdown("**Target Period:**")
                col3, col4 = st.columns(2)
                with col3:
                    target_start_date = st.date_input("Target Start Date", value=datetime.now().date(), key="target_start_date")
                    target_start_time = st.time_input("Target Start Time", value=datetime.now().time().replace(second=0, microsecond=0), key="target_start_time")
                with col4:
                    target_end_date = st.date_input("Target End Date", value=datetime.now().date(), key="target_end_date")
                    target_end_time = st.time_input("Target End Time", value=datetime.now().time().replace(second=0, microsecond=0), key="target_end_time")
                
                baseline_start = f"{baseline_start_date} {baseline_start_time.strftime('%H:%M:%S')}"
                baseline_end = f"{baseline_end_date} {baseline_end_time.strftime('%H:%M:%S')}"
                target_start = f"{target_start_date} {target_start_time.strftime('%H:%M:%S')}"
                target_end = f"{target_end_date} {target_end_time.strftime('%H:%M:%S')}"
                
                col_submit, col_cancel = st.columns(2)
                with col_submit:
                    if st.button("✅ Submit Times", key="submit_compare_times", type="primary"):
                        st.session_state["_time_picker_submitted"] = True
                        prompt = f"Compare AWR reports: Baseline from {baseline_start} to {baseline_end}, Target from {target_start} to {target_end}."
                        st.session_state["messages"].append({"role": "user", "content": prompt})
                        st.session_state["_pending_user_input"] = prompt
                        st.session_state["_processing"] = True
                        st.rerun()
                with col_cancel:
                    if st.button("❌ Cancel", key="cancel_compare_times"):
                        st.session_state["_time_picker_submitted"] = True
                        st.rerun()
    
    # Reset time picker flag when a new user message is added
    if messages_to_show and messages_to_show[-1]["role"] == "user":
        st.session_state["_time_picker_submitted"] = False
    
    # Chat input box - at bottom (Gemini-style, always visible)
    user_input = st.chat_input(
        placeholder="Ask: 'Run SQL...', 'Find jobs...', 'AWR last 3 hours', 'Download 19.23 patch'"
    )
    
    # Handle user input (Gemini-style: show immediately, then process)
    if user_input:
        # Add user message immediately so it shows in chat (like Gemini)
        st.session_state["messages"].append({"role": "user", "content": user_input})
        # Mark that we need to process this
        st.session_state["_pending_user_input"] = user_input
        st.rerun()
    
    # Process pending user input after rerun (so user message is visible first)
    if st.session_state.get("_pending_user_input"):
        st.session_state["_processing"] = True
        pending_input = st.session_state["_pending_user_input"]
        del st.session_state["_pending_user_input"]
        # Process in the background
        handle_agent_execution(pending_input)
        st.session_state["_processing"] = False

with tab2:
    # Performance Tab
    st.header("⚡ Performance Analysis")
    
    db = st.session_state["current_db"]
    
    # Time range selector
    time_range_options = {
        '24 hours': 1,
        '3 days': 3,
        '7 days': 7,
        '1 month': 30
    }
    
    # Get the default index based on stored value or use default
    time_range_options_list = list(time_range_options.keys())
    default_index = 3  # Default to 1 month
    if "perf_time_range_selected" in st.session_state:
        stored_value = st.session_state["perf_time_range_selected"]
        if stored_value in time_range_options_list:
            default_index = time_range_options_list.index(stored_value)
    
    selected_time_range = st.selectbox(
        "Select Time Range for Metrics",
        options=time_range_options_list,
        index=default_index,
        key="perf_time_range"
    )
    days_selected = time_range_options[selected_time_range]
    
    # Load metrics button
    if st.button("🔄 Load Metrics", type="primary"):
        with st.spinner(f"Fetching historical metrics for last {selected_time_range}..."):
            historical_data = get_historical_metrics(db, days=days_selected)
            
            if historical_data.get('error'):
                st.error(f"Error loading historical data: {historical_data['error']}")
            else:
                # Store in session state for display
                st.session_state["historical_metrics"] = historical_data
                st.session_state["perf_time_range_selected"] = selected_time_range  # Use different key
                st.success(f"✅ Historical data loaded successfully for last {selected_time_range}!")
                st.rerun()
    
    # Display metrics right below the button if they exist
    if st.session_state.get("historical_metrics"):
        st.markdown("---")
        st.subheader(f"📈 System Metrics (Last {st.session_state.get('perf_time_range_selected', selected_time_range)})")
        
        hist_data = st.session_state.get("historical_metrics")
        if hist_data:
            # CPU Utilization Graph
            if hist_data.get('cpu') and len(hist_data['cpu']) > 0:
                st.subheader("🔥 CPU Utilization Over Time (%)")
                df_cpu = pd.DataFrame(hist_data['cpu'])
                # Handle both uppercase and lowercase column names
                timestamp_col = 'timestamp' if 'timestamp' in df_cpu.columns else 'TIMESTAMP'
                cpu_col = 'cpu_utilization' if 'cpu_utilization' in df_cpu.columns else 'CPU_UTILIZATION'
                if timestamp_col in df_cpu.columns and cpu_col in df_cpu.columns:
                    df_cpu[timestamp_col] = pd.to_datetime(df_cpu[timestamp_col], format='%Y-%m-%d %H:%M', errors='coerce')
                    df_cpu = df_cpu.sort_values(timestamp_col)
                    # Format index to show only date and time (no day name)
                    df_cpu_indexed = df_cpu.set_index(timestamp_col)
                    df_cpu_indexed.index = df_cpu_indexed.index.strftime('%Y-%m-%d %H:%M')
                    st.line_chart(df_cpu_indexed[cpu_col], width='stretch')
                else:
                    st.dataframe(df_cpu)
            else:
                st.info(f"No CPU utilization data available for the last {st.session_state.get('perf_time_range_selected', selected_time_range)}.")
            
            st.markdown("---")
            
            # AAS (Average Active Sessions) Graph
            if hist_data.get('aas') and len(hist_data['aas']) > 0:
                st.subheader("⚡ Average Active Sessions (AAS) Over Time")
                df_aas = pd.DataFrame(hist_data['aas'])
                timestamp_col = 'timestamp' if 'timestamp' in df_aas.columns else 'TIMESTAMP'
                aas_col = 'aas' if 'aas' in df_aas.columns else 'AAS'
                if timestamp_col in df_aas.columns and aas_col in df_aas.columns:
                    df_aas[timestamp_col] = pd.to_datetime(df_aas[timestamp_col], format='%Y-%m-%d %H:%M', errors='coerce')
                    df_aas = df_aas.sort_values(timestamp_col)
                    # Format index to show only date and time (no day name)
                    df_aas_indexed = df_aas.set_index(timestamp_col)
                    df_aas_indexed.index = df_aas_indexed.index.strftime('%Y-%m-%d %H:%M')
                    st.line_chart(df_aas_indexed[aas_col], width='stretch')
                else:
                    st.dataframe(df_aas)
            else:
                st.info(f"No AAS data available for the last {st.session_state.get('perf_time_range_selected', selected_time_range)}.")
            
            st.markdown("---")
            
            # I/O Utilization Graph
            if hist_data.get('io') and len(hist_data['io']) > 0:
                st.subheader("💾 I/O Throughput Over Time (MB/sec)")
                df_io = pd.DataFrame(hist_data['io'])
                timestamp_col = 'timestamp' if 'timestamp' in df_io.columns else 'TIMESTAMP'
                io_col = 'io_operations' if 'io_operations' in df_io.columns else 'IO_OPERATIONS'
                if timestamp_col in df_io.columns and io_col in df_io.columns:
                    df_io[timestamp_col] = pd.to_datetime(df_io[timestamp_col], format='%Y-%m-%d %H:%M', errors='coerce')
                    df_io = df_io.sort_values(timestamp_col)
                    # Format index to show only date and time (no day name)
                    df_io_indexed = df_io.set_index(timestamp_col)
                    df_io_indexed.index = df_io_indexed.index.strftime('%Y-%m-%d %H:%M')
                    st.line_chart(df_io_indexed[io_col], width='stretch')
                else:
                    st.dataframe(df_io)
            else:
                st.info(f"No I/O operations data available for the last {st.session_state.get('perf_time_range_selected', selected_time_range)}.")
            
            st.markdown("---")
            
            # Memory Utilization Graph
            if hist_data.get('memory') and len(hist_data['memory']) > 0:
                st.subheader("🧠 Memory Usage Over Time (GB)")
                df_mem = pd.DataFrame(hist_data['memory'])
                timestamp_col = 'timestamp' if 'timestamp' in df_mem.columns else 'TIMESTAMP'
                mem_col = 'memory_gb' if 'memory_gb' in df_mem.columns else 'MEMORY_GB'
                if timestamp_col in df_mem.columns and mem_col in df_mem.columns:
                    df_mem[timestamp_col] = pd.to_datetime(df_mem[timestamp_col], format='%Y-%m-%d %H:%M', errors='coerce')
                    df_mem = df_mem.sort_values(timestamp_col)
                    # Format index to show only date and time (no day name)
                    df_mem_indexed = df_mem.set_index(timestamp_col)
                    df_mem_indexed.index = df_mem_indexed.index.strftime('%Y-%m-%d %H:%M')
                    st.line_chart(df_mem_indexed[mem_col], width='stretch')
                else:
                    st.dataframe(df_mem)
            else:
                st.info(f"No memory usage data available for the last {st.session_state.get('perf_time_range_selected', selected_time_range)}.")
    
    st.markdown("---")
    
    # SQL ID Performance Analysis Section
    st.subheader("🔍 SQL ID Performance Analysis")
    st.info("ℹ️ **Note:** AWR (Automatic Workload Repository) data is required. Data is typically available for the last 7-30 days depending on your retention settings. Very recent data (last few minutes) may not be available in AWR.")
    col_sql1, col_sql2 = st.columns([2, 1])
    with col_sql1:
        sql_id_input = st.text_input(
            "Enter SQL ID",
            placeholder="e.g., abc123xyz",
            key="sql_id_input",
            help="Enter a SQL ID to view its historical performance metrics"
        )
    with col_sql2:
        sql_time_range = st.selectbox(
            "Time Range",
            options=['10 mins', '1 hour', '24 hours', '3 days', '7 days', '1 month'],
            index=2,
            key="sql_time_range"
        )
    
    if st.button("📊 Analyze SQL ID", key="analyze_sql_id"):
        if sql_id_input:
            with st.spinner(f"Analyzing SQL ID {sql_id_input}..."):
                sql_perf = get_sql_id_performance(sql_id_input, sql_time_range, db)
                
                if sql_perf.get('status') == 'success':
                    st.session_state["sql_perf_data"] = sql_perf
                    st.success(f"✅ Found performance data for SQL ID {sql_id_input}")
                    st.rerun()
                elif sql_perf.get('status') == 'not_found':
                    st.warning(sql_perf.get('message', 'No data found'))
                    # Clear any previous data
                    if "sql_perf_data" in st.session_state:
                        del st.session_state["sql_perf_data"]
                elif sql_perf.get('status') == 'error':
                    error_msg = sql_perf.get('error', 'Unknown error')
                    message = sql_perf.get('message', f'Error: {error_msg}')
                    st.error(message)
                    # Show detailed error in expander
                    with st.expander("🔍 Error Details"):
                        st.code(f"SQL ID: {sql_id_input}\nTime Range: {sql_time_range}\nError: {error_msg}", language='text')
                    # Clear any previous data
                    if "sql_perf_data" in st.session_state:
                        del st.session_state["sql_perf_data"]
                else:
                    st.error(f"Unexpected status: {sql_perf.get('status', 'unknown')}")
                    if "sql_perf_data" in st.session_state:
                        del st.session_state["sql_perf_data"]
        else:
            st.warning("Please enter a SQL ID")
    
    # Display SQL ID performance results
    if st.session_state.get("sql_perf_data"):
        sql_perf = st.session_state["sql_perf_data"]
        if sql_perf.get('status') == 'success':
            with st.expander(f"📊 SQL ID: {sql_perf['sql_id']} - Performance Details", expanded=True):
                st.write(f"**Plan Hash Value:** {sql_perf.get('plan_hash_value', 'N/A')}")
                st.write(f"**SQL Text:**")
                st.code(sql_perf.get('sql_text', 'N/A'), language='sql')
                
                if sql_perf.get('performance_data'):
                    df_sql = pd.DataFrame(sql_perf['performance_data'])
                    st.dataframe(df_sql, width='stretch', hide_index=True)
                    
                    # Performance trends
                    if 'START_TIME' in df_sql.columns or 'start_time' in df_sql.columns:
                        time_col = 'START_TIME' if 'START_TIME' in df_sql.columns else 'start_time'
                        elapsed_col = 'TOTAL_ELAPSED_SEC' if 'TOTAL_ELAPSED_SEC' in df_sql.columns else 'total_elapsed_sec'
                        if elapsed_col not in df_sql.columns:
                            elapsed_col = 'AVG_ELAPSED_SEC' if 'AVG_ELAPSED_SEC' in df_sql.columns else 'avg_elapsed_sec'
                        cpu_col = 'AVG_CPU_SEC' if 'AVG_CPU_SEC' in df_sql.columns else 'avg_cpu_sec'
                        
                        if elapsed_col in df_sql.columns:
                            df_sql[time_col] = pd.to_datetime(df_sql[time_col], format='%Y-%m-%d %H:%M', errors='coerce')
                            df_sql = df_sql.sort_values(time_col)
                            st.line_chart(df_sql.set_index(time_col)[elapsed_col], width='stretch')
    
    st.markdown("---")
    
    # Table Name SQL Lookup Section
    st.subheader("📋 Table Usage Analysis")
    st.info("ℹ️ **Note:** AWR data is required. Data is typically available for the last 7-30 days. Very recent data may not be available in AWR.")
    col_tbl1, col_tbl2 = st.columns([2, 1])
    with col_tbl1:
        table_name_input = st.text_input(
            "Enter Table Name",
            placeholder="e.g., EMPLOYEES",
            key="table_name_input",
            help="Enter a table name to find all SQLs that used it"
        )
    with col_tbl2:
        table_time_range = st.selectbox(
            "Time Range",
            options=['10 mins', '1 hour', '7 days', '1 month'],
            index=2,
            key="table_time_range"
        )
    
    if st.button("🔎 Find SQLs Using Table", key="find_table_sqls"):
        if table_name_input:
            with st.spinner(f"Finding SQLs using table {table_name_input}..."):
                table_result = get_table_sql_history(table_name_input, table_time_range, db)
                
                if table_result.get('status') == 'success':
                    st.session_state["table_sql_data"] = table_result
                    st.success(f"✅ Found {table_result.get('sql_count', 0)} SQLs using table {table_name_input}")
                    st.rerun()
                elif table_result.get('status') == 'not_found':
                    st.warning(table_result.get('message', 'No SQLs found'))
                    # Clear any previous data
                    if "table_sql_data" in st.session_state:
                        del st.session_state["table_sql_data"]
                elif table_result.get('status') == 'error':
                    error_msg = table_result.get('error', 'Unknown error')
                    message = table_result.get('message', f'Error: {error_msg}')
                    st.error(message)
                    # Show detailed error in expander
                    with st.expander("🔍 Error Details"):
                        st.code(f"Table Name: {table_name_input}\nTime Range: {table_time_range}\nError: {error_msg}", language='text')
                    # Clear any previous data
                    if "table_sql_data" in st.session_state:
                        del st.session_state["table_sql_data"]
                else:
                    st.error(f"Unexpected status: {table_result.get('status', 'unknown')}")
                    if "table_sql_data" in st.session_state:
                        del st.session_state["table_sql_data"]
        else:
            st.warning("Please enter a table name")
    
    # Display table SQL results
    if st.session_state.get("table_sql_data"):
        table_result = st.session_state["table_sql_data"]
        if table_result.get('status') == 'success':
            with st.expander(f"📋 Table: {table_result['table_name']} - SQLs Found ({table_result.get('sql_count', 0)})", expanded=True):
                if table_result.get('sqls'):
                    df_table = pd.DataFrame(table_result['sqls'])
                    st.dataframe(df_table, width='stretch', hide_index=True)
    
    st.markdown("---")
    
    # Top 10 Heavily Used Tables Section
    st.subheader("📊 Top 10 Heavily Used Tables")
    col_tbl_top1, col_tbl_top2 = st.columns([2, 1])
    with col_tbl_top1:
        st.caption("Find the most heavily accessed tables based on I/O operations")
    with col_tbl_top2:
        top_tables_time_range = st.selectbox(
            "Time Range",
            options=['24 hours', '3 days', '7 days', '1 month'],
            index=2,
            key="top_tables_time_range"
        )
    
    if st.button("🔍 Get Top Tables", key="get_top_tables"):
        with st.spinner(f"Analyzing table usage for last {top_tables_time_range}..."):
            top_tables_result = get_top_heavily_used_tables(top_tables_time_range, db)
            
            if top_tables_result.get('status') == 'success':
                st.session_state["top_tables_data"] = top_tables_result
                st.success(f"✅ Found top 10 heavily used tables for last {top_tables_time_range}")
                st.rerun()
            elif top_tables_result.get('status') == 'not_found':
                st.warning(top_tables_result.get('message', 'No data found'))
            else:
                st.error(f"Error: {top_tables_result.get('error', 'Unknown error')}")
    
    # Display top tables results
    if st.session_state.get("top_tables_data"):
        top_tables_result = st.session_state["top_tables_data"]
        if top_tables_result.get('status') == 'success':
            with st.expander(f"📊 Top 10 Heavily Used Tables (Last {top_tables_result.get('time_range', 'N/A')})", expanded=True):
                if top_tables_result.get('tables'):
                    df_top_tables = pd.DataFrame(top_tables_result['tables'])
                    st.dataframe(df_top_tables, width='stretch', hide_index=True)
    
    st.markdown("---")

with tab3:
    # SQL Explorer Tab
    st.header("🔍 SQL Explorer")
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        sql_query = st.text_area("Enter SQL Query", height=200, placeholder="SELECT * FROM v$session WHERE...", key="sql_explorer_query")
        
        execute_query = st.button("▶️ Execute Query", type="primary", width='stretch')
        
        if execute_query:
            if sql_query.strip():
                # Direct execution - show results immediately
                db = st.session_state["current_db"]
                validation = validate_sql_query(sql_query)
                if not validation["valid"]:
                    st.error(f"❌ {validation['reason']}")
                else:
                    with st.spinner("Executing query..."):
                        try:
                            sql_query_clean = sql_query.strip().rstrip(";")
                            audit_log("SQL_QUERY", db, {"sql_hash": hashlib.md5(sql_query_clean.encode()).hexdigest()[:8], "query_preview": sql_query_clean[:100]})
                            result = run_oracle_query(sql_query_clean, db)
                            
                            if isinstance(result, list):
                                if result:
                                    df = pd.DataFrame(result)
                                    st.success(f"✅ Query executed successfully. Returned {len(df)} rows.")
                                    st.dataframe(df, width='stretch', height=400)
                                    
                                    # Show download option
                                    csv = df.to_csv(index=False)
                                    st.download_button(
                                        label="📥 Download as CSV",
                                        data=csv,
                                        file_name=f"query_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                                        mime="text/csv"
                                    )
                                else:
                                    st.info("Query executed successfully. No rows returned.")
                            elif isinstance(result, dict) and "error" in result:
                                st.error(f"❌ {handle_oracle_error(Exception(result['error']))}")
                            else:
                                st.success(f"✅ {str(result)}")
                        except Exception as e:
                            st.error(f"❌ {handle_oracle_error(e)}")
            else:
                st.warning("Please enter a SQL query.")
        
        # Show recent query results from chat
        st.markdown("---")
        st.subheader("📋 Recent Query Results")
        if st.session_state.get("messages"):
            # Find recent SQL query results
            recent_sql_results = []
            for msg in reversed(st.session_state["messages"][-10:]):
                if msg["role"] == "assistant" and "SQL Result" in msg.get("content", ""):
                    recent_sql_results.append(msg)
                    if len(recent_sql_results) >= 3:  # Show last 3
                        break
            
            if recent_sql_results:
                for msg in recent_sql_results:
                    # Extract clean title - get the first line before markdown table formatting
                    content = msg.get('content', '')
                    # Find the summary line (usually "**SQL Result (X rows):**")
                    lines = content.split('\n')
                    title = "SQL Query Result"
                    for line in lines[:3]:  # Check first few lines
                        if "SQL Result" in line:
                            # Clean up markdown formatting
                            title = line.replace('**', '').replace('*', '').strip()
                            if title.endswith(':'):
                                title = title[:-1]
                            break
                    
                    with st.expander(f"📊 {title}", expanded=False):
                        st.markdown(content)
            else:
                st.caption("No recent query results. Execute a query to see results here.")
    
    with col2:
        st.subheader("💾 Save Query")
        query_name = st.text_input("Query Name", key="sql_explorer_name")
        query_desc = st.text_area("Description (optional)", height=100, key="sql_explorer_desc")
        
        if st.button("💾 Save", width='stretch'):
            if query_name and sql_query:
                handle_agent_execution(f"Save this query with name '{query_name}' and description '{query_desc}': {sql_query}")
            else:
                st.warning("Enter query name and SQL.")
        

with tab4:
    # Settings Tab
    st.header("⚙️ Settings")
    
    st.subheader("ℹ️ Help & Information")
    if st.button("👋 Show Welcome Message Again", width='stretch'):
        st.session_state["welcome_message_seen"] = False
        st.success("Welcome message will be shown in the Chat tab. Please switch to the Chat tab to see it.")
        st.rerun()
    
    st.markdown("---")
    
    st.subheader("🔐 Security")
    st.info("Credentials are loaded from environment variables (.env file)")
    
    st.subheader("📋 Audit Log")
    if st.session_state.get("audit_log"):
        st.write(f"Total log entries: {len(st.session_state['audit_log'])}")
        if st.button("View Last 50 Entries"):
            recent_logs = st.session_state["audit_log"][-50:]
            for log in reversed(recent_logs):
                with st.expander(f"{log['timestamp']} - {log['action']}"):
                    st.json(log)
    else:
        st.info("No audit log entries yet.")
    
    st.subheader("💾 Saved Queries")
    if st.session_state["saved_queries"]:
        for q in st.session_state["saved_queries"]:
            with st.expander(f"📝 {q['name']}"):
                st.code(q['sql'], language="sql")
                st.caption(q.get('description', 'No description'))
                if st.button("🗑️ Delete", key=f"del_{q['id']}"):
                    st.session_state["saved_queries"] = [sq for sq in st.session_state["saved_queries"] if sq["id"] != q["id"]]
                    st.rerun()
    else:
        st.info("No saved queries yet.")

