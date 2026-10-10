"""Active and passive discovery functions for MyHome bus."""
import asyncio
import logging
from typing import List, Dict

from .ownd.connection import OWNSession, OWNCommandSession, OWNEventSession
from .ownd.message import OWNMessage, OWNSignaling

from .const import LOGGER

import re

async def async_scan_bus(gateway, who: str, addresses: List[str]) -> List[str]:
    """Scan a list of addresses on the OpenWebNet bus for a specific WHO."""
    session = OWNCommandSession(gateway=gateway, logger=LOGGER)
    connect_res = await session.connect()
    if not connect_res or not connect_res.get("Success"):
        raise ConnectionError(f"Failed to connect to gateway: {connect_res.get('Message')}")

    discovered = []
    try:
        for addr in addresses:
            # Drain any stale frames from previous queries
            while True:
                try:
                    await asyncio.wait_for(session._stream_reader.readuntil(OWNSession.SEPARATOR), timeout=0.01)
                except asyncio.TimeoutError:
                    break

            cmd = f"*#{who}*{addr}##"
            try:
                session._stream_writer.write(cmd.encode())
                await session._stream_writer.drain()
            except (OSError, ConnectionResetError, BrokenPipeError):
                LOGGER.warning("Connection dropped before sending to %s. Reconnecting...", addr)
                try:
                    await session.close()
                except Exception:
                    pass
                await asyncio.sleep(0.5)
                session = OWNCommandSession(gateway=gateway, logger=LOGGER)
                await session.connect()
                session._stream_writer.write(cmd.encode())
                await session._stream_writer.drain()

            has_state = False
            deadline = asyncio.get_event_loop().time() + 0.40
            try:
                while asyncio.get_event_loop().time() < deadline:
                    remaining = max(0.01, deadline - asyncio.get_event_loop().time())
                    raw_response = await asyncio.wait_for(
                        session._stream_reader.readuntil(OWNSession.SEPARATOR),
                        timeout=remaining
                    )
                    frame = raw_response.decode().strip()

                    # Strictly verify that the frame belongs to THIS who AND THIS addr
                    if who == "1" and re.match(rf"^\*1\*[0-9#]+\*{re.escape(addr)}##$", frame):
                        has_state = True
                    elif who == "2" and re.match(rf"^\*2\*[0-9#]+\*{re.escape(addr)}##$", frame):
                        has_state = True
                    elif who == "4" and (re.match(rf"^\*#?4\*[0-9#]+\*{re.escape(addr)}.*##$", frame) or re.match(rf"^\*4\*[0-9#]+\*{re.escape(addr)}##$", frame)):
                        has_state = True
                    elif who in ("16", "22") and re.match(rf"^\*#?{who}\*.*{re.escape(addr)}.*##$", frame):
                        has_state = True

                    if frame in ("*#*0##", "*#*1##"):
                        break
            except asyncio.TimeoutError:
                pass
            except (ConnectionResetError, asyncio.IncompleteReadError, BrokenPipeError, OSError) as conn_err:
                LOGGER.warning("Connection lost scanning %s: %s. Reconnecting...", addr, conn_err)
                try:
                    await session.close()
                except Exception:
                    pass
                await asyncio.sleep(0.5)
                session = OWNCommandSession(gateway=gateway, logger=LOGGER)
                await session.connect()
                continue
            except Exception as err:
                LOGGER.warning("Error scanning address %s: %s", addr, err)
                continue

            if has_state:
                discovered.append(addr)
                LOGGER.info("Discovered device at WHO %s, WHERE %s", who, addr)

            await asyncio.sleep(0.03)
    finally:
        try:
            await session.close()
        except Exception:
            pass

    return discovered

