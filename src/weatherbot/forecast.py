"""
Weather forecast retrieval: ECMWF (global), HRRR/GFS (US), and METAR observations.
"""

import requests

from .config import LOCATIONS, TIMEZONES


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
    Priority: HRRR (US ≤48h) > ECMWF > None.
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
