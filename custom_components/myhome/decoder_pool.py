"""Decoder pool manager for the MyHOME Dynamic Proxy.

Manages a pool of streaming decoders (squeezelite / Cambridge Audio) that are
physically connected to the BTicino F441M matrix source inputs.  The proxy
intercepts Music Assistant / Spotify play_media commands, claims an idle decoder
from this pool, routes the BTicino matrix to the correct source input, and
forwards the stream URL to the backend decoder.

Architecture
------------
- One ``DecoderPool`` instance per gateway, keyed by MAC address in
  ``hass.data[DOMAIN][mac]["decoder_pool"]``.
- Survives entity reloads (lives in hass.data, not inside an entity).
- Thread-safe: all claim/release operations are serialised with a single
  ``asyncio.Lock`` to prevent race conditions when multiple zones compete for
  the last available decoder.
- State-aware: inspects the live HA entity state of each decoder to determine
  whether it is truly idle before claiming.

Gain staging (anti-hiss)
------------------------
Each decoder carries an optional ``pre_gain`` offset (0–50 %).  When the user
adjusts the BTicino zone volume the proxy also sets the decoder volume to
``zone_volume + pre_gain``, capped at 1.0.  This keeps the analog signal level
high and the BTicino amplifier gain low, which reduces the inherent noise floor
of the 2-wire bus.

Typical values
--------------
- Cambridge Audio with Pre-Amp OFF: ``pre_gain = 0`` (already at full line level)
- Squeezelite / piCorePlayer:       ``pre_gain = 20``
"""
import asyncio

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

from .const import (
    LOGGER,
    CONF_DECODER_MODE,
    DECODER_MODE_SHARED,
    DECODER_MODE_EXCLUSIVE,
)


