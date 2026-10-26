# Creality Cloud Universal Klipper Bridge & WebRTC Video Relay

Connect **any** Klipper/Moonraker 3D printer to the Creality Cloud ecosystem.

Whether you are running a fully custom Raspberry Pi/BTT CB1 build, or a closed-ecosystem device like the **Creality Sonic Pad** or **Nebula Pad**, this bridge translates native Klipper/Moonraker telemetry into Creality's encrypted MQTT protocol. It enables full remote control, slicing, and monitoring from the Creality Cloud mobile app or web browser.

## ✨ Features

* **Universal Compatibility:** Works on any device running Moonraker (Raspberry Pi, CB1, TrueNAS/Proxmox VMs, Hacked Sonic Pad, Nebula Pad).

* **Native WebRTC Video:** Low-latency live video streaming directly to the Creality Cloud app (supports multiple camera rotations and remote video nodes).

* **Intelligent State Management:** Automatically detects and clears Klipper "complete/cancelled/error" ghost states to prevent the app UI from freezing at 100%.

* **Robust Reconnection:** Built-in MQTT watchdog survives network dropouts and automatically resubscribes to command channels without requiring a reboot.

* **Human-Readable Filenames:** Automatically extracts the real model names from cloud G-code payloads instead of displaying random UUID strings.

* **Flagship Controls Unlocked:** Supports target temperatures, speed/flow overrides, LED controls, part cooling fans, and live Z-axis reporting.

## ⚙️ Installation

### Step 1: Get Your Creality Cloud Key (`TB_TOKEN`)

Before installing the script, you need to generate a secure token for your printer.

1. Open the **Creality Cloud** mobile app.

2. Navigate to **Workbench** -> Tap the **+** (Add Device) icon in the top right.

3. Select **Raspberry Pi** (OctoPrint).

4. Go to your Creality cloud on your PC (https://www.crealitycloud.com/en/workbench-beta)

5. Open your Raspberry PI printer.

6. Tap the 3 dots on the top right.

7. Tap **Download Key File** (or copy the key directly if prompted).

8. Open the downloaded `.tk` file with any text editor and copy the long text string. You will paste this into the installer.

### Step 2: Run the Installer

SSH into your Klipper host device (e.g., your Sonic Pad or Raspberry Pi) and run the following commands:

```
git clone https://github.com/itsJaCaL/CrealityBridge.git
cd CrealityBridge
sudo bash install.sh

```

The interactive installer will ask you for:

* Your copied `.tk` token.

* A display name for your printer.

* Your printer profile type (e.g., K1 Max, Ender-3 V3, K2 Plus, etc.).

* Your installation mode (Telemetry Only, All-in-One with Video, or Remote Video Node).

## 🛠️ Service Management

The installer runs the bridge as a background `systemd` service so it starts automatically when your printer boots up.

**Check Bridge Status/Logs:**

```
sudo systemctl status creality_bridge.service
sudo journalctl -u creality_bridge.service -f

```

**Check Video Relay Status/Logs (If installed):**

```
sudo systemctl status creality_video.service
sudo journalctl -u creality_video.service -f

```

**Restart Services:**

```
sudo systemctl restart creality_bridge.service
sudo systemctl restart creality_video.service

```

## 🗑️ Uninstallation

To cleanly remove the bridge, video relay, and all background services, simply run the installer with the uninstall flag:

```
cd ~/CrealityBridge
sudo bash install.sh --uninstall

```

## 📝 License

This project is provided as-is for the 3D printing community. Please ensure you secure your `config.json` files, as they contain your private connection tokens.