# patch_downloader.py
import os
import subprocess
import re
from typing import Dict
import argparse

def get_paths():
    autoupgrade_dir = os.environ.get('AUTUPGRADE_DIR', r"C:\Users\omkarav\Downloads\autoupgrade")
    jdk_path = os.path.join(autoupgrade_dir, r"jdk-11.0.29_windows-x64_bin\jdk-11.0.29\bin\java.exe")
    jar_path = os.path.join(autoupgrade_dir, "autoupgrade.jar")
    config_file = os.path.join(autoupgrade_dir, "download.cfg")
    log_dir = os.path.join(autoupgrade_dir, "logs")
    keystore_path = os.path.join(autoupgrade_dir, "keyss")
    return {
        'autoupgrade_dir': autoupgrade_dir,
        'jdk_path': jdk_path,
        'jar_path': jar_path,
        'config_file': config_file,
        'log_dir': log_dir,
        'keystore_path': keystore_path
    }

def parse_user_input(request: str) -> Dict:
    """Parse 'download 19.28 DB RU' → {'patch': 'RU:19.28', 'target_version': '19'}."""
    request = request.lower().strip()
    version_match = re.search(r'(\d+\.\d+)', request)
    version = version_match.group(1) if version_match else None
    major = version.split('.')[0] if version else '19'

    patch = "OPATCH"  # Default
    if "db ru" in request or "ru" in request:
        patch = f"RU:{version}" if version else "RU:19.28"
    elif "grid ru" in request:
        patch = f"GI:{version}" if version else "GI:19.28"
    elif "ojvm" in request:
        patch = f"OJVM:{version}" if version else "OJVM:19.28"
    elif "full" in request:
        patch = f"RU:{version},OPATCH,OJVM" if version else "RU:19.28,OPATCH,OJVM"

    return {'patch': patch, 'target_version': major}

def update_config_patch_section(request: str, paths: Dict) -> str:
    """Update patch1 section, safely preserving globals from existing file."""
    parsed = parse_user_input(request)
    
    # Defaults
    current_keystore = paths['keystore_path']
    current_log_dir = paths['log_dir']

    # Read existing config to preserve globals (even if file is messy/one-line)
    if os.path.exists(paths['config_file']):
        with open(paths['config_file'], 'r') as f:
            content = f.read()
            
            # Regex to find global.keystore (stops at next config key or newline)
            k_match = re.search(r'global\.keystore=(.*?)(?:\s*global\.|\s*patch1\.|\s*$)', content)
            if k_match:
                current_keystore = k_match.group(1).strip()
                
            # Regex to find global.global_log_dir
            l_match = re.search(r'global\.global_log_dir=(.*?)(?:\s*global\.|\s*patch1\.|\s*$)', content)
            if l_match:
                current_log_dir = l_match.group(1).strip()

    # Construct clean content with explicit newlines (\n)
    lines = [
        f"global.global_log_dir={current_log_dir}\n",
        f"global.keystore={current_keystore}\n",
        f"# Updated for {request}\n",
        f"patch1.folder={paths['autoupgrade_dir']}\n",
        f"patch1.patch={parsed['patch']}\n",
        f"patch1.target_version={parsed['target_version']}\n",
        f"patch1.platform=LINUX.X64\n",
        f"patch1.download=YES\n"
    ]
    
    with open(paths['config_file'], 'w') as f:
        f.writelines(lines)
    
    print(f"[DEBUG] Cleaned & updated {paths['config_file']}")
    print(f"[DEBUG] Keystore path set to: {current_keystore}")
    return paths['config_file']
def run_download_command(paths: Dict) -> Dict:
    """Run the exact command."""
    cmd = [
        paths['jdk_path'],
        '-jar', paths['jar_path'],
        '-config', paths['config_file'],
        '-patch', '-mode', 'download'
    ]
    
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=paths['autoupgrade_dir'],
            timeout=600  # 10 min timeout
        )
        output = proc.stdout + proc.stderr
        output_lower = output.lower()
        
        if proc.returncode == 0 and ("validated" in output_lower or "downloading files" in output_lower):
            return {"status": "completed", "output": output, "message": "Patches downloaded successfully!"}
        elif "mos credentials" in output_lower or "load_password" in output_lower:
            manual_cmd = f"cd /d {paths['autoupgrade_dir']} && {paths['jdk_path']} -jar {paths['jar_path']} -config {paths['config_file']} -load_password"
            return {
                "status": "setup_needed",
                "output": output,
                "message": f"MOS keystore setup needed (one-time). Copy-paste this into CMD:\n{manual_cmd}\nThen rerun the request."
            }
        else:
            return {"status": "error", "output": output, "message": "Download failed—check output."}
    except subprocess.TimeoutExpired:
        return {"status": "error", "output": "Timeout (10 min).", "message": "Download timed out."}
    except Exception as e:
        return {"status": "error", "output": str(e), "message": "Command execution failed."}

def download_patches(request: str, stream_output: bool = False) -> Dict:
    """Main function for GUI/integration."""
    paths = get_paths()
    
    # Update config (only patch section)
    config_file = update_config_patch_section(request, paths)
    
    # Run command
    result = run_download_command(paths)
    
    if stream_output:
        # For real-time, return running process (adapt for GUI spinner)
        result["process"] = None  # Placeholder; use Popen for streaming if needed
        result["message"] += f"\nConfig: {config_file}"
    
    return result

# Standalone test
def main():
    parser = argparse.ArgumentParser(description="Oracle Patch Downloader")
    parser.add_argument("--test", type=str, default="download 19.28 DB RU", help="User request")
    args = parser.parse_args()
    
    result = download_patches(args.test)
    
    print("\n" + "=" * 60)
    print(f"STATUS: {result['status'].upper()}")
    print("=" * 60)
    print(result["message"])
    if "output" in result:
        print("\nOUTPUT (last 800 chars):")
        print(result["output"][-800:])
    
    if result["status"] == "setup_needed":
        print("\nAfter manual setup, rerun this script.")

if __name__ == "__main__":
    main()