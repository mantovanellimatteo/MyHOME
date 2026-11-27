"""Support for MyHome sensors (power/energy, temperature, illuminance)."""

from datetime import timedelta

from voluptuous import (
    Optional,
    Coerce,
    All,
    Range,
)

from homeassistant.components.sensor import DOMAIN as PLATFORM
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    CONF_ENTITIES,
    CONF_NAME,
    CONF_MAC,
    LIGHT_LUX,
    UnitOfPower,
    UnitOfEnergy,
    UnitOfTemperature,
    EntityCategory,
)
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from OWNd.message import (
    MESSAGE_TYPE_ACTIVE_POWER,
    MESSAGE_TYPE_CURRENT_DAY_CONSUMPTION,
    MESSAGE_TYPE_CURRENT_MONTH_CONSUMPTION,
    MESSAGE_TYPE_ENERGY_TOTALIZER,
    MESSAGE_TYPE_ILLUMINANCE,
    MESSAGE_TYPE_MAIN_TEMPERATURE,
    MESSAGE_TYPE_SECONDARY_TEMPERATURE,
    OWNEnergyCommand,
    OWNEnergyEvent,
    OWNHeatingCommand,
    OWNHeatingEvent,
    OWNLightingCommand,
    OWNLightingEvent,
)

from .const import (
    CONF_PLATFORMS,
    CONF_ENTITY,
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_MANUFACTURER,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
    LOGGER,
)
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity

SCAN_INTERVAL = timedelta(seconds=60)

SERVICE_SEND_INSTANT_POWER = "start_sending_instant_power"

# Instant power broadcast: the meter sends active power for DURATION minutes
POWER_BROADCAST_DURATION = 255
POWER_RENEW_AFTER = timedelta(minutes=55)
POWER_STALE_AFTER = timedelta(minutes=10)
POWER_WATCHDOG_INTERVAL = timedelta(minutes=5)

ATTR_DURATION = "duration"
ATTR_DATE = "date"
ATTR_MONTH = "month"
ATTR_DAY = "day"


