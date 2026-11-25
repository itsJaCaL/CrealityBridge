#!/bin/bash
set -e

# ==============================================================================
# Creality Cloud Universal Klipper Bridge & WebRTC Video Relay Installer
# ==============================================================================

REPO_RAW_URL="https://raw.githubusercontent.com/itsJaCaL/CrealityBridge/main"

# Enforce sudo but extract the REAL user
if [[ $EUID -ne 0 ]]; then
    echo -e "\033[1;31m[!] This script must be run with sudo: sudo bash $0\033[0m"
    exit 1
fi

if [[ -n "$SUDO_USER" && "$SUDO_USER" != "root" ]]; then
    CURRENT_USER="$SUDO_USER"
else
    echo -e "\033[1;31m[!] Do not run this directly as root. Run as your normal user with sudo.\033[0m"
    exit 1
fi

USER_HOME=$(getent passwd "$CURRENT_USER" | cut -d: -f6)
INSTALL_DIR="$USER_HOME/CrealityBridge"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Color Definitions for a Sexy UI
C_CYAN="\033[1;36m"
C_GREEN="\033[1;32m"
C_YELLOW="\033[1;33m"
C_RED="\033[1;31m"
C_RESET="\033[0m"

print_banner() {
    clear
    echo -e "${C_CYAN}============================================================${C_RESET}"
    echo -e "${C_CYAN}   🚀 Creality Cloud Universal Klipper Bridge & Video Relay ${C_RESET}"
    echo -e "${C_CYAN}============================================================${C_RESET}"
}

print_banner

if [[ "$1" == "--uninstall" ]]; then
    echo -e "${C_YELLOW}[*] Uninstalling services and removing files...${C_RESET}"
    systemctl stop creality_bridge.service 2>/dev/null || true
    systemctl disable creality_bridge.service 2>/dev/null || true
    rm -f /etc/systemd/system/creality_bridge.service
    systemctl stop creality_video.service 2>/dev/null || true
    systemctl disable creality_video.service 2>/dev/null || true
    rm -f /etc/systemd/system/creality_video.service
    systemctl daemon-reload
    rm -rf "$INSTALL_DIR"
    rm -rf "/root/CrealityBridge" 2>/dev/null || true

    if [[ -f /swapfile ]] && grep -qx '/swapfile none swap sw 0 0' /etc/fstab; then
        echo -e "\n${C_YELLOW}[?] A 2 GB /swapfile is set up. The video installer creates one on low-RAM boards,"
        echo -e "    but it may also be your own.${C_RESET}"
        read -p "Remove /swapfile and its /etc/fstab line? (y/N): " RM_SWAP
        if [[ "$RM_SWAP" =~ ^[Yy]$ ]]; then
            swapoff /swapfile 2>/dev/null || true
            sed -i '\|^/swapfile none swap sw 0 0$|d' /etc/fstab
            rm -f /swapfile
            echo -e "${C_GREEN}[+] Swapfile removed.${C_RESET}"
        fi
    fi
    echo -e "${C_CYAN}[i] The pip packages paho-mqtt and requests were left installed for $CURRENT_USER (other tools may use them)."
    echo -e "    To remove them: pip3 uninstall paho-mqtt requests${C_RESET}"
    echo -e "${C_GREEN}[+] Uninstalled cleanly!${C_RESET}"
    exit 0
fi

echo -e "\n${C_CYAN}Select Installation Mode:${C_RESET}"
echo "  [1] Telemetry & Control Bridge Only (Sonic Pad / Nebula Pad / No Cam)"
echo "  [2] All-in-One: Bridge + Local WebRTC Video Relay (Pi 5 / Pi 3B+ / CB1)"
echo "  [3] Remote Video Relay Node Only (TrueNAS VM / Proxmox / Home Server)"
echo "  [4] Uninstall Everything"
read -p "Enter choice [1-4]: " INSTALL_MODE

if [[ "$INSTALL_MODE" == "4" ]]; then
    bash "$0" --uninstall
    exit 0
