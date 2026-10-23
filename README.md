🚀 Creality Cloud Universal Klipper Bridge & WebRTC Video Relay
Connect any Klipper/Moonraker 3D printer (Raspberry Pi, BTT CB1/Manta, Sonic Pad, Nebula Pad) to Creality Cloud with full telemetry, remote G-code printing, UI profile unlocks, and ultra-low-latency WebRTC live video.

✨ Features
Universal Compatibility: Runs on Raspberry Pi, CB1, Sonic Pads, and remote Linux servers (Proxmox/TrueNAS VMs).

Zero-Latency WebRTC Video: Powered by a decoupled background scraper and OpenCV that drops stale frames and streams absolute real-time MJPEG video without freezing or lag.

Flagship UI Unlocks: Choose your printer model profile (K2 Plus, K1 Max, Ender-3 V3 KE, S1 Pro, or Custom) to unlock high-speed controls and features in Creality Cloud.

Full Telemetry & Control: Live temperature monitoring, print progress tracking, file browsing, fan adjustments, LED toggling, and bed leveling routines.

📋 Prerequisites & Step 1: Get Your .tk Token
Go to Creality Cloud Web or use the mobile app.

Navigate to Workbench / Devices -> Click Add Device -> Select Raspberry Pi / Klipper.

Download the generated device configuration key files.

Open the downloaded .tk file using a standard text editor (Notepad, TextEdit, Nano) and copy the entire text string inside.

⚡ Quick Start Installation
SSH into your Klipper host machine and run the following commands:
```bash
git clone https://github.com/itsJaCaL/CrealityBridge.git
cd CrealityBridge
chmod +x install.sh
sudo bash install.sh

During the Interactive Installer:
Select your installation mode:

[1] Telemetry & Control Bridge Only (Best for Sonic Pads, Nebula Pads, or setups without cameras)

[2] All-in-One: Bridge + Local WebRTC Video Relay (Best for Pi 5, Pi 3B+, or BTT CB1 hosts)

[3] Remote Video Relay Node Only (Best for a separate Proxmox/TrueNAS VM or home server)

Paste the contents of your .tk file when prompted.

Confirm your Moonraker API URL (defaults to http://127.0.0.1:7125).

Confirm your camera stream URL or printer IP if prompted by your chosen mode.

⚠️ Note for Sonic Pad & Nebula Pad Users
Due to firmware lockdowns and the lack of a native C compiler on Creality's official pads, these devices only support Mode 1 (Telemetry & Control Bridge Only). Video streaming remains handled by the pad's native architecture, while Mode 1 successfully bridges your telemetry, UI profile unlocks, and remote printing capabilities to Creality Cloud.

🛠️ Management & Status
Check your background service logs or remove the bridge anytime:

Bridge Status: sudo systemctl status creality_bridge.service

Video Relay Status: sudo systemctl status creality_video.service

Complete Uninstallation: bash install.sh --uninstall
