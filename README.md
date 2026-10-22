# Creality Cloud Universal Klipper Bridge & WebRTC Video Relay

Connect **any** Klipper/Moonraker 3D printer (Raspberry Pi, BTT CB1/Manta, Sonic Pad, Nebula Pad) to Creality Cloud with full telemetry, remote G-code slicing/printing, human-readable filenames, flagship UI controls, and low-latency WebRTC live video.

## Step 1: Get Your Creality Cloud Key (`TB_TOKEN`)
1. Open the **Creality Cloud** mobile app.
2. Tap **+** (Add Device) -> **Raspberry Pi** (OctoPrint).
3. Copy the generated **Key** (ThingsBoard Token).

## Step 2: Run the One-Line Installer
SSH into your Klipper host (or remote video server) and run:

```bash
git clone https://github.com/itsJaCaL/CrealityBridge.git
cd CrealityBridge
bash install.sh
