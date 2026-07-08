"""Provides device triggers for MyHOME."""
import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN

TRIGGER_TYPES = {"scenario_button_pressed"}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
        vol.Required("scenario"): vol.All(vol.Coerce(int), vol.Range(min=1, max=31)),
    }
)


async def async_get_triggers(hass, device_id):
    """List device triggers for MyHOME devices."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)

    if not device:
        return []

    # Check if this device is a scenario module
    is_scenario = False
    for identifier in device.identifiers:
        if identifier[0] == DOMAIN and "-scenario-" in identifier[1]:
            is_scenario = True
            break

    if not is_scenario:
        return []

    triggers = []
    # Provide triggers for button 1 to 8 in the UI dropdown
    for i in range(1, 9):
        triggers.append(
            {
                CONF_PLATFORM: "device",
                CONF_DOMAIN: DOMAIN,
                CONF_DEVICE_ID: device_id,
                CONF_TYPE: "scenario_button_pressed",
                "scenario": i,
            }
        )

    return triggers


async def async_attach_trigger(hass, config, action, trigger_info):
    """Attach a trigger."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(config[CONF_DEVICE_ID])

    control_panel = None
    if device:
        for identifier in device.identifiers:
            if identifier[0] == DOMAIN and "-scenario-" in identifier[1]:
                control_panel = identifier[1].split("-scenario-")[1]
                break

    if not control_panel:
        return None

    # Attach to the native event myhome_scenario_event
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: "myhome_scenario_event",
            event_trigger.CONF_EVENT_DATA: {
                "scenario": config["scenario"],
                "control_panel": control_panel,
            },
        }
    )

    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
