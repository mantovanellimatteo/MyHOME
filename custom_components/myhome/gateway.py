"""Code to handle a MyHome Gateway."""
import asyncio
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from homeassistant.const import (
    CONF_ENTITIES,
    CONF_HOST,
    CONF_PORT,
    CONF_PASSWORD,
    CONF_NAME,
    CONF_MAC,
    CONF_FRIENDLY_NAME,
)
from homeassistant.components.light import DOMAIN as LIGHT
from homeassistant.components.switch import (
    SwitchDeviceClass,
    DOMAIN as SWITCH,
)
from homeassistant.components.button import DOMAIN as BUTTON
from homeassistant.components.cover import DOMAIN as COVER
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    DOMAIN as BINARY_SENSOR,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    DOMAIN as SENSOR,
)
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    area_registry as ar,
)

from .ownd.connection import OWNSession, OWNEventSession, OWNCommandSession, OWNGateway
from .ownd.message import (
    OWNMessage,
    OWNLightingEvent,
    OWNLightingCommand,
    OWNEnergyEvent,
    OWNAutomationEvent,
    OWNDryContactEvent,
    OWNAuxEvent,
    OWNHeatingEvent,
    OWNHeatingCommand,
    OWNCENPlusEvent,
    OWNCENEvent,
    OWNGatewayEvent,
    OWNGatewayCommand,
    OWNScenarioEvent,
    OWNCommand,
    OWNSignaling,
    OWNSoundEvent,
    OWNSoundCommand,
    OWNFilodiffusioneEvent,
    OWNFilodiffusioneCommand,
)

from .const import (
    CONF_PLATFORMS,
    CONF_FIRMWARE,
    CONF_SSDP_LOCATION,
    CONF_SSDP_ST,
    CONF_DEVICE_TYPE,
    CONF_MANUFACTURER,
    CONF_MANUFACTURER_URL,
    CONF_UDN,
    CONF_SHORT_PRESS,
    CONF_SHORT_RELEASE,
    CONF_LONG_PRESS,
    CONF_LONG_RELEASE,
    EVENT_MYHOME_INTERCOM,
    DOMAIN,
    LOGGER,
)
from .myhome_device import MyHOMEEntity
from .button import (
    DisableCommandButtonEntity,
    EnableCommandButtonEntity,
)