fi

mkdir -p "$INSTALL_DIR/bridge" "$INSTALL_DIR/video-relay"

fetch_file() {
    local rel_path="$1"
    local dest_path="$2"
    local src_path="$SCRIPT_DIR/$rel_path"
    if [[ -f "$src_path" ]]; then
        if [[ "$(realpath "$src_path" 2>/dev/null)" != "$(realpath "$dest_path" 2>/dev/null)" ]]; then
            cp "$src_path" "$dest_path"
        fi
    else
        # -f: fail on HTTP errors instead of saving a "404: Not Found" page as the script
        curl -fsSL "$REPO_RAW_URL/$rel_path" -o "$dest_path"
    fi
}

# write_json FILE KEY...
# Merges keys into a JSON file (keeping any keys already there) and writes it with mode 600.
# Each value comes from an env var, so quotes and backslashes can't break the JSON:
#   JV_<key>  = string value     JR_<key> = raw JSON value (true/false/numbers)
# Prefix a KEY with "?" to only set it when the file doesn't already have a non-empty value.
write_json() {
    local file="$1"; shift
    JSON_FILE="$file" JSON_KEYS="$*" python3 - <<'PY'
import json, os
path = os.environ["JSON_FILE"]
cfg = {}
if os.path.exists(path):
    try:
        with open(path) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
for key in os.environ["JSON_KEYS"].split():
    only_if_missing = key.startswith("?")
    key = key.lstrip("?")
    if only_if_missing and cfg.get(key):
        continue
    if "JR_" + key in os.environ:
        cfg[key] = json.loads(os.environ["JR_" + key])
    elif "JV_" + key in os.environ:
        cfg[key] = os.environ["JV_" + key]
tmp = path + ".tmp"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(cfg, f, indent=4)
os.replace(tmp, path)
PY
}

json_get() {  # json_get FILE KEY -> prints the value, or nothing
    python3 -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get(sys.argv[2]) or "")
except Exception: pass' "$1" "$2" 2>/dev/null
}

# Shared secret between the bridge and the video relay's /control endpoint (mode 2 writes both ends).
RELAY_SECRET="$(tr -dc A-Za-z0-9 </dev/urandom | head -c 32)"

