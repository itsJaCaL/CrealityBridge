# Creality Cloud Universal Klipper Bridge & WebRTC Video Relay

Connect **any** Klipper/Moonraker 3D printer to the Creality Cloud ecosystem.

Whether you are running a fully custom Raspberry Pi/BTT CB1 build, or a closed-ecosystem device like the **Creality Sonic Pad** or **Nebula Pad**, this bridge translates native Klipper/Moonraker telemetry into Creality Cloud's MQTT protocol. It enables full remote control, slicing, and monitoring from the Creality Cloud mobile app or web browser.

## ✨ Features

* **Universal Compatibility:** Works on any device running Moonraker (Raspberry Pi, CB1, TrueNAS/Proxmox VMs, Hacked Sonic Pad, Nebula Pad).

* **Native WebRTC Video:** Low-latency live video streaming directly to the Creality Cloud app (supports multiple camera rotations and remote video nodes).

* **Intelligent State Management:** Automatically clears Klipper "complete/cancelled" ghost states to prevent the app UI from freezing at 100%. Errors are left in place so you can read them in Mainsail/Fluidd (configurable, see below).

* **Robust Reconnection:** Built-in MQTT watchdog survives network dropouts and automatically resubscribes to command channels without requiring a reboot.

* **Human-Readable Filenames:** Automatically extracts the real model names from cloud G-code payloads instead of displaying random UUID strings.

* **Flagship Controls Unlocked:** Supports target temperatures, speed/flow overrides, LED controls, part cooling fans, and live Z-axis reporting.

## ⚙️ Installation

### Step 1: Add the device in the Creality Cloud app

1. Open the **Creality Cloud** mobile app.
2. Navigate to **Workbench** -> tap the **+** (Add Device) icon in the top right.
3. Select **Raspberry Pi** (OctoPrint).

That is all you need to do by hand. The installer signs you in to Creality Cloud, finds the printer, and
fetches its key automatically. You pick how to sign in:

* **Web browser** (works with Google, Apple, Facebook, X or email accounts): the installer prints a link.
  Open it on any computer or phone and sign in. The browser then shows a "can't reach localhost" page; that
  is expected. Copy the address from the address bar and paste it back into the installer.
* **Email and password**, typed once at a hidden prompt.

Your password is never saved; only the tokens are written to `config.json`.

> Prefer the old way? Choose option 2 in the installer to paste a `.tk` key file instead.

### Step 2: Run the Installer

SSH into your Klipper host device (e.g., your Sonic Pad or Raspberry Pi) and run the following commands:

```
git clone https://github.com/itsJaCaL/CrealityBridge.git
cd CrealityBridge
sudo bash install.sh

```

The interactive installer will ask you for:

* How to sign in (web browser, email and password, or a pasted `.tk` key file). On a reinstall you can keep the existing sign-in.

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

## 🔑 Re-authenticating

The key fetched at install time is short-lived (about a week), but the MQTT token it provisions is what the bridge keeps using. If the printer ever shows offline in the Creality app, sign in again:

```
python3 ~/CrealityBridge/bridge/creality_login.py --config ~/CrealityBridge/bridge/config.json
sudo systemctl restart creality_bridge.service
```

## 🔧 Optional Settings (`bridge/config.json`)

Edit these and restart the bridge (`sudo systemctl restart creality_bridge.service`). Reinstalling keeps them.

| Key | Default | What it does |
| --- | --- | --- |
| `led_on_gcode` / `led_off_gcode` | `""` (do nothing) | G-code for the app's LED button, e.g. `SET_LED LED=chamber WHITE=1` or `SET_PIN PIN=caselight VALUE=1`. |
| `auto_clear_seconds` | `8` | Seconds after a print completes or is cancelled before the bridge resets Klipper's print state for the app. `-1` turns auto-clear off. |
| `auto_clear_errors` | `false` | Also auto-clear Klipper `error` states. |
| `mqtt_port` / `mqtt_tls` | `1883` / `false` | Creality's broker currently only offers plain MQTT on 1883, so commands and the device token are not encrypted in transit. These exist in case TLS becomes available. |

### Video relay security

The relay's `/control` endpoint (port 8555) starts and stops the stream. In the All-in-One install it only listens on `127.0.0.1`.
A Remote Video Relay node (mode 3) listens on the network and requires a shared secret: its installer prints the secret,
and you enter it when installing the bridge (mode 1, remote relay). Both are stored as `control_secret` in
`video-relay/video_config.json` and `video_relay_secret` in `bridge/config.json`.

## 🗑️ Uninstallation

To cleanly remove the bridge, video relay, and all background services, simply run the installer with the uninstall flag:

```
cd ~/CrealityBridge
sudo bash install.sh --uninstall

```

The uninstaller offers to remove the 2 GB `/swapfile` the video installer creates on low-RAM boards (answer No if the swapfile is your own).
The `paho-mqtt` and `requests` pip packages are left installed because other tools may use them; remove them with `pip3 uninstall paho-mqtt requests` if you like.

## 📝 License

Licensed under the [GNU General Public License v3.0](LICENSE). This project is provided as-is for the 3D printing community, with no warranty.

`config.json` and `video-relay/token_cache.json` contain your private connection tokens. The installer creates them readable only by your user (mode 600); keep them that way, and don't share them or commit them.