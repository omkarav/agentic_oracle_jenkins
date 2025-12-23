# patch_downloader_llm.py
import os
from autogen import AssistantAgent, UserProxyAgent, register_function
import subprocess
from dotenv import load_dotenv
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")


os.environ["REQUESTS_CA_BUNDLE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"
os.environ["SSL_CERT_FILE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"
# ======================== PATHS ========================

# ======================== PATHS ========================
BASE_DIR = os.environ.get('AUTUPGRADE_DIR', r"C:\Users\omkarav\Downloads\autoupgrade")
PATCHES_DIR = os.path.join(BASE_DIR, "patches")
JDK_PATH = os.path.join(BASE_DIR, r"jdk-11.0.29_windows-x64_bin\jdk-11.0.29\bin\java.exe")
JAR_PATH = os.path.join(BASE_DIR, "autoupgrade.jar")
CONFIG_FILE = os.path.join(BASE_DIR, "download.cfg")

os.makedirs(PATCHES_DIR, exist_ok=True)

common_llm_config = {
    "config_list": [{"model": "gpt-4o-mini", "api_key": openai_api_key}],
    "temperature": 0,
    "timeout": 120,
}
# ======================== TOOLS ========================
def write_config_file(content: str) -> str:
    """Tool: Write the exact content to download.cfg"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(content.strip() + "\n")
        return "SUCCESS: download.cfg written"
    except Exception as e:
        return f"ERROR: {e}"

def run_patch_download() -> str:
    """Tool: Execute autoupgrade.jar download"""
    cmd = [JDK_PATH, "-jar", JAR_PATH, "-config", CONFIG_FILE, "-patch", "-mode", "download"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, cwd=BASE_DIR)
        output = result.stdout + result.stderr

        if "mos credentials" in output.lower() or "load_password" in output.lower():
            manual = f'cd /d "{BASE_DIR}" && "{JDK_PATH}" -jar "{JAR_PATH}" -config "{CONFIG_FILE}" -load_password'
            return f"MOS_SETUP_REQUIRED\n\nRun this once:\n\n{manual}"

        if result.returncode == 0 and ("downloaded" in output.lower() or "validated" in output.lower()):
            return f"SUCCESS\nPatches saved to:\n{PATCHES_DIR}\n\nLast lines:\n" + "\n".join(output.splitlines()[-20:])

        return "FAILED\n" + output[-1200:]
    except Exception as e:
        return f"EXCEPTION: {e}"

# ======================== AGENTS ========================
patch_llm_agent = AssistantAgent(
    name="OraclePatchExpert",
    system_message="""
You are the world's best Oracle Patch Automation Specialist.

Your job:
1. Understand natural language request
2. Generate PERFECT download.cfg content for download.cfg
3. Call write_config_file with it
4. Immediately call run_patch_download
5. Then STOP.

MANDATORY LINES (always include exactly):
global.global_log_dir = C:\\Users\\omkarav\\Downloads\\autoupgrade\\logs
global.keystore = C:\\Users\\omkarav\\Downloads\\autoupgrade\\keyss
patch1.folder = C:\\Users\\omkarav\\Downloads\\autoupgrade\\patches
patch1.download = YES
patch1.target_version = 19
patch1.platform = LINUX.X64

PATCH LOGIC (you decide based on user words):
- "GI", "grid", "grid infrastructure" → patch1.patch=OCW:19.28
- "DB RU", "database ru", "ru" → patch1.patch=RU:19.28
- "OJVM" → patch1.patch=OJVM:19.28
- "opatch" → patch1.patch=OPATCH
- "full", "bundle", "all" → patch1.patch=RU:19.28,OPATCH,OJVM,OCW
- If version mentioned (19.25, 19.30, etc.) → use that instead of 19.28

NEVER guess wrong. NEVER retry. One shot only.
""",
    llm_config=common_llm_config,
    max_consecutive_auto_reply=1,
)

executor = UserProxyAgent(
    name="PatchExecutor",
    human_input_mode="NEVER",
    code_execution_config=False,
    is_termination_msg=lambda x: x and ("SUCCESS" in str(x.get("content","")) or "MOS_SETUP_REQUIRED" in str(x.get("content","")) or "FAILED" in str(x.get("content",""))),
)

register_function(write_config_file, caller=patch_llm_agent, executor=executor, name="write_config_file", description="Write full config")
register_function(run_patch_download, caller=patch_llm_agent, executor=executor, name="run_patch_download", description="Start download")

# ======================== MAIN FUNCTION (called by app) ========================
def download_oracle_patch(user_request: str):
    print(f"[Patch Agent] Request: {user_request}")
    try:
        patch_llm_agent.initiate_chat(
            executor,
            message=f"USER REQUEST: {user_request}\n\nGenerate config → download → done.",
            clear_history=True
        )
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = f.read()
            return {
                "status": "success",
                "message": f"**Download started!**\n\nConfig:\n```\n{cfg}\n```\n\nCheck folder:\n`{PATCHES_DIR}`"
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}
    return {"status": "info", "message": "Download in progress..."}