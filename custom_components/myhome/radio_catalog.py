"""Radio station catalog, geographic FM profiles, and logo resolver for MyHome Sound Diffusion (F500)."""
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

LOGGER = logging.getLogger(__name__)

# ── Profili Geografici Predefiniti Frequenze FM ──────────────────────────────
RADIO_PROFILES: dict[str, dict[float, str]] = {
    "piemonte_nord": {
        93.3: "Radio Deejay",
        91.1: "Radio Studio Più",
        99.5: "Radio 105",
        91.9: "Radio Nostalgia",
        101.5: "RDS",
        102.5: "RTL 102.5",
        105.8: "Radio Capital",
        103.3: "Rai Isoradio",
        98.7: "Virgin Radio",
        88.5: "Radio Monte Carlo",
        90.7: "Radio 24",
        94.2: "Radio Italia",
        95.7: "Rai Radio 1",
        98.2: "Rai Radio 2",
        90.1: "R101",
    },
    "lombardia_milano": {
        107.0: "Radio Deejay",
        99.1: "Radio 105",
        93.0: "Radio Studio Più",
        102.5: "RTL 102.5",
        101.5: "RDS",
        98.7: "Virgin Radio",
        105.8: "Radio Capital",
        104.8: "Radio 24",
        103.3: "Rai Isoradio",
        88.5: "Rai Radio 1",
        90.3: "Rai Radio 2",
        92.1: "Rai Radio 3",
        106.3: "Radio Italia",
        101.0: "R101",
    },
    "lazio_roma": {
        90.1: "Radio Deejay",
        96.1: "Radio 105",
        102.5: "RTL 102.5",
        103.0: "RDS",
        98.4: "Virgin Radio",
        95.8: "Radio Capital",
        103.3: "Rai Isoradio",
        87.6: "Rai Radio 1",
        91.7: "Rai Radio 2",
        93.7: "Rai Radio 3",
        97.7: "Radio Italia",
        101.0: "R101",
    },
    "veneto": {
        90.0: "Radio Deejay",
        98.4: "Radio 105",
        93.0: "Radio Studio Più",
        102.5: "RTL 102.5",
        101.5: "RDS",
        92.0: "Virgin Radio",
        103.3: "Rai Isoradio",
        88.3: "Rai Radio 1",
        101.0: "R101",
    },
    "emilia_romagna": {
        93.5: "Radio Deejay",
        96.0: "Radio 105",
        102.5: "RTL 102.5",
        101.5: "RDS",
        107.6: "Virgin Radio",
        103.3: "Rai Isoradio",
        101.0: "R101",
    },
    "toscana": {
        92.9: "Radio Deejay",
        98.9: "Radio 105",
        87.5: "Radio Nostalgia",
        102.5: "RTL 102.5",
        101.5: "RDS",
        103.3: "Rai Isoradio",
        101.0: "R101",
    },
    "campania_napoli": {
        97.0: "Radio Deejay",
        99.7: "Radio 105",
        102.5: "RTL 102.5",
        101.4: "RDS",
        103.0: "Radio Kiss Kiss",
        103.3: "Rai Isoradio",
        101.0: "R101",
    },
    "custom": {},
}

# ── Mappatura Loghi Ufficiali Utente (/local/loghi_radio/) ────────────────────
# Tutti i file risiedono nella cartella utente /config/www/loghi_radio/
# e sono serviti direttamente da Home Assistant con sfondo trasparente.
USER_LOGOS_PREFIX = "/local/loghi_radio"

