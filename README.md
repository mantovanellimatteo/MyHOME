<p align="center">
  <img src="https://raw.githubusercontent.com/mantovanellimatteo/MyHOME/master/images/logo.png" alt="BTicino MyHOME SCS" width="600">
</p>

<p align="center">
  <a href="https://buymeacoffee.com/mantovanellimatteo" target="_blank">
    <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="60">
  </a>
</p>

# MyHOME (Modernized Fork)
**Version: v1.6.1**

## 🌟 Changelog
* **v1.6.1**: **Renaming Entities Works Again & Cleaner Names!**
  - **Fix – Renaming Entities**: Changing the entity ID of a MyHOME entity from the UI made it disappear until the next restart (`Error adding entity`), because devices were linked to the gateway with the deprecated `via_device`. They now use `via_device_id`, as required by recent Home Assistant versions; the old way is still used on older versions.
  - **Fix – Duplicated Sensor Names**: Power and energy sensors no longer repeat the device name (`sensor.load_line_2_load_line_2_power` → `sensor.load_line_2_power`). Existing entity IDs are kept by Home Assistant; only new entities get the short names.
  - **Fix – False Load Control Warnings**: The gateway echo of the instant power request (`*1200#1*`) is no longer logged as an unhandled load control frame.
  - **Fix – Media Players in YAML**: `media_player` entries in `myhome.yaml` were rejected by the configuration schema; `who` defaults to `16`.
  - **Modern Home Assistant Compatibility**: Removed the deprecated `CONNECTION_CLASS` from the config flow and the deprecated device registry lookups.
* **v1.6.0**: **WHO 16 Speakers Learned from the Bus & Load Control Lines Power!**
  - **WHO 16 Sound System Speakers**: Amplifiers are now created automatically as `media_player` entities the first time they appear on the bus (e.g. when pressed from a wall control), even if no media player is configured: the platform was previously not loaded at all in that case. ON/OFF now use the WHO 16 amplifier frames `*16*3*WHERE##` / `*16*13*WHERE##` (previously `*16*1*` / `*16*0*`), and state is matched on the exact address (zone 1 no longer reacts to zone 11). Source frames (`*16*3*10S##`) no longer create bogus speakers.
  - **Load Control Lines**: Each load control actuator (WHO 18, `7N#0`) gets its own power sensor (`Load line N`). Actuators 1–12 are probed at startup and the ones that answer are created automatically, and kept across restarts; they also use the self-healing instant power broadcast. Lines can still be configured in `myhome.yaml` as `sensor` with `where: '7N'` and `class: power`.
  - **Load Control State Frames**: Frames from the actuators other than power/energy (e.g. a line disconnected by the load control central) are now logged as `[LOAD CONTROL] Unhandled frame ...` warnings, so they can be mapped to line states.
  - **Fix – CEN Event Entities**: CEN pushbutton event entities (v1.5.0) never received presses because of wrong event names; they now fire `short_press` / `short_release` / `long_press` / `long_release`.
  - **Fix – Import YAML**: The "Import YAML" step of the onboarding and options flows failed with `NameError: CONF_PLATFORMS`.
  - **Fix – Auto-learned Devices Saved**: Devices learned from the bus are now really persisted in the integration options (the options dictionary was modified in place, so Home Assistant saw no change).
* **v1.5.0**: **Pushbuttons & Scenarios as Event Entities, Self-Healing Instant Power!**
  - **CEN / CEN+ / Scenario Event Entities**: Every physical pushbutton and scenario is now a native Home Assistant `event` entity, created automatically the first time it is pressed (no YAML needed) and kept across restarts. Use them directly as automation triggers from the UI, with history and logbook. Existing `myhome_cen_event`, `myhome_cenplus_event`, `myhome_scenario_event` bus events and device triggers keep working unchanged.
  - **Self-Healing Instant Power**: The power sensor now re-requests the instant power broadcast right after every reconnection to the gateway and whenever no reading arrives for 10 minutes, on top of the periodic renewal. Automations that periodically call `myhome.start_sending_instant_power` are no longer needed. The service `duration` is now optional (defaults to 255 minutes).
