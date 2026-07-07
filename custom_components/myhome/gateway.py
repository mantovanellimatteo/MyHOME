"""Code to handle a MyHome Gateway."""
import asyncio
from typing import Dict, List

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
from homeassistant.components.climate import DOMAIN as CLIMATE

from OWNd.connection import OWNSession, OWNEventSession, OWNCommandSession, OWNGateway
from OWNd.message import (
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
    OWNCommand,
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
        self.listening_worker: asyncio.tasks.Task = None
        self.sending_workers: List[asyncio.tasks.Task] = []
        self.send_buffer = asyncio.Queue()

    @property
    def mac(self) -> str:
        return self.gateway.serial

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
                backoff = 1
                LOGGER.info("%s Listening session established.", self.log_id)

                while not self._terminate_listener:
                    message = await _event_session.get_next()
                    if message is None:
                        LOGGER.warning("%s Listening session disconnected. Reconnecting...", self.log_id)
                        break

                    LOGGER.debug("%s Message received: `%s`", self.log_id, message)

                    if self.generate_events:
                        if isinstance(message, OWNMessage):
                            _event_content = {"gateway": str(self.gateway.host)}
                            _event_content.update(message.event_content)
                            self.hass.bus.async_fire("myhome_message_event", _event_content)
                        else:
                            self.hass.bus.async_fire("myhome_message_event", {"gateway": str(self.gateway.host), "message": str(message)})

                    if not isinstance(message, OWNMessage):
                        LOGGER.warning(
                            "%s Data received is not a message: `%s`",
                            self.log_id,
                            message,
                        )
                    elif isinstance(message, OWNEnergyEvent):
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
                                    for _platform in self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS]:
                                        if _platform != BUTTON and message.entity in self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][_platform]:
                                            for _entity in self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][_platform][message.entity][CONF_ENTITIES]:
                                                if (
                                                    isinstance(
                                                        self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][_platform][message.entity][CONF_ENTITIES][_entity],
                                                        MyHOMEEntity,
                                                    )
                                                    and not isinstance(
                                                        self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][_platform][message.entity][CONF_ENTITIES][_entity],
                                                        DisableCommandButtonEntity,
                                                    )
                                                    and not isinstance(
                                                        self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][_platform][message.entity][CONF_ENTITIES][_entity],
                                                        EnableCommandButtonEntity,
                                                    )
                                                ):
                                                    self.hass.data[DOMAIN][self.mac][CONF_PLATFORMS][_platform][message.entity][CONF_ENTITIES][_entity].handle_event(message)

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
                    elif isinstance(message, OWNGatewayEvent) or isinstance(message, OWNGatewayCommand):
                        LOGGER.info(
                            "%s %s",
                            self.log_id,
                            message.human_readable_log,
                        )
                    else:
                        LOGGER.info(
                            "%s Unsupported message type: `%s`",
                            self.log_id,
                            message,
                        )
            except (OSError, asyncio.TimeoutError, ConnectionError) as err:
                self.is_connected = False
                LOGGER.warning(
                    "%s Connection error in listener: %s. Retrying in %d seconds...",
                    self.log_id,
                    err,
                    backoff,
                )
            except Exception as ex:
                self.is_connected = False
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

        self.is_connected = False
        LOGGER.debug("%s Destroying listening worker.", self.log_id)

    async def sending_loop(self, worker_id: int):
        self._terminate_sender = False
        backoff = 1

        LOGGER.debug(
            "%s Creating sending worker %s",
            self.log_id,
            worker_id,
        )

        while not self._terminate_sender:
            _command_session = OWNCommandSession(gateway=self.gateway, logger=LOGGER)
            try:
                LOGGER.debug("%s Connecting command session for worker %d...", self.log_id, worker_id)
                await _command_session.connect()
                if _command_session._stream_writer is None:
                    raise ConnectionError("Failed to open command streams.")

                backoff = 1
                LOGGER.debug("%s Command session established for worker %d.", self.log_id, worker_id)

                while not self._terminate_sender:
                    task = await self.send_buffer.get()
                    try:
                        LOGGER.debug(
                            "%s Message `%s` was successfully unqueued by worker %s.",
                            self.name,
                            self.gateway.host,
                            task["message"],
                            worker_id,
                        )
                        await _command_session.send(message=task["message"], is_status_request=task["is_status_request"])
                        self.send_buffer.task_done()
                    except Exception as task_err:
                        self.send_buffer.task_done()
                        await self.send_buffer.put(task)
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
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)

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
        await self.send_buffer.put({"message": message, "is_status_request": True})
        LOGGER.debug(
            "%s Message `%s` was successfully queued.",
            self.log_id,
            message,
        )