class DecoderPool:
    """Thread-safe pool of streaming decoders mapped to BTicino source inputs.

    Each decoder (squeezelite, Cambridge Audio, etc.) is physically connected
    to one of the 4 BTicino source inputs.  This class handles:

    - Thread-safe allocation via ``asyncio.Lock``
    - State-aware idle detection (inspects live HA entity state)
    - Pre-gain configuration per decoder (gain staging / anti-hiss)
    - Configurable Mode: Shared Bus Source (Multiroom) vs Exclusive Matrix Channels
    - Graceful release on zone turn-off or options reload
    """

    # HA states that mean "this decoder is available for claiming".
    # UNAVAILABLE is intentionally excluded: treat an offline Cambridge as busy
    # rather than risking a claim on a device that cannot actually play.
    _IDLE_STATES: frozenset = frozenset({
        MediaPlayerState.IDLE,
        MediaPlayerState.OFF,
        MediaPlayerState.PAUSED,
        None,  # entity not yet registered / state unknown
    })

    def __init__(
        self,
        hass: HomeAssistant,
        decoder_map: dict[str, int],
        pre_gain_map: dict[str, int] | None = None,
        mode: str = DECODER_MODE_SHARED,
    ) -> None:
        """Initialise the decoder pool."""
        self._hass = hass
        self._decoder_map: dict[str, int] = decoder_map          # entity_id → source_num
        self._pre_gain_map: dict[str, int] = pre_gain_map or {}  # entity_id → pre_gain %
        self._mode: str = mode                                   # shared vs exclusive
        self._assignments: dict[str, str | None] = {             # entity_id → zone_entity_id or None (exclusive)
            entity_id: None for entity_id in decoder_map
        }
        self._active_listeners: dict[str, set[str]] = {          # entity_id → set(zone_entity_ids) (shared)
            entity_id: set() for entity_id in decoder_map
        }
        self._lock = asyncio.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        """Return ``True`` if at least one decoder has been mapped."""
        return len(self._decoder_map) > 0

    @property
    def decoder_map(self) -> dict[str, int]:
        """Return {decoder_entity_id: source_num}."""
        return self._decoder_map

    @property
    def mode(self) -> str:
        """Return current decoder mode."""
        return self._mode

    def has_active_listeners(self, decoder_entity_id: str) -> bool:
        """Return True if other zones are still actively listening to this decoder."""
        listeners = self._active_listeners.get(decoder_entity_id, set())
        return len(listeners) > 0

    async def claim(self, zone_entity_id: str) -> tuple[str, int] | None:
        """Claim a decoder for *zone_entity_id*."""
        async with self._lock:
            # If this zone already owns or listens to a decoder, reuse it (idempotent).
            for dec_id, listeners in self._active_listeners.items():
                if zone_entity_id in listeners:
                    LOGGER.debug("Decoder %s already claimed by %s (shared)", dec_id, zone_entity_id)
                    return (dec_id, self._decoder_map[dec_id])

            for dec_id, owner in self._assignments.items():
                if owner == zone_entity_id:
                    LOGGER.debug("Decoder %s already claimed by %s (exclusive)", dec_id, zone_entity_id)
                    return (dec_id, self._decoder_map[dec_id])

            # ── 1. Shared Bus Source Mode (Multiroom) ─────────────────────────
            if self._mode == DECODER_MODE_SHARED:
                target_dec = None
                # Check if a decoder is already actively streaming to another zone
                for dec_id, listeners in self._active_listeners.items():
                    if len(listeners) > 0:
                        target_dec = dec_id
                        break

                # If none is currently active, pick the first configured decoder
                if target_dec is None and self._decoder_map:
                    target_dec = next(iter(self._decoder_map))

                if target_dec is not None:
                    self._active_listeners[target_dec].add(zone_entity_id)
                    self._assignments[target_dec] = zone_entity_id
                    LOGGER.info(
                        "DecoderPool [SHARED]: %s joined by zone %s (total listeners: %d, source %s)",
                        target_dec,
                        zone_entity_id,
                        len(self._active_listeners[target_dec]),
                        self._decoder_map[target_dec],
                    )
                    return (target_dec, self._decoder_map[target_dec])

            # ── 2. Exclusive Matrix Channels Mode ─────────────────────────────
            # Pass 1: Find the first decoder that is unassigned AND truly idle.
            candidate_unassigned = None
            for dec_id, owner in self._assignments.items():
                if owner is not None:
                    continue  # already in use by another zone

                state = self._hass.states.get(dec_id)
                state_val = state.state if state else None

                if state_val in self._IDLE_STATES:
                    self._assignments[dec_id] = zone_entity_id
                    LOGGER.info(
                        "DecoderPool: %s claimed by zone %s (source %s)",
                        dec_id,
                        zone_entity_id,
                        self._decoder_map[dec_id],
                    )
                    return (dec_id, self._decoder_map[dec_id])
                elif state_val != STATE_UNAVAILABLE:
                    if candidate_unassigned is None:
                        candidate_unassigned = dec_id

            # Pass 2: If no unassigned decoder is idle, but an unassigned decoder exists
            # (not owned by any other BTicino zone), claim it and take over playback.
            if candidate_unassigned is not None:
                self._assignments[candidate_unassigned] = zone_entity_id
                LOGGER.info(
                    "DecoderPool: %s claimed by zone %s (fallback unassigned, source %s)",
                    candidate_unassigned,
                    zone_entity_id,
                    self._decoder_map[candidate_unassigned],
                )
                return (candidate_unassigned, self._decoder_map[candidate_unassigned])

            # Pass 3: Reclaim decoder from an INACTIVE zone (off, idle, standby, or paused)
            for dec_id, owner in self._assignments.items():
                if owner is not None and owner != zone_entity_id:
                    owner_state = self._hass.states.get(owner)
                    owner_val = owner_state.state if owner_state else None
                    if owner_val in (
                        MediaPlayerState.OFF,
                        MediaPlayerState.IDLE,
                        MediaPlayerState.PAUSED,
                        None,
                    ):
                        old_owner = owner
                        self._assignments[dec_id] = zone_entity_id
                        LOGGER.info(
                            "DecoderPool: Reclaimed decoder %s from inactive zone %s for new zone %s (source %s)",
                            dec_id,
                            old_owner,
                            zone_entity_id,
                            self._decoder_map[dec_id],
                        )
                        from homeassistant.helpers.dispatcher import async_dispatcher_send
                        async_dispatcher_send(self._hass, f"myhome_decoder_lost_{old_owner}")
                        return (dec_id, self._decoder_map[dec_id])

            # Pass 4: Follow-Me Steal — If only 1 decoder is configured in the pool,
            # transfer it to the new requesting zone and release the old zone.
            if len(self._decoder_map) == 1:
                dec_id = next(iter(self._decoder_map))
                old_owner = self._assignments.get(dec_id)
                if old_owner and old_owner != zone_entity_id:
                    self._assignments[dec_id] = zone_entity_id
                    LOGGER.info(
                        "DecoderPool: Single-decoder follow-me: transferred %s from %s to %s (source %s)",
                        dec_id,
                        old_owner,
                        zone_entity_id,
                        self._decoder_map[dec_id],
                    )
                    from homeassistant.helpers.dispatcher import async_dispatcher_send
                    async_dispatcher_send(self._hass, f"myhome_decoder_lost_{old_owner}")
                    return (dec_id, self._decoder_map[dec_id])

            # All decoders are busy with other active BTicino zones or unavailable.
            LOGGER.warning(
                "DecoderPool: all decoders busy — zone %s cannot play", zone_entity_id
            )
            return None

    async def release(self, zone_entity_id: str) -> str | None:
        """Release the decoder assigned to *zone_entity_id*.

        Args:
            zone_entity_id: The ``entity_id`` of the BTicino zone releasing
                its decoder.

        Returns:
            The freed ``decoder_entity_id``, or ``None`` if the zone had no
            active assignment.

        Example::

            freed = await pool.release("media_player.audio_zone_3")
        """
        async with self._lock:
            # Shared mode release
            for dec_id, listeners in self._active_listeners.items():
                if zone_entity_id in listeners:
                    listeners.discard(zone_entity_id)
                    LOGGER.info(
                        "DecoderPool [SHARED]: Zone %s left decoder %s (remaining listeners: %d)",
                        zone_entity_id,
                        dec_id,
                        len(listeners),
                    )
                    if len(listeners) == 0:
                        self._assignments[dec_id] = None
                    return dec_id

            # Exclusive mode release
            for dec_id, owner in self._assignments.items():
                if owner == zone_entity_id:
                    self._assignments[dec_id] = None
                    LOGGER.info(
                        "DecoderPool [EXCLUSIVE]: %s released by zone %s",
                        dec_id,
                        zone_entity_id,
                    )
                    return dec_id
        return None

    async def release_all(self) -> None:
        """Release every decoder assignment."""
        async with self._lock:
            for dec_id in self._assignments:
                self._assignments[dec_id] = None
            for dec_id in self._active_listeners:
                self._active_listeners[dec_id].clear()
            LOGGER.info("DecoderPool: all assignments released (options reload)")

    def get_assignment(self, zone_entity_id: str) -> str | None:
        """Return the decoder entity_id assigned to *zone_entity_id*, or ``None``."""
        for dec_id, listeners in self._active_listeners.items():
            if zone_entity_id in listeners:
                return dec_id
        for dec_id, owner in self._assignments.items():
            if owner == zone_entity_id:
                return dec_id
        return None

    def get_pre_gain(self, decoder_entity_id: str) -> int:
        """Return the configured pre-gain offset for *decoder_entity_id*.

        Args:
            decoder_entity_id: The ``entity_id`` of the decoder.

        Returns:
            The pre-gain percent (0–50).  Defaults to ``0`` if not configured.

        Example::

            gain_pct = pool.get_pre_gain("media_player.cambridge_audio_cxn")
            decoder_volume = min(1.0, zone_volume + gain_pct / 100.0)
        """
        return self._pre_gain_map.get(decoder_entity_id, 0)

    # ── Introspection (for listeners and tests) ───────────────────────────────

    @property
    def decoder_entity_ids(self) -> list[str]:
        """Return all configured decoder entity IDs."""
        return list(self._decoder_map.keys())

    def has_active_listeners(self, decoder_entity_id: str) -> bool:
        """Return True if any zone is currently listening to this decoder."""
        if self._mode == DECODER_MODE_SHARED:
            return len(self._active_listeners.get(decoder_entity_id, set())) > 0
        return self._assignments.get(decoder_entity_id) is not None

    def __repr__(self) -> str:  # pragma: no cover
        busy = sum(1 for v in self._assignments.values() if v is not None)
        return (
            f"<DecoderPool decoders={len(self._decoder_map)} "
            f"busy={busy}/{len(self._decoder_map)}>"
        )
