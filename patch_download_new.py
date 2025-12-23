import re
from typing import Dict, Any, Optional

# NOTE: The actual 'download_patches' function is assumed to be imported
# in the main application file (app_new1.py) from patch_downloader.

def is_patch_download_request(query: str) -> Optional[str]:
    """
    Checks if the user's query is an intent to download a patch.
    Returns the cleaned request string if intent is found (e.g., 'download 19.28 db ru'), otherwise None.
    """
    query = query.strip().lower()
    
    # Define keywords that trigger the patch download functionality
    keywords = ["download patch", "get patch", "patch download", "download"]
    
    if any(query.startswith(k) for k in keywords):
        # Extract the content of the request
        for k in keywords:
            if query.startswith(k):
                request_content = query[len(k):].strip()
                # Ensure there is actual version content for the patch request
                if re.search(r'\d+\.\d+', request_content):
                    # Return the command structured for patch_downloader.py
                    return f"download {request_content}"
        
    return None

def run_download_and_analyze(request: str, download_func) -> Dict[str, str]:
    """
    Wrapper to call download_patches, execute the tool, and analyze the result.
    
    Args:
        request: The cleaned request string (e.g., 'download 19.28 db ru').
        download_func: The imported 'download_patches' function from patch_downloader.py.
    
    Returns:
        A dictionary containing analysis results for Streamlit display.
    """
    
    # 1. The actual download call
    try:
        result = download_func(request)
    except Exception as e:
        return {
            "status": "error",
            "title": "Internal Tool Error",
            "content": f"The patch download tool encountered a Python error: `{e}`. Ensure `patch_downloader.py` is available and dependencies are met."
        }

    status = result.get('status', 'unknown').lower()
    output = result.get('output', 'No output received.')
    
    # 2. Agentic Analysis of the Status and Output
    if 'setup_needed' in status or 'mos credentials' in output.lower() or 'load_password' in output.lower():
        # Extract the exact setup command from the tool's output to give the user
        setup_command_match = re.search(r'(cd.*?download.cfg.*?load_password)', output, re.DOTALL)
        
        if setup_command_match:
            setup_command = setup_command_match.group(1).strip()
        else:
            # Fallback command generation (requires knowledge of paths from patch_downloader.py)
            setup_command = result.get('message', 'Check the output log below for the correct command.')

        return {
            "status": "attention",
            "title": " MOS Setup Required",
            "content": (
                "The Oracle AutoUpgrade tool requires your MOS (My Oracle Support) "
                "credentials to be loaded into the keystore (a one-time setup).\n\n"
                "**You MUST run this command manually in a Windows Command Prompt (CMD):**\n\n"
                f"```bash\n{setup_command}\n```\n\n"
                "After running it, complete the interactive prompt, then retry your download request."
            ),
            "log": output
        }
    
    elif status == 'error' or status == 'failure':
        # General download failure
        return {
            "status": "error",
            "title": " Patch Download Failed",
            "content": f"The patch download failed. The tool returned status **{status.upper()}**. Check the output below for detailed Java errors.",
            "log": output
        }
        
    elif status == 'success':
        # Simple success message
        success_message = f" **Patch Download Completed!**"
        
        # Try to find the download location in the log for better user feedback
        location_match = re.search(r'Downloading files to\s*(.*?)\n', output, re.IGNORECASE)
        if location_match:
            success_message += f"\n\nFiles were saved in: `{location_match.group(1).strip()}`"
        
        return {
            "status": "success",
            "title": success_message,
            "content": f"Successfully processed request for **{request.replace('download ', '')}**.",
            "log": output
        }

    # Catch-all for unknown status
    return {
        "status": "attention",
        "title": f" Agent Status: {status.upper()}",
        "content": f"The tool returned an unexpected status **{status.upper()}**. Review the output below for clues.",
        "log": output
    }