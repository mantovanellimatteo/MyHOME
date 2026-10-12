"""Config flow to configure MyHome."""
import asyncio
import ipaddress
import re
import os
from typing import Dict, Optional

import async_timeout
from voluptuous import (
    Optional,
    Schema,
    Required,
    Coerce,
    All,
    In,
    Range,
    IsFile,
)
from homeassistant.config_entries import (
    CONN_CLASS_LOCAL_PUSH,
    ConfigEntry,
    ConfigFlow,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_ID,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)
from OWNd.connection import OWNGateway, OWNSession
from OWNd.discovery import find_gateways

from .const import (
    CONF_ADDRESS,
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    CONF_MANUFACTURER_URL,
    CONF_OWN_PASSWORD,
    CONF_SSDP_LOCATION,
    CONF_PLATFORMS,
    CONF_SSDP_ST,
    CONF_UDN,
    CONF_WORKER_COUNT,
    CONF_FILE_PATH,
    CONF_GENERATE_EVENTS,
    DOMAIN,
    LOGGER,
)
from .gateway import MyHOMEGatewayHandler


class MACAddress:
    def __init__(self, mac: str):
        mac = re.sub("[.:-]", "", mac).upper()
        mac = "".join(mac.split())
        if len(mac) != 12 or not mac.isalnum() or re.search("[G-Z]", mac) is not None:
            raise ValueError("Invalid MAC address")
        self.mac = mac

    def __repr__(self) -> str:
        return ":".join(["%s" % (self.mac[i : i + 2]) for i in range(0, 12, 2)])

    def __str__(self) -> str:
        return ":".join(["%s" % (self.mac[i : i + 2]) for i in range(0, 12, 2)])


import socket

