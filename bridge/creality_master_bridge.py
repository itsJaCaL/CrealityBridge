# -*- coding: utf-8 -*-
import json, time, requests, threading, os, gzip, sys, re, socket
import paho.mqtt.client as mqtt

# Load user configuration from config.json
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
with open(CONFIG_PATH, "r") as f:
    CONFIG = json.load(f)

TB_TOKEN = CONFIG.get("tb_token", "")
BROKER = CONFIG.get("broker", "mqtt.crealitycloud.com")
MOONRAKER = CONFIG.get("moonraker_url", "http://127.0.0.1:7125").rstrip("/")
PRINTER_NAME = CONFIG.get("printer_name", "Klipper-Printer")
PRINTER_MODEL = CONFIG.get("printer_model", "K1 Max")
VIDEO_ENABLED = CONFIG.get("video_enabled", False)
VIDEO_RELAY_URL = CONFIG.get("video_relay_url", "http://127.0.0.1:8555/control")

CURRENT_PRINT_ID = ""
LAST_STATE = ""
CURRENT_FAN_STATE = 0
CURRENT_LED_STATE = 0
COMPLETION_BROADCAST_TIME = 0
PRINT_START_TIMESTAMP = 0
IS_DOWNLOADING = False
DOWNLOAD_PROGRESS = 100
FILENAME_MAP = {}

ACTIVE_TOKEN = ""
ACTIVE_JWT_TOKEN = ""
ACTIVE_LIVESTREAM = 0

def log(msg):
    print(msg, flush=True)

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
        url = f"{MOONRAKER}{endpoint}"
        res = requests.post(url, json=payload if payload else {}, timeout=10)
        if res.status_code == 200:
            error = res.json().get("error")
            if error:
                log(f"[Klipper Notice] {error.get('message', 'Command ignored by Klipper')}")
        return res
    except Exception:
        return None

def send_gcode(cmd):
    return moonraker_post("/printer/gcode/script", {"script": cmd})

def klipper_console_echo(msg):
    safe_msg = str(msg).replace('"', "'")
    send_gcode(f'M118 [Creality Cloud] {safe_msg}')

def get_klipper_pos():
    try:
        res = requests.get(f"{MOONRAKER}/printer/objects/query?toolhead", timeout=3)
        if res.status_code == 200:
            pos = res.json().get("result", {}).get("status", {}).get("toolhead", {}).get("position", [0.0, 0.0, 0.0])
            return f"X:{pos[0]:.2f} Y:{pos[1]:.2f} Z:{pos[2]:.2f}"
    except Exception:
        pass
    return "X:0.00 Y:0.00 Z:0.00"

def get_host_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return round(float(f.read().strip()) / 1000.0, 1)
    except Exception:
        return 0.0

def sanitize_filename(raw_name):
    no_spaces = raw_name.replace(" ", "_").replace("/", "_")
    cleaned = re.sub(r"[^a-zA-Z0-9_\-.]", "", no_spaces)
    FILENAME_MAP[cleaned] = raw_name
    return cleaned

def resolve_filename(cloud_name):
    return FILENAME_MAP.get(cloud_name, cloud_name)

def extract_real_gcode_name(filepath, rpc_params, fallback_name):
    # 1. Check if Creality sent the human-readable name in the MQTT RPC payload
    for key in ("fileName", "filename", "name", "modelName", "title"):
        val = rpc_params.get(key)
        if val and isinstance(val, str) and not val.startswith("http"):
            clean = re.sub(r"[^a-zA-Z0-9_\-.]", "_", val.strip())
            if not clean.lower().endswith(".gcode"):
                clean += ".gcode"
            return clean

    # 2. Inspect the first 150 lines of the G-code header for slicer filename comments
    patterns = [
        r";\s*source_info\s*:\s*(.+)",
        r";\s*filename\s*=\s*(.+)",
        r";\s*input_filename_base\s*=\s*(.+)",
        r";\s*FILE_NAME\s*:\s*(.+)",
        r";\s*Name\s*:\s*(.+)"
    ]
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for _ in range(150):
                line = f.readline()
                if not line:
                    break
                for pat in patterns:
                    m = re.search(pat, line, re.IGNORECASE)
                    if m:
                        candidate = m.group(1).strip().strip('"').strip("'")
                        candidate = os.path.basename(candidate)
                        clean = re.sub(r"[^a-zA-Z0-9_\-.]", "_", candidate)
                        if clean:
                            if not clean.lower().endswith(".gcode"):
                                clean += ".gcode"
                            return clean
    except Exception:
        pass
    return fallback_name