USER_STATION_LOGOS: dict[str, str] = {
    # Radio Deejay
    "radio deejay": "RadioDeejay.png",
    "deejay": "RadioDeejay.png",

    # Radio 105
    "radio 105": "Radio105.png",
    "105": "Radio105.png",
    "105 fm": "Radio105.png",

    # Radio Studio Più / Party
    "radio studio più": "Studiopiu.png",
    "radio studio piu": "Studiopiu.png",
    "studio più": "Studiopiu.png",
    "studio piu": "Studiopiu.png",
    "studiopiu": "Studiopiu.png",
    "party": "Studiopiu.png",
    "party groove": "PartyGroove.png",

    # Radio Nostalgia
    "radio nostalgia": "RadioNostalgia.png",
    "nostalgia": "RadioNostalgia.png",
    "nostlgia": "RadioNostalgia.png",

    # RDS
    "rds": "RDS.png",
    "* rds *": "RDS.png",
    "rds 100% grandi successi": "RDS.png",

    # RTL 102.5
    "rtl 102.5": "RTL102.5.png",
    "rtl": "RTL102.5.png",

    # Virgin Radio
    "virgin radio": "Virgin.png",
    "virgin": "Virgin.png",

    # Radio Capital
    "radio capital": "RadioCapital.png",
    "capital": "RadioCapital.png",

    # Radio 24
    "radio 24": "Radio24.png",
    "radio24": "Radio24.png",

    # Radio Monte Carlo
    "radio monte carlo": "RadioMontecarlo.png",
    "radio montecarlo": "RadioMontecarlo.png",
    "monte carlo": "RadioMontecarlo.png",
    "rmc": "RadioMontecarlo.png",

    # Radio Italia
    "radio italia": "RadioItalia.png",
    "radioitalia": "RadioItalia.png",
    "radio italia solomusicaitaliana": "RadioItalia.png",
    "radio italia anni 60": "RadioItaliaAnni60.png",
    "radio italia network": "RadioItaliaNetwork.png",

    # R101
    "radio 101": "R101.png",
    "r101": "R101.png",
    "101": "R101.png",

    # Radio Kiss Kiss
    "radio kiss kiss": "RadioKissKiss.png",
    "kiss kiss": "RadioKissKiss.png",
    "kisskiss": "RadioKissKiss.png",
    "kiss kiss napoli": "KissKissNapoli.png",

    # Radio m2o
    "radio m2o": "m2o.png",
    "m2o": "m2o.png",

    # Radio Zeta
    "radio zeta": "RadioZeta.png",
    "radio z": "RadioZeta.png",

    # Radionorba
    "radio norba": "Radionorba.png",
    "radionorba": "Radionorba.png",

    # Rai
    "rai radio 1": "RaiRadio1.png",
    "radio 1": "RaiRadio1.png",
    "rai radio 2": "RaiRadio2.png",
    "radio 2": "RaiRadio2.png",
    "rai radio 3": "RaiRadio3.png",
    "radio 3": "RaiRadio3.png",
    "rai isoradio": "RaiIsoRadio.png",
    "isoradio": "RaiIsoRadio.png",
    "rai gr parlamento": "RaiGrParlamento.png",
    "gr parlamento": "GRParlamento.png",

    # Altre emittenti nazionali e locali presenti nella cartella
    "discoradio": "Discoradio.png",
    "radio subasio": "RadioSubasio.png",
    "subasio": "RadioSubasio.png",
    "radio suby": "RadioSuby.png",
    "radio bruno": "RadioBruno.png",
    "radio birikina": "RadioBirikina.png",
    "radio company": "RadioCompany.png",
    "radio bella monella": "RadioBellaMonella.png",
    "radio sportiva": "RadioSportiva.png",
    "radio margherita": "Radio-Margherita.png",
    "radio lattemiele": "RadioLatteMiele.png",
    "lattemiele": "RadioLatteMiele.png",
    "radio babboleo": "RadioBabboleo.png",
    "radio padova": "RadioPadova.png",
    "radio number one": "RadioNumberOne.png",
    "number one": "RadioNumberOne.png",
    "radio lombardia": "RadioLombardia.png",
    "radio marte": "RadioMarte.png",
    "radio globo": "RadioGlobo.png",
    "dimensione suono roma": "DimensioneSuonoRoma.png",
    "radio cuore": "RadioCuore.png",
    "radio fantastica": "RadioFantastica.png",
    "radio pico": "RadioPico.png",
    "radio stella": "RadioStella.png",
    "radio viva fm": "RadioVivaFM.png",
    "radio ibiza": "RadioIbiza.png",
    "easy network": "EasyNetwork.png",
    "lifegate radio": "LifeGateRadio.png",
    "radio reporter": "RadioReporter.png",
    "radio modena 90": "RadioModena90.png",
    "modena radio city": "ModenaRadioCity.png",
}