<details>
<summary><b>Previous versions changelog (Click to expand)</b></summary>

* **v1.4.2**: **Connection Robustness & Modern Home Assistant Compatibility!**
  - **No More Leaked Gateway Sessions**: Unloading or reloading the integration (which auto-learning does for every new device) now really stops the listening and sending workers and closes their sockets. Previously each reload left a command session open, until the gateway refused new connections.
  - **Automatic Retry When the Gateway Is Offline at Startup**: If the gateway is unreachable, times out (30 s) or refuses the session, the setup now raises `ConfigEntryNotReady` and Home Assistant retries on its own, instead of failing with a `KeyError`. Wrong passwords start the standard re-authentication flow.
  - **One Bad Frame No Longer Drops the Bus**: Each received frame is processed in isolation; an error is logged and listening continues, instead of disconnecting and reconnecting the whole event session.
  - **Persistent Notifications Fixed**: Replaced the removed `hass.components.persistent_notification` API, which crashed auto-learning (forcing a reconnect) and the `scan_bus` / `export_to_yaml` services.
  - **Services Without `gateway`**: `sync_time`, `send_message`, `scan_bus` and `export_to_yaml` now default to the first loaded gateway (they used to pick an internal flag and fail with "Gateway not found"). Services are removed only when the last gateway is unloaded.
  - **Diagnostic Frames (WHO 1001/1004)**: Fixed a missing import that silently skipped the status request for devices configured by `where` address.
  - **Quieter Logs**: The `[BUS SNIFFER]` trace of every bus frame is now logged at DEBUG instead of WARNING.
* **v1.4.1**: **Actuator Transient Echo Fix & Universal Virtual Positioning!**
  - **Actuator Transient Stop Echo Filter**: Fixed an issue where the initial ~80ms transient stop/switch frame emitted by BTicino actuators upon starting movement prematurely aborted the percentage timer, causing curtains to move all the way to 0% or 100%. The integration now maintains movement tracking and accurately stops at the requested target position.
  - **Universal Virtual Positioning**: Removed hardware bypass that attempted to send OpenWebNet level write frames (`*#2*WHERE*#11#...##`) when `advanced: True` was present in existing configurations. All covers now use the reliable virtual time-based positioning regardless of whether `advanced` is specified in `myhome.yaml`.
* **v1.4.0**: **Native Shutter Percentage Control & "Unknown" State Elimination!**
  - **Virtual Time-Based Positioning (`SET_POSITION`)**: Full native support for cover percentage control (0% to 100%) on standard BTicino shutter actuators (`F411/2`, `F411/4`, `3476`, etc.). Home Assistant now displays an interactive percentage slider and preset buttons (0%, 25%, 50%, 75%, 100%) for all shutters.
  - **Permanently Eliminated "Unknown" ("Sconosciuto") State**: Fixed a fundamental issue where standard BTicino stop frames (`*2*0*WHERE##`) caused Home Assistant to revert cover entities to "Unknown". Covers now accurately retain and reflect their state (`open`, `closed`, `opening`, `closing`) at all times.
  - **State Restoration Across Reboots (`RestoreEntity`)**: Cover entities now remember their exact position and open/closed state across Home Assistant restarts and integration reloads.
  - **Configurable Travel Time**: Added per-device optional configuration (`travel_time`, `open_time`, `close_time`, defaulting to 25.0 seconds).
  - **Bidirectional Physical Wall Switch Sync**: When opening, closing, or stopping shutters from physical in-wall pushbuttons, Home Assistant tracks elapsed travel time and updates the position percentage and state in real time.
