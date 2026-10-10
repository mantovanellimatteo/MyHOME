import asyncio
from datetime import timedelta
import re
import time

SCAN_INTERVAL = timedelta(seconds=10)

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
    EVENT_STATE_CHANGED,
)
from homeassistant.core import callback, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event

from .ownd.message import (
    OWNCommand,
    OWNMessage,
    OWNSoundEvent,
    OWNSoundCommand,
    OWNFilodiffusioneEvent,
    OWNFilodiffusioneCommand,
)
from .decoder_pool import DecoderPool
from .const import (
    CONF_PLATFORMS,
    CONF_ENTITY,
    CONF_ENTITY_NAME,
    CONF_WHO,
    CONF_WHERE,
    CONF_BUS_INTERFACE,
    CONF_MANUFACTURER,
    CONF_DEVICE_MODEL,
    CONF_DECODER_ENTITY,
    CONF_DECODER_SOURCE,
    CONF_DECODER_PRE_GAIN,
    CONF_DECODER_SLOTS,
    CONF_DECODER_MODE,
    DECODER_MODE_SHARED,
    DOMAIN,
    LOGGER,
)
from .myhome_device import MyHOMEEntity
from .gateway import MyHOMEGatewayHandler


SOURCES_WHO22 = {
    "Radio FM (Tuner)": "1",
    "Ingresso AUX 1": "1",
    "Ingresso AUX 2": "2",
    "Ingresso AUX 3": "3",
    "Ingresso AUX 4": "4",
}

# Mapping codici sorgente MyHome -> Nome sorgente Home Assistant di base
SRC_MAP_INV = {
    "1": "Radio FM (Tuner)",
    "7": "Radio FM (Tuner)",
    "0": "Radio FM (Tuner)",
    "2": "Ingresso AUX 2",
    "3": "Ingresso AUX 3",
    "4": "Ingresso AUX 4",
    "10": "Radio FM (Tuner)",
    "11": "Radio FM (Tuner)",
    "12": "Ingresso AUX 2",
    "13": "Ingresso AUX 3",
    "14": "Ingresso AUX 4",
}
TUNER_WHERE = "2#1"

# Registro dinamico e condiviso dei preset radio (1..5) del sintonizzatore centrale F500
# Popolato dinamicamente dal bus OpenWebNet e ripristinato dallo stato di Home Assistant
TUNER_PRESETS: dict[int, str | None] = {
    1: None,
    2: None,
    3: None,
    4: None,
    5: None,
}

TUNER_STATE: dict = {
    "preset": 1,
    "freq": None,
    "presets": TUNER_PRESETS,
    "transit_until": 0.0,
    "target_preset": None,
    "old_freq": None,
}

# ── Tabella di Calibrazione Psicoacustica Volume (LUT 1..31) ─────────────────
# Mappa i 31 step discreti dell'amplificatore BTicino su una curva ad alta prontezza:
# - Range 0% - 50%: progressione decisa (Step 21 raggiunto a metà slider, per un ascolto subito presente).
# - Range 50% - 100%: progressione morbida e distesa (gli ultimi 10 step spalmati su tutta la seconda metà).
VOLUME_LUT: tuple[float, ...] = (
    0.0,   # 0 (non usato)
    0.01, 0.02, 0.03, 0.04, 0.05,  # Step 1..5
    0.06, 0.07, 0.09, 0.11, 0.13,  # Step 6..10
    0.16, 0.19, 0.22, 0.26, 0.30,  # Step 11..15
    0.34, 0.38, 0.42, 0.46, 0.48,  # Step 16..20
    0.51, 0.55, 0.60, 0.65, 0.70,  # Step 21..25 (Step 21 = ~50%)
    0.76, 0.82, 0.88, 0.93, 0.97,  # Step 26..30
    1.00                           # Step 31
)


def bticino_volume_to_ha(step: int) -> float:
    """Convert physical BTicino step (1..31) to Home Assistant volume level (0.0..1.0)."""
    if step <= 0:
        return 0.0
    if step >= 31:
        return 1.0
    return VOLUME_LUT[step]


def ha_volume_to_bticino(vol: float) -> int:
    """Convert Home Assistant volume level (0.0..1.0) to physical BTicino step (1..31)."""
    if vol <= 0.01:
        return 1
    if vol >= 0.99:
        return 31
    best_step = 1
    best_diff = 999.0
    for s in range(1, 32):
        diff = abs(VOLUME_LUT[s] - vol)
        if diff < best_diff:
            best_diff = diff
            best_step = s
    return best_step


def _build_pool(hass: HomeAssistant, config_entry) -> DecoderPool:
    """Build a DecoderPool from the current options entry."""
    options = config_entry.options
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

    return DecoderPool(hass, decoder_map, pre_gain_map, mode=decoder_mode)


async def async_setup_entry(hass: HomeAssistant, config_entry, async_add_entities):
    """Set up MyHome media player entities and initialize the decoder pool."""
    mac = config_entry.data[CONF_MAC]

    # Initialize and store DecoderPool in hass.data
    pool = _build_pool(hass, config_entry)
    hass.data[DOMAIN][mac]["decoder_pool"] = pool

    if PLATFORM not in hass.data[DOMAIN][mac][CONF_PLATFORMS]:
        hass.data[DOMAIN][mac][CONF_PLATFORMS][PLATFORM] = {}

    _media_players = []
    _configured_players = hass.data[DOMAIN][mac][CONF_PLATFORMS][PLATFORM]

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
            gateway=hass.data[DOMAIN][mac][CONF_ENTITY],
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
            model="F500N Sound Diffusion" if str(who) == "22" else "Audio System",
            gateway=hass.data[DOMAIN][mac][CONF_ENTITY],
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
        LOGGER.info("Creato dinamicamente nuovo media_player: %s (%s)", name, where)
        return new_player

    hass.data[DOMAIN][mac]["async_add_media_player"] = async_add_new_player

    # Allineamento iniziale singolo al boot per il Tuner centrale e le zone
    async def _initial_startup_query():
        await asyncio.sleep(4)
        gateway = hass.data[DOMAIN][mac][CONF_ENTITY]
        try:
            cmd = OWNCommand.parse(f"*#22*{TUNER_WHERE}*6##")
            if cmd:
                await gateway.send_status_request(cmd)
            for player in _media_players:
                await asyncio.sleep(0.2)
                if player._who == "22":
                    q_cmd = OWNCommand.parse(f"*#22*{player._where}*12##")
                else:
                    q_cmd = OWNCommand.parse(f"*#16*{player._where}##")
                if q_cmd:
                    await gateway.send_status_request(q_cmd)
        except Exception as err:
            LOGGER.debug("Errore query iniziale startup: %s", err)

    hass.async_create_task(_initial_startup_query())


async def async_unload_entry(hass: HomeAssistant, config_entry):
    """Unload media player platform."""
    mac = config_entry.data[CONF_MAC]
    if PLATFORM not in hass.data[DOMAIN][mac][CONF_PLATFORMS]:
        return True

    _configured_players = hass.data[DOMAIN][mac][CONF_PLATFORMS][PLATFORM]
    for _player in _configured_players.keys():
        del hass.data[DOMAIN][mac][CONF_PLATFORMS][PLATFORM][_player]

    return True