async def async_setup_entry(hass, config_entry, async_add_entities):
    _sensors = []
    gateway = hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_ENTITY]

    # Always register Gateway Configured Devices diagnostic sensor
    _sensors.append(
        MyHOMEGatewayDeviceCount(gateway=gateway, config_entry=config_entry)
    )

    _power_devices_configured = False

    if PLATFORM in hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS]:
        _configured_sensors = hass.data[DOMAIN][config_entry.data[CONF_MAC]][
            CONF_PLATFORMS
        ][PLATFORM]

        for _sensor in _configured_sensors.keys():
            if (
                _configured_sensors[_sensor][CONF_DEVICE_CLASS] == SensorDeviceClass.POWER
                or _configured_sensors[_sensor][CONF_DEVICE_CLASS]
                == SensorDeviceClass.ENERGY
            ):
                _required_entities = list(
                    _configured_sensors[_sensor][CONF_ENTITIES].keys()
                )

                if (
                    _configured_sensors[_sensor][CONF_DEVICE_CLASS]
                    == SensorDeviceClass.POWER
                ):
                    _power_devices_configured = True

                    ent_reg = er.async_get(hass)
                    existing_entity_id = ent_reg.async_get_entity_id(
                        "sensor", DOMAIN, _sensor
                    )
                    if existing_entity_id is not None:
                        LOGGER.warning(
                            "Sensor %s: %s will be migrated to %s-%s",
                            _sensor,
                            existing_entity_id,
                            _sensor,
                            SensorDeviceClass.POWER,
                        )
                        ent_reg.async_update_entity(
                            entity_id=existing_entity_id,
                            new_unique_id=f"{_sensor}-{SensorDeviceClass.POWER}",
                        )

                    _sensors.append(
                        MyHOMEPowerSensor(
                            hass=hass,
                            device_id=_sensor,
                            who=_configured_sensors[_sensor][CONF_WHO],
                            where=_configured_sensors[_sensor][CONF_WHERE],
                            name=_configured_sensors[_sensor][CONF_NAME],
                            device_class=_configured_sensors[_sensor][CONF_DEVICE_CLASS],
                            manufacturer=_configured_sensors[_sensor][CONF_MANUFACTURER],
                            model=_configured_sensors[_sensor][CONF_DEVICE_MODEL],
                            gateway=hass.data[DOMAIN][config_entry.data[CONF_MAC]][
                                CONF_ENTITY
                            ],
                        )
                    )
                    _required_entities.remove(SensorDeviceClass.POWER)

                for entity_specific_id in _required_entities:
                    _sensors.append(
                        MyHOMEEnergySensor(
                            hass=hass,
                            device_id=_sensor,
                            who=_configured_sensors[_sensor][CONF_WHO],
                            where=_configured_sensors[_sensor][CONF_WHERE],
                            name=_configured_sensors[_sensor][CONF_NAME],
                            entity_specific_id=entity_specific_id,
                            device_class=SensorDeviceClass.ENERGY,
                            manufacturer=_configured_sensors[_sensor][CONF_MANUFACTURER],
                            model=_configured_sensors[_sensor][CONF_DEVICE_MODEL],
                            gateway=hass.data[DOMAIN][config_entry.data[CONF_MAC]][
                                CONF_ENTITY
                            ],
                        )
                    )

            elif (
                _configured_sensors[_sensor][CONF_DEVICE_CLASS]
                == SensorDeviceClass.TEMPERATURE
            ):
                _sensors.append(
                    MyHOMETemperatureSensor(
                        hass=hass,
                        device_id=_sensor,
                        who=_configured_sensors[_sensor][CONF_WHO],
                        where=_configured_sensors[_sensor][CONF_WHERE],
                        name=_configured_sensors[_sensor][CONF_NAME],
                        device_class=_configured_sensors[_sensor][CONF_DEVICE_CLASS],
                        manufacturer=_configured_sensors[_sensor][CONF_MANUFACTURER],
                        model=_configured_sensors[_sensor][CONF_DEVICE_MODEL],
                        gateway=hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_ENTITY],
                    )
                )

            elif (
                _configured_sensors[_sensor][CONF_DEVICE_CLASS]
                == SensorDeviceClass.ILLUMINANCE
            ):
                _sensors.append(
                    MyHOMEIlluminanceSensor(
                        hass=hass,
                        device_id=_sensor,
                        who=_configured_sensors[_sensor][CONF_WHO],
                        where=_configured_sensors[_sensor][CONF_WHERE],
                        name=_configured_sensors[_sensor][CONF_NAME],
                        device_class=_configured_sensors[_sensor][CONF_DEVICE_CLASS],
                        manufacturer=_configured_sensors[_sensor][CONF_MANUFACTURER],
                        model=_configured_sensors[_sensor][CONF_DEVICE_MODEL],
                        gateway=hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_ENTITY],
                    )
                )

    if _power_devices_configured:
        platform = entity_platform.current_platform.get()

        platform.async_register_entity_service(
            SERVICE_SEND_INSTANT_POWER,
            {Optional(ATTR_DURATION): All(Coerce(int), Range(min=1, max=255))},
            "start_sending_instant_power",
        )

    async_add_entities(_sensors)


async def async_unload_entry(hass, config_entry):
    if PLATFORM not in hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS]:
        return True

    _configured_sensors = hass.data[DOMAIN][config_entry.data[CONF_MAC]][
        CONF_PLATFORMS
    ][PLATFORM]

    for _sensor in _configured_sensors.keys():
        del hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS][PLATFORM][
            _sensor
        ]


