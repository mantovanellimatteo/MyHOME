"""Support for MyHome sound diffusion (WHO 16 and WHO 22) media players."""
import asyncio
from homeassistant.components.media_player import (
    DOMAIN as PLATFORM,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import (
    CONF_NAME,
    CONF_MAC,
    CONF_ENTITIES,
)

from OWNd.message import OWNCommand, OWNMessage

from homeassistant.helpers.restore_state import RestoreEntity

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


SOURCES_WHO22 = {
    "Radio FM (Tuner)": "7",
    "Ingresso Locale AUX": "10",
}

# Mapping codici sorgente MyHome -> Nome sorgente Home Assistant
SRC_MAP_INV = {
    "7": "Radio FM (Tuner)",
    "4": "Radio FM (Tuner)",
    "1": "Radio FM (Tuner)",
    "0": "Radio FM (Tuner)",
    "2": "Ingresso Locale AUX",
    "3": "Ingresso Locale AUX",
    "10": "Ingresso Locale AUX",
    "11": "Ingresso Locale AUX",
    "12": "Ingresso Locale AUX",
    "13": "Ingresso Locale AUX",
}
TUNER_WHERE = "2#1"

# Registro dinamico dei preset radio (1..5) popolato dal bus OpenWebNet
TUNER_PRESETS: dict[int, str | None] = {
    1: None,
    2: None,
    3: None,
    4: None,
    5: None,
}


async def async_setup_entry(hass, config_entry, async_add_entities):
    if PLATFORM not in hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS]:
        hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS][PLATFORM] = {}

    _media_players = []
    _configured_players = hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS][PLATFORM]

    for _player in list(_configured_players.keys()):
        _media_player = MyHOMEMediaPlayer(
            hass=hass,
            device_id=_player,
            who=_configured_players[_player][CONF_WHO],
            where=_configured_players[_player][CONF_WHERE],
            interface=_configured_players[_player].get(CONF_BUS_INTERFACE),
            name=_configured_players[_player].get(CONF_NAME),
            entity_name=_configured_players[_player].get(CONF_ENTITY_NAME),
            manufacturer=_configured_players[_player].get(CONF_MANUFACTURER, "BTicino S.p.A."),
            model=_configured_players[_player].get(CONF_DEVICE_MODEL, None),
            gateway=hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_ENTITY],
        )
        if CONF_ENTITIES not in _configured_players[_player]:
            _configured_players[_player][CONF_ENTITIES] = {}
        _configured_players[_player][CONF_ENTITIES][PLATFORM] = _media_player
        _media_players.append(_media_player)

    async_add_entities(_media_players)

    def async_add_new_player(dev_id: str, who: str, where: str, name: str = None):
        """Dynamically add a new media player entity on the fly."""
        if dev_id in _configured_players and PLATFORM in _configured_players[dev_id].get(CONF_ENTITIES, {}):
            return _configured_players[dev_id][CONF_ENTITIES][PLATFORM]
        if name is None:
            name = f"Sound Zone {where}"
        new_player = MyHOMEMediaPlayer(
            hass=hass,
            device_id=dev_id,
            who=who,
            where=where,
            interface=None,
            name=name,
            entity_name=name,
            manufacturer="BTicino S.p.A.",
            model="F500N Sound Diffusion",
            gateway=hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_ENTITY],
        )
        if dev_id not in _configured_players:
            _configured_players[dev_id] = {
                CONF_WHO: who,
                CONF_WHERE: where,
                CONF_NAME: name,
                CONF_ENTITY_NAME: name,
            }
        if CONF_ENTITIES not in _configured_players[dev_id]:
            _configured_players[dev_id][CONF_ENTITIES] = {}
        _configured_players[dev_id][CONF_ENTITIES][PLATFORM] = new_player
        async_add_entities([new_player])
        LOGGER.info("Dynamically created new media_player: %s (%s)", name, where)
        return new_player

    hass.data[DOMAIN][config_entry.data[CONF_MAC]]["async_add_media_player"] = async_add_new_player

    # Initial single startup alignment query for central Tuner and zones
    async def _initial_startup_query():
        await asyncio.sleep(4)
        gateway = hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_ENTITY]
        try:
            # Ask the WHO 22 tuner only if there is a WHO 22 zone on this bus
            if any(player._who == "22" for player in _media_players):
                cmd = OWNCommand.parse(f"*#22*{TUNER_WHERE}*6##")
                if cmd:
                    await gateway.send(cmd)
            for player in _media_players:
                await asyncio.sleep(0.2)
                if player._who == "22":
                    q_cmd = OWNCommand.parse(f"*#22*{player._where}*12##")
                else:
                    q_cmd = OWNCommand.parse(f"*#16*{player._where}##")
                if q_cmd:
                    await gateway.send(q_cmd)
        except Exception as err:
            LOGGER.debug("Error in initial startup query: %s", err)

    hass.async_create_task(_initial_startup_query())


