# -*- coding: utf-8 -*-
import json, time, requests, threading, os, sys, uuid, random, socket, subprocess
import paho.mqtt.client as mqtt

# --- CONFIGURATION ---
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
with open(CONFIG_PATH, "r") as f:
    CONFIG = json.load(f)

TB_TOKEN = CONFIG.get("tb_token", "")
JWT_TOKEN = CONFIG.get("jwt_token", "")
BROKER = CONFIG.get("broker", "mqtt.crealitycloud.com")
MOONRAKER = CONFIG.get("moonraker_url", "http://127.0.0.1:7125").rstrip("/")
PRINTER_NAME = CONFIG.get("printer_name", "Chris")
PRINTER_MODEL = CONFIG.get("printer_model", "K1 Max")

# --- GLOBALS ---
CURRENT_PRINT_ID = ""
CURRENT_FAN_STATE = 0
CURRENT_LED_STATE = 0
ACTIVE_LIVESTREAM = 0
ACTIVE_JWT_TOKEN = JWT_TOKEN

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
        return requests.post(f"{MOONRAKER}{endpoint}", json=payload or {}, timeout=5)
    except:
        return None

def send_gcode(cmd):
    return moonraker_post("/printer/gcode/script", {"script": cmd})

def get_klipper_pos():
    try:
        res = requests.get(f"{MOONRAKER}/printer/objects/query?toolhead", timeout=2)
        pos = res.json()["result"]["status"]["toolhead"]["position"]
        return f"X:{pos[0]:.2f} Y:{pos[1]:.2f} Z:{pos[2]:.2f}"
    except:
        return "X:0.00 Y:0.00 Z:0.00"

def sanitize_filename(p):
    if not p: return ""
    return p.lstrip("/").replace("gcodes/", "")

def get_clean_moonraker_files():
    try:
        res = requests.get(f"{MOONRAKER}/server/files/list?root=gcodes", timeout=5)
        if res.status_code == 200:
            files = res.json()["result"]
            files.sort(key=lambda x: x.get("modified", 0), reverse=True)
            return files
    except:
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
    if idx >= len(pages): return []
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

def import_cloud_device():
    global TB_TOKEN
    try:
        if not JWT_TOKEN:
            return
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
        log(f"[*] Cloud Import Response: {res.text}")
        if res_json.get("code") == 0 and res_json.get("result"):
            new_tb = res_json["result"].get("tbToken")
            if new_tb:
                TB_TOKEN = new_tb
                log(f"[*] Dynamically acquired provisioned tbToken: {TB_TOKEN}")
                try:
                    CONFIG["tb_token"] = new_tb
                    with open(CONFIG_PATH, "w") as cf:
                        json.dump(CONFIG, cf, indent=4)
                except Exception as se:
                    log(f"[-] Could not persist tbToken to config.json: {se}")
    except Exception as e:
        log(f"[-] Cloud Import Exception: {e}")

