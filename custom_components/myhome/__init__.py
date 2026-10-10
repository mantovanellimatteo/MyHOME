""" MyHOME integration. """

from pathlib import Path
import os
import aiofiles
import yaml

from .ownd.message import OWNCommand, OWNGatewayCommand

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er, config_validation as cv
from homeassistant.const import CONF_MAC
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig

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


async def _async_register_frontend(hass: HomeAssistant):
    """Register static path and custom Lovelace card."""
    if hass.data.setdefault(DOMAIN, {}).get("_frontend_registered"):
        return
    hass.data[DOMAIN]["_frontend_registered"] = True
    frontend_dir = Path(__file__).parent / "frontend"
    if frontend_dir.exists():
        try:
            await hass.http.async_register_static_paths([
                StaticPathConfig("/myhome_static", str(frontend_dir), cache_headers=False)
            ])
        except Exception as err:
            LOGGER.debug(f"Static path registration: {err}")

        card_url = "/myhome_static/myhome-monitor-card.js?v=1.3.2"
        add_extra_js_url(hass, card_url)

        # Automatically register into Lovelace resources so it appears in the visual Card Picker and all dashboards
        async def _async_register_lovelace_resource(_event=None):
            try:
                lovelace = hass.data.get("lovelace")
                if lovelace and hasattr(lovelace, "resources"):
                    resources = lovelace.resources
                    if resources:
                        if not getattr(resources, "loaded", True):
                            await resources.async_load()
                            resources.loaded = True

                        registered = any(
                            "/myhome_static/myhome-monitor-card.js" in r.get("url", "")
                            for r in resources.async_items()
                        )
                        if not registered:
                            if hasattr(resources, "async_create_item"):
                                await resources.async_create_item({
                                    "res_type": "module",
                                    "url": card_url,
                                })
                            elif hasattr(resources, "data") and hasattr(resources.data, "append"):
                                resources.data.append({
                                    "type": "module",
                                    "url": card_url,
                                })
                            LOGGER.info("Successfully registered MyHOME Monitor Card in Lovelace resources")
            except Exception as res_err:
                LOGGER.debug("Could not auto-register Lovelace resource: %s", res_err)

        if hass.is_running:
            hass.async_create_task(_async_register_lovelace_resource())
        else:
            hass.bus.async_listen_once("homeassistant_started", _async_register_lovelace_resource)


async def _async_ensure_radio_logos(hass: HomeAssistant):
    """Ensure bundled radio logos are deployed to /config/www/loghi_radio without overwriting user files."""
    def _sync_logos():
        try:
            component_logos_dir = Path(__file__).parent / "logos"
            if not component_logos_dir.exists():
                return

            target_dir = Path(hass.config.path("www", "loghi_radio"))
            target_dir.mkdir(parents=True, exist_ok=True)

            copied_count = 0
            for logo_file in component_logos_dir.iterdir():
                if logo_file.is_file() and logo_file.suffix.lower() in (".png", ".jpg", ".jpeg", ".svg"):
                    dest_file = target_dir / logo_file.name
                    if not dest_file.exists():
                        import shutil
                        shutil.copy2(logo_file, dest_file)
                        copied_count += 1

            if copied_count > 0:
                LOGGER.info("MyHOME: Automatically deployed %d bundled radio logo(s) to %s", copied_count, target_dir)
        except Exception as err:
            LOGGER.warning("MyHOME: Could not auto-deploy bundled radio logos: %s", err)

    await hass.async_add_executor_job(_sync_logos)