* **v1.3.2**: **MHS1 Gateway Stability, OWNSignaling Fix & YAML Import Preservation!**
  - **`OWNSignaling` Crash Loop Fixed**: Fixed `AttributeError: 'OWNSignaling' object has no attribute '_who'` which caused a continuous 1-second crash/reconnect loop whenever protocol signaling frames (ACK, NACK, Nonce, handshake) arrived from gateways (particularly MyHomeServer1).
  - **MHS1 & Embedded Gateway Scan Reliability**: Increased response deadline to 200ms and inter-command spacing to 30ms to accommodate slower embedded gateways like MHS1 on physical 9600-baud SCS buses. Added automatic socket reconnection and recovery during active bus scans.
  - **YAML Metadata Preservation during Onboarding**: When both "Import YAML" and "Auto Scan" are enabled, existing YAML-imported devices (custom entity names, models, manufacturers) are preserved and never overwritten by generic scan names.
* **v1.3.1**: **Critical Gateway Anti-Flooding Fix & Sound Diffusion Upgrades!**
  - **Eliminated Command Queue Flooding & Delay**: Fixed an issue where periodic actuator diagnostic frames (`WHO 1001` / `WHO 1004`) triggered an infinite storm of status queries on unconfigured hardware addresses, causing up to 2-minute delays on light switches.
  - **Entity Validation & 60s Throttling**: Diagnostic status queries now strictly verify that the target device exists in Home Assistant and rate-limit queries to a maximum of once every 60 seconds per device.
  - **Zero Retries for Status Queries**: Failed status requests are dropped immediately instead of blocking the gateway sender queue.
  - **Sound Diffusion Improvements (WHO 22/16)**: Integrated full support for F441 audio matrices, H4562 wall controls with bidirectional sync, 3-frame source switching, FM Tuner metadata tracking (frequencies & presets), and gateway freeze prevention (contributed by @bboykaos).

* **v1.3.0**: **Gateway Diagnostics & Sleek "MyHOME Monitor" Lovelace Card!**
  - **Zero-Overhead Gateway Diagnostics**: Added native diagnostic entities for BTicino gateways:
    - `binary_sensor.<gateway>_connectivity` (tracks active OpenWebNet connection status, IP, port, hardware model, firmware version, session uptime, and reconnection counter).
    - `sensor.<gateway>_configured_devices` (reports total configured devices with attributes breakdown: lights, covers, climate zones, switches, sensors, and scenarios).
    - **100% Passive & Safe**: Zero bus polling, zero pinging, and zero frame flooding — completely safe for legacy and resource-constrained gateways (AM4890, F454, MH200N).
  - **Dedicated "MyHOME Monitor" Custom Lovelace Card**: Built-in, ultra-responsive Lovelace card (`custom:myhome-monitor-card`) featuring gateway status badge, hardware metrics, and interactive device counters grid with dark/light mode support. Automatically registered with Home Assistant frontend.

* **v1.2.0**: **Official BTicino MyHOME SCS Brand Assets & Clean Alpha Packaging!**
  - **Brand & Logo Overhaul**: Added official high-resolution brand assets (`icon.png`, `icon@2x.png`, `logo.png`) featuring authentic BTicino typography, signature orange 'b', and SCS bus emblem.
  - **Native Local Brand Packaging & Clean Alpha Channel**: Packaged official brand icons with 32-bit RGBA smooth anti-aliased transparency inside `custom_components/myhome/brand/` ensuring Home Assistant Core automatically loads and serves custom integration icons under *Settings > Devices & Services* without white square corner artifacts.
  - **HACS Markdown Rendering Fix**: Switched README image URLs to absolute GitHub raw links so logos and screenshots render properly inside HACS.

* **v1.1.0**: **Native Animated Progress Screens!**
  - **Animated Progress Feedback (`async_show_progress`)**: Active bus scanning and passive packet sniffing in the Options menu now run seamlessly as asynchronous background tasks with native animated spinners and live contextual status messages.
  - **Fluid Onboarding Experience**: The Zero-Knowledge setup wizard now displays a progress spinner during the initial automatic SCS bus scan instead of freezing the dialog.
  - **Full Bilingual Localization**: Progress indicators and completion summaries fully translated in Italian (🇮🇹) and English (🇬🇧).

