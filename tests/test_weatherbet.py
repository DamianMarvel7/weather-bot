"""
Tests for src/weatherbot/weatherbet.py

Run with:
    uv run pytest tests/test_weatherbet.py -v
"""

import json
import math
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# Make the project root importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src.weatherbot.weatherbet as wb


# ---------------------------------------------------------------------------
# Part 4: EV and Kelly
# ---------------------------------------------------------------------------

class TestCalcEV(unittest.TestCase):
    def test_positive_ev(self):
        # p=0.7, price=0.5 → EV = 0.7*(1/0.5 - 1) - 0.3 = 0.7*1 - 0.3 = 0.4
        self.assertAlmostEqual(wb.calc_ev(0.7, 0.5), 0.4, places=4)

    def test_negative_ev(self):
        # p=0.3, price=0.5 → EV = 0.3*1 - 0.7 = -0.4
        self.assertAlmostEqual(wb.calc_ev(0.3, 0.5), -0.4, places=4)

    def test_zero_ev(self):
        # p=0.5, price=0.5 → EV = 0.5*1 - 0.5 = 0.0
        self.assertAlmostEqual(wb.calc_ev(0.5, 0.5), 0.0, places=4)

    def test_price_zero_returns_zero(self):
        self.assertEqual(wb.calc_ev(0.7, 0.0), 0.0)

    def test_price_one_returns_zero(self):
        self.assertEqual(wb.calc_ev(0.7, 1.0), 0.0)

    def test_price_above_one_returns_zero(self):
        self.assertEqual(wb.calc_ev(0.7, 1.5), 0.0)

    def test_cheap_price_large_ev(self):
        # p=0.8, price=0.1 → EV = 0.8*9 - 0.2 = 7.0
        self.assertAlmostEqual(wb.calc_ev(0.8, 0.1), 7.0, places=4)


class TestCalcKelly(unittest.TestCase):
    def test_no_edge_returns_zero(self):
        # p=0.3, price=0.5 → negative kelly → clamped to 0
        self.assertEqual(wb.calc_kelly(0.3, 0.5), 0.0)

    def test_positive_kelly_capped_by_fraction(self):
        # p=0.9, price=0.5 → b=1, f=(0.9 - 0.1)/1 = 0.8 → *KELLY_FRACTION
        expected = min(0.8 * wb.KELLY_FRACTION, 1.0)
        self.assertAlmostEqual(wb.calc_kelly(0.9, 0.5), expected, places=6)

    def test_price_zero_returns_zero(self):
        self.assertEqual(wb.calc_kelly(0.8, 0.0), 0.0)

    def test_price_one_returns_zero(self):
        self.assertEqual(wb.calc_kelly(0.8, 1.0), 0.0)

    def test_result_between_zero_and_one(self):
        result = wb.calc_kelly(0.7, 0.4)
        self.assertGreaterEqual(result, 0.0)
        self.assertLessEqual(result, 1.0)


# ---------------------------------------------------------------------------
# Part 6: parse_bucket_bounds
# ---------------------------------------------------------------------------