class MyHOMEGatewayHandler:
    """Manages a single MyHOME Gateway."""

    def __init__(self, hass, config_entry, generate_events=False):
        build_info = {
            "address": config_entry.data[CONF_HOST],
            "port": config_entry.data[CONF_PORT],
            "password": config_entry.data[CONF_PASSWORD],
            "ssdp_location": config_entry.data[CONF_SSDP_LOCATION],
            "ssdp_st": config_entry.data[CONF_SSDP_ST],
            "deviceType": config_entry.data[CONF_DEVICE_TYPE],
            "friendlyName": config_entry.data[CONF_FRIENDLY_NAME],
            "manufacturer": config_entry.data[CONF_MANUFACTURER],
            "manufacturerURL": config_entry.data[CONF_MANUFACTURER_URL],
            "modelName": config_entry.data[CONF_NAME],
            "modelNumber": config_entry.data[CONF_FIRMWARE],
            "serialNumber": config_entry.data[CONF_MAC],
            "UDN": config_entry.data[CONF_UDN],
        }
        self.hass = hass
        self.config_entry = config_entry
        self.generate_events = generate_events
        self.gateway = OWNGateway(build_info)
        self._terminate_listener = False
        self._terminate_sender = False
        self.is_connected = False
        self.connected_at: Optional[datetime] = None
        self.reconnect_count: int = 0
        self._connection_callbacks: List[Callable[[], None]] = []
        self.listening_worker: asyncio.tasks.Task = None
        self.sending_workers: List[asyncio.tasks.Task] = []
        self.send_buffer = asyncio.Queue()
        self._ducking_active_environments = set()
        self._last_diagnostic_query: Dict[str, float] = {}

    def _is_configured_device(self, platform_name: str, target_where: str) -> bool:
        """Check if a given 'where' address is configured under a specific platform."""
        try:
            platform_devices = (
                self.hass.data.get(DOMAIN, {})
                .get(self.mac, {})
                .get(CONF_PLATFORMS, {})
                .get(platform_name, {})
            )
            if target_where in platform_devices:
                return True
            for dev_conf in platform_devices.values():
                if isinstance(dev_conf, dict) and dev_conf.get(CONF_WHERE) == target_where:
                    return True
        except Exception:
            pass
        return False

    def register_connection_callback(self, callback: Callable[[], None]) -> None:
        """Register callback for connection state changes."""
        if callback not in self._connection_callbacks:
            self._connection_callbacks.append(callback)

    def unregister_connection_callback(self, callback: Callable[[], None]) -> None:
        """Unregister callback."""
        if callback in self._connection_callbacks:
            self._connection_callbacks.remove(callback)

    def _notify_connection_change(self) -> None:
        """Notify all registered listeners about connection state changes."""
        for callback in self._connection_callbacks:
            try:
                callback()
            except Exception as ex:
                LOGGER.error("%s Error in connection state callback: %s", self.log_id, ex)

    @property
    def mac(self) -> str:
        return self.config_entry.data[CONF_MAC]

    @property
    def unique_id(self) -> str:
        return self.mac

    @property
    def log_id(self) -> str:
        return self.gateway.log_id

    @property
    def manufacturer(self) -> str:
        return self.gateway.manufacturer

    @property
    def name(self) -> str:
        return f"{self.gateway.model_name} Gateway"

    @property
    def model(self) -> str:
        return self.gateway.model_name

    @property
    def firmware(self) -> str:
        return self.gateway.firmware

    async def test(self) -> Dict:
        return await OWNSession(gateway=self.gateway, logger=LOGGER).test_connection()

    async def listening_loop(self):
        self._terminate_listener = False
        backoff = 1

        while not self._terminate_listener:
            LOGGER.debug("%s Connecting listening session...", self.log_id)
            _event_session = OWNEventSession(gateway=self.gateway, logger=LOGGER)
            try:
                await _event_session.connect()
                if _event_session._stream_reader is None:
                    raise ConnectionError("Failed to open listening connection streams.")

                self.is_connected = True
                self.connected_at = datetime.now(timezone.utc)
                self.reconnect_count += 1
                self._notify_connection_change()
                backoff = 1
                LOGGER.info("%s Listening session established.", self.log_id)

                # Active Discovery for supported subsystems (Lighting, Covers, Climate)
                try:
                    await self.send_status_request(OWNCommand.parse("*#1*0##"))   # WHO 1: Lighting
                    await self.send_status_request(OWNCommand.parse("*#2*0##"))   # WHO 2: Automation / Covers
                    await self.send_status_request(OWNCommand.parse("*#4*0##"))   # WHO 4: Climate
                except Exception as disc_err:
                    LOGGER.debug("%s Errore invio richieste discovery: %s", self.log_id, disc_err)

                while not self._terminate_listener:
                    message = await _event_session.get_next()
                    if message is None:
                        LOGGER.warning("%s Listening session disconnected. Reconnecting...", self.log_id)
                        break

                    if isinstance(message, OWNSignaling):
                        continue

                    try:
                        LOGGER.debug("[BUS SNIFFER] %s (type=%s)", message, type(message).__name__)

                        # Handle and dispatch intercom / audio ducking events
                        self._handle_intercom_events(message)

                        if self.generate_events:
                            if isinstance(message, OWNMessage):
                                _event_content = {"gateway": str(self.gateway.host)}
                                _event_content.update(message.event_content)
                                self.hass.bus.async_fire("myhome_message_event", _event_content)
                            else:
                                self.hass.bus.async_fire("myhome_message_event", {"gateway": str(self.gateway.host), "message": str(message)})

                        if not isinstance(message, OWNMessage):
                            if isinstance(message, str) and (
                                message.startswith("*22*")
                                or message.startswith("*#22*")
                                or message.startswith("*16*")
                                or message.startswith("*#16*")
                            ):
                                LOGGER.debug("%s Intercettato messaggio grezzo Filodiffusione / Sound: %s", self.log_id, message)
                                try:
                                    clean = message.strip("#").split("*")
                                    who_raw = clean[1].replace("#", "") if len(clean) >= 2 else None
                                    where_raw = None
                                    if len(clean) >= 3:
                                        where_raw = clean[2] if message.startswith("*#") else (clean[3] if len(clean) > 3 else clean[2])

                                    # Riconosci speaker fisici: '3#...' per WHO 22 o numerico per WHO 16
                                    if where_raw and (where_raw.startswith("3#") or (who_raw == "16" and where_raw.isdigit())):
                                        dev_id = f"{who_raw}-{where_raw}"

                                        # 1. Creazione dinamica immediata a runtime (Zero YAML / Zero reload!)
                                        add_fn = self.hass.data[DOMAIN].get(self.mac, {}).get("async_add_media_player")
                                        if add_fn:
                                            created_entity = add_fn(dev_id, who_raw, where_raw, f"Sound Zone {where_raw}")
                                            if created_entity and hasattr(created_entity, "handle_event"):
                                                created_entity.handle_event(message)

                                        # 2. Persistenza nelle opzioni di configurazione per i riavvii successivi
                                        new_options = dict(self.config_entry.options)
                                        devices = new_options.get("devices", {})
                                        if "media_player" not in devices:
                                            devices["media_player"] = {}
                                        if dev_id not in devices["media_player"]:
                                            dev_conf = {
                                                "who": who_raw,
                                                "where": where_raw,
                                                "name": f"Sound Zone {where_raw}",
                                            }
                                            devices["media_player"][dev_id] = dev_conf
                                            new_options["devices"] = devices
                                            LOGGER.info("Auto-learning discovered new sound device: %s (%s)", dev_id, "media_player")
                                            self.hass.config_entries.async_update_entry(
                                                self.config_entry,
                                                options=new_options
                                            )
                                            self.hass.components.persistent_notification.async_create(
                                                self.hass,
                                                title="MyHOME Auto-learning",
                                                message=f"Discovered and registered new `media_player` device: **{dev_conf['name']}** (address {where_raw})",
                                                notification_id=f"myhome_learned_{dev_id}"
                                            )
                                except Exception as learn_ex:
                                    LOGGER.debug("Errore auto-learning sound: %s", learn_ex)

                                if "media_player" in self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS]:
                                    for dev_id, dev_data in self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS]["media_player"].items():
                                        if isinstance(dev_data, dict):
                                            for _entity in dev_data.get(CONF_ENTITIES, {}).values():
                                                if hasattr(_entity, "handle_event"):
                                                    _entity.handle_event(message)
                                continue

                            elif isinstance(message, str) and (message.startswith("*3*") or message.startswith("*#3*")):
                                LOGGER.info("Intercepted Load Management (WHO 3) raw message: %s", message)
                                safe_msg_id = message.replace('*', '_').replace('#', '_')
                                self.hass.components.persistent_notification.async_create(
                                    self.hass,
                                    title="MyHOME Controllo Carichi",
                                    message=f"Nuovo messaggio di gestione carichi intercettato: `{message}`\nInvia questo log allo sviluppatore per integrarlo!",
                                    notification_id=f"myhome_load_management_{safe_msg_id}",
                                )
                                continue
                            else:
                                LOGGER.warning(
                                    "%s Data received is not a message: `%s`",
                                    self.log_id,
                                    message,
                                )
                            continue
                    except Exception as ex:
                        LOGGER.error("Error processing message %s: %s", message, ex)
                        continue

                    # Auto-learning logic
                    if self.config_entry.options.get("enable_auto_learning", False):
                        who = str(message.who)
                        where = str(message.where)
                        platform = None
                        dev_conf = {"who": who, "where": where}
                        if who == "1":
                            platform = "light"
                            dev_conf["dimmable"] = hasattr(message, "brightness") and message.brightness is not None
                            dev_conf["name"] = f"Light {where}"
                        elif who == "2":
                            platform = "cover"
                            dev_conf["name"] = f"Cover {where}"
                        elif who == "4":
                            platform = "climate"
                            dev_conf["name"] = f"Zone {where}"
                            dev_conf["zone"] = where
                        elif who == "16":
                            platform = "media_player"
                            dev_conf["name"] = f"Sound Zone {where}"
                        elif who == "22":
                            platform = "media_player"
                            dev_conf["name"] = f"Sound Zone {where}"
                        if platform:
                            dev_id = f"{who}-{where}"
                            if (
                                platform not in self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS]
                                or dev_id not in self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][platform]
                            ):
                                LOGGER.info("Auto-learning discovered new device: %s (%s)", dev_id, platform)
                                new_options = dict(self.config_entry.options)
                                devices = new_options.get("devices", {})
                                if platform not in devices:
                                    devices[platform] = {}
                                if dev_id not in devices[platform]:
                                    devices[platform][dev_id] = dev_conf
                                    new_options["devices"] = devices
                                    self.hass.config_entries.async_update_entry(
                                        self.config_entry,
                                        options=new_options
                                    )
                                    self.hass.components.persistent_notification.async_create(
                                        self.hass,
                                        title="MyHOME Auto-learning",
                                        message=f"Discovered and registered new `{platform}` device: **{dev_conf['name']}** (address {where})",
                                        notification_id=f"myhome_learned_{dev_id}"
                                    )
                                    self.hass.async_create_task(
                                        self.hass.config_entries.async_reload(self.config_entry.entry_id)
                                    )

                    if isinstance(message, OWNEnergyEvent):
                        if SENSOR in self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS] and message.entity in self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][SENSOR]:
                            for _entity in self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][SENSOR][message.entity][CONF_ENTITIES]:
                                if isinstance(
                                    self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][SENSOR][message.entity][CONF_ENTITIES][_entity],
                                    MyHOMEEntity,
                                ):
                                    self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][SENSOR][message.entity][CONF_ENTITIES][_entity].handle_event(message)
                        else:
                            continue
                    elif (
                        isinstance(message, OWNLightingEvent)
                        or isinstance(message, OWNAutomationEvent)
                        or isinstance(message, OWNDryContactEvent)
                        or isinstance(message, OWNAuxEvent)
                        or isinstance(message, OWNHeatingEvent)
                        or str(getattr(message, "who", "")) in ["6", "16", "22"]
                    ):
                        if not message.is_translation:
                            is_event = False
                            if isinstance(message, OWNLightingEvent):
                                if message.is_general:
                                    is_event = True
                                    event = "on" if message.is_on else "off"
                                    self.hass.bus.async_fire(
                                        "myhome_general_light_event",
                                        {"message": str(message), "event": event},
                                    )
                                    await asyncio.sleep(0.1)
                                    await self.send_status_request(OWNLightingCommand.status("0"))
                                elif message.is_area:
                                    is_event = True
                                    event = "on" if message.is_on else "off"
                                    self.hass.bus.async_fire(
                                        "myhome_area_light_event",
                                        {
                                            "message": str(message),
                                            "area": message.area,
                                            "event": event,
                                        },
                                    )
                                    await asyncio.sleep(0.1)
                                    await self.send_status_request(OWNLightingCommand.status(message.area))
                                elif message.is_group:
                                    is_event = True
                                    event = "on" if message.is_on else "off"
                                    self.hass.bus.async_fire(
                                        "myhome_group_light_event",
                                        {
                                            "message": str(message),
                                            "group": message.group,
                                            "event": event,
                                        },
                                    )
                            elif isinstance(message, OWNAutomationEvent):
                                if message.is_general:
                                    is_event = True
                                    if message.is_opening and not message.is_closing:
                                        event = "open"
                                    elif message.is_closing and not message.is_opening:
                                        event = "close"
                                    else:
                                        event = "stop"
                                    self.hass.bus.async_fire(
                                        "myhome_general_automation_event",
                                        {"message": str(message), "event": event},
                                    )
                                elif message.is_area:
                                    is_event = True
                                    if message.is_opening and not message.is_closing:
                                        event = "open"
                                    elif message.is_closing and not message.is_opening:
                                        event = "close"
                                    else:
                                        event = "stop"
                                    self.hass.bus.async_fire(
                                        "myhome_area_automation_event",
                                        {
                                            "message": str(message),
                                            "area": message.area,
                                            "event": event,
                                        },
                                    )
                                elif message.is_group:
                                    is_event = True
                                    if message.is_opening and not message.is_closing:
                                        event = "open"
                                    elif message.is_closing and not message.is_opening:
                                        event = "close"
                                    else:
                                        event = "stop"
                                    self.hass.bus.async_fire(
                                        "myhome_group_automation_event",
                                        {
                                            "message": str(message),
                                            "group": message.group,
                                            "event": event,
                                        },
                                    )
                            if not is_event:
                                if isinstance(message, OWNLightingEvent) and message.brightness_preset:
                                    if isinstance(
                                        self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][LIGHT][message.entity][CONF_ENTITIES][LIGHT],
                                        MyHOMEEntity,
                                    ):
                                        await self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][LIGHT][message.entity][CONF_ENTITIES][LIGHT].async_update()
                                else:
                                    msg_str = str(message)
                                    who_prefix = str(getattr(message, "who", "")).replace("#", "")
                                    if not who_prefix and ("*22*" in msg_str or "*#22*" in msg_str):
                                        who_prefix = "22"
                                    elif not who_prefix and ("*16*" in msg_str or "*#16*" in msg_str):
                                        who_prefix = "16"
                                    elif not who_prefix and ("*6*" in msg_str or "*#6*" in msg_str):
                                        who_prefix = "6"

                                    if who_prefix in ["22", "16"]:
                                        LOGGER.info("%s [Sound Bus Sniffer] Ricevuto evento OpenWebNet: %s", self.log_id, msg_str)
                                    elif who_prefix == "6":
                                        LOGGER.info("%s [Door Entry Sniffer] Ricevuto evento OpenWebNet: %s", self.log_id, msg_str)

                                    for _platform in self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS]:
                                        if _platform == BUTTON:
                                            continue

                                        matched_keys = []
                                        platform_devices = self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][_platform]
                                        if message.entity in platform_devices:
                                            matched_keys.append(message.entity)
                                        if who_prefix:
                                            combined_key = f"{who_prefix}-{message.entity}"
                                            if combined_key in platform_devices and combined_key not in matched_keys:
                                                matched_keys.append(combined_key)

                                        # Per i messaggi Sound (WHO 22 / WHO 16), inoltra a tutte le zone media_player
                                        # così che eventi Tuner (2#1) e cambi sorgente (#4#) siano ricevuti istantaneamente
                                        if _platform == "media_player" and who_prefix in ["22", "16"]:
                                            for p_key in platform_devices:
                                                if p_key not in matched_keys:
                                                    matched_keys.append(p_key)

                                        for dev_key in matched_keys:
                                            for _entity in platform_devices[dev_key].get(CONF_ENTITIES, {}):
                                                ent = platform_devices[dev_key][CONF_ENTITIES][_entity]
                                                if (
                                                    isinstance(ent, MyHOMEEntity)
                                                    and not isinstance(ent, DisableCommandButtonEntity)
                                                    and not isinstance(ent, EnableCommandButtonEntity)
                                                ):
                                                    ent.handle_event(message)

                        else:
                            LOGGER.debug(
                                "%s Ignoring translation message `%s`",
                                self.log_id,
                                message,
                            )
                    elif isinstance(message, OWNHeatingCommand) and message.dimension is not None and message.dimension == 14:
                        where = message.where[1:] if message.where.startswith("#") else message.where
                        LOGGER.debug(
                            "%s Received heating command, sending query to zone %s",
                            self.log_id,
                            where,
                        )
                        await self.send_status_request(OWNHeatingCommand.status(where))
                    elif isinstance(message, OWNCENPlusEvent):
                        event = None
                        if message.is_short_pressed:
                            event = CONF_SHORT_PRESS
                        elif message.is_held or message.is_still_held:
                            event = CONF_LONG_PRESS
                        elif message.is_released:
                            event = CONF_LONG_RELEASE
                        else:
                            event = None
                        self.hass.bus.async_fire(
                            "myhome_cenplus_event",
                            {
                                "object": int(message.object),
                                "pushbutton": int(message.push_button),
                                "event": event,
                            },
                        )
                        LOGGER.info(
                            "%s %s",
                            self.log_id,
                            message.human_readable_log,
                        )
                    elif isinstance(message, OWNCENEvent):
                        event = None
                        if message.is_pressed:
                            event = CONF_SHORT_PRESS
                        elif message.is_released_after_short_press:
                            event = CONF_SHORT_RELEASE
                        elif message.is_held:
                            event = CONF_LONG_PRESS
                        elif message.is_released_after_long_press:
                            event = CONF_LONG_RELEASE
                        else:
                            event = None
                        self.hass.bus.async_fire(
                            "myhome_cen_event",
                            {
                                "object": int(message.object),
                                "pushbutton": int(message.push_button),
                                "event": event,
                            },
                        )
                        LOGGER.info(
                            "%s %s",
                            self.log_id,
                            message.human_readable_log,
                        )
                    elif isinstance(message, OWNScenarioEvent):
                        # Find the config entry for this gateway
                        config_entry = next(
                            (
                                entry for entry in self.hass.config_entries.async_entries(DOMAIN) # type: ignore
                                if entry.data.get(CONF_MAC) == self.mac
                            ),
                            None
                        )
                        if config_entry:
                            device_registry = dr.async_get(self.hass)
                            device_registry.async_get_or_create(
                                config_entry_id=config_entry.entry_id,
                                identifiers={(DOMAIN, f"{self.mac}-scenario-{message.control_panel}")},
                                name=f"Scenario Control {message.control_panel}",
                                manufacturer="BTicino",
                                model="Scenario Module",
                                via_device=(DOMAIN, self.mac),
                            )

                        self.hass.bus.async_fire(
                            "myhome_scenario_event",
                            {
                                "scenario": int(message.scenario),
                                "control_panel": message.control_panel,
                            },
                        )
                        LOGGER.info(
                            "%s %s",
                            self.log_id,
                            message.human_readable_log,
                        )
                    elif isinstance(message, OWNGatewayEvent) or isinstance(message, OWNGatewayCommand):
                        LOGGER.info(
                            "%s %s",
                            self.log_id,
                            message.human_readable_log,
                        )
                    elif str(getattr(message, "who", "")) in ["1001", "1004"]:
                        import re
                        match = re.match(r"\*#(1001|1004)\*([0-9#]+)\*", str(message))
                        if match:
                            who_diag = match.group(1)
                            where = match.group(2)
                            platform = LIGHT if who_diag == "1001" else CLIMATE

                            # Only query if this device is actually configured in Home Assistant
                            if not self._is_configured_device(platform, where):
                                LOGGER.debug(
                                    "%s Diagnostic frame for unconfigured %s device %s (WHO %s). Skipping status request.",
                                    self.log_id,
                                    platform,
                                    where,
                                    who_diag,
                                )
                                continue

                            # Rate-limit: do not query the same device more than once every 60 seconds
                            now_ts = asyncio.get_running_loop().time()
                            diag_key = f"{who_diag}-{where}"
                            last_query = self._last_diagnostic_query.get(diag_key, 0)
                            if now_ts - last_query < 60:
                                LOGGER.debug(
                                    "%s Throttling diagnostic status request for %s (last queried %.1fs ago).",
                                    self.log_id,
                                    diag_key,
                                    now_ts - last_query,
                                )
                                continue

                            self._last_diagnostic_query[diag_key] = now_ts

                            if who_diag == "1001":
                                LOGGER.info("%s Intercepted Lighting Diagnostic frame (WHO 1001) for %s. Requesting standard status...", self.log_id, where)
                                await self.send_status_request(OWNLightingCommand.status(where))
                            elif who_diag == "1004":
                                LOGGER.info("%s Intercepted Climate Diagnostic frame (WHO 1004) for %s. Requesting standard status...", self.log_id, where)
                                await self.send_status_request(OWNHeatingCommand.status(where))
                        else:
                            LOGGER.debug("%s Diagnostic message: `%s`", self.log_id, message)
                    else:
                        LOGGER.info(
                            "%s Unsupported message type: `%s`",
                            self.log_id,
                            message,
                        )
            except (OSError, asyncio.TimeoutError, ConnectionError) as err:
                if self.is_connected:
                    self.is_connected = False
                    self._notify_connection_change()
                LOGGER.warning(
                    "%s Connection error in listener: %s. Retrying in %d seconds...",
                    self.log_id,
                    err,
                    backoff,
                )
            except Exception as ex:
                if self.is_connected:
                    self.is_connected = False
                    self._notify_connection_change()
                LOGGER.exception(
                    "%s Unexpected error in listener: %s. Retrying in %d seconds...",
                    self.log_id,
                    ex,
                    backoff,
                )

            try:
                await _event_session.close()
            except Exception:
                pass

            if not self._terminate_listener:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)

        if self.is_connected:
            self.is_connected = False
            self._notify_connection_change()
        LOGGER.debug("%s Destroying listening worker.", self.log_id)

    async def sending_loop(self, worker_id: int):
        self._terminate_sender = False
        backoff = 1

        LOGGER.debug(
            "%s Creating sending worker %s",
            self.log_id,
            worker_id,
        )

        IDLE_TIMEOUT_THRESHOLD = 90.0  # BTicino drops idle command sockets at ~120s

        while not self._terminate_sender:
            _command_session = OWNCommandSession(gateway=self.gateway, logger=LOGGER)
            last_activity_time = None
            try:
                LOGGER.debug("%s Connecting command session for worker %d...", self.log_id, worker_id)
                await _command_session.connect()
                if _command_session._stream_writer is None:
                    raise ConnectionError("Failed to open command streams.")

                backoff = 1
                last_activity_time = asyncio.get_event_loop().time()
                LOGGER.debug("%s Command session established for worker %d.", self.log_id, worker_id)

                while not self._terminate_sender:
                    task = await self.send_buffer.get()

                    # Zero-Traffic Smart Refresh:
                    # Se sono trascorsi più di 90s dall'ultimo comando, la sessione sul gateway è quasi certamente
                    # scaduta/chiusa in silenzio. Rinnoviamo la connessione al volo in ~100ms per evitare il timeout da 3s.
                    now = asyncio.get_event_loop().time()
                    if last_activity_time is None or (now - last_activity_time) >= IDLE_TIMEOUT_THRESHOLD:
                        LOGGER.debug(
                            "%s Command session idle for %.1fs (>= 90s). Refreshing connection before sending...",
                            self.log_id,
                            now - (last_activity_time or 0),
                        )
                        try:
                            await _command_session.close()
                        except Exception:
                            pass
                        _command_session = OWNCommandSession(gateway=self.gateway, logger=LOGGER)
                        await _command_session.connect()
                        if _command_session._stream_writer is None:
                            self.send_buffer.task_done()
                            await self.send_buffer.put(task)
                            raise ConnectionError("Failed to refresh command session.")

                    try:
                        LOGGER.debug(
                            "%s Message `%s` was successfully unqueued by worker %s.",
                            self.name,
                            self.gateway.host,
                            task["message"],
                            worker_id,
                        )
                        await _command_session.send(message=task["message"], is_status_request=task["is_status_request"])
                        last_activity_time = asyncio.get_event_loop().time()
                        self.send_buffer.task_done()
                    except Exception as task_err:
                        self.send_buffer.task_done()
                        task_retries = task.get("retries", 0)
                        if not task.get("is_status_request") and task_retries < 2:
                            task["retries"] = task_retries + 1
                            await self.send_buffer.put(task)
                            # Primo retry: azzera il backoff per riconnettersi immediatamente senza pause
                            backoff = 0
                        else:
                            LOGGER.error("%s Dropping failed message `%s` (status_request=%s, retries=%d): %s", self.log_id, task.get("message"), task.get("is_status_request"), task_retries, task_err)
                        raise ConnectionError("Command sending failed, reconnecting...") from task_err
            except (OSError, asyncio.TimeoutError, ConnectionError) as err:
                LOGGER.warning(
                    "%s Connection error in sender worker %d: %s. Retrying in %d seconds...",
                    self.log_id,
                    worker_id,
                    err,
                    backoff,
                )
            except Exception as ex:
                LOGGER.exception(
                    "%s Unexpected error in sender worker %d: %s. Retrying in %d seconds...",
                    self.log_id,
                    worker_id,
                    ex,
                    backoff,
                )

            try:
                await _command_session.close()
            except Exception:
                pass

            if not self._terminate_sender:
                if backoff > 0:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 120)
                else:
                    backoff = 1

        LOGGER.debug(
            "%s Destroying sending worker %s",
            self.log_id,
            worker_id,
        )

    async def close_listener(self) -> bool:
        LOGGER.info("%s Closing event listener", self.log_id)
        self._terminate_sender = True
        self._terminate_listener = True

        return True

    async def send(self, message: OWNCommand):
        await self.send_buffer.put({"message": message, "is_status_request": False})
        LOGGER.debug(
            "%s Message `%s` was successfully queued.",
            self.log_id,
            message,
        )

    async def send_status_request(self, message: OWNCommand):
        """Send a non-critical status request with low priority, 0 retries and drop-on-error behavior."""
        if not self.is_connected:
            return
        await self.send_buffer.put({
            "message": message,
            "retries": 0,
            "is_status_request": True,
        })
        LOGGER.debug(
            "%s Status request `%s` was queued.",
            self.log_id,
            message,
        )

    def _resolve_zone_metadata(self, who: int, where: str) -> dict:
        """Dynamically resolve OpenWebNet address to Home Assistant entity, device, and area metadata."""
        meta = {}
        if not where:
            return meta

        # 1. Parse OpenWebNet hierarchical address parts
        if "#" in where:
            parts = where.split("#")
            # e.g., '3#7#4' -> type 3, environment 7, point 4
            if len(parts) == 3 and parts[1].isdigit():
                meta["environment"] = int(parts[1])
                meta["point"] = int(parts[2]) if parts[2].isdigit() else parts[2]
            # e.g., '5#3#7#7' -> type 5, source 3, environment 7, point 7
            elif len(parts) == 4 and parts[2].isdigit():
                meta["environment"] = int(parts[2])
                meta["point"] = int(parts[3]) if parts[3].isdigit() else parts[3]

        try:
            ent_reg = er.async_get(self.hass)
            dev_reg = dr.async_get(self.hass)
            area_reg = ar.async_get(self.hass)

            # Search in platform entities for matching WHERE / environment
            target_entity = None
            platforms_data = self.hass.data.get(DOMAIN, {}).get(self.mac, {}).get(CONF_PLATFORMS, {})
            env_val = meta.get("environment")

            for plat_name, devices in platforms_data.items():
                if plat_name == BUTTON or not isinstance(devices, dict):
                    continue
                for dev_id, dev_data in devices.items():
                    if not isinstance(dev_data, dict):
                        continue
                    dev_where = dev_id.split("-")[-1] if "-" in dev_id else dev_id
                    is_match = False
                    if dev_where == where:
                        is_match = True
                    elif env_val is not None and f"#{env_val}#" in dev_where:
                        is_match = True

                    if is_match:
                        for ent_domain, entity_inst in dev_data.get(CONF_ENTITIES, {}).items():
                            if hasattr(entity_inst, "entity_id") and entity_inst.entity_id:
                                target_entity = entity_inst
                                break
                    if target_entity:
                        break
                if target_entity:
                    break

            if target_entity:
                meta["entity_id"] = target_entity.entity_id
                if hasattr(target_entity, "name") and target_entity.name:
                    meta["device_name"] = target_entity.name

                # Look up Area via Entity Registry or Device Registry
                entry = ent_reg.async_get(target_entity.entity_id)
                area_id = None
                if entry:
                    if entry.area_id:
                        area_id = entry.area_id
                    elif entry.device_id:
                        device = dev_reg.async_get(entry.device_id)
                        if device:
                            if not meta.get("device_name"):
                                meta["device_name"] = device.name_by_user or device.name
                            if device.area_id:
                                area_id = device.area_id

                if area_id:
                    area_entry = area_reg.async_get_area(area_id)
                    if area_entry:
                        meta["area"] = area_entry.name

        except Exception as ex:
            LOGGER.debug("%s Error resolving zone metadata for where=%s: %s", self.log_id, where, ex)

        return meta

    def _handle_intercom_events(self, message):
        """Analyze incoming OpenWebNet message and fire myhome_intercom_event if applicable."""
        msg_str = str(message).strip()
        if not msg_str:
            return

        try:
            # 1. Sound Diffusion Intercom Ducking (WHO 22 / Dimension 12)
            # E.g., *#22*3#7#4*12*0*10## -> Broadcast Ducking active (intercom call in progress)
            if "*12*0*" in msg_str and ("*22*" in msg_str or "*#22*" in msg_str):
                clean = msg_str.strip("#").split("*")
                where = clean[2] if len(clean) > 2 else ""
                meta = self._resolve_zone_metadata(22, where)
                point_val = meta.get("point")
                env_val = meta.get("environment")

                # Must be a broadcast ducking telegram (point == 4 or point == 0) and not an individual standby (e.g. 3#7#5)
                if point_val in [4, 0, "4", "0"] or where.endswith("#4") or where.endswith("#0"):
                    if env_val is not None:
                        self._ducking_active_environments.add(env_val)
                    payload = {
                        "event": "ducking_start",
                        "who": 22,
                        "dimension": 12,
                        "where": where,
                        "message": msg_str,
                    }
                    payload.update(meta)
                    self.hass.bus.async_fire(EVENT_MYHOME_INTERCOM, payload)
                    LOGGER.info("%s Intercom Event fired: ducking_start (where=%s, area=%s, env=%s)", self.log_id, where, meta.get("area"), env_val)
                else:
                    LOGGER.debug("%s Ignoring single-amplifier standby frame: %s (where=%s)", self.log_id, msg_str, where)

            # 2. Sound Diffusion Volume Restore (End of call / ducking end)
            # E.g., *#22*3#7#7*1*12## -> Restores normal volume after call
            elif ("*1*" in msg_str or "*#1*" in msg_str) and ("*22*" in msg_str or "*#22*" in msg_str) and not ("*1*1##" in msg_str or "*#*1##" in msg_str):
                clean = msg_str.strip("#").split("*")
                if len(clean) >= 4 and clean[3].isdigit():
                    where = clean[2] if len(clean) > 2 else ""
                    vol = clean[3] if len(clean) > 3 else ""
                    meta = self._resolve_zone_metadata(22, where)
                    env_val = meta.get("environment")

                    # Only fire ducking_end if ducking was actively in progress for this environment
                    if env_val in self._ducking_active_environments:
                        self._ducking_active_environments.discard(env_val)
                        payload = {
                            "event": "ducking_end",
                            "who": 22,
                            "dimension": 1,
                            "volume": int(vol),
                            "where": where,
                            "message": msg_str,
                        }
                        payload.update(meta)
                        self.hass.bus.async_fire(EVENT_MYHOME_INTERCOM, payload)
                        LOGGER.info("%s Intercom Event fired: ducking_end (where=%s, vol=%s, area=%s)", self.log_id, where, vol, meta.get("area"))
                    else:
                        LOGGER.debug("%s Normal volume update ignored for ducking_end: %s (where=%s)", self.log_id, msg_str, where)

            # 3. Intercom Ring / External Call trigger (WHO 1 with WHERE 24 or auxiliary relay)
            elif msg_str.startswith("*1*1*24##") or msg_str.startswith("*1*1000#1*24##"):
                payload = {
                    "event": "call_started",
                    "who": 1,
                    "what": 1,
                    "where": "24",
                    "message": msg_str,
                }
                meta = self._resolve_zone_metadata(1, "24")
                payload.update(meta)
                self.hass.bus.async_fire(EVENT_MYHOME_INTERCOM, payload)
                LOGGER.info("%s Intercom Event fired: call_started (where=24)", self.log_id)

            # 4. Video Door Entry (WHO 7)
            elif msg_str.startswith("*7*") or msg_str.startswith("*#7*"):
                clean = msg_str.strip("#").split("*")
                what = clean[2] if len(clean) > 2 else ""
                where = clean[3] if len(clean) > 3 else ""
                evt = "video_call" if what == "0" else ("video_end" if what == "1" else "video_event")
                payload = {
                    "event": evt,
                    "who": 7,
                    "what": what,
                    "where": where,
                    "message": msg_str,
                }
                meta = self._resolve_zone_metadata(7, where)
                payload.update(meta)
                self.hass.bus.async_fire(EVENT_MYHOME_INTERCOM, payload)
                LOGGER.info("%s Intercom Event fired: %s (who=7, where=%s, area=%s)", self.log_id, evt, where, meta.get("area"))

            # 5. Legacy Door Entry / Lock (WHO 6)
            elif msg_str.startswith("*6*") or msg_str.startswith("*#6*"):
                clean = msg_str.strip("#").split("*")
                what = clean[2] if len(clean) > 2 else ""
                where = clean[3] if len(clean) > 3 else ""
                evt = "door_open" if what in ["10", "1"] else "call_started"
                payload = {
                    "event": evt,
                    "who": 6,
                    "what": what,
                    "where": where,
                    "message": msg_str,
                }
                meta = self._resolve_zone_metadata(6, where)
                payload.update(meta)
                self.hass.bus.async_fire(EVENT_MYHOME_INTERCOM, payload)
                LOGGER.info("%s Intercom Event fired: %s (who=6, where=%s, area=%s)", self.log_id, evt, where, meta.get("area"))
        except Exception as err:
            LOGGER.debug("%s Error analyzing intercom event: %s", self.log_id, err)


