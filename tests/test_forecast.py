"""
Tests for src/weatherbot/forecast.py

Run with:
    uv run pytest tests/test_forecast.py -v

Key invariant verified: US cities must use HRRR (not ECMWF) as best_source
when hours_ahead <= 48 and HRRR returns a valid temperature.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.weatherbot.forecast import get_best_forecast, get_ecmwf, get_hrrr, get_metar


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_open_meteo(temps_by_date: dict):
    """Return a mock requests.Response whose .json() matches Open-Meteo shape."""
    dates = list(temps_by_date.keys())
    temps = list(temps_by_date.values())
    mock = MagicMock()
    mock.json.return_value = {
        "daily": {"time": dates, "temperature_2m_max": temps}
    }
    return mock


def _mock_metar(temp_c: float | None):
    mock = MagicMock()
    mock.json.return_value = [{"temp": temp_c}] if temp_c is not None else []
    return mock


# ---------------------------------------------------------------------------
# get_ecmwf
# ---------------------------------------------------------------------------

class TestGetEcmwf(unittest.TestCase):
    DATE = "2026-04-10"

    @patch("src.weatherbot.forecast.requests.get")
    def test_returns_temp_for_requested_date(self, mock_get):
        mock_get.return_value = _mock_open_meteo({self.DATE: 72.5})
        result = get_ecmwf("nyc", [self.DATE])
        self.assertAlmostEqual(result[self.DATE], 72.5)

    @patch("src.weatherbot.forecast.requests.get")
    def test_returns_none_for_missing_date(self, mock_get):
        mock_get.return_value = _mock_open_meteo({"2026-04-11": 70.0})
        result = get_ecmwf("nyc", [self.DATE])
        self.assertIsNone(result[self.DATE])

    @patch("src.weatherbot.forecast.requests.get")
    def test_uses_fahrenheit_for_us_city(self, mock_get):
        mock_get.return_value = _mock_open_meteo({self.DATE: 72.0})
        get_ecmwf("nyc", [self.DATE])
        url = mock_get.call_args[0][0]
        self.assertIn("fahrenheit", url)

    @patch("src.weatherbot.forecast.requests.get")
    def test_uses_celsius_for_eu_city(self, mock_get):
        mock_get.return_value = _mock_open_meteo({self.DATE: 18.0})
        get_ecmwf("london", [self.DATE])
        url = mock_get.call_args[0][0]
        self.assertIn("celsius", url)

    @patch("src.weatherbot.forecast.requests.get")
    def test_uses_ecmwf_model(self, mock_get):
        mock_get.return_value = _mock_open_meteo({self.DATE: 70.0})
        get_ecmwf("nyc", [self.DATE])
        url = mock_get.call_args[0][0]
        self.assertIn("ecmwf_ifs025", url)

    @patch("src.weatherbot.forecast.requests.get")
    def test_returns_none_on_network_error(self, mock_get):
        mock_get.side_effect = Exception("timeout")
        result = get_ecmwf("nyc", [self.DATE])
        self.assertIsNone(result[self.DATE])

    @patch("src.weatherbot.forecast.requests.get")
    def test_handles_multiple_dates(self, mock_get):
        dates = ["2026-04-10", "2026-04-11", "2026-04-12"]
        mock_get.return_value = _mock_open_meteo(dict(zip(dates, [70.0, 71.0, 72.0])))
        result = get_ecmwf("nyc", dates)
        self.assertEqual(len(result), 3)
        self.assertAlmostEqual(result["2026-04-11"], 71.0)


# ---------------------------------------------------------------------------
# get_hrrr
# ---------------------------------------------------------------------------

class TestGetHrrr(unittest.TestCase):
    DATE = "2026-04-10"

    @patch("src.weatherbot.forecast.requests.get")
    def test_returns_temp_for_us_city(self, mock_get):
        mock_get.return_value = _mock_open_meteo({self.DATE: 75.0})
        result = get_hrrr("nyc", [self.DATE])
        self.assertAlmostEqual(result[self.DATE], 75.0)

    @patch("src.weatherbot.forecast.requests.get")
    def test_returns_empty_for_non_us_city(self, mock_get):
        result = get_hrrr("london", [self.DATE])
        self.assertEqual(result, {})
        mock_get.assert_not_called()

    @patch("src.weatherbot.forecast.requests.get")
    def test_uses_gfs_seamless_model(self, mock_get):
        mock_get.return_value = _mock_open_meteo({self.DATE: 75.0})
        get_hrrr("chicago", [self.DATE])
        url = mock_get.call_args[0][0]
        self.assertIn("gfs_seamless", url)

    @patch("src.weatherbot.forecast.requests.get")
    def test_always_uses_fahrenheit(self, mock_get):
        mock_get.return_value = _mock_open_meteo({self.DATE: 75.0})
        get_hrrr("nyc", [self.DATE])
        url = mock_get.call_args[0][0]
        self.assertIn("fahrenheit", url)

    @patch("src.weatherbot.forecast.requests.get")
    def test_returns_empty_on_network_error(self, mock_get):
        mock_get.side_effect = Exception("timeout")
        result = get_hrrr("nyc", [self.DATE])
        self.assertEqual(result, {})

    @patch("src.weatherbot.forecast.requests.get")
    def test_returns_none_for_date_not_in_response(self, mock_get):
        mock_get.return_value = _mock_open_meteo({"2026-04-11": 73.0})
        result = get_hrrr("nyc", [self.DATE])
        self.assertIsNone(result.get(self.DATE))


# ---------------------------------------------------------------------------
# get_metar
# ---------------------------------------------------------------------------

class TestGetMetar(unittest.TestCase):
    @patch("src.weatherbot.forecast.requests.get")
    def test_converts_celsius_to_fahrenheit_for_us_city(self, mock_get):
        mock_get.return_value = _mock_metar(20.0)  # 20°C → 68°F
        result = get_metar("nyc")
        self.assertAlmostEqual(result, 68.0, places=1)

    @patch("src.weatherbot.forecast.requests.get")
    def test_returns_celsius_for_eu_city(self, mock_get):
        mock_get.return_value = _mock_metar(15.0)
        result = get_metar("london")
        self.assertAlmostEqual(result, 15.0, places=1)

    @patch("src.weatherbot.forecast.requests.get")
    def test_returns_none_when_api_returns_empty_list(self, mock_get):
        mock = MagicMock()
        mock.json.return_value = []
        mock_get.return_value = mock
        self.assertIsNone(get_metar("nyc"))

    @patch("src.weatherbot.forecast.requests.get")
    def test_returns_none_when_temp_field_missing(self, mock_get):
        mock = MagicMock()
        mock.json.return_value = [{"wind": 5}]  # no "temp" key
        mock_get.return_value = mock
        self.assertIsNone(get_metar("nyc"))

    @patch("src.weatherbot.forecast.requests.get")
    def test_returns_none_on_network_error(self, mock_get):
        mock_get.side_effect = Exception("timeout")
        self.assertIsNone(get_metar("nyc"))

    @patch("src.weatherbot.forecast.requests.get")
    def test_uses_correct_icao_station(self, mock_get):
        mock_get.return_value = _mock_metar(10.0)
        get_metar("nyc")
        url = mock_get.call_args[0][0]
        self.assertIn("KLGA", url)

    @patch("src.weatherbot.forecast.requests.get")
    def test_rounds_to_one_decimal(self, mock_get):
        mock_get.return_value = _mock_metar(21.3)  # 21.3°C → 70.34°F → 70.3
        result = get_metar("nyc")
        self.assertEqual(result, round(21.3 * 9 / 5 + 32, 1))


# ---------------------------------------------------------------------------
# get_best_forecast — source-selection logic
# This is the critical section: US cities ≤48h must prefer HRRR over ECMWF.
# ---------------------------------------------------------------------------

class TestGetBestForecastSourceSelection(unittest.TestCase):
    DATE = "2026-04-10"

    def _patch_all(self, ecmwf_val, hrrr_val, metar_val=None):
        """Context-manager stack: patches get_ecmwf, get_hrrr, get_metar."""
        p_ecmwf = patch(
            "src.weatherbot.forecast.get_ecmwf",
            return_value={self.DATE: ecmwf_val},
        )
        p_hrrr = patch(
            "src.weatherbot.forecast.get_hrrr",
            return_value={self.DATE: hrrr_val} if hrrr_val is not None else {},
        )
        p_metar = patch(
            "src.weatherbot.forecast.get_metar",
            return_value=metar_val,
        )
        return p_ecmwf, p_hrrr, p_metar

    # --- US + ≤48h: HRRR must win ---

    def test_us_city_within_48h_uses_hrrr(self):
        p_ecmwf, p_hrrr, p_metar = self._patch_all(ecmwf_val=70.0, hrrr_val=72.0)
        with p_ecmwf, p_hrrr, p_metar:
            r = get_best_forecast("nyc", self.DATE, hours_ahead=24)
        self.assertEqual(r["best_source"], "hrrr")
        self.assertAlmostEqual(r["best"], 72.0)

    def test_us_city_exactly_48h_uses_hrrr(self):
        p_ecmwf, p_hrrr, p_metar = self._patch_all(ecmwf_val=68.0, hrrr_val=69.0)
        with p_ecmwf, p_hrrr, p_metar:
            r = get_best_forecast("nyc", self.DATE, hours_ahead=48)
        self.assertEqual(r["best_source"], "hrrr")

    def test_all_us_cities_within_48h_prefer_hrrr(self):
        """Regression guard: every US city must select HRRR when it returns a value."""
        us_cities = [
            "nyc", "chicago", "miami", "dallas", "seattle",
            "atlanta", "denver", "los-angeles", "san-francisco", "houston", "austin",
        ]
        p_ecmwf, p_hrrr, p_metar = self._patch_all(ecmwf_val=70.0, hrrr_val=72.0)
        for city in us_cities:
            with self.subTest(city=city):
                with p_ecmwf, p_hrrr, p_metar:
                    r = get_best_forecast(city, self.DATE, hours_ahead=24)
                self.assertEqual(
                    r["best_source"], "hrrr",
                    f"{city}: expected best_source='hrrr', got '{r['best_source']}'"
                )

    # --- US + >48h: must fall back to ECMWF ---

    def test_us_city_beyond_48h_falls_back_to_ecmwf(self):
        p_ecmwf, p_hrrr, p_metar = self._patch_all(ecmwf_val=70.0, hrrr_val=None)
        with p_ecmwf, p_hrrr, p_metar:
            r = get_best_forecast("nyc", self.DATE, hours_ahead=72)
        self.assertEqual(r["best_source"], "ecmwf")
        self.assertAlmostEqual(r["best"], 70.0)

    def test_get_hrrr_not_called_when_hours_beyond_48(self):
        with patch("src.weatherbot.forecast.get_ecmwf", return_value={self.DATE: 70.0}), \
             patch("src.weatherbot.forecast.get_hrrr") as mock_hrrr, \
             patch("src.weatherbot.forecast.get_metar", return_value=None):
            mock_hrrr.return_value = {}
            get_best_forecast("nyc", self.DATE, hours_ahead=72)
        mock_hrrr.assert_not_called()

    # --- US + HRRR returns None: fall back to ECMWF ---

    def test_us_city_hrrr_none_falls_back_to_ecmwf(self):
        """If HRRR API succeeds but date is missing from response, use ECMWF."""
        with patch("src.weatherbot.forecast.get_ecmwf", return_value={self.DATE: 70.0}), \
             patch("src.weatherbot.forecast.get_hrrr", return_value={self.DATE: None}), \
             patch("src.weatherbot.forecast.get_metar", return_value=None):
            r = get_best_forecast("nyc", self.DATE, hours_ahead=24)
        self.assertEqual(r["best_source"], "ecmwf")

    # --- Non-US cities: always ECMWF ---

    def test_eu_city_uses_ecmwf_regardless_of_hours(self):
        p_ecmwf, p_hrrr, p_metar = self._patch_all(ecmwf_val=18.0, hrrr_val=None)
        with p_ecmwf, p_hrrr, p_metar:
            r = get_best_forecast("london", self.DATE, hours_ahead=24)
        self.assertEqual(r["best_source"], "ecmwf")

    def test_get_hrrr_not_called_for_eu_city(self):
        with patch("src.weatherbot.forecast.get_ecmwf", return_value={self.DATE: 18.0}), \
             patch("src.weatherbot.forecast.get_hrrr") as mock_hrrr, \
             patch("src.weatherbot.forecast.get_metar", return_value=None):
            get_best_forecast("london", self.DATE, hours_ahead=24)
        mock_hrrr.assert_not_called()

    # --- Both sources missing ---

    def test_both_none_returns_none_source(self):
        p_ecmwf, p_hrrr, p_metar = self._patch_all(ecmwf_val=None, hrrr_val=None)
        with p_ecmwf, p_hrrr, p_metar:
            r = get_best_forecast("nyc", self.DATE, hours_ahead=24)
        self.assertIsNone(r["best"])
        self.assertIsNone(r["best_source"])


# ---------------------------------------------------------------------------
# get_best_forecast — METAR gating
# ---------------------------------------------------------------------------

class TestGetBestForecastMetar(unittest.TestCase):
    DATE = "2026-04-10"

    def test_metar_fetched_when_hours_le_24(self):
        with patch("src.weatherbot.forecast.get_ecmwf", return_value={self.DATE: 70.0}), \
             patch("src.weatherbot.forecast.get_hrrr", return_value={self.DATE: 72.0}), \
             patch("src.weatherbot.forecast.get_metar", return_value=73.5) as mock_metar:
            r = get_best_forecast("nyc", self.DATE, hours_ahead=12)
        mock_metar.assert_called_once_with("nyc")
        self.assertAlmostEqual(r["metar"], 73.5)

    def test_metar_not_fetched_when_hours_gt_24(self):
        with patch("src.weatherbot.forecast.get_ecmwf", return_value={self.DATE: 70.0}), \
             patch("src.weatherbot.forecast.get_hrrr", return_value={self.DATE: 72.0}), \
             patch("src.weatherbot.forecast.get_metar") as mock_metar:
            r = get_best_forecast("nyc", self.DATE, hours_ahead=36)
        mock_metar.assert_not_called()
        self.assertIsNone(r["metar"])

    def test_metar_does_not_affect_best_source(self):
        """METAR is informational only — best and best_source must ignore it."""
        with patch("src.weatherbot.forecast.get_ecmwf", return_value={self.DATE: 70.0}), \
             patch("src.weatherbot.forecast.get_hrrr", return_value={self.DATE: 72.0}), \
             patch("src.weatherbot.forecast.get_metar", return_value=99.9):
            r = get_best_forecast("nyc", self.DATE, hours_ahead=12)
        # best_source must still be "hrrr", not "metar"
        self.assertEqual(r["best_source"], "hrrr")
        self.assertAlmostEqual(r["best"], 72.0)


# ---------------------------------------------------------------------------
# get_best_forecast — return dict shape
# ---------------------------------------------------------------------------

class TestGetBestForecastShape(unittest.TestCase):
    DATE = "2026-04-10"

    def test_all_keys_present(self):
        with patch("src.weatherbot.forecast.get_ecmwf", return_value={self.DATE: 70.0}), \
             patch("src.weatherbot.forecast.get_hrrr", return_value={self.DATE: 72.0}), \
             patch("src.weatherbot.forecast.get_metar", return_value=73.0):
            r = get_best_forecast("nyc", self.DATE, hours_ahead=12)
        self.assertSetEqual(set(r.keys()), {"ecmwf", "hrrr", "metar", "best", "best_source"})

    def test_ecmwf_and_hrrr_values_always_returned(self):
        """Both raw values must be present even when HRRR wins."""
        with patch("src.weatherbot.forecast.get_ecmwf", return_value={self.DATE: 70.0}), \
             patch("src.weatherbot.forecast.get_hrrr", return_value={self.DATE: 72.0}), \
             patch("src.weatherbot.forecast.get_metar", return_value=None):
            r = get_best_forecast("nyc", self.DATE, hours_ahead=24)
        self.assertAlmostEqual(r["ecmwf"], 70.0)
        self.assertAlmostEqual(r["hrrr"], 72.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
