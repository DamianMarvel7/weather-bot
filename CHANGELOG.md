# Changelog

This document records every significant change to the bot, the bug that triggered it,
and the reasoning behind the fix. Intended as institutional memory for future development.

---

## Session: 2026-03-26

### Overview
Investigated why 19 markets from March 25 had not auto-resolved despite today being
March 26. Discovered the resolution logic was flawed, diagnosed 0/12 loss rate on the
first batch of resolved trades, and traced it to three compounding model bugs.

---

### Fix 1 — Auto-resolution: Add Gamma API fallback to `_auto_resolve`

**Files changed:** `src/weatherbot/polymarket.py`, `src/weatherbot/bot.py`

**The bug:**
`_auto_resolve` detected settlement by checking CLOB orderbook prices — if any
outcome's mid price hit ≥ 0.95, that bucket was declared the winner. This works
once the CLOB has fully settled, but Polymarket often takes hours or even a day
after the market's end time to push final prices to the orderbook. During that
window, CLOB prices are still mid-range (e.g. 0.3–0.7) and the bot can't resolve.

**The reference:**
`archive/bot_v2.py` used `GET /markets/{market_id}` on the Gamma API and checked
the `closed` boolean + `outcomePrices` field. This is the authoritative settlement
signal — Polymarket writes it once resolution is confirmed, regardless of CLOB state.

**The fix:**
Two-pass resolution in `_auto_resolve`:
1. Pass 1: existing CLOB mid price ≥ 0.95 (fast path, works post-settlement)
2. Pass 2: Gamma API `closed=True` + `outcomePrices[0] ≥ 0.95` (catches the gap
   between market end time and CLOB settlement)

To enable Pass 2, we also store the Gamma child market `id` in each outcome dict
during `get_polymarket_event`. Previously only `token_id` (CLOB token) was stored.

**Lesson for future development:**
Always distinguish between "CLOB price reflects settlement" and "Gamma API says
closed". They are not simultaneous. Gamma is the source of truth; CLOB reflects
it eventually. Any resolution logic should check Gamma first or as a fallback.

**New function added:** `check_gamma_resolved(market_id)` in `polymarket.py`

---

### Fix 1b — Backfill: Retroactively resolved March 25 markets

**Context:**
The 19 open March 25 markets had no `market_id` stored (positions opened before Fix 1
was deployed). The new Gamma fallback in `_auto_resolve` couldn't fire without it.

**Approach:**
Ran a one-off script that:
1. For each stale open market file, searched the Gamma API (including `closed=true`
   events) to find the matching event by city name + date fragment
2. Built a `token_id → market_id` mapping from the event's child markets
3. Backfilled `market_id` into each outcome in `all_outcomes`
4. Attempted Gamma resolution immediately using the new `check_gamma_resolved`

**Result:** 13 of 19 resolved. 6 (buenos-aires, dallas, miami, sao-paulo, seattle,
toronto) were still pending Polymarket settlement — they have `market_id` stored and
will auto-resolve on the next scan once Gamma marks them closed.

**Note on bot_state.json:**
The backfill script wrote PnL and close_reason directly into market files but did not
update `bot_state.json` (balance, win/loss counters). Those go through
`BotState.close_position`. After the backfill, run `uv run weatherbot status` to
let the normal scan pick up and sync state.

---

### Fix 2 — Entry point: Add `weatherbot` CLI command

**Files changed:** `pyproject.toml`

**The bug:**
Running `uv run src/weatherbot/weatherbet.py status` failed with
`ImportError: attempted relative import with no known parent package`.
The file uses relative imports (`.config`, `.bot`, etc.) which only work when
the module is loaded as part of a package, not as a standalone script.

**The fix:**
Added proper packaging to `pyproject.toml`:
```toml
[tool.uv]
package = true

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/weatherbot"]

[project.scripts]
weatherbot = "weatherbot.weatherbet:main"
```

Run `uv sync` once after this change to install the entry point.

**Usage going forward:**
```bash
uv run weatherbot          # start main scan loop
uv run weatherbot status   # balance + open positions
uv run weatherbot report   # resolved P&L history
uv run weatherbot edge     # EV calibration analysis
```

---

### Fix 3 — Probability model: Correct default sigma

**File changed:** `src/weatherbot/portfolio.py` (`get_probability`)

**The bug:**
Default sigma (forecast uncertainty) was `1.5°C / 3.0°F`. This is roughly half the
real-world ECMWF 48-hour max temperature MAE at airport stations (~2.5°C / 5°F based
on published verification data).

**Why this caused losses:**
The probability formula is `P(actual in bucket) = Normal(forecast, sigma)` integrated
over the bucket range. With sigma=1.5°C and a 1°C-wide bucket:
- P(center bucket) = 26%
- Market typically prices each 1°C bucket at 3–10 cents

EV = P / ask − 1. At P=26% and ask=0.05: EV = 4.2 (420% edge). This looks like a
massive opportunity but is fake — the true P with sigma=2.5°C is only 16%.

The inflated probability generated phantom EV on every single bucket, making the
model look like it had an edge everywhere when it had no real edge.

**Additional consequence:**
With max_ev=0.5 (the old cap), EV=4.2 was blocked. Combined with the inflated
probabilities, basically NO bets could pass the filter. The refactored bot was
silently not opening any new positions.

**The fix:**
```python
# Before
default = 3.0 if unit == "F" else 1.5

# After — based on published ECMWF 48h max-temp verification
default = 5.0 if unit == "F" else 2.5
```

