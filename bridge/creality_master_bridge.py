# -*- coding: utf-8 -*-
import json, time, requests, threading, os, sys, uuid, random, socket, subprocess, traceback, re, io, gzip, shutil, tempfile
import paho.mqtt.client as mqtt

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_CACHE_PATH = os.path.join(BASE_DIR, "state_cache.json")

with open(CONFIG_PATH, "r") as f:
    CONFIG = json.load(f)

TB_TOKEN = CONFIG.get("tb_token", "")
JWT_TOKEN = CONFIG.get("jwt_token", "")
BROKER = CONFIG.get("broker", "mqtt.crealitycloud.com")
MOONRAKER = CONFIG.get("moonraker_url", "http://127.0.0.1:7125").rstrip("/")
PRINTER_NAME = CONFIG.get("printer_name", "Larry")
PRINTER_MODEL = CONFIG.get("printer_model", "Ender-3 S1 Pro")
VIDEO_ENABLED = bool(CONFIG.get("video_enabled", False))
VIDEO_RELAY_URL = CONFIG.get("video_relay_url", "http://127.0.0.1:8555/control")
VIDEO_RELAY_SECRET = CONFIG.get("video_relay_secret", "")
# The cloud app's "LED" button. Empty = do nothing (it used to drive the part fan).
# Examples: "SET_LED LED=chamber WHITE=1" / "SET_PIN PIN=caselight VALUE=1"
LED_ON_GCODE = CONFIG.get("led_on_gcode", "")
LED_OFF_GCODE = CONFIG.get("led_off_gcode", "")
# Seconds after complete/cancelled before the bridge resets Klipper's print state for the cloud UI.
AUTO_CLEAR_SECONDS = float(CONFIG.get("auto_clear_seconds", 8))
# Klipper "error" states are left alone by default so you can read them in Mainsail/Fluidd.
AUTO_CLEAR_ERRORS = bool(CONFIG.get("auto_clear_errors", False))
# Creality's broker only listens on plain 1883 today; these exist in case that changes.
MQTT_PORT = int(CONFIG.get("mqtt_port", 1883))
MQTT_TLS = bool(CONFIG.get("mqtt_tls", False))

# --- GLOBALS ---
CURRENT_PRINT_ID = ""
PRINT_START_TIME = 0
CLOUD_PRINT_URL = ""
IS_DOWNLOADING = False
DOWNLOAD_LOCK_TIME = 0
CURRENT_FAN_STATE = 0
CURRENT_LED_STATE = 0
ACTIVE_LIVESTREAM = 0
ACTIVE_JWT_TOKEN = JWT_TOKEN
LAST_LOGGED_STATE = None
LAST_ATTR_PAYLOAD = ""

def log(msg):
    print(msg, flush=True)

SECRET_KEYS = {"token", "jwtToken", "tbToken", "jwt_token", "tb_token", "accessToken"}

def mask(secret):
    s = str(secret or "")
    return f"<redacted ...{s[-4:]}>" if len(s) > 8 else "<redacted>"

