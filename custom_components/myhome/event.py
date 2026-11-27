"""Event entities for MyHOME CEN / CEN+ pushbuttons and scenario modules.

Entities are created automatically the first time a button is pressed (or a
scenario is launched) on the bus, and recreated from the entity registry on
the following restarts, so no YAML configuration is needed.
"""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.const import CONF_MAC
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, LOGGER

KIND_CEN = "cen"
KIND_CENPLUS = "cenplus"
KIND_SCENARIO = "scenario"

EVENT_SHORT_PRESS = "short_press"
EVENT_SHORT_RELEASE = "short_release"
EVENT_LONG_PRESS = "long_press"
EVENT_LONG_RELEASE = "long_release"
EVENT_PRESSED = "pressed"

EVENT_TYPES = {
    KIND_CEN: [EVENT_SHORT_PRESS, EVENT_SHORT_RELEASE, EVENT_LONG_PRESS, EVENT_LONG_RELEASE],
    KIND_CENPLUS: [
        EVENT_SHORT_PRESS,
        EVENT_LONG_PRESS,
        EVENT_LONG_RELEASE,
        "rotate_cw_slow",
        "rotate_cw_fast",
        "rotate_ccw_slow",
        "rotate_ccw_fast",
    ],
    KIND_SCENARIO: [EVENT_PRESSED],
}

TRANSLATION_KEYS = {
    KIND_CEN: "cen_button",
    KIND_CENPLUS: "cenplus_button",
    KIND_SCENARIO: "scenario_button",
}

# Key under hass.data[DOMAIN][mac] holding the callback used by the gateway
ADD_EVENT_ENTITY = "async_add_event_entity"


def _slug(address: str) -> str:
    """OpenWebNet addresses only contain digits and '#': make them id-safe."""
    return address.replace("#", "_")


def _unique_id(mac: str, kind: str, address: str, button: int) -> str:
    return f"{mac}-{kind}-{_slug(address)}-{button}"


def _parse_unique_id(mac: str, unique_id: str):
    """Return (kind, address, button) for an event entity unique_id, else None."""
    prefix = f"{mac}-"
    if not unique_id.startswith(prefix):
        return None
    parts = unique_id[len(prefix):].split("-")
    if len(parts) != 3 or parts[0] not in EVENT_TYPES or not parts[2].isdigit():
        return None
    return parts[0], parts[1].replace("_", "#"), int(parts[2])


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up MyHOME event entities and register the auto-creation callback."""
    mac = config_entry.data[CONF_MAC]
    gateway_data = hass.data[DOMAIN][mac]
    entities: dict[str, MyHOMEButtonEvent] = {}

    def _get_or_create(kind: str, address: str, button: int, add: bool = True):
        unique_id = _unique_id(mac, kind, address, button)
        entity = entities.get(unique_id)
        if entity is None:
            entity = MyHOMEButtonEvent(mac, kind, address, button)
            entities[unique_id] = entity
            if add:
                LOGGER.info(
                    "Auto-learning discovered new %s button: %s/%s", kind, address, button
                )
                async_add_entities([entity])
        return entity

    # Recreate the entities learned during previous runs
    registry = er.async_get(hass)
    restored = []
    for entry in er.async_entries_for_config_entry(registry, config_entry.entry_id):
        if entry.domain != "event":
            continue
        parsed = _parse_unique_id(mac, entry.unique_id)
        if parsed:
            restored.append(_get_or_create(*parsed, add=False))
    if restored:
        async_add_entities(restored)

    gateway_data[ADD_EVENT_ENTITY] = _get_or_create


async def async_unload_entry(hass, config_entry):
    gateway_data = hass.data[DOMAIN].get(config_entry.data[CONF_MAC], {})
    gateway_data.pop(ADD_EVENT_ENTITY, None)
    return True


class MyHOMEButtonEvent(EventEntity):
    """A CEN / CEN+ pushbutton or a scenario of a scenario module."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, mac: str, kind: str, address: str, button: int) -> None:
        self._kind = kind
        self._address = address
        self._button = button
        self._holding = False
        self._pending: str | None = None

        self._attr_unique_id = _unique_id(mac, kind, address, button)
        self._attr_event_types = EVENT_TYPES[kind]
        self._attr_translation_key = TRANSLATION_KEYS[kind]
        self._attr_translation_placeholders = {"button": str(button)}

        if kind == KIND_SCENARIO:
            # Same device the gateway creates for scenario modules (device triggers)
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{mac}-scenario-{address}")},
                name=f"Scenario Control {address}",
                manufacturer="BTicino",
                model="Scenario Module",
                via_device=(DOMAIN, mac),
            )
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{mac}-{kind}-{address}")},
                name=f"{'CEN+' if kind == KIND_CENPLUS else 'CEN'} {address}",
                manufacturer="BTicino S.p.A.",
                model="CEN+ pushbutton" if kind == KIND_CENPLUS else "CEN pushbutton",
                via_device=(DOMAIN, mac),
            )

        self._attr_extra_state_attributes = {"address": address, "button": button}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._pending is not None:
            event_type, self._pending = self._pending, None
            self._fire(event_type)

    def handle_button(self, event_type: str) -> None:
        """Handle an event coming from the bus."""
        if event_type not in self._attr_event_types:
            return
        # A held button repeats its "held" frame: report the long press once
        if event_type == EVENT_LONG_PRESS:
            if self._holding:
                return
            self._holding = True
        else:
            self._holding = False
        self._fire(event_type)

    def _fire(self, event_type: str) -> None:
        # Before being added, an entity has no usable `hass` attribute
        if getattr(self, "hass", None) is None or self.platform is None:
            # Entity just created and not yet added: fire once it is
            self._pending = event_type
            return
        self._trigger_event(event_type)
        self.async_write_ha_state()