class TestParseBucketBounds(unittest.TestCase):
    def test_range_fahrenheit(self):
        self.assertEqual(wb.parse_bucket_bounds("56-57°F"), (56.0, 57.0))

    def test_range_celsius(self):
        self.assertEqual(wb.parse_bucket_bounds("10-15°C"), (10.0, 15.0))

    def test_or_higher(self):
        self.assertEqual(wb.parse_bucket_bounds("58°F or higher"), (58.0, 999.0))

    def test_or_above(self):
        self.assertEqual(wb.parse_bucket_bounds("20°C or above"), (20.0, 999.0))

    def test_or_lower(self):
        self.assertEqual(wb.parse_bucket_bounds("33°C or lower"), (-999.0, 33.0))

    def test_or_below(self):
        self.assertEqual(wb.parse_bucket_bounds("73°F or below"), (-999.0, 73.0))

    def test_single_value(self):
        lo, hi = wb.parse_bucket_bounds("34°C")
        self.assertAlmostEqual(lo, 33.5)
        self.assertAlmostEqual(hi, 34.5)

    def test_to_format(self):
        lo, hi = wb.parse_bucket_bounds("40 to 45")
        self.assertEqual(lo, 40.0)
        self.assertEqual(hi, 45.0)

    def test_above_keyword(self):
        lo, hi = wb.parse_bucket_bounds("above 50")
        self.assertEqual(lo, 50.0)
        self.assertEqual(hi, 999.0)

    def test_below_keyword(self):
        lo, hi = wb.parse_bucket_bounds("below 32")
        self.assertEqual(lo, -999.0)
        self.assertEqual(hi, 32.0)

    def test_negative_range(self):
        lo, hi = wb.parse_bucket_bounds("-5-0°C")
        self.assertEqual(lo, -5.0)
        self.assertEqual(hi, 0.0)


# ---------------------------------------------------------------------------
# Part 6: find_matching_bucket
# ---------------------------------------------------------------------------

class TestFindMatchingBucket(unittest.TestCase):
    def setUp(self):
        self.outcomes = [
            {"label": "56-57°F", "lo": 56.0, "hi": 57.0, "bid": 0.3, "ask": 0.35, "token_id": "A"},
            {"label": "58-59°F", "lo": 58.0, "hi": 59.0, "bid": 0.4, "ask": 0.45, "token_id": "B"},
            {"label": "60°F or higher", "lo": 60.0, "hi": 999.0, "bid": 0.2, "ask": 0.25, "token_id": "C"},
        ]

    def test_match_first_bucket(self):
        result = wb.find_matching_bucket(self.outcomes, 56.5)
        self.assertEqual(result["token_id"], "A")

    def test_match_second_bucket(self):
        result = wb.find_matching_bucket(self.outcomes, 58.0)
        self.assertEqual(result["token_id"], "B")

    def test_match_open_ended_high(self):
        result = wb.find_matching_bucket(self.outcomes, 75.0)
        self.assertEqual(result["token_id"], "C")

    def test_no_match_gap(self):
        # temp 57.5 falls between 57 and 58 → no match
        result = wb.find_matching_bucket(self.outcomes, 57.5)
        self.assertIsNone(result)

    def test_empty_outcomes(self):
        result = wb.find_matching_bucket([], 60.0)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Part 7: Calibration math
# ---------------------------------------------------------------------------

class TestNormalCDF(unittest.TestCase):
    def test_at_mean(self):
        # CDF at mean should be 0.5
        self.assertAlmostEqual(wb._normal_cdf(0.0, 0.0, 1.0), 0.5, places=6)

    def test_far_right(self):
        # CDF at mean + 10 sigma ≈ 1.0
        self.assertAlmostEqual(wb._normal_cdf(10.0, 0.0, 1.0), 1.0, places=4)

    def test_far_left(self):
        # CDF at mean - 10 sigma ≈ 0.0
        self.assertAlmostEqual(wb._normal_cdf(-10.0, 0.0, 1.0), 0.0, places=4)

    def test_one_sigma_right(self):
        # P(X ≤ μ + σ) ≈ 0.8413
        self.assertAlmostEqual(wb._normal_cdf(1.0, 0.0, 1.0), 0.8413, places=3)


