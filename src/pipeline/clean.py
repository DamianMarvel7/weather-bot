"""
clean.py — Parse raw Gamma market JSON and build a clean, flat DataFrame.

Cleaning rules applied (in order):
  1. Keep only markets that are resolved (not cancelled / annulled).
  2. Keep only binary YES/NO markets.
  3. Drop rows where final_price_yes is missing.
  4. Drop rows where start_price_yes is missing (unless relaxed by caller).
  5. Drop rows with volume_usd < min_volume.
  6. Drop rows with prices outside [0, 1].
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    s = str(value).strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S+00:00",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_json_field(v) -> list:
    """Coerce a value that might be a JSON string, list, or None into a list."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            result = json.loads(v)
            return result if isinstance(result, list) else []
        except Exception:
            return []
    return []


def _yes_token(m: dict) -> str | None:
    ids = _parse_json_field(m.get("clobTokenIds") or m.get("clob_token_ids"))
    return ids[0] if ids else None


# ── market parser ─────────────────────────────────────────────────────────────

def _parse_market(m: dict, price_histories: dict) -> dict | None:
    """
    Convert a single raw Gamma market dict into a flat record dict.
    Returns None to signal the market should be dropped entirely.
    """
    # ── 1. Must be closed ─────────────────────────────────────────────────────
    if not m.get("closed"):
        return None

    # ── 2. Binary markets only ────────────────────────────────────────────────
    outcomes = _parse_json_field(m.get("outcomes"))
    if len(outcomes) != 2:
        return None
    yes_label = outcomes[0].lower()
    if yes_label not in ("yes", "1", "true"):
        return None

    # ── 3. Resolution label — infer from final outcomePrices ─────────────────
    # The Gamma API does not expose a 'resolved'/'winner' field; instead the
    # final YES price converges to ~1 (YES won) or ~0 (NO won).
    op = _parse_json_field(m.get("outcomePrices"))
    if not op:
        return None
    yes_price = _to_float(op[0])
    if yes_price is None:
        return None
    if yes_price >= 0.99:
        resolved_yes = 1
    elif yes_price <= 0.01:
        resolved_yes = 0
    else:
        return None  # ambiguous / still-live price — skip

    # ── 4. Identifiers & text ─────────────────────────────────────────────────
    record: dict = {}
    record["market_id"] = str(m.get("id") or m.get("conditionId") or "")
    record["question"] = (m.get("question") or m.get("title") or "").strip()
    record["category"] = (m.get("category") or m.get("group") or "Other").strip()

    # ── 5. Dates ──────────────────────────────────────────────────────────────
    start_dt = _parse_dt(m.get("startDate") or m.get("start_date"))
    end_dt = _parse_dt(m.get("endDate") or m.get("end_date"))
    res_dt = _parse_dt(
        m.get("resolutionDate") or m.get("resolution_date") or m.get("endDate")
    )

    record["start_date"] = start_dt.isoformat() if start_dt else None
    record["end_date"] = end_dt.isoformat() if end_dt else None
    record["resolution_date"] = res_dt.isoformat() if res_dt else None

    # ── 6. Days open ──────────────────────────────────────────────────────────
    close_dt = end_dt or res_dt
    if start_dt and close_dt:
        record["days_open"] = max((close_dt - start_dt).days, 0)
    else:
        record["days_open"] = np.nan

    # ── 7. Volume ─────────────────────────────────────────────────────────────
    record["volume_usd"] = _to_float(
        m.get("volume") or m.get("volumeNum") or m.get("usdcVolume")
    )

    # ── 8. Resolution label ───────────────────────────────────────────────────
    record["resolved_yes"] = resolved_yes

    # ── 9. Prices — prefer CLOB price history, fall back to Gamma field ───────
    tok = _yes_token(m)
    hist = sorted(price_histories.get(tok, []), key=lambda h: h["t"]) if tok else []

    if hist:
        # First data-point = price when the market opened (or earliest we have)
        record["start_price_yes"] = hist[0]["p"]
        # Last data-point = price just before resolution
        record["final_price_yes"] = hist[-1]["p"]
    else:
        # Fallback: Gamma's outcomePrices (last-known YES price at close time)
        record["start_price_yes"] = np.nan          # genuinely unknown
        record["final_price_yes"] = yes_price       # already parsed above

    return record


# ── public API ────────────────────────────────────────────────────────────────

def build_dataframe(
    raw_markets: list[dict],
    price_histories: dict,
    min_volume: float = 1_000.0,
    require_start_price: bool = True,
) -> pd.DataFrame:
    """
    Parse all raw Gamma markets, apply cleaning filters, return a DataFrame.

    Parameters
    ----------
    raw_markets       : raw list returned by fetch_gamma_markets()
    price_histories   : dict returned by fetch_all_price_histories()
    min_volume        : minimum USD volume; markets below this are dropped
    require_start_price : when True (default) drop rows missing start_price_yes
    """
    records = [
        r
        for m in raw_markets
        if (r := _parse_market(m, price_histories)) is not None
    ]
    df = pd.DataFrame(records)

    if df.empty:
        print("[clean] No markets parsed — check API response format.")
        return df

    print(f"[clean] Parsed {len(df):,} non-cancelled resolved binary markets")

    # Drop missing final price (always required)
    before = len(df)
    df = df.dropna(subset=["final_price_yes"])
    print(f"[clean] Dropped {before - len(df):,} rows missing final_price_yes")

    # Optionally drop missing start price
    if require_start_price:
        before = len(df)
        df = df.dropna(subset=["start_price_yes"])
        print(f"[clean] Dropped {before - len(df):,} rows missing start_price_yes")

    # Drop low-volume markets
    before = len(df)
    df = df[df["volume_usd"].fillna(0) >= min_volume]
    print(f"[clean] Dropped {before - len(df):,} rows with volume < ${min_volume:,.0f}")

    # Price sanity: must be in [0, 1]
    before = len(df)
    price_ok = df["final_price_yes"].between(0, 1)
    if require_start_price:
        price_ok &= df["start_price_yes"].between(0, 1)
    df = df[price_ok]
    print(f"[clean] Dropped {before - len(df):,} rows with out-of-range prices")

    print(f"[clean] Final clean dataset: {len(df):,} rows")
    return df.reset_index(drop=True)