def get_clean_moonraker_files():
    try:
        res = requests.get(f"{MOONRAKER}/server/files/list?root=gcodes", timeout=5)
        if res.status_code == 200:
            all_files = res.json().get("result", [])
            clean_files = [f for f in all_files if f.get("path", "").endswith(".gcode")]
            return sorted(clean_files, key=lambda x: x.get("modified", 0), reverse=True)
    except Exception:
        pass
    return []

def get_paged_files(page_idx, origin=0):
    clean_files = get_clean_moonraker_files()
    pages = [clean_files[i:i + 5] for i in range(0, len(clean_files), 5)]
    if not pages:
        return {"tf": 1, "fileinfo": "", "pageindex": 0}
    if page_idx < len(pages):
        selected = pages[page_idx]
        prefix = "/usb:" if origin == 1 else "/local:"
        fileinfo_str = ""
        for f in selected:
            safe_name = sanitize_filename(f.get("path", ""))
            fsize = int(f.get("size", 0))
            fdate = int(f.get("modified", 0))
            fileinfo_str += f"{prefix}{safe_name}:{fsize}:{fdate};"
        return {"tf": 1, "fileinfo": fileinfo_str, "pageindex": page_idx}
    return {"tf": 1, "fileinfo": "", "pageindex": page_idx}

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
            "address": "", "path": path_prefix, "name": safe_name, "fileName": safe_name,
            "size": fsize, "file_size": fsize, "time": fdate, "file_create_time": fdate,
            "thumbnail": "", "isSelect": False
        })
    return result

def cancel_print_job(client):
    global CURRENT_PRINT_ID, PRINT_START_TIMESTAMP
    log("[!] Aborting print job...")
    res = moonraker_post("/printer/print/cancel")
    if not res or res.status_code != 200:
        send_gcode("CANCEL_PRINT")
    CURRENT_PRINT_ID = ""
    PRINT_START_TIMESTAMP = 0
    client.publish("v1/devices/me/attributes", json.dumps({"state": 4, "stop": 1, "mcu_is_print": 0, "printProgress": 0}))

def pause_print_job(client, do_pause):
    if do_pause:
        moonraker_post("/printer/print/pause")
        client.publish("v1/devices/me/attributes", json.dumps({"state": 5, "pause": 1}))
    else:
        moonraker_post("/printer/print/resume")
        client.publish("v1/devices/me/attributes", json.dumps({"state": 1, "pause": 0}))