* **v1.0.0**: **Major Release: Zero-Knowledge Wizard & Onboarding Redesign!**
  - **Zero-Knowledge Setup**: Automatic gateway discovery via SSDP. No manual IP or technical details required to get started.
  - **🤓 NERD MODE**: Optional manual configuration switch for advanced users who want to manually specify IP, port, MAC, and gateway model.
  - **One-Click YAML Import**: Seamlessly import your existing `myhome.yaml` configuration into the UI during onboarding or from the Options menu, automatically renaming the old file to `myhome.yaml.old` to prevent conflicts.
  - **Ultra-Fast SCS Bus Discovery**: High-speed active scan (~4-7s) of lights, covers, and climate zones with strict frame matching, buffer draining, and zero ghost devices.
  - **Bilingual Support**: Complete Italian (🇮🇹) and English (🇬🇧) localization for all setup steps, options, and descriptions.

* **v0.5.0**: **New Feature!** Added a "Import from YAML" button to the Config Flow options. Users can now securely migrate all their `myhome.yaml` configuration into the UI with one click. The script automatically converts the entities and renames the YAML file to `myhome.yaml.old` to prevent conflicts.
* **v0.4.12**: **Documentation Update:** Added explicit instructions on how to migrate from the original `anotherjulien/MyHOME` integration without losing entities or configuration.

* **v0.4.11**: **Startup Crash Fix!** Fixed a critical `KeyError: entities` and `KeyError: entity_name` crash during startup on Home Assistant 2024.x when devices were configured via `myhome.yaml`. This was caused by the Voluptuous validation library silently bypassing our schema defaults during dictionary unwrapping. All schemas are now explicitly enforced.
* **v0.4.10**: **Climate HVAC Action Fix!** Fixed a bug where `hvac_action` would constantly show as `idle` when actuator frames were received for zones that support both heating and cooling. Actuator frames (unlike valve frames) do not explicitly specify if they are heating or cooling, so the integration now correctly infers the action from the current `hvac_mode`.
* **v0.4.9**: **Diagnostic Frames Interception Fix!** Fixed a bug where diagnostic frames (`*#1001*` for lights and `*#1004*` for climate) were not properly intercepted because the `OWNd` library parses them into a generic `OWNEvent` object, skipping the raw string interception logic. The integration now correctly handles these objects and queries the gateway for the standard status.
* **v0.4.8**: **Localization Fix!** Fixed an issue where the Home Assistant frontend would fail to render the "Button pressed" string in the dropdown menu with a `MISSING_VALUE` error. Changed the internal context variable from `scenario` to the HA-standard `subtype` to allow the UI to interpolate the string correctly.
* **v0.4.7**: **Trigger Fix Part 2!** Fixed a critical error where `homeassistant.helpers.trigger` was incorrectly used instead of the `event` trigger integration. This caused "Integration does not support device automation triggers" or "no attribute 'async_attach_trigger'" errors in HA Core 2024+ when attempting to render or attach scenario module triggers.
* **v0.4.6**: **Diagnostic Tracing!** Changed all internal device trigger logs from `DEBUG` to `WARNING` to force them to appear in the Home Assistant logs. This allows us to trace exactly where the UI is dropping the scenario triggers.
* **v0.4.5**: **Bulletproof Trigger Fix!** Removed all internal dependencies on `cv.DEVICE_TRIGGER_BASE_SCHEMA` which caused random silent initialization errors depending on the Home Assistant version. Added detailed internal logging to `device_trigger.py` to catch any edge cases in `async_get_triggers`.
* **v0.4.4**: **Critical Fix!** Fixed a silent `ImportError` (`homeassistant.components.homeassistant.triggers`) introduced by API changes in Home Assistant 2024 which caused Scenario Module device triggers to be silently dropped on initialization. Device triggers have been modernized to use the standard core `trigger_helper`.
* **v0.4.3**: **Hotfix!** Fixed an issue introduced by strict schema validation in Home Assistant 2024 where Scenario Modules device triggers would not appear ("No triggers for this device") because they lacked the `metadata` parameter and `DEVICE_TRIGGER_BASE_SCHEMA` extension.
* **v0.4.2**: **Bugfix!** Added interception for diagnostic extended frames (`*#1001` for lights and `*#1004` for climate) which OWNd fails to parse. The integration now catches these frames and immediately queries the gateway for the standard status to ensure Home Assistant stays perfectly in sync.
* **v0.4.1**: **Critical Hotfix!** Fixed a severe issue introduced in v0.4.0 where intercepting Load Management (WHO 3) messages crashed the integration's listening loop due to invalid notification ID characters. This crash caused the integration to rapidly reconnect to the BTicino gateway, effectively creating a Denial of Service (DoS) condition on the bus that prevented the hardware load management unit from successfully sending disconnect commands to appliances.
* **v0.4.0**: **Features Galore!** 
  - **Smooth Transitions**: Native support for fade-in and fade-out transitions on dimmable lights (using software-based stepped dimming).
  - **Load Management Discovery**: Added exploratory support for WHO=3. The integration now intercepts load management events and creates persistent notifications in Home Assistant to aid in mapping specific actuators.
  - **Bugfix**: Fixed an issue where Scenario Modules (stateless devices without entities) were being incorrectly pruned/deleted on every Home Assistant restart.
