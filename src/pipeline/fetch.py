"""
fetch.py — Pull resolved markets from Polymarket APIs with caching.

Primary source : Gamma markets API  (metadata + volume + resolution)
Price source   : CLOB prices-history (start / final YES prices per token)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

_SLEEP_PAGES = 0.5       # seconds between paginated Gamma requests
_SLEEP_PRICES = 0.5      # seconds between per-market CLOB price fetches


# ── low-level HTTP ────────────────────────────────────────────────────────────

def _get(url: str, params: dict | None = None, retries: int = 3) -> dict | list:
    """GET with simple exponential-backoff retry."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < retries - 1:
                wait = 2.0 * (2 ** attempt)
                print(f"  [fetch] Request error ({exc}), retrying in {wait:.0f}s…")
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


# ── Gamma markets ─────────────────────────────────────────────────────────────

_CHECKPOINT_EVERY = 5_000  # write partial cache after this many markets


def _save_cache(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def fetch_gamma_markets(
    cache_path: str = "data/raw/markets_raw.json",
    force_refresh: bool = False,
) -> list[dict]:
    """
    Paginate the Gamma markets API (?closed=true) and cache all results.

    Writes a checkpoint every _CHECKPOINT_EVERY markets so a network failure
    or KeyboardInterrupt mid-run doesn't discard everything. Re-running
    without --force-refresh resumes from the partial cache automatically.
    """
    cache = Path(cache_path)
    limit = 100

    # Load existing cache (partial or complete) unless forced to restart
    all_markets: list[dict] = []
    if cache.exists() and not force_refresh:
        all_markets = json.loads(cache.read_text())
        print(f"[fetch] Loading Gamma markets from cache: {cache}")
        return all_markets

    if not all_markets:
        print("[fetch] Fetching resolved markets from Gamma API…")

    offset = len(all_markets)

    try:
        while True:
            data = _get(
                f"{GAMMA_BASE}/markets",
                params={"closed": "true", "limit": limit, "offset": offset},
            )

            if isinstance(data, list):
                page = data
            else:
                page = data.get("markets") or data.get("data") or []

            if not page:
                break

            all_markets.extend(page)
            total = len(all_markets)

            if total % 500 < limit or offset == 0:
                print(f"  Fetched {total} markets…")

            # Periodic checkpoint — survive network blips without losing data
            if total % _CHECKPOINT_EVERY < limit:
                _save_cache(cache, all_markets)
                print(f"  [fetch] Checkpoint saved ({total:,} markets) → {cache}")

            time.sleep(_SLEEP_PAGES)

            if len(page) < limit:
                break
            offset += limit

    except BaseException as exc:
        # Save whatever we collected before the failure (catches KeyboardInterrupt too)
        if all_markets:
            _save_cache(cache, all_markets)
            print(f"\n[fetch] Interrupted at {len(all_markets):,} markets: {exc}")
            print(f"[fetch] Partial cache saved → {cache}  (re-run to resume)")
        raise

    print(f"[fetch] Total Gamma markets fetched: {len(all_markets):,}")
    _save_cache(cache, all_markets)
    print(f"[fetch] Raw cache saved → {cache}")
    return all_markets


# ── CLOB price histories ──────────────────────────────────────────────────────

def _extract_yes_token(m: dict) -> str | None:
    """Return the CLOB YES-token ID from a Gamma market dict, or None."""
    ids = m.get("clobTokenIds") or m.get("clob_token_ids") or []
    if isinstance(ids, str):
        try:
            ids = json.loads(ids)
        except Exception:
            return None
    return ids[0] if ids else None


def fetch_price_history(token_id: str, fidelity: int = 60) -> list[dict]:
    """
    Fetch the full price history for a single YES token from the CLOB API.

    *fidelity* is minutes per data-point (60 = hourly buckets).
    Returns a list of {"t": <unix_ts>, "p": <price>} dicts (may be empty).
    """
    try:
        data = _get(
            f"{CLOB_BASE}/prices-history",
            params={"market": token_id, "interval": "max", "fidelity": fidelity},
        )
        return data.get("history", [])
    except Exception as exc:
        print(f"  [fetch] Price history failed for token {token_id[:10]}…: {exc}")
        return []


def fetch_all_price_histories(
    markets: list[dict],
    cache_path: str = "data/raw/prices_raw.json",
    force_refresh: bool = False,
) -> dict[str, list[dict]]:
    """
    Batch-fetch CLOB price histories for all markets that have a YES token ID.

    Results are merged with any existing cache so interrupted runs resume
    without re-fetching already-downloaded histories.

    Returns dict mapping  token_id -> list[{"t": int, "p": float}].
    """
    cache = Path(cache_path)
    histories: dict[str, list[dict]] = {}

    if cache.exists() and not force_refresh:
        print(f"[fetch] Loading price histories from cache: {cache}")
        histories = json.loads(cache.read_text())

    # Determine which tokens we still need
    pending = [
        (m, tok)
        for m in markets
        if (tok := _extract_yes_token(m)) and tok not in histories
    ]

    if not pending:
        print("[fetch] All price histories already cached.")
        return histories

    print(f"[fetch] Fetching price histories for {len(pending):,} markets…")
    for i, (_, tok) in enumerate(pending, 1):
        histories[tok] = fetch_price_history(tok)
        if i % 100 == 0:
            print(f"  Price histories: {i:,}/{len(pending):,}")
        time.sleep(_SLEEP_PRICES)

    # Persist merged cache after every batch (in case of interruption, we
    # still lose at most the last ~100 fetches — acceptable trade-off)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(histories))
    print(f"[fetch] Price history cache saved → {cache}")
    return histories