def download_and_print(url, client, rpc_params=None):
    global CURRENT_PRINT_ID, IS_DOWNLOADING, DOWNLOAD_PROGRESS, PRINT_START_TIMESTAMP
    if rpc_params is None:
        rpc_params = {}
    try:
        IS_DOWNLOADING = True
        DOWNLOAD_PROGRESS = 0
        temp_filename = url.split("/")[-1].split("?")[0]
        temp_path = os.path.join("/tmp", temp_filename)

        res = requests.get(url, stream=True, timeout=20)
        total_length = int(res.headers.get('content-length', 0))
        downloaded = 0

        with open(temp_path, "wb") as f:
            for chunk in res.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_length > 0:
                        DOWNLOAD_PROGRESS = int((downloaded / total_length) * 100)

        DOWNLOAD_PROGRESS = 100
        IS_DOWNLOADING = False
        raw_filename = temp_filename.replace(".gz", "")
        final_path = os.path.join("/tmp", raw_filename)

        if temp_filename.endswith(".gz"):
            with gzip.open(temp_path, "rb") as f_in, open(final_path, "wb") as f_out:
                f_out.write(f_in.read())
        else:
            final_path = temp_path

        # Smart Filename Extraction (Option 1)
        final_filename = extract_real_gcode_name(final_path, rpc_params, raw_filename)
        log(f"[*] Uploading as '{final_filename}' to Moonraker...")

        with open(final_path, "rb") as f:
            requests.post(f"{MOONRAKER}/server/files/upload", data={"root": "gcodes"}, files={"file": (final_filename, f)}, timeout=30)

        start_res = moonraker_post("/printer/print/start", {"filename": final_filename})
        if start_res and start_res.status_code == 200:
            PRINT_START_TIMESTAMP = int(time.time())
            safe_name = sanitize_filename(final_filename)
            client.publish("v1/devices/me/attributes", json.dumps({
                "state": 1, "mcu_is_print": 1, "filename": safe_name, "print": safe_name,
                "printId": CURRENT_PRINT_ID, "printStartTime": str(PRINT_START_TIMESTAMP)
            }))
        else:
            CURRENT_PRINT_ID = ""
            client.publish("v1/devices/me/attributes", json.dumps({"state": 0, "mcu_is_print": 0}))

        if os.path.exists(temp_path):
            os.remove(temp_path)
        if temp_path != final_path and os.path.exists(final_path):
            os.remove(final_path)
    except Exception as e:
        log(f"[-] Download/Print Error: {e}")
        IS_DOWNLOADING = False
        DOWNLOAD_PROGRESS = 100
        CURRENT_PRINT_ID = ""

def forward_video_token(t_val, ls_val):
    if not VIDEO_ENABLED:
        return
    try:
        requests.post(VIDEO_RELAY_URL, json={"token": t_val, "livestream": ls_val}, timeout=0.5)
    except Exception:
        pass