async def async_setup(hass, config):
    """Set up the MyHOME component."""
    hass.data.setdefault(DOMAIN, {})
    await _async_register_frontend(hass)
    await _async_ensure_radio_logos(hass)

    if DOMAIN not in config:
        return True

    LOGGER.error("configuration.yaml not supported for this component!")

    return False


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    await _async_register_frontend(hass)
    await _async_ensure_radio_logos(hass)

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
    if "platforms" in ui_devices and isinstance(ui_devices["platforms"], dict):
        ui_devices = ui_devices["platforms"]

    for platform, devices in ui_devices.items():
        if platform in ("platforms", CONF_PLATFORMS) or not isinstance(devices, dict):
            continue
        if platform not in hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS]:
            hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS][platform] = {}
        for dev_id, dev_conf in devices.items():
            if not isinstance(dev_conf, dict):
                continue
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

    _mfg = hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].manufacturer
    if isinstance(_mfg, list):
        _mfg = ", ".join(str(m) for m in _mfg) if _mfg else "BTicino S.p.A."
    elif not isinstance(_mfg, str):
        _mfg = str(_mfg) if _mfg is not None else "BTicino S.p.A."

    gateway_device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, entry.data[CONF_MAC])},
        identifiers={
            (DOMAIN, hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].unique_id)
        },
        manufacturer=_mfg,
        name=hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].name,
        model=hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].model,
        sw_version=hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].firmware,
    )

    _platforms_to_setup = set(
        hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS].keys()
    ) | {"binary_sensor", "sensor"}
    await hass.config_entries.async_forward_entry_setups(
        entry, _platforms_to_setup
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

    async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry):
        """Rebuild decoder pool when options change."""
        from .decoder_pool import DecoderPool
        from .const import (
            CONF_DECODER_ENTITY,
            CONF_DECODER_SOURCE,
            CONF_DECODER_PRE_GAIN,
            CONF_DECODER_SLOTS,
            CONF_DECODER_MODE,
            DECODER_MODE_SHARED,
        )
        mac = entry.data[CONF_MAC]
        old_pool = hass.data.get(DOMAIN, {}).get(mac, {}).get("decoder_pool")
        if old_pool:
            await old_pool.release_all()

        options = entry.options
        decoder_map: dict[str, int] = {}
        pre_gain_map: dict[str, int] = {}
        decoder_mode = options.get(CONF_DECODER_MODE, DECODER_MODE_SHARED)
        for i in range(1, CONF_DECODER_SLOTS + 1):
            entity_id = options.get(CONF_DECODER_ENTITY.format(i), "").strip()
            source_num = options.get(CONF_DECODER_SOURCE.format(i), i)
            pre_gain = options.get(CONF_DECODER_PRE_GAIN.format(i), 0)
            if entity_id and entity_id.startswith("media_player."):
                decoder_map[entity_id] = int(source_num)
                pre_gain_map[entity_id] = int(pre_gain)

        pool = DecoderPool(hass, decoder_map, pre_gain_map, mode=decoder_mode)
        if DOMAIN in hass.data and mac in hass.data[DOMAIN]:
            hass.data[DOMAIN][mac]["decoder_pool"] = pool
            hass.data[DOMAIN][mac]["options"] = options
        LOGGER.info(
            "MyHOME: decoder pool rebuilt after options update — %d decoder(s) configured",
            len(decoder_map),
        )
        from homeassistant.helpers.dispatcher import async_dispatcher_send
        async_dispatcher_send(hass, f"myhome_pool_updated_{mac}")
        async_dispatcher_send(hass, f"myhome_radio_catalog_updated_{mac}")

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))


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

    # Protect gateway diagnostic entities from pruning
    configured_entities.append(f"{entry.data[CONF_MAC]}-gateway-connectivity")
    configured_entities.append(f"{entry.data[CONF_MAC]}-gateway-devices")

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
        device_entry = device_registry.async_get(device_id)
        if device_entry and device_entry.model == "Scenario Module":
            continue
            
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
            active_gateways = [k for k, v in hass.data[DOMAIN].items() if isinstance(v, dict) and CONF_ENTITY in v]
            if not active_gateways:
                LOGGER.error("No active MyHome gateway found to send message.")
                return False
            gateway = active_gateways[0]
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
            active_gateways = [k for k, v in hass.data[DOMAIN].items() if isinstance(v, dict) and CONF_ENTITY in v]
            if not active_gateways:
                LOGGER.error("No active MyHome gateway found, could not start scan.")
                return False
            gateway = active_gateways[0]
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
            active_gateways = [k for k, v in hass.data[DOMAIN].items() if isinstance(v, dict) and CONF_ENTITY in v]
            if not active_gateways:
                LOGGER.error("No active MyHome gateway found, could not export configuration.")
                return False
            gateway = active_gateways[0]
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

    _platforms_to_unload = set(
        hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS].keys()
    ) | {"binary_sensor", "sensor"}
    for platform in _platforms_to_unload:
        await hass.config_entries.async_forward_entry_unload(entry, platform)

    hass.services.async_remove(DOMAIN, "sync_time")
    hass.services.async_remove(DOMAIN, "send_message")
    hass.services.async_remove(DOMAIN, "scan_bus")
    hass.services.async_remove(DOMAIN, "export_to_yaml")

    gateway_handler = hass.data[DOMAIN][entry.data[CONF_MAC]].pop(CONF_ENTITY)
    del hass.data[DOMAIN][entry.data[CONF_MAC]]

    return await gateway_handler.close_listener()