def on_message(client, userdata, msg):
    global CURRENT_FAN_STATE, CURRENT_LED_STATE, ACTIVE_LIVESTREAM, ACTIVE_JWT_TOKEN
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        topic = msg.topic
        log(f"[<<< MQTT INCOMING <<<] Topic: {topic} | Payload: {json.dumps(payload)}")

        if "v1/devices/me/rpc/request/" in topic:
            req_id = topic.split("/")[-1]
            method = payload.get("method")
            params = payload.get("params", {})
            rpc_reply = {}

            if method in ["set", "get"]:
                for key, val in params.items():
                    log(f"[EXECUTE] {key} -> {repr(val)}")

                    if key == "pFileList":
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
                        except:
                            f_info = {"tf": 0, "fileinfo": "", "pageindex": 0}
                        client.publish("v1/devices/me/attributes", json.dumps({"retGcodeFileInfo": f_info}))
                        rpc_reply.update({"retGcodeFileInfo": f_info, "result": f_info})

                    elif key == "opGcodeFile":
                        val_str = str(val)
                        if "print" in val_str and "local" in val_str:
                            fname = val_str.split("/local/")[-1]
                            moonraker_post("/printer/print/start", {"filename": fname})
                        client.publish("v1/devices/me/attributes", json.dumps({"opGcodeFile": val_str}))
                        rpc_reply.update({"opGcodeFile": val_str})

                    elif key == "print" and str(val).startswith("http"):
                        client.publish("v1/devices/me/attributes", json.dumps({"print": val}))
                        def download_and_print(url):
                            import gzip
                            try:
                                r = requests.get(url, timeout=30)
                                file_data = r.content
                                
                                # Unpack the compressed Creality Cloud payload
                                if url.endswith(".gz"):
                                    try:
                                        file_data = gzip.decompress(file_data)
                                    except Exception:
                                        pass
                                
                                # Extract the CDN hash name from the URL
                                base_name = url.split("/")[-1].replace(".gz", "")
                                if not base_name.endswith(".gcode"):
                                    base_name += ".gcode"
                                fname = "CC_" + base_name
                                
                                # Upload pure text gcode and start print
                                requests.post(f"{MOONRAKER}/server/files/upload", files={'file': (fname, file_data)}, data={'print': 'true'}, timeout=10)
                            except Exception:
                                pass
                        threading.Thread(target=download_and_print, args=(str(val),)).start()

                    elif key == "pause":
                        if int(val) == 1: moonraker_post("/printer/print/pause")
                        else: moonraker_post("/printer/print/resume")
                        client.publish("v1/devices/me/attributes", json.dumps({"pause": int(val)}))
                    elif key == "stop":
                        if int(val) == 1: moonraker_post("/printer/print/cancel")
                        client.publish("v1/devices/me/attributes", json.dumps({"stop": 1, "state": 4}))

                    elif key in ["nozzleTemp", "nozzleTemp2"]:
                        send_gcode(f"M104 S{val}")
                    elif key in ["bedTemp", "bedTemp2"]:
                        send_gcode(f"M140 S{val}")

                    elif key == "fan":
                        CURRENT_FAN_STATE = int(val)
                        speed = int((CURRENT_FAN_STATE / 100.0) * 255) if CURRENT_FAN_STATE > 1 else (255 if CURRENT_FAN_STATE == 1 else 0)
                        send_gcode(f"M106 S{speed}")
                        client.publish("v1/devices/me/attributes", json.dumps({"fan": CURRENT_FAN_STATE}))

                    elif key == "led":
                        CURRENT_LED_STATE = int(val)
                        client.publish("v1/devices/me/attributes", json.dumps({"led_state": CURRENT_LED_STATE, "led": CURRENT_LED_STATE}))
                        if CURRENT_LED_STATE == 1:
                            send_gcode("M106 S255")
                        else:
                            send_gcode("M106 S0")

                    elif key == "autohome":
                        val_str = str(val).lower()
                        if val_str == "z":
                            send_gcode("G28 Z\nG90")
                        elif val_str in ["xy", "x", "y", "1", "true"]:
                            send_gcode("G28 X Y\nG90")
                        else:
                            send_gcode("G28\nG90")
                        pos = get_klipper_pos()
                        client.publish("v1/devices/me/attributes", json.dumps({"curPosition": pos, "autohome": 1}))
                        rpc_reply.update({"curPosition": pos, "autohome": 1})

                    elif key == "gcodeCmd":
                        send_gcode(val)

                    elif key == "token":
                        ACTIVE_JWT_TOKEN = val
                        if 'livestream' in params:
                            try:
                                v_url = CONFIG.get("video_relay_url", "http://127.0.0.1:8555/control")
                                requests.post(v_url, json={"token": val, "livestream": int(params.get("livestream", 1))}, timeout=3)
                            except Exception:
                                pass

                    elif key == "livestream":
                        ACTIVE_LIVESTREAM = int(val)
                        client.publish("v1/devices/me/attributes", json.dumps({"livestream": ACTIVE_LIVESTREAM}))

            if "token" not in params and "jwtToken" not in params:
                client.publish(f"v1/devices/me/rpc/response/{req_id}", json.dumps(rpc_reply))
    except Exception as e:
        log(f"[-] RPC Error: {e}")

