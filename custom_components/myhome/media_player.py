"""Support for MyHome sound diffusion (WHO 22) media players."""
from homeassistant.components.media_player import (
    DOMAIN as PLATFORM,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import (
    CONF_NAME,
    CONF_MAC,
)

from OWNd.message import OWNCommand, OWNMessage

from .const import (
    CONF_PLATFORMS,
    CONF_ENTITY,
    CONF_ENTITY_NAME,
    CONF_WHO,
    CONF_WHERE,
    CONF_BUS_INTERFACE,
    CONF_MANUFACTURER,
    CONF_DEVICE_MODEL,
    DOMAIN,
    LOGGER,
)
from .myhome_device import MyHOMEEntity
from .gateway import MyHOMEGatewayHandler


async def async_setup_entry(hass, config_entry, async_add_entities):
    if PLATFORM not in hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS]:
        return True

    _media_players = []
    _configured_players = hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS][PLATFORM]

    for _player in _configured_players.keys():
        _media_player = MyHOMEMediaPlayer(
            hass=hass,
            device_id=_player,
            who=_configured_players[_player][CONF_WHO],
            where=_configured_players[_player][CONF_WHERE],
            interface=_configured_players[_player][CONF_BUS_INTERFACE] if CONF_BUS_INTERFACE in _configured_players[_player] else None,
            name=_configured_players[_player][CONF_NAME],
            entity_name=_configured_players[_player][CONF_ENTITY_NAME],
            manufacturer=_configured_players[_player][CONF_MANUFACTURER],
            model=_configured_players[_player][CONF_DEVICE_MODEL],
            gateway=hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_ENTITY],
        )
        _media_players.append(_media_player)

    async_add_entities(_media_players)


async def async_unload_entry(hass, config_entry):
    if PLATFORM not in hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS]:
        return True

    _configured_players = hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS][PLATFORM]

    for _player in _configured_players.keys():
        del hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS][PLATFORM][_player]


class MyHOMEMediaPlayer(MyHOMEEntity, MediaPlayerEntity):
    """Representation of a MyHome Sound Diffusion entity."""

    def __init__(
        self,
        hass,
        name: str,
        entity_name: str,
        device_id: str,
        who: str,
        where: str,
        interface: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ):
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
        self._full_where = f"{self._where}#4#{self._interface}" if self._interface is not None else self._where

        # Supported features: Turn On/Off
        self._attr_supported_features = (
            MediaPlayerEntityFeature.TURN_ON | MediaPlayerEntityFeature.TURN_OFF
        )
        self._attr_state = MediaPlayerState.OFF

    async def async_update(self):
        """Update the entity state."""
        cmd = OWNCommand.parse(f"*#22*{self._full_where}##")
        if cmd is not None:
            await self._gateway_handler.send_status_request(cmd)

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Turn the sound diffusion zone on."""
        cmd = OWNCommand.parse(f"*22*1*{self._full_where}##")
        if cmd is not None:
            await self._gateway_handler.send(cmd)

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Turn the sound diffusion zone off."""
        cmd = OWNCommand.parse(f"*22*0*{self._full_where}##")
        if cmd is not None:
            await self._gateway_handler.send(cmd)

    def handle_event(self, message: OWNMessage):
        """Handle an event message."""
        LOGGER.info(
            "%s Sound Diffusion event: %s",
            self._gateway_handler.log_id,
            message,
        )
        if hasattr(message, "what") and message.what is not None:
            if message.what == "1":
                self._attr_state = MediaPlayerState.ON
            elif message.what == "0":
                self._attr_state = MediaPlayerState.OFF

        self.async_schedule_update_ha_state()