async def async_sniff_bus(gateway, duration_seconds: int) -> Dict[str, Dict[str, dict]]:
    """Sniff the OpenWebNet event stream for a duration to passively discover active devices."""
    session = OWNEventSession(gateway=gateway, logger=LOGGER)
    connect_res = await session.connect()
    if not connect_res or not connect_res.get("Success"):
        raise ConnectionError(f"Failed to connect event session: {connect_res.get('Message')}")

    discovered = {}
    start_time = asyncio.get_event_loop().time()
    try:
        while asyncio.get_event_loop().time() - start_time < duration_seconds:
            try:
                message = await asyncio.wait_for(session.get_next(), timeout=1.0)
                if message is None:
                    continue
                
                if isinstance(message, OWNMessage) and not isinstance(message, OWNSignaling):
                    who = getattr(message, "who", None)
                    where = getattr(message, "where", None)
                    
                    if who is None or where is None:
                        continue
                    who = str(who)
                    where = str(where)
                        
                    platform = None
                    dev_conf = {"who": who, "where": where}
                    
                    if who == "1":
                        platform = "light"
                        dev_conf["dimmable"] = hasattr(message, "brightness") and message.brightness is not None
                        dev_conf["name"] = f"Light {where}"
                    elif who == "2":
                        platform = "cover"
                        dev_conf["name"] = f"Cover {where}"
                    elif who == "4":
                        platform = "climate"
                        dev_conf["name"] = f"Zone {where}"
                        dev_conf["zone"] = where
                    elif who == "16":
                        platform = "media_player"
                        dev_conf["name"] = f"Sound Zone {where}"
                    elif who == "22":
                        platform = "media_player"
                        dev_conf["name"] = f"Sound Zone {where}"
                    
                    if platform:
                        dev_id = f"{who}-{where}"
                        if platform not in discovered:
                            discovered[platform] = {}
                        if dev_id not in discovered[platform]:
                            discovered[platform][dev_id] = dev_conf
                            LOGGER.info("Sniffed new device: %s (%s)", dev_id, platform)
            except asyncio.TimeoutError:
                continue
            except Exception as err:
                LOGGER.warning("Error during sniffing: %s", err)
                continue
    finally:
        await session.close()
        
    return discovered

async def async_scan_sound_who22(gateway, quick: bool = False) -> Dict[str, dict]:
    """Scan bus specifically for WHO 22 Sound Diffusion devices: amplifier zones (3#A#P) and Tuner sources (2#S)."""
    session = OWNCommandSession(gateway=gateway, logger=LOGGER)
    connect_res = await session.connect()
    if not connect_res or not connect_res.get("Success"):
        LOGGER.warning("Could not connect command session for Sound WHO 22 discovery: %s", connect_res.get("Message"))
        return {}

    discovered_sound = {}

    # 1. Moduli Tuner FM / Sorgenti (Tipo 2: 2#S) - sorgenti da 1 a 4
    # Si interrogano con Dimensione 6 (preset) o 4 (frequenza)
    sources = range(1, 5)

    # 2. Punti Sonori / Amplificatori (Tipo 3: 3#A#P)
    # Ambienti A da 0 a 9 (incluso 0 per impianti senza configuratori su A o piano terra)
    environments = range(0, 8) if quick else range(0, 10)
    sound_points = range(1, 10)

    scan_items = []
    for s in sources:
        addr = f"2#{s}"
        scan_items.append((addr, f"*#22*{addr}*6##", f"Sound Source {addr}", "tuner"))

    for a in environments:
        for p in sound_points:
            addr = f"3#{a}#{p}"
            scan_items.append((addr, f"*#22*{addr}*12##", f"Sound Zone {addr}", "amplifier"))

    async def _query(target_cmd: str, target_addr: str) -> bool:
        nonlocal session
        # Drain any stale frames
        while True:
            try:
                await asyncio.wait_for(session._stream_reader.readuntil(OWNSession.SEPARATOR), timeout=0.005)
            except asyncio.TimeoutError:
                break

        try:
            session._stream_writer.write(target_cmd.encode())
            await session._stream_writer.drain()
        except (OSError, ConnectionResetError, BrokenPipeError):
            LOGGER.warning("Connection dropped before sending %s. Reconnecting...", target_cmd)
            try:
                await session.close()
            except Exception:
                pass
            await asyncio.sleep(0.3)
            session = OWNCommandSession(gateway=gateway, logger=LOGGER)
            await session.connect()
            session._stream_writer.write(target_cmd.encode())
            await session._stream_writer.drain()

        got_state = False
        deadline = asyncio.get_event_loop().time() + 0.45
        try:
            while asyncio.get_event_loop().time() < deadline:
                remaining = max(0.01, deadline - asyncio.get_event_loop().time())
                raw_response = await asyncio.wait_for(
                    session._stream_reader.readuntil(OWNSession.SEPARATOR),
                    timeout=remaining,
                )
                frame = raw_response.decode().strip()

                if f"*22*{target_addr}" in frame or f"*#22*{target_addr}" in frame:
                    got_state = True

                if frame in ("*#*0##", "*#*1##"):
                    break
        except asyncio.TimeoutError:
            pass
        except (ConnectionResetError, asyncio.IncompleteReadError, BrokenPipeError, OSError) as conn_err:
            LOGGER.warning("Connection lost scanning sound %s: %s. Reconnecting...", target_addr, conn_err)
            try:
                await session.close()
            except Exception:
                pass
            await asyncio.sleep(0.3)
            session = OWNCommandSession(gateway=gateway, logger=LOGGER)
            await session.connect()
        except Exception as err:
            LOGGER.warning("Error scanning sound address %s: %s", target_addr, err)

        return got_state

    try:
        for addr, cmd, default_name, item_type in scan_items:
            has_state = await _query(cmd, addr)

            # Se la dimensione 12 non ha risposto (es. amplificatore in standby profondo),
            # tentiamo con la dimensione volume 1 (*#22*3#A#P*1##) o richiesta stato generale
            if not has_state and item_type == "amplifier":
                has_state = await _query(f"*#22*{addr}*1##", addr)
                if not has_state:
                    has_state = await _query(f"*#22*{addr}##", addr)

            if has_state:
                dev_id = f"22-{addr}"
                discovered_sound[dev_id] = {
                    "who": "22",
                    "where": addr,
                    "name": default_name,
                }
                LOGGER.info("Discovered Sound Diffusion device at WHO 22, WHERE %s (%s)", addr, item_type)

            await asyncio.sleep(0.02)

    finally:
        try:
            await session.close()
        except Exception:
            pass

    return discovered_sound


