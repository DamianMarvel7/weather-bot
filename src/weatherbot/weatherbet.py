"""
Self-Calibrating Polymarket Weather Bot
Fetches weather forecasts from multiple sources, finds underpriced markets,
and paper-trades using Expected Value + Kelly Criterion sizing.
"""

import glob
import json
import math
import os
import re
import sys
import time
import threading
import requests
from datetime import datetime, timedelta, timezone

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

with open(CONFIG_PATH) as f:
    CONFIG = json.load(f)

KELLY_FRACTION   = CONFIG["kelly_fraction"]
MAX_BET          = CONFIG["max_bet"]
MIN_EV           = CONFIG["min_ev"]
MIN_PRICE        = CONFIG.get("min_price", 0.03)
MAX_PRICE        = CONFIG["max_price"]
MIN_VOLUME       = CONFIG["min_volume"]
MIN_HOURS        = CONFIG["min_hours"]
MAX_HOURS        = CONFIG["max_hours"]
MAX_SLIPPAGE     = CONFIG["max_slippage"]
SCAN_INTERVAL    = CONFIG["scan_interval"]
CALIBRATION_MIN  = CONFIG["calibration_min"]

# Paths
_PROJECT_ROOT    = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_DIR         = os.path.join(_PROJECT_ROOT, "data", "markets")
CALIBRATION_PATH = os.path.join(_PROJECT_ROOT, "data", "calibration.json")
STATE_PATH       = os.path.join(_PROJECT_ROOT, "data", "bot_state.json")
os.makedirs(DATA_DIR, exist_ok=True)

