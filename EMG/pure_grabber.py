import subprocess
import time
import datetime
import re
import base64
import json
import os
import sys

def get_adb_path():
    # Detect the portable adb path from our installer bat
    adb_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adb", "platform-tools", "adb.exe")
    if os.path.exists(adb_path):
        return adb_path
    
    # Check fallback folder if mis-extracted
    adb_path_fallback = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adb", "adb.exe")
    if os.path.exists(adb_path_fallback):
        return adb_path_fallback
        
    return "adb.exe"

def run_adb(device_id, *args):
    adb = get_adb_path()
    cmd = [adb]
    if device_id:
        cmd.extend(["-s", device_id])
    cmd.extend(list(args))
    # Use DEVNULL to prevent adb from hanging on empty pipes when run as a background service
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=25)
        return res.stdout.strip() + res.stderr.strip()
    except Exception as e:
        return str(e)

def extract_tokens(device_id, login_code=None):
    debug_log = []
    def log(msg):
        debug_log.append(msg)
        print(msg)
        
    adb = get_adb_path()

    if not device_id:
        try:
            res = subprocess.run([adb, "devices"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
            m = re.search(r"(\S+)\s+device\b", res.stdout)
            if m:
                device_id = m.group(1)
                log(f"[*] Using device: {device_id}")
            else:
                log("[-] No device found.")
                return False, "\n".join(debug_log), None
        except Exception as e:
            log(f"[-] ADB device check failed: {e}")
            return False, "\n".join(debug_log), None
    else:
        if ":" in device_id:
            log(f"[*] Connecting to network device: {device_id}")
            conn_res = run_adb("", "connect", device_id)
            
    if login_code:
        log(f"[*] Running glogin with code: {login_code}")
        login_stripped = run_adb(device_id, "shell", "glogin", login_code).replace("\r", "")
        if "success" in login_stripped.lower() or "already logged" in login_stripped.lower():
            log("[+] glogin OK (already logged in)")
        else:
            log(f"[-] glogin failed: {login_stripped}")
            return False, "\n".join(debug_log), None

    # Check root access
    root_test = run_adb(device_id, "shell", "su -c 'id'")
    if "uid=0" not in root_test:
        log(f"[-] Root access not available. Output: {root_test}")
        return False, "\n".join(debug_log), None
    log("[+] Root access confirmed")

    log("[*] Restarting Pure app...")
    run_adb(device_id, "shell", "am force-stop com.getpure.pure")
    time.sleep(1)
    run_adb(device_id, "shell", "monkey -p com.getpure.pure -c android.intent.category.LAUNCHER 1")
    
    log("[*] Waiting for app to start and make network calls (8 seconds)...")
    time.sleep(8)

    today = datetime.datetime.now().strftime("%d_%m_%Y")
    log_file = f"/data/data/com.getpure.pure/files/daily/{today}.txt"
    
    log(f"[*] Reading log file: {log_file}")
    log_content = run_adb(device_id, "shell", f"su -c 'cat {log_file}'")
    
    if not log_content.strip() or "No such file" in log_content:
        log(f"[-] Log file empty or not found: {log_content[:50]}")
        return False, "\n".join(debug_log), None

    access_token = None
    refresh_token = None

    # Method 1
    m1 = re.search(r'"access_token":"(eyJ[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_.+/=]*)"', log_content)
    if m1:
        access_token = m1.group(1)
        log("[+] Access token extracted (from refresh response)")
        
    m2 = re.search(r'"refresh_token":"(eyJ[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_.+/=]*)"', log_content)
    if m2:
        refresh_token = m2.group(1)
        log("[+] Refresh token extracted")

    # Method 2
    if not access_token:
        # Find all matches
        matches = re.findall(r'Authorization: Bearer (eyJ[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_.+/=]*)', log_content)
        if matches:
            access_token = matches[-1] # last one
            log("[+] Access token extracted from Authorization header")

    if not access_token:
        log("[-] No JWT found in log.")
        log("[-] Open the Pure app manually, wait 10 seconds, then re-run this script.")
        return False, "\n".join(debug_log), None

    # Token Info
    try:
        parts = access_token.split(".")
        payload = parts[1].replace("-", "+").replace("_", "/")
        padding = len(payload) % 4
        if padding > 0:
            payload += "=" * (4 - padding)
        
        decoded = base64.b64decode(payload).decode("utf-8")
        js = json.loads(decoded)
        exp = js.get("exp", 0)
        
        expiry = datetime.datetime.fromtimestamp(exp, tz=datetime.timezone.utc)
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        minutes_left = round((expiry - now).total_seconds() / 60)
        
        log("\n=== TOKEN INFO ===")
        log(f"User ID : {js.get('user_id')}")
        log(f"Email   : {js.get('email')}")
        log(f"Expires : {expiry.strftime('%Y-%m-%d %H:%M:%S UTC')} ({minutes_left} minutes left)\n")
    except Exception as e:
        pass

    log("============================================")
    log("ACCESS TOKEN (Bearer):")
    log("============================================")
    log(access_token)
    log("")
    
    if refresh_token:
        log("============================================")
        log("REFRESH TOKEN:")
        log("============================================")
        log(refresh_token)
        log("")

    # Save files
    try:
        output_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jwt_output.json")
        out_data = {
            "bearerToken": access_token,
            "refreshToken": refresh_token,
            "extracted_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(out_data, f, indent=4)
        log(f"[+] Tokens saved to: {output_file}")
    except Exception as e:
        pass

    log("\n=== PASTE THIS INTO THE DASHBOARD ===")
    log(json.dumps({"bearerToken": access_token}))
    
    return True, "\n".join(debug_log), access_token

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-DeviceId", default="23.236.115.68:20370")
    parser.add_argument("-LoginCode", default="")
    args = parser.parse_args()
    
    extract_tokens(args.DeviceId, args.LoginCode)
