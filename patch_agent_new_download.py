# patch_agent_FINAL_NO_BS.py
import os
import subprocess
import re
from autogen import AssistantAgent, UserProxyAgent, register_function
from dotenv import load_dotenv

load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")

# Adjust these paths to your specific environment if needed
os.environ["REQUESTS_CA_BUNDLE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"
os.environ["SSL_CERT_FILE"] = r"C:\Users\omkarav\Downloads\Amdocs RSA Root CA.crt"
# ======================== PATHS ========================
BASE_DIR = os.environ.get('AUTUPGRADE_DIR', r"C:\Users\omkarav\Downloads\autoupgrade")
PATCHES_DIR = os.path.join(BASE_DIR, "patches")
JDK_PATH = os.path.join(BASE_DIR, r"jdk-11.0.29_windows-x64_bin\jdk-11.0.29\bin\java.exe")
JAR_PATH = os.path.join(BASE_DIR, "autoupgrade.jar")
CONFIG_FILE = os.path.join(BASE_DIR, "download.cfg")

os.makedirs(PATCHES_DIR, exist_ok=True)

# ======================== CONFIG ========================
common_llm_config = {
    "config_list": [{"model": "gpt-4o-mini", "api_key": openai_api_key}],
    "temperature": 0,
    "timeout": 120,
}
# ======================== TOOLS ========================
def write_config(content: str) -> str:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(content.strip() + "\n")
    return "CONFIG_WRITTEN"

def download_patches() -> str:
    cmd = [JDK_PATH, "-jar", JAR_PATH, "-config", CONFIG_FILE, "-patch", "-mode", "download"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, cwd=BASE_DIR)
        out = result.stdout + result.stderr

        if "Cannot find the latest Release Update" in out or "not supported" in out.lower():
            return "FAILED: No such patch exists yet (19.26+ not released as of Nov 2025)"

        if "mos credentials" in out.lower() or "load_password" in out.lower():
            manual = f'cd /d "{BASE_DIR}" && "{JDK_PATH}" -jar "{JAR_PATH}" -config "{CONFIG_FILE}" -load_password'
            return f"ERROR: MOS CREDENTIALS MISSING\n\nRun ONCE:\n\n{manual}\n\nThen try again."

        if result.returncode == 0 and ("downloaded" in out.lower() or "validated" in out.lower()):
            return "SUCCESS: Patches downloaded to:\n" + PATCHES_DIR

        return "FAILED:\n" + out[-1000:]

    except Exception as e:
        return f"CRASH: {e}"

# ======================== AGENT ========================
assistant = AssistantAgent(
    name="PatchMaster",
    system_message="""You are a ruthless Oracle patch robot.
You get it right ONCE. No retries.

Rules:
- Always include:
  global.global_log_dir = C:\\Users\\omkarav\\Downloads\\autoupgrade\\logs
  global.keystore = C:\\Users\\omkarav\\Downloads\\autoupgrade\\keyss
  patch1.folder = C:\\Users\\omkarav\\Downloads\\autoupgrade\\patches
  patch1.download=YES
  patch1.target_version=19
  patch1.platform=LINUX.X64

PATCH MAPPING (strict!):
- if the user says anything with "GI" or "grid" → patch1.patch=OCW:19.28 (or the version they said)
- if the user says "DB RU" or just "RU" or "release update" → patch1.patch=RU:19.28
- if the user says "OJVM" → patch1.patch=OJVM:19.28
- if the user says "opatch" or "latest opatch" → patch1.patch=OPATCH
- if the user says "full" → patch1.patch=RU:19.28,OPATCH,OJVM,OCW

You call write_config ONCE → then download_patches ONCE → then STOP FOREVER.""",
    llm_config=common_llm_config,
    max_consecutive_auto_reply=2,  # only 2 messages max: config + download
)

executor = UserProxyAgent(
    name="Runner",
    human_input_mode="NEVER",
    code_execution_config=False,
    max_consecutive_auto_reply=2,
    is_termination_msg=lambda x: x and ("SUCCESS" in str(x.get("content","")) or "FAILED" in str(x.get("content","")) or "ERROR" in str(x.get("content",""))),
)

register_function(write_config, caller=assistant, executor=executor, name="write_config", description="Write config")
register_function(download_patches, caller=assistant, executor=executor, name="download_patches", description="Download")

# ======================== MAIN ========================
def download(request: str):
    print(f"nRequest → {request}")
    print("Working...n")

    assistant.initiate_chat(
        executor,
        message=f"USER WANTS: {request.upper()}nDO IT PERFECTLY ON FIRST TRY.nNO RETRIES.nNO BULLSHIT.",
        clear_history=True
    )

# ======================== RUN ========================
if __name__ == "__main__":
    print("Oracle Patch Agent — FINAL NO-RETRY EDITION")
    print("Works first time or tells you why it can't.n")

    while True:
        r = input("nYour request (or quit): ").strip()
        if r.lower() in {"quit", "q", "exit", ""}: break
        if r: download(r)
        print("n" + "═" * 80 + "n")