# Load VC_KEY from .env file if present, fall back to config, then empty string
def _load_vc_key() -> str:
    env_file = os.path.join(_PROJECT_ROOT, ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line.startswith("VC_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    return CONFIG.get("vc_key", "")

VC_KEY = _load_vc_key()

# API roots
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

# ---------------------------------------------------------------------------
# Locations — 20 cities across 4 continents
# Coordinates point to airport ICAO stations, matching Polymarket's resolution
# source (Weather Underground).
# ---------------------------------------------------------------------------

LOCATIONS = {
    # US — °F
    "nyc":          {"lat": 40.7772,  "lon":  -73.8726, "name": "New York City", "station": "KLGA",  "unit": "F", "region": "us"},
    "chicago":      {"lat": 41.9742,  "lon":  -87.9073, "name": "Chicago",       "station": "KORD",  "unit": "F", "region": "us"},
    "miami":        {"lat": 25.7959,  "lon":  -80.2870, "name": "Miami",         "station": "KMIA",  "unit": "F", "region": "us"},
    "dallas":       {"lat": 32.8471,  "lon":  -96.8518, "name": "Dallas",        "station": "KDAL",  "unit": "F", "region": "us"},
    "seattle":      {"lat": 47.4502,  "lon": -122.3088, "name": "Seattle",       "station": "KSEA",  "unit": "F", "region": "us"},
    "atlanta":      {"lat": 33.6407,  "lon":  -84.4277, "name": "Atlanta",       "station": "KATL",  "unit": "F", "region": "us"},
    # EU — °C
    "london":       {"lat": 51.5048,  "lon":    0.0495, "name": "London",        "station": "EGLC",  "unit": "C", "region": "eu"},
    "paris":        {"lat": 48.9962,  "lon":    2.5979, "name": "Paris",         "station": "LFPG",  "unit": "C", "region": "eu"},
    "munich":       {"lat": 48.3537,  "lon":   11.7750, "name": "Munich",        "station": "EDDM",  "unit": "C", "region": "eu"},
    "ankara":       {"lat": 40.1281,  "lon":   32.9951, "name": "Ankara",        "station": "LTAC",  "unit": "C", "region": "eu"},
    # Asia — °C
    "seoul":        {"lat": 37.4691,  "lon":  126.4505, "name": "Seoul",         "station": "RKSI",  "unit": "C", "region": "asia"},
    "tokyo":        {"lat": 35.7647,  "lon":  140.3864, "name": "Tokyo",         "station": "RJTT",  "unit": "C", "region": "asia"},
    "shanghai":     {"lat": 31.1443,  "lon":  121.8083, "name": "Shanghai",      "station": "ZSPD",  "unit": "C", "region": "asia"},
    "singapore":    {"lat":  1.3502,  "lon":  103.9940, "name": "Singapore",     "station": "WSSS",  "unit": "C", "region": "asia"},
    "lucknow":      {"lat": 26.7606,  "lon":   80.8893, "name": "Lucknow",       "station": "VILK",  "unit": "C", "region": "asia"},
    "tel-aviv":     {"lat": 32.0114,  "lon":   34.8867, "name": "Tel Aviv",      "station": "LLBG",  "unit": "C", "region": "asia"},
    # Americas — °C
    "toronto":      {"lat": 43.6772,  "lon":  -79.6306, "name": "Toronto",       "station": "CYYZ",  "unit": "C", "region": "ca"},
    "sao-paulo":    {"lat": -23.4356, "lon":  -46.4731, "name": "Sao Paulo",     "station": "SBGR",  "unit": "C", "region": "sa"},
    "buenos-aires": {"lat": -34.8222, "lon":  -58.5358, "name": "Buenos Aires",  "station": "SAEZ",  "unit": "C", "region": "sa"},
    # Oceania — °C
    "wellington":   {"lat": -41.3272, "lon":  174.8052, "name": "Wellington",    "station": "NZWN",  "unit": "C", "region": "oc"},
}

TIMEZONES = {
    "nyc":          "America/New_York",
    "chicago":      "America/Chicago",
    "miami":        "America/New_York",
    "dallas":       "America/Chicago",
    "seattle":      "America/Los_Angeles",
    "atlanta":      "America/New_York",
    "london":       "Europe/London",
    "paris":        "Europe/Paris",
    "munich":       "Europe/Berlin",
    "ankara":       "Europe/Istanbul",
    "seoul":        "Asia/Seoul",
    "tokyo":        "Asia/Tokyo",
    "shanghai":     "Asia/Shanghai",
    "singapore":    "Asia/Singapore",
    "lucknow":      "Asia/Kolkata",
    "tel-aviv":     "Asia/Jerusalem",
    "toronto":      "America/Toronto",
    "sao-paulo":    "America/Sao_Paulo",
    "buenos-aires": "America/Argentina/Buenos_Aires",
    "wellington":   "Pacific/Auckland",
}

# ---------------------------------------------------------------------------
# Part 3: Forecast sources
# ---------------------------------------------------------------------------

def get_ecmwf(city_slug: str, dates: list) -> dict:
    """ECMWF IFS 0.25° via Open-Meteo — global, bias-corrected, free."""
    loc = LOCATIONS[city_slug]
    temp_unit = "fahrenheit" if loc["unit"] == "F" else "celsius"
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&daily=temperature_2m_max&temperature_unit={temp_unit}"
        f"&forecast_days=7&timezone={TIMEZONES.get(city_slug, 'UTC')}"
        f"&models=ecmwf_ifs025&bias_correction=true"
    )
    try:
        data = requests.get(url, timeout=(5, 8)).json()
        daily = data.get("daily", {})
        result = dict(zip(daily.get("time", []), daily.get("temperature_2m_max", [])))
        return {d: result.get(d) for d in dates}
    except Exception:
        return {d: None for d in dates}


def get_hrrr(city_slug: str, dates: list) -> dict:
    """GFS Seamless (HRRR-blended) via Open-Meteo — US only, D+0/D+1."""
    if LOCATIONS[city_slug]["region"] != "us":
        return {}
    loc = LOCATIONS[city_slug]
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&daily=temperature_2m_max&temperature_unit=fahrenheit"
        f"&forecast_days=3&timezone={TIMEZONES.get(city_slug, 'UTC')}"
        f"&models=gfs_seamless"
    )
    try:
        data = requests.get(url, timeout=(5, 8)).json()
        daily = data.get("daily", {})
        result = dict(zip(daily.get("time", []), daily.get("temperature_2m_max", [])))
        return {d: result.get(d) for d in dates}
    except Exception:
        return {}


def get_metar(city_slug: str) -> float | None:
    """Live ICAO station observation — same station Polymarket uses for resolution."""
    loc = LOCATIONS[city_slug]
    url = f"https://aviationweather.gov/api/data/metar?ids={loc['station']}&format=json"
    try:
        data = requests.get(url, timeout=(5, 8)).json()
        if not data:
            return None
        temp_c = data[0].get("temp")
        if temp_c is None:
            return None
        return round(temp_c * 9 / 5 + 32, 1) if loc["unit"] == "F" else round(float(temp_c), 1)
    except Exception:
        return None