With sigma=2.5°C, center bucket P=16%. At ask=0.10: EV=0.60 — a genuine, plausible
edge that can be acted on.

---

### Fix 4 — Probability model: Add forecast bias correction

**File changed:** `src/weatherbot/portfolio.py` (`run_calibration`, `get_probability`)

**The bug:**
Calibration tracked only MAE (mean absolute error) — a symmetric measure of forecast
spread. It did not track the signed error (bias = forecast − actual), which means
systematic warm/cold offsets were never corrected.

**Evidence from March 25 losses:**
Running calibration after the backfill revealed consistent cold bias at most stations:
- US cities: −1.5 to −2.1°F (ECMWF runs systematically cold at US airports)
- EU cities: −0.1 to −0.9°C

When the model forecast 9°C for Paris and bet on the "9°C" bucket, the actual came in
at 13°C. The bias alone (−0.86°C) doesn't explain a 4°C miss, but across many trades,
a 1°C systematic cold bias means the model is consistently one bucket lower than
reality.

**The fix:**
`run_calibration` now computes both:
- `mae`: mean(|forecast − actual|) — used as sigma
- `bias`: mean(forecast − actual) — systematic offset

`get_probability` now applies bias correction before computing bucket probability:
```python
corrected_temp = forecast_temp - bias
return _bucket_probability(bucket_lo, bucket_hi, corrected_temp, sigma)
```

If the model runs 2°F cold (bias = −2.0), the corrected forecast is 2°F warmer,
shifting probability mass toward the buckets where temperatures actually fall.

**Fallback for missing actual_temp (no VC key):**
When `actual_temp` is None (Visual Crossing API key not configured), calibration now
falls back to estimating the actual from the resolved bucket midpoint. This works well
for exact 1-unit buckets ("15°C" → 15°C) but is skipped for tail buckets
("33°C or higher") where the midpoint is undefined. Set `vc_key` in config.json to
get precise actual temps for full calibration quality.

---

### Fix 5 — Config: Coherent EV window

**File changed:** `src/weatherbot/config.json`

**The problem:**
Three config values were inconsistent with each other and with the probability model:

| Parameter | Old | New | Reason |
|---|---|---|---|
| `max_ev` | 0.5 | 1.5 | Old value blocked all realistic bets given corrected sigma |
| `min_price` | 0.03 | 0.05 | Avoid sub-5-cent lottery tickets even with correct sigma |
| `calibration_min` | 30 | 15 | Learn from data sooner; 15 resolved markets is enough for stable MAE |

**How these interact:**
With sigma=2.5°C, center bucket P=16%. For EV to be in the valid window [0.05, 1.5]:
- min ask: P / (1 + max_ev) = 0.16 / 2.5 = 0.064 (6.4 cents)
- max ask: P / (1 + min_ev) = 0.16 / 1.05 = 0.152 (15.2 cents)

So the bot now bets on buckets priced roughly 6–15 cents where its model assigns ≥16%
probability. This is a coherent, sensible range. Cities with high MAE (chicago=10.49°F)
will have P < 5% on narrow 1°F buckets and naturally get skipped; wide tail buckets
for those cities will still have 30–70% P and can be bet if the market underprices them.

---

### Fix 6 — `_maybe_open`: Minimum probability guard

**File changed:** `src/weatherbot/bot.py`

**The fix:**
Added `if p < 0.05: continue` before EV calculation in `_maybe_open`.

**Why:**
Even with min_price=0.05, it's possible to construct a scenario where a bucket with
P=2% at ask=0.03 has positive EV (2%/3% − 1 = −0.33, actually negative here, but
edge cases exist). More importantly, a 2% probability bet means the model barely
believes in the outcome — even if EV is technically positive, it's driven by price
noise not genuine belief.

The 5% floor ensures the bot only bets on outcomes it considers at least plausible.
With the calibrated sigma values now in place, this will also naturally filter all
narrow buckets for high-uncertainty cities like Chicago.

---

## Architectural Lessons (for future development)

### On resolution
- Gamma API `closed` + `outcomePrices` is the authoritative resolution signal
- CLOB prices approach 0/1 after settlement but can lag by hours
- Always store both `token_id` (for CLOB) and `market_id` (for Gamma) in outcomes

### On probability modeling
- Sigma should match the forecast source's real-world MAE, not an intuition
- MAE alone is not enough — track signed bias separately and apply as correction
- Use the calibration system aggressively: lower `calibration_min` so it activates
  sooner and feeds real per-city values back into the model

### On EV thresholds
- `max_ev` and `min_price` must be chosen together given the expected sigma range
- Too-tight `max_ev` silently blocks all bets; too-loose creates lottery-ticket trades
- A good sanity check: compute the implied ask range where bets would be placed
  (min_ask = P / (1 + max_ev), max_ask = P / (1 + min_ev)) and verify it's a
  realistic market price range

### On the cold-start problem
- Before calibration activates (< calibration_min resolved markets), defaults drive
  all decisions — so defaults must be grounded in real verification data, not guesses
- The bucket-midpoint fallback for actual_temp lets calibration run without a VC key,
  but get a VC key eventually for precise values

### On debugging losses
When the bot shows a bad win rate, diagnose in this order:
1. Check `report` for bet bucket vs resolved bucket — are we off by 1 bucket or many?
2. Check calibration MAE per city — high MAE means narrow bucket bets will always lose
3. Check bias per city — consistent directional miss = bias not corrected
4. Check whether any new positions are actually being opened (EV window coherence)
5. Check if market_ids are populated — resolution fallback won't fire without them