class TestBucketProbability(unittest.TestCase):
    def test_symmetric_bucket_around_mean(self):
        # P(-1 ≤ N(0,1) ≤ 1) ≈ 0.6827
        p = wb.bucket_probability(-1.0, 1.0, 0.0, 1.0)
        self.assertAlmostEqual(p, 0.6827, places=3)

    def test_open_ended_high_bucket(self):
        # P(X ≥ μ) ≈ 0.5 for open-ended upper bucket
        p = wb.bucket_probability(0.0, 999.0, 0.0, 1.0)
        self.assertAlmostEqual(p, 0.5, places=3)

    def test_open_ended_low_bucket(self):
        # P(X ≤ μ) ≈ 0.5 for open-ended lower bucket
        p = wb.bucket_probability(-999.0, 0.0, 0.0, 1.0)
        self.assertAlmostEqual(p, 0.5, places=3)

    def test_result_between_zero_and_one(self):
        p = wb.bucket_probability(70.0, 80.0, 75.0, 3.0)
        self.assertGreaterEqual(p, 0.0)
        self.assertLessEqual(p, 1.0)

    def test_impossible_bucket(self):
        # Bucket very far from forecast → near zero
        p = wb.bucket_probability(100.0, 110.0, 0.0, 1.0)
        self.assertAlmostEqual(p, 0.0, places=4)


class TestGetSigma(unittest.TestCase):
    def test_calibrated_value(self):
        calib = {"nyc_ecmwf": {"mae": 2.5, "n": 50}}
        self.assertEqual(wb.get_sigma("nyc", "ecmwf", calib), 2.5)

    def test_default_fahrenheit(self):
        # No calibration → default 3.0 for US cities (°F)
        self.assertEqual(wb.get_sigma("nyc", "ecmwf", {}), 3.0)

    def test_default_celsius(self):
        # No calibration → default 1.5 for non-US cities (°C)
        self.assertEqual(wb.get_sigma("london", "ecmwf", {}), 1.5)

    def test_missing_source_uses_default(self):
        calib = {"nyc_hrrr": {"mae": 2.0, "n": 40}}
        # ecmwf not in calib → default
        self.assertEqual(wb.get_sigma("nyc", "ecmwf", calib), 3.0)


# ---------------------------------------------------------------------------
# Part 5: Market data helpers
# ---------------------------------------------------------------------------

class TestMarketPath(unittest.TestCase):
    def test_path_format(self):
        path = wb._market_path("nyc", "2026-03-24")
        self.assertTrue(path.endswith("nyc_2026-03-24.json"))


class TestNowIso(unittest.TestCase):
    def test_format(self):
        ts = wb._now_iso()
        # Should be parseable and end with Z
        self.assertTrue(ts.endswith("Z"))
        datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")


class TestNewMarket(unittest.TestCase):
    def test_structure(self):
        mkt = wb.new_market("nyc", "2026-03-24", "NYC temp March 24?", 36.0)
        self.assertEqual(mkt["city"], "nyc")
        self.assertEqual(mkt["date"], "2026-03-24")
        self.assertEqual(mkt["status"], "open")
        self.assertIsNone(mkt["position"])
        self.assertEqual(mkt["forecast_snapshots"], [])
        self.assertEqual(mkt["market_snapshots"], [])
        self.assertIn("created_at", mkt)


class TestAppendForecastSnapshot(unittest.TestCase):
    def _make_market(self):
        return wb.new_market("nyc", "2026-03-24", "NYC temp?", 36.0)

    def test_horizon_d0(self):
        mkt = self._make_market()
        wb.append_forecast_snapshot(mkt, 12.0, {"ecmwf": 72.0, "hrrr": 73.0, "metar": 71.5, "best": 73.0, "best_source": "hrrr"})
        snap = mkt["forecast_snapshots"][0]
        self.assertEqual(snap["horizon"], "D+0")
        self.assertEqual(snap["ecmwf"], 72.0)
        self.assertEqual(snap["hrrr"], 73.0)

    def test_horizon_d1(self):
        mkt = self._make_market()
        wb.append_forecast_snapshot(mkt, 36.0, {"ecmwf": 68.0, "hrrr": None, "metar": None, "best": 68.0, "best_source": "ecmwf"})
        self.assertEqual(mkt["forecast_snapshots"][0]["horizon"], "D+1")

    def test_horizon_d2(self):
        mkt = self._make_market()
        wb.append_forecast_snapshot(mkt, 60.0, {"ecmwf": 65.0, "hrrr": None, "metar": None, "best": 65.0, "best_source": "ecmwf"})
        self.assertEqual(mkt["forecast_snapshots"][0]["horizon"], "D+2")

    def test_multiple_snapshots_accumulate(self):
        mkt = self._make_market()
        for h in [48.0, 24.0, 6.0]:
            wb.append_forecast_snapshot(mkt, h, {"ecmwf": 70.0, "hrrr": None, "metar": None, "best": 70.0, "best_source": "ecmwf"})
        self.assertEqual(len(mkt["forecast_snapshots"]), 3)