def get_best_forecast(city_slug: str, date_str: str, hours_ahead: float) -> dict:
    """
    Return best forecast for a single date.
    HRRR (US ≤48h) > ECMWF > None.
    METAR fetched for D+0 but doesn't drive entry decisions.
    """
    ecmwf_map = get_ecmwf(city_slug, [date_str])
    ecmwf = ecmwf_map.get(date_str)

    hrrr = None
    if LOCATIONS[city_slug]["region"] == "us" and hours_ahead <= 48:
        hrrr_map = get_hrrr(city_slug, [date_str])
        hrrr = hrrr_map.get(date_str)

    metar = get_metar(city_slug) if hours_ahead <= 24 else None

    if hrrr is not None:
        best, best_source = hrrr, "hrrr"
    elif ecmwf is not None:
        best, best_source = ecmwf, "ecmwf"
    else:
        best, best_source = None, None

    return {"ecmwf": ecmwf, "hrrr": hrrr, "metar": metar,
            "best": best, "best_source": best_source}

# ---------------------------------------------------------------------------
# Part 4: Expected Value and Kelly Criterion
# ---------------------------------------------------------------------------

def calc_ev(p: float, price: float) -> float:
    """EV = p × (1/price − 1) − (1 − p). Positive = edge exists."""
    if price <= 0 or price >= 1:
        return 0.0
    return round(p * (1.0 / price - 1.0) - (1.0 - p), 4)


def calc_kelly(p: float, price: float) -> float:
    """Fractional Kelly bet size as fraction of balance. Clamped to [0, 1]."""
    if price <= 0 or price >= 1:
        return 0.0
    b = 1.0 / price - 1.0
    f = (p * b - (1.0 - p)) / b
    return min(max(0.0, f) * KELLY_FRACTION, 1.0)

# ---------------------------------------------------------------------------
# Part 5: Data storage — one JSON file per market
# ---------------------------------------------------------------------------

def _market_path(city_slug: str, date_str: str) -> str:
    return os.path.join(DATA_DIR, f"{city_slug}_{date_str}.json")


def load_market(city_slug: str, date_str: str) -> dict | None:
    path = _market_path(city_slug, date_str)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def save_market(market: dict) -> None:
    path = _market_path(market["city"], market["date"])
    with open(path, "w") as f:
        json.dump(market, f, indent=2)


def new_market(city_slug: str, date_str: str, event_title: str, hours: float) -> dict:
    loc = LOCATIONS[city_slug]
    return {
        "city":               city_slug,
        "city_name":          loc["name"],
        "date":               date_str,
        "event":              event_title,
        "status":             "open",
        "position":           None,
        "actual_temp":        None,
        "resolved_outcome":   None,
        "pnl":                None,
        "forecast_snapshots": [],
        "market_snapshots":   [],
        "all_outcomes":       [],
        "created_at":         datetime.now(timezone.utc).isoformat(),
    }


def append_forecast_snapshot(market: dict, hours_left: float, forecast: dict) -> None:
    if hours_left <= 24:
        horizon = "D+0"
    elif hours_left <= 48:
        horizon = "D+1"
    else:
        horizon = f"D+{int(hours_left // 24)}"
    market["forecast_snapshots"].append({
        "ts":          _now_iso(),
        "horizon":     horizon,
        "hours_left":  round(hours_left, 1),
        "ecmwf":       forecast.get("ecmwf"),
        "hrrr":        forecast.get("hrrr"),
        "metar":       forecast.get("metar"),
        "best":        forecast.get("best"),
        "best_source": forecast.get("best_source"),
    })


def append_market_snapshot(market: dict, hours_left: float,
                           bucket: str, bid: float, ask: float) -> None:
    market["market_snapshots"].append({
        "ts":         _now_iso(),
        "hours_left": round(hours_left, 1),
        "bucket":     bucket,
        "bid":        bid,
        "ask":        ask,
    })


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ---------------------------------------------------------------------------
# Part 6: Polymarket API helpers
# ---------------------------------------------------------------------------

def _gamma_get(path: str, params: dict = None) -> list | dict | None:
    """GET from Gamma API with basic error handling."""
    try:
        resp = requests.get(f"{GAMMA_API}{path}", params=params, timeout=(5, 10))
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def get_clob_prices(clob_token_id: str) -> tuple[float | None, float | None]:
    """
    Return (best_bid, best_ask) from the CLOB orderbook for a token.
    We enter at ask, monitor/exit at bid — honest simulation.
    """
    try:
        resp = requests.get(f"{CLOB_API}/books",
                            params={"token_id": clob_token_id}, timeout=(5, 8))
        resp.raise_for_status()
        book = resp.json()
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        best_bid = float(bids[0]["price"]) if bids else None
        best_ask = float(asks[0]["price"]) if asks else None
        return best_bid, best_ask
    except Exception:
        return None, None