# ==============================================================================
# STEP A: Configure Telemetry & Control Bridge
# ==============================================================================
if [[ "$INSTALL_MODE" == "1" || "$INSTALL_MODE" == "2" ]]; then
    echo -e "\n${C_CYAN}============================================================${C_RESET}"
    echo -e "${C_CYAN}                CREALITY CLOUD AUTHENTICATION                ${C_RESET}"
    echo -e "${C_CYAN}============================================================${C_RESET}"
    echo "To connect your printer to Creality Cloud:"
    echo "  1. In the Creality Cloud app: Workbench -> + -> Raspberry Pi (adds the device)."
    echo "  2. Sign in below with the same Creality Cloud email and password."
    echo "     (Your password is used once to fetch the key and is never saved.)"
    echo -e "${C_CYAN}============================================================${C_RESET}\n"
    BRIDGE_CONFIG="$INSTALL_DIR/bridge/config.json"
    EXISTING_TB="$(json_get "$BRIDGE_CONFIG" tb_token)"
    HAS_SIGNIN="false"
    [[ -n "$EXISTING_TB" && "$EXISTING_TB" != CC_* ]] && HAS_SIGNIN="true"

    echo "  [1] Sign in to Creality Cloud (recommended: browser with Google/Apple/Facebook/X/email, or email + password)"
    echo "  [2] Paste a downloaded .tk key file instead"
    if [[ "$HAS_SIGNIN" == "true" ]]; then
        echo "  [3] Keep this printer's existing sign-in (reinstall/update)"
    fi
    read -p "Select [default: 1]: " AUTH_MODE
    AUTH_MODE="${AUTH_MODE:-1}"
    JWT_TOKEN=""
    if [[ "$AUTH_MODE" == "2" ]]; then
        while [[ -z "$JWT_TOKEN" ]]; do
            read -p "Paste the contents of your .tk file (Token): " JWT_TOKEN
        done
    elif [[ "$AUTH_MODE" == "3" && "$HAS_SIGNIN" == "true" ]]; then
        AUTH_MODE="3"
    else
        AUTH_MODE="1"
    fi

    RANDOM_KEY="CC_$(tr -dc A-Za-z0-9 </dev/urandom | head -c 20)"

    read -p "Enter a display name for your printer [default: Klipper-Printer]: " PRINTER_NAME
    PRINTER_NAME="${PRINTER_NAME:-Klipper-Printer}"

    echo -e "\n${C_CYAN}Select Printer Model Profile:${C_RESET}"
    echo "  (The cloud keeps the model from the FIRST registration of a device. Multi-colour models show a"
    echo "   Happy Hare MMU (ERCF, Tradrack...) as CFS slots: gate 0 = T1A, gate 4 = T2A ...)"
    echo "  [1]  K2 Plus        (multi-colour/CFS; all flagship controls)"
    echo "  [2]  K2 Pro         (multi-colour/CFS)"
    echo "  [3]  K2             (multi-colour/CFS)"
    echo "  [4]  Creality Hi    (multi-colour/CFS)"
    echo "  [5]  SPARKX i7      (multi-colour/CFS)"
    echo "  [6]  K1 Max         (high-speed Klipper controls)"
    echo "  [7]  K1C"
    echo "  [8]  K1"
    echo "  [9]  Ender-3 V3 KE  (bed-slinger)"
    echo "  [10] Ender-3 V3"
    echo "  [11] Ender-3 S1 Pro"
    echo "  [12] Custom model name (exactly as Creality Cloud spells it)"
    read -p "Select profile [default: 1]: " MODEL_CHOICE
    case "${MODEL_CHOICE:-1}" in
        1) PRINTER_MODEL="K2 Plus" ;;
        2) PRINTER_MODEL="K2 Pro" ;;
        3) PRINTER_MODEL="K2" ;;
        4) PRINTER_MODEL="Creality Hi" ;;
        5) PRINTER_MODEL="SPARKX i7" ;;
        6) PRINTER_MODEL="K1 Max" ;;
        7) PRINTER_MODEL="K1C" ;;
        8) PRINTER_MODEL="K1" ;;
        9) PRINTER_MODEL="Ender-3 V3 KE" ;;
        10) PRINTER_MODEL="Ender-3 V3" ;;
        11) PRINTER_MODEL="Ender-3 S1 Pro" ;;
        12) read -p "Enter exact custom model string: " PRINTER_MODEL ;;
        *) PRINTER_MODEL="K2 Plus" ;;
    esac

    read -p "Enter local Moonraker URL [default: http://127.0.0.1:7125]: " MOONRAKER_URL
    MOONRAKER_URL="${MOONRAKER_URL:-http://127.0.0.1:7125}"

    VIDEO_ENABLED="false"
    VIDEO_RELAY_URL="http://127.0.0.1:8555/control"
    BRIDGE_RELAY_SECRET=""

    if [[ "$INSTALL_MODE" == "2" ]]; then
        VIDEO_ENABLED="true"
        VIDEO_RELAY_URL="http://127.0.0.1:8555/control"
        BRIDGE_RELAY_SECRET="$RELAY_SECRET"
    elif [[ "$INSTALL_MODE" == "1" ]]; then
        read -p "Are you running a Remote Video Relay VM on another IP? (y/N): " HAS_REMOTE_VID
        if [[ "$HAS_REMOTE_VID" =~ ^[Yy]$ ]]; then
            VIDEO_ENABLED="true"
            read -p "Enter Remote Video Relay IP (e.g., 192.168.128.230): " REMOTE_VID_IP
            VIDEO_RELAY_URL="http://${REMOTE_VID_IP}:8555/control"
            read -p "Enter the relay secret printed by the relay node's installer: " BRIDGE_RELAY_SECRET
        fi
    fi

    echo -e "${C_YELLOW}[*] Checking Python dependencies for Bridge...${C_RESET}"
    if ! sudo -u $CURRENT_USER python3 -c "import paho.mqtt.client, requests" 2>/dev/null; then
        echo -e "${C_YELLOW}[*] Installing paho-mqtt and requests...${C_RESET}"
        PIP_LOG="$INSTALL_DIR/bridge/pip-install.log"
        { sudo -u $CURRENT_USER pip3 install paho-mqtt requests --break-system-packages \
            || sudo -u $CURRENT_USER pip3 install paho-mqtt requests; } >"$PIP_LOG" 2>&1 || true
        if ! sudo -u $CURRENT_USER python3 -c "import paho.mqtt.client, requests" 2>/dev/null; then
            echo -e "${C_RED}[!] Could not install paho-mqtt / requests. Last lines of the pip output:${C_RESET}"
            tail -n 25 "$PIP_LOG"
            echo -e "${C_RED}    Full log: $PIP_LOG${C_RESET}"
            exit 1
        fi
    fi

    fetch_file "bridge/creality_master_bridge.py" "$INSTALL_DIR/bridge/creality_master_bridge.py"
    fetch_file "bridge/creality_login.py" "$INSTALL_DIR/bridge/creality_login.py"

    # Reinstalls merge into the existing config, so a working tb_token and any custom settings
    # (LED macros etc.) survive even if the new sign-in fails. Keep a copy of the old file too.
    if [[ -f "$BRIDGE_CONFIG" ]]; then
        cp -p "$BRIDGE_CONFIG" "$BRIDGE_CONFIG.preinstall.bak"
        chmod 600 "$BRIDGE_CONFIG.preinstall.bak"
    fi
    CONFIG_KEYS="?tb_token broker moonraker_url printer_name printer_model video_enabled video_relay_url"
    CONFIG_KEYS+=" ?led_on_gcode ?led_off_gcode ?auto_clear_seconds ?auto_clear_errors"
    [[ -n "$JWT_TOKEN" ]] && CONFIG_KEYS+=" jwt_token"
    [[ -n "$BRIDGE_RELAY_SECRET" ]] && CONFIG_KEYS+=" video_relay_secret"
    JV_tb_token="$RANDOM_KEY" JV_jwt_token="$JWT_TOKEN" JV_broker="mqtt.crealitycloud.com" \
    JV_moonraker_url="$MOONRAKER_URL" JV_printer_name="$PRINTER_NAME" JV_printer_model="$PRINTER_MODEL" \
    JR_video_enabled="$VIDEO_ENABLED" JV_video_relay_url="$VIDEO_RELAY_URL" JV_video_relay_secret="$BRIDGE_RELAY_SECRET" \
    JV_led_on_gcode="" JV_led_off_gcode="" JR_auto_clear_seconds="8" JR_auto_clear_errors="false" \
        write_json "$BRIDGE_CONFIG" $CONFIG_KEYS
    chown -R $CURRENT_USER:$CURRENT_USER "$INSTALL_DIR"

    if [[ "$AUTH_MODE" == "1" ]]; then
        echo -e "\n${C_CYAN}[*] Signing in to Creality Cloud...${C_RESET}"
        sudo -u $CURRENT_USER python3 "$INSTALL_DIR/bridge/creality_login.py" --config "$BRIDGE_CONFIG" --no-restart-hint || {
            echo -e "${C_YELLOW}[!] Sign-in did not finish. The service is still being installed; finish later with:${C_RESET}"
            echo "      python3 $INSTALL_DIR/bridge/creality_login.py --config $INSTALL_DIR/bridge/config.json"
            echo "      sudo systemctl restart creality_bridge.service"
        }
    fi

    echo -e "${C_YELLOW}[*] Registering creality_bridge.service...${C_RESET}"
    tee /etc/systemd/system/creality_bridge.service > /dev/null << EOF