class MyHOMEMediaPlayer(MyHOMEEntity, MediaPlayerEntity, RestoreEntity):
    """Representation of a MyHome Sound Diffusion entity with Dynamic Proxy support."""

    def __init__(
        self,
        hass: HomeAssistant,
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
        self._source = "Radio FM (Tuner)" if self._who == "22" else "Sorgente 1"
        self._tuner_preset = 1
        self._tuner_freq = None
        self._media_title = "Radio FM P1" if self._who == "22" else None
        self._attr_should_poll = False
        self._is_muted = False
        self._pre_mute_volume = 0.5

        # Dynamic Proxy variables
        self._active_decoder: str | None = None
        self._syncing_volume: bool = False

        self._base_supported_features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_STEP
            | MediaPlayerEntityFeature.SELECT_SOURCE
        )

    def _get_pool(self) -> DecoderPool | None:
        """Return the shared DecoderPool from hass.data."""
        mac_data = self.hass.data.get(DOMAIN, {}).get(self._gateway_handler.mac, {})
        return mac_data.get("decoder_pool")

    def _get_all_myhome_media_players(self) -> list["MyHOMEMediaPlayer"]:
        """Return all instantiated MyHOMEMediaPlayer entities in this gateway."""
        players = []
        try:
            mac = getattr(self._gateway_handler, "mac", None)
            platforms_data = (
                self.hass.data.get(DOMAIN, {})
                .get(mac, {})
                .get(CONF_PLATFORMS, {})
                .get(PLATFORM, {})
            )
            for dev_data in platforms_data.values():
                if isinstance(dev_data, dict):
                    for ent in dev_data.get(CONF_ENTITIES, {}).values():
                        if isinstance(ent, MyHOMEMediaPlayer):
                            players.append(ent)
                elif isinstance(dev_data, MyHOMEMediaPlayer):
                    players.append(dev_data)
        except Exception as err:
            LOGGER.debug("Errore recupero lista media players: %s", err)
        return players

    def _is_any_other_zone_on_radio(self) -> bool:
        """Check if any other configured sound zone is currently playing Radio FM."""
        for player in self._get_all_myhome_media_players():
            if player != self and getattr(player, "_state", None) in (MediaPlayerState.PLAYING, "playing"):
                src = getattr(player, "_source", "")
                if src and ("Radio" in src or src == "7"):
                    return True
        return False

    def _is_any_zone_playing_source(self, source: str | None = None, decoder_id: str | None = None) -> bool:
        """Check if any other zone is currently ON and playing the specified source or decoder."""
        pool = self._get_pool()
        target_src_num = None
        if decoder_id and pool and pool.is_configured:
            target_src_num = pool.decoder_map.get(decoder_id)

        for player in self._get_all_myhome_media_players():
            if player != self and getattr(player, "_state", None) in (MediaPlayerState.PLAYING, "playing"):
                p_src = str(getattr(player, "_source", "") or "")
                p_dec = getattr(player, "_active_decoder", None)

                # Se l'altra stanza è sulla Radio FM, non sta usando questo decoder/streamer
                if "Radio" in p_src or p_src == "7":
                    continue

                # 1. Corrispondenza del decoder attivo
                if decoder_id is not None and p_dec == decoder_id:
                    return True

                # 2. Corrispondenza del canale AUX collegato a questo decoder (es. "AUX 2")
                if target_src_num is not None and f"AUX {target_src_num}" in p_src:
                    return True

                # 3. Se c'è un solo decoder configurato nel pool e l'altra stanza è accesa su AUX / streamer
                if pool and len(pool.decoder_map) == 1:
                    return True

                # 4. Corrispondenza della sorgente testuale passata
                if source is not None and (p_src == source or source in p_src or p_src in source):
                    return True

                # 5. Se nessun filtro specifico è richiesto e l'altra stanza è attiva su una sorgente AUX
                if source is None and decoder_id is None:
                    return True

        return False

    def _get_global_matrix_source(self) -> str | None:
        """Return the active source of the BTicino matrix recorded from bus events."""
        mac = getattr(self._gateway_handler, "mac", None)
        src_code = self.hass.data.get(DOMAIN, {}).get(mac, {}).get("active_matrix_source") if mac else None
        if not src_code:
            src_code = getattr(self._gateway_handler, "active_matrix_source", None)
        if src_code == "1":
            return "Radio FM (Tuner)"
        elif src_code in ("2", "3", "4"):
            return self._get_source_label(src_code)
        return None

    def _get_active_house_source(self) -> tuple[str | None, str | None]:
        """Return (source_name, active_decoder) of an actively playing BTicino zone or external streamer in the house."""
        for player in self._get_all_myhome_media_players():
            if player != self and getattr(player, "_state", None) in (MediaPlayerState.PLAYING, "playing"):
                src = getattr(player, "_source", None)
                dec = getattr(player, "_active_decoder", None)
                if src:
                    return (src, dec)

        # Se nessuna zona BTicino è attiva, verifica se uno streamer configurato (WiiM, Echo, Spotify) sta già suonando
        pool = self._get_pool()
        if pool and pool.is_configured:
            for dec_id, src_num in pool.decoder_map.items():
                dec_st = self.hass.states.get(dec_id)
                if dec_st and dec_st.state in (MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING):
                    return (self._get_source_label(str(src_num)), dec_id)
                cloud_p = self._find_cloud_streamer_for_decoder(dec_id)
                if cloud_p:
                    cp_st = self.hass.states.get(cloud_p)
                    if cp_st and cp_st.state in (MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING):
                        return (self._get_source_label(str(src_num)), dec_id)

        return (None, None)

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Return supported features, dynamically tailored to active source."""
        features = self._base_supported_features
        if self._source in ["Radio FM (Tuner)", "7"]:
            features |= (
                MediaPlayerEntityFeature.NEXT_TRACK
                | MediaPlayerEntityFeature.PREVIOUS_TRACK
                | MediaPlayerEntityFeature.STOP
                | MediaPlayerEntityFeature.PLAY_MEDIA
            )
            return features

        pool = self._get_pool()
        if (pool and pool.is_configured) or self._active_decoder:
            features |= (
                MediaPlayerEntityFeature.PLAY_MEDIA
                | MediaPlayerEntityFeature.PLAY
                | MediaPlayerEntityFeature.PAUSE
                | MediaPlayerEntityFeature.STOP
                | MediaPlayerEntityFeature.NEXT_TRACK
                | MediaPlayerEntityFeature.PREVIOUS_TRACK
            )
            # Abilita SEEK solo se lo streamer attivo (Spotify o Decoder) supporta il posizionamento traccia
            target = self._get_target_streaming_entity() or self._active_decoder
            if target:
                target_state = self.hass.states.get(target)
                if target_state:
                    t_features = target_state.attributes.get("supported_features", 0)
                    if t_features & MediaPlayerEntityFeature.SEEK:
                        features |= MediaPlayerEntityFeature.SEEK
        return features

    async def async_added_to_hass(self):
        """When entity is added to hass, restore state and register listeners."""
        await super().async_added_to_hass()

        # Restore state from HA Registry
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
            if "radio_presets" in attrs and isinstance(attrs["radio_presets"], dict):
                for p_k, p_val in attrs["radio_presets"].items():
                    try:
                        p_num = int(p_k.replace("P", ""))
                        if 1 <= p_num <= 5 and p_val and "MHz" in str(p_val):
                            match = re.search(r"(\d+\.?\d*)\s*MHz", str(p_val))
                            if match:
                                TUNER_PRESETS[p_num] = f"{float(match.group(1)):.1f} MHz"
                    except Exception:
                        pass
            if "radio_frequenza" in attrs and attrs["radio_frequenza"]:
                self._tuner_freq = attrs["radio_frequenza"]
                TUNER_STATE["freq"] = attrs["radio_frequenza"]
            if "radio_preset" in attrs and attrs["radio_preset"]:
                saved_p = attrs["radio_preset"]
                if saved_p in TUNER_PRESETS and TUNER_PRESETS[saved_p] == self._tuner_freq:
                    self._tuner_preset = saved_p
                    TUNER_STATE["preset"] = saved_p
                else:
                    matched_p = None
                    for p_num, p_freq in TUNER_PRESETS.items():
                        if p_freq and p_freq == self._tuner_freq:
                            matched_p = p_num
                            break
                    self._tuner_preset = matched_p
                    TUNER_STATE["preset"] = matched_p
            if "active_decoder" in attrs and attrs["active_decoder"]:
                self._active_decoder = attrs["active_decoder"]
            elif self._source and "Radio" not in self._source:
                pool = self._get_pool()
                if pool and pool.is_configured:
                    self.hass.async_create_task(self._async_claim_decoder_and_resume())
            self.async_write_ha_state()

        # Decoder pool rebuild listener (when OptionsFlow updates)
        @callback
        def _pool_updated(*args):
            LOGGER.debug("%s: Decoder pool aggiornato — ripubblicazione funzionalità", self.entity_id)
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"myhome_pool_updated_{self._gateway_handler.mac}",
                _pool_updated,
            )
        )

        # Radio catalog update listener (when OptionsFlow updates FM stations or zone)
        @callback
        def _radio_catalog_updated(*args):
            from .radio_catalog import RadioCatalog
            RadioCatalog.clear_cache()
            LOGGER.debug("%s: Catalogo radio FM aggiornato — rinfresco metadati", self.entity_id)
            self._refresh_media_title()
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"myhome_radio_catalog_updated_{self._gateway_handler.mac}",
                _radio_catalog_updated,
            )
        )

        # Decoder reassignment listener (follow-me: when another room claims the decoder)
        @callback
        def _decoder_lost(*args):
            LOGGER.info("%s: Decoder trasferito a un'altra stanza — rilascio decoder locale e spegnimento amplificatore", self.entity_id)
            self._active_decoder = None
            self._state = MediaPlayerState.OFF
            self._refresh_media_title()
            self.async_write_ha_state()

            async def _hw_turn_off():
                if self._who == "22":
                    await self._send_own_command(f"*22*1#4#0*{self._where}##")
                    await asyncio.sleep(0.08)
                    await self._send_own_command(f"*22*0#4#0*{self._where}##")
                else:
                    await self._gateway_handler.send(OWNSoundCommand.turn_off(self._where))

            self.hass.async_create_task(_hw_turn_off())

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"myhome_decoder_lost_{self.entity_id}",
                _decoder_lost,
            )
        )

        # Decoder & Cloud Streamer state tracking listener (for state mirroring, metadata sync, and gain staging)
        @callback
        def _async_on_media_player_state_changed(event):
            entity_id = event.data.get("entity_id", "")
            if not entity_id.startswith("media_player.") or entity_id == self.entity_id:
                return
            self._async_decoder_state_changed(event)

        self.async_on_remove(
            self.hass.bus.async_listen(
                EVENT_STATE_CHANGED,
                _async_on_media_player_state_changed,
            )
        )

    def _get_radio_info(self):
        """Resolve current radio station name, details, and logo from catalog."""
        from .radio_catalog import RadioCatalog
        from .const import (
            CONF_RADIO_ZONE_PROFILE,
            CONF_RADIO_CUSTOM_FREQUENCIES,
            CONF_RADIO_ENABLE_LOGOS,
            CONF_RADIO_LOGOS_PATH,
            DEFAULT_RADIO_ZONE_PROFILE,
            DEFAULT_RADIO_ENABLE_LOGOS,
            DEFAULT_RADIO_LOGOS_PATH,
        )
        mac = getattr(self._gateway_handler, "mac", None)
        options = self.hass.data.get(DOMAIN, {}).get(mac, {}).get("options", {}) if mac else {}
        if not options and hasattr(self._gateway_handler, "config_entry"):
            options = getattr(self._gateway_handler.config_entry, "options", {})

        zone = options.get(CONF_RADIO_ZONE_PROFILE, DEFAULT_RADIO_ZONE_PROFILE)
        custom_txt = options.get(CONF_RADIO_CUSTOM_FREQUENCIES, "")
        custom_map = RadioCatalog.parse_custom_frequencies(custom_txt)
        enable_logos = options.get(CONF_RADIO_ENABLE_LOGOS, DEFAULT_RADIO_ENABLE_LOGOS)
        logos_path = options.get(CONF_RADIO_LOGOS_PATH, DEFAULT_RADIO_LOGOS_PATH)

        freq_str = TUNER_STATE.get("freq") or self._tuner_freq
        active_preset = TUNER_STATE.get("preset") or self._tuner_preset

        preset_to_use = None
        if freq_str:
            # Verifica se la frequenza letta dal bus corrisponde ad uno dei preset memorizzati
            for p_num, p_freq in TUNER_PRESETS.items():
                if p_freq and p_freq == freq_str:
                    preset_to_use = p_num
                    break
            # Se la frequenza coincide con il preset attivo corrente confermato
            if preset_to_use is None and active_preset and TUNER_PRESETS.get(active_preset) == freq_str:
                preset_to_use = active_preset
        elif active_preset and TUNER_PRESETS.get(active_preset):
            # Se la frequenza del bus non è ancora arrivata, ma c'è un preset attivo con frequenza nota
            preset_to_use = active_preset
            freq_str = TUNER_PRESETS.get(active_preset)

        info = RadioCatalog.resolve_station(
            freq_str=freq_str,
            preset_num=preset_to_use,
            zone_profile=zone,
            custom_mapping=custom_map,
            enable_logos=enable_logos,
            logos_prefix=logos_path,
            hass=self.hass,
        )

        return info

    def _get_radio_presets_map(self) -> dict[str, str]:
        """Return map of presets (P1..P5) with formatted frequency and station name."""
        from .radio_catalog import RadioCatalog
        from .const import CONF_RADIO_ZONE_PROFILE, CONF_RADIO_CUSTOM_FREQUENCIES, DEFAULT_RADIO_ZONE_PROFILE
        mac = getattr(self._gateway_handler, "mac", None)
        options = self.hass.data.get(DOMAIN, {}).get(mac, {}).get("options", {}) if mac else {}
        if not options and hasattr(self._gateway_handler, "config_entry"):
            options = getattr(self._gateway_handler.config_entry, "options", {})

        zone = options.get(CONF_RADIO_ZONE_PROFILE, DEFAULT_RADIO_ZONE_PROFILE)
        custom_txt = options.get(CONF_RADIO_CUSTOM_FREQUENCIES, "")
        custom_map = RadioCatalog.parse_custom_frequencies(custom_txt)

        presets_map = {}
        for k, v in TUNER_PRESETS.items():
            if v:
                st_info = RadioCatalog.resolve_station(
                    freq_str=v,
                    preset_num=k,
                    zone_profile=zone,
                    custom_mapping=custom_map,
                    enable_logos=False,
                )
                presets_map[f"P{k}"] = st_info.title
            else:
                presets_map[f"P{k}"] = "Non sintonizzato"
        return presets_map

    def _get_radio_preset_logos(self) -> dict[str, str | None]:
        """Return map of presets (P1..P5) with direct logo URLs resolved from catalog."""
        from .radio_catalog import RadioCatalog
        from .const import (
            CONF_RADIO_ZONE_PROFILE,
            CONF_RADIO_CUSTOM_FREQUENCIES,
            CONF_RADIO_LOGOS_PATH,
            DEFAULT_RADIO_ZONE_PROFILE,
            DEFAULT_RADIO_LOGOS_PATH,
        )
        mac = getattr(self._gateway_handler, "mac", None)
        options = self.hass.data.get(DOMAIN, {}).get(mac, {}).get("options", {}) if mac else {}
        if not options and hasattr(self._gateway_handler, "config_entry"):
            options = getattr(self._gateway_handler.config_entry, "options", {})

        zone = options.get(CONF_RADIO_ZONE_PROFILE, DEFAULT_RADIO_ZONE_PROFILE)
        custom_txt = options.get(CONF_RADIO_CUSTOM_FREQUENCIES, "")
        custom_map = RadioCatalog.parse_custom_frequencies(custom_txt)
        logos_path = options.get(CONF_RADIO_LOGOS_PATH, DEFAULT_RADIO_LOGOS_PATH)

        logos_map = {}
        for k, v in TUNER_PRESETS.items():
            if v:
                st_info = RadioCatalog.resolve_station(
                    freq_str=v,
                    preset_num=k,
                    zone_profile=zone,
                    custom_mapping=custom_map,
                    enable_logos=True,
                    logos_prefix=logos_path,
                    hass=self.hass,
                )
                logos_map[f"P{k}"] = st_info.logo_url
            else:
                logos_map[f"P{k}"] = None
        return logos_map

    def _refresh_media_title(self):
        """Update media_title attribute based on current state, source, and tuner info."""
        if self._source in ["Radio FM (Tuner)", "7"]:
            info = self._get_radio_info()
            self._media_title = info.title
        elif self._source:
            self._media_title = self._source

    def _get_target_streaming_entity(self) -> str | None:
        """Return the active streaming entity (active cloud streamer if playing to decoder; otherwise decoder)."""
        if not self._active_decoder and self._source and self._source not in ["Radio FM (Tuner)", "7"]:
            pool = self._get_pool()
            if pool and pool.is_configured:
                for dec_id, src_num in pool.decoder_map.items():
                    if f"AUX {src_num}" in self._source or len(pool.decoder_map) == 1:
                        self._active_decoder = dec_id
                        break
        if not self._active_decoder or self._source in ["Radio FM (Tuner)", "7"]:
            return None
        cloud_player = self._find_cloud_streamer_for_decoder(self._active_decoder)
        if cloud_player:
            return cloud_player
        return self._active_decoder

    def _is_decoder_playing(self) -> bool:
        """Return True if active decoder or active Spotify is currently playing."""
        if not self._active_decoder or self._source in ["Radio FM (Tuner)", "7"]:
            return False
        dec_state = self.hass.states.get(self._active_decoder)
        if dec_state and dec_state.state in (MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING):
            return True
        target = self._get_target_streaming_entity()
        if target and target != self._active_decoder:
            st = self.hass.states.get(target)
            if st and st.state in (MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING):
                return True
        return False

    @property
    def should_poll(self) -> bool:
        # Se la zona è accesa su AUX con un decoder attivo, esegui il polling per mantenere freschi i metadati dello streaming
        if self._state not in (MediaPlayerState.OFF, "off") and self._source not in ["Radio FM (Tuner)", "7"] and self._active_decoder:
            return True
        return False

    @property
    def state(self) -> MediaPlayerState | None:
        """Mirror the active decoder or Spotify playback state if streaming; otherwise return zone state."""
        if self._state == MediaPlayerState.OFF:
            return MediaPlayerState.OFF
        if self._source in ["Radio FM (Tuner)", "7"]:
            return self._state
        if self._active_decoder:
            # 1. Controlla prima se Spotify sta suonando attivamente verso questo decoder
            target_spotify = self._get_target_streaming_entity()
            if target_spotify and target_spotify != self._active_decoder:
                sp_state = self.hass.states.get(target_spotify)
                if sp_state and sp_state.state in (
                    MediaPlayerState.PLAYING,
                    MediaPlayerState.PAUSED,
                    MediaPlayerState.BUFFERING,
                ):
                    return sp_state.state

            # 2. Altrimenti usa lo stato riportato dal decoder
            dec_state = self.hass.states.get(self._active_decoder)
            if dec_state and dec_state.state in (
                MediaPlayerState.PLAYING,
                MediaPlayerState.PAUSED,
                MediaPlayerState.BUFFERING,
            ):
                return dec_state.state

        # Se l'amplificatore fisico è acceso ma la sorgente esterna è idle/non sta riproducendo,
        # l'amplificatore è comunque acceso e alimentato (PLAYING)
        return self._state

    @property
    def volume_level(self) -> float | None:
        return self._volume_level

    @property
    def is_volume_muted(self) -> bool:
        return self._is_muted

    @property
    def source(self) -> str | None:
        return self._source

    @property
    def source_list(self) -> list[str]:
        if self._who == "22":
            sources = ["Radio FM (Tuner)"]
            pool = self._get_pool()
            if pool and pool.is_configured:
                for dec_id, src_num in pool.decoder_map.items():
                    dec_state = self.hass.states.get(dec_id)
                    fname = dec_state.attributes.get("friendly_name") if dec_state else None
                    label = f"Ingresso AUX {src_num} ({fname})" if fname else f"Ingresso AUX {src_num}"
                    if label not in sources:
                        sources.append(label)
                return sources
            return list(SOURCES_WHO22.keys())
        return ["Sorgente 1", "Sorgente 2", "Sorgente 3", "Sorgente 4"]

    def _get_source_label(self, src_code: str) -> str:
        """Map OpenWebNet source code (1..4, 7) to active source_list label."""
        try:
            num = int(src_code)
            if num >= 10:
                num = num - 10
            src_str = str(num)
        except ValueError:
            src_str = src_code

        pool = self._get_pool()
        if pool and pool.is_configured:
            for dec_id, src_num in pool.decoder_map.items():
                if str(src_num) == src_str or (src_str in ("2", "4") and len(pool.decoder_map) == 1):
                    dec_state = self.hass.states.get(dec_id)
                    fname = dec_state.attributes.get("friendly_name") if dec_state else None
                    return f"Ingresso AUX {src_num} ({fname})" if fname else f"Ingresso AUX {src_num}"

        if src_str in ("7", "0"):
            return "Radio FM (Tuner)"

        return f"Ingresso AUX {src_str}"

    @property
    def media_title(self) -> str | None:
        if self._source in ["Radio FM (Tuner)", "7"]:
            info = self._get_radio_info()
            return info.title
        if self._active_decoder:
            title = self._get_decoder_attr("media_title")
            if title:
                return title
            return "Nessuna traccia in riproduzione"
        return self._media_title

    @property
    def media_artist(self) -> str | None:
        if self._source in ["Radio FM (Tuner)", "7"]:
            info = self._get_radio_info()
            return info.artist
        if self._active_decoder:
            return self._get_decoder_attr("media_artist")
        return None

    @property
    def media_album_name(self) -> str | None:
        if self._source in ["Radio FM (Tuner)", "7"]:
            info = self._get_radio_info()
            active_p = getattr(info, "preset_num", None)
            p_str = f"Preset P{active_p}" if active_p else "Sintonizzazione Manuale"
            if info.is_known:
                return f"{p_str} • {info.name}"
            return f"{p_str} • BTicino F500"
        if self._active_decoder:
            return self._get_decoder_attr("media_album_name")
        return None

    @property
    def entity_picture(self) -> str | None:
        if self._source in ["Radio FM (Tuner)", "7"]:
            info = self._get_radio_info()
            return info.logo_url
        if self._active_decoder and self._source not in ["Radio FM (Tuner)", "7"]:
            if self._get_decoder_attr("media_title"):
                # 1. Se un player cloud (Spotify, ecc.) sta suonando attivamente verso questo decoder
                target_cloud = self._get_target_streaming_entity()
                if target_cloud and target_cloud != self._active_decoder:
                    sp_state = self.hass.states.get(target_cloud)
                    if sp_state and sp_state.state in (MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING):
                        return (
                            sp_state.attributes.get("entity_picture_local")
                            or sp_state.attributes.get("entity_picture")
                        )
                # 2. Decoder diretto (Amazon Music su Echo Dot, Arylic, WiiM, ecc.)
                dec_state = self.hass.states.get(self._active_decoder)
                if dec_state:
                    pic = (
                        dec_state.attributes.get("entity_picture_local")
                        or dec_state.attributes.get("entity_picture")
                    )
                    if pic and "e3b0c442" not in pic:
                        return pic

                # 3. Fallback DLNA gemello (accoppiamento per unique_id hardware o friendly_name)
                if dec_state:
                    # Tentativo A: Matching hardware tramite Unique ID (stesso UUID Arylic, es. FF31F09EF5C1EE8E)
                    try:
                        from homeassistant.helpers import entity_registry as er
                        ent_reg = er.async_get(self.hass)
                        active_entry = ent_reg.async_get(self._active_decoder)
                        if active_entry and active_entry.unique_id:
                            clean_hw_uuid = active_entry.unique_id.replace("uuid:", "").replace("-", "").upper()
                            for candidate_entry in ent_reg.entities.values():
                                if candidate_entry.domain == "media_player" and candidate_entry.entity_id != self._active_decoder:
                                    c_uid = (candidate_entry.unique_id or "").replace("uuid:", "").replace("-", "").upper()
                                    if clean_hw_uuid and (clean_hw_uuid in c_uid or c_uid in clean_hw_uuid):
                                        cand_state = self.hass.states.get(candidate_entry.entity_id)
                                        if cand_state:
                                            cand_pic = cand_state.attributes.get("entity_picture_local") or cand_state.attributes.get("entity_picture")
                                            if cand_pic and "e3b0c442" not in cand_pic:
                                                return cand_pic
                    except Exception:
                        pass

                    # Tentativo B: Suffisso standard o Friendly Name
                    target_fname = dec_state.attributes.get("friendly_name")
                    for s in self.hass.states.async_all("media_player"):
                        if s.entity_id in (f"{self._active_decoder}_2", f"{self._active_decoder}_dlna"):
                            candidate_pic = s.attributes.get("entity_picture_local") or s.attributes.get("entity_picture")
                            if candidate_pic and "e3b0c442" not in candidate_pic:
                                return candidate_pic
                        elif target_fname and s.attributes.get("friendly_name") == target_fname and s.entity_id != self._active_decoder:
                            if not s.entity_id.startswith("media_player.filodiffusione_"):
                                candidate_pic = s.attributes.get("entity_picture_local") or s.attributes.get("entity_picture")
                                if candidate_pic and "e3b0c442" not in candidate_pic:
                                    return candidate_pic

                if dec_state:
                    return (
                        dec_state.attributes.get("entity_picture_local")
                        or dec_state.attributes.get("entity_picture")
                    )
        return None

    @property
    def media_image_remotely_accessible(self) -> bool:
        return True

    @property
    def media_duration(self) -> int | None:
        if self._source in ["Radio FM (Tuner)", "7"]:
            return None
        if self._active_decoder:
            return self._get_decoder_attr("media_duration")
        return None

    @property
    def media_position(self) -> int | None:
        if self._source in ["Radio FM (Tuner)", "7"]:
            return None
        if self._active_decoder:
            return self._get_decoder_attr("media_position")
        return None

    @property
    def media_position_updated_at(self):
        if self._source in ["Radio FM (Tuner)", "7"]:
            return None
        if self._active_decoder:
            return self._get_decoder_attr("media_position_updated_at")
        return None

    def _get_decoder_attr(self, attr: str):
        """Read attribute from active Spotify player if streaming; otherwise from active decoder."""
        if not self._active_decoder and self._source and self._source not in ["Radio FM (Tuner)", "7"]:
            pool = self._get_pool()
            if pool and pool.is_configured:
                for dec_id, src_num in pool.decoder_map.items():
                    if f"AUX {src_num}" in self._source or len(pool.decoder_map) == 1:
                        self._active_decoder = dec_id
                        break
        if not self._active_decoder or self._source in ["Radio FM (Tuner)", "7"]:
            return None

        # 1. Se un player cloud (Spotify, Tidal, ecc.) sta suonando attivamente verso questo decoder, i suoi metadati hanno la precedenza
        target_cloud = self._get_target_streaming_entity()
        if target_cloud and target_cloud != self._active_decoder:
            sp_state = self.hass.states.get(target_cloud)
            if sp_state and sp_state.state in (MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING):
                val = sp_state.attributes.get(attr)
                if val is not None:
                    return val
            elif sp_state and sp_state.state == MediaPlayerState.PAUSED:
                # Mostra metadati di un player cloud pausato SOLO se il decoder hardware non ha attività recente
                dec_state = self.hass.states.get(self._active_decoder)
                dec_is_active = dec_state and dec_state.state in (MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING)
                if not dec_is_active:
                    if not (dec_state and dec_state.last_changed and sp_state.last_changed and sp_state.last_changed < dec_state.last_changed):
                        val = sp_state.attributes.get(attr)
                        if val is not None:
                            return val

        # 2. Decoder diretto (Amazon Music, radio, bluetooth, ecc.)
        dec_state = self.hass.states.get(self._active_decoder)
        if dec_state:
            return dec_state.attributes.get(attr)

        return None

    @property
    def extra_state_attributes(self):
        cur_val = ha_volume_to_bticino(self._volume_level or 0.0)
        attrs = {
            "volume_bticino": cur_val,
            "scala_volume": "1-31",
            "radio_presets": self._get_radio_presets_map(),
            "radio_preset_logos": self._get_radio_preset_logos(),
        }
        if self._source in ["Radio FM (Tuner)", "7"]:
            info = self._get_radio_info()
            active_p = getattr(info, "preset_num", None)
            if active_p:
                attrs["radio_preset"] = active_p
            else:
                attrs["radio_preset"] = None
            if info.frequency:
                attrs["radio_frequenza"] = info.frequency
            if info.is_known:
                attrs["radio_stazione"] = info.name
            attrs["radio_info"] = info.title
        elif self._active_decoder:
            attrs["active_decoder"] = self._active_decoder
        return attrs

    async def async_update(self):
        """Keep streaming metadata fresh without flooding the SCS bus."""
        try:
            # Se siamo su AUX con decoder, aggiorna periodicamente i player cloud (Spotify, Tidal, ecc.)
            if self._source not in ["Radio FM (Tuner)", "7"] and self._active_decoder:
                cloud_keywords = ("spotify", "tidal", "deezer", "mass", "music_assistant")
                for s in self.hass.states.async_all("media_player"):
                    if any(kw in s.entity_id for kw in cloud_keywords):
                        try:
                            await self.hass.services.async_call(
                                "homeassistant", "update_entity", {"entity_id": s.entity_id}, blocking=True
                            )
                        except Exception:
                            pass
        except Exception as err:
            LOGGER.debug("Errore update per %s: %s", self._where, err)

    async def _send_own_command(self, cmd_str: str):
        """Send raw OpenWebNet command safely."""
        try:
            LOGGER.info("[Sound Zone %s] Invio comando OpenWebNet: %s", self._where, cmd_str)
            cmd = OWNCommand.parse(cmd_str) or cmd_str
            # Le query di stato pure (*#22*... senza #1, #5, #6 di scrittura) vanno inviate come status_request
            if cmd_str.startswith("*#") and not ("*#1*" in cmd_str or "*#5*" in cmd_str or "*#6*" in cmd_str):
                await self._gateway_handler.send_status_request(cmd)
            else:
                await self._gateway_handler.send(cmd)
        except Exception as err:
            LOGGER.error("Errore invio comando MyHome %s: %s", cmd_str, err)

    async def _forward_to_decoder(self, service: str):
        """Forward a media_player service call to the active backend decoder or Spotify."""
        target = self._get_target_streaming_entity() or self._active_decoder
        if target:
            await self.hass.services.async_call(
                "media_player", service, {"entity_id": target}
            )
            # Schedula aggiornamenti a cascata (0.3s, 1.2s, 2.5s) per sincronizzare immediatamente il nuovo brano
            async def _cascade_flush():
                for delay in (0.3, 1.2, 2.5):
                    await asyncio.sleep(delay)
                    try:
                        await self.hass.services.async_call(
                            "homeassistant", "update_entity", {"entity_id": target}, blocking=False
                        )
                    except Exception:
                        pass
            self.hass.async_create_task(_cascade_flush())

    async def _async_claim_decoder_and_resume(self):
        """Claim decoder from pool and resume playback if paused or idle."""
        pool = self._get_pool()
        if not pool or not pool.is_configured:
            return
        if not self._active_decoder:
            res = await pool.claim(self.entity_id)
            if res:
                self._active_decoder, _ = res

        if not self._active_decoder:
            return

        # Se lo streamer o Spotify sta già suonando, non inviare comandi di play (evita stutter / pause accidentali)
        if self._is_decoder_playing():
            LOGGER.debug("%s: Decoder %s già in riproduzione attiva — nessun resume necessario", self.entity_id, self._active_decoder)
            return

        target_entity = self._get_target_streaming_entity() or self._active_decoder
        dec_state = self.hass.states.get(target_entity)

        if dec_state and dec_state.state in (MediaPlayerState.OFF, MediaPlayerState.IDLE):
            try:
                await self.hass.services.async_call(
                    "media_player", "turn_on", {"entity_id": target_entity}, blocking=True
                )
                await asyncio.sleep(0.3)
            except Exception:
                pass
            dec_state = self.hass.states.get(target_entity)

        if dec_state and dec_state.state != MediaPlayerState.PLAYING:
            try:
                await self.hass.services.async_call(
                    "media_player", "media_play", {"entity_id": target_entity}, blocking=True
                )
            except Exception:
                pass

    def _find_cloud_streamer_for_decoder(self, decoder_id: str | None) -> str | None:
        """Find active cloud streaming player (Spotify, Tidal, Deezer, Music Assistant, etc.) connected to this decoder."""
        if not decoder_id:
            return None
        dec_state = self.hass.states.get(decoder_id)
        if not dec_state:
            return None

        friendly_name = str(dec_state.attributes.get("friendly_name", "")).lower()
        if not friendly_name:
            return None

        # 1. Cerca prima un player cloud che sia ATTIVAMENTE in riproduzione verso questo decoder
        for s in self.hass.states.async_all("media_player"):
            if s.entity_id == decoder_id or s.entity_id.startswith("media_player.filodiffusione_"):
                continue
            if s.state in (MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING):
                src = str(s.attributes.get("source", "")).lower()
                if friendly_name == src or friendly_name in src:
                    return s.entity_id

        # 2. Se il decoder hardware stesso sta già suonando (es. Amazon Music, radio, bluetooth),
        # il decoder fisico ha la precedenza assoluta rispetto a un cloud player rimasto in pausa!
        if dec_state.state in (MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING):
            return None

        # 3. Solo se nessuno sta suonando attivamente, controlla se c'è un player cloud in pausa da riprendere
        for s in self.hass.states.async_all("media_player"):
            if s.entity_id == decoder_id or s.entity_id.startswith("media_player.filodiffusione_"):
                continue
            if s.state == MediaPlayerState.PAUSED:
                # Se il decoder hardware è stato attivo più di recente rispetto a quando il cloud player è stato messo in pausa,
                # significa che l'utente sta usando il decoder fisico (es. Amazon Music): non riesumare la vecchia traccia cloud!
                if dec_state.last_changed and s.last_changed and s.last_changed < dec_state.last_changed:
                    continue
                src = str(s.attributes.get("source", "")).lower()
                if friendly_name == src or friendly_name in src:
                    return s.entity_id

        return None

    def _find_spotify_for_decoder(self, decoder_id: str | None) -> str | None:
        """Compatibility alias for _find_cloud_streamer_for_decoder."""
        return self._find_cloud_streamer_for_decoder(decoder_id)

    async def _async_release_decoder_and_pause(self):
        """Release decoder from pool and pause/stop it if no other listeners remain."""
        pool = self._get_pool()
        dec_to_release = self._active_decoder
        if not dec_to_release and pool:
            dec_to_release = pool.get_assignment(self.entity_id)

        self._active_decoder = None

        if pool:
            released = await pool.release(self.entity_id)
            if not dec_to_release:
                dec_to_release = released

        spotify_entity = self._find_spotify_for_decoder(dec_to_release) if dec_to_release else None

        # Solo se nessun'altra stanza è rimasta in ascolto nel pool e nessuna zona sta suonando questo decoder
        if dec_to_release and pool and not pool.has_active_listeners(dec_to_release) and not self._is_any_zone_playing_source(decoder_id=dec_to_release):
            targets = [t for t in (spotify_entity, dec_to_release) if t]
            LOGGER.info("DecoderPool: Nessun'altra zona attiva su %s — invio pausa a %s", dec_to_release, targets)
            for t in targets:
                t_state = self.hass.states.get(t)
                if not t_state or t_state.state not in (MediaPlayerState.PLAYING, "playing"):
                    continue
                try:
                    await self.hass.services.async_call(
                        "media_player", "media_pause", {"entity_id": t}, blocking=False
                    )
                except Exception:
                    pass

    async def _async_switch_source_hw(self, src_num: str):
        """Execute verified 3-frame matrix routing sequence with bus timing."""
        # 1. Indicazione zona richiedente
        await self._send_own_command(f"*22*22#4#7*5#{self._where}##")
        await asyncio.sleep(0.18)
        # 2. Selezione canale sorgente (1 = Tuner FM, 2 = AUX 2, ecc.)
        await self._send_own_command(f"*22*22#4#7*2#{src_num}##")
        await asyncio.sleep(0.18)
        # 3. Connessione audio matrice al canale
        await self._send_own_command(f"*22*2#4#7*5#2#{src_num}##")

    async def async_turn_on(self, **kwargs):
        """Turn the media player on, honoring active house source, multiroom, and follow-me."""
        if self._who == "22":
            # Determina la sorgente desiderata PRIMA di inviare comandi hardware:
            # 1. Se un'altra stanza (o uno streamer) è già accesa nella casa, eredita la sorgente (Follow-me / Multiroom)
            # 2. Altrimenti usa la sorgente globale memorizzata della matrice
            # 3. Altrimenti mantieni la sorgente precedente dell'entità
            # 4. Fallback su "Radio FM (Tuner)"
            active_src, active_dec = self._get_active_house_source()
            global_src = self._get_global_matrix_source()
            target_source = active_src or global_src or self._source or "Radio FM (Tuner)"

            self._source = target_source

            if "Radio" in target_source:
                self._active_decoder = None
                self._refresh_media_title()
                # Accendi l'amplificatore zonale sulla sorgente 7 (Radio Tuner F500)
                await self._send_own_command(f"*22*1#4#7*{self._where}##")
                if not active_src:
                    await asyncio.sleep(0.12)
                    await self._async_switch_source_hw("1")
                    p_to_recall = TUNER_STATE.get("preset") or self._tuner_preset or 1
                    await asyncio.sleep(0.12)
                    await self._send_own_command(f"*#22*{TUNER_WHERE}*#6*{p_to_recall}##")
            else:
                # Sorgente AUX / Streamer (es. Spotify, WiiM, Echo Dot)
                src_num = "2"
                for s_candidate in ("1", "2", "3", "4"):
                    if f"AUX {s_candidate}" in target_source:
                        src_num = s_candidate
                        break

                # Accendi l'amplificatore zonale direttamente sul canale AUX target (1#4#src_num)
                # senza toccare il Tuner (1#4#7) che causerebbe la disconnessione dello streamer!
                await self._send_own_command(f"*22*1#4#{src_num}*{self._where}##")
                await asyncio.sleep(0.15)
                await self._async_switch_source_hw(src_num)
                if active_dec and not self._active_decoder:
                    self._active_decoder = active_dec
                await self._async_claim_decoder_and_resume()
                self._refresh_media_title()
        else:
            await self._gateway_handler.send(OWNSoundCommand.turn_on(self._where))

        self._state = MediaPlayerState.PLAYING
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn the media player off and release any claimed decoder."""
        self._state = MediaPlayerState.OFF

        await self._async_release_decoder_and_pause()

        if self._who == "22":
            # Spegni la stanza con sequenza combinata (1#4#0 disconnessione sorgente audio immediata e 0#4#0 standby hardware)
            await self._send_own_command(f"*22*1#4#0*{self._where}##")
            await asyncio.sleep(0.08)
            await self._send_own_command(f"*22*0#4#0*{self._where}##")
        else:
            await self._gateway_handler.send(OWNSoundCommand.turn_off(self._where))

        self.async_write_ha_state()

    async def async_media_play(self):
        """Play media."""
        # Se l'amplificatore della stanza è spento, accendilo SEMPRE prima di qualunque comando
        if self._state not in (MediaPlayerState.PLAYING, "playing"):
            await self.async_turn_on()

        if self._active_decoder and self._source not in ["Radio FM (Tuner)", "7"]:
            await self._forward_to_decoder("media_play")

    async def async_media_pause(self):
        """Pause media."""
        if self._active_decoder and self._source not in ["Radio FM (Tuner)", "7"]:
            await self._forward_to_decoder("media_pause")
        else:
            await self.async_turn_off()

    async def async_media_stop(self):
        """Stop media."""
        if self._active_decoder and self._source not in ["Radio FM (Tuner)", "7"]:
            await self._forward_to_decoder("media_stop")
        else:
            await self.async_turn_off()

    async def async_media_seek(self, position: float):
        """Send seek command to active streaming decoder or Spotify."""
        if self._active_decoder and self._source not in ["Radio FM (Tuner)", "7"]:
            target = self._get_target_streaming_entity() or self._active_decoder
            if target:
                try:
                    await self.hass.services.async_call(
                        "media_player",
                        "media_seek",
                        {"entity_id": target, "seek_position": position},
                    )
                except Exception as err:
                    LOGGER.debug("Target %s non supporta media_seek: %s", target, err)
                    return

                async def _cascade_flush():
                    for delay in (0.2, 0.8):
                        await asyncio.sleep(delay)
                        try:
                            await self.hass.services.async_call(
                                "homeassistant", "update_entity", {"entity_id": target}, blocking=False
                            )
                        except Exception:
                            pass
                self.hass.async_create_task(_cascade_flush())

    async def async_set_volume_level(self, volume: float):
        """Set volume level (scaled 1..31 via LUT) on physical BTicino amplifier."""
        val = ha_volume_to_bticino(volume)
        self._volume_level = bticino_volume_to_ha(val)
        if self._is_muted and volume > 0:
            self._is_muted = False

        if self._who == "22":
            await self._send_own_command(f"*#22*{self._where}*#1*{val}##")
        else:
            await self._gateway_handler.send(OWNSoundCommand.set_volume(self._where, val))

        # Gain staging: keep decoder volume proportionally higher than the zone volume (solo se pre_gain > 0)
        if self._active_decoder and self._source not in ["Radio FM (Tuner)", "7"]:
            pool = self._get_pool()
            if pool:
                pre_gain_pct = pool.get_pre_gain(self._active_decoder)
                if pre_gain_pct > 0:
                    decoder_volume = min(1.0, max(0.0, volume + pre_gain_pct / 100.0))
                    self._syncing_volume = True
                    try:
                        await self.hass.services.async_call(
                            "media_player",
                            "volume_set",
                            {
                                "entity_id": self._active_decoder,
                                "volume_level": decoder_volume,
                            },
                            blocking=False,
                        )
                    except Exception as err:
                        LOGGER.debug("Could not set volume on decoder %s: %s", self._active_decoder, err)
                    finally:
                        self._syncing_volume = False

        self.async_write_ha_state()

    async def async_volume_up(self):
        """Turn volume up for media player."""
        if self._who == "22":
            await self._send_own_command(f"*22*3#1*{self._where}##")
        else:
            await self._gateway_handler.send(OWNSoundCommand.volume_up(self._where))

        if self._volume_level is not None:
            cur_val = ha_volume_to_bticino(self._volume_level)
            new_val = min(31, cur_val + 1)
            self._volume_level = bticino_volume_to_ha(new_val)
            if self._active_decoder and self._source not in ["Radio FM (Tuner)", "7"]:
                pool = self._get_pool()
                if pool:
                    pre_gain_pct = pool.get_pre_gain(self._active_decoder)
                    if pre_gain_pct > 0:
                        decoder_volume = min(1.0, max(0.0, self._volume_level + pre_gain_pct / 100.0))
                        try:
                            await self.hass.services.async_call(
                                "media_player",
                                "volume_set",
                                {"entity_id": self._active_decoder, "volume_level": decoder_volume},
                                blocking=False,
                            )
                        except Exception:
                            pass
        self.async_write_ha_state()

    async def async_volume_down(self):
        """Turn volume down for media player."""
        if self._who == "22":
            await self._send_own_command(f"*22*4#1*{self._where}##")
        else:
            await self._gateway_handler.send(OWNSoundCommand.volume_down(self._where))

        if self._volume_level is not None:
            cur_val = ha_volume_to_bticino(self._volume_level)
            new_val = max(1, cur_val - 1)
            self._volume_level = bticino_volume_to_ha(new_val)
            if self._active_decoder and self._source not in ["Radio FM (Tuner)", "7"]:
                pool = self._get_pool()
                if pool:
                    pre_gain_pct = pool.get_pre_gain(self._active_decoder)
                    if pre_gain_pct > 0:
                        decoder_volume = min(1.0, max(0.0, self._volume_level + pre_gain_pct / 100.0))
                        try:
                            await self.hass.services.async_call(
                                "media_player",
                                "volume_set",
                                {"entity_id": self._active_decoder, "volume_level": decoder_volume},
                                blocking=False,
                            )
                        except Exception:
                            pass
        self.async_write_ha_state()

    async def async_mute_volume(self, mute: bool):
        """Mute/unmute volume (software emulated on physical amplifier)."""
        if mute:
            self._pre_mute_volume = self._volume_level or 0.5
            await self.async_set_volume_level(0.0)
        else:
            restore_vol = self._pre_mute_volume or 0.3
            await self.async_set_volume_level(restore_vol)

        self._is_muted = mute
        self.async_write_ha_state()

    async def async_media_next_track(self):
        """Next radio preset station or forward to streaming decoder."""
        if self._active_decoder and self._source not in ["Radio FM (Tuner)", "7"]:
            await self._forward_to_decoder("media_next_track")
        elif self._who == "22":
            cur_p = TUNER_STATE.get("preset") or self._tuner_preset or 1
            next_p = (cur_p % 5) + 1
            now = time.time()
            old_freq = TUNER_STATE.get("freq") or self._tuner_freq
            TUNER_STATE["target_preset"] = next_p
            TUNER_STATE["old_freq"] = old_freq
            TUNER_STATE["transit_until"] = now + 1.5
            TUNER_STATE["preset"] = next_p
            self._tuner_preset = next_p
            learned_f = TUNER_PRESETS.get(next_p)
            if learned_f:
                TUNER_STATE["freq"] = learned_f
                self._tuner_freq = learned_f
            self._refresh_media_title()
            self.async_write_ha_state()
            await self._send_own_command(f"*#22*{TUNER_WHERE}*#6*{next_p}##")

    async def async_media_previous_track(self):
        """Previous radio preset station or forward to streaming decoder."""
        if self._active_decoder and self._source not in ["Radio FM (Tuner)", "7"]:
            await self._forward_to_decoder("media_previous_track")
        elif self._who == "22":
            cur_p = TUNER_STATE.get("preset") or self._tuner_preset or 1
            prev_p = 5 if cur_p <= 1 else cur_p - 1
            now = time.time()
            old_freq = TUNER_STATE.get("freq") or self._tuner_freq
            TUNER_STATE["target_preset"] = prev_p
            TUNER_STATE["old_freq"] = old_freq
            TUNER_STATE["transit_until"] = now + 1.5
            TUNER_STATE["preset"] = prev_p
            self._tuner_preset = prev_p
            learned_f = TUNER_PRESETS.get(prev_p)
            if learned_f:
                TUNER_STATE["freq"] = learned_f
                self._tuner_freq = learned_f
            self._refresh_media_title()
            self.async_write_ha_state()
            await self._send_own_command(f"*#22*{TUNER_WHERE}*#6*{prev_p}##")

    async def async_seek_up(self):
        """Scan forward to the next radio station with a strong signal (Seek Up)."""
        TUNER_STATE["preset"] = None
        TUNER_STATE["target_preset"] = None
        TUNER_STATE["old_freq"] = None
        TUNER_STATE["transit_until"] = 0.0
        self._tuner_preset = None
        self._tuner_freq = None
        self._refresh_media_title()
        self.async_write_ha_state()
        await self._send_own_command(f"*22*13#1*{TUNER_WHERE}##")

    async def async_seek_down(self):
        """Scan backward to the previous radio station with a strong signal (Seek Down)."""
        TUNER_STATE["preset"] = None
        TUNER_STATE["target_preset"] = None
        TUNER_STATE["old_freq"] = None
        TUNER_STATE["transit_until"] = 0.0
        self._tuner_preset = None
        self._tuner_freq = None
        self._refresh_media_title()
        self.async_write_ha_state()
        await self._send_own_command(f"*22*14#1*{TUNER_WHERE}##")

    async def _tune_frequency(self, freq_float: float):
        """Tune specific frequency float and broadcast to tuner."""
        self._tuner_freq = f"{freq_float:.1f} MHz"
        matched_preset = None
        for p_num, p_freq in TUNER_PRESETS.items():
            if p_freq == self._tuner_freq:
                matched_preset = p_num
                break
        TUNER_STATE["preset"] = matched_preset
        TUNER_STATE["target_preset"] = None
        TUNER_STATE["old_freq"] = None
        TUNER_STATE["freq"] = self._tuner_freq
        TUNER_STATE["transit_until"] = 0.0
        self._tuner_preset = matched_preset

        was_off = self._state not in (MediaPlayerState.PLAYING, "playing")
        if was_off:
            self._state = MediaPlayerState.PLAYING
            await self._async_release_decoder_and_pause()
            self._source = "Radio FM (Tuner)"
            self._active_decoder = None
            await self._send_own_command(f"*22*1#4#7*{self._where}##")
            await asyncio.sleep(0.18)
            await self._async_switch_source_hw("1")
        elif self._source not in ["Radio FM (Tuner)", "7"]:
            await self._async_release_decoder_and_pause()
            self._source = "Radio FM (Tuner)"
            self._active_decoder = None
            await self._async_switch_source_hw("1")

        self._refresh_media_title()
        self.async_write_ha_state()

        freq_code = int(round(freq_float * 10))
        cmd_str = f"*#22*{TUNER_WHERE}*#5*1*{freq_code}##"
        await self._send_own_command(cmd_str)

    async def async_play_media(self, media_type: str, media_id: str, **kwargs):
        """Play radio preset, tune frequency, or stream via Dynamic Proxy decoder."""
        if not media_id and "media" in kwargs and isinstance(kwargs["media"], dict):
            media_dict = kwargs["media"]
            media_id = str(media_dict.get("media_content_id", ""))
            media_type = str(media_dict.get("media_content_type", media_type or ""))

        media_type_l = (media_type or "").lower()
        media_id_str = str(media_id).strip()

        # ── 1. Controlli Sintonizzatore Radio FM (WHO 22) ──────────────────────────
        # Scansione automatica emittenti (Seek Up / Seek Down)
        if media_type_l in ["scan", "seek", "search", "step", "frequency_step", "tune_step"]:
            if media_id_str.lower() in ["up", "+", "+1", "next", "forward", "seek_up", "scan_up", "+0.1"]:
                await self.async_seek_up()
                return
            elif media_id_str.lower() in ["down", "-", "-1", "prev", "previous", "backward", "seek_down", "scan_down", "-0.1"]:
                await self.async_seek_down()
                return

        # Selezione diretta preset (es. media_type='preset' o media_id='1'..'5' o 'P1'..'P5')
        p_clean = media_id_str.upper().replace("P", "").strip()
        if media_type_l in ["preset", "channel", "track"] or (p_clean.isdigit() and 1 <= int(p_clean) <= 5 and "." not in media_id_str):
            p_val = int(p_clean)
            now = time.time()
            old_freq = TUNER_STATE.get("freq") or self._tuner_freq
            TUNER_STATE["target_preset"] = p_val
            TUNER_STATE["old_freq"] = old_freq
            TUNER_STATE["transit_until"] = now + 1.5
            TUNER_STATE["preset"] = p_val
            self._tuner_preset = p_val
            learned_f = TUNER_PRESETS.get(p_val)
            if learned_f:
                TUNER_STATE["freq"] = learned_f
                self._tuner_freq = learned_f

            # Se la stanza era spenta o su un'altra sorgente, accendila e commutala su Tuner
            was_off = self._state not in (MediaPlayerState.PLAYING, "playing")
            if was_off:
                self._state = MediaPlayerState.PLAYING
                await self._async_release_decoder_and_pause()
                self._source = "Radio FM (Tuner)"
                self._active_decoder = None
                await self._send_own_command(f"*22*1#4#7*{self._where}##")
                await asyncio.sleep(0.18)
                await self._async_switch_source_hw("1")
            elif self._source not in ["Radio FM (Tuner)", "7"]:
                await self._async_release_decoder_and_pause()
                self._source = "Radio FM (Tuner)"
                self._active_decoder = None
                await self._async_switch_source_hw("1")

            self._refresh_media_title()
            self.async_write_ha_state()
            await self._send_own_command(f"*#22*{TUNER_WHERE}*#6*{p_val}##")
            return

        # Sintonizzazione frequenza numerica diretta (es. media_type='frequency' o media_id='95.2' o '102.5 MHz')
        f_clean = media_id_str.upper().replace("MHZ", "").strip()
        try:
            f_float = float(f_clean)
            if 87.0 <= f_float <= 108.5:
                await self._tune_frequency(f_float)
                return
        except ValueError:
            pass

        # ── 2. Streaming Audio via Dynamic Proxy (Music Assistant / Spotify) ────
        pool = self._get_pool()
        if not pool or not pool.is_configured:
            LOGGER.warning(
                "%s: play_media chiamato per streaming ma nessun decoder configurato nelle opzioni MyHome",
                self.entity_id,
            )
            return

        result = await pool.claim(self.entity_id)
        if result is None:
            raise HomeAssistantError(
                f"{self.entity_id}: Tutti gli streamer/decoder sono attualmente occupati da altre stanze!"
            )
        decoder_id, source_num = result
        self._active_decoder = decoder_id

        # Risveglia il decoder se in standby o spento
        dec_state = self.hass.states.get(decoder_id)
        if dec_state and dec_state.state in (MediaPlayerState.OFF, MediaPlayerState.IDLE):
            await self.hass.services.async_call("media_player", "turn_on", {"entity_id": decoder_id})
            for _ in range(10):
                await asyncio.sleep(0.5)
                dec_state = self.hass.states.get(decoder_id)
                if dec_state and dec_state.state not in (MediaPlayerState.OFF, MediaPlayerState.IDLE):
                    break

        # Commuta la zona BTicino sulla sorgente corretta
        if self._who == "22":
            src_id = str(source_num) if str(source_num) in ("1", "2", "3", "4") else "2"
            target_lbl = self._get_source_label(src_id)

            if self._state not in (MediaPlayerState.PLAYING, "playing"):
                await self._send_own_command(f"*22*1#4#{src_id}*{self._where}##")
                await asyncio.sleep(0.18)

            await self._async_switch_source_hw(src_id)
            self._source = target_lbl
        else:
            # Per WHO 16: invia selezione sorgente stereo
            for cmd in OWNSoundCommand.select_source(self._where, source_num):
                await self._gateway_handler.send(cmd)
                await asyncio.sleep(0.2)

        # Gain staging: imposta il volume del decoder proporzionalmente al volume della stanza (solo se pre_gain > 0)
        pre_gain_pct = pool.get_pre_gain(decoder_id)
        if pre_gain_pct > 0:
            cur_vol = self._volume_level if self._volume_level is not None else 0.5
            decoder_volume = min(1.0, max(0.0, cur_vol + pre_gain_pct / 100.0))
            try:
                await self.hass.services.async_call(
                    "media_player",
                    "volume_set",
                    {"entity_id": decoder_id, "volume_level": decoder_volume},
                    blocking=False,
                )
            except Exception as err:
                LOGGER.debug("Could not pre-set volume on decoder %s: %s", decoder_id, err)

        # Invia lo streaming URL al decoder esterno
        service_data = {
            "entity_id": decoder_id,
            "media_content_type": media_type,
            "media_content_id": media_id,
        }
        for k in ("announce", "enqueue", "extra"):
            if k in kwargs:
                service_data[k] = kwargs[k]

        try:
            await self.hass.services.async_call("media_player", "play_media", service_data)
        except Exception as err:
            LOGGER.error("%s: Errore inoltro play_media al decoder %s: %s", self.entity_id, decoder_id, err)
            await pool.release(self.entity_id)
            self._active_decoder = None
            raise HomeAssistantError(
                f"{self.entity_id}: Decoder {decoder_id} non è riuscito ad avviare la riproduzione: {err}"
            ) from err

    async def async_select_source(self, source: str):
        """Select input source directly via verified sequence."""
        was_off = self._state not in (MediaPlayerState.PLAYING, "playing")
        self._state = MediaPlayerState.PLAYING

        if self._who == "22":
            if "Radio" in source:
                if not was_off and self._source == "Radio FM (Tuner)":
                    self._active_decoder = None
                    self._refresh_media_title()
                    self.async_write_ha_state()
                    return

                await self._async_release_decoder_and_pause()

                self._source = "Radio FM (Tuner)"
                self._active_decoder = None
                p_to_recall = TUNER_STATE.get("preset") or self._tuner_preset or 1
                now = time.time()
                old_freq = TUNER_STATE.get("freq") or self._tuner_freq
                TUNER_STATE["target_preset"] = p_to_recall
                TUNER_STATE["old_freq"] = old_freq
                TUNER_STATE["transit_until"] = now + 1.5
                TUNER_STATE["preset"] = p_to_recall
                self._tuner_preset = p_to_recall
                learned_f = TUNER_PRESETS.get(p_to_recall)
                if learned_f:
                    TUNER_STATE["freq"] = learned_f
                    self._tuner_freq = learned_f
                self._refresh_media_title()
                self.async_write_ha_state()

                if was_off:
                    await self._send_own_command(f"*22*1#4#7*{self._where}##")
                    await asyncio.sleep(0.18)

                await self._async_switch_source_hw("1")

                await asyncio.sleep(0.15)
                await self._send_own_command(f"*#22*{TUNER_WHERE}*#6*{p_to_recall}##")
            else:
                # Estrazione accurata del canale sorgente BTicino (1-4)
                src_num = None
                pool = self._get_pool()
                if pool and pool.is_configured:
                    for dec_id, s_num in pool.decoder_map.items():
                        dec_state = self.hass.states.get(dec_id)
                        fname = dec_state.attributes.get("friendly_name") if dec_state else None
                        lbl = f"Ingresso AUX {s_num} ({fname})" if fname else f"Ingresso AUX {s_num}"
                        if source == lbl or source.strip() == f"Ingresso AUX {s_num}" or source == dec_id:
                            src_num = str(s_num)
                            break

                if not src_num:
                    import re
                    match = re.search(r"(?:AUX|Sorgente)\s*([1-4])", source, re.IGNORECASE)
                    if match:
                        src_num = match.group(1)
                    elif pool and len(pool.decoder_map) == 1:
                        src_num = str(list(pool.decoder_map.values())[0])
                    else:
                        src_num = "2"

                target_lbl = self._get_source_label(src_num)
                if not was_off and self._source == target_lbl:
                    await self._async_claim_decoder_and_resume()
                    self._refresh_media_title()
                    self.async_write_ha_state()
                    return

                self._source = target_lbl
                self._refresh_media_title()
                self.async_write_ha_state()

                if was_off:
                    await self._send_own_command(f"*22*1#4#{src_num}*{self._where}##")
                    await asyncio.sleep(0.18)

                await self._async_switch_source_hw(src_num)

                await self._async_claim_decoder_and_resume()
        else:
            src_id = "1" if "Radio" in source else "2"
            await self._send_own_command(f"*16*1#{src_id}*{self._where}##")
            if "Radio" in source:
                await self._async_release_decoder_and_pause()
            else:
                await self._async_claim_decoder_and_resume()

        self._refresh_media_title()
        self.async_write_ha_state()

    @callback
    def _async_decoder_state_changed(self, event):
        """Update UI when the active decoder or associated cloud stream changes state."""
        if not self._active_decoder or self._source in ["Radio FM (Tuner)", "7"]:
            return
        entity_id = event.data.get("entity_id")
        STREAMING_KEYWORDS = ("spotify", "tidal", "deezer", "mass", "music_assistant", "qobuz", "apple")
        target_cloud = self._get_target_streaming_entity()
        is_relevant = (
            entity_id == self._active_decoder
            or entity_id == target_cloud
            or any(kw in str(entity_id).lower() for kw in STREAMING_KEYWORDS)
        )
        if not is_relevant:
            return
        # Se il decoder hardware fisico ha cambiato stato (es. Echo Dot è passato da playing a idle quando Spotify Connect si connette, o viceversa),
        # aggiorna i player cloud con retry a cascata (0.2s, 1.0s, 2.5s) per sincronizzare immediatamente il cambio sorgente senza attendere il polling
        if entity_id == self._active_decoder:
            async def _cascade_flush():
                for delay in (0.2, 1.0, 2.5):
                    await asyncio.sleep(delay)
                    for s in self.hass.states.async_all("media_player"):
                        if any(kw in s.entity_id for kw in STREAMING_KEYWORDS):
                            try:
                                await self.hass.services.async_call(
                                    "homeassistant", "update_entity", {"entity_id": s.entity_id}, blocking=False
                                )
                            except Exception:
                                pass
            self.hass.async_create_task(_cascade_flush())

        self._refresh_media_title()
        self.async_write_ha_state()

    def handle_event(self, event):
        """Handle status update from the bus."""
        msg_str = str(event)
        LOGGER.debug("%s [Sound Zone %s] Ricevuto evento bus: %s", self._gateway.log_id, self._where, msg_str)

        # ── Gestione eventi WHO 16 da OWNSoundEvent ───────────────────────────
        if isinstance(event, OWNSoundEvent) or (hasattr(event, "who") and str(event.who) == "16"):
            if hasattr(event, "is_on") and event.is_on:
                self._state = MediaPlayerState.PLAYING
                self.async_write_ha_state()
            elif hasattr(event, "is_off") and event.is_off:
                self._state = MediaPlayerState.OFF
                self.hass.async_create_task(self._async_release_decoder_and_pause())
                self.async_write_ha_state()
            if hasattr(event, "volume") and event.volume is not None:
                self._volume_level = bticino_volume_to_ha(event.volume)
                self.async_write_ha_state()

        # ── Gestione eventi WHO 22 (Filodiffusione BTicino) ───────────────────
        # 1a. Messaggio Preset memorizzato dal Tuner (Dimensione 11: *#22*5#2#1*11*1*<freq>*<preset>##)
        if ("*11*1*" in msg_str or "*#11*1*" in msg_str) and ("*22*" in msg_str or "*#22*" in msg_str):
            try:
                parts = [p for p in msg_str.strip("#").split("*") if p]
                if len(parts) >= 5:
                    preset_str = parts[-1]
                    freq_raw_str = parts[-2]
                    if preset_str.isdigit() and freq_raw_str.isdigit():
                        p_idx = int(preset_str)
                        freq_raw = int(freq_raw_str)
                        p_freq_str = None
                        if 870 <= freq_raw <= 1085:
                            p_freq_str = f"{freq_raw / 10.0:.1f} MHz"
                        elif 8700 <= freq_raw <= 10850:
                            f_float = freq_raw / 100.0
                            p_freq_str = f"{f_float:.2f}".rstrip("0").rstrip(".") + " MHz" if (freq_raw % 10 != 0) else f"{f_float:.1f} MHz"
                        elif 87000 <= freq_raw <= 108500:
                            f_float = freq_raw / 1000.0
                            p_freq_str = f"{f_float:.2f}".rstrip("0").rstrip(".") + " MHz" if (freq_raw % 100 != 0) else f"{f_float:.1f} MHz"

                        if p_freq_str and 1 <= p_idx <= 5:
                            TUNER_PRESETS[p_idx] = p_freq_str
                            curr_p = TUNER_STATE.get("preset") or self._tuner_preset
                            if curr_p == p_idx:
                                TUNER_STATE["freq"] = p_freq_str
                                self._tuner_freq = p_freq_str
                                if self._source in ["Radio FM (Tuner)", "7"]:
                                    self._refresh_media_title()
                                    self.async_write_ha_state()
            except Exception as ex:
                LOGGER.debug("Errore parsing preset dimensione 11 %s: %s", msg_str, ex)

        # 1b. Messaggio Frequenza dal Tuner (Dimensione 5: *#22*5#2#1*5*1*<freq>## o *#22*2#1*5*1*<freq>##)
        elif ("*5*1*" in msg_str or "*#5*1*" in msg_str) and ("*22*" in msg_str or "*#22*" in msg_str):
            try:
                parts = [p for p in msg_str.strip("#").split("*") if p]
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
                            active_p = TUNER_STATE.get("preset") or self._tuner_preset
                            target_p = TUNER_STATE.get("target_preset")
                            old_f = TUNER_STATE.get("old_freq")
                            now = time.time()

                            # Se è in corso una commutazione di preset
                            if now < TUNER_STATE.get("transit_until", 0.0):
                                # Se il tuner emette ancora la vecchia frequenza della stazione precedente, scartala
                                if old_f and freq_str == old_f:
                                    LOGGER.debug(
                                        "Ignorata vecchia frequenza transitoria bus '%s' durante passaggio a P%s",
                                        freq_str, target_p or active_p
                                    )
                                    return

                            # La nuova frequenza è valida e confermata dal tuner:
                            eff_p = target_p if (now < TUNER_STATE.get("transit_until", 0.0) and target_p) else active_p
                            TUNER_STATE["freq"] = freq_str
                            self._tuner_freq = freq_str
                            TUNER_STATE["transit_until"] = 0.0
                            TUNER_STATE["target_preset"] = None
                            TUNER_STATE["old_freq"] = None

                            matched_p = None
                            for p_num, p_freq in TUNER_PRESETS.items():
                                if p_freq and p_freq == freq_str:
                                    matched_p = p_num
                                    break

                            if matched_p is not None:
                                TUNER_STATE["preset"] = matched_p
                                self._tuner_preset = matched_p
                            elif eff_p and 1 <= eff_p <= 5 and TUNER_PRESETS.get(eff_p) is None:
                                TUNER_PRESETS[eff_p] = freq_str
                                TUNER_STATE["preset"] = eff_p
                                self._tuner_preset = eff_p
                            else:
                                TUNER_STATE["preset"] = None
                                self._tuner_preset = None

                            if self._source in ["Radio FM (Tuner)", "7"]:
                                self._refresh_media_title()
                                self.async_write_ha_state()
            except Exception as ex:
                LOGGER.debug("Errore parsing frequenza %s: %s", msg_str, ex)

        # 2. Messaggio Preset dal Tuner (Dimensione 6: *#22*2#1*6*<preset>## o *#22*2#1*#6*<preset>##)
        elif ("*6*" in msg_str or "*#6*" in msg_str) and ("*2#1" in msg_str or "*5#2#1" in msg_str):
            try:
                parts = [p for p in msg_str.strip("#").split("*") if p]
                if parts:
                    last_val = parts[-1]
                    if last_val.isdigit():
                        preset_val = int(last_val)
                        if 1 <= preset_val <= 5:
                            now = time.time()
                            old_freq = TUNER_STATE.get("freq") or self._tuner_freq
                            TUNER_STATE["target_preset"] = preset_val
                            TUNER_STATE["old_freq"] = old_freq
                            TUNER_STATE["transit_until"] = now + 1.5
                            TUNER_STATE["preset"] = preset_val
                            self._tuner_preset = preset_val
                            learned_f = TUNER_PRESETS.get(preset_val)
                            if learned_f:
                                TUNER_STATE["freq"] = learned_f
                                self._tuner_freq = learned_f
                            if self._source in ["Radio FM (Tuner)", "7"]:
                                self._refresh_media_title()
                                self.async_write_ha_state()
                        elif preset_val == 0:
                            TUNER_STATE["preset"] = None
                            TUNER_STATE["target_preset"] = None
                            self._tuner_preset = None
                            if self._source in ["Radio FM (Tuner)", "7"]:
                                self._refresh_media_title()
                                self.async_write_ha_state()
            except Exception as ex:
                LOGGER.debug("Errore parsing preset %s: %s", msg_str, ex)

        # 3. Messaggi destinati all'amplificatore di questa stanza
        if self._where in msg_str or self._full_where in msg_str:
            clean = msg_str.strip("#").split("*")
            norm_parts = [p.replace("#", "") for p in clean if p]

            # Stato Dimensione 12: *#22*WHERE*12*ST*MOD## (es. *#22*WHERE*12*1*4## -> ON, *#22*WHERE*12*0*10## -> OFF)
            if "12" in norm_parts and ("*12*" in msg_str or "*#12*" in msg_str):
                try:
                    idx = norm_parts.index("12")
                    if len(norm_parts) > idx + 1:
                        st = norm_parts[idx + 1]
                        if st == "0":
                            self._state = MediaPlayerState.OFF
                            self.hass.async_create_task(self._async_release_decoder_and_pause())
                        elif st == "1":
                            self._state = MediaPlayerState.PLAYING
                            # Dimension 12 segnala lo stato di accensione dell'amplificatore (st=1 ON, st=0 OFF).
                            active_src, active_dec = self._get_active_house_source()
                            global_src = self._get_global_matrix_source()
                            target_src = active_src or global_src or self._source or "Radio FM (Tuner)"
                            self._source = target_src
                            if "Radio" in target_src:
                                self._active_decoder = None
                            else:
                                self.hass.async_create_task(self._async_claim_decoder_and_resume())
                    self._refresh_media_title()
                    self.async_write_ha_state()
                except Exception as ex:
                    LOGGER.debug("Errore parsing stato sorgente %s: %s", msg_str, ex)

            # Comando o Evento con selezione sorgente diretta (1#4#SRC) per questa stanza
            # es. *22*1#4#7*WHERE## (Tuner), *22*1#4#2*WHERE## (AUX 2), *22*1#4#0*WHERE## (OFF)
            elif "#4#" in msg_str:
                try:
                    for part in clean:
                        if part.startswith("1#4#") or part.startswith("0#4#"):
                            src_code = part.split("#")[-1]
                            if src_code == "0":
                                self._state = MediaPlayerState.OFF
                                self.hass.async_create_task(self._async_release_decoder_and_pause())
                            elif src_code == "7":
                                self._state = MediaPlayerState.PLAYING
                                self._source = "Radio FM (Tuner)"
                                self.hass.async_create_task(self._async_release_decoder_and_pause())
                                self._refresh_media_title()
                            elif src_code in ("1", "2", "3", "4"):
                                self._state = MediaPlayerState.PLAYING
                                self._source = self._get_source_label(src_code)
                                pool = self._get_pool()
                                is_streaming_decoder = (
                                    pool and pool.is_configured and
                                    any(str(num) == str(src_code) for num in pool.decoder_map.values())
                                )
                                if is_streaming_decoder:
                                    self.hass.async_create_task(self._async_claim_decoder_and_resume())
                                else:
                                    self.hass.async_create_task(self._async_release_decoder_and_pause())
                                self._refresh_media_title()
                    self.async_write_ha_state()
                except Exception as ex:
                    LOGGER.debug("Errore parsing #4# sorgente %s: %s", msg_str, ex)

            # Comando ON semplice: *22*1*WHERE## o *16*1*WHERE##
            elif msg_str.startswith(f"*22*1*{self._where}") or msg_str.startswith(f"*16*1*{self._where}"):
                self._state = MediaPlayerState.PLAYING
                if not self._source:
                    active_src, active_dec = self._get_active_house_source()
                    if active_src:
                        self._source = active_src
                        if active_dec:
                            self.hass.async_create_task(self._async_claim_decoder_and_resume())
                    else:
                        self._source = "Radio FM (Tuner)"
                self._refresh_media_title()
                self.async_write_ha_state()

            # Accensione da tasto fisico a muro / commutazione matrice: *22*9*5#WHERE## o *22*22#4#A*5#WHERE##
            elif msg_str.startswith("*22*9*") or "*22#4#" in msg_str:
                self._state = MediaPlayerState.PLAYING
                if not self._source:
                    active_src, active_dec = self._get_active_house_source()
                    self._source = active_src or "Radio FM (Tuner)"
                    if active_dec:
                        self.hass.async_create_task(self._async_claim_decoder_and_resume())
                self._refresh_media_title()
                self.async_write_ha_state()

                # Query asincrona per allineare sorgente e frequenza dalla matrice
                async def _delayed_status_refresh():
                    await asyncio.sleep(0.3)
                    await self._send_own_command(f"*#22*{self._where}*12##")
                self.hass.async_create_task(_delayed_status_refresh())

            # Comando OFF semplice: *22*0*WHERE## o *16*0*WHERE##
            elif msg_str.startswith(f"*22*0*{self._where}") or msg_str.startswith(f"*16*0*{self._where}"):
                self._state = MediaPlayerState.OFF
                self.hass.async_create_task(self._async_release_decoder_and_pause())
                self.async_write_ha_state()

            # Incremento / Decremento volume da tasti fisici a muro: *22*3#1*WHERE## o *22*4#1*WHERE##
            elif "*3#1*" in msg_str:
                cur_val = ha_volume_to_bticino(self._volume_level or 0.5)
                new_val = min(31, cur_val + 1)
                self._volume_level = bticino_volume_to_ha(new_val)
                self.async_write_ha_state()
            elif "*4#1*" in msg_str:
                cur_val = ha_volume_to_bticino(self._volume_level or 0.5)
                new_val = max(1, cur_val - 1)
                self._volume_level = bticino_volume_to_ha(new_val)
                self.async_write_ha_state()

            # Stato Dimensione 1 (Volume): *#22*WHERE*1*VAL## oppure *#22*WHERE*#1*VAL##
            elif "*1*" in msg_str or "*#1*" in msg_str:
                try:
                    prefix = f"*#22*{self._where}*"
                    if prefix in msg_str:
                        tail = msg_str.split(prefix)[-1].rstrip("#")
                        sub_parts = tail.split("*")
                        if sub_parts and sub_parts[0] in ("1", "#1") and len(sub_parts) > 1:
                            val = int(sub_parts[1].replace("#", ""))
                            if 1 <= val <= 31:
                                self._volume_level = bticino_volume_to_ha(val)
                                self.async_write_ha_state()
                    elif "1" in norm_parts:
                        idx = norm_parts.index("1")
                        if len(norm_parts) > idx + 1:
                            val = int(norm_parts[idx + 1])
                            if 1 <= val <= 31:
                                self._volume_level = bticino_volume_to_ha(val)
                                self.async_write_ha_state()
                except Exception as ex:
                    LOGGER.debug("Errore parsing volume %s: %s", msg_str, ex)

        # 4. Riconoscimento commutazione sorgente da tasto a muro o matrice (WHO 22)
        # La matrice invia *22*2#4#7*5#2#1## per Tuner, *22*2#4#7*5#2#2## per AUX 2, ecc.
        if "*22*2#4#7*5#2#" in msg_str:
            try:
                src_code = msg_str.split("*5#2#")[-1].replace("#", "").strip()
                mac = getattr(self._gateway_handler, "mac", None)
                if mac:
                    self.hass.data.setdefault(DOMAIN, {}).setdefault(mac, {})["active_matrix_source"] = src_code
                setattr(self._gateway_handler, "active_matrix_source", src_code)

                if src_code == "1":
                    target_lbl = "Radio FM (Tuner)"
                    if self._state in (MediaPlayerState.PLAYING, "playing"):
                        if self._source != target_lbl:
                            self._source = target_lbl
                            self._active_decoder = None
                            self.hass.async_create_task(self._async_release_decoder_and_pause())
                    else:
                        self._source = target_lbl
                        self._active_decoder = None
                elif src_code in ("2", "3", "4"):
                    target_lbl = self._get_source_label(src_code)
                    if self._state in (MediaPlayerState.PLAYING, "playing"):
                        if self._source != target_lbl:
                            self._source = target_lbl
                            self.hass.async_create_task(self._async_claim_decoder_and_resume())
                    else:
                        self._source = target_lbl
                self._refresh_media_title()
                self.async_write_ha_state()
            except Exception as ex:
                LOGGER.debug("Errore parsing evento matrice sorgente: %s", ex)