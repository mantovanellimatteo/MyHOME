"""Support for common values for MyHome devices."""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.const import CONF_ENTITIES


from .const import DOMAIN, CONF_PLATFORMS, CONF_ENTITIES

# HA 2026.8+ links child devices by device id: `via_device` is deprecated and
# re-adding an entity that uses it (e.g. after renaming it) fails.
_HAS_VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__annotations__


def gateway_link(hass, mac: str) -> dict:
    """DeviceInfo / device registry kwargs linking a device to its gateway."""
    if not _HAS_VIA_DEVICE_ID:
        return {"via_device": (DOMAIN, mac)}
    registry = dr.async_get(hass)
    for entry in hass.config_entries.async_entries(DOMAIN):
        if hasattr(registry, "async_get_device_by_identifier"):
            gateway = registry.async_get_device_by_identifier((DOMAIN, mac), entry.entry_id)
        else:
            gateway = registry.async_get_device(identifiers={(DOMAIN, mac)})
        if gateway:
            return {"via_device_id": gateway.id}
    return {}


class MyHOMEEntity(Entity):
    def __init__(
        self,
        hass,
        name: str,
        platform: str,
        device_id: str,
        who: str,
        where: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ):
        self._hass = hass
        self._platform = platform
        self._who = who
        self._where = where
        self._device_id = device_id
        self._attr_unique_id = f"{gateway.mac}-{self._device_id}"
        self._manufacturer = manufacturer or "BTicino S.p.A."
        self._model = model
        self._gateway_handler = gateway
        self._attr_has_entity_name = True
        self._attr_name = None
        self._attr_entity_registry_enabled_default = True
        self._attr_should_poll = False

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{gateway.mac}-{self._device_id}")},
            name=name,
            manufacturer=self._manufacturer,
            model=self._model,
            **gateway_link(hass, self._gateway_handler.unique_id),
        )

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        await super().async_added_to_hass()
        self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][self._platform][self._device_id][CONF_ENTITIES][self._platform] = self
        await self.async_update()

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        if self._platform in self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][self._platform][self._device_id][CONF_ENTITIES]:
            del self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][self._platform][self._device_id][CONF_ENTITIES][self._platform]
        await super().async_will_remove_from_hass()