class TestAppendMarketSnapshot(unittest.TestCase):
    def test_appends_correctly(self):
        mkt = wb.new_market("nyc", "2026-03-24", "NYC temp?", 36.0)
        wb.append_market_snapshot(mkt, 24.5, "70-71°F", 0.35, 0.38)
        snap = mkt["market_snapshots"][0]
        self.assertEqual(snap["bucket"], "70-71°F")
        self.assertEqual(snap["bid"], 0.35)
        self.assertEqual(snap["ask"], 0.38)
        self.assertAlmostEqual(snap["hours_left"], 24.5)


# ---------------------------------------------------------------------------
# Part 6: Position management
# ---------------------------------------------------------------------------

class TestOpenAndClosePosition(unittest.TestCase):
    def _make_market_and_outcome(self):
        mkt = wb.new_market("nyc", "2026-03-24", "NYC temp?", 36.0)
        outcome = {
            "label": "70-71°F", "lo": 70.0, "hi": 71.0,
            "bid": 0.40, "ask": 0.43, "token_id": "tok123", "volume": 5000.0,
        }
        return mkt, outcome

    def test_open_position_deducts_balance(self):
        mkt, outcome = self._make_market_and_outcome()
        state = {"balance": 1000.0}
        wb.open_position(mkt, outcome, 20.0, 0.12, 0.08, state)
        self.assertAlmostEqual(state["balance"], 980.0)
        self.assertEqual(mkt["position"]["bucket"], "70-71°F")
        self.assertEqual(mkt["position"]["size"], 20.0)

    def test_close_position_adds_proceeds(self):
        mkt, outcome = self._make_market_and_outcome()
        state = {"balance": 980.0}
        wb.open_position(mkt, outcome, 20.0, 0.12, 0.08, state)
        # After open: balance = 980 - 20 = 960
        # Close at bid=0.80 (winner) → proceeds = 20/0.43 * 0.80
        wb.close_position(mkt, 0.80, "resolved", state)
        expected_proceeds = 20.0 / 0.43 * 0.80
        self.assertAlmostEqual(state["balance"], 960.0 + expected_proceeds, places=1)
        self.assertEqual(mkt["position"]["close_reason"], "resolved")

    def test_close_at_loss(self):
        mkt, outcome = self._make_market_and_outcome()
        state = {"balance": 1000.0}
        wb.open_position(mkt, outcome, 20.0, 0.12, 0.08, state)
        wb.close_position(mkt, 0.01, "resolved", state)
        # proceeds ≈ 20/0.43 * 0.01 ≈ 0.47 → pnl ≈ -19.53
        self.assertLess(mkt["pnl"], 0)

    def test_close_on_no_position_is_safe(self):
        mkt = wb.new_market("nyc", "2026-03-24", "NYC temp?", 36.0)
        state = {"balance": 1000.0}
        wb.close_position(mkt, 0.5, "resolved", state)  # should not raise
        self.assertAlmostEqual(state["balance"], 1000.0)


# ---------------------------------------------------------------------------
# Part 6: Stop logic
# ---------------------------------------------------------------------------

