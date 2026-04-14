"""
Weather forecast retrieval: multi-model ensemble via Open-Meteo, and METAR observations.
"""

import requests

from .config import LOCATIONS, TIMEZONES, MAX_FORECAST_SPREAD_F, MAX_FORECAST_SPREAD_C

_session = requests.Session()

# ---------------------------------------------------------------------------
# Per-scan caches — call prefetch_forecasts() / clear_forecast_cache() in bot
# ---------------------------------------------------------------------------

_forecast_cache: dict[str, dict[str, dict[str, float | None]]] = {}
_metar_cache: dict[str, float | None] = {}


def prefetch_forecasts(city_slugs: list[str], dates: list[str]) -> None:
    """Batch-fetch forecasts for all cities. One API call per city (all dates)."""
    _forecast_cache.clear()
    _metar_cache.clear()
    for city_slug in city_slugs:
        loc = LOCATIONS[city_slug]
        temp_unit = "fahrenheit" if loc["unit"] == "F" else "celsius"
        if loc["region"] == "us":
            models = ["gfs_seamless", "ecmwf_ifs025", "gfs_global"]
        else:
            models = ["ecmwf_ifs025", "gfs_global", "icon_seamless"]
        data = _fetch_open_meteo(city_slug, dates, models, temp_unit, forecast_days=7)
        _forecast_cache[city_slug] = data
        # Prefetch METAR too
        _metar_cache[city_slug] = _get_metar_raw(city_slug)


def clear_forecast_cache() -> None:
    _forecast_cache.clear()
    _metar_cache.clear()


def _fetch_open_meteo(city_slug: str, dates: list, models: list[str],
                      temp_unit: str, forecast_days: int) -> dict[str, dict[str, float | None]]:
    """
    Fetch daily max temperature from Open-Meteo for one or more models.

    Returns {date_str: {model_name: temp_or_None}}.
    """
    loc = LOCATIONS[city_slug]
    models_str = ",".join(models)
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&daily=temperature_2m_max&temperature_unit={temp_unit}"
        f"&forecast_days={forecast_days}&timezone={TIMEZONES.get(city_slug, 'UTC')}"
        f"&models={models_str}"
    )
    try:
        data = _session.get(url, timeout=(5, 15)).json()
        daily = data.get("daily", {})
        times = daily.get("time", [])
        result: dict[str, dict[str, float | None]] = {d: {} for d in dates}
        for model in models:
            key = f"temperature_2m_max_{model}" if len(models) > 1 else "temperature_2m_max"
            temps = daily.get(key, [])
            model_map = dict(zip(times, temps))
            for d in dates:
                result[d][model] = model_map.get(d)
        return result
    except Exception:
        return {d: {} for d in dates}


def _get_metar_raw(city_slug: str) -> float | None:
    """Live ICAO station observation — same station Polymarket uses for resolution."""
    loc = LOCATIONS[city_slug]
    url = f"https://aviationweather.gov/api/data/metar?ids={loc['station']}&format=json"
    try:
        data = _session.get(url, timeout=(5, 8)).json()
        if not data:
            return None
        temp_c = data[0].get("temp")
        if temp_c is None:
            return None
        return round(temp_c * 9 / 5 + 32, 1) if loc["unit"] == "F" else round(float(temp_c), 1)
    except Exception:
        return None


def get_metar(city_slug: str) -> float | None:
    """Return cached METAR if available, otherwise fetch live."""
    if city_slug in _metar_cache:
        return _metar_cache[city_slug]
    return _get_metar_raw(city_slug)


def get_best_forecast(city_slug: str, date_str: str, hours_ahead: float) -> dict:
    """
    Return ensemble forecast for a single date.

    Fetches multiple models, computes weighted average and model spread.
    METAR fetched for D+0 but doesn't drive entry decisions.
    """
    loc = LOCATIONS[city_slug]
    temp_unit = "fahrenheit" if loc["unit"] == "F" else "celsius"

    # Select models based on region
    if loc["region"] == "us" and hours_ahead <= 48:
        models = ["gfs_seamless", "ecmwf_ifs025", "gfs_global"]
        forecast_days = 3
    else:
        models = ["ecmwf_ifs025", "gfs_global", "icon_seamless"]
        forecast_days = 7

    # Use cached data if available (from prefetch_forecasts), else fetch live
    if city_slug in _forecast_cache and date_str in _forecast_cache[city_slug]:
        day_data = _forecast_cache[city_slug][date_str]
    else:
        model_data = _fetch_open_meteo(city_slug, [date_str], models, temp_unit, forecast_days)
        day_data = model_data.get(date_str, {})

    # Collect non-None forecasts
    valid_forecasts: dict[str, float] = {}
    for model, temp in day_data.items():
        if temp is not None:
            valid_forecasts[model] = temp

    metar = get_metar(city_slug) if hours_ahead <= 30 else None

    if not valid_forecasts:
        return {"ecmwf": None, "hrrr": None, "metar": metar,
                "best": None, "best_source": None,
                "models": {}, "spread": None}

    # Ensemble average and spread
    temps = list(valid_forecasts.values())
    avg_temp = sum(temps) / len(temps)
    spread = max(temps) - min(temps) if len(temps) > 1 else 0.0

    # For backward compatibility, extract individual model temps
    ecmwf = valid_forecasts.get("ecmwf_ifs025")
    hrrr = valid_forecasts.get("gfs_seamless")

    return {
        "ecmwf":       ecmwf,
        "hrrr":        hrrr,
        "metar":       metar,
        "best":        round(avg_temp, 1),
        "best_source": "ensemble",
        "models":      valid_forecasts,
        "spread":      round(spread, 1),
    }
