# MyHOME (Modernized Fork)
**Version: alpha 0.1**

A modernized and maintained custom integration for Bticino/Legrand MyHOME SCS bus systems in Home Assistant.

This is a fork of the original `anotherjulien/MyHOME` integration, updated with modern Home Assistant APIs, robust connection recovery, and auto-discovery capabilities.

---

## Key Features

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