* **v0.3.2**: **Hotfix!** Fixed an issue where Scenario Module graphic device triggers were not showing up in the Automation UI on Home Assistant versions 2024.2+ due to a deprecated base schema import.
* **v0.3.1**: **Hotfix!** Fixed a critical bug introduced in v0.2.2 where the gateway's MAC address capitalization could cause a `KeyError` during the reception of Instant Power broadcast events, triggering a bootloop/crash of the MyHOME integration.
* **v0.3.0**: **Graphic Device Triggers!** Scenario modules (WHO 0) now automatically register themselves as physical Devices in Home Assistant the first time you press a button. This allows you to create automations natively using the visual UI dropdown menus ("Button 1 pressed", "Button 2 pressed", etc.) instead of writing YAML or listening to raw events!
* **v0.2.2**: **Energy Meters Fix!** Instantaneous Power meters (WHO 18) now automatically and silently renew their broadcast stream every 55 minutes. You no longer need to manually create an automation to call the `start_sending_instant_power` service every hour!
* **v0.2.1**: **Active Scanning Bugfix!** Fixed an issue where the Active Scanning tool would aggressively add non-existent ("ghost") devices to Home Assistant because it interpreted the gateway's syntactic protocol `ACK` as a confirmation of device existence. It now correctly waits for and requires a valid `STATE` frame to confirm a device is actually physically present.
* **v0.2.0**: **New Feature!** Added native support for Bticino Scenario controls (WHO 0). Pressing physical scenario buttons now natively fires the `myhome_scenario_event` in Home Assistant, allowing you to use your scenario wall-plates as remote controls for ANY entity in Home Assistant (Philips Hue, Sonos, generic automations, etc.) without needing to physically reconfigure them as CEN/CEN+ modules!
* **v0.1.1**: **Stable Release Update!** Fixed OptionsFlow `500 Internal Server Error` in HA 2024.12+, fixed `Unknown error` during auto-learning, fixed Cover `lock/unlock` icon mapping issue by assigning `_attr_device_class` properly, and fixed a critical initialization `KeyError` crash for ALL auto-learned devices (Covers, Switches, Lights, Climates, MediaPlayers).
* **v0.1.0**: Official Stable Release! Fixed HACS version tracking.

</details>

## 📖 Introduction

### What is it?
This is a custom Home Assistant integration that acts as a local bridge to Bticino / Legrand MyHome wired home automation (domotic) systems.

### What does it do?
It communicates directly over the local network with your Bticino IP gateway (such as the F454, F453, F452, MH200, MH200N, MH201, MH202, or MyHomeServer1) using the OpenWebNet protocol. It enables Home Assistant to:
- **Control** lights, dimmers, switches, motorized covers/shutters, and sound diffusion (WHO 22) zones/media players.
- **Monitor** temperature sensors, energy/power meters, and binary sensors.
- **Listen** to bus events (such as physical scenario button keypresses) to trigger complex automation routines.

