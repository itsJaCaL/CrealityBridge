# 🚀 Creality Cloud Universal Klipper Bridge & WebRTC Video Relay

Connect **any** Klipper/Moonraker 3D printer (Raspberry Pi, BTT CB1/Manta, Sonic Pad, Nebula Pad) to Creality Cloud with full telemetry, remote G-code printing, UI profile unlocks, and ultra-low-latency WebRTC live video.

---

## ✨ Key Features

* **Universal Compatibility:** Native support for Raspberry Pi, BTT CB1, Sonic Pads, and remote Linux servers (Proxmox/TrueNAS VMs).
* **Zero-Latency WebRTC Video:** Powered by a decoupled background scraper and OpenCV that drops stale frames and streams absolute real-time MJPEG video without freezing or lag.
* **Flagship UI Unlocks:** Choose your printer model profile (`K2 Plus`, `K1 Max`, `Ender-3 V3 KE`, `S1 Pro`, or `Custom`) to unlock high-speed controls and features in Creality Cloud.
* **Full Telemetry & Control:** Live temperature monitoring, print progress tracking, file browsing, fan adjustments, LED toggling, and bed leveling routines.

---

## 📋 Step 1: Get Your `.tk` Token

1. Go to the [Creality Cloud Web Platform](https://www.crealitycloud.com) or use the mobile app.
2. Navigate to **Workbench / Devices** ➔ Click **Add Device** ➔ Select **Raspberry Pi / Klipper**.
3. Download the generated device configuration key package.
4. Open the downloaded `.tk` file using any standard text editor (Notepad, TextEdit, Nano) and **copy the entire text string inside**.

---

## ⚡ Step 2: Quick Start Installation

SSH into your Klipper host machine and run the following commands sequentially:

```bash
git clone https://github.com/itsJaCaL/CrealityBridge.git
cd CrealityBridge
chmod +x install.sh
sudo bash install.sh
```

### 🎛️ During the Interactive Installer:
1. **Select your installation mode:**
   * `[1]` **Telemetry & Control Bridge Only** *(Best for Sonic Pads, Nebula Pads, or setups without cameras)*
   * `[2]` **All-in-One:** Bridge + Local WebRTC Video Relay *(Best for Pi 5, Pi 3B+, or BTT CB1 hosts)*
   * `[3]` **Remote Video Relay Node Only** *(Best for a separate Proxmox/TrueNAS VM or home server)*
2. **Paste the contents of your `.tk` file** when prompted.
3. **Confirm your Moonraker API URL** (defaults to `[http://127.0.0.1:7125](http://127.0.0.1:7125)`).
4. **Confirm your camera stream URL or printer IP** if prompted by your chosen mode.

---

## ⚠️ Important Note for Sonic Pad & Nebula Pad Users

Due to firmware lockdowns and the lack of a native C compiler on Creality's official pads, these devices **only support Mode 1 (Telemetry & Control Bridge Only)**. Video streaming remains handled by the pad's native architecture, while Mode 1 successfully bridges your telemetry, UI profile unlocks, and remote printing capabilities to Creality Cloud.

---

## 🛠️ Management & Status

Check your background service logs or remove the bridge anytime using these commands:

* **Check Bridge Status:**
  ```bash
  sudo systemctl status creality_bridge.service
  ```
* **Check Video Relay Status:**
  ```bash
  sudo systemctl status creality_video.service
  ```
* **Complete Clean Uninstallation:**
  ```bash
  bash install.sh --uninstall
  ```

---

## 🖨️ Creality Cloud Official Model Profile Identifiers

When selecting your printer profile during installation or mapping it in your config, these are the primary model strings recognized by Creality Cloud to unlock specific UI controls (such as high-speed Klipper tuning, pressure advance, or standard bed-slinger layouts):

* **Flagship High-Speed Series:**
  * `K2 Plus` *(Unlocks full flagship controls: Flow, Speed, Extrude/Retract, Fans, CFS)*
  * `K1 Max` *(Unlocks full high-speed Klipper controls)*
  * `K1`, `K1C`, `K1 SE`
* **Modern Bed-Slingers:**
  * `Ender-3 V3`, `Ender-3 V3 KE`, `Ender-3 V3 SE`
  * `Ender-3 S1 Pro`, `Ender-3 S1`
* **Classic & Community Machines (Map to standard profiles or Custom):**
  * `Ender-3 V2`, `Ender-3 Pro`, `Ender-3`
  * `Ender-5 Plus`, `Ender-5 S1`
  * `CR-10 SE`, `CR-10 Smart Pro`
* **Custom / DIY / Voron Builds:**
  * Use option `[5]` (Custom Model Name) during the installer to input any custom string (e.g., `Voron 2.4`, `Mercury One.1`, or `Custom CoreXY`) if you want a custom title displayed on your Creality Cloud dashboard.
