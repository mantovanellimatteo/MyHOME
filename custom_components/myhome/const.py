"""Constants for the MyHome component."""
import logging

LOGGER = logging.getLogger(__package__)
DOMAIN = "myhome"

ATTR_GATEWAY = "gateway"
ATTR_MESSAGE = "message"

EVENT_MYHOME_INTERCOM = "myhome_intercom_event"

CONF = "config"
CONF_ENTITY = "entity"
CONF_ENTITIES = "entities"
CONF_ENTITY_NAME = "entity_name"
CONF_ICON = "icon"
CONF_ICON_ON = "icon_on"
CONF_PLATFORMS = "platforms"
CONF_ADDRESS = "address"
CONF_OWN_PASSWORD = "password"
CONF_FIRMWARE = "firmware"
CONF_SSDP_LOCATION = "ssdp_location"
CONF_SSDP_ST = "ssdp_st"
CONF_DEVICE_TYPE = "deviceType"
CONF_DEVICE_MODEL = "model"
CONF_MANUFACTURER = "manufacturer"
CONF_MANUFACTURER_URL = "manufacturerURL"
CONF_UDN = "UDN"
CONF_WORKER_COUNT = "command_worker_count"
CONF_FILE_PATH = "config_file_path"
CONF_GENERATE_EVENTS = "generate_events"
CONF_PARENT_ID = "parent_id"
CONF_WHO = "who"
CONF_WHERE = "where"
CONF_BUS_INTERFACE = "interface"
CONF_ZONE = "zone"
CONF_DIMMABLE = "dimmable"
CONF_GATEWAY = "gateway"
CONF_DEVICE_CLASS = "class"
CONF_INVERTED = "inverted"
CONF_ADVANCED_SHUTTER = "advanced"
CONF_HEATING_SUPPORT = "heat"
CONF_COOLING_SUPPORT = "cool"
CONF_FAN_SUPPORT = "fan"
CONF_STANDALONE = "standalone"
CONF_CENTRAL = "central"
CONF_SHORT_PRESS = "pushbutton_short_press"
CONF_SHORT_RELEASE = "pushbutton_short_release"
CONF_LONG_PRESS = "pushbutton_long_press"
CONF_LONG_RELEASE = "pushbutton_long_release"
CONF_TRAVEL_TIME = "travel_time"
CONF_OPEN_TIME = "open_time"
CONF_CLOSE_TIME = "close_time"
DEFAULT_TRAVEL_TIME = 25.0

# ── Decoder pool (Dynamic Proxy for Music Assistant / Spotify) ──────────────
# Up to 4 decoder slots, one per BTicino physical source input.
# Keys follow the pattern: decoder_{n}_{field}, n = 1..4
CONF_DECODER_ENTITY = "decoder_{}_entity"     # HA media_player entity_id
CONF_DECODER_SOURCE = "decoder_{}_source"     # BTicino source number (int 1-4)
CONF_DECODER_PRE_GAIN = "decoder_{}_pre_gain" # Volume offset % added to decoder (0-50)
CONF_DECODER_SLOTS = 4                        # Maximum number of decoder slots
CONF_DECODER_MODE = "decoder_mode"            # Shared bus source vs exclusive matrix channels
DECODER_MODE_SHARED = "shared"                # Multiple rooms can share the same decoder/source simultaneously
DECODER_MODE_EXCLUSIVE = "exclusive"          # Strict 1-to-1 decoder allocation per room

# ── Radio FM Catalog (F500 Tuner) ──────────────────────────────────────────
CONF_RADIO_ZONE_PROFILE = "radio_zone_profile"
CONF_RADIO_CUSTOM_FREQUENCIES = "radio_custom_frequencies"
CONF_RADIO_ENABLE_LOGOS = "radio_enable_logos"
CONF_RADIO_LOGOS_PATH = "radio_logos_path"
DEFAULT_RADIO_ZONE_PROFILE = "piemonte_nord"
DEFAULT_RADIO_ENABLE_LOGOS = True
DEFAULT_RADIO_LOGOS_PATH = "/local/loghi_radio"


