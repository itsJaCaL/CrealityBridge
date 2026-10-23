# 🚀 Creality Cloud Universal Klipper Bridge & WebRTC Video Relay

Connect **any** Klipper/Moonraker 3D printer (Raspberry Pi, BTT CB1/Manta, Sonic Pad, Nebula Pad) to Creality Cloud with full telemetry, remote G-code printing, UI profile unlocks, and ultra-low-latency WebRTC live video.

---

## ✨ Features
* **Universal Compatibility:** Runs on Raspberry Pi, CB1, Sonic Pads, and remote Linux servers (Proxmox/TrueNAS VMs).
* **Zero-Latency WebRTC Video:** Decoupled background scraper that drops stale frames and streams absolute real-time video without freezing.
* **Flagship UI Unlocks:** Choose your printer model profile (K2 Plus, K1 Max, Ender-3 V3 KE, S1 Pro) to unlock high-speed controls in Creality Cloud.
* **Full Telemetry & Control:** Live temperature monitoring, print progress tracking, fan adjustments, LED toggling, and bed leveling routines.

---

## 📋 Prerequisites & Step 1: Get Your `.tk` Token
1. Go to [Creality Cloud Web](https://www.crealitycloud.com) or use the mobile app.
2. Navigate to **Workbench / Devices** -> Click **Add Device** -> Select **Raspberry Pi / Klipper**.
3. Download the generated device configuration key files.
4. Open the downloaded `.tk` file (e.g., `rasp_pie_credential.tk`) using a standard text editor (Notepad, TextEdit, Nano) and copy the entire text string inside.

---

## ⚡ Quick Start Installation
SSH into your Klipper host machine and run the following commands:

```bash
git clone https://github.com/itsJaCaL/CrealityBridge.git
cd CrealityBridge
chmod +x install.sh
sudo bash install.sh