@dataclass
class StationInfo:
    """Station metadata resolved from frequency and catalog."""
    name: str              # Nome pulito della stazione (es. 'Radio Deejay')
    title: str             # Titolo affiancato (es. 'P1: 93.3 MHz - Radio Deejay' o '93.3 MHz - Radio Deejay')
    artist: str            # Dettagli riproduzione (es. 'Preset P1 • FM Stereo')
    frequency: str         # Frequenza nativa (es. '93.3 MHz')
    logo_url: Optional[str] = None
    is_known: bool = False
    preset_num: Optional[int] = None


class RadioCatalog:
    """Resolver for FM radio station names and logos based on frequency and zone options."""

    _cached_files_by_prefix: dict[str, dict[str, str]] = {}

    @classmethod
    def clear_cache(cls):
        """Clear cached file listings."""
        cls._cached_files_by_prefix.clear()

    @classmethod
    def parse_custom_frequencies(cls, text: str) -> dict[float, str]:
        """Parse user-provided custom frequencies in 'frequency: name' format."""
        mapping: dict[float, str] = {}
        if not text:
            return mapping
        for line in text.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            parts = line.split(":", 1)
            try:
                freq_val = float(parts[0].replace(",", ".").replace("MHz", "").strip())
                name_val = parts[1].strip()
                if name_val:
                    mapping[round(freq_val, 2)] = name_val
            except ValueError:
                continue
        return mapping

    @classmethod
    def format_custom_frequencies(cls, mapping: dict[float, str]) -> str:
        """Format frequency mapping dictionary back into multiline string for OptionsFlow."""
        lines = []
        for freq in sorted(mapping.keys()):
            lines.append(f"{freq:.1f}: {mapping[freq]}")
        return "\n".join(lines)

    @classmethod
    def find_station_name(
        cls,
        freq_mhz: float,
        zone_profile: str = "piemonte_nord",
        custom_mapping: Optional[dict[float, str]] = None,
    ) -> Optional[str]:
        """Find matching station name by frequency with tolerance (±0.05 MHz)."""
        # 1. Priorità massima: frequenze personalizzate definite dall'utente
        if custom_mapping:
            for f, name in custom_mapping.items():
                if abs(f - freq_mhz) <= 0.05:
                    return name

        # 2. Profilo geografico selezionato
        profile = RADIO_PROFILES.get(zone_profile, {})
        for f, name in profile.items():
            if abs(f - freq_mhz) <= 0.05:
                return name

        return None

    @classmethod
    def _get_available_user_logos(cls, hass=None, logos_prefix: str = USER_LOGOS_PREFIX) -> dict[str, str]:
        """Return map of normalized filename keys to actual filenames in the logos directory."""
        cache_key = logos_prefix or USER_LOGOS_PREFIX
        if cache_key in cls._cached_files_by_prefix:
            return cls._cached_files_by_prefix[cache_key]

        config_dir = getattr(hass.config, "config_dir", None) if hass and hasattr(hass, "config") else "/config"
        if not os.path.isdir(config_dir) and os.path.isdir("/Volumes/config"):
            config_dir = "/Volumes/config"

        # Deriva la cartella su disco dall'URL locale (es. /local/loghi_radio -> <config_dir>/www/loghi_radio)
        clean_prefix = cache_key.strip("/")
        if clean_prefix.startswith("local/"):
            subpath = clean_prefix[len("local/"):]
        elif clean_prefix == "local":
            subpath = ""
        else:
            subpath = clean_prefix
        logos_dir = os.path.join(config_dir, "www", subpath)

        res: dict[str, str] = {}
        if os.path.isdir(logos_dir):
            try:
                for f in os.listdir(logos_dir):
                    if f.endswith((".png", ".jpg", ".jpeg", ".svg")):
                        norm = re.sub(r"[^a-z0-9]", "", f.lower().rsplit(".", 1)[0])
                        res[norm] = f
            except Exception as err:
                LOGGER.debug("Error listing logos in %s: %s", logos_dir, err)

        cls._cached_files_by_prefix[cache_key] = res
        return res

    @classmethod
    def get_station_logo(
        cls,
        station_name: str,
        enable_logos: bool = True,
        logos_prefix: str = USER_LOGOS_PREFIX,
        hass=None,
    ) -> Optional[str]:
        """Resolve station logo file from local storage (default /local/loghi_radio/)."""
        if not enable_logos or not station_name:
            return None

        prefix = (logos_prefix or USER_LOGOS_PREFIX).rstrip("/")
        clean = station_name.strip().lower()

        # 1. Controllo mappatura esplicita
        if clean in USER_STATION_LOGOS:
            return f"{prefix}/{USER_STATION_LOGOS[clean]}"

        for key, fname in USER_STATION_LOGOS.items():
            if key in clean or clean in key:
                return f"{prefix}/{fname}"

        # 2. Corrispondenza automatica sui file reali in cartella loghi
        user_files = cls._get_available_user_logos(hass, logos_prefix=prefix)
        norm_name = re.sub(r"[^a-z0-9]", "", clean)

        if norm_name in user_files:
            return f"{prefix}/{user_files[norm_name]}"

        if norm_name.startswith("radio") and norm_name[5:] in user_files:
            return f"{prefix}/{user_files[norm_name[5:]]}"

        with_radio = f"radio{norm_name}"
        if with_radio in user_files:
            return f"{prefix}/{user_files[with_radio]}"

        for k, fname in user_files.items():
            if k and (k in norm_name or norm_name in k):
                return f"{prefix}/{fname}"

        return None

    @classmethod
    def resolve_station(
        cls,
        freq_str: Optional[str],
        preset_num: Optional[int] = None,
        zone_profile: str = "piemonte_nord",
        custom_mapping: Optional[dict[float, str]] = None,
        enable_logos: bool = True,
        logos_prefix: str = USER_LOGOS_PREFIX,
        hass=None,
    ) -> StationInfo:
        """Resolve station metadata, ensuring native frequency and station name are presented together."""
        p_prefix = f"P{preset_num}: " if preset_num else ""
        freq_mhz = None

        if freq_str:
            clean_str = freq_str.replace("MHz", "").strip()
            try:
                freq_mhz = float(clean_str)
            except ValueError:
                freq_mhz = None

        display_freq = f"{freq_mhz:.1f} MHz" if freq_mhz is not None else (freq_str or "")

        name = None
        if freq_mhz is not None:
            name = cls.find_station_name(freq_mhz, zone_profile, custom_mapping)

        if name:
            # Emittente riconosciuta: affianca frequenza nativa e nome stazione
            if display_freq:
                title = f"{p_prefix}{display_freq} - {name}"
            else:
                title = f"{p_prefix}{name}"

            if preset_num:
                artist = f"Preset P{preset_num} • FM Stereo"
            else:
                artist = "FM Stereo RDS"

            logo = cls.get_station_logo(name, enable_logos=enable_logos, logos_prefix=logos_prefix, hass=hass)

            return StationInfo(
                name=name,
                title=title,
                artist=artist,
                frequency=display_freq,
                logo_url=logo,
                is_known=True,
                preset_num=preset_num,
            )

        # Emittente non riconosciuta nel catalogo: mostra la frequenza nativa
        if display_freq:
            title = f"{p_prefix}{display_freq}"
        elif preset_num:
            title = f"Preset P{preset_num}"
        else:
            title = "Radio FM"

        if preset_num:
            artist = f"Preset P{preset_num} • FM Stereo RDS"
        else:
            artist = "FM Stereo RDS"

        return StationInfo(
            name=name or display_freq or "Radio FM",
            title=title,
            artist=artist,
            frequency=display_freq,
            logo_url=None,
            is_known=False,
            preset_num=preset_num,
        )