def parse_bucket_bounds(label: str) -> tuple[float, float]:
    """
    Parse a Polymarket outcome label into (lo, hi) numeric bounds.

    Handles formats observed on Polymarket weather markets:
      "56-57°F"        →  (56, 57)
      "58°F or higher" →  (58, 999)
      "33°C or below"  →  (-999, 33)
      "34°C"           →  (33.5, 34.5)   single value — ±0.5 window
      "40 to 45"       →  (40, 45)
    """
    label = label.strip()
    # Range: "56-57°F" or "40°F-45°F" or "40 to 45"
    m = re.search(r"(-?\d+\.?\d*)\s*(?:°[FC])?\s*(?:-|to)\s*(-?\d+\.?\d*)", label)
    if m:
        return float(m.group(1)), float(m.group(2))
    # "X or higher" / "X or above" / "above X"
    m = re.search(r"(-?\d+\.?\d*)\s*(?:°[FC])?\s*or\s+(?:higher|above)", label, re.I)
    if m:
        return float(m.group(1)), 999.0
    m = re.search(r"(?:above|over)\s*(-?\d+\.?\d*)", label, re.I)
    if m:
        return float(m.group(1)), 999.0
    # "X or lower" / "X or below" / "below X"
    m = re.search(r"(-?\d+\.?\d*)\s*(?:°[FC])?\s*or\s+(?:lower|below)", label, re.I)
    if m:
        return -999.0, float(m.group(1))
    m = re.search(r"(?:below|under)\s*(-?\d+\.?\d*)", label, re.I)
    if m:
        return -999.0, float(m.group(1))
    # Single value: "34°C" or "17°C" — treat as ±0.5 window
    m = re.search(r"^(-?\d+\.?\d*)\s*(?:°[FC])?$", label)
    if m:
        v = float(m.group(1))
        return v - 0.5, v + 0.5
    return -999.0, 999.0


def find_matching_bucket(outcomes: list, temp: float) -> dict | None:
    """
    Given a list of {label, lo, hi, bid, ask, token_id} dicts and a forecast
    temperature, return the outcome whose range contains temp.
    """
    for o in outcomes:
        if o["lo"] <= temp <= o["hi"]:
            return o
    return None


def get_polymarket_event(city_slug: str, date_str: str) -> dict | None:
    """
    Fetch active temperature event for city + date from Polymarket.

    Architecture: one Event → N child binary markets (one per temperature bucket).
    Each child market question encodes the bucket, e.g.:
      "Will the highest temperature in Atlanta be 73°F or below on March 27?"

    Uses /events endpoint with tag_slug=temperature and closed=false.
    Returns {title, hours_left, volume, outcomes:[{label, lo, hi, bid, ask, token_id}]}
    """
    city_name = LOCATIONS[city_slug]["name"]
    dt        = datetime.strptime(date_str, "%Y-%m-%d")
    # Polymarket event titles: "Highest temperature in Atlanta on March 27?"
    date_frag = f"{dt.strftime('%B')} {dt.day}"   # "March 27"

    events = _gamma_get("/events", params={
        "active":    "true",
        "closed":    "false",
        "tag_slug":  "temperature",
        "limit":     200,
        "order":     "createdAt",
        "ascending": "false",
    })
    if not events:
        return None

    # Find the event for this city + date
    match = None
    for ev in events:
        title = ev.get("title", "")
        if city_name.lower() not in title.lower():
            continue
        if date_frag not in title:
            continue
        match = ev
        break

    if match is None:
        return None

    # Check hours left
    end_dt_str = match.get("endDate") or ""
    try:
        end_dt     = datetime.fromisoformat(end_dt_str.replace("Z", "+00:00"))
        hours_left = (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
    except Exception:
        hours_left = 999.0

    if not (MIN_HOURS <= hours_left <= MAX_HOURS):
        return None

    # Build outcomes from child markets — each child IS one bucket (binary YES/NO)
    child_markets = match.get("markets", [])
    outcomes      = []
    total_volume  = 0.0

    for child in child_markets:
        question = child.get("question", "")
        # Extract bucket label from question:
        # "Will the highest temperature in Atlanta be 73°F or below on March 27?"
        # → "73°F or below"
        m = re.search(r"\bbe\s+(.+?)\s+on\s+\w+\s+\d+", question, re.I)
        label = m.group(1) if m else question
        lo, hi = parse_bucket_bounds(label)

        # YES token is index 0 in clobTokenIds
        raw_ids  = child.get("clobTokenIds", "[]")
        try:
            clob_ids = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
        except Exception:
            clob_ids = []
        token_id = clob_ids[0] if clob_ids else None

        # Price from CLOB; fallback to outcomePrices[0] (YES price)
        bid, ask = (None, None)
        if token_id:
            bid, ask = get_clob_prices(token_id)
        if ask is None:
            raw_prices = child.get("outcomePrices", "[]")
            try:
                prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
                ask = bid = float(prices[0]) if prices else None
            except Exception:
                pass

        vol = float(child.get("volume") or 0)
        total_volume += vol

        outcomes.append({
            "label":    label,
            "lo":       lo,
            "hi":       hi,
            "bid":      bid,
            "ask":      ask,
            "token_id": token_id,
            "volume":   vol,
        })

    if total_volume < MIN_VOLUME:
        return None

    return {
        "title":      match.get("title", ""),
        "event_id":   match.get("id"),
        "hours_left": hours_left,
        "volume":     total_volume,
        "outcomes":   outcomes,
    }

# ---------------------------------------------------------------------------
# Part 6: Position management
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"balance": CONFIG["balance"]}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def open_position(market: dict, outcome: dict, size_dollars: float,
                  ev: float, kelly: float, state: dict) -> None:
    """Record a new YES position. Entry is at ask price."""
    ask = outcome["ask"]
    market["position"] = {
        "bucket":     outcome["label"],
        "token_id":   outcome["token_id"],
        "entry_ask":  ask,
        "peak_bid":   ask,          # for trailing stop tracking
        "size":       round(size_dollars, 2),
        "ev":         ev,
        "kelly":      kelly,
        "opened_at":  _now_iso(),
        "close_reason": None,
    }
    state["balance"] = round(state["balance"] - size_dollars, 2)
    print(f"  OPEN  {market['city']} {market['date']} | "
          f"bucket={outcome['label']} ask={ask:.3f} "
          f"size=${size_dollars:.2f} ev={ev:.4f}")


