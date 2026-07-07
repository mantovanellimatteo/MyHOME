# MyHOME (Modernized Fork)
**Version: v0.1.0-alpha.6**

## 🆕 Changelog
* **v0.1.0-alpha.6**: Fixed `Unknown error occurred` during Sniffing and Scanning by correctly importing missing `CONF_ENTITY`.
* **v0.1.0-alpha.5**: Fixed OptionsFlow `500 Internal Server Error` in HA 2024.12+ (removed config_entry initialization parameter).
* **v0.1.0-alpha.4**: Added full native support for **WHO 16 / WHO 22** Audio Systems (Sources, Amplifiers, Matrices) and a persistent background **Auto-learning (sniffing) mode**.

## 📖 Introduction

### What is it? (Cos'è?)
This is a custom Home Assistant integration that acts as a local bridge to Bticino / Legrand MyHome wired home automation (domotic) systems.

### What does it do? (Cosa fa?)
It communicates directly over the local network with your Bticino IP gateway (such as the F454, F453, F452, MH200, MH200N, MH201, MH202, or MyHomeServer1) using the OpenWebNet protocol. It enables Home Assistant to:
- **Control** lights, dimmers, switches, motorized covers/shutters, and sound diffusion (WHO 22) zones/media players.
- **Monitor** temperature sensors, energy/power meters, and binary sensors.
- **Listen** to bus events (such as physical scenario button keypresses) to trigger complex automation routines.

### What is it for? (A cosa serve?)
If you have a wired Bticino MyHome SCS system, this integration brings all your physical devices into Home Assistant for unified control, dashboard visualization, and automation.
- **100% Local & Offline**: It operates entirely within your local network (LAN) with zero cloud dependencies. Your smart home remains fully functional even without an internet connection, keeping it private, secure, and extremely fast.
- **Legacy Hardware Support**: Gives a new lease of life to older Bticino gateways (like the F454) by integrating them with modern smart home tech.
- **Alternative to Cloud Migration**: For gateways like the MyHomeServer1, using this local integration keeps everything local instead of forcing you to migrate your system to the Netatmo cloud-based "Home + Control" API.

---

This is a fork of the original `anotherjulien/MyHOME` integration, updated with modern Home Assistant APIs, robust connection recovery, and auto-discovery capabilities.

---

## Key Features

### 🎵 Sound Diffusion (WHO 22) Support
Includes active and passive discovery of sound diffusion zones on your SCS bus, automatically registering them as `media_player` entities in Home Assistant. This allows you to turn fonic points, audio sources, and speaker zones ON and OFF directly from your dashboards and automations.

### 🔌 Robust Connection Recovery
The connection listener and command sessions are now wrapped in resilient supervisors with **exponential backoff reconnection**. If the gateway drops the connection due to network issues or session limits, the integration automatically reconnects without requiring a Home Assistant reboot or manual integration reload.

### 📁 Optional YAML Configuration
Unlike the legacy integration, `myhome.yaml` is now **entirely optional**. You can configure all your devices directly through the Home Assistant Options Flow UI or let the discovery engine do it for you. 
*(If you prefer text configuration, `myhome.yaml` is still fully supported and resolved dynamically using the Home Assistant config path).*

### 🔍 Active Bus Discovery
You can scan your entire SCS bus for active actuators directly from the UI.
1. Go to **Settings** > **Devices & Services** > **MyHOME**.
2. Click **Configure** and choose **Scan Bus for Devices (Active)**.
3. The integration will query addresses `11-99` for lights and covers, and `1-99` for climate zones, auto-configuring every responsive device in ~10 seconds.

### 🎙️ Passive Bus Sniffing (Discovery by Keypress)
If you don't know your addresses, you can sniff them passively:
1. Under **Configure**, choose **Sniff Bus for Keypresses (Passive)**.
2. Select a duration (e.g., 60 seconds).
3. Walk around your house and physically press the buttons on the wall. The integration will capture the packets and register the devices instantly!

### 📥 Configuration Export Services
Easily move between UI and YAML configurations using built-in services:
- `myhome.scan_bus`: Scans the bus and exports discovered devices to `myhome_discovered.yaml`.
- `myhome.export_to_yaml`: Exports all your UI-configured devices to `myhome_exported.yaml` for backup or manual editing.

---

## Installation

### Via HACS (Home Assistant Community Store)
1. Go to HACS > **Integrations**.
2. Click the three dots in the top-right corner and select **Custom repositories**.
3. Add the URL of this repository: `https://github.com/mantovanellimatteo/MyHOME` as an **Integration**.
4. Click **Download**.
5. Restart Home Assistant.

---

## Configuration

1. In Home Assistant, go to **Settings** > **Devices & Services**.
2. Click **Add Integration** and search for **MyHOME**.
3. Fill in your gateway's IP address, port (`20000`), and your OpenWebNet password.
4. Once added, click **Configure** on the MyHOME card to access gateway settings, run an Active Scan, or start Passive Sniffing.

### YAML Configuration (Optional)
If you wish to configure devices manually using YAML, create a file named `myhome.yaml` in your Home Assistant configuration directory (e.g., `/config/` or `/homeassistant/` depending on your setup):

```yaml
myhome_gateway:
  mac: "00:03:50:XX:XX:XX" # Replace with your gateway's MAC address
  light:
    kitchen_light:
      who: "1"
      where: "12"
      name: "Kitchen Light"
      dimmable: false
  cover:
    living_room_shutter:
      who: "2"
      where: "34"
      name: "Living Room Shutter"
```
For more advanced parameters, please refer to the [original wiki documentation](https://github.com/anotherjulien/MyHOME/wiki).