def on_message(client, userdata, msg):
    global CURRENT_PRINT_ID, CURRENT_FAN_STATE, CURRENT_LED_STATE, PRINT_START_TIMESTAMP, ACTIVE_TOKEN, ACTIVE_JWT_TOKEN, ACTIVE_LIVESTREAM
    try:
        topic = msg.topic
        raw_payload = msg.payload.decode("utf-8", errors="ignore")
        is_object_spam = '"printObjects"' in raw_payload

        if not is_object_spam:
            log(f"\n[<<< MQTT INCOMING <<<] Topic: {topic} | Payload: {raw_payload}")

        payload = json.loads(raw_payload)

        if "rpc/request/" in topic:
            req_id = topic.split("/")[-1]
            params = payload.get("params", {})
            rpc_reply = {"code": 0}

            if "token" in params or "jwtToken" in params:
                t_val = params.get("token", params.get("jwtToken"))
                ACTIVE_TOKEN = t_val
                ACTIVE_JWT_TOKEN = t_val
                ACTIVE_LIVESTREAM = params.get("livestream", 1)

                client.publish(f"v1/devices/me/rpc/response/{req_id}", json.dumps({}))
                client.publish("v1/devices/me/attributes", json.dumps({
                    "video": 1 if VIDEO_ENABLED else 0,
                    "livestream": ACTIVE_LIVESTREAM,
                    "jwtToken": ACTIVE_JWT_TOKEN,
                    "token": ACTIVE_TOKEN
                }))
                threading.Thread(target=forward_video_token, args=(t_val, ACTIVE_LIVESTREAM), daemon=True).start()

            for key, val in params.items():
                if not is_object_spam and key not in ("token", "jwtToken", "livestream"):
                    log(f"[EXECUTE] {key} -> {repr(val)}")

                if key == "pFileList":
                    rpc_reply["pFileList"] = get_web_file_array(val, 0)
                elif key == "uFileList":
                    rpc_reply["uFileList"] = get_web_file_array(val, 1)
                elif key == "reqGcodeFile":
                    v = int(val)
                    f_info = get_paged_files(v & 0xFFFF, v >> 16)
                    client.publish("v1/devices/me/attributes", json.dumps({"retGcodeFileInfo": f_info}))
                    rpc_reply.update({"retGcodeFileInfo": f_info})
                elif key == "opGcodeFile":
                    val_str = str(val)
                    if "print" in val_str:
                        cloud_fname = val_str.split(":/local/")[-1].split(":/usb/")[-1].strip()
                        PRINT_START_TIMESTAMP = int(time.time())
                        moonraker_post("/printer/print/start", {"filename": resolve_filename(cloud_fname)})
                        client.publish("v1/devices/me/attributes", json.dumps({
                            "state": 1, "mcu_is_print": 1, "filename": cloud_fname, "print": cloud_fname,
                            "opGcodeFile": val_str, "printStartTime": str(PRINT_START_TIMESTAMP)
                        }))
                    elif "delete" in val_str:
                        requests.delete(f"{MOONRAKER}/server/files/gcodes/{resolve_filename(val_str.split(':/local/')[-1].strip())}", timeout=5)
                    elif "rename" in val_str:
                        parts = val_str.split(":")
                        if len(parts) >= 4:
                            moonraker_post("/server/files/move", {"source": f"gcodes/{resolve_filename(parts[2].lstrip('/'))}", "dest": f"gcodes/{parts[3].lstrip('/')}"})
                    client.publish("v1/devices/me/attributes", json.dumps({"opGcodeFile": val_str}))

                elif key == "printId":
                    CURRENT_PRINT_ID = val
                elif key == "stop":
                    cancel_print_job(client)
                elif key == "pause":
                    pause_print_job(client, int(val) == 1)
                elif key == "print":
                    threading.Thread(target=download_and_print, args=(val, client, params), daemon=True).start()

                elif key == "curFeedratePct":
                    send_gcode(f"M220 S{val}")
                elif key == "curFlowratePct":
                    send_gcode(f"M221 S{val}")
                elif key == "enableSelfTest":
                    send_gcode("G28")
                elif key == "feed":
                    target_temp = params.get("feedStateTemp2", 200)
                    send_gcode(f"M109 S{target_temp}\nG91\nG1 E50 F300\nG90")
                elif key == "return":
                    target_temp = params.get("feedStateTemp2", 200)
                    send_gcode(f"M109 S{target_temp}\nG91\nG1 E-10 F300\nG90")
                elif key == "led":
                    CURRENT_LED_STATE = int(val)
                    send_gcode(f"SET_PIN PIN=caselight VALUE={1.0 if CURRENT_LED_STATE else 0.0}")
                    client.publish("v1/devices/me/attributes", json.dumps({"led_state": CURRENT_LED_STATE}))
                elif key == "fan":
                    CURRENT_FAN_STATE = int(val)
                    send_gcode(f"M106 S{int((CURRENT_FAN_STATE / 100.0) * 255) if CURRENT_FAN_STATE > 1 else (255 if CURRENT_FAN_STATE == 1 else 0)}")
                    client.publish("v1/devices/me/attributes", json.dumps({"fan": CURRENT_FAN_STATE}))
                elif key == "nozzleTemp2":
                    send_gcode(f"M104 S{val}")
                elif key == "bedTemp2":
                    send_gcode(f"M140 S{val}")

                elif key == "gcodeCmd":
                    cmd = str(val).strip().upper()
                    attr_update = {"gcodeCmd": cmd}
                    if "G28" in cmd:
                        send_gcode("G28\nG90")
                        attr_update["curPosition"] = get_klipper_pos()
                    elif "M290" in cmd:
                        m = re.search(r"Z([+-]?\d*\.?\d+)", cmd)
                        if m:
                            send_gcode(f"SET_GCODE_OFFSET Z_ADJUST={float(m.group(1))} MOVE=1")
                    elif "M221" in cmd:
                        m = re.search(r"S(\d+)", cmd)
                        if m:
                            send_gcode(f"M221 S{m.group(1)}")
                    elif "G0" in cmd or "G1" in cmd:
                        send_gcode(f"G90\n{cmd}")
                        pos_idx = cmd.find("G0")
                        if pos_idx >= 0:
                            attr_update["setPosition"] = cmd[pos_idx + 2:].strip()
                        attr_update["curPosition"] = get_klipper_pos()
                    else:
                        send_gcode(cmd)
                    client.publish("v1/devices/me/attributes", json.dumps(attr_update))

                elif key == "autohome":
                    send_gcode("G28\nG90")
                    client.publish("v1/devices/me/attributes", json.dumps({"curPosition": get_klipper_pos(), "autohome": 1}))
                elif key == "ReqPrinterPara":
                    client.publish("v1/devices/me/attributes", json.dumps({"curPosition": get_klipper_pos(), "autohome": 1}))
                    rpc_reply.update({"curPosition": get_klipper_pos(), "autohome": 1})

            if "token" not in params and "jwtToken" not in params:
                client.publish(f"v1/devices/me/rpc/response/{req_id}", json.dumps(rpc_reply))
    except Exception:
        pass