### What is it for?
If you have a wired Bticino MyHome SCS system, this integration brings all your physical devices into Home Assistant for unified control, dashboard visualization, and automation.
- **100% Local & Offline**: It operates entirely within your local network (LAN) with zero cloud dependencies. Your smart home remains fully functional even without an internet connection, keeping it private, secure, and extremely fast.
- **Legacy Hardware Support**: Gives a new lease of life to older Bticino gateways (like the F454) by integrating them with modern smart home tech.
- **Alternative to Cloud Migration**: For gateways like the MyHomeServer1, using this local integration keeps everything local instead of forcing you to migrate your system to the Netatmo cloud-based "Home + Control" API.

---

This is a fork of the original `anotherjulien/MyHOME` integration, updated with modern Home Assistant APIs, robust connection recovery, and auto-discovery capabilities.

---

## Key Features

### 🎵 Sound Diffusion (WHO 16 / WHO 22) Support
WHO 16 amplifiers (address "room + point", e.g. `11`) are created as `media_player` entities the first time they show up on the bus, e.g. after turning them on once from a wall control; ON/OFF send `*16*3*WHERE##` / `*16*13*WHERE##`.

Includes active and passive discovery of sound diffusion zones on your SCS bus, automatically registering them as `media_player` entities in Home Assistant. This allows you to turn fonic points, audio sources, and speaker zones ON and OFF directly from your dashboards and automations.

### ⚡ Load Control Lines (New in v1.6.0)
Every load control actuator (WHO 18, address `7N#0`) gets a `Load line N` power sensor, so besides the total house consumption you can see what each controlled line draws. Actuators 1–12 are probed at startup and created automatically when they answer. Frames about the line state are logged as `[LOAD CONTROL]` warnings, to be decoded in a future version.

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

### 🔘 Pushbuttons & Scenarios as Event Entities (New in v1.5.0)
Every CEN / CEN+ pushbutton and every scenario of a scenario module becomes a native **`event` entity**, created automatically the first time it is used on the bus:
- **CEN**: device `CEN <object>`, one entity per button (`Button 1`, `Button 2`, ...), event types `short_press`, `short_release`, `long_press`, `long_release`.
- **CEN+**: device `CEN+ <object>`, event types `short_press`, `long_press`, `long_release` and the rotations `rotate_cw_slow`, `rotate_cw_fast`, `rotate_ccw_slow`, `rotate_ccw_fast`.
- **Scenarios**: one entity per scenario (`Scenario 1`, ...) on the `Scenario Control <address>` device, event type `pressed`.

A held button reports `long_press` once, not on every repeated frame. In an automation pick the entity with a **State** trigger, or use the `event_type` attribute:
```yaml
triggers:
  - trigger: state
    entity_id: event.cen_21_button_3
    attribute: event_type
    to: short_release
```

### 🎛️ Scenario Control Integration (New in v0.2.0)
You can now use your physical Bticino Scenario Modules (such as the 4-button wall plates) to trigger automations directly in Home Assistant without physically reconfiguring them as CEN/CEN+ modules!
Whenever you press a scenario button, the integration fires a native `myhome_scenario_event` on the Home Assistant bus.

To trigger an automation, use an **Event** trigger in Home Assistant:
- **Event type:** `myhome_scenario_event`
- **Event data:**
  - `scenario`: the number of the scenario button pressed (e.g., 1, 2, 3...)
  - `control_panel`: the address of the scenario module (`where`)

This allows you to control anything (e.g. Philips Hue, Sonos, Zigbee devices) directly from your Bticino wall switches!

#### Example Automation (YAML)
```yaml
alias: "Trigger Philips Hue with Bticino Button 1"
trigger:
  - platform: event
    event_type: myhome_scenario_event
    event_data:
      scenario: 1
      control_panel: "51" # Optional: Specify the module address if you have multiple
action:
  - service: light.toggle
    target:
      entity_id: light.living_room_hue
```