def close_position(market: dict, bid_price: float, reason: str, state: dict) -> None:
    """Close open position at current bid. Realise P&L."""
    pos = market["position"]
    if pos is None:
        return
    size       = pos["size"]
    entry_ask  = pos["entry_ask"]
    proceeds   = size / entry_ask * bid_price   # shares × current bid
    pnl        = round(proceeds - size, 2)
    state["balance"] = round(state["balance"] + proceeds, 2)
    pos["close_reason"] = reason
    pos["close_bid"]    = bid_price
    pos["closed_at"]    = _now_iso()
    market["pnl"]       = pnl
    print(f"  CLOSE {market['city']} {market['date']} | "
          f"reason={reason} bid={bid_price:.3f} pnl=${pnl:+.2f}")


def check_stops(market: dict, current_bid: float, forecast_temp: float | None) -> str | None:
    """
    Return stop reason or None.

    Stop conditions (in priority order):
      stop_loss      — bid dropped ≥20% below entry ask
      trailing_stop  — bid reached +20% then fell back to ≤ entry ask
      forecast_change — forecast moved outside bought bucket (±2°F/1°C buffer)
    """
    pos = market["position"]
    if pos is None:
        return None

    entry  = pos["entry_ask"]
    peak   = pos.get("peak_bid", entry)
    bucket = pos["bucket"]
    unit   = LOCATIONS[market["city"]]["unit"]
    drift_buffer = 2.0 if unit == "F" else 1.0

    # Keep peak updated
    if current_bid > peak:
        pos["peak_bid"] = current_bid

    if current_bid <= entry * 0.80:
        return "stop_loss"

    if peak >= entry * 1.20 and current_bid <= entry:
        return "trailing_stop"

    if forecast_temp is not None:
        lo, hi = parse_bucket_bounds(bucket)
        if lo != -999.0:
            lo -= drift_buffer
        if hi != 999.0:
            hi += drift_buffer
        if not (lo <= forecast_temp <= hi):
            return "forecast_change"

    return None

# ---------------------------------------------------------------------------
# Part 6: Auto-resolution
# ---------------------------------------------------------------------------

def get_actual_temp_vc(city_slug: str, date_str: str) -> float | None:
    """
    Fetch actual max temperature from Visual Crossing historical API.
    Called after a market resolves to compare with forecast for calibration.
    """
    if VC_KEY == "YOUR_KEY_HERE":
        return None
    loc  = LOCATIONS[city_slug]
    unit = "us" if loc["unit"] == "F" else "metric"
    url  = (
        f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services"
        f"/timeline/{loc['lat']},{loc['lon']}/{date_str}"
        f"?unitGroup={unit}&include=days&key={VC_KEY}&contentType=json"
    )
    try:
        data = requests.get(url, timeout=(5, 10)).json()
        days = data.get("days", [])
        if not days:
            return None
        return days[0].get("tempmax")
    except Exception:
        return None


