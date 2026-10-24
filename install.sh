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
        curl -sSL "$REPO_RAW_URL/$rel_path" -o "$dest_path"
    fi
}

# ==============================================================================
# STEP A: Configure Telemetry & Control Bridge
# ==============================================================================
if [[ "$INSTALL_MODE" == "1" || "$INSTALL_MODE" == "2" ]]; then
    echo -e "\n${C_CYAN}============================================================${C_RESET}"
    echo -e "${C_CYAN}                CREALITY CLOUD AUTHENTICATION                ${C_RESET}"
    echo -e "${C_CYAN}============================================================${C_RESET}"
    echo "To connect your printer to Creality Cloud:"
    echo "  1. Go to Creality Cloud -> Devices -> Add Device (Pi/Klipper)."
    echo "  2. Download your printer key files."
    echo "  3. Open the downloaded .tk file with a text editor & copy text."
    echo -e "${C_CYAN}============================================================${C_RESET}\n"

    while [[ -z "$JWT_TOKEN" ]]; do
        read -p "Paste the contents of your .tk file (Token): " JWT_TOKEN
    done

    RANDOM_KEY="CC_$(tr -dc A-Za-z0-9 </dev/urandom | head -c 20)"

    read -p "Enter a display name for your printer [default: Klipper-Printer]: " PRINTER_NAME
    PRINTER_NAME="${PRINTER_NAME:-Klipper-Printer}"

    echo -e "\n${C_CYAN}Select Printer Model Profile:${C_RESET}"
    echo "  [1] K2 Plus        (Unlocks ALL flagship controls: Flow, Speed, Extrude/Retract, Fans, CFS)"
    echo "  [2] K1 Max         (Unlocks full high-speed Klipper controls: Flow, Speed, Extrude/Retract)"
    echo "  [3] Ender-3 V3 KE  (Standard Klipper bed-slinger profile)"
    echo "  [4] Ender-3 S1 Pro"
    echo "  [5] Custom Model Name"
    read -p "Select profile [default: 1]: " MODEL_CHOICE
    case "${MODEL_CHOICE:-1}" in
        1) PRINTER_MODEL="K2 Plus" ;;
        2) PRINTER_MODEL="K1 Max" ;;
        3) PRINTER_MODEL="Ender-3 V3 KE" ;;
        4) PRINTER_MODEL="Ender-3 S1 Pro" ;;
        5) read -p "Enter exact custom model string: " PRINTER_MODEL ;;
        *) PRINTER_MODEL="K2 Plus" ;;
    esac

    read -p "Enter local Moonraker URL [default: http://127.0.0.1:7125]: " MOONRAKER_URL
    MOONRAKER_URL="${MOONRAKER_URL:-http://127.0.0.1:7125}"

    VIDEO_ENABLED="false"
    VIDEO_RELAY_URL="http://127.0.0.1:8555/control"

    if [[ "$INSTALL_MODE" == "2" ]]; then
        VIDEO_ENABLED="true"
        VIDEO_RELAY_URL="http://127.0.0.1:8555/control"
    elif [[ "$INSTALL_MODE" == "1" ]]; then
        read -p "Are you running a Remote Video Relay VM on another IP? (y/N): " HAS_REMOTE_VID
        if [[ "$HAS_REMOTE_VID" =~ ^[Yy]$ ]]; then
            VIDEO_ENABLED="true"
            read -p "Enter Remote Video Relay IP (e.g., 192.168.128.230): " REMOTE_VID_IP
            VIDEO_RELAY_URL="http://${REMOTE_VID_IP}:8555/control"
        fi
    fi

    echo -e "${C_YELLOW}[*] Checking Python dependencies for Bridge...${C_RESET}"
    sudo -u $CURRENT_USER python3 -c "import paho.mqtt.client, requests" 2>/dev/null || {
        echo -e "${C_YELLOW}[*] Installing paho-mqtt and requests...${C_RESET}"
        sudo -u $CURRENT_USER pip3 install paho-mqtt requests --break-system-packages &>/dev/null || sudo -u $CURRENT_USER pip3 install paho-mqtt requests &>/dev/null
    }

    fetch_file "bridge/creality_master_bridge.py" "$INSTALL_DIR/bridge/creality_master_bridge.py"

    cat << EOF > "$INSTALL_DIR/bridge/config.json"
{
    "tb_token": "$RANDOM_KEY",
    "jwt_token": "$JWT_TOKEN",
    "broker": "mqtt.crealitycloud.com",
    "moonraker_url": "$MOONRAKER_URL",
    "printer_name": "$PRINTER_NAME",
    "printer_model": "$PRINTER_MODEL",
    "video_enabled": $VIDEO_ENABLED,
    "video_relay_url": "$VIDEO_RELAY_URL"
}
EOF
    chown -R $CURRENT_USER:$CURRENT_USER "$INSTALL_DIR"

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
    sudo -u $CURRENT_USER "$INSTALL_DIR/video-relay/venv_video/bin/pip" install --upgrade pip &>/dev/null
    sudo -u $CURRENT_USER "$INSTALL_DIR/video-relay/venv_video/bin/pip" install aiortc aiohttp websockets websocket-client requests paho-mqtt "numpy<2" opencv-python-headless &>/dev/null

    echo -e "${C_YELLOW}[*] Downloading official signaling scripts...${C_RESET}"
    curl -sSL "https://raw.githubusercontent.com/crealitycloud/OctoPrint-CrealityCloud-With-Video/master/octoprint_crealitycloud/signaling_channel.py" \
        -o "$INSTALL_DIR/video-relay/signaling_channel.py"

    fetch_file "video-relay/webrtc_manager.py" "$INSTALL_DIR/video-relay/webrtc_manager.py"
    fetch_file "video-relay/webrtc_relay.py" "$INSTALL_DIR/video-relay/webrtc_relay.py"

    cat << EOF > "$INSTALL_DIR/video-relay/video_config.json"
{
    "camera_url": "$CAM_URL",
    "rotation": $ROT_DEG
}
EOF
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
fi

echo -e "\n${C_CYAN}============================================================${C_RESET}"
echo -e "${C_GREEN} [+] INSTALLATION COMPLETE!${C_RESET}"
echo -e "     ${C_CYAN}Bridge Status :${C_RESET} sudo systemctl status creality_bridge.service"
echo -e "     ${C_CYAN}Video Status  :${C_RESET} sudo systemctl status creality_video.service"
echo -e "     ${C_CYAN}Uninstall     :${C_RESET} sudo bash $0 --uninstall"
echo -e "${C_CYAN}============================================================${C_RESET}"