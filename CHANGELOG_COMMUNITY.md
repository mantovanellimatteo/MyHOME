# BTicino MyHome Modernized - Community Changelog & Feature Guide

This document provides a detailed overview of the technical architecture, new features, and stability optimizations integrated into the **MyHome** custom component for **Home Assistant**, building upon the official foundation by **mantovanellimatteo** and expanding it with the full advanced sound diffusion ecosystem, F500 FM RDS tuner with station logos, multi-room Dynamic Proxy, and bus anti-flooding fixes.

---

## Table of Contents
1. [Features Derived from GreenGrassBlueOcean & Architectural Improvements](#1-features-derived-from-greengrassblueocean--architectural-improvements)
2. [Decoder Pool (Dynamic Proxy) Configuration Guide](#2-decoder-pool-dynamic-proxy-configuration-guide)
3. [Audio & Sound Diffusion Architecture (WHO 22 / WHO 16)](#3-audio--sound-diffusion-architecture-who-22--who-16)
4. [F500 FM Radio Tuner, RDS & Automatic Logo Deployment](#4-f500-fm-radio-tuner-rds--automatic-logo-deployment)
5. [Single-Tap Fast Turn-Off](#5-single-tap-fast-turn-off)
6. [Intercom Integration & Ducking Events (`myhome_intercom_event`)](#6-intercom-integration--ducking-events-myhome_intercom_event)
7. [Gateway Stability, Anti-Flooding & Command Socket Protections](#7-gateway-stability-anti-flooding--command-socket-protections)
8. [Active Bus Scanning for Sound Diffusion (WHO 22)](#8-active-bus-scanning-for-sound-diffusion-who-22)
9. [Zero-Traffic Smart Reconnect & Synchronous Radio Wake-Up](#9-zero-traffic-smart-reconnect--synchronous-radio-wake-up)
10. [Formal Alignment with OpenWebNet Specifications (Verified via `openwebnet-mcp`)](#10-formal-alignment-with-openwebnet-specifications-verified-via-openwebnet-mcp)
11. [Physical Keypress Detection on H4562 In-Wall Modules & Global Services](#11-physical-keypress-detection-on-h4562-in-wall-modules--global-services)

---

## 1. Features Derived from GreenGrassBlueOcean & Architectural Improvements

Inherited from the pioneering work of the *GreenGrassBlueOcean* project, we adopted the core principles of the **Dynamic Proxy Multi-Room** architecture while introducing several key architectural evolutions:

### A. Dynamic Proxy Decoder Pool (`decoder_pool.py`)
* **Concept**: In BTicino home automation setups, modern media streamers (e.g., Amazon Echo Dot, WiiM Mini, Linkplay, Raspberry HiFiBerry) are often fewer in number than the wired rooms equipped with sound diffusion (for example, 1 or 2 streamers connected to AUX 2 and AUX 3 inputs of an F441 matrix to serve 5 or more zones).
* **Operation**: When a room (e.g., Bedroom or Bathroom) starts media playback (Spotify, Music Assistant, radio stream URL, or `play_media` service), the integration:
  1. Requests an available decoder from `DecoderPool`.
  2. Automatically switches the physical BTicino audio matrix to the AUX input corresponding to the assigned decoder.
  3. Powers on the room's amplifier zone.
  4. Wakes up the external decoder if it was in standby or powered off.
  5. Forwards the media stream and metadata to the decoder.
  6. When playback stops or the room is turned off, the decoder is released back to the pool for other rooms.

### B. Architectural Improvements Over the Original Implementation
1. **Non-Linear Volume Rescaling & Gain Staging**:
   - In BTicino hardware, volume is discrete with **31 steps** (`1` = lowest audible, `31` = maximum volume).
   - In Home Assistant, volume is a continuous float from `0.0` to `1.0`.
   - Introduced a logarithmic compensation curve with a **configurable Pre-Gain** parameter: prevents external streamers from clipping/saturating the analog input of the BTicino matrix and ensures low volume levels remain clearly audible.
2. **Instant Bidirectional Synchronization**:
   - Volume adjustments from physical BTicino in-wall rocker switches immediately update the Home Assistant UI slider without causing feedback loops.
3. **Graceful Fallback & Availability Handling**:
   - If all AUX sources are busy, the system clearly alerts the user rather than blocking or stalling the OpenWebNet bus.

---

## 2. Decoder Pool (Dynamic Proxy) Configuration Guide

The integration features a guided **two-step configuration wizard** in Options Flow to route external streamers (Amazon Echo Dot, WiiM, Arylic, Linkplay, HiFiBerry, etc.) connected to the AUX inputs of the BTicino audio matrix (F441/F441M):

1. In Home Assistant, navigate to **Settings** ➔ **Devices & Services** ➔ **MyHome (Modernized)**.
2. Click **Configure** (Options Flow) and select **Configure Streaming Decoders (Dynamic Proxy)**.
3. **Step 1: Streamer Routing Mode (`decoders`)**:
   * **Shared Source (Multi-Room / Common Input)**: Multiple rooms dynamically share available external decoders. When a room starts streaming (Spotify, cloud radio, TTS, Music Assistant), the first free AUX input is dynamically claimed and automatically released when playback stops.
   * **Exclusive Matrix (1 Streamer per Room)**: Dedicated point-to-point mapping, where specific external decoders are exclusively bound to designated amplifier zones.
4. **Step 2: Streamer Inputs & Pre-Gain Staging (`decoders_slots`)**:
   Up to **4 configurable slots** matching matrix hardware inputs (AUX 1–4):
   * **Decoder Slot N (`media_player`)**: Select the Home Assistant media player entity representing the hardware streamer.
   * **BTicino Source Slot N (1–4)**: Select the corresponding physical AUX input on the audio matrix (e.g., Source `2` for AUX 2, Source `3` for AUX 3).
   * **Pre-gain Slot N (0–50% Anti-Hiss Offset)**: Configurable analog gain staging calibration. Prevents DAC background hiss on sensitive in-wall speakers while ensuring external volume sliders operate comfortably without clipping or saturation.
5. Click **Submit**: The decoder pool is dynamically reloaded in real time without requiring a Home Assistant restart.

---

## 3. Audio & Sound Diffusion Architecture (WHO 22 / WHO 16)

* **H4562 Flush-Mounted In-Wall Amplifiers (WHO 22)**: Full state management (power, volume, and source selection) for each wired zone (OpenWebNet zone addresses in the format `3#Environment#Point`).
* **Stereo and Single-Channel Sound Systems (WHO 16)**: Backward-compatible support for standard BTicino source selection commands.
* **F441 Audio Matrix**: Verified 3-telegram asynchronous switching sequence ensuring reliable channel opening and stereo routing.

---

## 4. F500 FM Radio Tuner, RDS & Automatic Logo Deployment

The integration natively supports the centralized BTicino **F500** FM tuner module (address `2#1`):

1. **Frequency & Preset Tracking**:
   * Intercepts transmitted frequencies in real time in MHz (e.g., `*#22*5#2#1*5*1*10150##` ➔ `101.5 MHz`).
   * Recognizes stored presets from **P1** to **P5** (`*#22*2#1*6*Preset##`).
2. **Auto-Wake on Radio Presets**:
   * If a room amplifier is off or set to an AUX input, tapping an FM preset (P1–P5) automatically turns on the zone amplifier, switches the matrix to Tuner (`1`), and tunes to the station in a single operation.
3. **National & Regional RDS Station Catalog (`radio_catalog.py`)**:
   * Automatic mapping of FM frequencies to station metadata (RDS, Radio Deejay, RTL 102.5, Radio 105, Radio Nostalgia, Studio Più, etc.).
   * Dynamic calculation of `media_title`, `media_artist` (e.g., `Preset P5 • FM Stereo`), and `radio_stazione` attributes.
4. **Bundling and Auto-Deployment of 109 Logos Out-of-the-Box**:
   * **Zero manual configuration**: **109 graphic assets** (107 transparent PNG station logos + Amazon and Spotify streaming cover art) are bundled directly inside the `logos/` directory of the integration.
   * On component startup (`_async_ensure_radio_logos` in `__init__.py`), missing files are automatically deployed in the background to `/config/www/loghi_radio/`, preserving any existing user customizations.
   * **Configurable Web Path (`radio_logos_path`)**: FM tuner options allow setting a custom static URL path (default: `/local/loghi_radio`).
   * **Dynamic Cache Reloading**: Saving integration options clears and reloads the logo cache (`RadioCatalog.clear_cache()`), instantly updating entity metadata.

---

## 5. Single-Tap Fast Turn-Off

On BTicino installations with F441 matrices and H4562 amplifiers, sending the simple command `*22*0*WHERE##` often required two taps because it only unlinked the active source or left the amplifier in pre-standby.

We introduced an **atomic combined power-off sequence**:
1. Source disconnection telegram: `*22*1#4#0*WHERE##`
2. Calibrated hardware delay of **80 milliseconds** allowing the SCS bus to process the frame.
3. Amplifier hardware standby telegram: `*22*0#4#0*WHERE##`

**Result**: The speaker turns off instantly and cleanly on the first tap from Home Assistant, without audio pops and without requiring a second press.

---

## 6. Intercom Integration & Ducking Events (`myhome_intercom_event`)

The integration monitors OpenWebNet bus traffic for intercom and sound diffusion interactions:
* **Audio Ducking on Incoming Calls**:
  When an incoming door call or intercom conversation begins, the BTicino system temporarily ducks the volume (`WHO 22` Dimension `12`). The integration intercepts this frame and fires a **`myhome_intercom_event`** on the Home Assistant event bus with payload `event: "ducking_start"`.
* **Volume Restore**:
  Upon conversation termination, `event: "ducking_end"` is dispatched.
* **Automation Opportunities**: Enables automations to pause Apple TVs, mute external media players, or flash smart lights without extra hardware.

---

## 7. Gateway Stability, Anti-Flooding & Command Socket Protections

We aligned the component with upstream releases **v1.3.1**, **v1.3.2**, **v1.4.0**, and **v1.4.1** by Matteo Mantovanelli, and introduced essential socket protections:

### A. Actuator Diagnostic Anti-Flooding (from v1.3.1) - Double-Click Light Fix
* **Original Issue**: Periodic broadcast frames from actuators (`WHO 1001` for lights and `WHO 1004` for HVAC) queued dozens of state queries for non-existent channels. User commands were delayed by up to 2 minutes or ignored on first click.
* **Resolution**:
  1. `_is_configured_device`: State queries are only sent for devices actually configured in Home Assistant.
  2. Rate limiting of **60 seconds** maximum per address.
  3. **Zero retries** for diagnostic status queries: failed requests are dropped immediately to avoid stalling user commands.

### B. `OWNSignaling` Crash Loop Resolution (from v1.3.2)
* Low-level signaling frames (ACK `*#*1##`, NACK `*#*0##`, SHA tokens) received on the monitor channel are filtered at the ingress stage, preventing the fatal `AttributeError: 'OWNSignaling' object has no attribute '_who'` that caused the socket to disconnect and reconnect every second.

### C. Optimized SCS Bus Timing for MyHomeServer1 (from v1.3.2)
* Scan timeout adjusted to 200ms with a 30ms inter-command pause, ensuring reliable communication on the 9600-baud SCS bus.

### D. Native Percentage Cover Control (from v1.4.0 & v1.4.1)
* Precise virtual run-time calculation for covers/shutters (0-100%) with debounce filtering against transient actuator frames.

### E. Command Socket Timeout Workaround (`ownd/connection.py`)
* **Problem**: BTicino gateways silently terminate command sessions after ~2 minutes of inactivity. Without a read timeout, Python's `readuntil()` would hang indefinitely waiting for an ACK, blocking all subsequent commands.
* **Solution**: Applied an `asyncio.wait_for(..., timeout=3.0)` on `readuntil()` within the `send()` method. If the gateway fails to acknowledge within 3 seconds, the stale socket is closed and re-established within 50ms, immediately retrying the command.
* **Removal of Unsupported Query `*#22*0##`**: Eliminated the legacy global sound discovery query at boot, which caused `Could not send message *#22*0##` errors on MyHomeServer1 gateways.

---

## 8. Active Bus Scanning for Sound Diffusion (WHO 22)

Previously, sound diffusion devices required passive bus listening to be discovered. We implemented direct active bus scanning for WHO 22 (`discovery.py`):

1. **Targeted Dimensional Queries**:
   * In OpenWebNet, generic state requests `*#22*WHERE##` always return NACK (`*#*0##`).
   * The `async_scan_sound_who22` function performs targeted dimensional queries:
     - **FM Tuner Sources (`2#S`)**: Dimension 6 query (`*#22*2#S*6##`) for sources $S \in [1..4]$.
     - **Zone Amplifiers (`3#A#P`)**: Dimension 12 query (Volume `*#22*3#A#P*12##`) for Environments $A \in [1..9]$ and Sound Points $P \in [1..9]$.
2. **Silent & Non-Intrusive Scanning**:
   * Using read-only dimensional status requests (`*#`), devices respond with stored parameters **without turning on amplifiers, without clicking relays, and without emitting sound**.
   * Devices do not need to be actively playing: the 27V bus microcontroller responds even while in standby.
3. **Safe Sequential Execution**:
   * Sound scanning runs sequentially after lights, covers, and climate, with zero impact on existing discovery pipelines.
   * Discovered devices automatically register under the `media_player` platform.
4. **User-Friendly Feedback**:
   * Updated the Config Flow description across English, Italian, French, and Dutch with an accurate scan estimate of **25–35 seconds**.

---

## 9. Zero-Traffic Smart Reconnect & Synchronous Radio Wake-Up

To eliminate delays and unresponsive first commands after prolonged inactivity or Home Assistant restarts:

### A. Zero-Traffic Smart Reconnect (`gateway.py`)
* **Problem**: MyHomeServer1 gateways silently close idle command TCP sockets after ~120 seconds. Upon the first user command after idle periods, the stale socket spent 3 seconds timing out before attempting a reconnect with a 1-second backoff.
* **Zero-Traffic Solution**:
  - The worker monitors command timestamps with a safety margin of **90 seconds** (`IDLE_TIMEOUT_THRESHOLD = 90.0`).
  - If the connection has been idle for more than 90 seconds (or on the first command after boot/reload), the worker **proactively renews the session in ~100ms** before dispatching the command frame.
  - **No Periodic Polling**: While the home is idle, zero bytes are transmitted across the LAN and SCS bus, completely avoiding bus traffic or buffer overruns.
  - **Zero Backoff on First Attempt**: If an unexpected disconnection occurs, the first user retry has 0s backoff for immediate dispatch.

### B. Synchronous Radio Wake-Up on Power-On (`media_player.py`)
* In the `async_turn_on()` method:
  - When turning on a room set to the Radio source (`"Radio FM (Tuner)"`) and no other room is actively streaming radio, the integration executes the full synchronized sequence:
    1. Powers on the room amplifier (`*22*1#4#7*WHERE##`).
    2. Routes matrix input to Source 1 (`*22*2#4#7*5#2#1##`).
    3. Triggers the preset/frequency recall on the F500 FM tuner (`*#22*2#1*#6*<preset>##`).
  - Sound starts **immediately on the first tap**, without requiring secondary station changes.

---

## 10. Formal Alignment with OpenWebNet Specifications (Verified via `openwebnet-mcp`)

Leveraging the formal **`openwebnet-mcp`** protocol server (grounded in official BTicino/Legrand specification manuals and dimension tables), several legacy syntax discrepancies in the low-level parser (`ownd/message.py`) were corrected:

### A. Shutters & Automation (WHO 2)
* **Correction of Dimension 10 vs 11**: The `set_shutter_level` method previously used `*#2*WHERE*#11#001*LEVEL##` (which in Legrand specifications is Dimension 11 for Venetian blind slat tilt). It has been corrected to **Dimension 10** (`*#2*WHERE*#10*LEVEL##`), which controls absolute percentage position (0–100%).
* **Dedicated `set_slat_angle` Method**: Venetian slat angle control has been extracted into a separate, dedicated method compliant with Dimension 11.
* **Hybrid Support in `cover.py`**: For covers with advanced actuators (`advanced: true`), Home Assistant sends the instant hardware Dimension 10 command, while maintaining virtual run-time calculation as fallback for standard relays.

### B. Traditional Sound Diffusion (WHO 16)
* **Standard ON / OFF Commands**: The `OWNSoundCommand.turn_on` method sent `*16*3*WHERE##` and `turn_off` sent `*16*13*WHERE##`. These have been aligned with standard OpenWebNet codes **`WHAT = 1`** (`*16*1*WHERE##`) and **`WHAT = 0`** (`*16*0*WHERE##`), ensuring bidirectional consistency with `media_player.py`.

### C. CEN Scenario Buttons (WHO 15)
* **Button / Action Inversion Bug Fix**: In standard CEN frames (`*15*WHAT*WHERE#BUTTON##`), the physical button number resides in `where_param` and the press type in `what` (1=short press, 0=start long, 2=end long, 3=heartbeat). The `OWNCENEvent` parser now correctly extracts the physical button number and press mode, restoring full compatibility with automation Blueprints.

### D. Video Session Termination (WHO 7)
* **Standardized Video Teardown**: The `close_video` command in `OWNAVCommand` was corrected from irregular syntax `*7*9**##` to standard **`*7*0*WHERE##`** (`WHAT = 0`).

### E. Standardized Fast Turn-Off in `OWNFilodiffusioneCommand` (WHO 22)
* Updated the `OWNFilodiffusioneCommand` class to include verified telegrams `1#4#0` (source unlink) and `0#4#0` (amplifier standby).

---

## 11. Physical Keypress Detection on H4562 In-Wall Modules & Global Services

### A. Physical Keypress Recognition on H4562 Audio Modules (`media_player.py`)
* **Observed Behavior**: Manually turning on an in-wall H4562 amplifier via its rocker switch left the Home Assistant entity in `OFF` or `IDLE` state until an interaction occurred from the Lovelace UI.
* **OpenWebNet Frame Analysis**: Physical activation on the in-wall module does not emit a standard `*22*1*WHERE##` command, but generates a specific hardware event sequence:
  - Local keypress notification: `*22*9*5#WHERE##`
  - Default source connection notification: `*22*22#4#A*5#WHERE##`
* **Resolution**: Added pattern matching for these hardware event frames in the `media_player.py` event handler. Tapping the physical wall switch immediately transitions the Home Assistant entity to **`Playing`** (`PLAYING`) and asynchronously queries the audio matrix for current volume and source.

### B. Gateway Lookup Hardening in Global Services (`__init__.py`)
* Diagnostic and configuration services (`myhome.send_message`, `myhome.scan_bus`, `myhome.export_to_yaml`) were hardened: default gateway lookup now strictly filters active `CONF_ENTITY` instances, preventing conflicts with internal state keys in `hass.data[DOMAIN]`.

---

## 12. Bus-Primary Tuner Frequency & Real-Time Preset Matching (WHO 22 / F500)

### A. Bus-Primary Frequency & Accurate Preset Resolution (`media_player.py`)
* **Real-time Bus Priority**: Frequency reporting now strictly prioritizes the live frequency received from the OpenWebNet bus (`TUNER_STATE["freq"]` / `self._tuner_freq`), rather than assuming a preset's stale memory.
* **Elimination of Untuned Preset Fallback Bug**: Fixed an issue where an untuned or empty preset slot (e.g. P2) inherited the residual frequency and station title of a previously tuned station (e.g. Radio 105 / P3).
* **Dynamic Preset Matching**: The active preset number is dynamically resolved by verifying if the bus frequency matches any entry in the stored preset bank (`TUNER_PRESETS`).

### B. Logo and Metadata Resolution for Free / Unsaved Frequencies (`radio_catalog.py`)
* **Universal Catalog Lookup**: Station names and official station logos from the local directory (`/local/loghi_radio/`) are now resolved for all valid frequencies detected on the bus, even when the station is tuned manually or via Seek Up/Down and not saved in preset slots P1–P5.
* **Added `preset_num` Field to `StationInfo`**: Enables seamless distinction between preset broadcasts (`P<N>: <Frequency> - <Station>`) and manual tuning (`<Frequency> - <Station>`).

### C. Dimension 11 Preset Bank Memory Support
* Added parsing for Dimension 11 telegrams (`*#22*5#2#1*11*1*<FREQ>*<PRESET>##`) broadcast by the F500 tuner hardware, preventing accidental corruption of preset memories during manual frequency sweeps or state restore.

---

## 13. Follow-Me Multi-Room & AUX / Spotify Source Routing Resolution (`media_player.py`)

### A. Elimination of Preemptive Tuner Hijacking on Zone Turn-On
* **Root Cause**: Powering on a zone unconditionally sent `*22*1#4#7*WHERE##` (source 7 = FM Tuner) before inspecting the active room source. This triggered a bus `#4#7` echo that forced the player to `"Radio FM (Tuner)"`, released the decoder, and paused Spotify on the bus.
* **Resolution**: `async_turn_on()` now resolves the desired active source **first** (`_get_active_house_source()` or saved source). If an AUX source is active (e.g. AUX 2 for Spotify/WiiM), it directly sends `*22*1#4#{src_num}*WHERE##` to turn on the zone amplifier on that specific AUX channel without touching the Tuner or interrupting ongoing playback.
* Fixed the same premature `1#4#7` transmission for AUX sources in `async_select_source()` and `async_play_media()`.

### B. Intelligent Active Source Inheritance (Follow-Me & Multi-Room)
* `_get_active_house_source()` now detects in real time whether another BTicino room is playing or if an external streaming decoder (WiiM, Spotify Connect, Echo Dot) is actively `PLAYING` or `BUFFERING`, allowing newly activated zones to seamlessly join the whole-house audio session.
* When a single-decoder assignment is transferred in exclusive Follow-Me mode, `_decoder_lost` now issues hardware standby telegrams (`*22*1#4#0*OLD_WHERE##` and `0#4#0`) to immediately silence the vacated room's physical speakers.

### C. Persistent Radio Preset Exposure (`radio_presets`)
* The `radio_presets` attribute (P1–P5 with paired frequencies and station titles) is now unconditionally present in `extra_state_attributes` across all playback states and sources, ensuring persistent state restoration across Home Assistant reboots and full Lovelace dashboard compatibility.