def auto_resolve(market: dict, state: dict) -> bool:
    """
    Check Polymarket for resolution of an open market.
    Determination:
      YES price >= 0.95  → that outcome WON
      YES price <= 0.05  → that outcome LOST

    Returns True if the market was resolved.
    """
    if market["status"] != "open":
        return False

    resolved_bucket = None
    for outcome in market.get("all_outcomes", []):
        token_id = outcome.get("token_id")
        if not token_id:
            continue
        bid, ask = get_clob_prices(token_id)
        if bid is None:
            continue
        mid = (bid + ask) / 2 if ask else bid
        if mid >= 0.95:
            resolved_bucket = outcome["label"]
            break

    if resolved_bucket is None:
        return False

    market["status"]           = "resolved"
    market["resolved_outcome"] = resolved_bucket
    market["actual_temp"]      = get_actual_temp_vc(market["city"], market["date"])

    # Close any open position
    pos = market.get("position")
    if pos and pos.get("close_reason") is None:
        token_id  = pos.get("token_id")
        final_bid = 0.99 if resolved_bucket == pos["bucket"] else 0.01
        close_position(market, final_bid, "resolved", state)

    print(f"  RESOLVED {market['city']} {market['date']} → {resolved_bucket} "
          f"actual={market['actual_temp']}")
    return True


def auto_resolve_all(state: dict) -> None:
    """Scan all open market files and attempt resolution."""
    for path in glob.glob(os.path.join(DATA_DIR, "*.json")):
        with open(path) as f:
            mkt = json.load(f)
        if mkt.get("status") == "open":
            if auto_resolve(mkt, state):
                save_market(mkt)

# ---------------------------------------------------------------------------
# Part 7: Calibration — MAE → sigma → normal-distribution probability
# ---------------------------------------------------------------------------

def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    """Standard normal CDF using math.erf."""
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2))))


def bucket_probability(lo: float, hi: float, mu: float, sigma: float) -> float:
    """
    P(lo ≤ actual ≤ hi) assuming actual ~ N(mu, sigma).
    Clamps open-ended buckets (lo=-999 / hi=999) at ±15 sigma.
    """
    lo_clamp = mu - 15 * sigma if lo == -999.0 else lo
    hi_clamp = mu + 15 * sigma if hi == 999.0 else hi
    return max(0.0, min(1.0, _normal_cdf(hi_clamp, mu, sigma) - _normal_cdf(lo_clamp, mu, sigma)))


def run_calibration() -> dict:
    """
    Compute mean absolute error per (city, source) from all resolved markets
    that have actual_temp recorded. Saves to data/calibration.json.

    Returns the calibration dict: {"{city}_{source}": {"mae": x, "n": n}}
    """
    all_markets = []
    for path in glob.glob(os.path.join(DATA_DIR, "*.json")):
        with open(path) as f:
            all_markets.append(json.load(f))

    resolved = [m for m in all_markets
                if m.get("status") == "resolved" and m.get("actual_temp") is not None]

    calib: dict[str, dict] = {}
    for source in ("ecmwf", "hrrr"):
        for city_slug in LOCATIONS:
            city_markets = [m for m in resolved if m["city"] == city_slug]
            errors = []
            for mkt in city_markets:
                actual = mkt["actual_temp"]
                # Use the forecast snapshot closest to D+0 (smallest hours_left)
                snaps = [s for s in mkt["forecast_snapshots"] if s.get(source) is not None]
                if not snaps:
                    continue
                closest = min(snaps, key=lambda s: s["hours_left"])
                predicted = closest[source]
                errors.append(abs(predicted - actual))

            if len(errors) >= CALIBRATION_MIN:
                mae = sum(errors) / len(errors)
                calib[f"{city_slug}_{source}"] = {"mae": round(mae, 3), "n": len(errors)}

    os.makedirs(os.path.dirname(CALIBRATION_PATH), exist_ok=True)
    with open(CALIBRATION_PATH, "w") as f:
        json.dump(calib, f, indent=2)

    print(f"[calibration] {len(calib)} entries written to {CALIBRATION_PATH}")
    return calib