async def async_discover_all_devices(gateway, quick: bool = False) -> Dict[str, Dict[str, dict]]:
    """Perform an active scan of all possible bus addresses for lights, covers, climate, and sound."""
    discovered = {}
    
    if quick:
        ptp_addresses = []
        for a in range(1, 6):
            for pl in range(1, 10):
                ptp_addresses.append(f"{a}{pl}")
        climate_addresses = [str(z) for z in range(1, 5)]
    else:
        ptp_addresses = []
        for a in range(1, 10):
            for pl in range(1, 10):
                ptp_addresses.append(f"{a}{pl}")
        climate_addresses = [str(z) for z in range(1, 100)]
    
    # 1. Lights (WHO = 1)
    LOGGER.info("Starting active bus scan for Lights...")
    lights = await async_scan_bus(gateway, "1", ptp_addresses)
    if lights:
        discovered["light"] = {}
        for addr in lights:
            dev_id = f"1-{addr}"
            discovered["light"][dev_id] = {
                "who": "1",
                "where": addr,
                "name": f"Light {addr}",
                "dimmable": False
            }
            
    # 2. Covers (WHO = 2)
    LOGGER.info("Starting active bus scan for Covers...")
    covers = await async_scan_bus(gateway, "2", ptp_addresses)
    if covers:
        discovered["cover"] = {}
        for addr in covers:
            dev_id = f"2-{addr}"
            discovered["cover"][dev_id] = {
                "who": "2",
                "where": addr,
                "name": f"Cover {addr}"
            }
            
    # 3. Climate (WHO = 4)
    LOGGER.info("Starting active bus scan for Climate zones...")
    climates = await async_scan_bus(gateway, "4", climate_addresses)
    if climates:
        discovered["climate"] = {}
        for addr in climates:
            dev_id = f"4-{addr}"
            discovered["climate"][dev_id] = {
                "who": "4",
                "zone": addr,
                "name": f"Zone {addr}"
            }
            
    # 4. Sound Diffusion (WHO = 22): Punti sonori 3#A#P e Tuner 2#S
    LOGGER.info("Starting active bus scan for Sound Diffusion zones (WHO 22)...")
    sound_devices_22 = await async_scan_sound_who22(gateway, quick=quick)
    if sound_devices_22:
        if "media_player" not in discovered:
            discovered["media_player"] = {}
        discovered["media_player"].update(sound_devices_22)

    if not quick:
        # 5. Legacy Sound Diffusion (WHO = 16)
        LOGGER.info("Starting active bus scan for Sound Diffusion zones (WHO 16)...")
        audio_addresses = [str(a) for a in range(1, 10)] + ptp_addresses
        for zone in range(11, 15):
            for sub in range(0, 10):
                audio_addresses.append(f"{zone}{sub}")
                
        audio_zones = await async_scan_bus(gateway, "16", audio_addresses)
        if audio_zones:
            if "media_player" not in discovered:
                discovered["media_player"] = {}
            for addr in audio_zones:
                dev_id = f"16-{addr}"
                if dev_id not in discovered["media_player"]:
                    discovered["media_player"][dev_id] = {
                        "who": "16",
                        "where": addr,
                        "name": f"Sound Zone {addr}"
                    }

    return discovered