def redact(obj):
    """Copy of obj with any credential-looking fields masked, safe to log."""
    if isinstance(obj, dict):
        return {k: (mask(v) if k in SECRET_KEYS else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str) and "://" in obj:
        return redact_url_in(obj)
    return obj

def save_config():
    """Write CONFIG atomically, readable only by the bridge's user."""
    tmp = CONFIG_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as cf:
        json.dump(CONFIG, cf, indent=4)
    os.replace(tmp, CONFIG_PATH)

def safe_url(url):
    """Download links are often pre-signed; never log the query string."""
    return str(url).split("?")[0]

def redact_url_in(text):
    """Strip query strings from any URLs inside free text (e.g. exception messages)."""
    # Covers full URLs and the bare "/path?query" form requests uses in its error messages.
    return re.sub(r"((?:https?://|/)[^\s?'\"]*)\?[^\s'\")]*", r"\1?<redacted>", str(text))

SAFE_OBJECT_NAME = re.compile(r"[\w.\-]+")  # use with fullmatch()

def as_temp(val):
    """Cloud temperature value -> number for M104/M140, or None if it isn't one."""
    try:
        t = float(val)
    except (TypeError, ValueError):
        return None
    return int(t) if t == int(t) else round(t, 1)

class StreamingMultipart:
    """multipart/form-data body that streams a file from disk instead of holding it in RAM."""

    def __init__(self, field, filename, path, fields=None):
        boundary = uuid.uuid4().hex
        self.content_type = f"multipart/form-data; boundary={boundary}"
        head = ""
        for k, v in (fields or {}).items():
            head += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
        head += (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
                 f'Content-Type: application/octet-stream\r\n\r\n')
        tail = f"\r\n--{boundary}--\r\n".encode()
        self._parts = [io.BytesIO(head.encode()), open(path, "rb"), io.BytesIO(tail)]
        self._len = len(head.encode()) + os.path.getsize(path) + len(tail)

    def __len__(self):
        return self._len

    def read(self, size=-1):
        out = b""
        while self._parts and (size < 0 or len(out) < size):
            chunk = self._parts[0].read(-1 if size < 0 else size - len(out))
            if chunk:
                out += chunk
            else:
                self._parts.pop(0).close()
        return out

    def __iter__(self):
        while True:
            chunk = self.read(1024 * 1024)
            if not chunk:
                return
            yield chunk

    def close(self):
        for p in self._parts:
            p.close()
        self._parts = []

_LAYER_META = {}

def get_layer_meta(filename):
    """Layer heights for a G-code file from Moonraker's metadata (cached per file)."""
    if not filename:
        return {}
    if filename in _LAYER_META:
        return _LAYER_META[filename]
    try:
        r = requests.get(f"{MOONRAKER}/server/files/metadata", params={"filename": filename}, timeout=2)
        if r.status_code != 200:
            return {}
        m = r.json().get("result", {})
    except Exception:
        return {}  # don't cache a failed lookup
    meta = {}
    try:
        lh = float(m.get("layer_height") or 0)
        if lh > 0:
            meta["layer_height"] = lh
            meta["first_layer_height"] = float(m.get("first_layer_height") or lh)
            if m.get("object_height"):
                meta["object_height"] = float(m["object_height"])
        if m.get("layer_count"):
            meta["layer_count"] = int(m["layer_count"])  # the slicer's own count wins
    except (TypeError, ValueError):
        meta = {}
    _LAYER_META[filename] = meta
    return meta

def layer_at_z(z, meta):
    lh = meta.get("layer_height", 0.2)
    first = meta.get("first_layer_height", lh)
    if z <= first + 0.01:
        return 1
    return int(round((z - first) / lh)) + 1

def load_state_cache():
    global CURRENT_PRINT_ID, PRINT_START_TIME, CLOUD_PRINT_URL
    try:
        if os.path.exists(STATE_CACHE_PATH):
            with open(STATE_CACHE_PATH, "r") as f:
                st = json.load(f)
                CURRENT_PRINT_ID = st.get("printId", "")
                PRINT_START_TIME = st.get("printStartTime", 0)
                CLOUD_PRINT_URL = st.get("cloudPrintUrl", "")
                if CURRENT_PRINT_ID:
                    log(f"[*] Restored active printId from cache: {CURRENT_PRINT_ID}")
    except Exception as e:
        log(f"[-] State cache load error: {e}")

def save_state_cache():
    try:
        with open(STATE_CACHE_PATH, "w") as f:
            json.dump({
                "printId": CURRENT_PRINT_ID,
                "printStartTime": PRINT_START_TIME,
                "cloudPrintUrl": CLOUD_PRINT_URL
            }, f)
    except Exception:
        pass

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def moonraker_post(endpoint, payload=None):
    try:
        return requests.post(f"{MOONRAKER}{endpoint}", json=payload or {}, timeout=5)
    except Exception:
        return None

def send_gcode(cmd):
    return moonraker_post("/printer/gcode/script", {"script": cmd})

def get_klipper_pos():
    try:
        res = requests.get(f"{MOONRAKER}/printer/objects/query?toolhead", timeout=2)
        pos = res.json()["result"]["status"]["toolhead"]["position"]
        return f"X:{pos[0]:.2f} Y:{pos[1]:.2f} Z:{pos[2]:.2f}"
    except Exception:
        return "X:0.00 Y:0.00 Z:0.00"

def get_exclude_objects():
    try:
        res = requests.get(f"{MOONRAKER}/printer/objects/query?exclude_object", timeout=2)
        if res.status_code == 200:
            eo = res.json()["result"]["status"].get("exclude_object", {})
            objs = eo.get("objects", [])
            excluded = eo.get("excluded_objects", [])
            return [{"name": o.get("name", ""), "excluded": o.get("name", "") in excluded} for o in objs]
    except Exception:
        pass
    return []

def sanitize_filename(p):
    if not p:
        return ""
    return p.lstrip("/").replace("gcodes/", "")

def get_clean_moonraker_files():
    try:
        res = requests.get(f"{MOONRAKER}/server/files/list?root=gcodes", timeout=5)
        if res.status_code == 200:
            files = res.json()["result"]
            files.sort(key=lambda x: x.get("modified", 0), reverse=True)
            return files
    except Exception:
        pass
    return []

def get_paged_files(page_idx, origin=0):
    clean_files = get_clean_moonraker_files()
    pages = [clean_files[i:i + 5] for i in range(0, len(clean_files), 5)]
    if not pages:
        return {"tf": origin, "fileinfo": "", "pageindex": 0}
    if page_idx < len(pages):
        selected = pages[page_idx]
        prefix = "/usb:" if origin == 1 else "/local:"
        fileinfo_str = ""
        for f in selected:
            safe_name = sanitize_filename(f.get("path", ""))
            fsize = int(f.get("size", 0))
            fdate = int(f.get("modified", 0))
            fileinfo_str += f"{prefix}{safe_name}:{fsize}:{fdate};"
        return {"tf": origin, "fileinfo": fileinfo_str, "pageindex": page_idx}
    return {"tf": origin, "fileinfo": "", "pageindex": page_idx}

def get_web_file_array(page_num_1based, origin=0):
    clean_files = get_clean_moonraker_files()
    page_size = 10
    pages = [clean_files[i:i + page_size] for i in range(0, len(clean_files), page_size)]
    idx = max(0, int(page_num_1based) - 1)
    if idx >= len(pages):
        return []
    path_prefix = "/usb" if origin == 1 else "/local"
    result = []
    for f in pages[idx]:
        safe_name = sanitize_filename(f.get("path", ""))
        fsize = int(f.get("size", 0))
        fdate = int(f.get("modified", 0))
        result.append({
            # The app sends "path" back verbatim as opGcodeFile when you press print,
            # so it must be the full path, not just the folder.
            "address": "", "path": f"{path_prefix}/{safe_name}", "name": safe_name, "fileName": safe_name,
            "size": fsize, "file_size": fsize, "time": fdate, "file_create_time": fdate,
            "thumbnail": "", "isSelect": False
        })
    return result

def import_cloud_device():
    """Refresh tb_token from the saved key. Returns False only if the cloud couldn't be reached."""
    global TB_TOKEN
    try:
        if not JWT_TOKEN:
            return True
        url = "https://api.crealitycloud.com/api/cxy/v2/device/user/importDevice"
        req_id = "Raspberry" + str(int(time.time()) % 10000) + "10" + str(int(random.random() * 89999 + 10000))
        headers = {
            "Content-Type": "application/json",
            "__CXY_JWTOKEN_": JWT_TOKEN,
            "__CXY_OS_VER_": "v0.0.1",
            "_CXY_OS_LANG_": "1",
            "__CXY_PLATFORM_": "5",
            "__CXY_DUID_": "234",
            "__CXY_APP_ID_": "creality_model",
            "__CXY_REQUESTID_": req_id
        }
        mac = uuid.UUID(int=uuid.getnode()).hex[-12:].upper()
        data = json.dumps({"mac": mac, "iotType": 1, "deviceName": PRINTER_MODEL})
        res = requests.post(url, data=data, headers=headers, timeout=5)
        res_json = res.json()
        log(f"[*] Cloud Import Response: HTTP {res.status_code}, code={res_json.get('code')} msg={res_json.get('msg')!r}")
        if res_json.get("code") == 0 and res_json.get("result"):
            new_tb = res_json["result"].get("tbToken")
            if new_tb:
                TB_TOKEN = new_tb
                log(f"[*] Acquired provisioned tbToken {mask(TB_TOKEN)}")
                try:
                    CONFIG["tb_token"] = new_tb
                    save_config()
                except Exception as se:
                    log(f"[-] Could not persist tbToken to config.json: {se}")
        elif res_json.get("code") != 0:
            log("[!] Cloud import was refused (the saved key is probably expired). The existing tb_token is still in use.")
            log(f"[!] If the printer shows offline, run: python3 {os.path.join(BASE_DIR, 'creality_login.py')} --config {CONFIG_PATH}")
        return True
    except requests.exceptions.RequestException as e:
        log(f"[-] Cloud import: cloud unreachable ({type(e).__name__}); will retry.")
        return False
    except Exception as e:
        log(f"[-] Cloud Import Exception: {e}")
        return True

def on_message(client, userdata, msg):
    global CURRENT_FAN_STATE, CURRENT_LED_STATE, ACTIVE_LIVESTREAM, ACTIVE_JWT_TOKEN
    global CURRENT_PRINT_ID, PRINT_START_TIME, CLOUD_PRINT_URL, IS_DOWNLOADING, DOWNLOAD_LOCK_TIME, LAST_ATTR_PAYLOAD
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        topic = msg.topic
        method = payload.get("method")
        params = payload.get("params", {})

        noisy_keys = {"pFileList", "uFileList", "printObjects"}
        if not (method == "get" and set(params.keys()).issubset(noisy_keys)):
            log(f"[<<< MQTT INCOMING <<<] Topic: {topic} | Payload: {json.dumps(redact(payload))}")

        if "v1/devices/me/rpc/request/" in topic:
            req_id = topic.split("/")[-1]
            rpc_reply = {}

            if method in ["set", "get"]:
                if "printId" in params and params["printId"]:
                    CURRENT_PRINT_ID = str(params["printId"])
                    PRINT_START_TIME = int(time.time())
                    IS_DOWNLOADING = True
                    DOWNLOAD_LOCK_TIME = time.time()
                    save_state_cache()
                    log(f"[+] Captured Cloud printId: {CURRENT_PRINT_ID} (Start: {PRINT_START_TIME})")
                    
                    LAST_ATTR_PAYLOAD = "" 
                    client.publish("v1/devices/me/attributes", json.dumps({
                        "printId": CURRENT_PRINT_ID,
                        "printStartTime": str(PRINT_START_TIME)
                    }))
                    rpc_reply["printId"] = CURRENT_PRINT_ID

                for key, val in params.items():
                    if key not in noisy_keys:
                        log(f"[EXECUTE] {key} -> {mask(val) if key in SECRET_KEYS else repr(redact(val))}")

                    if key == "printId":
                        continue

                    elif key == "printObjects":
                        objs = get_exclude_objects()
                        rpc_reply["printObjects"] = objs
                        rpc_reply["result"] = objs

                    elif key in ["excludeObjects", "excludeObject"]:
                        names = val if isinstance(val, list) else ([val] if isinstance(val, str) and val else [])
                        for obj_name in names:
                            obj_name = str(obj_name).strip()
                            if SAFE_OBJECT_NAME.fullmatch(obj_name):
                                send_gcode(f"EXCLUDE_OBJECT NAME={obj_name}")
                            else:
                                log(f"[-] Ignoring exclude request with unsafe object name: {obj_name!r}")
                        rpc_reply[key] = val

                    elif key == "pFileList":
                        f_arr = get_web_file_array(val, 0)
                        rpc_reply["pFileList"] = f_arr
                        rpc_reply["result"] = f_arr

                    elif key == "uFileList":
                        f_arr = get_web_file_array(val, 1)
                        rpc_reply["uFileList"] = f_arr
                        rpc_reply["result"] = f_arr

                    elif key == "reqGcodeFile":
                        try:
                            v = int(val)
                            f_info = get_paged_files(v & 0xFFFF, v >> 16)
                        except Exception:
                            f_info = {"tf": 0, "fileinfo": "", "pageindex": 0}
                        client.publish("v1/devices/me/attributes", json.dumps({"retGcodeFileInfo": f_info}))
                        rpc_reply.update({"retGcodeFileInfo": f_info, "result": f_info})

                    elif key == "setFeedratePct":
                        val_int = int(val)
                        send_gcode(f"M220 S{val_int}")
                        client.publish("v1/devices/me/telemetry", json.dumps({"curFeedratePct": val_int}))
                        rpc_reply.update({"setFeedratePct": val_int})

                    elif key == "setFlowratePct":
                        val_int = int(val)
                        send_gcode(f"M221 S{val_int}")
                        client.publish("v1/devices/me/telemetry", json.dumps({"curFlowratePct": val_int}))
                        rpc_reply.update({"setFlowratePct": val_int})

                    elif key == "opGcodeFile":
                        val_str = str(val)
                        fname = ""
                        for prefix in ("/local/", "/usb/"):
                            if prefix in val_str:
                                fname = val_str.split(prefix, 1)[-1].strip()
                                break
                        is_file_cmd = val_str.startswith(("/local", "/usb"))
                        if is_file_cmd and not fname:
                            # Never guess: an empty name used to start the newest file on disk.
                            log(f"[-] Ignoring opGcodeFile with no filename: {val_str!r}")
                            rpc_reply.update({"opGcodeFile": val_str, "error": "no filename"})
                            continue
                        if fname:
                            if not CURRENT_PRINT_ID:
                                CURRENT_PRINT_ID = str(uuid.uuid4()).replace("-", "")[:24]
                            PRINT_START_TIME = int(time.time())
                            IS_DOWNLOADING = True
                            DOWNLOAD_LOCK_TIME = time.time()
                            save_state_cache()
                            
                            # Force a state clear before starting to ensure Moonraker accepts the command
                            send_gcode("SDCARD_RESET_FILE")
                            time.sleep(0.5)
                            
                            moonraker_post("/printer/print/start", {"filename": fname})
                        LAST_ATTR_PAYLOAD = ""
                        client.publish("v1/devices/me/attributes", json.dumps({"opGcodeFile": val_str}))
                        rpc_reply.update({"opGcodeFile": val_str})

                    elif key == "print" and str(val).startswith("http"):
                        CLOUD_PRINT_URL = str(val)
                        IS_DOWNLOADING = True
                        DOWNLOAD_LOCK_TIME = time.time()
                        if not PRINT_START_TIME:
                            PRINT_START_TIME = int(time.time())
                        save_state_cache()
                        LAST_ATTR_PAYLOAD = ""
                        client.publish("v1/devices/me/attributes", json.dumps({"print": CLOUD_PRINT_URL}))
                        client.publish("v1/devices/me/telemetry", json.dumps({"dProgress": 0}))

                        def download_and_print(url):
                            global IS_DOWNLOADING, CURRENT_PRINT_ID
                            # Stream to disk next to the bridge (not /tmp, which can be RAM-backed)
                            # so large prints don't have to fit in memory on 1 GB boards.
                            tmp_files = []
                            body = None
                            try:
                                log(f"[*] Downloading Cloud G-code: {safe_url(url)}")
                                fd, raw_path = tempfile.mkstemp(prefix=".cc_dl_", dir=BASE_DIR)
                                tmp_files.append(raw_path)
                                with os.fdopen(fd, "wb") as out, requests.get(url, stream=True, timeout=60) as r:
                                    r.raise_for_status()
                                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                                        out.write(chunk)
                                client.publish("v1/devices/me/telemetry", json.dumps({"dProgress": 50}))

                                gcode_path = raw_path
                                with open(raw_path, "rb") as f:
                                    is_gzip = f.read(2) == b"\x1f\x8b"
                                if is_gzip:
                                    fd, gcode_path = tempfile.mkstemp(prefix=".cc_dl_", dir=BASE_DIR)
                                    tmp_files.append(gcode_path)
                                    with gzip.open(raw_path, "rb") as src, os.fdopen(fd, "wb") as dst:
                                        shutil.copyfileobj(src, dst, 1024 * 1024)
                                    os.remove(raw_path)
                                    tmp_files.remove(raw_path)

                                with open(gcode_path, "rb") as f:
                                    text_data = f.read(32768).decode('utf-8', errors='ignore')
                                real_name = ""

                                match = re.search(r';\s*model_name\s*[:=]\s*([^\r\n]+)', text_data, re.IGNORECASE)
                                if match:
                                    real_name = match.group(1).strip().replace(" ", "_")

                                if not real_name:
                                    exc_match = re.search(r'EXCLUDE_OBJECT_DEFINE NAME=([^\s\.]+)', text_data, re.IGNORECASE)
                                    if exc_match:
                                        real_name = exc_match.group(1).strip()

                                if not real_name:
                                    obj_match = re.search(r';\s*printing object\s+([^\r\n]+)', text_data, re.IGNORECASE)
                                    if obj_match:
                                        real_name = obj_match.group(1).split("id:")[0].replace(".stl", "").replace(".STL", "").strip().replace(" ", "_")

                                if real_name and not real_name.lower().endswith(".gcode"):
                                    real_name += ".gcode"

                                if not real_name:
                                    base_name = safe_url(url).split("/")[-1].replace(".gz", "")
                                    real_name = "CC_" + base_name
                                    if not real_name.endswith(".gcode"):
                                        real_name += ".gcode"

                                # The name comes from inside the file: keep it a plain filename.
                                real_name = re.sub(r"[^\w.\-]", "_", real_name).lstrip(".") or "CC_print.gcode"

                                log(f"[+] Uploading '{real_name}' to Moonraker & starting print (printId={CURRENT_PRINT_ID})...")
                                client.publish("v1/devices/me/telemetry", json.dumps({"dProgress": 100}))
                                
                                # Force clear leftover complete/cancel states before starting
                                send_gcode("SDCARD_RESET_FILE")
                                time.sleep(0.5)
                                
                                body = StreamingMultipart("file", real_name, gcode_path, {"print": "true"})
                                up = requests.post(
                                    f"{MOONRAKER}/server/files/upload",
                                    data=body,
                                    headers={"Content-Type": body.content_type},
                                    timeout=120
                                )
                                up.raise_for_status()
                            except Exception as de:
                                log(f"[-] Download/Upload Error: {redact_url_in(str(de))}")
                                IS_DOWNLOADING = False
                            finally:
                                if body is not None:
                                    body.close()
                                for p in tmp_files:
                                    try:
                                        os.remove(p)
                                    except OSError:
                                        pass
                        threading.Thread(target=download_and_print, args=(str(val),)).start()

                    elif key == "pause":
                        if int(val) == 1:
                            moonraker_post("/printer/print/pause")
                        else:
                            moonraker_post("/printer/print/resume")
                        LAST_ATTR_PAYLOAD = ""

                    elif key == "stop":
                        if int(val) == 1:
                            moonraker_post("/printer/print/cancel")
                        LAST_ATTR_PAYLOAD = ""
                        client.publish("v1/devices/me/attributes", json.dumps({
                            "stop": 1, "state": 4, 
                            "printId": CURRENT_PRINT_ID,
                            "print": "", "filename": "", "printFileName": ""
                        }))

                    elif key in ["nozzleTemp", "nozzleTemp2", "bedTemp", "bedTemp2"]:
                        temp = as_temp(val)
                        if temp is None:
                            log(f"[-] Ignoring non-numeric {key}: {val!r}")
                        else:
                            send_gcode(f"{'M104' if key.startswith('nozzle') else 'M140'} S{temp}")
                        LAST_ATTR_PAYLOAD = ""

                    elif key == "fan":
                        CURRENT_FAN_STATE = int(val)
                        speed = int((CURRENT_FAN_STATE / 100.0) * 255) if CURRENT_FAN_STATE > 1 else (255 if CURRENT_FAN_STATE == 1 else 0)
                        send_gcode(f"M106 S{speed}")
                        LAST_ATTR_PAYLOAD = ""

                    elif key == "led":
                        CURRENT_LED_STATE = int(val)
                        LAST_ATTR_PAYLOAD = ""
                        led_cmd = LED_ON_GCODE if CURRENT_LED_STATE == 1 else LED_OFF_GCODE
                        if led_cmd:
                            send_gcode(led_cmd)
                        else:
                            log("[*] LED toggled in the app, but led_on_gcode/led_off_gcode are not set in config.json.")

                    elif key == "autohome":
                        val_str = str(val).lower()
                        if val_str == "z":
                            send_gcode("G28 Z\nG90")
                        elif val_str in ["xy", "x", "y", "1", "true"]:
                            send_gcode("G28 X Y\nG90")
                        else:
                            send_gcode("G28\nG90")
                        pos = get_klipper_pos()
                        LAST_ATTR_PAYLOAD = ""
                        client.publish("v1/devices/me/attributes", json.dumps({"curPosition": pos, "autohome": 1}))
                        rpc_reply.update({"curPosition": pos, "autohome": 1})

                    elif key == "gcodeCmd":
                        send_gcode(val)

                    elif key == "token":
                        ACTIVE_JWT_TOKEN = val
                        if 'livestream' in params:
                            try:
                                hdrs = {"X-Relay-Secret": VIDEO_RELAY_SECRET} if VIDEO_RELAY_SECRET else {}
                                requests.post(VIDEO_RELAY_URL, json={"token": val, "livestream": int(params.get("livestream", 1))},
                                              headers=hdrs, timeout=3)
                            except Exception:
                                pass

                    elif key == "livestream":
                        ACTIVE_LIVESTREAM = int(val)
                        LAST_ATTR_PAYLOAD = ""

            if "token" not in params and "jwtToken" not in params:
                client.publish(f"v1/devices/me/rpc/response/{req_id}", json.dumps(rpc_reply))
    except Exception as e:
        log(f"[-] RPC Error: {e}")

def main():
    global CURRENT_PRINT_ID, PRINT_START_TIME, CLOUD_PRINT_URL, IS_DOWNLOADING, DOWNLOAD_LOCK_TIME, LAST_LOGGED_STATE, LAST_ATTR_PAYLOAD
    load_state_cache()
    imported = import_cloud_device()
    if TB_TOKEN.startswith("CC_") and not JWT_TOKEN:
        log("[!] This bridge has not been signed in to Creality Cloud yet, so the cloud will refuse it. Run:")
        log(f"[!]   python3 {os.path.join(BASE_DIR, 'creality_login.py')} --config {CONFIG_PATH}")
    try:
        init_res = requests.get(f"{MOONRAKER}/printer/objects/query?print_stats", timeout=2)
        if init_res.status_code == 200:
            k_state = init_res.json()["result"]["status"]["print_stats"].get("state", "")
            if k_state not in ["printing", "paused"]:
                CURRENT_PRINT_ID = ""
                PRINT_START_TIME = 0
                CLOUD_PRINT_URL = ""
                save_state_cache()
                log("[*] Printer is not actively printing. Cleared stale state cache.")
    except Exception:
        pass

    log(f"[*] Initializing Creality Klipper Bridge ({PRINTER_NAME} as {PRINTER_MODEL})...")

    try:
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        client = mqtt.Client()

    def on_connect(client, userdata, flags, reason_code, *rest):
        # Subscribe here (not once at startup) so the subscription survives paho's auto-reconnects.
        if str(reason_code) in ("0", "Success"):
            client.subscribe("v1/devices/me/rpc/request/+")
        else:
            log(f"[-] MQTT broker refused the connection: {reason_code}")

    client.on_message = on_message
    client.on_connect = on_connect
    client.reconnect_delay_set(min_delay=2, max_delay=300)
    if MQTT_TLS:
        client.tls_set()

    # Retry here with backoff instead of exiting, so being offline doesn't
    # make systemd restart us (and re-hit importDevice) every 3 seconds.
    delay = 5
    while True:
        if not imported:
            imported = import_cloud_device()
        client.username_pw_set(TB_TOKEN)
        try:
            client.connect(BROKER, MQTT_PORT, 60)
            break
        except Exception as e:
            log(f"[-] MQTT connection failed ({e}); retrying in {delay}s")
            time.sleep(delay)
            delay = min(delay * 2, 300)
    client.loop_start()

    completion_timer = 0

    while True:
        try:
            res = requests.get(f"{MOONRAKER}/printer/objects/query?print_stats&display_status&toolhead&heater_bed&extruder&gcode_move", timeout=2)
            
            if res.status_code == 200:
                data = res.json()["result"]["status"]

                p_stats = data.get("print_stats", {})
                d_stats = data.get("display_status", {})
                extruder = data.get("extruder", {})
                heater_bed = data.get("heater_bed", {})
                toolhead = data.get("toolhead", {})
                gcode_move = data.get("gcode_move", {})

                klipper_state = p_stats.get("state", "")
                safe_file = p_stats.get("filename", "")
                is_active_print = (klipper_state == "printing")

                # --- Expanded Auto-Clear Timer Logic ---
                clear_states = ["complete", "cancelled"] + (["error"] if AUTO_CLEAR_ERRORS else [])
                if AUTO_CLEAR_SECONDS >= 0 and klipper_state in clear_states:
                    if completion_timer == 0:
                        completion_timer = time.time()
                    elif time.time() - completion_timer > AUTO_CLEAR_SECONDS:
                        log(f"[*] Auto-clearing Klipper '{klipper_state}' state to release Cloud UI...")
                        send_gcode("SDCARD_RESET_FILE")
                        send_gcode("CLEAR_PAUSE")
                        completion_timer = 0
                else:
                    completion_timer = 0

                if is_active_print and IS_DOWNLOADING:
                    IS_DOWNLOADING = False
                elif IS_DOWNLOADING and (time.time() - DOWNLOAD_LOCK_TIME > 45):
                    IS_DOWNLOADING = False

                state_code = 0
                if klipper_state == "printing": state_code = 1
                elif klipper_state == "complete": state_code = 2
                elif klipper_state == "error": state_code = 3
                elif klipper_state == "cancelled": state_code = 4
                elif klipper_state == "paused": state_code = 5

                if state_code not in [1, 2, 5]:
                    safe_file = ""

                if is_active_print and not CURRENT_PRINT_ID:
                    CURRENT_PRINT_ID = str(uuid.uuid4()).replace("-", "")[:24]
                    PRINT_START_TIME = int(time.time())
                    save_state_cache()
                    log(f"[*] Generated local UUID printId: {CURRENT_PRINT_ID}")

                noz_temp = round(float(extruder.get("temperature", 0.0) or 0.0), 1)
                bed_temp = round(float(heater_bed.get("temperature", 0.0) or 0.0), 1)
                target_noz = round(float(extruder.get("target", 0.0) or 0.0), 1)
                target_bed = round(float(heater_bed.get("target", 0.0) or 0.0), 1)
                
                sf = gcode_move.get("speed_factor")
                current_speed_pct = int(float(sf) * 100) if sf is not None else 100
                ef = gcode_move.get("extrude_factor")
                current_flow_pct = int(float(ef) * 100) if ef is not None else 100

                fan_pct = 0
                try:
                    fan_res = requests.get(f"{MOONRAKER}/printer/objects/query?fan", timeout=1)
                    if fan_res.status_code == 200:
                        f_spd = fan_res.json()["result"]["status"].get("fan", {}).get("speed")
                        fan_pct = int(float(f_spd) * 100) if f_spd is not None else 0
                except Exception:
                    pass

                is_homed = 1 if "xyz" in toolhead.get("homed_axes", "") else 0

                progress = float(d_stats.get("progress", 0.0) or 0.0)
                if state_code == 2:
                    progress = 1.0

                reported_progress = int(progress * 100) if state_code in [1, 2, 5] else 0

                pd = p_stats.get("print_duration")
                print_dur = float(pd) if pd is not None else 0.0
                time_left = 0
                if is_active_print and progress > 0.01:
                    time_left = int((print_dur / progress) - print_dur)

                layer_info = p_stats.get("info") or {}
                cl_val = layer_info.get("current_layer")
                raw_layer = int(cl_val) if cl_val is not None else -1
                # Real layer heights from the file's metadata, when Moonraker has them.
                layer_meta = get_layer_meta(p_stats.get("filename", "")) if state_code in [1, 2, 5] else {}

                if raw_layer >= 1:
                    current_layer = raw_layer
                else:
                    z_pos = 0.0
                    if "position" in toolhead and len(toolhead["position"]) >= 3:
                        z_pos = float(toolhead["position"][2])
                    current_layer = layer_at_z(z_pos, layer_meta) if z_pos > 0.05 else 1

                total = layer_info.get("total_layer") or layer_meta.get("layer_count")
                if total:
                    # Z-based guesses run high while probing or after the end-of-print lift.
                    current_layer = min(current_layer, int(total))
                    if raw_layer < 1 and state_code in [1, 5]:
                        # ...so also keep them in step with how far through the file we are.
                        current_layer = max(1, min(current_layer, int(progress * int(total)) + 1))

                if state_code not in [1, 2, 5]:
                    current_layer = 0  # idle: Z height says nothing about layers
                elif state_code == 2:
                    if total:
                        current_layer = int(total)
                    elif layer_meta.get("object_height"):
                        current_layer = layer_at_z(layer_meta["object_height"], layer_meta)
                    elif current_layer <= 1:
                        current_layer = 10  # last resort when nothing reports layers; the cloud UI wants >1

                filament_used_m = int(float(p_stats.get("filament_used", 0.0) or 0.0) / 1000.0)
                cur_pos_str = get_klipper_pos()

                if state_code != LAST_LOGGED_STATE:
                    log(f"[STATE CHANGE] Klipper='{klipper_state}' -> CloudState={state_code} | Progress={reported_progress}% | Layer={current_layer} | printId='{CURRENT_PRINT_ID}'")
                    LAST_LOGGED_STATE = state_code

                attr_payload = {
                    "connect": 1,
                    "state": state_code,
                    "mcu_is_print": 1 if is_active_print else 0,
                    "filename": safe_file,
                    "print": safe_file if state_code == 2 else (CLOUD_PRINT_URL if (CLOUD_PRINT_URL and state_code in [1, 5]) else safe_file),
                    "printFileName": safe_file,
                    "printId": CURRENT_PRINT_ID,
                    "printStartTime": str(PRINT_START_TIME) if PRINT_START_TIME else "",
                    "printProgress": reported_progress,
                    "dProgress": 100 if state_code in [1, 2, 5] else 0,
                    "setFeedratePct": current_speed_pct,
                    "feedratePct": current_speed_pct,
                    "curFeedratePct": current_speed_pct,
                    "curFlowratePct": current_flow_pct,
                    "printSpeed": current_speed_pct,
                    "print_speed": current_speed_pct,
                    "fan": fan_pct,
                    "fanSpeed": fan_pct,
                    "modelFanPct": fan_pct,
                    "layer": current_layer,
                    "autohome": is_homed,
                    "nozzleTemp": noz_temp,      
                    "bedTemp": bed_temp,         
                    "nozzleTemp2": target_noz,
                    "bedTemp2": target_bed,
                    "curPosition": cur_pos_str,
                    "model": PRINTER_MODEL,
                    "machineType": PRINTER_MODEL,
                    "deviceName": PRINTER_NAME,
                    "led_state": CURRENT_LED_STATE,
                    "video": 1 if VIDEO_ENABLED else 0
                }
                
                attr_json = json.dumps(attr_payload)
                if attr_json != LAST_ATTR_PAYLOAD:
                    client.publish("v1/devices/me/attributes", attr_json)
                    LAST_ATTR_PAYLOAD = attr_json

                client.publish("v1/devices/me/telemetry", json.dumps({
                    "nozzleTemp": noz_temp,
                    "bedTemp": bed_temp,
                    "printJobTime": int(print_dur),
                    "printLeftTime": time_left,
                    "printProgress": reported_progress,
                    "dProgress": 100 if state_code in [1, 2, 5] else 0,
                    "printId": CURRENT_PRINT_ID,
                    "curFeedratePct": current_speed_pct,
                    "curFlowratePct": current_flow_pct,
                    "printSpeed": current_speed_pct,
                    "feedratePct": current_speed_pct,
                    "fan": fan_pct,
                    "fanSpeed": fan_pct,
                    "layer": current_layer,
                    "curPosition": cur_pos_str,
                    "FilamentLen": filament_used_m,
                    "ConsumablesLen": filament_used_m
                }))

                if not IS_DOWNLOADING and klipper_state in ["standby", "ready"] and CURRENT_PRINT_ID:
                    log(f"[*] Printer returned to standby. Clearing printId: {CURRENT_PRINT_ID}")
                    CURRENT_PRINT_ID = ""
                    PRINT_START_TIME = 0
                    CLOUD_PRINT_URL = ""
                    save_state_cache()

            else:
                client.publish("v1/devices/me/attributes", json.dumps({
                    "state": 0, "mcu_is_print": 0, "filename": "", "print": ""
                }))

        except Exception as e:
            log(f"[-] Loop Crash: {e}\n{traceback.format_exc()}")

        time.sleep(2)

if __name__ == "__main__":
    main()