def load_calibration() -> dict:
    if os.path.exists(CALIBRATION_PATH):
        with open(CALIBRATION_PATH) as f:
            return json.load(f)
    return {}


def get_sigma(city_slug: str, source: str, calibration: dict) -> float:
    """
    Return calibrated sigma (MAE) for city/source, or a conservative default
    (3°F / 1.5°C) until enough data is collected.
    """
    unit    = LOCATIONS[city_slug]["unit"]
    default = 3.0 if unit == "F" else 1.5
    entry   = calibration.get(f"{city_slug}_{source}")
    return entry["mae"] if entry else default


def get_probability(city_slug: str, bucket_lo: float, bucket_hi: float,
                    forecast_temp: float, best_source: str,
                    calibration: dict) -> float:
    """
    Return P(actual in bucket) using calibrated normal distribution.
    Falls back to p=1.0 when forecast_temp is exactly in the bucket and
    calibration data is unavailable (pre-calibration behaviour matches Part 4).
    """
    sigma = get_sigma(city_slug, best_source, calibration)
    return bucket_probability(bucket_lo, bucket_hi, forecast_temp, sigma)

# ---------------------------------------------------------------------------
# Part 6: Main loop
# ---------------------------------------------------------------------------

def _scan_dates() -> list:
    """Return date strings for today and the next 2 days (UTC)."""
    today = datetime.now(timezone.utc).date()
    return [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(3)]


def scan_and_update() -> None:
    """
    Full hourly scan.
    For each city × date: fetch forecasts → find Polymarket event → manage position.
    The MIN_HOURS / MAX_HOURS guard lives inside get_polymarket_event, so we always
    probe all three dates and let the API result filter by actual hours_left.
    """
    state       = _load_state()
    calibration = load_calibration()
    dates       = _scan_dates()

    for city_slug in LOCATIONS:
        for date_str in dates:
            try:
                _process_city_date(city_slug, date_str, state, calibration)
            except Exception as exc:
                print(f"  ERROR {city_slug} {date_str}: {exc}")

    # After processing all cities, try to resolve any closed markets
    auto_resolve_all(state)
    # Update calibration if new resolved markets exist
    run_calibration()
    _save_state(state)


def _process_city_date(city_slug: str, date_str: str,
                       state: dict, calibration: dict) -> None:
    # 1. Find Polymarket event (enforces MIN_HOURS / MAX_HOURS internally)
    event = get_polymarket_event(city_slug, date_str)
    if event is None:
        return

    hours_left = event["hours_left"]

    # 2. Fetch forecast using confirmed hours_left
    forecast = get_best_forecast(city_slug, date_str, hours_left)
    if forecast["best"] is None:
        return

    # 3. Load or create market record
    mkt = load_market(city_slug, date_str) or new_market(
        city_slug, date_str, event["title"], hours_left
    )

    # 4. Save forecast snapshot
    append_forecast_snapshot(mkt, hours_left, forecast)

    # 5. Update latest outcome list
    mkt["all_outcomes"] = event["outcomes"]

    # 6. Check stops on open position
    pos = mkt.get("position")
    if pos and pos.get("close_reason") is None:
        token_id = pos.get("token_id")
        cur_bid, cur_ask = get_clob_prices(token_id) if token_id else (None, None)
        if cur_bid is not None:
            append_market_snapshot(mkt, hours_left, pos["bucket"], cur_bid, cur_ask or cur_bid)
            stop = check_stops(mkt, cur_bid, forecast["best"])
            if stop:
                close_position(mkt, cur_bid, stop, state)

    # 7. Open position if no position and signal found
    if mkt.get("position") is None or mkt["position"].get("close_reason") is not None:
        _maybe_open(mkt, event, forecast, hours_left, state, calibration)

    save_market(mkt)


def _maybe_open(market: dict, event: dict, forecast: dict,
                hours_left: float, state: dict, calibration: dict) -> None:
    """Evaluate all buckets and open the best-EV position if it clears thresholds."""
    city_slug    = market["city"]
    temp         = forecast["best"]
    best_source  = forecast["best_source"] or "ecmwf"
    balance      = state["balance"]
    best_ev      = MIN_EV
    best_outcome = None
    best_ev_val  = 0.0

    for outcome in event["outcomes"]:
        ask = outcome.get("ask")
        bid = outcome.get("bid")
        if ask is None or ask < MIN_PRICE or ask >= MAX_PRICE:
            continue
        if bid is not None and ask is not None and (ask - bid) > MAX_SLIPPAGE:
            continue

        lo, hi = outcome["lo"], outcome["hi"]
        p  = get_probability(city_slug, lo, hi, temp, best_source, calibration)
        ev = calc_ev(p, ask)
        if ev > best_ev:
            best_ev      = ev
            best_ev_val  = ev
            best_outcome = outcome
            best_p       = p

    if best_outcome is None:
        return

    kelly_frac  = calc_kelly(best_p, best_outcome["ask"])
    size_dollars = min(kelly_frac * balance, MAX_BET)
    if size_dollars < 1.0 or size_dollars > balance:
        return

    open_position(market, best_outcome, size_dollars, best_ev_val, kelly_frac, state)


