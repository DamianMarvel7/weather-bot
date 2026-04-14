"""
Polymarket API: event discovery, CLOB orderbook prices, and bucket parsing.
"""

import json
import re
from datetime import datetime, timezone

import requests

from .config import GAMMA_API, CLOB_API, MIN_HOURS, MAX_HOURS, MIN_VOLUME, LOCATIONS


_session = requests.Session()


def _gamma_get(path: str, params: dict = None) -> list | dict | None:
    """GET from Gamma API with basic error handling."""
    try:
        resp = _session.get(f"{GAMMA_API}{path}", params=params, timeout=(5, 10))
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
        resp = _session.get(f"{CLOB_API}/book",
                            params={"token_id": clob_token_id}, timeout=(5, 8))
        resp.raise_for_status()
        book = resp.json()
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        # /book returns bids ascending, asks descending — best prices are at the end
        best_bid = float(bids[-1]["price"]) if bids else None
        best_ask = float(asks[-1]["price"]) if asks else None
        return best_bid, best_ask
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Cached event list — fetched once per scan, reused across all city-date lookups
# ---------------------------------------------------------------------------

_cached_events: list | None = None


def prefetch_events() -> None:
    """Fetch the full temperature event list once. Call at start of each scan."""
    global _cached_events
    _cached_events = _gamma_get("/events", params={
        "active":    "true",
        "closed":    "false",
        "tag_slug":  "temperature",
        "limit":     200,
        "order":     "createdAt",
        "ascending": "false",
    })


def clear_events_cache() -> None:
    """Clear cached events at end of scan."""
    global _cached_events
    _cached_events = None


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
    date_frag = f"{dt.strftime('%B')} {dt.day}"  # e.g. "March 27"

    events = _cached_events if _cached_events is not None else _gamma_get("/events", params={
        "active":    "true",
        "closed":    "false",
        "tag_slug":  "temperature",
        "limit":     200,
        "order":     "createdAt",
        "ascending": "false",
    })
    if not events:
        return None

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

    end_dt_str = match.get("endDate") or ""
    try:
        end_dt     = datetime.fromisoformat(end_dt_str.replace("Z", "+00:00"))
        hours_left = (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
    except Exception:
        hours_left = 999.0

    if not (MIN_HOURS <= hours_left <= MAX_HOURS):
        return None

    child_markets = match.get("markets", [])
    outcomes      = []
    total_volume  = 0.0

    for child in child_markets:
        question = child.get("question", "")
        m = re.search(r"\bbe\s+(.+?)\s+on\s+\w+\s+\d+", question, re.I)
        label = m.group(1) if m else question
        lo, hi = parse_bucket_bounds(label)

        raw_ids  = child.get("clobTokenIds", "[]")
        try:
            clob_ids = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
        except Exception:
            clob_ids = []
        token_id = clob_ids[0] if clob_ids else None

        bid, ask = None, None
        if token_id:
            bid, ask = get_clob_prices(token_id)
        # Only fall back to outcomePrices for bid display — never use it as ask
        # (outcomePrices is stale AMM data and bypasses the min_price filter)
        if bid is None:
            raw_prices = child.get("outcomePrices", "[]")
            try:
                prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
                bid = float(prices[0]) if prices else None
            except Exception:
                pass

        vol = float(child.get("volume") or 0)
        total_volume += vol

        outcomes.append({
            "label":     label,
            "lo":        lo,
            "hi":        hi,
            "bid":       bid,
            "ask":       ask,
            "token_id":  token_id,
            "market_id": child.get("id"),
            "volume":    vol,
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


def get_polymarket_historical_resolved(city_slug: str, date_str: str) -> tuple[str | None, float | None]:
    """
    Fetch the winning bucket label and representative temp for a closed Polymarket event.

    Paginates through closed temperature events until the city+date match is found.
    Returns (label, midpoint) or (None, None) if not found / not yet settled.

    Midpoint rules:
      - exact bucket "13°C" (lo=12.5, hi=13.5) → 13.0
      - tail "33°C or below"                    → 33.0 (use the bound)
      - tail "58°F or higher"                   → 58.0 (use the bound)
    """
    city_name = LOCATIONS[city_slug]["name"]
    dt        = datetime.strptime(date_str, "%Y-%m-%d")
    date_frag = f"{dt.strftime('%B')} {dt.day}"  # e.g. "March 25"

    offset = 0
    while True:
        events = _gamma_get("/events", params={
            "closed":    "true",
            "tag_slug":  "temperature",
            "limit":     200,
            "offset":    offset,
            "order":     "createdAt",
            "ascending": "false",
        })
        if not events:
            break

        for ev in events:
            title = ev.get("title", "")
            if city_name.lower() not in title.lower():
                continue
            if date_frag not in title:
                continue
            # Found matching event — find the winning child market
            for child in ev.get("markets", []):
                raw = child.get("outcomePrices", "[]")
                try:
                    prices    = json.loads(raw) if isinstance(raw, str) else raw
                    yes_price = float(prices[0]) if prices else 0.5
                except Exception:
                    continue
                if yes_price >= 0.95:
                    question = child.get("question", "")
                    m_q      = re.search(r"\bbe\s+(.+?)\s+on\s+\w+\s+\d+", question, re.I)
                    label    = m_q.group(1) if m_q else question
                    lo, hi   = parse_bucket_bounds(label)
                    if lo <= -900:
                        midpoint = hi        # "X or below" → use X
                    elif hi >= 900:
                        midpoint = lo        # "X or above" → use X
                    else:
                        midpoint = (lo + hi) / 2
                    return label, midpoint
            return None, None  # event found but no winner settled yet

        if len(events) < 200:
            break
        offset += 200

    return None, None


def check_gamma_resolved(market_id: str) -> bool | None:
    """
    Check if a child market has been officially settled on Polymarket via Gamma API.

    Returns True  → YES outcome won (outcomePrices[0] >= 0.95)
            False → NO outcome won  (outcomePrices[0] <= 0.05)
            None  → market not closed or outcome unclear
    """
    data = _gamma_get(f"/markets/{market_id}")
    if not data or not data.get("closed", False):
        return None
    raw = data.get("outcomePrices", "[]")
    try:
        prices = json.loads(raw) if isinstance(raw, str) else raw
        yes_price = float(prices[0]) if prices else 0.5
    except Exception:
        return None
    if yes_price >= 0.95:
        return True
    if yes_price <= 0.05:
        return False
    return None