[Unit]
Description=Creality Cloud Telemetry & Control Bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$INSTALL_DIR/bridge
ExecStart=/usr/bin/python3 $INSTALL_DIR/bridge/creality_master_bridge.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable creality_bridge.service
    systemctl restart creality_bridge.service
    echo -e "${C_GREEN}[+] Creality Master Bridge installed and started!${C_RESET}"
fi

# ==============================================================================
# STEP B: Configure WebRTC Video Relay
# ==============================================================================
if [[ "$INSTALL_MODE" == "2" || "$INSTALL_MODE" == "3" ]]; then
    echo -e "\n${C_CYAN}--- WebRTC Video Relay Configuration ---${C_RESET}"

    DEFAULT_CAM_URL="http://127.0.0.1/webcam/?action=stream"
    if [[ "$INSTALL_MODE" == "3" ]]; then
        read -p "Enter your Klipper Printer's IP address (e.g., 192.168.50.157): " PRINTER_IP
        DEFAULT_CAM_URL="http://${PRINTER_IP}/webcam/?action=stream"
    else
        DETECTED_STREAM=$(curl -s "http://127.0.0.1:7125/server/webcams/list" | grep -o '"/webcam/[^"]*"' | head -n 1 | tr -d '"')
        if [[ -n "$DETECTED_STREAM" ]]; then
            DEFAULT_CAM_URL="http://127.0.0.1${DETECTED_STREAM}"
        fi
    fi

    read -p "Enter Camera Stream URL [default: $DEFAULT_CAM_URL]: " CAM_URL
    CAM_URL="${CAM_URL:-$DEFAULT_CAM_URL}"

    echo -e "\n${C_CYAN}Select Camera Rotation:${C_RESET}"
    echo "  [1] 0 degrees   (No rotation)"
    echo "  [2] 90 degrees  (Counter-Clockwise)"
    echo "  [3] 180 degrees (Upside-down flip)"
    echo "  [4] 270 degrees (Clockwise)"
    read -p "Select rotation [default: 1]: " ROT_CHOICE
    case "${ROT_CHOICE:-1}" in
        1) ROT_DEG=0 ;;
        2) ROT_DEG=90 ;;
        3) ROT_DEG=180 ;;
        4) ROT_DEG=270 ;;
        *) ROT_DEG=0 ;;
    esac

    TOTAL_MEM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    TOTAL_SWAP_KB=$(grep SwapTotal /proc/meminfo | awk '{print $2}')
    if [[ "$TOTAL_MEM_KB" -lt 2500000 && "$TOTAL_SWAP_KB" -lt 1000000 ]]; then
        echo -e "${C_YELLOW}[*] Low RAM detected. Creating 2GB safety swapfile...${C_RESET}"
        if [[ ! -f /swapfile ]]; then
            fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048 &>/dev/null
            chmod 600 /swapfile
            mkswap /swapfile &>/dev/null
            swapon /swapfile
            echo '/swapfile none swap sw 0 0' | tee -a /etc/fstab > /dev/null
        fi
    fi

    echo -e "${C_YELLOW}[*] Installing system packages (FFmpeg & Dev Tools)...${C_RESET}"
    apt-get update -qq
    apt-get install -y -qq python3-venv python3-pip python3-dev ffmpeg \
        libavdevice-dev libavfilter-dev libopus-dev libvpx-dev pkg-config \
        libsrtp2-dev libssl-dev libffi-dev curl

    echo -e "${C_YELLOW}[*] Building Python virtual environment (venv_video)...${C_RESET}"
    sudo -u $CURRENT_USER python3 -m venv "$INSTALL_DIR/video-relay/venv_video"
    
    echo -e "${C_YELLOW}[*] Installing WebRTC & OpenCV Python packages (this may take a minute)...${C_RESET}"
    VENV_PIP="$INSTALL_DIR/video-relay/venv_video/bin/pip"
    VIDEO_PIP_LOG="$INSTALL_DIR/video-relay/pip-install.log"
    {
        sudo -u $CURRENT_USER "$VENV_PIP" install --upgrade pip &&
        sudo -u $CURRENT_USER "$VENV_PIP" install aiortc aiohttp websockets websocket-client requests paho-mqtt "numpy<2" opencv-python-headless
    } >"$VIDEO_PIP_LOG" 2>&1 || true
    if ! sudo -u $CURRENT_USER "$INSTALL_DIR/video-relay/venv_video/bin/python3" -c "import aiortc, aiohttp, websocket, cv2, numpy" 2>/dev/null; then
        echo -e "${C_RED}[!] The video relay's Python packages did not install. Last lines of the pip output:${C_RESET}"
        tail -n 25 "$VIDEO_PIP_LOG"
        echo -e "${C_RED}    Full log: $VIDEO_PIP_LOG${C_RESET}"
        exit 1
    fi

    echo -e "${C_YELLOW}[*] Downloading official signaling scripts...${C_RESET}"
    # Pinned to a known commit so a change upstream can't silently break (or alter) installs.
    SIGNALING_COMMIT="42622164d97a006cfe10a54327bb3db4c17d5837"
    curl -fsSL "https://raw.githubusercontent.com/crealitycloud/OctoPrint-CrealityCloud-With-Video/${SIGNALING_COMMIT}/octoprint_crealitycloud/signaling_channel.py" \
        -o "$INSTALL_DIR/video-relay/signaling_channel.py"

    fetch_file "video-relay/webrtc_manager.py" "$INSTALL_DIR/video-relay/webrtc_manager.py"
    fetch_file "video-relay/webrtc_relay.py" "$INSTALL_DIR/video-relay/webrtc_relay.py"

    VIDEO_CONFIG="$INSTALL_DIR/video-relay/video_config.json"
    if [[ "$INSTALL_MODE" == "3" ]]; then
        # Remote node: reachable from the LAN, so /control requires the shared secret.
        # Keep an existing secret so already-configured bridges keep working after a reinstall.
        JV_camera_url="$CAM_URL" JR_rotation="$ROT_DEG" JV_bind_host="0.0.0.0" JV_control_secret="$RELAY_SECRET" \
            write_json "$VIDEO_CONFIG" camera_url rotation bind_host ?control_secret
    else
        # Same box as the bridge: loopback only.
        JV_camera_url="$CAM_URL" JR_rotation="$ROT_DEG" JV_bind_host="127.0.0.1" JV_control_secret="$RELAY_SECRET" \
            write_json "$VIDEO_CONFIG" camera_url rotation bind_host control_secret
    fi
    chown -R $CURRENT_USER:$CURRENT_USER "$INSTALL_DIR"

    echo -e "${C_YELLOW}[*] Registering creality_video.service...${C_RESET}"
    tee /etc/systemd/system/creality_video.service > /dev/null << EOF
