"""
All static configuration: constants, paths, API roots, and location data.
"""

import json
import os

_DIR          = os.path.dirname(__file__)
_PROJECT_ROOT = os.path.abspath(os.path.join(_DIR, "..", ".."))

_CONFIG_PATH = os.path.join(_DIR, "config.json")
with open(_CONFIG_PATH) as _f:
    CONFIG = json.load(_f)

# Trading parameters
KELLY_FRACTION  = CONFIG["kelly_fraction"]
MAX_BET         = CONFIG["max_bet"]
MIN_EV          = CONFIG["min_ev"]
MIN_PRICE       = CONFIG.get("min_price", 0.03)
MAX_PRICE       = CONFIG["max_price"]
MIN_VOLUME      = CONFIG["min_volume"]
MIN_HOURS       = CONFIG["min_hours"]
MAX_HOURS       = CONFIG["max_hours"]
MAX_SLIPPAGE    = CONFIG["max_slippage"]
SCAN_INTERVAL   = CONFIG["scan_interval"]
CALIBRATION_MIN  = CONFIG["calibration_min"]
MAX_EV              = CONFIG.get("max_ev", 0.5)
MAX_POSITIONS       = CONFIG.get("max_positions", 10)
BIAS_SCALE          = CONFIG.get("bias_scale", 1.0)
INITIAL_BALANCE     = CONFIG["balance"]
MAX_FORECAST_SPREAD_F = CONFIG.get("max_forecast_spread_f", 4.0)
MAX_FORECAST_SPREAD_C = CONFIG.get("max_forecast_spread_c", 2.0)
CITY_BLACKLIST      = set(CONFIG.get("city_blacklist", []))

# Paths
DATA_DIR         = os.path.join(_PROJECT_ROOT, "data", "markets")
CALIBRATION_PATH = os.path.join(_PROJECT_ROOT, "data", "calibration.json")
STATE_PATH       = os.path.join(_PROJECT_ROOT, "data", "bot_state.json")
LOG_DIR          = os.path.join(_PROJECT_ROOT, "logs")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# API roots
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"


def _load_env() -> dict[str, str]:
    """Load credential keys from .env file at project root."""
    env_file = os.path.join(_PROJECT_ROOT, ".env")
    result: dict[str, str] = {}
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, val = line.partition("=")
                    result[key.strip()] = val.strip().strip("'\"")
    return result


_ENV = _load_env()

VC_KEY          = _ENV.get("VC_KEY", "")
TELEGRAM_TOKEN  = _ENV.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = _ENV.get("TELEGRAM_CHAT_ID", "")

# ---------------------------------------------------------------------------
# Location data — 20 cities across 4 continents.
# Coordinates point to airport ICAO stations, matching Polymarket's resolution
# source (Weather Underground).
# ---------------------------------------------------------------------------