class MyHOMEPowerSensor(MyHOMEEntity, SensorEntity):
    def __init__(
        self,
        hass,
        name: str,
        device_id: str,
        who: str,
        where: str,
        device_class: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ) -> None:
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

        self._entity_specific_name = "Power"
        self._attr_name = f"{name} {self._entity_specific_name}"

        self._attr_device_class = device_class
        self._attr_unique_id = (
            f"{gateway.mac}-{self._device_id}-{self._attr_device_class}"
        )
        self._attr_native_unit_of_measurement = UnitOfPower.WATT
        self._attr_state_class = SensorStateClass.MEASUREMENT

        self._attr_native_value = None
        self._attr_extra_state_attributes = {
            "Sensor": f"({self._where[0]}){self._where[1:]}"
        }

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][
            self._platform
        ][self._device_id][CONF_ENTITIES][self._attr_device_class] = self

        self._last_power_frame: float | None = None
        self._last_power_request: float | None = None

        # Start sending instant power immediately
        await self.start_sending_instant_power(POWER_BROADCAST_DURATION)

        # Watchdog: renew the broadcast before it expires and re-request it
        # when the meter goes silent, so no external automation is needed.
        self._unsub_power_renewal = async_track_time_interval(
            self._hass,
            self._async_power_watchdog,
            POWER_WATCHDOG_INTERVAL,
        )
        # The meter forgets the request when the bus or gateway restarts:
        # ask again as soon as the event session is re-established.
        self._gateway_handler.register_connection_callback(self._on_connection_change)

        await self.async_update()

    def _on_connection_change(self) -> None:
        """Re-request the broadcast after every (re)connection to the gateway."""
        recently_requested = (
            self._last_power_request is not None
            and self._hass.loop.time() - self._last_power_request < 30
        )
        if self._gateway_handler.is_connected and not recently_requested:
            self._hass.async_create_task(
                self.start_sending_instant_power(POWER_BROADCAST_DURATION)
            )

    async def _async_power_watchdog(self, now=None):
        """Renew the broadcast periodically, or sooner if no frame arrives."""
        if not self._gateway_handler.is_connected:
            return
        loop_now = self._hass.loop.time()
        since_request = (
            loop_now - self._last_power_request
            if self._last_power_request is not None
            else None
        )
        if since_request is None or since_request >= POWER_RENEW_AFTER.total_seconds():
            LOGGER.debug("Renewing instant power broadcast for %s", self.entity_id)
            await self.start_sending_instant_power(POWER_BROADCAST_DURATION)
            return
        last_data = self._last_power_frame or self._last_power_request
        if (
            loop_now - last_data >= POWER_STALE_AFTER.total_seconds()
            and since_request >= POWER_STALE_AFTER.total_seconds()
        ):
            LOGGER.info(
                "%s No instant power received for %s for %d minutes, requesting it again",
                self._gateway_handler.log_id,
                self.entity_id,
                (loop_now - last_data) // 60,
            )
            await self.start_sending_instant_power(POWER_BROADCAST_DURATION)

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        if getattr(self, "_unsub_power_renewal", None):
            self._unsub_power_renewal()
            self._unsub_power_renewal = None
        self._gateway_handler.unregister_connection_callback(self._on_connection_change)

        if (
            self._attr_device_class
            in self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][
                self._platform
            ][self._device_id][CONF_ENTITIES]
        ):
            del self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][
                self._platform
            ][self._device_id][CONF_ENTITIES][self._attr_device_class]

    async def async_update(self):
        """Update the entity.

        Only used by the generic entity update service.
        """

    def handle_event(self, message: OWNEnergyEvent):
        """Handle an event message."""
        if message.message_type not in [MESSAGE_TYPE_ACTIVE_POWER]:
            return True

        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        self._last_power_frame = self._hass.loop.time()
        self._attr_native_value = message.active_power
        self.async_schedule_update_ha_state()

    async def start_sending_instant_power(self, duration=POWER_BROADCAST_DURATION):
        """Request automatic instant power (duration in minutes, max 255)."""
        self._last_power_request = self._hass.loop.time()
        await self._gateway_handler.send(
            OWNEnergyCommand.start_sending_instant_power(self._where, duration)
        )