class TestCheckStops(unittest.TestCase):
    def _make_open_market(self, entry_ask=0.40):
        mkt = wb.new_market("nyc", "2026-03-24", "NYC temp?", 36.0)
        mkt["position"] = {
            "bucket": "70-71°F",
            "token_id": "tok",
            "entry_ask": entry_ask,
            "peak_bid": entry_ask,
            "size": 20.0,
            "ev": 0.1,
            "kelly": 0.05,
            "opened_at": wb._now_iso(),
            "close_reason": None,
        }
        return mkt

    def test_no_stop_normal(self):
        mkt = self._make_open_market(0.40)
        result = wb.check_stops(mkt, 0.42, 70.5)
        self.assertIsNone(result)

    def test_stop_loss_triggered(self):
        mkt = self._make_open_market(0.40)
        # bid ≤ 0.40 * 0.80 = 0.32
        result = wb.check_stops(mkt, 0.31, None)
        self.assertEqual(result, "stop_loss")

    def test_trailing_stop_triggered(self):
        mkt = self._make_open_market(0.40)
        # First push peak above 1.20× entry (0.48)
        wb.check_stops(mkt, 0.50, None)
        # Now bid falls back to entry or below
        result = wb.check_stops(mkt, 0.39, None)
        self.assertEqual(result, "trailing_stop")

    def test_forecast_change_triggered(self):
        mkt = self._make_open_market(0.40)
        # bucket "70-71°F", drift buffer=2°F → effective [68, 73]
        # forecast=80 → outside range
        result = wb.check_stops(mkt, 0.42, 80.0)
        self.assertEqual(result, "forecast_change")

    def test_forecast_within_buffer_no_stop(self):
        mkt = self._make_open_market(0.40)
        # bucket "70-71°F", with 2°F buffer → [68, 73]; forecast=69 → within
        result = wb.check_stops(mkt, 0.42, 69.0)
        self.assertIsNone(result)

    def test_no_position_returns_none(self):
        mkt = wb.new_market("nyc", "2026-03-24", "NYC temp?", 36.0)
        result = wb.check_stops(mkt, 0.30, None)
        self.assertIsNone(result)

    def test_peak_updated(self):
        mkt = self._make_open_market(0.40)
        wb.check_stops(mkt, 0.55, None)
        self.assertEqual(mkt["position"]["peak_bid"], 0.55)


# ---------------------------------------------------------------------------
# Part 6: Scan dates
# ---------------------------------------------------------------------------

class TestScanDates(unittest.TestCase):
    def test_returns_three_dates(self):
        dates = wb._scan_dates()
        self.assertEqual(len(dates), 3)

    def test_dates_are_sequential(self):
        dates = wb._scan_dates()
        d0 = datetime.strptime(dates[0], "%Y-%m-%d")
        d1 = datetime.strptime(dates[1], "%Y-%m-%d")
        d2 = datetime.strptime(dates[2], "%Y-%m-%d")
        self.assertEqual((d1 - d0).days, 1)
        self.assertEqual((d2 - d1).days, 1)

    def test_first_date_is_today(self):
        dates = wb._scan_dates()
        today = datetime.now(timezone.utc).date().strftime("%Y-%m-%d")
        self.assertEqual(dates[0], today)


# ---------------------------------------------------------------------------
# Part 5: Save/load market (uses temp dir)
# ---------------------------------------------------------------------------

class TestSaveLoadMarket(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_data_dir = wb.DATA_DIR
            wb.DATA_DIR = tmpdir
            try:
                mkt = wb.new_market("london", "2026-03-24", "London temp?", 24.0)
                wb.save_market(mkt)
                loaded = wb.load_market("london", "2026-03-24")
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded["city"], "london")
                self.assertEqual(loaded["date"], "2026-03-24")
            finally:
                wb.DATA_DIR = orig_data_dir

    def test_load_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_data_dir = wb.DATA_DIR
            wb.DATA_DIR = tmpdir
            try:
                result = wb.load_market("paris", "2026-03-24")
                self.assertIsNone(result)
            finally:
                wb.DATA_DIR = orig_data_dir


