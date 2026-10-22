#!/bin/bash
set -e

# ==============================================================================
# Creality Cloud Universal Klipper Bridge & WebRTC Video Relay Installer
# ==============================================================================

# Replace this URL with your own GitHub raw URL once you upload the repo!
REPO_RAW_URL="https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/creality-klipper-bridge/main"

CURRENT_USER="${USER:-$(whoami)}"
USER_HOME=$(eval echo "~$CURRENT_USER")
INSTALL_DIR="$USER_HOME/creality-klipper-bridge"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "============================================================"
echo "   Creality Cloud Universal Klipper Bridge & Video Relay    "
echo "============================================================"

if [[ "$1" == "--uninstall" ]]; then
    echo "[*] Uninstalling services and removing files..."
    sudo systemctl stop creality_bridge.service 2>/dev/null || true
    sudo systemctl disable creality_bridge.service 2>/dev/null || true
    sudo rm -f /etc/systemd/system/creality_bridge.service
    sudo systemctl stop creality_video.service 2>/dev/null || true
    sudo systemctl disable creality_video.service 2>/dev/null || true
    sudo rm -f /etc/systemd/system/creality_video.service
    sudo systemctl daemon-reload
    rm -rf "$INSTALL_DIR"
    echo "[+] Uninstalled cleanly!"
    exit 0
fi

echo ""
echo "Select Installation Mode:"
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

# Helper to copy from local clone if available, otherwise download from GitHub
fetch_file() {
    local rel_path="$1"
    local dest_path="$2"
    if [[ -f "$SCRIPT_DIR/$rel_path" ]]; then
        cp "$SCRIPT_DIR/$rel_path" "$dest_path"
    else
        curl -sSL "$REPO_RAW_URL/$rel_path" -o "$dest_path"
    fi
}

# ==============================================================================
# STEP A: Configure Telemetry & Control Bridge (Modes 1 & 2)
# ==============================================================================
if [[ "$INSTALL_MODE" == "1" || "$INSTALL_MODE" == "2" ]]; then
    echo ""
    echo "--- Bridge Configuration ---"
    while [[ -z "$TB_TOKEN" ]]; do
        read -p "Enter your Creality Cloud Key (TB_TOKEN): " TB_TOKEN
    done

    read -p "Enter a display name for your printer [default: Klipper-Printer]: " PRINTER_NAME
    PRINTER_NAME="${PRINTER_NAME:-Klipper-Printer}"

    echo ""
    echo "Select Printer Model Profile (Controls which UI features unlock in Creality Cloud):"
    echo "  [1] K2 Plus       (Unlocks ALL flagship controls: Flow, Speed, Extrude/Retract, Fans, CFS)"
    echo "  [2] K1 Max        (Unlocks full high-speed Klipper controls: Flow, Speed, Extrude/Retract)"
    echo "  [3] Ender-3 V3 KE (Standard Klipper bed-slinger profile)"
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

    echo "[*] Checking Python dependencies for Bridge..."
    python3 -c "import paho.mqtt.client, requests" 2>/dev/null || {
        echo "[*] Installing paho-mqtt and requests..."
        pip3 install paho-mqtt requests --break-system-packages 2>/dev/null || pip3 install paho-mqtt requests
    }

    fetch_file "bridge/creality_master_bridge.py" "$INSTALL_DIR/bridge/creality_master_bridge.py"

    cat << EOF > "$INSTALL_DIR/bridge/config.json"
{
    "tb_token": "$TB_TOKEN",
    "broker": "mqtt.crealitycloud.com",
    "moonraker_url": "$MOONRAKER_URL",
    "printer_name": "$PRINTER_NAME",
    "printer_model": "$PRINTER_MODEL",
    "video_enabled": $VIDEO_ENABLED,
    "video_relay_url": "$VIDEO_RELAY_URL"
}
EOF

    echo "[*] Registering creality_bridge.service..."
    sudo tee /etc/systemd/system/creality_bridge.service > /dev/null << EOF
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

    sudo systemctl daemon-reload
    sudo systemctl enable creality_bridge.service
    sudo systemctl restart creality_bridge.service
    echo "[+] Creality Master Bridge installed and started!"
fi

# ==============================================================================
# STEP B: Configure WebRTC Video Relay (Modes 2 & 3)
# ==============================================================================
if [[ "$INSTALL_MODE" == "2" || "$INSTALL_MODE" == "3" ]]; then
    echo ""
    echo "--- WebRTC Video Relay Configuration ---"

    DEFAULT_CAM_URL="http://127.0.0.1/webcam/?action=stream"
    if [[ "$INSTALL_MODE" == "3" ]]; then
        read -p "Enter your Klipper Printer's IP address (e.g., 192.168.50.157): " PRINTER_IP
        DEFAULT_CAM_URL="http://${PRINTER_IP}/webcam/?action=stream"
    else
        # Try auto-detecting local stream path from Moonraker
        DETECTED_STREAM=$(curl -s "http://127.0.0.1:7125/server/webcams/list" | grep -o '"/webcam/[^"]*"' | head -n 1 | tr -d '"')
        if [[ -n "$DETECTED_STREAM" ]]; then
            DEFAULT_CAM_URL="http://127.0.0.1${DETECTED_STREAM}"
        fi
    fi

    read -p "Enter Camera Stream URL [default: $DEFAULT_CAM_URL]: " CAM_URL
    CAM_URL="${CAM_URL:-$DEFAULT_CAM_URL}"

    echo ""
    echo "Select Camera Rotation:"
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

    # Ensure 2GB Swap on low-RAM systems (<2.5GB RAM) to prevent OOM Kills
    TOTAL_MEM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    TOTAL_SWAP_KB=$(grep SwapTotal /proc/meminfo | awk '{print $2}')
    if [[ "$TOTAL_MEM_KB" -lt 2500000 && "$TOTAL_SWAP_KB" -lt 1000000 ]]; then
        echo "[*] Low RAM detected with minimal swap. Creating 2GB safety swapfile..."
        if [[ ! -f /swapfile ]]; then
            sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
            sudo chmod 600 /swapfile
            sudo mkswap /swapfile
            sudo swapon /swapfile
            echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab > /dev/null
        fi
    fi

    echo "[*] Installing system packages for WebRTC & PyAV..."
    sudo apt-get update
    sudo apt-get install -y python3-venv python3-pip python3-dev ffmpeg \
        libavdevice-dev libavfilter-dev libopus-dev libvpx-dev pkg-config \
        libsrtp2-dev libssl-dev libffi-dev curl

    echo "[*] Building Python virtual environment (venv_video)..."
    python3 -m venv "$INSTALL_DIR/video-relay/venv_video"
    source "$INSTALL_DIR/video-relay/venv_video/bin/activate"
    pip install --upgrade pip
    pip install aiortc aiohttp websockets websocket-client requests paho-mqtt "numpy<2"

    echo "[*] Downloading official signaling channel and relay scripts..."
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

    echo "[*] Registering creality_video.service..."
    sudo tee /etc/systemd/system/creality_video.service > /dev/null << EOF
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

    sudo systemctl daemon-reload
    sudo systemctl enable creality_video.service
    sudo systemctl restart creality_video.service
    echo "[+] Creality WebRTC Video Relay installed and started!"
fi

echo ""
echo "============================================================"
echo " [+] INSTALLATION COMPLETE!"
echo "     Bridge Status : sudo systemctl status creality_bridge.service"
echo "     Video Status  : sudo systemctl status creality_video.service"
echo "     Uninstall     : bash install.sh --uninstall"
echo "============================================================"
