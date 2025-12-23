import os
import subprocess
import re
import sys
from typing import Dict, Optional

class OraclePatchAgent:
    def __init__(self, autoupgrade_dir: Optional[str] = None):
        """Initialize the agent with knowledge of the environment."""
        self.base_dir = autoupgrade_dir or os.environ.get('AUTUPGRADE_DIR', r"C:\Users\omkarav\Downloads\autoupgrade")
        self.paths = self._discover_paths()
        self.history = []
        
        # Verify environment sanity on startup
        if not os.path.exists(self.paths['jar_path']):
            print(f"⚠️  [AGENT] Warning: I cannot find the autoupgrade.jar at {self.paths['jar_path']}")

    def _discover_paths(self) -> Dict[str, str]:
        """Map out the tool environment."""
        return {
            'autoupgrade_dir': self.base_dir,
            'jdk_path': os.path.join(self.base_dir, r"jdk-11.0.29_windows-x64_bin\jdk-11.0.29\bin\java.exe"),
            'jar_path': os.path.join(self.base_dir, "autoupgrade.jar"),
            'config_file': os.path.join(self.base_dir, "download.cfg"),
            'log_dir': os.path.join(self.base_dir, "logs"),
            'keystore_path': os.path.join(self.base_dir, "keys") # Default, will be overridden if found in config
        }

    def perceive_intent(self, user_query: str) -> Dict:
        """
        Cognitive Step: Translate natural language into technical parameters.
        (You can replace this method with an LLM call for true AI parsing).
        """
        print(f"🤖 [AGENT] Thinking about request: '{user_query}'...")
        user_query = user_query.lower().strip()
        
        # Extract Version (e.g., 19.28, 21.3)
        version_match = re.search(r'(\d+\.\d+(\.\d+)*)', user_query)
        version = version_match.group(1) if version_match else None
        major_ver = version.split('.')[0] if version else '19'

        # Determine Patch Type contextually
        patch_type = "OPATCH" # Fallback
        if any(x in user_query for x in ["db ru", "database ru", "ru"]):
            patch_type = f"RU:{version}" if version else "RU:19.28"
        elif "grid" in user_query or "gi" in user_query:
            patch_type = f"GI:{version}" if version else "GI:19.28"
        elif "ojvm" in user_query:
            patch_type = f"OJVM:{version}" if version else "OJVM:19.28"
        
        intent = {
            'patch': patch_type,
            'target_version': major_ver,
            'platform': 'LINUX.X64', # Can be made dynamic if needed
            'original_query': user_query
        }
        
        print(f"🤖 [AGENT] I've identified you want: {intent['patch']} for Platform: {intent['platform']}")
        return intent

    def prepare_environment(self, intent: Dict) -> str:
        """
        Action Step: Safely update configuration files without corruption.
        """
        # 1. Read existing config to preserve global settings (Keystore/Logs)
        current_keystore = self.paths['keystore_path']
        current_log = self.paths['log_dir']
        
        if os.path.exists(self.paths['config_file']):
            try:
                with open(self.paths['config_file'], 'r') as f:
                    content = f.read()
                    # Robust Regex to grab values even if file is messy
                    k_match = re.search(r'global\.keystore=(.*?)(?:\s*global\.|\s*patch1\.|\s*$)', content)
                    if k_match: current_keystore = k_match.group(1).strip()
                    
                    l_match = re.search(r'global\.global_log_dir=(.*?)(?:\s*global\.|\s*patch1\.|\s*$)', content)
                    if l_match: current_log = l_match.group(1).strip()
            except Exception as e:
                print(f"⚠️  [AGENT] Error reading config: {e}. Reverting to defaults.")

        # 2. Construct the new config with EXPLICIT newlines
        config_lines = [
            f"global.global_log_dir={current_log}\n",
            f"global.keystore={current_keystore}\n",
            f"# Auto-generated for request: {intent['original_query']}\n",
            f"patch1.folder={self.paths['autoupgrade_dir']}\n",
            f"patch1.patch={intent['patch']}\n",
            f"patch1.target_version={intent['target_version']}\n",
            f"patch1.platform={intent['platform']}\n",
            f"patch1.download=YES\n"
        ]

        # 3. Write file
        with open(self.paths['config_file'], 'w') as f:
            f.writelines(config_lines)
            
        return self.paths['config_file']

    def execute_tool(self) -> Dict:
        """
        Action Step: Run the underlying Java tool and capture raw output.
        """
        print("🤖 [AGENT] executing Oracle AutoUpgrade tool...")
        
        cmd = [
            self.paths['jdk_path'],
            '-jar', self.paths['jar_path'],
            '-config', self.paths['config_file'],
            '-patch', '-mode', 'download'
        ]
        
        try:
            # We use Popen if we wanted to stream, but run() is safer for capturing exit codes
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=self.paths['autoupgrade_dir'],
                timeout=900 # 15 mins
            )
            return {
                "return_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "full_log": proc.stdout + "\n" + proc.stderr
            }
        except subprocess.TimeoutExpired:
            return {"return_code": -1, "full_log": "TIMEOUT: Operation took too long."}
        except Exception as e:
            return {"return_code": -1, "full_log": f"EXCEPTION: {str(e)}"}

    def analyze_outcome(self, result: Dict) -> str:
        """
        Reflection Step: Analyze the tool's output to determine success or specific failures.
        """
        log = result['full_log'].lower()
        
        # 1. Check for Success
        if "validated" in log or "downloading files" in log:
            # Extract what file was downloaded
            file_match = re.search(r'file:\s*([a-zA-Z0-9_.-]+)', result['full_log'])
            filename = file_match.group(1) if file_match else "patches"
            return f"✅ SUCCESS: Downloaded {filename} successfully."

        # 2. Check for Known Errors
        if "mos credentials" in log or "load_password" in log:
            return (
                "⚠️  NEEDS ATTENTION: Oracle Support credentials are missing or invalid.\n"
                "   -> I cannot fix this automatically for security reasons.\n"
                "   -> Please run: python patch_agent.py --setup"
            )
        
        if "parameter 'global.global_log_dir' found" in log:
            return "❌ INTERNAL ERROR: Config file corruption detected. I will reset configs next run."

        # 3. Catch-all
        return f"❌ FAILURE: The tool returned an error code {result['return_code']}.\n   Last log line: {result['full_log'].strip().splitlines()[-1]}"

    def run(self, user_query: str):
        """Main Agent execution flow."""
        # 1. Perceive
        intent = self.perceive_intent(user_query)
        
        # 2. Act (Prepare)
        self.prepare_environment(intent)
        
        # 3. Act (Execute)
        tool_result = self.execute_tool()
        
        # 4. Reflect & Respond
        final_response = self.analyze_outcome(tool_result)
        print(f"\n{final_response}\n")
        return tool_result

# ---------------------------------------------------------
# CLI / Interaction Layer
# ---------------------------------------------------------
if __name__ == "__main__":
    agent = OraclePatchAgent()
    
    # Check for direct setup flag
    if "--setup" in sys.argv:
        print("🤖 [AGENT] Entering credentials setup mode...")
        subprocess.run(
            [agent.paths['jdk_path'], '-jar', agent.paths['jar_path'], '-config', agent.paths['config_file'], '-load_password'],
            cwd=agent.paths['autoupgrade_dir']
        )
        sys.exit(0)

    # Interactive Mode
    print("="*60)
    print("🤖 Oracle Patching Agent Online")
    print("   Tell me what to download (e.g., 'download 19.28 RU')")
    print("   Type 'exit' to quit.")
    print("="*60)

    while True:
        try:
            user_input = input("\nYOU: ")
            if user_input.lower() in ['exit', 'quit', 'q']:
                print("🤖 [AGENT] Goodbye!")
                break
            
            if not user_input.strip():
                continue
                
            agent.run(user_input)
            
        except KeyboardInterrupt:
            print("\n🤖 [AGENT] Operation cancelled.")
            break