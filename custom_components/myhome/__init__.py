""" MyHOME integration. """

import aiofiles
import yaml

from OWNd.message import OWNCommand, OWNGatewayCommand

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er, config_validation as cv
from homeassistant.const import CONF_MAC

from .const import (
    ATTR_GATEWAY,
    ATTR_MESSAGE,
    CONF_PLATFORMS,
    CONF_ENTITY,
    CONF_ENTITIES,
    CONF_GATEWAY,
    CONF_WORKER_COUNT,
    CONF_FILE_PATH,
    CONF_GENERATE_EVENTS,
    CONF_MANUFACTURER,
    CONF_DEVICE_MODEL,
    CONF_ENTITY_NAME,
    DOMAIN,
    LOGGER,
)
from .validate import config_schema, format_mac
from .gateway import MyHOMEGatewayHandler

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = ["light", "switch", "cover", "climate", "binary_sensor", "sensor", "media_player"]


async def async_setup(hass, config):
    """Set up the MyHOME component."""
    hass.data[DOMAIN] = {}

    if DOMAIN not in config:
        return True

    LOGGER.error("configuration.yaml not supported for this component!")

    return False


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    if entry.data[CONF_MAC] not in hass.data[DOMAIN]:
        hass.data[DOMAIN][entry.data[CONF_MAC]] = {}

    _config_file_path = (
        str(entry.options[CONF_FILE_PATH])
        if CONF_FILE_PATH in entry.options
        else hass.config.path("myhome.yaml")
    )
    if _config_file_path.startswith("/config/"):
        _config_file_path = hass.config.path(_config_file_path[8:])

    _generate_events = (
        entry.options[CONF_GENERATE_EVENTS]
        if CONF_GENERATE_EVENTS in entry.options
        else False
    )

    _validated_config = {}
    try:
        async with aiofiles.open(_config_file_path, mode="r") as yaml_file:
            _validated_config = config_schema(yaml.safe_load(await yaml_file.read()))
    except FileNotFoundError:
        LOGGER.warning(f"Configuration file '{_config_file_path}' is not present. Using UI-configured devices.")
        _validated_config = {entry.data[CONF_MAC]: {}}
    except Exception as exc:
        LOGGER.error(f"Error loading configuration file '{_config_file_path}': {exc}")
        _validated_config = {entry.data[CONF_MAC]: {}}

    if entry.data[CONF_MAC] not in _validated_config:
        _validated_config[entry.data[CONF_MAC]] = {}

    hass.data[DOMAIN][entry.data[CONF_MAC]] = _validated_config[entry.data[CONF_MAC]]

    if CONF_PLATFORMS not in hass.data[DOMAIN][entry.data[CONF_MAC]]:
        hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS] = {}

    # Merge UI-configured devices from options
    ui_devices = entry.options.get("devices", {})
    for platform, devices in ui_devices.items():
        if platform not in hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS]:
            hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS][platform] = {}
        for dev_id, dev_conf in devices.items():
            merged_conf = dev_conf.copy()
            merged_conf[CONF_ENTITIES] = {}
            if CONF_MANUFACTURER not in merged_conf:
                merged_conf[CONF_MANUFACTURER] = "BTicino S.p.A."
            if CONF_DEVICE_MODEL not in merged_conf:
                merged_conf[CONF_DEVICE_MODEL] = None
            if CONF_ENTITY_NAME not in merged_conf:
                merged_conf[CONF_ENTITY_NAME] = None
            hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS][platform][dev_id] = merged_conf

    # Migrating the config entry's unique_id if it was not formated to the recommended hass standard
    if entry.unique_id != dr.format_mac(entry.unique_id):
        hass.config_entries.async_update_entry(
            entry, unique_id=dr.format_mac(entry.unique_id)
        )
        LOGGER.warning("Migrating config entry unique_id to %s", entry.unique_id)

    hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY] = MyHOMEGatewayHandler(
        hass=hass, config_entry=entry, generate_events=_generate_events
    )

    try:
        tests_results = await hass.data[DOMAIN][entry.data[CONF_MAC]][
            CONF_ENTITY
        ].test()
    except OSError as ose:
        _gateway_handler = hass.data[DOMAIN].pop(CONF_GATEWAY)
        _host = _gateway_handler.gateway.host
        raise ConfigEntryNotReady(
            f"Gateway cannot be reached at {_host}, make sure its address is correct."
        ) from ose

    if not tests_results["Success"]:
        if (
            tests_results["Message"] == "password_error"
            or tests_results["Message"] == "password_required"
        ):
            hass.async_create_task(
                hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": SOURCE_REAUTH},
                    data=entry.data,
                )
            )
        del hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY]
        return False

    _command_worker_count = (
        int(entry.options[CONF_WORKER_COUNT])
        if CONF_WORKER_COUNT in entry.options
        else 1
    )

    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    gateway_device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, entry.data[CONF_MAC])},
        identifiers={
            (DOMAIN, hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].unique_id)
        },
        manufacturer=hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].manufacturer,
        name=hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].name,
        model=hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].model,
        sw_version=hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].firmware,
    )

    await hass.config_entries.async_forward_entry_setups(
        entry, hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS].keys()
    )

    hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].listening_worker = (
        hass.loop.create_task(
            hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].listening_loop()
        )
    )
    for i in range(_command_worker_count):
        hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].sending_workers.append(
            hass.loop.create_task(
                hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].sending_loop(i)
            )
        )

    # Pruning lose entities and devices from the registry
    entity_entries = er.async_entries_for_config_entry(entity_registry, entry.entry_id)

    entities_to_be_removed = []
    devices_to_be_removed = [
        device_entry.id
        for device_entry in device_registry.devices.values()
        if entry.entry_id in device_entry.config_entries
    ]

    configured_entities = []

    for _platform in hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS].keys():
        for _device in hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS][
            _platform
        ].keys():
            for _entity_name in hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS][
                _platform
            ][_device][CONF_ENTITIES]:
                if _entity_name != _platform:
                    configured_entities.append(
                        f"{entry.data[CONF_MAC]}-{_device}-{_entity_name}"
                    )  # extrapolating _attr_unique_id out of the entity's place in the config data structure
                else:
                    configured_entities.append(
                        f"{entry.data[CONF_MAC]}-{_device}"
                    )  # extrapolating _attr_unique_id out of the entity's place in the config data structure

    for entity_entry in entity_entries:
        if entity_entry.unique_id in configured_entities:
            if entity_entry.device_id in devices_to_be_removed:
                devices_to_be_removed.remove(entity_entry.device_id)
            continue
        entities_to_be_removed.append(entity_entry.entity_id)

    for enity_id in entities_to_be_removed:
        entity_registry.async_remove(enity_id)

    if gateway_device_entry.id in devices_to_be_removed:
        devices_to_be_removed.remove(gateway_device_entry.id)

    for device_id in devices_to_be_removed:
        if (
            len(
                er.async_entries_for_device(
                    entity_registry, device_id, include_disabled_entities=True
                )
            )
            == 0
        ):
            device_registry.async_remove_device(device_id)

    # Defining the services
    async def handle_sync_time(call):
        gateway = call.data.get(ATTR_GATEWAY, None)
        if gateway is None:
            gateway = list(hass.data[DOMAIN].keys())[0]
        else:
            mac = format_mac(gateway)
            if mac is None:
                LOGGER.error(
                    "Invalid gateway mac `%s`, could not send time synchronisation message.",
                    gateway,
                )
                return False
            else:
                gateway = mac
        timezone = hass.config.as_dict()["time_zone"]
        if gateway in hass.data[DOMAIN]:
            await hass.data[DOMAIN][gateway][CONF_ENTITY].send(
                OWNGatewayCommand.set_datetime_to_now(timezone)
            )
        else:
            LOGGER.error(
                "Gateway `%s` not found, could not send time synchronisation message.",
                gateway,
            )
            return False

    hass.services.async_register(DOMAIN, "sync_time", handle_sync_time)

    async def handle_send_message(call):
        gateway = call.data.get(ATTR_GATEWAY, None)
        message = call.data.get(ATTR_MESSAGE, None)
        if gateway is None:
            gateway = list(hass.data[DOMAIN].keys())[0]
        else:
            mac = format_mac(gateway)
            if mac is None:
                LOGGER.error(
                    "Invalid gateway mac `%s`, could not send message `%s`.",
                    gateway,
                    message,
                )
                return False
            else:
                gateway = mac
        LOGGER.debug("Handling message `%s` to be sent to `%s`", message, gateway)
        if gateway in hass.data[DOMAIN]:
            if message is not None:
                own_message = OWNCommand.parse(message)
                if own_message is not None:
                    if own_message.is_valid:
                        LOGGER.debug(
                            "%s Sending valid OpenWebNet Message: `%s`",
                            hass.data[DOMAIN][gateway][CONF_ENTITY].log_id,
                            own_message,
                        )
                        await hass.data[DOMAIN][gateway][CONF_ENTITY].send(own_message)
                else:
                    LOGGER.error(
                        "Could not parse message `%s`, not sending it.", message
                    )
                    return False
        else:
            LOGGER.error(
                "Gateway `%s` not found, could not send message `%s`.", gateway, message
            )
            return False

    hass.services.async_register(DOMAIN, "send_message", handle_send_message)

    async def handle_scan_bus(call):
        gateway = call.data.get(ATTR_GATEWAY, None)
        output_file = call.data.get("output_file", "myhome_discovered.yaml")
        
        if gateway is None:
            gateway = list(hass.data[DOMAIN].keys())[0]
        else:
            mac = format_mac(gateway)
            if mac is None:
                LOGGER.error("Invalid gateway mac `%s`, could not start scan.", gateway)
                return False
            else:
                gateway = mac
                
        if gateway not in hass.data[DOMAIN]:
            LOGGER.error("Gateway `%s` not found, could not start scan.", gateway)
            return False
            
        gateway_handler = hass.data[DOMAIN][gateway][CONF_ENTITY]
        
        from .discovery import async_discover_all_devices
        try:
            LOGGER.info("Starting bus scan via service call...")
            discovered = await async_discover_all_devices(gateway_handler.gateway)
            
            yaml_data = {
                "myhome_gateway": {
                    "mac": gateway,
                }
            }
            for platform, devices in discovered.items():
                yaml_data["myhome_gateway"][platform] = {}
                for dev_id, dev_conf in devices.items():
                    where_key = dev_conf.get("where") or dev_conf.get("zone")
                    dev_key = f"{platform}_{where_key}"
                    dev_data = {
                        "who": dev_conf["who"],
                        "name": dev_conf["name"]
                    }
                    if "where" in dev_conf:
                        dev_data["where"] = dev_conf["where"]
                    if "zone" in dev_conf:
                        dev_data["zone"] = dev_conf["zone"]
                    if "dimmable" in dev_conf:
                        dev_data["dimmable"] = dev_conf["dimmable"]
                        
                    yaml_data["myhome_gateway"][platform][dev_key] = dev_data
            
            full_path = hass.config.path(output_file)
            async with aiofiles.open(full_path, mode="w") as out_f:
                await out_f.write(yaml.dump(yaml_data, default_flow_style=False))
                
            LOGGER.info("Bus scan completed successfully! Discovered devices written to %s", full_path)
            hass.components.persistent_notification.async_create(
                hass,
                title="MyHOME Bus Scan",
                message=f"Bus scan completed! Discovered devices exported to `{output_file}`.",
                notification_id="myhome_scan"
            )
        except Exception as err:
            LOGGER.exception("Bus scan via service failed: %s", err)
            return False

    hass.services.async_register(DOMAIN, "scan_bus", handle_scan_bus)

    async def handle_export_to_yaml(call):
        gateway = call.data.get(ATTR_GATEWAY, None)
        output_file = call.data.get("output_file", "myhome_exported.yaml")
        
        if gateway is None:
            gateway = list(hass.data[DOMAIN].keys())[0]
        else:
            mac = format_mac(gateway)
            if mac is None:
                LOGGER.error("Invalid gateway mac `%s`, could not export configuration.", gateway)
                return False
            else:
                gateway = mac
                
        if gateway not in hass.data[DOMAIN]:
            LOGGER.error("Gateway `%s` not found, could not export configuration.", gateway)
            return False
            
        entry = None
        for config_entry in hass.config_entries.async_entries(DOMAIN):
            if format_mac(config_entry.data.get(CONF_MAC)) == gateway:
                entry = config_entry
                break
                
        if not entry:
            LOGGER.error("Config entry not found for gateway %s", gateway)
            return False
            
        ui_devices = entry.options.get("devices", {})
        
        yaml_data = {
            "myhome_gateway": {
                "mac": gateway,
            }
        }
        for platform, devices in ui_devices.items():
            yaml_data["myhome_gateway"][platform] = {}
            for dev_id, dev_conf in devices.items():
                where_key = dev_conf.get("where") or dev_conf.get("zone")
                dev_key = f"{platform}_{where_key}"
                dev_data = {
                    "who": dev_conf["who"],
                    "name": dev_conf["name"]
                }
                if "where" in dev_conf:
                    dev_data["where"] = dev_conf["where"]
                if "zone" in dev_conf:
                    dev_data["zone"] = dev_conf["zone"]
                if "dimmable" in dev_conf:
                    dev_data["dimmable"] = dev_conf["dimmable"]
                    
                yaml_data["myhome_gateway"][platform][dev_key] = dev_data
                
        full_path = hass.config.path(output_file)
        async with aiofiles.open(full_path, mode="w") as out_f:
            await out_f.write(yaml.dump(yaml_data, default_flow_style=False))
            
        LOGGER.info("Export completed! Configured devices written to %s", full_path)
        hass.components.persistent_notification.async_create(
            hass,
            title="MyHOME Export",
            message=f"Configuration exported to `{output_file}`.",
            notification_id="myhome_export"
        )

    hass.services.async_register(DOMAIN, "export_to_yaml", handle_export_to_yaml)

    return True


async def async_unload_entry(hass, entry):
    """Unload a config entry."""

    LOGGER.info("Unloading MyHome entry.")

    for platform in hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS].keys():
        await hass.config_entries.async_forward_entry_unload(entry, platform)

    hass.services.async_remove(DOMAIN, "sync_time")
    hass.services.async_remove(DOMAIN, "send_message")
    hass.services.async_remove(DOMAIN, "scan_bus")
    hass.services.async_remove(DOMAIN, "export_to_yaml")

    gateway_handler = hass.data[DOMAIN][entry.data[CONF_MAC]].pop(CONF_ENTITY)
    del hass.data[DOMAIN][entry.data[CONF_MAC]]

    return await gateway_handler.close_listener()
