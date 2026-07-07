"""Support for MyHome sound diffusion (WHO 16 and WHO 22) media players."""
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

        # Supported features and attributes
        self._attr_supported_features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_MUTE
        )

        if self._who == "16":
            self._attr_supported_features |= MediaPlayerEntityFeature.SELECT_SOURCE
            self._attr_source_list = ["Source 1", "Source 2", "Source 3", "Source 4"]
        else:
            self._attr_source_list = []

        self._attr_state = MediaPlayerState.OFF
        self._attr_source = None
        self._attr_volume_level = 0.5  # default to 50%
        self._hardware_volume = 15     # default hardware volume (middle of 0-31 range)
        self._is_muted = False
        self._pre_mute_volume = 15     # cached hardware volume level before mute

    @property
    def is_volume_muted(self) -> bool:
        """Return True if volume is muted."""
        return self._is_muted

    async def async_update(self):
        """Update the entity state."""
        # Query status: *#WHO*WHERE##
        cmd = OWNCommand.parse(f"*#{self._who}*{self._full_where}##")
        if cmd is not None:
            await self._gateway_handler.send_status_request(cmd)
            
        # Query volume dimension if WHO 16: *#16*WHERE*1##
        if self._who == "16":
            vol_cmd = OWNCommand.parse(f"*#16*{self._full_where}*1##")
            if vol_cmd is not None:
                await self._gateway_handler.send_status_request(vol_cmd)

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Turn the sound diffusion zone on."""
        if self._who == "16":
            # Turn on using the active/cached source, default to Source 1
            src_num = 1
            if self._attr_source in self._attr_source_list:
                src_num = self._attr_source_list.index(self._attr_source) + 1
            cmd = OWNCommand.parse(f"*16*{10 + src_num}*{self._full_where}##")
        else:
            cmd = OWNCommand.parse(f"*22*1*{self._full_where}##")
            
        if cmd is not None:
            await self._gateway_handler.send(cmd)

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Turn the sound diffusion zone off."""
        if self._who == "16":
            cmd = OWNCommand.parse(f"*16*10*{self._full_where}##")
        else:
            cmd = OWNCommand.parse(f"*22*0*{self._full_where}##")
            
        if cmd is not None:
            await self._gateway_handler.send(cmd)

    async def async_select_source(self, source):
        """Select input source."""
        if self._who == "16" and source in self._attr_source_list:
            src_num = self._attr_source_list.index(source) + 1
            # In WHO 16, sending *16*1X*WHERE## selects source X and turns on the zone
            cmd = OWNCommand.parse(f"*16*{10 + src_num}*{self._full_where}##")
            if cmd is not None:
                await self._gateway_handler.send(cmd)

    async def async_set_volume_level(self, volume):
        """Set volume level, range 0..1."""
        # Convert float (0..1) to hardware volume (0..31)
        hardware_vol = int(volume * 31)
        hardware_vol = max(0, min(31, hardware_vol))
        
        # Disable mute if setting volume to positive
        if hardware_vol > 0 and self._is_muted:
            self._is_muted = False
            
        cmd = OWNCommand.parse(f"*#16*{self._full_where}*#1*{hardware_vol}##")
        if cmd is not None:
            await self._gateway_handler.send(cmd)

    async def async_volume_up(self):
        """Step volume up."""
        # Increase hardware volume by 2 steps
        new_hw_vol = min(31, self._hardware_volume + 2)
        await self.async_set_volume_level(new_hw_vol / 31.0)

    async def async_volume_down(self):
        """Step volume down."""
        # Decrease hardware volume by 2 steps
        new_hw_vol = max(0, self._hardware_volume - 2)
        await self.async_set_volume_level(new_hw_vol / 31.0)

    async def async_mute_volume(self, mute):
        """Mute/unmute volume (Emulated)."""
        if mute:
            if not self._is_muted:
                self._pre_mute_volume = self._hardware_volume
                self._is_muted = True
                # Set volume to 0 to emulate mute
                cmd = OWNCommand.parse(f"*#16*{self._full_where}*#1*0##")
                if cmd is not None:
                    await self._gateway_handler.send(cmd)
        else:
            if self._is_muted:
                self._is_muted = False
                # Restore pre-mute volume
                cmd = OWNCommand.parse(f"*#16*{self._full_where}*#1*{self._pre_mute_volume}##")
                if cmd is not None:
                    await self._gateway_handler.send(cmd)

    def handle_event(self, message: OWNMessage):
        """Handle an event message."""
        LOGGER.info(
            "%s Sound Diffusion event: %s",
            self._gateway_handler.log_id,
            message,
        )
        
        # 1. Parse ON/OFF and Source events
        if hasattr(message, "what") and message.what is not None:
            what = str(message.what)
            
            # WHO 16 parsing
            if self._who == "16":
                if what == "10":
                    self._attr_state = MediaPlayerState.OFF
                elif what in ["11", "12", "13", "14"]:
                    self._attr_state = MediaPlayerState.ON
                    src_num = int(what) - 10
                    self._attr_source = f"Source {src_num}"
            # WHO 22 parsing
            elif self._who == "22":
                if what == "0":
                    self._attr_state = MediaPlayerState.OFF
                elif what == "1":
                    self._attr_state = MediaPlayerState.ON

        # 2. Parse Absolute Volume dimension: *#16*WHERE*1*VOL## or *#22*WHERE*1*VOL##
        # When a dimension message is parsed, it might be an OWNMessage with dimension properties.
        # OpenWebNet format for dimension value feedback is: *#WHO*WHERE*1*VOL## (where 1 is the volume dimension)
        # We can extract the volume value by parsing raw message string or checking properties.
        # Let's write a regex/string match to extract absolute volume from message frames
        msg_str = str(message)
        if "*#" in msg_str:
            # Format: *#16*WHERE*1*VOL## or similar
            import re
            match = re.match(r"\*#(?:16|22)\*(\d+)\*1\*(\d+)##", msg_str)
            if match:
                # Extract volume (0-31 scale)
                vol_val = int(match.group(2))
                self._hardware_volume = vol_val
                self._attr_volume_level = vol_val / 31.0
                
                # Update muted flag based on volume state
                if vol_val == 0:
                    self._is_muted = True
                else:
                    self._is_muted = False

        self.async_schedule_update_ha_state()