def resolve_mac_from_ip(ip: str) -> Optional[str]:
    """Try to resolve MAC address from local ARP table."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.8)
        try:
            s.connect((ip, 20000))
            s.close()
        except Exception:
            pass

        if os.path.exists("/proc/net/arp"):
            with open("/proc/net/arp", "r") as arp_file:
                for line in arp_file:
                    parts = line.split()
                    if len(parts) >= 4 and parts[0] == ip:
                        mac = parts[3]
                        if mac and mac != "00:00:00:00:00:00":
                            return dr.format_mac(mac)
    except Exception as exc:
        LOGGER.debug("Could not resolve MAC from ARP for %s: %s", ip, exc)
    return None


def fallback_mac_from_ip(ip: str) -> str:
    """Generate a consistent MAC-like unique ID from IP if ARP resolution fails."""
    try:
        octets = [int(p) for p in ip.split(".")]
        if len(octets) == 4:
            return f"00:03:50:{octets[1]:02x}:{octets[2]:02x}:{octets[3]:02x}"
    except Exception:
        pass
    import hashlib
    h = hashlib.md5(ip.encode()).hexdigest()
    return f"00:03:50:{h[0:2]}:{h[2:4]}:{h[4:6]}"

class MyhomeFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a MyHome config flow."""

    VERSION = 1
    CONNECTION_CLASS = CONN_CLASS_LOCAL_PUSH

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return MyhomeOptionsFlowHandler()

    def __init__(self):
        """Initialize the MyHome flow."""
        self.gateway_handler: Optional[OWNGateway] = None
        self.discovered_gateways: Optional[Dict[str, dict]] = None
        self._existing_entry: Optional[ConfigEntry] = None
        self._new_entry_data: Optional[dict] = None
        self._new_entry_options: Optional[dict] = None
        self._onboarding_scan_task: Optional[asyncio.Task] = None

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        errors = {}

        # 1. Discover local gateways via SSDP (fast, 3s timeout)
        if self.discovered_gateways is None:
            try:
                with async_timeout.timeout(3):
                    local_gateways = await find_gateways()
            except Exception:
                local_gateways = []
            self.discovered_gateways = {
                gw["serialNumber"]: gw for gw in local_gateways if "serialNumber" in gw
            }

        already_configured = self._async_current_ids(False)

        if user_input is not None:
            nerd_mode = user_input.get("nerd_mode", False)
            selected_serial = user_input.get("gateway_select")

            if nerd_mode:
                suggested_ip = ""
                suggested_mac = None
                suggested_model = "F454"
                if selected_serial and selected_serial in self.discovered_gateways:
                    gw_data = self.discovered_gateways[selected_serial]
                    suggested_ip = gw_data.get("address", "")
                    suggested_mac = gw_data.get("serialNumber")
                    suggested_model = gw_data.get("modelName", "F454")
                return await self.async_step_nerd(
                    suggested_values={
                        "address": suggested_ip,
                        "serialNumber": suggested_mac,
                        "modelName": suggested_model,
                    }
                )

            # Zero-Knowledge Mode 🪄
            discovery_info = None
            if selected_serial and selected_serial in self.discovered_gateways:
                discovery_info = self.discovered_gateways[selected_serial]

            if not discovery_info:
                # If no gateway was selected or found, prompt user to use Nerd Mode
                errors["base"] = "no_gateway_found"
            else:
                self.gateway_handler = await OWNGateway.build_from_discovery_info(discovery_info)
                formatted_mac = dr.format_mac(self.gateway_handler.serial)

                if formatted_mac in already_configured:
                    return self.async_abort(reason="already_configured")

                await self.async_set_unique_id(formatted_mac, raise_on_progress=False)
                return await self.async_step_test_connection()

        # Build schema for Step 1
        schema_dict = {}
        available_gateways = {
            gw["serialNumber"]: f"🏠 {gw.get('modelName', 'MyHome')} Gateway ({gw.get('address')})"
            for gw in self.discovered_gateways.values()
            if dr.format_mac(gw.get("serialNumber", "")) not in already_configured
        }

        if available_gateways:
            schema_dict[Required("gateway_select", default=list(available_gateways.keys())[0])] = SelectSelector(
                SelectSelectorConfig(
                    options=[{"value": k, "label": v} for k, v in available_gateways.items()],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        schema_dict[Required("nerd_mode", default=False)] = BooleanSelector()

        return self.async_show_form(
            step_id="user",
            data_schema=Schema(schema_dict),
            errors=errors,
        )

    async def async_step_nerd(self, user_input=None, suggested_values=None):
        """Handle manual gateway configuration (Nerd Mode)."""
        errors = {}
        suggested = suggested_values or {}

        if user_input is not None:
            try:
                user_input["address"] = str(ipaddress.IPv4Address(user_input["address"]))
            except ipaddress.AddressValueError:
                errors["address"] = "invalid_ip"

            try:
                user_input["serialNumber"] = dr.format_mac(f'{MACAddress(user_input["serialNumber"])}')
            except ValueError:
                errors["serialNumber"] = "invalid_mac"

            if not errors:
                user_input["ssdp_location"] = None
                user_input["ssdp_st"] = None
                user_input["deviceType"] = None
                user_input["friendlyName"] = None
                user_input["manufacturer"] = "BTicino S.p.A."
                user_input["manufacturerURL"] = "http://www.bticino.it"
                user_input["modelNumber"] = None
                user_input["UDN"] = None
                self.gateway_handler = OWNGateway(user_input)
                await self.async_set_unique_id(user_input["serialNumber"], raise_on_progress=False)
                return await self.async_step_test_connection()

        address_sugg = user_input.get("address") if user_input else suggested.get("address", "")
        port_sugg = user_input.get("port") if user_input else suggested.get("port", 20000)
        mac_sugg = user_input.get("serialNumber") if user_input else suggested.get("serialNumber", "00:03:50:00:00:00")
        model_sugg = user_input.get("modelName") if user_input else suggested.get("modelName", "F454")

        return self.async_show_form(
            step_id="nerd",
            data_schema=Schema(
                {
                    Required("address", default=address_sugg): TextSelector(TextSelectorConfig()),
                    Required("port", default=port_sugg): NumberSelector(
                        NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX)
                    ),
                    Required("serialNumber", default=mac_sugg): TextSelector(TextSelectorConfig()),
                    Required("modelName", default=model_sugg): TextSelector(TextSelectorConfig()),
                }
            ),
            errors=errors,
        )

    async def async_step_custom(self, user_input=None, errors=None):
        """Legacy custom step alias pointing to nerd mode."""
        return await self.async_step_nerd(user_input)

    async def async_step_reauth(self, config: dict = None):
        """Perform reauth upon an authentication error."""
        self._existing_entry = await self.async_set_unique_id(config[CONF_MAC])
        self.gateway_handler = MyHOMEGatewayHandler(hass=self.hass, config_entry=self._existing_entry).gateway

        self.context.update(
            {
                CONF_HOST: self.gateway_handler.host,
                CONF_NAME: self.gateway_handler.model,
                CONF_MAC: self.gateway_handler.serial,
                "title_placeholders": {
                    CONF_HOST: self.gateway_handler.host,
                    CONF_NAME: self.gateway_handler.model,
                    CONF_MAC: self.gateway_handler.serial,
                },
            }
        )

        return await self.async_step_password(errors={CONF_OWN_PASSWORD: "password_error"})

    async def async_step_test_connection(self, user_input=None, errors={}):
        """Testing connection to the OWN Gateway."""
        gateway = self.gateway_handler
        assert gateway is not None

        self.context.update(
            {
                CONF_HOST: gateway.host,
                CONF_NAME: gateway.model_name,
                CONF_MAC: gateway.serial,
                "title_placeholders": {
                    CONF_HOST: gateway.host,
                    CONF_NAME: gateway.model_name,
                    CONF_MAC: gateway.serial,
                },
            }
        )

        test_session = OWNSession(gateway=gateway, logger=LOGGER)
        test_result = await test_session.test_connection()

        if test_result["Success"]:
            self._new_entry_data = {
                CONF_ID: dr.format_mac(gateway.serial),
                CONF_HOST: gateway.address,
                CONF_PORT: gateway.port,
                CONF_PASSWORD: gateway.password,
                CONF_SSDP_LOCATION: gateway.ssdp_location,
                CONF_SSDP_ST: gateway.ssdp_st,
                CONF_DEVICE_TYPE: gateway.device_type,
                CONF_FRIENDLY_NAME: gateway.friendly_name,
                CONF_MANUFACTURER: gateway.manufacturer,
                CONF_MANUFACTURER_URL: gateway.manufacturer_url,
                CONF_NAME: gateway.model_name,
                CONF_FIRMWARE: gateway.model_number,
                CONF_MAC: dr.format_mac(gateway.serial),
                CONF_UDN: gateway.udn,
            }
            self._new_entry_options = {
                CONF_WORKER_COUNT: (
                    self._existing_entry.options[CONF_WORKER_COUNT]
                    if self._existing_entry and CONF_WORKER_COUNT in self._existing_entry.options
                    else 1
                ),
            }

            if self._existing_entry:
                self.hass.config_entries.async_update_entry(
                    self._existing_entry,
                    data=self._new_entry_data,
                    options=self._new_entry_options,
                )
                await self.hass.config_entries.async_reload(self._existing_entry.entry_id)
                return self.async_abort(reason="reauth_successful")
            else:
                return await self.async_step_welcome()
        else:
            if test_result["Message"] == "password_required":
                return await self.async_step_password()
            elif test_result["Message"] in ("password_error", "password_retry"):
                errors["password"] = test_result["Message"]
                return await self.async_step_password(errors=errors)
            else:
                return self.async_abort(reason=test_result["Message"])

    async def async_step_port(self, user_input=None, errors={}):
        """Port information for the gateway is missing."""
        if user_input is not None:
            if 1 <= int(user_input[CONF_PORT]) <= 65535:
                self.gateway_handler.port = int(user_input[CONF_PORT])
                return await self.async_step_test_connection()
            errors["port"] = "invalid_port"

        return self.async_show_form(
            step_id="port",
            data_schema=Schema(
                {
                    Required(CONF_PORT, description={"suggested_value": 20000}): int,
                }
            ),
            description_placeholders={
                CONF_HOST: self.context[CONF_HOST],
                CONF_NAME: self.context[CONF_NAME],
                CONF_MAC: self.context[CONF_MAC],
            },
            errors=errors,
        )

    async def async_step_password(self, user_input=None, errors={}):
        """Password is required to connect the gateway."""
        if user_input is not None:
            self.gateway_handler.password = str(user_input[CONF_OWN_PASSWORD])
            return await self.async_step_test_connection()
        else:
            _suggested_password = self.gateway_handler.password if self.gateway_handler.password is not None else 12345

        return self.async_show_form(
            step_id="password",
            data_schema=Schema(
                {
                    Required(
                        CONF_OWN_PASSWORD,
                        description={"suggested_value": _suggested_password},
                    ): Coerce(str),
                }
            ),
            description_placeholders={
                CONF_HOST: self.context[CONF_HOST],
                CONF_NAME: self.context[CONF_NAME],
                CONF_MAC: self.context[CONF_MAC],
            },
            errors=errors,
        )

    async def async_step_welcome(self, user_input=None):
        """Welcome screen with zero-knowledge smart bus scan and YAML import."""
        if user_input is not None:
            auto_scan = user_input.get("auto_scan", False)
            import_yaml = user_input.get("import_yaml", False)

            current_devices = self._new_entry_options.setdefault("devices", {})

            # 1. Import from YAML if requested
            if import_yaml:
                try:
                    import aiofiles
                    import yaml
                    from .validate import config_schema
                    config_file = self.hass.config.path("myhome.yaml")
                    if not os.path.exists(config_file):
                        old_file = f"{config_file}.old"
                        if os.path.exists(old_file):
                            config_file = old_file

                    if os.path.exists(config_file):
                        async with aiofiles.open(config_file, mode="r") as yf:
                            yaml_content = yaml.safe_load(await yf.read())
                            validated = config_schema(yaml_content)
                            mac = dr.format_mac(self.gateway_handler.serial)
                            if mac in validated:
                                platforms_data = validated[mac].get(CONF_PLATFORMS, validated[mac])
                                for platform, devs in platforms_data.items():
                                    if platform in ("platforms", CONF_PLATFORMS) or not isinstance(devs, dict):
                                        continue
                                    current_devices.setdefault(platform, {}).update(devs)

                        if config_file.endswith("myhome.yaml"):
                            new_path = f"{config_file}.old"
                            if os.path.exists(new_path):
                                import time
                                new_path = f"{config_file}.{int(time.time())}.old"
                            os.rename(config_file, new_path)
                except Exception as err:
                    LOGGER.warning("Could not auto-import myhome.yaml during wizard: %s", err)

            # 2. Active Bus Scan if requested
            if auto_scan:
                return await self.async_step_onboarding_scan()

            return self._async_create_gateway_entry()

        schema_dict = {
            Required("auto_scan", default=True): BooleanSelector(),
            Required("import_yaml", default=False): BooleanSelector(),
        }

        return self.async_show_form(
            step_id="welcome",
            data_schema=Schema(schema_dict),
            description_placeholders={
                CONF_HOST: self.gateway_handler.host,
                CONF_NAME: self.gateway_handler.model_name,
                CONF_MAC: self.gateway_handler.serial,
            },
        )

    def _async_create_gateway_entry(self):
        """Create config entry for gateway."""
        return self.async_create_entry(
            title=f"{self.gateway_handler.model_name} Gateway",
            data=self._new_entry_data,
            options=self._new_entry_options,
        )

    async def async_step_onboarding_scan(self, user_input=None):
        """Show progress while performing quick bus scan on onboarding."""
        if not self._onboarding_scan_task:
            from .discovery import async_discover_all_devices
            LOGGER.info("Starting active bus discovery during onboarding...")
            self._onboarding_scan_task = self.hass.async_create_task(
                async_discover_all_devices(self.gateway_handler, quick=True)
            )

        if not self._onboarding_scan_task.done():
            return self.async_show_progress(
                step_id="onboarding_scan",
                progress_action="scanning_bus",
                progress_task=self._onboarding_scan_task,
            )

        try:
            discovered = await self._onboarding_scan_task
            current_devices = self._new_entry_options.setdefault("devices", {})
            for platform, devs in discovered.items():
                target_platform = current_devices.setdefault(platform, {})
                for dev_id, dev_conf in devs.items():
                    if dev_id not in target_platform:
                        target_platform[dev_id] = dev_conf
                    else:
                        LOGGER.info(
                            "Device %s already configured (e.g. from YAML import), keeping existing custom configuration",
                            dev_id,
                        )
        except Exception as err:
            LOGGER.warning("Bus discovery during onboarding encountered an issue: %s", err)
        finally:
            self._onboarding_scan_task = None

        return self.async_show_progress_done(next_step_id="onboarding_done")

    async def async_step_onboarding_done(self, user_input=None):
        """Finalize gateway creation after onboarding scan."""
        return self._async_create_gateway_entry()

    async def async_step_ssdp(self, discovery_info):
        """Handle a discovered OpenWebNet gateway."""
        _discovery_info = discovery_info.upnp
        _discovery_info["ssdp_st"] = discovery_info.ssdp_st
        _discovery_info["ssdp_location"] = discovery_info.ssdp_location
        _discovery_info["address"] = discovery_info.ssdp_headers["_host"]
        _discovery_info["port"] = 20000

        gateway = await OWNGateway.build_from_discovery_info(_discovery_info)
        await self.async_set_unique_id(dr.format_mac(gateway.unique_id))
        LOGGER.info("Found gateway: %s", gateway.address)
        updatable = {
            CONF_HOST: gateway.address,
            CONF_NAME: gateway.model_name,
            CONF_FRIENDLY_NAME: gateway.friendly_name,
            CONF_UDN: gateway.udn,
            CONF_FIRMWARE: gateway.firmware,
        }
        if gateway.port is not None:
            updatable[CONF_PORT] = gateway.port

        self._abort_if_unique_id_configured(updates=updatable)

        self.gateway_handler = gateway

        if self.gateway_handler.port is None:
            return await self.async_step_port()
        return await self.async_step_test_connection()

class MyhomeOptionsFlowHandler(OptionsFlow):
    """Handle MyHome options."""

    def __init__(self):
        """Initialize MyHome options flow."""
        self.options = {}
        self.data = {}
        self._scan_task: Optional[asyncio.Task] = None
        self._sniff_task: Optional[asyncio.Task] = None
        self._sniff_duration: int = 60
        self._new_device_count: int = 0

    async def async_step_init(self, user_input=None):  # pylint: disable=unused-argument
        """Manage the MyHome options."""
        self.options = dict(self.config_entry.options)
        self.data = dict(self.config_entry.data)
        if CONF_WORKER_COUNT not in self.options:
            self.options[CONF_WORKER_COUNT] = 1
        if CONF_FILE_PATH not in self.options:
            self.options[CONF_FILE_PATH] = "/config/myhome.yaml"
        if CONF_GENERATE_EVENTS not in self.options:
            self.options[CONF_GENERATE_EVENTS] = False
        if "enable_auto_learning" not in self.options:
            self.options["enable_auto_learning"] = False
            
        return await self.async_step_menu()

    async def async_step_menu(self, user_input=None):
        """Show selection menu for MyHome options."""
        if user_input is not None:
            choice = user_input["select_option"]
            if choice == "settings":
                return await self.async_step_user()
            elif choice == "scan":
                return await self.async_step_scan_active()
            elif choice == "sniff":
                return await self.async_step_sniff_passive()
            elif choice == "import_yaml":
                return await self.async_step_import_yaml()

        return self.async_show_form(
            step_id="menu",
            data_schema=Schema(
                {
                    Required("select_option", default="settings"): SelectSelector(
                        SelectSelectorConfig(
                            options=["settings", "scan", "sniff", "import_yaml"],
                            translation_key="menu_options",
                        )
                    )
                }
            ),
        )

    def _process_discovered_devices(self, discovered):
        """Update options with newly discovered devices and reload."""
        current_devices = self.options.get("devices", {})
        self._new_device_count = 0
        for platform, devices in discovered.items():
            if platform not in current_devices:
                current_devices[platform] = {}
            for dev_id, dev_conf in devices.items():
                if dev_id not in current_devices[platform]:
                    current_devices[platform][dev_id] = dev_conf
                    self._new_device_count += 1

        self.options["devices"] = current_devices

        # Automatically rename the YAML file to prevent it from being loaded again
        import os
        config_file_path = self.options.get("config_file_path", self.hass.config.path("myhome.yaml"))
        if os.path.exists(config_file_path):
            new_path = f"{config_file_path}.old"
            if os.path.exists(new_path):
                import time
                new_path = f"{config_file_path}.{int(time.time())}.old"
            os.rename(config_file_path, new_path)

        self.hass.config_entries.async_update_entry(
            self.config_entry,
            options=self.options
        )
        self.hass.async_create_task(
            self.hass.config_entries.async_reload(self.config_entry.entry_id)
        )

    async def async_step_scan_active(self, user_input=None):
        """Confirm active scan of the OpenWebNet bus."""
        if user_input is not None:
            return await self.async_step_scan_progress()

        return self.async_show_form(
            step_id="scan_active"
        )

    async def async_step_scan_progress(self, user_input=None):
        """Show progress while scanning bus."""
        if not self._scan_task:
            gateway_handler = self.hass.data[DOMAIN][self.config_entry.data[CONF_MAC]][CONF_ENTITY]
            gateway = gateway_handler.gateway
            from .discovery import async_discover_all_devices
            self._scan_task = self.hass.async_create_task(
                async_discover_all_devices(gateway)
            )

        if not self._scan_task.done():
            return self.async_show_progress(
                step_id="scan_progress",
                progress_action="scanning_bus",
                progress_task=self._scan_task,
            )

        try:
            discovered = await self._scan_task
            self._process_discovered_devices(discovered)
        except Exception as err:
            LOGGER.exception("Active scan failed: %s", err)
            return self.async_show_progress_done(next_step_id="scan_failed")
        finally:
            self._scan_task = None

        return self.async_show_progress_done(next_step_id="scan_done")

    async def async_step_scan_done(self, user_input=None):
        """Finish scan and show result."""
        return self.async_abort(
            reason="scan_completed",
            description_placeholders={"count": str(self._new_device_count)}
        )

    async def async_step_scan_failed(self, user_input=None):
        """Abort on scan failure."""
        return self.async_abort(reason="scan_failed")

    async def async_step_sniff_passive(self, user_input=None):
        """Configure passive sniffing duration."""
        errors = {}
        if user_input is not None:
            self._sniff_duration = int(user_input["duration"])
            return await self.async_step_sniff_progress()

        return self.async_show_form(
            step_id="sniff_passive",
            data_schema=Schema(
                {
                    Required("duration", default=60): All(Coerce(int), Range(min=10, max=300)),
                }
            ),
            errors=errors,
        )

    async def async_step_sniff_progress(self, user_input=None):
        """Show progress while sniffing bus."""
        if not self._sniff_task:
            gateway_handler = self.hass.data[DOMAIN][self.config_entry.data[CONF_MAC]][CONF_ENTITY]
            gateway = gateway_handler.gateway
            from .discovery import async_sniff_bus
            self._sniff_task = self.hass.async_create_task(
                async_sniff_bus(gateway, self._sniff_duration)
            )

        if not self._sniff_task.done():
            return self.async_show_progress(
                step_id="sniff_progress",
                progress_action="sniffing_bus",
                progress_task=self._sniff_task,
            )

        try:
            discovered = await self._sniff_task
            self._process_discovered_devices(discovered)
        except Exception as err:
            LOGGER.exception("Passive sniffing failed: %s", err)
            return self.async_show_progress_done(next_step_id="sniff_failed")
        finally:
            self._sniff_task = None

        return self.async_show_progress_done(next_step_id="sniff_done")

    async def async_step_sniff_done(self, user_input=None):
        """Finish sniffing and show result."""
        return self.async_abort(
            reason="sniff_completed",
            description_placeholders={"count": str(self._new_device_count)}
        )

    async def async_step_sniff_failed(self, user_input=None):
        """Abort on sniffing failure."""
        return self.async_abort(reason="sniff_failed")

    async def async_step_user(self, user_input=None, errors={}):  # pylint: disable=dangerous-default-value
        """Manage the MyHome devices options."""

        errors = {}

        if user_input is not None:
            if not os.path.isfile(user_input[CONF_FILE_PATH]):
                errors[CONF_FILE_PATH] = "invalid_config_path"

            self.options.update({CONF_WORKER_COUNT: user_input[CONF_WORKER_COUNT]})
            self.options.update({CONF_FILE_PATH: user_input[CONF_FILE_PATH]})
            self.options.update({CONF_GENERATE_EVENTS: user_input[CONF_GENERATE_EVENTS]})
            self.options.update({"enable_auto_learning": user_input["enable_auto_learning"]})

            _data_update = not (self.data[CONF_HOST] == user_input[CONF_ADDRESS] and self.data[CONF_OWN_PASSWORD] == user_input[CONF_OWN_PASSWORD])
            self.data.update({CONF_HOST: user_input[CONF_ADDRESS]})
            self.data.update({CONF_OWN_PASSWORD: user_input[CONF_OWN_PASSWORD]})

            try:
                self.data[CONF_HOST] = str(ipaddress.IPv4Address(self.data[CONF_HOST]))
            except ipaddress.AddressValueError:
                errors[CONF_ADDRESS] = "invalid_ip"

            if not errors:
                if _data_update:
                    self.hass.config_entries.async_update_entry(self.config_entry, data=self.data)
                    await self.hass.config_entries.async_reload(self.config_entry.entry_id)

                return self.async_create_entry(title="", data=self.options)

        return self.async_show_form(
            step_id="user",
            data_schema=Schema(
                {
                    Required(
                        CONF_ADDRESS,
                        description={"suggested_value": self.data[CONF_HOST]},
                    ): str,
                    Required(
                        CONF_OWN_PASSWORD,
                        description={"suggested_value": self.data[CONF_PASSWORD]},
                    ): str,
                    Required(
                        CONF_FILE_PATH,
                        description={"suggested_value": self.options[CONF_FILE_PATH]},
                    ): Coerce(str),
                    Required(
                        CONF_WORKER_COUNT,
                        description={"suggested_value": self.options[CONF_WORKER_COUNT]},
                    ): All(Coerce(int), Range(min=1, max=10)),
                    Required(
                        CONF_GENERATE_EVENTS,
                        description={"suggested_value": self.options[CONF_GENERATE_EVENTS]},
                    ): bool,
                    Required(
                        "enable_auto_learning",
                        description={"suggested_value": self.options["enable_auto_learning"]},
                    ): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_import_yaml(self, user_input=None):
        """Import devices from myhome.yaml."""
        if user_input is not None:
            return self.async_create_entry(title="", data=self.options)

        import aiofiles
        import yaml
        from .validate import config_schema

        
        _config_file_path = (
            str(self.options.get(CONF_FILE_PATH, "/config/myhome.yaml"))
        )
        if _config_file_path.startswith("/config/"):
            _config_file_path = self.hass.config.path(_config_file_path[8:])
        elif _config_file_path == "myhome.yaml":
            _config_file_path = self.hass.config.path("myhome.yaml")

        imported_count = 0
        error_msg = None

        try:
            async with aiofiles.open(_config_file_path, mode="r") as yaml_file:
                parsed_yaml = yaml.safe_load(await yaml_file.read())
                validated_config = config_schema(parsed_yaml)
                gateway_mac = self.config_entry.data[CONF_MAC]
                yaml_platforms = validated_config.get(gateway_mac, {}).get(CONF_PLATFORMS, {})
                
                current_devices = self.options.get("devices", {})
                current_devices.pop("platforms", None)
                current_devices.pop(CONF_PLATFORMS, None)

                for platform, devices in yaml_platforms.items():
                    if platform in ("platforms", CONF_PLATFORMS) or not isinstance(devices, dict):
                        continue
                    if platform not in current_devices:
                        current_devices[platform] = {}
                    for dev_id, dev_conf in devices.items():
                        current_devices[platform][dev_id] = dev_conf
                        imported_count += 1
                        
                self.options["devices"] = current_devices
                
                # Automatically rename the YAML file to prevent it from being loaded again
                import os
                if os.path.exists(_config_file_path):
                    new_path = f"{_config_file_path}.old"
                    if os.path.exists(new_path):
                        import time
                        new_path = f"{_config_file_path}.{int(time.time())}.old"
                    os.rename(_config_file_path, new_path)
        except FileNotFoundError:
            error_msg = f"File non trovato: {_config_file_path}"
        except Exception as e:
            error_msg = f"Errore durante la validazione del file YAML: {e}"

        description = f"**{imported_count} devices successfully imported from myhome.yaml!**\n\nYour `myhome.yaml` file has been automatically renamed to `myhome.yaml.old` to prevent conflicts.\n\nYou can now safely restart Home Assistant. All your entities will remain exactly as they were, but they will now be managed completely via the UI."
        if error_msg:
            description = f"**ERROR:** {error_msg}\nMake sure your `myhome.yaml` file exists and is formatted correctly."

        return self.async_show_form(
            step_id="import_yaml",
            description_placeholders={"summary": description},
        )
