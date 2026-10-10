"""Support for MyHome covers."""
from __future__ import annotations

import asyncio
import time

from homeassistant.components.cover import (
    ATTR_POSITION,
    DOMAIN as PLATFORM,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.const import (
    CONF_NAME,
    CONF_MAC,
)

from .ownd.message import (
    OWNAutomationEvent,
    OWNAutomationCommand,
)

from .const import (
    CONF_PLATFORMS,
    CONF_ENTITY,
    CONF_ENTITY_NAME,
    CONF_WHO,
    CONF_WHERE,
    CONF_BUS_INTERFACE,
    CONF_MANUFACTURER,
    CONF_DEVICE_MODEL,
    CONF_ADVANCED_SHUTTER,
    CONF_TRAVEL_TIME,
    CONF_OPEN_TIME,
    CONF_CLOSE_TIME,
    DEFAULT_TRAVEL_TIME,
    DOMAIN,
    LOGGER,
)
from .myhome_device import MyHOMEEntity
from .gateway import MyHOMEGatewayHandler


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the MyHome covers from config entry."""
    if PLATFORM not in hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS]:
        return True

    _covers = []
    _configured_covers = hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS][PLATFORM]

    for _cover in _configured_covers.keys():
        _cover_data = _configured_covers[_cover]
        _travel_time = _cover_data.get(CONF_TRAVEL_TIME, DEFAULT_TRAVEL_TIME)
        _open_time = _cover_data.get(CONF_OPEN_TIME, _travel_time)
        _close_time = _cover_data.get(CONF_CLOSE_TIME, _travel_time)

        _cover_entity = MyHOMECover(
            hass=hass,
            device_id=_cover,
            who=_cover_data[CONF_WHO],
            where=_cover_data[CONF_WHERE],
            interface=_cover_data.get(CONF_BUS_INTERFACE, None),
            name=_cover_data[CONF_NAME],
            entity_name=_cover_data.get(CONF_ENTITY_NAME, None),
            advanced=_cover_data.get(CONF_ADVANCED_SHUTTER, False),
            travel_time=_travel_time,
            open_time=_open_time,
            close_time=_close_time,
            manufacturer=_cover_data.get(CONF_MANUFACTURER, "BTicino S.p.A."),
            model=_cover_data.get(CONF_DEVICE_MODEL, None),
            gateway=hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_ENTITY],
        )
        _covers.append(_cover_entity)

    async_add_entities(_covers)


async def async_unload_entry(hass, config_entry):  # pylint: disable=unused-argument
    """Unload MyHome cover config entry."""
    if PLATFORM not in hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS]:
        return True

    _configured_covers = hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS][PLATFORM]

    for _cover in list(_configured_covers.keys()):
        del hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS][PLATFORM][_cover]


class MyHOMECover(MyHOMEEntity, CoverEntity, RestoreEntity):
    """Representation of a MyHome Cover."""

    _attr_device_class = CoverDeviceClass.SHUTTER

    def __init__(
        self,
        hass,
        name: str,
        entity_name: str,
        device_id: str,
        who: str,
        where: str,
        interface: str,
        advanced: bool,
        travel_time: float,
        open_time: float,
        close_time: float,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ):
        """Initialize the cover."""
        super().__init__(
            hass=hass,
            name=name,
            platform=PLATFORM,
            device_id=device_id,
            who=who,
            where=where,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
        )

        self._attr_name = entity_name
        self._interface = interface
        self._full_where = (
            f"{self._where}#4#{self._interface}"
            if self._interface is not None
            else self._where
        )

        self._advanced = advanced
        self._travel_time = float(travel_time)
        self._open_time = float(open_time)
        self._close_time = float(close_time)

        # All covers support OPEN, CLOSE, STOP and virtual/hardware SET_POSITION
        self._attr_supported_features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )
        self._gateway_handler = gateway

        self._attr_extra_state_attributes = {
            "A": where[: len(where) // 2],
            "PL": where[len(where) // 2 :],
            "travel_time": self._travel_time,
            "open_time": self._open_time,
            "close_time": self._close_time,
        }
        if self._interface is not None:
            self._attr_extra_state_attributes["Int"] = self._interface

        self._attr_current_cover_position = 100
        self._attr_is_opening = False
        self._attr_is_closing = False
        self._attr_is_closed = False

        self._start_time: float | None = None
        self._start_pos: int = 100
        self._moving_to_target: bool = False
        self._timer_task: asyncio.Task | None = None

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        await RestoreEntity.async_added_to_hass(self)
        last_state = await self.async_get_last_state()
        if last_state is not None:
            if (
                "current_position" in last_state.attributes
                and last_state.attributes["current_position"] is not None
            ):
                self._attr_current_cover_position = last_state.attributes["current_position"]
            elif last_state.state == "closed":
                self._attr_current_cover_position = 0
            elif last_state.state == "open":
                self._attr_current_cover_position = 100

        if self._attr_current_cover_position is None:
            self._attr_current_cover_position = 100

        self._attr_is_closed = (self._attr_current_cover_position == 0)

        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        self._cancel_timer()
        await super().async_will_remove_from_hass()

    def _cancel_timer(self):
        """Cancel any running movement timer."""
        if self._timer_task is not None and not self._timer_task.done():
            self._timer_task.cancel()
        self._timer_task = None
        self._moving_to_target = False

    def _calculate_intermediate_position(self):
        """Calculate intermediate position when motion stops."""
        if self._start_time is not None:
            elapsed = time.monotonic() - self._start_time
            if self._attr_is_opening:
                remaining = 100.0 - self._start_pos
                if self._open_time > 0 and elapsed >= (remaining / 100.0) * self._open_time * 0.95:
                    self._attr_current_cover_position = 100
                elif self._open_time > 0:
                    fraction = elapsed / self._open_time
                    self._attr_current_cover_position = min(
                        100, round(self._start_pos + (fraction * 100.0))
                    )
            elif self._attr_is_closing:
                if self._close_time > 0 and elapsed >= (self._start_pos / 100.0) * self._close_time * 0.95:
                    self._attr_current_cover_position = 0
                elif self._close_time > 0:
                    fraction = elapsed / self._close_time
                    self._attr_current_cover_position = max(
                        0, round(self._start_pos - (fraction * 100.0))
                    )
            self._start_time = None

        if self._attr_current_cover_position is None:
            self._attr_current_cover_position = 100

        self._attr_is_opening = False
        self._attr_is_closing = False
        self._attr_is_closed = (self._attr_current_cover_position == 0)

    async def _async_move_to_position(self, run_time: float, target: int):
        """Wait for travel time then stop cover and update position."""
        try:
            await asyncio.sleep(run_time)
            LOGGER.info(
                "%s Reached target position %s%% after %.2fs. Sending STOP command to %s.",
                self._gateway_handler.log_id,
                target,
                run_time,
                self._full_where,
            )
            await self._gateway_handler.send(OWNAutomationCommand.stop_shutter(self._full_where))
            self._attr_current_cover_position = target
            self._attr_is_closed = (target == 0)
            self._attr_is_opening = False
            self._attr_is_closing = False
            self._moving_to_target = False
            self._start_time = None
            self.async_write_ha_state()
        except asyncio.CancelledError:
            self._moving_to_target = False


    async def _async_full_travel_complete(self, run_time: float, target: int):
        """Wait for full travel time to finish animation and guarantee final state."""
        try:
            await asyncio.sleep(run_time + 0.5)
            self._attr_current_cover_position = target
            self._attr_is_closed = (target == 0)
            self._attr_is_opening = False
            self._attr_is_closing = False
            self._start_time = None
            self.async_write_ha_state()
        except asyncio.CancelledError:
            pass

    async def async_update(self):
        """Update the entity.

        Only used by the generic entity update service.
        """
        await self._gateway_handler.send_status_request(OWNAutomationCommand.status(self._full_where))

    async def async_open_cover(self, **kwargs):  # pylint: disable=unused-argument
        """Open the cover."""
        if self._attr_is_opening:
            return
        self._cancel_timer()
        current = (
            self._attr_current_cover_position
            if self._attr_current_cover_position is not None
            else 0
        )
        run_time = ((100.0 - current) / 100.0) * self._open_time
        self._start_pos = current
        self._start_time = time.monotonic()
        self._attr_is_opening = True
        self._attr_is_closing = False
        self.async_write_ha_state()

        await self._gateway_handler.send(OWNAutomationCommand.raise_shutter(self._full_where))
        self._timer_task = asyncio.create_task(self._async_full_travel_complete(run_time, 100))

    async def async_close_cover(self, **kwargs):  # pylint: disable=unused-argument
        """Close cover."""
        if self._attr_is_closing:
            return
        self._cancel_timer()
        current = (
            self._attr_current_cover_position
            if self._attr_current_cover_position is not None
            else 100
        )
        run_time = (current / 100.0) * self._close_time
        self._start_pos = current
        self._start_time = time.monotonic()
        self._attr_is_opening = False
        self._attr_is_closing = True
        self.async_write_ha_state()

        await self._gateway_handler.send(OWNAutomationCommand.lower_shutter(self._full_where))
        self._timer_task = asyncio.create_task(self._async_full_travel_complete(run_time, 0))

    async def async_set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        if ATTR_POSITION not in kwargs:
            return
        target = kwargs[ATTR_POSITION]

        current = (
            self._attr_current_cover_position
            if self._attr_current_cover_position is not None
            else 100
        )
        if target == current:
            return

        self._cancel_timer()

        if self._advanced:
            self._moving_to_target = True
            self._attr_is_opening = target > current
            self._attr_is_closing = target < current
            self.async_write_ha_state()
            await self._gateway_handler.send(
                OWNAutomationCommand.set_shutter_level(self._full_where, target)
            )
            return

        self._moving_to_target = True

        if target > current:
            delta = target - current
            run_time = (delta / 100.0) * self._open_time
            self._start_pos = current
            self._start_time = time.monotonic()
            self._attr_is_opening = True
            self._attr_is_closing = False
            self.async_write_ha_state()

            await self._gateway_handler.send(OWNAutomationCommand.raise_shutter(self._full_where))
            self._timer_task = asyncio.create_task(self._async_move_to_position(run_time, target))
        else:
            delta = current - target
            run_time = (delta / 100.0) * self._close_time
            self._start_pos = current
            self._start_time = time.monotonic()
            self._attr_is_opening = False
            self._attr_is_closing = True
            self.async_write_ha_state()

            await self._gateway_handler.send(OWNAutomationCommand.lower_shutter(self._full_where))
            self._timer_task = asyncio.create_task(self._async_move_to_position(run_time, target))

    async def async_stop_cover(self, **kwargs):  # pylint: disable=unused-argument
        """Stop the cover."""
        self._cancel_timer()
        await self._gateway_handler.send(OWNAutomationCommand.stop_shutter(self._full_where))
        self._calculate_intermediate_position()
        self.async_write_ha_state()

    def handle_event(self, message: OWNAutomationEvent):
        """Handle an event message."""
        LOGGER.info(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )

        # Advanced covers / dimension 10 reporting hardware position
        if message.current_position is not None:
            self._cancel_timer()
            self._attr_current_cover_position = message.current_position
            self._attr_is_closed = (message.current_position == 0)
            self._attr_is_opening = False
            self._attr_is_closing = False
            self._start_time = None
            self.async_schedule_update_ha_state()
            return

        if message.is_opening:
            if not self._attr_is_opening:
                # If we are already moving to target or just started < 1.2s ago, keep target timer
                if self._moving_to_target or (self._start_time is not None and (time.monotonic() - self._start_time) < 1.2):
                    self._attr_is_opening = True
                    self._attr_is_closing = False
                    self.async_schedule_update_ha_state()
                    return

                self._cancel_timer()
                if self._attr_is_closing:
                    self._calculate_intermediate_position()
                self._start_pos = (
                    self._attr_current_cover_position
                    if self._attr_current_cover_position is not None
                    else 0
                )
                self._start_time = time.monotonic()
                self._attr_is_opening = True
                self._attr_is_closing = False
                remaining_time = ((100.0 - self._start_pos) / 100.0) * self._open_time
                self._timer_task = asyncio.create_task(
                    self._async_full_travel_complete(remaining_time, 100)
                )
        elif message.is_closing:
            if not self._attr_is_closing:
                # If we are already moving to target or just started < 1.2s ago, keep target timer
                if self._moving_to_target or (self._start_time is not None and (time.monotonic() - self._start_time) < 1.2):
                    self._attr_is_closing = True
                    self._attr_is_opening = False
                    self.async_schedule_update_ha_state()
                    return

                self._cancel_timer()
                if self._attr_is_opening:
                    self._calculate_intermediate_position()
                self._start_pos = (
                    self._attr_current_cover_position
                    if self._attr_current_cover_position is not None
                    else 100
                )
                self._start_time = time.monotonic()
                self._attr_is_closing = True
                self._attr_is_opening = False
                remaining_time = (self._start_pos / 100.0) * self._close_time
                self._timer_task = asyncio.create_task(
                    self._async_full_travel_complete(remaining_time, 0)
                )
        elif message.state == 0:
            # Ignore initial transient startup stop/switch frame (< 1.2s after command initiated)
            if self._start_time is not None and (time.monotonic() - self._start_time) < 1.2:
                LOGGER.debug(
                    "%s Ignoring initial transient stop frame for %s (elapsed %.3fs)",
                    self._gateway_handler.log_id,
                    self._full_where,
                    time.monotonic() - self._start_time,
                )
                return

            # Stopped (wall switch, timeout, or stop command)
            self._cancel_timer()
            self._moving_to_target = False
            if self._attr_is_opening or self._attr_is_closing:
                self._calculate_intermediate_position()
            else:
                self._attr_is_opening = False
                self._attr_is_closing = False


        if message.is_closed is not None:
            self._attr_is_closed = message.is_closed
            if message.is_closed:
                self._attr_current_cover_position = 0
            elif self._attr_current_cover_position == 0:
                self._attr_current_cover_position = 100

        if self._attr_current_cover_position is None:
            self._attr_current_cover_position = 100
        self._attr_is_closed = (self._attr_current_cover_position == 0)

        self.async_schedule_update_ha_state()