### 📊 MyHOME Monitor Card (Custom Lovelace Card)
The integration comes with a built-in, beautifully styled Lovelace card: **MyHOME Monitor Card** (`custom:myhome-monitor-card`). It displays real-time connection status with animated badge, hardware/network parameters (IP, port, model, firmware, uptime, reconnects), and a clean responsive grid breaking down all configured devices on your SCS bus (Lights, Shutters, Climate, Switches, Sensors, and Scenarios).

<p align="center">
  <img src="https://raw.githubusercontent.com/mantovanellimatteo/MyHOME/master/images/myhome_monitor_card.png" alt="MyHOME Monitor Card" width="480">
</p>

#### Visual Dashboard Editor
You can add it directly from the Home Assistant dashboard UI without writing any code:
1. In any dashboard, click **Add Card**.
2. Search for **MyHOME Monitor Card** in the card picker.
3. Click to insert — it automatically discovers your MyHOME gateway and device statistics!

#### Manual YAML Configuration
If you prefer adding or customizing the card via YAML code:

```yaml
type: custom:myhome-monitor-card
```

#### Customization Options

```yaml
type: custom:myhome-monitor-card
title: "MyHOME Gateway"        # Optional custom title (defaults to gateway name)
show_attributes: true          # Set to false to hide IP, firmware, and uptime section
show_device_counts: true       # Set to false to hide the bus devices grid
gateway_entity: binary_sensor.am4890_gateway_connectivity      # Optional: manual entity override
device_count_entity: sensor.am4890_gateway_configured_devices  # Optional: manual entity override
```

---

## Installation

### Via HACS (Home Assistant Community Store)
1. Go to HACS > **Integrations**.
2. Click the three dots in the top-right corner and select **Custom repositories**.
3. Add the URL of this repository: `https://github.com/mantovanellimatteo/MyHOME` as an **Integration**.
4. Click **Download**.
5. Restart Home Assistant.

### Migrating from the Original Integration (`anotherjulien`)
If you are already using the original `anotherjulien/MyHOME` integration and want to switch to this fork **without losing your entities, historical data, or breaking your automations**, you can do so safely. 
Because this fork maintains the same `DOMAIN` and `unique_id` generation logic, it functions as a drop-in replacement.

**Zero-Problem Migration Steps:**
1. **⚠️ IMPORTANT:** Do **NOT** delete the integration from the *Settings -> Devices & Services* page in Home Assistant. Doing so will permanently wipe your entities!
2. Open **HACS** and go to **Integrations**.
3. Click the three dots in the top-right corner and select **Custom repositories**.
4. Add `https://github.com/mantovanellimatteo/MyHOME` with the category **Integration**.
5. Once the repository is added, click on it and hit **Download**. HACS will simply overwrite the old files in the `custom_components/myhome` folder with the new ones.
6. **Restart** Home Assistant.

Your setup will boot up exactly as it was, but running the new codebase. You can verify the installation by checking for the new `MyHOME` options and discovery menus under *Settings -> Devices & Services*.

---

## Configuration

### Zero-Knowledge Onboarding Wizard
1. In Home Assistant, go to **Settings** > **Devices & Services**.
2. Click **Add Integration** and search for **MyHOME**.
3. Your gateway will be automatically discovered on your local network. Simply select it, or toggle **🤓 NERD MODE** if you wish to configure your IP, port, MAC, and model manually.
4. Choose whether to perform a high-speed active bus scan (~4-7s) and/or import your existing `myhome.yaml` file with one click!

<p align="center">
  <img src="https://raw.githubusercontent.com/mantovanellimatteo/MyHOME/master/images/onboarding_wizard.png" alt="MyHOME Zero-Knowledge Wizard" width="600">
</p>

Once added, you can also click **Configure** on the MyHOME card at any time to access gateway settings, run an Active Scan, or start Passive Sniffing.

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
