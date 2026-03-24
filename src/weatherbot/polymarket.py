"""
Polymarket API: event discovery, CLOB orderbook prices, and bucket parsing.
"""

import json
import re
from datetime import datetime, timezone

import requests

from .config import GAMMA_API, CLOB_API, MIN_HOURS, MAX_HOURS, MIN_VOLUME, LOCATIONS


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
    date_frag = f"{dt.strftime('%B')} {dt.day}"  # e.g. "March 27"

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
