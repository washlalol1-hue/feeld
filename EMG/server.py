#!/usr/bin/env python3
"""
Proxy-formatter local server with VA management.

HOW TO USE:
  1. Put server.py and the HTML files in the same folder.
  2. Run:  python server.py
  3. Open: http://127.0.0.1:5000          (API Panel)
           http://127.0.0.1:5000/admin     (Admin Panel)
           http://127.0.0.1:5000/va        (VA Dashboard)
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
import re
import shutil

PORT = 5000
HTML_FILE = "proxy-formatter.html"
ADMIN_HTML = "admin.html"
VA_DATA_FILE = "va_data.json"
VA_IMAGES_DIR = "va_images"

# In-memory sessions: token -> {type, username, expires}
sessions = {}


def here():
    return os.path.dirname(os.path.abspath(__file__))


def data_path():
    return os.path.join(here(), VA_DATA_FILE)


def images_dir():
    return os.path.join(here(), VA_IMAGES_DIR)


def load_data():
    p = data_path()
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    default = {"admin_password": "admin", "users": {}}
    save_data(default)
    return default


def save_data(d):
    with open(data_path(), "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)


def create_session(stype, username):
    token = uuid.uuid4().hex
    sessions[token] = {"type": stype, "username": username, "expires": time.time() + 86400}
    return token


def get_session(headers, required_type=None):
    auth = headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    s = sessions.get(token)
    if not s or s["expires"] < time.time():
        sessions.pop(token, None)
        return None
    if required_type and s["type"] != required_type:
        return None
    return s


def slugify(name):
    return re.sub(r'[^a-z0-9_-]', '', name.lower().strip().replace(' ', '-'))


def user_images_path(uid):
    p = os.path.join(images_dir(), uid)
    os.makedirs(p, exist_ok=True)
    return p


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} - {fmt % args}")

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    # ── Helpers ──

    def _json(self, code, obj):
        payload = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _text(self, code, msg):
        payload = msg.encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _serve_file(self, filename, content_type="text/html; charset=utf-8"):
        fpath = os.path.join(here(), filename)
        if not os.path.exists(fpath):
            self._text(404, f"File not found: {filename}")
            return
        with open(fpath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length)

    def _read_json(self):
        raw = self._read_body()
        return json.loads(raw)

    # ── GET ──

    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/", "/index.html", "/" + HTML_FILE):
            self._serve_file(HTML_FILE)
        elif path == "/admin":
            self._serve_file(ADMIN_HTML)
        elif path == "/va":
            self._serve_file(HTML_FILE)
        elif path == "/ping":
            self._json(200, {"status": "ok"})
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()

        # ── API: list users (admin) ──
        elif path == "/api/users":
            s = get_session(self.headers, "admin")
            if not s:
                self._json(401, {"error": "Unauthorized"})
                return
            d = load_data()
            users = []
            for uid, u in d["users"].items():
                users.append({
                    "id": uid,
                    "name": u["name"],
                    "active": u.get("active", True),
                    "created": u.get("created", ""),
                    "gmail_count": len(u.get("gmails", [])),
                    "image_count": len(u.get("images", [])),
                })
            self._json(200, {"users": users})

        # ── API: get single user detail (admin) ──
        elif path.startswith("/api/users/") and path.count("/") == 3:
            s = get_session(self.headers, "admin")
            if not s:
                self._json(401, {"error": "Unauthorized"})
                return
            uid = path.split("/")[3]
            d = load_data()
            u = d["users"].get(uid)
            if not u:
                self._json(404, {"error": "User not found"})
                return
            self._json(200, {
                "id": uid,
                "name": u["name"],
                "active": u.get("active", True),
                "created": u.get("created", ""),
                "gmails": u.get("gmails", []),
                "images": u.get("images", []),
            })

        # ── API: VA get own data ──
        elif path == "/api/va/me":
            s = get_session(self.headers, "va")
            if not s:
                self._json(401, {"error": "Unauthorized"})
                return
            d = load_data()
            u = d["users"].get(s["username"])
            if not u or not u.get("active", True):
                self._json(403, {"error": "Account disabled"})
                return
            gmails_safe = [{"email": g["email"], "password": g["password"]} for g in u.get("gmails", [])]
            self._json(200, {
                "name": u["name"],
                "gmails": gmails_safe,
                "images": u.get("images", []),
                "image_base": f"/va-images/{s['username']}/",
            })

        # ── Serve VA images ──
        elif path.startswith("/va-images/"):
            parts = path.split("/")
            if len(parts) >= 4:
                uid = parts[2]
                fname = "/".join(parts[3:])
                fpath = os.path.join(images_dir(), uid, fname)
                if os.path.exists(fpath) and os.path.isfile(fpath):
                    ext = os.path.splitext(fname)[1].lower()
                    ct_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                              ".webp": "image/webp", ".gif": "image/gif"}
                    ct = ct_map.get(ext, "application/octet-stream")
                    with open(fpath, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self._cors()
                    self.send_header("Content-Type", ct)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "public, max-age=3600")
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._text(404, "Image not found")
            else:
                self._text(404, "Not found")

        else:
            self._text(404, "Not found")

    # ── POST / PATCH / DELETE ──

    def do_POST(self):
        path = self.path.split("?")[0]

        # ── Admin login ──
        if path == "/api/admin/login":
            try:
                body = self._read_json()
            except:
                self._json(400, {"error": "Invalid JSON"})
                return
            d = load_data()
            if body.get("password") == d["admin_password"]:
                token = create_session("admin", "__admin__")
                self._json(200, {"token": token})
            else:
                self._json(401, {"error": "Wrong password"})
            return

        # ── VA login ──
        if path == "/api/va/login":
            try:
                body = self._read_json()
            except:
                self._json(400, {"error": "Invalid JSON"})
                return
            d = load_data()
            license_key = body.get("license", "").strip()
            
            matched_u = None
            matched_uid = None
            for uid, u in d["users"].items():
                if u.get("password") == license_key or uid == license_key.lower():
                    matched_u = u
                    matched_uid = uid
                    break

            if not matched_u:
                self._json(401, {"error": "Invalid license key"})
                return
            if not matched_u.get("active", True):
                self._json(403, {"error": "Account disabled"})
                return
            token = create_session("va", matched_uid)
            self._json(200, {"token": token, "name": matched_u["name"]})
            return

        # ── Create user (admin) ──
        if path == "/api/users":
            s = get_session(self.headers, "admin")
            if not s:
                self._json(401, {"error": "Unauthorized"})
                return
            try:
                body = self._read_json()
            except:
                self._json(400, {"error": "Invalid JSON"})
                return
            name = body.get("name", "").strip()
            password = body.get("password", "").strip()
            if not name or not password:
                self._json(400, {"error": "Name and password required"})
                return
            uid = slugify(name)
            if not uid:
                self._json(400, {"error": "Invalid name"})
                return
            d = load_data()
            if uid in d["users"]:
                self._json(409, {"error": f"User '{uid}' already exists"})
                return
            d["users"][uid] = {
                "name": name,
                "password": password,
                "active": True,
                "created": time.strftime("%Y-%m-%d"),
                "gmails": [],
                "images": [],
            }
            save_data(d)
            self._json(201, {"id": uid, "name": name})
            return

        # ── Import gmails for user (admin) ──
        if path.startswith("/api/users/") and path.endswith("/gmails"):
            s = get_session(self.headers, "admin")
            if not s:
                self._json(401, {"error": "Unauthorized"})
                return
            uid = path.split("/")[3]
            try:
                body = self._read_json()
            except:
                self._json(400, {"error": "Invalid JSON"})
                return
            d = load_data()
            u = d["users"].get(uid)
            if not u:
                self._json(404, {"error": "User not found"})
                return
            raw = body.get("raw", "").strip()
            if not raw:
                self._json(400, {"error": "No data provided"})
                return
            lines = [l.strip() for l in raw.split("\n") if l.strip()]
            added = 0
            for line in lines:
                parts = line.split(";")
                if len(parts) >= 2:
                    entry = {
                        "email": parts[0].strip(),
                        "password": parts[1].strip(),
                        "recovery": parts[2].strip() if len(parts) > 2 else "",
                        "webhook": parts[3].strip() if len(parts) > 3 else "",
                    }
                    existing = [g["email"] for g in u.get("gmails", [])]
                    if entry["email"] not in existing:
                        u.setdefault("gmails", []).append(entry)
                        added += 1
            save_data(d)
            self._json(200, {"added": added, "total": len(u["gmails"])})
            return

        # ── Upload image for user (admin) ──
        if path.startswith("/api/users/") and path.endswith("/images"):
            s = get_session(self.headers, "admin")
            if not s:
                self._json(401, {"error": "Unauthorized"})
                return
            uid = path.split("/")[3]
            try:
                body = self._read_json()
            except:
                self._json(400, {"error": "Invalid JSON"})
                return
            d = load_data()
            u = d["users"].get(uid)
            if not u:
                self._json(404, {"error": "User not found"})
                return
            b64 = body.get("base64", "")
            filename = body.get("filename", "image.jpg").strip()
            if not b64:
                self._json(400, {"error": "No image data"})
                return
            # Sanitize filename
            filename = re.sub(r'[^\w.\-]', '_', filename)
            # Ensure unique
            img_dir = user_images_path(uid)
            dest = os.path.join(img_dir, filename)
            counter = 1
            base, ext = os.path.splitext(filename)
            while os.path.exists(dest):
                filename = f"{base}_{counter}{ext}"
                dest = os.path.join(img_dir, filename)
                counter += 1
            try:
                img_bytes = base64.b64decode(b64)
                with open(dest, "wb") as f:
                    f.write(img_bytes)
            except Exception as e:
                self._json(500, {"error": f"Failed to save image: {e}"})
                return
            u.setdefault("images", []).append(filename)
            save_data(d)
            self._json(201, {"filename": filename, "total": len(u["images"])})
            return

        # ── Change admin password ──
        if path == "/api/admin/password":
            s = get_session(self.headers, "admin")
            if not s:
                self._json(401, {"error": "Unauthorized"})
                return
            try:
                body = self._read_json()
            except:
                self._json(400, {"error": "Invalid JSON"})
                return
            new_pw = body.get("password", "").strip()
            if not new_pw:
                self._json(400, {"error": "Password required"})
                return
            d = load_data()
            d["admin_password"] = new_pw
            save_data(d)
            self._json(200, {"ok": True})
            return

        # ── Existing: /grab ──
        if path == "/grab":
            self._handle_grab()
            return

        # ── Existing: /upload-photo ──
        if path == "/upload-photo":
            self._handle_upload_photo()
            return

        # ── Existing: /run ──
        if path == "/run":
            self._handle_run()
            return

        self._text(404, "Not found")

    def do_PATCH(self):
        path = self.path.split("?")[0]

        # ── Update user (admin) ──
        if path.startswith("/api/users/") and path.count("/") == 3:
            s = get_session(self.headers, "admin")
            if not s:
                self._json(401, {"error": "Unauthorized"})
                return
            uid = path.split("/")[3]
            try:
                body = self._read_json()
            except:
                self._json(400, {"error": "Invalid JSON"})
                return
            d = load_data()
            u = d["users"].get(uid)
            if not u:
                self._json(404, {"error": "User not found"})
                return
            if "active" in body:
                u["active"] = bool(body["active"])
            if "password" in body and body["password"].strip():
                u["password"] = body["password"].strip()
            if "name" in body and body["name"].strip():
                u["name"] = body["name"].strip()
            save_data(d)
            self._json(200, {"ok": True})
            return

        self._text(404, "Not found")

    def do_DELETE(self):
        path = self.path.split("?")[0]

        # ── Delete user (admin) ──
        if path.startswith("/api/users/") and path.count("/") == 3:
            s = get_session(self.headers, "admin")
            if not s:
                self._json(401, {"error": "Unauthorized"})
                return
            uid = path.split("/")[3]
            d = load_data()
            if uid not in d["users"]:
                self._json(404, {"error": "User not found"})
                return
            del d["users"][uid]
            save_data(d)
            # Remove image folder
            img_dir = os.path.join(images_dir(), uid)
            if os.path.isdir(img_dir):
                shutil.rmtree(img_dir, ignore_errors=True)
            self._json(200, {"ok": True})
            return

        # ── Delete gmail from user (admin) ──
        if path.startswith("/api/users/") and "/gmails/" in path:
            s = get_session(self.headers, "admin")
            if not s:
                self._json(401, {"error": "Unauthorized"})
                return
            parts = path.split("/")
            uid = parts[3]
            try:
                idx = int(parts[5])
            except (IndexError, ValueError):
                self._json(400, {"error": "Invalid index"})
                return
            d = load_data()
            u = d["users"].get(uid)
            if not u:
                self._json(404, {"error": "User not found"})
                return
            gmails = u.get("gmails", [])
            if idx < 0 or idx >= len(gmails):
                self._json(404, {"error": "Gmail index out of range"})
                return
            gmails.pop(idx)
            save_data(d)
            self._json(200, {"ok": True, "total": len(gmails)})
            return

        # ── Delete image from user (admin) ──
        if path.startswith("/api/users/") and "/images/" in path:
            s = get_session(self.headers, "admin")
            if not s:
                self._json(401, {"error": "Unauthorized"})
                return
            parts = path.split("/")
            uid = parts[3]
            fname = "/".join(parts[5:])
            d = load_data()
            u = d["users"].get(uid)
            if not u:
                self._json(404, {"error": "User not found"})
                return
            if fname in u.get("images", []):
                u["images"].remove(fname)
                fpath = os.path.join(images_dir(), uid, fname)
                if os.path.exists(fpath):
                    os.remove(fpath)
                save_data(d)
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "Image not found"})
            return

        # ── Delete gmail from user (VA) ──
        if path.startswith("/api/va/gmails/"):
            s = get_session(self.headers, "va")
            if not s:
                self._json(401, {"error": "Unauthorized"})
                return
            try:
                idx = int(path.split("/")[4])
            except (IndexError, ValueError):
                self._json(400, {"error": "Invalid index"})
                return
            d = load_data()
            uid = s["username"]
            u = d["users"].get(uid)
            if not u:
                self._json(404, {"error": "User not found"})
                return
            gmails = u.get("gmails", [])
            if idx < 0 or idx >= len(gmails):
                self._json(404, {"error": "Gmail index out of range"})
                return
            gmails.pop(idx)
            save_data(d)
            self._json(200, {"ok": True, "total": len(gmails)})
            return

        # ── Delete image from user (VA) ──
        if path.startswith("/api/va/images/"):
            s = get_session(self.headers, "va")
            if not s:
                self._json(401, {"error": "Unauthorized"})
                return
            fname = path.split("/", 4)[4]
            d = load_data()
            uid = s["username"]
            u = d["users"].get(uid)
            if not u:
                self._json(404, {"error": "User not found"})
                return
            if fname in u.get("images", []):
                u["images"].remove(fname)
                fpath = os.path.join(images_dir(), uid, fname)
                if os.path.exists(fpath):
                    os.remove(fpath)
                save_data(d)
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "Image not found"})
            return

        self._text(404, "Not found")

    # ── Existing handlers (unchanged logic) ──

    def _handle_grab(self):
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length)
        try:
            data = json.loads(raw_body)
        except Exception as e:
            self._json(400, {"error": f"Invalid JSON body: {e}"})
            return

        device_id = data.get("device_id", "").strip()
        login_code = data.get("login_code", "").strip()

        ps_script = os.path.join(here(), "PURE GRABBER.ps1")

        print(f"\n  -> Running Grabber: {device_id} code '{login_code}'")
        try:
            project_dir = here()
            bat_path = os.path.join(project_dir, "run_grabber_temp.bat")
            out_path = os.path.join(project_dir, "run_grabber_out.txt")

            cmd_line = f'@echo off\npowershell -NoProfile -ExecutionPolicy Bypass -File "{ps_script}"'
            if device_id:
                cmd_line += f' -DeviceId "{device_id}"'
            if login_code:
                cmd_line += f' -LoginCode "{login_code}"'
            cmd_line += f' > "{out_path}" 2>&1\nexit /b %errorlevel%'

            with open(bat_path, "w", encoding="utf-8") as bf:
                bf.write(cmd_line)

            res = subprocess.run(["cmd.exe", "/c", bat_path], cwd=project_dir, timeout=60, stdin=subprocess.DEVNULL)

            out_content = ""
            if os.path.exists(out_path):
                with open(out_path, "r", encoding="utf-8", errors="replace") as outf:
                    out_content = outf.read()

            try: os.remove(bat_path)
            except: pass
            try: os.remove(out_path)
            except: pass

            self._json(200, {"ok": res.returncode == 0, "body": out_content, "stderr": ""})
        except subprocess.TimeoutExpired:
            self._json(504, {"error": "Grabber timed out after 60 seconds"})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _handle_upload_photo(self):
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length)
        try:
            data = json.loads(raw_body)
        except Exception as e:
            self._json(400, {"error": f"Invalid JSON body: {e}"})
            return

        photo_b64 = data.get("photo_base64", "")
        url       = data.get("url", "").strip()
        headers   = data.get("headers", {})
        proxy     = data.get("proxy", "").strip()
        filename  = data.get("filename", "photo.jpg")

        if not photo_b64 or not url:
            self._json(400, {"error": "photo_base64 and url are required"})
            return

        tmp_path = None
        try:
            photo_bytes = base64.b64decode(photo_b64)
            ext = os.path.splitext(filename)[1] or ".jpg"
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext, dir=here())
            with os.fdopen(tmp_fd, "wb") as f:
                f.write(photo_bytes)

            print(f"\n  -> UPLOAD PHOTO to {url}  ({len(photo_bytes)} bytes)")
            if proxy:
                print(f"     proxy: {proxy}")

            cmd = [
                "curl", "--request", "POST", "--url", url,
                "--compressed", "--silent", "--show-error", "--include",
                "--max-time", "60",
                "--form", f"file=@{tmp_path};type=image/jpeg",
            ]
            for name, value in headers.items():
                if name.lower() == "content-type":
                    continue
                cmd += ["--header", f"{name}: {value}"]

            if proxy:
                proxy_lower = proxy.lower()
                if proxy_lower.startswith("socks5h://"):
                    cmd += ["--socks5-hostname", proxy[10:]]
                elif proxy_lower.startswith("socks5://"):
                    cmd += ["--socks5-hostname", proxy[9:]]
                elif proxy_lower.startswith("socks4a://"):
                    cmd += ["--socks4a", proxy[10:]]
                elif proxy_lower.startswith("socks4://"):
                    cmd += ["--socks4", proxy[9:]]
                else:
                    cmd += ["--proxy", proxy]

            result = subprocess.run(cmd, capture_output=True, timeout=65)
            stdout = result.stdout.decode('utf-8', errors='replace')

            if "\r\n\r\n" in stdout:
                head_part, _, body_part = stdout.partition("\r\n\r\n")
            elif "\n\n" in stdout:
                head_part, _, body_part = stdout.partition("\n\n")
            else:
                head_part, body_part = "", stdout

            status_code = 0
            status_text = ""
            resp_headers = {}
            lines = head_part.splitlines()
            if lines:
                parts = lines[0].split(" ", 2)
                if len(parts) >= 2:
                    try: status_code = int(parts[1])
                    except ValueError: pass
                    status_text = parts[2] if len(parts) > 2 else ""
                for line in lines[1:]:
                    if ":" in line:
                        k, _, v = line.partition(":")
                        resp_headers[k.strip()] = v.strip()

            if status_code == 100 and "\r\n\r\n" in body_part:
                head2, _, body_part = body_part.partition("\r\n\r\n")
                lines2 = head2.splitlines()
                if lines2:
                    parts2 = lines2[0].split(" ", 2)
                    if len(parts2) >= 2:
                        try: status_code = int(parts2[1])
                        except: pass
                        status_text = parts2[2] if len(parts2) > 2 else ""

            try:
                parsed_body = json.loads(body_part)
                body_out = json.dumps(parsed_body, indent=2)
            except:
                body_out = body_part.strip()

            stderr_out = result.stderr.decode('utf-8', errors='replace').strip() if result.stderr else ""
            if stderr_out:
                print(f"     stderr: {stderr_out}")

            self._json(200, {
                "ok": result.returncode == 0, "status_code": status_code,
                "status_text": status_text, "headers": resp_headers,
                "body": body_out, "stderr": stderr_out, "returncode": result.returncode,
            })
        except subprocess.TimeoutExpired:
            self._json(504, {"error": "Photo upload timed out after 60 seconds"})
        except Exception as e:
            self._json(500, {"error": str(e)})
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except: pass

    def _handle_run(self):
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length)
        try:
            data = json.loads(raw_body)
        except Exception as e:
            self._json(400, {"error": f"Invalid JSON body: {e}"})
            return

        method  = data.get("method", "GET").upper()
        url     = data.get("url", "").strip()
        headers = data.get("headers", {})
        proxy   = data.get("proxy", "").strip()
        payload = data.get("body", None)

        if not url:
            self._json(400, {"error": "url is required"})
            return

        cmd = [
            "curl", "--request", method, "--url", url,
            "--compressed", "--silent", "--show-error", "--include",
            "--max-time", "30",
        ]
        for name, value in headers.items():
            cmd += ["--header", f"{name}: {value}"]

        if proxy:
            proxy_lower = proxy.lower()
            if proxy_lower.startswith("socks5h://"):
                cmd += ["--socks5-hostname", proxy[10:]]
            elif proxy_lower.startswith("socks5://"):
                cmd += ["--socks5-hostname", proxy[9:]]
            elif proxy_lower.startswith("socks4a://"):
                cmd += ["--socks4a", proxy[10:]]
            elif proxy_lower.startswith("socks4://"):
                cmd += ["--socks4", proxy[9:]]
            else:
                cmd += ["--proxy", proxy]

        if payload:
            cmd += ["--data-binary", "@-"]

        print(f"\n  -> {method} {url}")
        if proxy:
            print(f"     proxy: {proxy}")

        try:
            if payload:
                result = subprocess.run(cmd, capture_output=True, input=payload.encode('utf-8'), timeout=35)
            else:
                result = subprocess.run(cmd, capture_output=True, timeout=35)
            
            stdout = result.stdout.decode('utf-8', errors='replace')
            stderr_out = result.stderr.decode('utf-8', errors='replace').strip() if result.stderr else ""

            if "\r\n\r\n" in stdout:
                head_part, _, body_part = stdout.partition("\r\n\r\n")
            elif "\n\n" in stdout:
                head_part, _, body_part = stdout.partition("\n\n")
            else:
                head_part, body_part = "", stdout

            status_code = 0
            status_text = ""
            resp_headers = {}
            lines = head_part.splitlines()
            if lines:
                parts = lines[0].split(" ", 2)
                if len(parts) >= 2:
                    try: status_code = int(parts[1])
                    except ValueError: pass
                    status_text = parts[2] if len(parts) > 2 else ""
                for line in lines[1:]:
                    if ":" in line:
                        k, _, v = line.partition(":")
                        resp_headers[k.strip()] = v.strip()

            try:
                parsed_body = json.loads(body_part)
                body_out = json.dumps(parsed_body, indent=2)
            except:
                body_out = body_part.strip()

            if stderr_out:
                print(f"     stderr: {stderr_out}")

            self._json(200, {
                "ok": result.returncode == 0, "status_code": status_code,
                "status_text": status_text, "headers": resp_headers,
                "body": body_out, "stderr": stderr_out, "returncode": result.returncode,
            })
        except subprocess.TimeoutExpired:
            self._json(504, {"error": "Request timed out after 30 seconds"})
        except FileNotFoundError:
            self._json(500, {"error": "curl not found — please install curl and make sure it is in your PATH"})
        except Exception as e:
            self._json(500, {"error": str(e)})


if __name__ == "__main__":
    os.makedirs(images_dir(), exist_ok=True)
    load_data()

    print("\n  Pure API Panel + VA Management Server")
    print("  ──────────────────────────────────────")
    print(f"  API Panel (VA login):  http://127.0.0.1:{PORT}/")
    print(f"  Admin Panel:           http://127.0.0.1:{PORT}/admin")
    print(f"  Default admin password: admin")
    print(f"\n  Press Ctrl+C to stop.\n")

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        sys.exit(0)