async def async_unload_entry(hass, config_entry):
    if PLATFORM not in hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS]:
        return True

    _configured_players = hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS][PLATFORM]

    for _player in _configured_players.keys():
        del hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS][PLATFORM][_player]


class MyHOMEMediaPlayer(MyHOMEEntity, MediaPlayerEntity, RestoreEntity):
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
        self._who = str(who).replace("#", "")
        self._where = str(where)
        self._full_where = str(where)
        self._gateway_handler = gateway
        self._gateway = gateway
        self._state = MediaPlayerState.OFF
        self._volume_level = 0.5
        self._source = "Radio FM (Tuner)"
        self._tuner_preset = 1
        self._tuner_freq = None
        self._media_title = "Radio FM P1"
        self._attr_should_poll = False
        self._supported_features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_STEP
            | MediaPlayerEntityFeature.SELECT_SOURCE
            | MediaPlayerEntityFeature.NEXT_TRACK
            | MediaPlayerEntityFeature.PREVIOUS_TRACK
            | MediaPlayerEntityFeature.PLAY_MEDIA
        )

    async def async_added_to_hass(self):
        """When entity is added to hass, restore state and register."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state:
            if last_state.state in [MediaPlayerState.PLAYING, MediaPlayerState.ON, "playing", "on"]:
                self._state = MediaPlayerState.PLAYING
            elif last_state.state in [MediaPlayerState.OFF, "off"]:
                self._state = MediaPlayerState.OFF

            attrs = last_state.attributes
            if "source" in attrs and attrs["source"]:
                self._source = attrs["source"]
            if "volume_level" in attrs and attrs["volume_level"] is not None:
                self._volume_level = attrs["volume_level"]
            if "media_title" in attrs and attrs["media_title"]:
                self._media_title = attrs["media_title"]
            if "radio_preset" in attrs and attrs["radio_preset"]:
                self._tuner_preset = attrs["radio_preset"]
            if "radio_frequenza" in attrs and attrs["radio_frequenza"]:
                self._tuner_freq = attrs["radio_frequenza"]
            self.async_write_ha_state()

    def _refresh_media_title(self):
        """Update media_title attribute based on current state, source, and tuner info."""
        if self._source in ["Radio FM (Tuner)", "7", "4"]:
            p_num = self._tuner_preset
            p_str = f"P{p_num}" if p_num else ""
            freq_str = self._tuner_freq or (TUNER_PRESETS.get(p_num) if p_num else None)

            if p_str and freq_str:
                self._media_title = f"Radio FM {p_str} - {freq_str}"
            elif freq_str:
                self._media_title = f"Radio FM - {freq_str}"
            elif p_str:
                self._media_title = f"Radio FM {p_str}"
            else:
                self._media_title = "Radio FM"
        elif self._source:
            self._media_title = self._source

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        return self._supported_features

    @property
    def state(self) -> MediaPlayerState | None:
        return self._state

    @property
    def volume_level(self) -> float | None:
        return self._volume_level

    @property
    def source(self) -> str | None:
        return self._source

    @property
    def source_list(self) -> list[str]:
        if self._who == "22":
            return list(SOURCES_WHO22.keys())
        return ["Sorgente 1"]

    @property
    def media_title(self) -> str | None:
        return self._media_title

    @property
    def extra_state_attributes(self):
        cur_val = int(round((self._volume_level or 0) * 31))
        attrs = {
            "volume_bticino": cur_val,
            "scala_volume": "1-31",
        }
        if self._source in ["Radio FM (Tuner)", "7", "4"]:
            if self._tuner_preset:
                attrs["radio_preset"] = self._tuner_preset
            freq = self._tuner_freq or (TUNER_PRESETS.get(self._tuner_preset) if self._tuner_preset else None)
            if freq:
                attrs["radio_frequenza"] = freq
            attrs["radio_presets"] = {
                f"P{k}": (v if v else "Non sintonizzato")
                for k, v in TUNER_PRESETS.items()
            }
        return attrs

    async def async_update(self):
        """Query actual zone state from the bus on startup or on demand."""
        try:
            if self._who == "22":
                await self._send_own_command(f"*#22*{self._where}*12##")
            else:
                await self._send_own_command(f"*#16*{self._where}##")
        except Exception as err:
            LOGGER.debug("Error querying bus state for %s: %s", self._where, err)

    async def _send_own_command(self, cmd_str: str):
        """Send raw OpenWebNet command safely."""
        try:
            LOGGER.info("[Sound Zone %s] Sending OpenWebNet command: %s", self._where, cmd_str)
            cmd = OWNCommand.parse(cmd_str) or cmd_str
            await self._gateway_handler.send(cmd)
        except Exception as err:
            LOGGER.error("Error sending MyHome command %s: %s", cmd_str, err)

    async def async_turn_on(self, **kwargs):
        """Turn the media player on."""
        src_id = SOURCES_WHO22.get(self._source, "7")
        if self._who == "22":
            await self._send_own_command(f"*22*1#4#{src_id}*{self._where}##")
            await self._send_own_command(f"*22*22#4#{src_id}*{self._where}##")
            await self._send_own_command(f"*22*22#4#{src_id}*5#{self._where}##")
        else:
            # WHO 16 amplifiers: *16*3*WHERE## is ON, *16*13*WHERE## is OFF
            cmd_str = f"*16*3*{self._where}##"
            await self._send_own_command(cmd_str)
        self._state = MediaPlayerState.PLAYING
        if src_id not in ["4", "7"] and self._source:
            self._media_title = self._source
        else:
            self._refresh_media_title()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn the media player off."""
        if self._who == "22":
            cmd_str = f"*22*0#4#0*{self._where}##"
        else:
            cmd_str = f"*16*13*{self._where}##"
        await self._send_own_command(cmd_str)
        self._state = MediaPlayerState.OFF
        self.async_write_ha_state()

    async def async_media_play(self):
        """Play media."""
        await self.async_turn_on()

    async def async_media_pause(self):
        """Pause media."""
        await self.async_turn_off()

    async def async_media_stop(self):
        """Stop media."""
        await self.async_turn_off()

    async def async_set_volume_level(self, volume: float):
        """Set volume level (scaled 1..31)."""
        val = max(1, min(31, int(round(volume * 31))))
        self._volume_level = round(val / 31.0, 2)
        if self._who == "22":
            cmd_str = f"*#22*{self._where}*#1*{val}##"
        else:
            cmd_str = f"*#16*{self._where}*#1*{val}##"
        await self._send_own_command(cmd_str)
        self.async_write_ha_state()

    async def async_volume_up(self):
        """Turn volume up for media player."""
        cmd_str = f"*22*3#1*{self._where}##" if self._who == "22" else f"*16*3#1*{self._where}##"
        await self._send_own_command(cmd_str)
        if self._volume_level is not None:
            cur_val = int(round(self._volume_level * 31))
            new_val = min(31, cur_val + 1)
            self._volume_level = round(new_val / 31.0, 2)
        self.async_write_ha_state()

    async def async_volume_down(self):
        """Turn volume down for media player."""
        cmd_str = f"*22*4#1*{self._where}##" if self._who == "22" else f"*16*4#1*{self._where}##"
        await self._send_own_command(cmd_str)
        if self._volume_level is not None:
            cur_val = int(round(self._volume_level * 31))
            new_val = max(1, cur_val - 1)
            self._volume_level = round(new_val / 31.0, 2)
        self.async_write_ha_state()

    async def async_media_next_track(self):
        """Next radio preset station (P1 -> P2 -> P3 -> P4 -> P5 -> P1)."""
        cmd_str = f"*22*11#1*{TUNER_WHERE}##"
        await self._send_own_command(cmd_str)

    async def async_media_previous_track(self):
        """Previous radio preset station (P1 -> P5 -> P4 -> P3 -> P2 -> P1)."""
        cmd_str = f"*22*12#1*{TUNER_WHERE}##"
        await self._send_own_command(cmd_str)

    async def async_seek_up(self):
        """Scan forward to the next radio station with a strong signal (Seek Up)."""
        cmd_str = f"*22*13#1*{TUNER_WHERE}##"
        await self._send_own_command(cmd_str)

    async def async_seek_down(self):
        """Scan backward to the previous radio station with a strong signal (Seek Down)."""
        cmd_str = f"*22*14#1*{TUNER_WHERE}##"
        await self._send_own_command(cmd_str)

    async def _tune_frequency(self, freq_float: float):
        """Tune specific frequency float and broadcast to tuner."""
        self._tuner_freq = f"{freq_float:.1f} MHz"
        matched_preset = None
        for p_num, p_freq in TUNER_PRESETS.items():
            if p_freq == self._tuner_freq:
                matched_preset = p_num
                break
        self._tuner_preset = matched_preset
        self._refresh_media_title()
        self.async_write_ha_state()

        freq_code = int(round(freq_float * 10))
        cmd_str = f"*#22*{TUNER_WHERE}*#5*1*{freq_code}##"
        await self._send_own_command(cmd_str)

    async def async_play_media(self, media_type: str, media_id: str, **kwargs):
        """Play radio preset, scan for next/previous station, or tune specific frequency."""
        media_type_l = (media_type or "").lower()
        media_id_str = str(media_id).strip()

        # 1. Scansione automatica emittenti (Seek Up / Seek Down)
        if media_type_l in ["scan", "seek", "search", "step", "frequency_step", "tune_step"]:
            if media_id_str.lower() in ["up", "+", "+1", "next", "forward", "seek_up", "scan_up", "+0.1"]:
                await self.async_seek_up()
                return
            elif media_id_str.lower() in ["down", "-", "-1", "prev", "previous", "backward", "seek_down", "scan_down", "-0.1"]:
                await self.async_seek_down()
                return

        # 2. Selezione diretta preset (es. media_type='preset' o media_id='1'..'5' o 'P1'..'P5')
        p_clean = media_id_str.upper().replace("P", "").strip()
        if media_type_l in ["preset", "channel", "track"] or (p_clean.isdigit() and 1 <= int(p_clean) <= 5 and "." not in media_id_str):
            p_val = int(p_clean)
            self._tuner_preset = p_val
            stored_freq = TUNER_PRESETS.get(p_val)
            if stored_freq:
                self._tuner_freq = stored_freq
            self._refresh_media_title()
            self.async_write_ha_state()
            cmd_str = f"*#22*{TUNER_WHERE}*#6*{p_val}##"
            await self._send_own_command(cmd_str)
            return

        # 3. Sintonizzazione frequenza numerica diretta (es. media_type='frequency' o media_id='95.2' o '102.5 MHz')
        f_clean = media_id_str.upper().replace("MHZ", "").strip()
        try:
            f_float = float(f_clean)
            if 87.0 <= f_float <= 108.5:
                await self._tune_frequency(f_float)
        except ValueError:
            LOGGER.warning("Formato media_id frequenza non riconosciuto: %s", media_id)

    async def async_select_source(self, source: str):
        """Select input source directly via verified 3-frame sequence with bus timing."""
        self._source = source
        if self._who == "22":
            src_num = "1" if source == "Radio FM (Tuner)" else "2"
            if self._state != MediaPlayerState.PLAYING:
                await self._send_own_command(f"*22*1*{self._where}##")
                await asyncio.sleep(0.15)
                self._state = MediaPlayerState.PLAYING

            await self._send_own_command(f"*22*22#4#7*5#{self._where}##")
            await asyncio.sleep(0.18)
            await self._send_own_command(f"*22*22#4#7*2#{src_num}##")
            await asyncio.sleep(0.18)
            await self._send_own_command(f"*22*2#4#7*5#2#{src_num}##")
        else:
            src_id = "1" if source == "Radio FM (Tuner)" else "2"
            await self._send_own_command(f"*16*1#{src_id}*{self._where}##")
            self._state = MediaPlayerState.PLAYING

        self._refresh_media_title()
        self.async_write_ha_state()

    def handle_event(self, event):
        """Handle status update from the bus."""
        msg_str = str(event)
        LOGGER.debug("%s [Sound Zone %s] Received bus event: %s", self._gateway.log_id, self._where, msg_str)

        # 0. Source switching synchronization (Tuner 2#1 vs AUX 2#2)
        if ("*2#1##" in msg_str or "*5#2#1##" in msg_str) and "*22*" in msg_str:
            self._source = "Radio FM (Tuner)"
            self._refresh_media_title()
            self.async_write_ha_state()
            return
        elif ("*2#2##" in msg_str or "*5#2#2##" in msg_str) and "*22*" in msg_str:
            self._source = "Ingresso Locale AUX"
            self._refresh_media_title()
            self.async_write_ha_state()
            return

        # 1. Frequency message from Tuner (Dimension 5: *#22*5#2#1*5*1*<freq>## or *#22*2#1*5*1*<freq>##)
        if ("*5*1*" in msg_str or "*#5*1*" in msg_str) and ("*22*" in msg_str or "*#22*" in msg_str):
            try:
                # Extract parts delimited by '*'
                parts = [p for p in msg_str.strip("#").split("*") if p]
                # Dimension 5 (frequency read or write)
                if len(parts) >= 4:
                    val_str = parts[-1]
                    if val_str.isdigit():
                        freq_val = int(val_str)
                        freq_str = None
                        if 870 <= freq_val <= 1085:
                            freq_str = f"{freq_val / 10.0:.1f} MHz"
                        elif 8700 <= freq_val <= 10850:
                            f_float = freq_val / 100.0
                            freq_str = f"{f_float:.2f}".rstrip("0").rstrip(".") + " MHz" if (freq_val % 10 != 0) else f"{f_float:.1f} MHz"
                        elif 87000 <= freq_val <= 108500:
                            f_float = freq_val / 1000.0
                            freq_str = f"{f_float:.2f}".rstrip("0").rstrip(".") + " MHz" if (freq_val % 100 != 0) else f"{f_float:.1f} MHz"

                        if freq_str:
                            self._tuner_freq = freq_str
                            if self._tuner_preset and 1 <= self._tuner_preset <= 5:
                                TUNER_PRESETS[self._tuner_preset] = freq_str
                            else:
                                for p_num, p_freq in TUNER_PRESETS.items():
                                    if p_freq == freq_str:
                                        self._tuner_preset = p_num
                                        break
                            self._refresh_media_title()
                            self.async_write_ha_state()
            except Exception as ex:
                LOGGER.debug("Error parsing frequency %s: %s", msg_str, ex)

        # 2. Preset message from Tuner (Dimension 6: *#22*2#1*6*<preset>## or *#22*2#1*#6*<preset>##)
        elif ("*6*" in msg_str or "*#6*" in msg_str) and ("*2#1" in msg_str or "*5#2#1" in msg_str):
            try:
                parts = [p for p in msg_str.strip("#").split("*") if p]
                if parts:
                    last_val = parts[-1]
                    if last_val.isdigit():
                        preset_val = int(last_val)
                        if 1 <= preset_val <= 5:
                            self._tuner_preset = preset_val
                            stored_freq = TUNER_PRESETS.get(preset_val)
                            if stored_freq:
                                self._tuner_freq = stored_freq
                            self._refresh_media_title()
                            self.async_write_ha_state()
                        elif preset_val == 0:
                            self._tuner_preset = None
                            self._refresh_media_title()
                            self.async_write_ha_state()
            except Exception as ex:
                LOGGER.debug("Error parsing preset %s: %s", msg_str, ex)

        # 3a. WHO 16 amplifier power frames, matched exactly so that e.g. zone 1
        # never reacts to zone 11: ON = *16*3* / *16*0*, OFF = *16*13* / *16*10*
        if self._who == "16":
            frame = msg_str.strip()
            if frame in (f"*16*3*{self._where}##", f"*16*0*{self._where}##"):
                self._state = MediaPlayerState.PLAYING
                self._refresh_media_title()
                self.async_write_ha_state()
                return
            if frame in (f"*16*13*{self._where}##", f"*16*10*{self._where}##"):
                self._state = MediaPlayerState.OFF
                self.async_write_ha_state()
                return

        # 3. Messages destined for this room's amplifier
        if self._where in msg_str or self._full_where in msg_str:
            clean = msg_str.strip("#").split("*")
            norm_parts = [p.replace("#", "") for p in clean if p]

            # Dimension 12 state: *#22*WHERE*12*ST*SRC## or *#22*WHERE*#12*ST*SRC##
            if "12" in norm_parts and ("*12*" in msg_str or "*#12*" in msg_str):
                try:
                    idx = norm_parts.index("12")
                    if len(norm_parts) > idx + 1:
                        st = norm_parts[idx + 1]
                        if st == "0":
                            self._state = MediaPlayerState.OFF
                        elif st == "1":
                            self._state = MediaPlayerState.PLAYING
                            if len(norm_parts) > idx + 2:
                                src_code = norm_parts[idx + 2]
                                if src_code in SRC_MAP_INV:
                                    self._source = SRC_MAP_INV[src_code]
                                    self._refresh_media_title()
                                    LOGGER.debug("[Sound Zone %s] Source synchronized from bus state: %s", self._where, self._source)
                    self.async_write_ha_state()
                except Exception as ex:
                    LOGGER.debug("Error parsing source state %s: %s", msg_str, ex)

            # Command or event with source selection (#4#SRC)
            # e.g. *22*1#4#1*WHERE##, *22*22#4#1*WHERE##, *22*2#4#1*WHERE##, *22*0#4#0*WHERE##
            elif "#4#" in msg_str:
                try:
                    for part in clean:
                        if "#4#" in part:
                            src_code = part.split("#")[-1]
                            if src_code == "0":
                                self._state = MediaPlayerState.OFF
                            elif src_code in SRC_MAP_INV:
                                self._state = MediaPlayerState.PLAYING
                                self._source = SRC_MAP_INV[src_code]
                                self._refresh_media_title()
                                LOGGER.debug("[Sound Zone %s] Source updated from bus (#4#): %s", self._where, self._source)
                    self.async_write_ha_state()
                except Exception as ex:
                    LOGGER.debug("Error parsing #4# source %s: %s", msg_str, ex)

            # Generic source toggle command (e.g. *22*22#4*WHERE## from physical wall button)
            elif "#4" in msg_str:
                try:
                    self.hass.async_create_task(self.async_update())
                except Exception as ex:
                    LOGGER.debug("Error requesting update for #4: %s", ex)

            # Simple ON command: *22*1*WHERE## or *16*1*WHERE##
            elif msg_str.startswith(f"*22*1*{self._where}") or msg_str.startswith(f"*16*1*{self._where}"):
                self._state = MediaPlayerState.PLAYING
                self._refresh_media_title()
                self.async_write_ha_state()

            # Simple OFF command: *22*0*WHERE## or *16*0*WHERE##
            elif msg_str.startswith(f"*22*0*{self._where}") or msg_str.startswith(f"*16*0*{self._where}"):
                self._state = MediaPlayerState.OFF
                self.async_write_ha_state()

            # Dimension 1 state (Volume): *#22*WHERE*1*VAL## or *#22*WHERE*#1*VAL##
            elif "*1*" in msg_str or "*#1*" in msg_str:
                try:
                    if "1" in norm_parts:
                        idx = norm_parts.index("1")
                        if len(norm_parts) > idx + 1:
                            val = int(norm_parts[idx + 1])
                            if 1 <= val <= 31:
                                self._volume_level = round(val / 31.0, 2)
                                self.async_write_ha_state()
                except Exception as ex:
                    LOGGER.debug("Error parsing volume %s: %s", msg_str, ex)