class MyHOMEEnergySensor(MyHOMEEntity, SensorEntity):
    def __init__(
        self,
        hass,
        name: str,
        device_id: str,
        who: str,
        where: str,
        entity_specific_id: str,
        device_class: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ) -> None:
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

        self._entity_specific_id = entity_specific_id
        if self._entity_specific_id == "daily-energy":
            self._entity_specific_name = "Energy (today)"
            self._attr_entity_registry_enabled_default = False
        elif self._entity_specific_id == "monthly-energy":
            self._entity_specific_name = "Energy (current month)"
            self._attr_entity_registry_enabled_default = False
        elif self._entity_specific_id == "total-energy":
            self._entity_specific_name = "Energy"
            self._attr_entity_registry_enabled_default = True
        self._attr_name = f"{name} {self._entity_specific_name}"

        self._attr_unique_id = (
            f"{gateway.mac}-{self._device_id}-{self._entity_specific_id}"
        )
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_should_poll = True
        self._attr_native_value = None
        self._attr_extra_state_attributes = {
            "Sensor": f"({self._where[0]}){self._where[1:]}"
        }

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][
            self._platform
        ][self._device_id][CONF_ENTITIES][self._entity_specific_id] = self
        await self.async_update()

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        if (
            self._entity_specific_id
            in self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][
                self._platform
            ][self._device_id][CONF_ENTITIES]
        ):
            del self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][
                self._platform
            ][self._device_id][CONF_ENTITIES][self._entity_specific_id]

    async def async_update(self):
        """Update the entity.

        Only used by the generic entity update service.
        """
        if self._entity_specific_id == "total-energy":
            await self._gateway_handler.send_status_request(
                OWNEnergyCommand.get_total_consumption(self._where)
            )
        elif self._entity_specific_id == "monthly-energy":
            await self._gateway_handler.send_status_request(
                OWNEnergyCommand.get_partial_monthly_consumption(self._where)
            )
        elif self._entity_specific_id == "daily-energy":
            await self._gateway_handler.send_status_request(
                OWNEnergyCommand.get_partial_daily_consumption(self._where)
            )

    def handle_event(self, message: OWNEnergyEvent):
        """Handle an event message."""
        if message.message_type not in [
            MESSAGE_TYPE_ENERGY_TOTALIZER,
            MESSAGE_TYPE_CURRENT_MONTH_CONSUMPTION,
            MESSAGE_TYPE_CURRENT_DAY_CONSUMPTION,
        ]:
            return True

        if (
            self._entity_specific_id == "total-energy"
            and message.message_type == MESSAGE_TYPE_ENERGY_TOTALIZER
        ):
            LOGGER.info(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._attr_native_value = message.total_consumption
        elif (
            self._entity_specific_id == "monthly-energy"
            and message.message_type == MESSAGE_TYPE_CURRENT_MONTH_CONSUMPTION
        ):
            LOGGER.info(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._attr_native_value = message.current_month_partial_consumption
        elif (
            self._entity_specific_id == "daily-energy"
            and message.message_type == MESSAGE_TYPE_CURRENT_DAY_CONSUMPTION
        ):
            LOGGER.info(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._attr_native_value = message.current_day_partial_consumption
        self.async_schedule_update_ha_state()


class MyHOMETemperatureSensor(MyHOMEEntity, SensorEntity):
    def __init__(
        self,
        hass,
        name: str,
        device_id: str,
        who: str,
        where: str,
        device_class: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ) -> None:
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

        self._entity_specific_name = "Temperature"
        self._attr_name = f"{name} {self._entity_specific_name}"

        self._attr_device_class = device_class
        self._attr_unique_id = (
            f"{gateway.mac}-{self._device_id}-{self._attr_device_class}"
        )
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = True
        self._attr_native_value = None
        self._attr_extra_state_attributes = {
            "Sensor": f"({self._where[0]}){self._where[1:]}"
        }

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][
            self._platform
        ][self._device_id][CONF_ENTITIES][self._attr_device_class] = self
        await self.async_update()

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        if (
            self._attr_device_class
            in self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][
                self._platform
            ][self._device_id][CONF_ENTITIES]
        ):
            del self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][
                self._platform
            ][self._device_id][CONF_ENTITIES][self._attr_device_class]

    async def async_update(self):
        """Update the entity.

        Only used by the generic entity update service.
        """
        await self._gateway_handler.send_status_request(
            OWNHeatingCommand.get_temperature(self._where)
        )

    def handle_event(self, message: OWNHeatingEvent):
        """Handle an event message."""
        if message.message_type not in [
            MESSAGE_TYPE_MAIN_TEMPERATURE,
            MESSAGE_TYPE_SECONDARY_TEMPERATURE,
        ]:
            return True

        if message.message_type == MESSAGE_TYPE_MAIN_TEMPERATURE:
            LOGGER.info(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._attr_native_value = message.main_temperature
            self.async_schedule_update_ha_state()
        elif message.message_type == MESSAGE_TYPE_SECONDARY_TEMPERATURE:
            LOGGER.info(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._attr_native_value = message.secondary_temperature[1]
            self.async_schedule_update_ha_state()


class MyHOMEIlluminanceSensor(MyHOMEEntity, SensorEntity):
    def __init__(
        self,
        hass,
        name: str,
        device_id: str,
        who: str,
        where: str,
        device_class: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ) -> None:
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

        self._entity_specific_name = "Illuminance"
        self._attr_name = f"{name} {self._entity_specific_name}"

        self._attr_device_class = device_class
        self._attr_unique_id = (
            f"{gateway.mac}-{self._device_id}-{self._attr_device_class}"
        )
        self._attr_native_unit_of_measurement = LIGHT_LUX
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_value = None
        self._attr_extra_state_attributes = {
            "A": where[: len(where) // 2],
            "PL": where[len(where) // 2 :],
        }

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][
            self._platform
        ][self._device_id][CONF_ENTITIES][self._attr_device_class] = self
        await self.async_update()

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        if (
            self._attr_device_class
            in self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][
                self._platform
            ][self._device_id][CONF_ENTITIES]
        ):
            del self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][
                self._platform
            ][self._device_id][CONF_ENTITIES][self._attr_device_class]

    async def async_update(self):
        """Update the entity.

        Only used by the generic entity update service.
        """
        await self._gateway_handler.send_status_request(
            OWNLightingCommand.get_illuminance(self._where)
        )

    def handle_event(self, message: OWNLightingEvent):
        """Handle an event message."""
        if message.message_type not in [MESSAGE_TYPE_ILLUMINANCE]:
            return True

        LOGGER.info(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        self._attr_native_value = message.illuminance
        self.async_schedule_update_ha_state()


class MyHOMEGatewayDeviceCount(SensorEntity):
    """Diagnostic sensor for total configured devices on MyHOME Gateway."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:home-automation"
    _attr_has_entity_name = True
    _attr_translation_key = "gateway_devices"

    def __init__(self, gateway: MyHOMEGatewayHandler, config_entry) -> None:
        """Initialize the device count sensor."""
        self._gateway = gateway
        self._config_entry = config_entry
        self._attr_unique_id = f"{gateway.mac}-gateway-devices"
        self._attr_name = "Configured devices"

    @property
    def device_info(self) -> DeviceInfo:
        """Link directly to the MyHOME Gateway device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._gateway.unique_id)},
            connections={(dr.CONNECTION_NETWORK_MAC, self._gateway.mac)},
            manufacturer=self._gateway.manufacturer,
            model=self._gateway.model,
            name=self._gateway.name,
            sw_version=self._gateway.firmware,
        )

    def _calculate_counts(self) -> tuple[int, dict]:
        """Compute device counts from local memory without sending network frames."""
        platforms = (
            self.hass.data.get(DOMAIN, {})
            .get(self._gateway.mac, {})
            .get(CONF_PLATFORMS, {})
        )
        counts = {}
        total = 0
        for plat in [
            "light",
            "cover",
            "climate",
            "switch",
            "sensor",
            "binary_sensor",
            "media_player",
        ]:
            c = len(platforms.get(plat, {}))
            counts[plat] = c
            total += c

        # Also count scenario modules from device registry
        try:
            dev_reg = dr.async_get(self.hass)
            devices = dr.async_entries_for_config_entry(dev_reg, self._config_entry.entry_id)
            scenario_count = sum(1 for d in devices if d.model == "Scenario Module")
        except Exception:
            scenario_count = 0

        counts["scenarios"] = scenario_count
        total += scenario_count

        return total, counts

    @property
    def native_value(self) -> int:
        """Return total number of configured devices."""
        total, _ = self._calculate_counts()
        return total

    @property
    def extra_state_attributes(self) -> dict:
        """Return breakdown of configured devices per category."""
        _, counts = self._calculate_counts()
        return {
            "lights": counts.get("light", 0),
            "covers": counts.get("cover", 0),
            "climate_zones": counts.get("climate", 0),
            "switches": counts.get("switch", 0),
            "sensors": counts.get("sensor", 0),
            "binary_sensors": counts.get("binary_sensor", 0),
            "media_players": counts.get("media_player", 0),
            "scenarios": counts.get("scenarios", 0),
        }

    async def async_added_to_hass(self) -> None:
        """Update state when added to hass."""
        self.async_write_ha_state()