# ---------------------------------------------------------------------------
# Part 3: Forecast helpers (mock HTTP)
# ---------------------------------------------------------------------------

class TestGetECMWF(unittest.TestCase):
    @patch("src.weatherbot.weatherbet.requests.get")
    def test_returns_temp_for_requested_dates(self, mock_get):
        mock_get.return_value.json.return_value = {
            "daily": {
                "time": ["2026-03-24", "2026-03-25"],
                "temperature_2m_max": [72.5, 68.0],
            }
        }
        result = wb.get_ecmwf("nyc", ["2026-03-24"])
        self.assertEqual(result["2026-03-24"], 72.5)

    @patch("src.weatherbot.weatherbet.requests.get", side_effect=Exception("network error"))
    def test_returns_none_on_error(self, _):
        result = wb.get_ecmwf("nyc", ["2026-03-24"])
        self.assertIsNone(result["2026-03-24"])


class TestGetHRRR(unittest.TestCase):
    @patch("src.weatherbot.weatherbet.requests.get")
    def test_us_city_returns_data(self, mock_get):
        mock_get.return_value.json.return_value = {
            "daily": {
                "time": ["2026-03-24"],
                "temperature_2m_max": [73.0],
            }
        }
        result = wb.get_hrrr("nyc", ["2026-03-24"])
        self.assertEqual(result["2026-03-24"], 73.0)

    def test_non_us_city_returns_empty(self):
        result = wb.get_hrrr("london", ["2026-03-24"])
        self.assertEqual(result, {})


class TestGetMETAR(unittest.TestCase):
    @patch("src.weatherbot.weatherbet.requests.get")
    def test_fahrenheit_conversion(self, mock_get):
        mock_get.return_value.json.return_value = [{"temp": 20.0}]  # 20°C = 68°F
        result = wb.get_metar("nyc")
        self.assertAlmostEqual(result, 68.0, places=1)

    @patch("src.weatherbot.weatherbet.requests.get")
    def test_celsius_no_conversion(self, mock_get):
        mock_get.return_value.json.return_value = [{"temp": 15.0}]
        result = wb.get_metar("london")
        self.assertAlmostEqual(result, 15.0, places=1)

    @patch("src.weatherbot.weatherbet.requests.get")
    def test_empty_response_returns_none(self, mock_get):
        mock_get.return_value.json.return_value = []
        result = wb.get_metar("nyc")
        self.assertIsNone(result)

    @patch("src.weatherbot.weatherbet.requests.get", side_effect=Exception("timeout"))
    def test_error_returns_none(self, _):
        result = wb.get_metar("nyc")
        self.assertIsNone(result)


class TestGetClobPrices(unittest.TestCase):
    @patch("src.weatherbot.weatherbet.requests.get")
    def test_returns_bid_ask(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "bids": [{"price": "0.38"}],
            "asks": [{"price": "0.41"}],
        }
        mock_get.return_value = mock_resp
        bid, ask = wb.get_clob_prices("some_token")
        self.assertAlmostEqual(bid, 0.38)
        self.assertAlmostEqual(ask, 0.41)

    @patch("src.weatherbot.weatherbet.requests.get")
    def test_empty_book_returns_none(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"bids": [], "asks": []}
        mock_get.return_value = mock_resp
        bid, ask = wb.get_clob_prices("some_token")
        self.assertIsNone(bid)
        self.assertIsNone(ask)

    @patch("src.weatherbot.weatherbet.requests.get", side_effect=Exception("error"))
    def test_error_returns_none_none(self, _):
        bid, ask = wb.get_clob_prices("some_token")
        self.assertIsNone(bid)
        self.assertIsNone(ask)


if __name__ == "__main__":
    unittest.main(verbosity=2)