def monitor_stops() -> None:
    """
    Lightweight stop check every 10 minutes.
    Only reads existing open markets — no API calls for forecast or new events.
    """
    state = _load_state()
    for path in glob.glob(os.path.join(DATA_DIR, "*.json")):
        with open(path) as f:
            mkt = json.load(f)
        if mkt.get("status") != "open":
            continue
        pos = mkt.get("position")
        if not pos or pos.get("close_reason") is not None:
            continue
        token_id = pos.get("token_id")
        if not token_id:
            continue
        cur_bid, cur_ask = get_clob_prices(token_id)
        if cur_bid is None:
            continue
        stop = check_stops(mkt, cur_bid, None)   # no fresh forecast
        if stop:
            close_position(mkt, cur_bid, stop, state)
            save_market(mkt)
    _save_state(state)

# ---------------------------------------------------------------------------
# CLI: status, report
# ---------------------------------------------------------------------------

def cmd_status() -> None:
    state = _load_state()
    print(f"\nBalance: ${state['balance']:.2f}")
    open_positions = []
    for path in glob.glob(os.path.join(DATA_DIR, "*.json")):
        with open(path) as f:
            mkt = json.load(f)
        pos = mkt.get("position")
        if pos and pos.get("close_reason") is None and mkt.get("status") == "open":
            open_positions.append((mkt["city"], mkt["date"], pos))

    print(f"Open positions: {len(open_positions)}")
    for city, date, pos in open_positions:
        print(f"  {city} {date}  bucket={pos['bucket']}  "
              f"size=${pos['size']:.2f}  entry={pos['entry_ask']:.3f}  "
              f"ev={pos['ev']:.4f}")
    print()


def cmd_report() -> None:
    rows = []
    for path in glob.glob(os.path.join(DATA_DIR, "*.json")):
        with open(path) as f:
            mkt = json.load(f)
        if mkt.get("status") == "resolved" and mkt.get("pnl") is not None:
            rows.append(mkt)

    if not rows:
        print("No resolved markets yet.")
        return

    rows.sort(key=lambda m: m["date"])
    wins   = sum(1 for r in rows if r["pnl"] > 0)
    losses = sum(1 for r in rows if r["pnl"] <= 0)
    total_pnl = sum(r["pnl"] for r in rows)

    print(f"\nResolved markets: {len(rows)}  W/L: {wins}/{losses}  "
          f"Total P&L: ${total_pnl:+.2f}\n")
    print(f"{'City':<14} {'Date':<12} {'Bucket':<20} {'Actual':>8} {'P&L':>8}  Reason")
    print("-" * 80)
    for r in rows:
        pos    = r.get("position", {}) or {}
        bucket = pos.get("bucket", r.get("resolved_outcome", "-"))
        actual = f"{r['actual_temp']:.1f}" if r.get("actual_temp") else "-"
        reason = pos.get("close_reason", "-")
        print(f"{r['city']:<14} {r['date']:<12} {bucket:<20} {actual:>8} "
              f"${r['pnl']:>+7.2f}  {reason}")
    print()

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else ""

    if arg == "status":
        cmd_status()
        return
    if arg == "report":
        cmd_report()
        return

    print("Polymarket Weather Bot starting…")
    print(f"Scanning {len(LOCATIONS)} cities every {SCAN_INTERVAL}s. Ctrl-C to stop.\n")

    # Quick stop-check thread — runs every 10 minutes between full scans
    def _monitor_loop():
        while True:
            time.sleep(600)
            try:
                monitor_stops()
            except Exception:
                pass

    t = threading.Thread(target=_monitor_loop, daemon=True)
    t.start()

    while True:
        print(f"[{_now_iso()}] Running full scan…")
        try:
            scan_and_update()
        except Exception as exc:
            print(f"Scan error: {exc}")
        print(f"[{_now_iso()}] Scan complete. Sleeping {SCAN_INTERVAL}s.\n")
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
