"""Active and passive discovery functions for MyHome bus."""
import asyncio
import logging
from typing import List, Dict

from OWNd.connection import OWNSession, OWNCommandSession, OWNEventSession
from OWNd.message import OWNMessage

from .const import LOGGER

async def async_scan_bus(gateway, who: str, addresses: List[str]) -> List[str]:
    """Scan a list of addresses on the OpenWebNet bus for a specific WHO."""
    session = OWNCommandSession(gateway=gateway, logger=LOGGER)
    connect_res = await session.connect()
    if not connect_res or not connect_res.get("Success"):
        raise ConnectionError(f"Failed to connect to gateway: {connect_res.get('Message')}")

    discovered = []
    try:
        for addr in addresses:
            cmd = f"*#{who}*{addr}##"
            session._stream_writer.write(cmd.encode())
            await session._stream_writer.drain()
            
            try:
                # Rapid read with short timeout since gateway responds instantly on local network
                raw_response = await asyncio.wait_for(
                    session._stream_reader.readuntil(OWNSession.SEPARATOR),
                    timeout=0.15
                )
                resp = raw_response.decode()
                
                is_nack = "*#*0##" in resp
                is_ack = "*#*1##" in resp
                has_state = f"*{who}*" in resp
                
                # Consume extra frames until we get ACK or NACK
                while not (is_ack or is_nack):
                    try:
                        raw_response = await asyncio.wait_for(
                            session._stream_reader.readuntil(OWNSession.SEPARATOR),
                            timeout=0.05
                        )
                        resp = raw_response.decode()
                        if "*#*0##" in resp:
                            is_nack = True
                        if "*#*1##" in resp:
                            is_ack = True
                        if f"*{who}*" in resp:
                            has_state = True
                    except asyncio.TimeoutError:
                        break
                
                if not is_nack and (is_ack or has_state):
                    discovered.append(addr)
                    LOGGER.debug("Discovered device at WHO %s, WHERE %s", who, addr)
            except asyncio.TimeoutError:
                # Timeout means no device exists at this address
                continue
            except Exception as err:
                LOGGER.warning("Error scanning address %s: %s", addr, err)
                continue
    finally:
        await session.close()
        
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
                
                if isinstance(message, OWNMessage):
                    who = message.who
                    where = message.where
                    
                    if not who or not where:
                        continue
                        
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

async def async_discover_all_devices(gateway) -> Dict[str, Dict[str, dict]]:
    """Perform an active scan of all possible bus addresses for lights, covers, and climate."""
    discovered = {}
    
    # 1. Generate addresses 11 to 99 (no 0s in standard point-to-point)
    ptp_addresses = []
    for a in range(1, 10):
        for pl in range(1, 10):
            ptp_addresses.append(f"{a}{pl}")
            
    # 2. Climate zones 1 to 99
    climate_addresses = [str(z) for z in range(1, 100)]
    
    # Lights (WHO = 1)
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
            
    # Covers (WHO = 2)
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
            
    # Climate (WHO = 4)
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
            
    # Sound Diffusion (WHO = 22)
    LOGGER.info("Starting active bus scan for Sound Diffusion zones...")
    audio_zones = await async_scan_bus(gateway, "22", ptp_addresses)
    if audio_zones:
        discovered["media_player"] = {}
        for addr in audio_zones:
            dev_id = f"22-{addr}"
            discovered["media_player"][dev_id] = {
                "who": "22",
                "where": addr,
                "name": f"Sound Zone {addr}"
            }
            
    return discovered