[Unit]
Description=Creality Cloud WebRTC Video Relay
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$INSTALL_DIR/video-relay
ExecStart=$INSTALL_DIR/video-relay/venv_video/bin/python3 $INSTALL_DIR/video-relay/webrtc_relay.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable creality_video.service
    systemctl restart creality_video.service
    echo -e "${C_GREEN}[+] Creality WebRTC Video Relay installed and started!${C_RESET}"
    if [[ "$INSTALL_MODE" == "3" ]]; then
        echo -e "\n${C_YELLOW}[!] Relay secret: $(json_get "$VIDEO_CONFIG" control_secret)${C_RESET}"
        echo    "    Enter this when installing the bridge on the printer (mode 1, remote relay)."
    fi
fi

echo -e "\n${C_CYAN}============================================================${C_RESET}"
echo -e "${C_GREEN} [+] INSTALLATION COMPLETE!${C_RESET}"
echo -e "     ${C_CYAN}Bridge Status :${C_RESET} sudo systemctl status creality_bridge.service"
echo -e "     ${C_CYAN}Video Status  :${C_RESET} sudo systemctl status creality_video.service"
echo -e "     ${C_CYAN}Uninstall     :${C_RESET} sudo bash $0 --uninstall"
echo -e "${C_CYAN}============================================================${C_RESET}"