LOCATIONS: dict[str, dict] = {
    # US — °F
    "nyc":           {"lat": 40.7772,  "lon":  -73.8726,  "name": "New York City", "station": "KLGA",  "unit": "F", "region": "us"},
    "chicago":       {"lat": 41.9742,  "lon":  -87.9073,  "name": "Chicago",       "station": "KORD",  "unit": "F", "region": "us"},
    "miami":         {"lat": 25.7959,  "lon":  -80.2870,  "name": "Miami",         "station": "KMIA",  "unit": "F", "region": "us"},
    "dallas":        {"lat": 32.8471,  "lon":  -96.8518,  "name": "Dallas",        "station": "KDAL",  "unit": "F", "region": "us"},
    "seattle":       {"lat": 47.4502,  "lon": -122.3088,  "name": "Seattle",       "station": "KSEA",  "unit": "F", "region": "us"},
    "atlanta":       {"lat": 33.6407,  "lon":  -84.4277,  "name": "Atlanta",       "station": "KATL",  "unit": "F", "region": "us"},
    "denver":        {"lat": 39.8561,  "lon": -104.6737,  "name": "Denver",        "station": "KDEN",  "unit": "F", "region": "us"},
    "los-angeles":   {"lat": 33.9425,  "lon": -118.4081,  "name": "Los Angeles",   "station": "KLAX",  "unit": "F", "region": "us"},
    "san-francisco": {"lat": 37.6213,  "lon": -122.3790,  "name": "San Francisco", "station": "KSFO",  "unit": "F", "region": "us"},
    "houston":       {"lat": 29.9902,  "lon":  -95.3368,  "name": "Houston",       "station": "KIAH",  "unit": "F", "region": "us"},
    "austin":        {"lat": 30.1975,  "lon":  -97.6664,  "name": "Austin",        "station": "KAUS",  "unit": "F", "region": "us"},
    # EU — °C
    "london":        {"lat": 51.5048,  "lon":    0.0495,  "name": "London",        "station": "EGLC",  "unit": "C", "region": "eu"},
    "paris":         {"lat": 48.9962,  "lon":    2.5979,  "name": "Paris",         "station": "LFPG",  "unit": "C", "region": "eu"},
    "munich":        {"lat": 48.3537,  "lon":   11.7750,  "name": "Munich",        "station": "EDDM",  "unit": "C", "region": "eu"},
    "ankara":        {"lat": 40.1281,  "lon":   32.9951,  "name": "Ankara",        "station": "LTAC",  "unit": "C", "region": "eu"},
    "warsaw":        {"lat": 52.1657,  "lon":   20.9671,  "name": "Warsaw",        "station": "EPWA",  "unit": "C", "region": "eu"},
    "madrid":        {"lat": 40.4936,  "lon":   -3.5668,  "name": "Madrid",        "station": "LEMD",  "unit": "C", "region": "eu"},
    "milan":         {"lat": 45.4654,  "lon":    9.2756,  "name": "Milan",         "station": "LIML",  "unit": "C", "region": "eu"},
    # Asia — °C
    "seoul":         {"lat": 37.4691,  "lon":  126.4505,  "name": "Seoul",         "station": "RKSI",  "unit": "C", "region": "asia"},
    "tokyo":         {"lat": 35.7647,  "lon":  140.3864,  "name": "Tokyo",         "station": "RJTT",  "unit": "C", "region": "asia"},
    "shanghai":      {"lat": 31.1443,  "lon":  121.8083,  "name": "Shanghai",      "station": "ZSPD",  "unit": "C", "region": "asia"},
    "singapore":     {"lat":  1.3502,  "lon":  103.9940,  "name": "Singapore",     "station": "WSSS",  "unit": "C", "region": "asia"},
    "lucknow":       {"lat": 26.7606,  "lon":   80.8893,  "name": "Lucknow",       "station": "VILK",  "unit": "C", "region": "asia"},
    "tel-aviv":      {"lat": 32.0114,  "lon":   34.8867,  "name": "Tel Aviv",      "station": "LLBG",  "unit": "C", "region": "asia"},
    "beijing":       {"lat": 40.0799,  "lon":  116.5853,  "name": "Beijing",       "station": "ZBAA",  "unit": "C", "region": "asia"},
    "wuhan":         {"lat": 30.7838,  "lon":  114.2081,  "name": "Wuhan",         "station": "ZHHH",  "unit": "C", "region": "asia"},
    "chongqing":     {"lat": 29.7192,  "lon":  106.6419,  "name": "Chongqing",     "station": "ZUCK",  "unit": "C", "region": "asia"},
    "chengdu":       {"lat": 30.5785,  "lon":  103.9470,  "name": "Chengdu",       "station": "ZUUU",  "unit": "C", "region": "asia"},
    "shenzhen":      {"lat": 22.6393,  "lon":  113.8107,  "name": "Shenzhen",      "station": "ZGSZ",  "unit": "C", "region": "asia"},
    "hong-kong":     {"lat": 22.3080,  "lon":  113.9185,  "name": "Hong Kong",     "station": "VHHH",  "unit": "C", "region": "asia"},
    "taipei":        {"lat": 25.0777,  "lon":  121.2325,  "name": "Taipei",        "station": "RCTP",  "unit": "C", "region": "asia"},
    # Americas — °C
    "toronto":       {"lat": 43.6772,  "lon":  -79.6306,  "name": "Toronto",       "station": "CYYZ",  "unit": "C", "region": "ca"},
    "sao-paulo":     {"lat": -23.4356, "lon":  -46.4731,  "name": "Sao Paulo",     "station": "SBGR",  "unit": "C", "region": "sa"},
    "buenos-aires":  {"lat": -34.8222, "lon":  -58.5358,  "name": "Buenos Aires",  "station": "SAEZ",  "unit": "C", "region": "sa"},
    # Oceania — °C
    "wellington":    {"lat": -41.3272, "lon":  174.8052,  "name": "Wellington",    "station": "NZWN",  "unit": "C", "region": "oc"},
}

TIMEZONES: dict[str, str] = {
    "nyc":           "America/New_York",
    "chicago":       "America/Chicago",
    "miami":         "America/New_York",
    "dallas":        "America/Chicago",
    "seattle":       "America/Los_Angeles",
    "atlanta":       "America/New_York",
    "denver":        "America/Denver",
    "los-angeles":   "America/Los_Angeles",
    "san-francisco": "America/Los_Angeles",
    "houston":       "America/Chicago",
    "austin":        "America/Chicago",
    "london":        "Europe/London",
    "paris":         "Europe/Paris",
    "munich":        "Europe/Berlin",
    "ankara":        "Europe/Istanbul",
    "warsaw":        "Europe/Warsaw",
    "madrid":        "Europe/Madrid",
    "milan":         "Europe/Rome",
    "seoul":         "Asia/Seoul",
    "tokyo":         "Asia/Tokyo",
    "shanghai":      "Asia/Shanghai",
    "singapore":     "Asia/Singapore",
    "lucknow":       "Asia/Kolkata",
    "tel-aviv":      "Asia/Jerusalem",
    "beijing":       "Asia/Shanghai",
    "wuhan":         "Asia/Shanghai",
    "chongqing":     "Asia/Shanghai",
    "chengdu":       "Asia/Shanghai",
    "shenzhen":      "Asia/Shanghai",
    "hong-kong":     "Asia/Hong_Kong",
    "taipei":        "Asia/Taipei",
    "toronto":       "America/Toronto",
    "sao-paulo":     "America/Sao_Paulo",
    "buenos-aires":  "America/Argentina/Buenos_Aires",
    "wellington":    "Pacific/Auckland",
}