def main():
    global LAST_STATE, COMPLETION_BROADCAST_TIME, CURRENT_PRINT_ID, DOWNLOAD_PROGRESS, IS_DOWNLOADING, PRINT_START_TIMESTAMP
    log(f"[*] Initializing Creality Klipper Bridge ({PRINTER_NAME} as {PRINTER_MODEL})...")
    try:
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        client = mqtt.Client()

    client.username_pw_set(TB_TOKEN)
    client.on_message = on_message

    try:
        client.connect(BROKER, 1883, 60)
        client.subscribe("v1/devices/me/rpc/request/+")
        client.loop_start()
    except Exception as e:
        log(f"[-] MQTT Connection Failed: {e}")
        return

    send_gcode("G90")
    get_clean_moonraker_files()
    local_ip = get_local_ip()

    client.publish("v1/devices/me/attributes", json.dumps({
        "connect": 1, "tfCard": 1, "boxVersion": "rasp_v2.01b99", "tag": "1.0.8",
        "netIP": local_ip, "model": PRINTER_MODEL, "machineType": PRINTER_MODEL,
        "deviceName": PRINTER_NAME, "state": 0, "err": 0, "stop": 0, "pause": 0,
        "mcu_is_print": 0, "video": 1 if VIDEO_ENABLED else 0,
        "led_state": CURRENT_LED_STATE, "fan": CURRENT_FAN_STATE,
        "curPosition": get_klipper_pos(), "print": "", "filename": "", "printId": "",
        "boxTemp": int(get_host_temp())
    }))

    heartbeat_counter = 0

    while True:
        try:
            res = requests.get(f"{MOONRAKER}/printer/objects/query?print_stats&display_status&toolhead&heater_bed&extruder&virtual_sdcard&gcode_move&fan&webhooks", timeout=2)
            if res.status_code == 200:
                data = res.json().get("result", {}).get("status", {})
                print_stats = data.get("print_stats", {})
                display_status = data.get("display_status", {})
                extruder = data.get("extruder", {})
                heater_bed = data.get("heater_bed", {})
                vsd = data.get("virtual_sdcard", {})
                gcode_move = data.get("gcode_move", {})
                klipper_fan = data.get("fan", {})
                webhooks = data.get("webhooks", {})

                klipper_ready = webhooks.get("state", "ready") == "ready"
                state_str = print_stats.get("state", "standby")
                err_code = 0

                if state_str == "printing":
                    state_code = 1
                    COMPLETION_BROADCAST_TIME = 0
                    if PRINT_START_TIMESTAMP == 0:
                        PRINT_START_TIMESTAMP = int(time.time() - print_stats.get("print_time", 0))
                elif state_str == "paused":
                    state_code = 5
                    COMPLETION_BROADCAST_TIME = 0
                elif state_str == "complete":
                    if COMPLETION_BROADCAST_TIME == 0:
                        COMPLETION_BROADCAST_TIME = time.time()
                    if time.time() - COMPLETION_BROADCAST_TIME < 20:
                        state_code = 2
                    else:
                        state_code = 0
                        CURRENT_PRINT_ID = ""
                        PRINT_START_TIMESTAMP = 0
                elif state_str in ("cancelled", "error") or not klipper_ready:
                    state_code = 4 if state_str == "cancelled" else 3
                    err_code = 1 if state_str == "error" else 0
                    CURRENT_PRINT_ID = ""
                    PRINT_START_TIMESTAMP = 0
                else:
                    state_code = 0
                    COMPLETION_BROADCAST_TIME = 0
                    PRINT_START_TIMESTAMP = 0

                if state_str != LAST_STATE:
                    LAST_STATE = state_str
                is_active_print = state_str in ("printing", "paused")
                job_time = int(print_stats.get("print_time", 0)) if is_active_print else 0
                file_size = vsd.get("file_size", 0)
                left_time = int((job_time / (vsd.get("file_position", 0) / file_size)) - job_time) if is_active_print and vsd.get("file_position", 0) > 0 and file_size > 0 and job_time > 0 else 0

                client.publish("v1/devices/me/telemetry", json.dumps({
                    "dProgress": DOWNLOAD_PROGRESS if IS_DOWNLOADING else 100,
                    "printProgress": int(display_status.get("progress", 0) * 100) if is_active_print else 0,
                    "nozzleTemp": float(extruder.get("temperature", 0.0)),
                    "bedTemp": float(heater_bed.get("temperature", 0.0)),
                    "boxTemp": int(get_host_temp()),
                    "printJobTime": job_time, "printLeftTime": left_time,
                    "curFeedratePct": int(gcode_move.get("speed_factor", 1.0) * 100),
                    "curFlowratePct": int(gcode_move.get("extrude_factor", 1.0) * 100)
                }))

                safe_file = sanitize_filename(print_stats.get("filename", "")) if is_active_print else ""
                fan_val = 1 if klipper_fan.get("speed", 0.0) > 0 else 0
                cur_layer = print_stats.get("info", {}).get("current_layer", 0) if is_active_print else 0
                tot_layer = print_stats.get("info", {}).get("total_layer", 0) if is_active_print else 0

                attr_payload = {
                    "connect": 1 if klipper_ready else 0, "state": state_code, "err": err_code,
                    "mcu_is_print": 1 if is_active_print else 0, "filename": safe_file, "print": safe_file,
                    "printId": CURRENT_PRINT_ID if (is_active_print or state_code == 2) else "",
                    "nozzleTemp2": float(extruder.get("target", 0.0)), "bedTemp2": float(heater_bed.get("target", 0.0)),
                    "boxTemp": int(get_host_temp()),
                    "layer": cur_layer, "curLayer": cur_layer,
                    "totalLayer": tot_layer, "total_layer": tot_layer,
                    "curPosition": get_klipper_pos(), "fan": fan_val, "led_state": CURRENT_LED_STATE,
                    "model": PRINTER_MODEL, "machineType": PRINTER_MODEL, "deviceName": PRINTER_NAME,
                    "tfCard": 1, "boxVersion": "rasp_v2.01b99", "tag": "1.0.8", "netIP": local_ip,
                    "video": 1 if VIDEO_ENABLED else 0
                }

                if ACTIVE_TOKEN:
                    attr_payload["token"] = ACTIVE_TOKEN
                    attr_payload["jwtToken"] = ACTIVE_JWT_TOKEN
                    attr_payload["livestream"] = ACTIVE_LIVESTREAM

                if PRINT_START_TIMESTAMP > 0:
                    attr_payload["printStartTime"] = str(PRINT_START_TIMESTAMP)

                client.publish("v1/devices/me/attributes", json.dumps(attr_payload))

                heartbeat_counter += 1
                if heartbeat_counter >= 5:
                    log(f"[HEARTBEAT] State={state_code} | Z={get_klipper_pos().split('Z:')[-1]} | Temp={extruder.get('temperature', 0.0):.1f}/{extruder.get('target', 0.0):.1f}C")
                    heartbeat_counter = 0

        except Exception:
            pass
        time.sleep(1)

if __name__ == "__main__":
    main()