def main():
    global CURRENT_PRINT_ID
    import_cloud_device()
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

    attr_init = {
        "printStartTime": " ", "layer": 0, "printedTimes": 0, "timesLeftToPrint": 0,
        "err": 0, "curPosition": get_klipper_pos(), "printId": "", "filename": "", "video": 1,
        "netIP": get_local_ip(), "state": 0, "tfCard": 1, "model": PRINTER_MODEL, "machineType": PRINTER_MODEL, "deviceName": PRINTER_NAME,
        "mcu_is_print": 0, "boxVersion": "rasp_v2.11b03", "InitString": " ", "APILicense": " ",
        "DIDString": " ", "retGcodeFileInfo": get_paged_files(0, 0), "autohome": 0, "fan": CURRENT_FAN_STATE,
        "stop": 0, "print": "", "nozzleTemp2": 0.0, "bedTemp2": 0.0, "pause": 0,
        "opGcodeFile": "", "gcodeCmd": "", "setPosition": "", "livestream": ACTIVE_LIVESTREAM,
        "token": ACTIVE_JWT_TOKEN, "pullclient": "", "tag": "1.1.3", "led_state": CURRENT_LED_STATE
    }
    client.publish("v1/devices/me/attributes", json.dumps(attr_init))

    while True:
        try:
            res = requests.get(f"{MOONRAKER}/printer/objects/query?print_stats&display_status&toolhead&heater_bed&extruder", timeout=2)
            if res.status_code == 200:
                data = res.json()["result"]["status"]

                p_stats = data.get("print_stats", {})
                d_stats = data.get("display_status", {})
                extruder = data.get("extruder", {})
                heater_bed = data.get("heater_bed", {})
                toolhead = data.get("toolhead", {})

                klipper_state = p_stats.get("state", "")
                safe_file = p_stats.get("filename", "")
                is_active_print = (klipper_state == "printing")

                state_code = 0
                if klipper_state == "printing": state_code = 1
                elif klipper_state == "complete": state_code = 2
                elif klipper_state == "error": state_code = 3
                elif klipper_state == "cancelled": state_code = 4
                elif klipper_state == "paused": state_code = 5

                if is_active_print and not CURRENT_PRINT_ID:
                    CURRENT_PRINT_ID = f"local_{int(time.time())}"
                elif not is_active_print and state_code != 2:
                    CURRENT_PRINT_ID = ""

                noz_temp = float(extruder.get("temperature", 0.0) or 0.0)
                bed_temp = float(heater_bed.get("temperature", 0.0) or 0.0)

                z_pos = 0.0
                if "position" in toolhead and len(toolhead["position"]) >= 3:
                    z_pos = float(toolhead["position"][2])

                client.publish("v1/devices/me/telemetry", json.dumps({
                    "printProgress": int(d_stats.get("progress", 0) * 100) if is_active_print else 0,
                    "nozzleTemp": noz_temp if noz_temp > 10.0 else 25.0,
                    "bedTemp": bed_temp if bed_temp > 10.0 else 25.0,
                    "printJobTime": int(p_stats.get("print_duration", 0)),
                    "printLeftTime": 0
                }))

                attr_payload = {
                    "connect": 1, "state": state_code,
                    "mcu_is_print": 1 if is_active_print else 0,
                    "filename": safe_file, "print": safe_file,
                    "printId": CURRENT_PRINT_ID,
                    "nozzleTemp2": float(extruder.get("target", 0.0)),
                    "bedTemp2": float(heater_bed.get("target", 0.0)),
                    "curPosition": get_klipper_pos(),
                    "model": PRINTER_MODEL, "machineType": PRINTER_MODEL, "deviceName": PRINTER_NAME,
                    "led_state": CURRENT_LED_STATE,
                    "video": 1
                }
                client.publish("v1/devices/me/attributes", json.dumps(attr_payload))

        except Exception as e:
            pass

        time.sleep(2)

if __name__ == "__main__":
    main()
