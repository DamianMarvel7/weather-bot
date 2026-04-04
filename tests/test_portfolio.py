"""
Tests for src/weatherbot/portfolio.py

Run with:
    uv run pytest tests/test_portfolio.py -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.weatherbot.portfolio import (
    _resolved_temp_estimate,
    calc_ev,
    calc_kelly,
    _bucket_probability,
    get_probability,
)


# ---------------------------------------------------------------------------
# _resolved_temp_estimate — all decision branches
# ---------------------------------------------------------------------------

class TestResolvedTempEstimate(unittest.TestCase):
    """
    Branch map (priority order):
      A. resolved_outcome exists, non-tail (lo > -999 & hi < 999), narrow (width ≤ 3)
         → return midpoint
      B. resolved_outcome exists, non-tail but WIDE (width > 3)
         → fall through to actual_temp
      C. resolved_outcome is a TAIL bucket ("X or below" / "X or above")
         → fall through to actual_temp
      D. No resolved_outcome → use actual_temp
      E. No resolved_outcome, no actual_temp → last-resort tail bound
         (lo ≤ -999 → return hi; hi ≥ 999 → return lo)
      F. Nothing available → return None
    """

    # --- Branch A: narrow non-tail bucket → midpoint ---

    def test_narrow_1unit_bucket_returns_midpoint(self):
        # "70-71°F" width=1 → midpoint=70.5
        mkt = {"resolved_outcome": "70-71°F", "actual_temp": None}
        self.assertAlmostEqual(_resolved_temp_estimate(mkt), 70.5)

    def test_narrow_2unit_bucket_returns_midpoint(self):
        # "69-71°F" width=2 → midpoint=70.0
        mkt = {"resolved_outcome": "69-71°F", "actual_temp": None}
        self.assertAlmostEqual(_resolved_temp_estimate(mkt), 70.0)

    def test_exactly_3unit_bucket_returns_midpoint(self):
        # width=3 is still ≤ 3 → should use midpoint
        mkt = {"resolved_outcome": "68-71°F", "actual_temp": None}
        self.assertAlmostEqual(_resolved_temp_estimate(mkt), 69.5)

    def test_narrow_celsius_bucket_returns_midpoint(self):
        # "20-22°C" width=2 → midpoint=21.0
        mkt = {"resolved_outcome": "20-22°C", "actual_temp": None}
        self.assertAlmostEqual(_resolved_temp_estimate(mkt), 21.0)

    def test_narrow_bucket_prefers_midpoint_over_actual_temp(self):
        # actual_temp present but narrow bucket should still take midpoint
        mkt = {"resolved_outcome": "70-71°F", "actual_temp": 99.0}
        self.assertAlmostEqual(_resolved_temp_estimate(mkt), 70.5)

    # --- Branch B: wide non-tail bucket → fallback to actual_temp ---

    def test_wide_bucket_falls_back_to_actual_temp(self):
        # "65-70°F" width=5 > 3 → skip midpoint, use actual_temp
        mkt = {"resolved_outcome": "65-70°F", "actual_temp": 67.3}
        self.assertAlmostEqual(_resolved_temp_estimate(mkt), 67.3)

    def test_wide_bucket_no_actual_temp_returns_none(self):
        # "65-70°F" width=5, no actual_temp, no tail → None
        mkt = {"resolved_outcome": "65-70°F", "actual_temp": None}
        self.assertIsNone(_resolved_temp_estimate(mkt))

    # --- Branch C: tail bucket → fallback to actual_temp ---

    def test_upper_tail_bucket_uses_actual_temp_when_available(self):
        # "80°F or higher" → tail, hi=999 → skip midpoint, use actual_temp
        mkt = {"resolved_outcome": "80°F or higher", "actual_temp": 83.0}
        self.assertAlmostEqual(_resolved_temp_estimate(mkt), 83.0)

    def test_lower_tail_bucket_uses_actual_temp_when_available(self):
        # "55°F or lower" → tail, lo=-999 → skip midpoint, use actual_temp
        mkt = {"resolved_outcome": "55°F or lower", "actual_temp": 52.0}
        self.assertAlmostEqual(_resolved_temp_estimate(mkt), 52.0)

    # --- Branch D: no resolved_outcome → actual_temp ---

    def test_no_resolved_outcome_returns_actual_temp(self):
        mkt = {"resolved_outcome": None, "actual_temp": 74.5}
        self.assertAlmostEqual(_resolved_temp_estimate(mkt), 74.5)

    def test_missing_resolved_outcome_key_returns_actual_temp(self):
        mkt = {"actual_temp": 74.5}
        self.assertAlmostEqual(_resolved_temp_estimate(mkt), 74.5)

    # --- Branch E: tail bucket, no actual_temp → last-resort bound ---

    def test_upper_tail_no_actual_temp_returns_lo_bound(self):
        # "80°F or higher" → lo=80, hi=999; hi≥999 → return lo=80
        mkt = {"resolved_outcome": "80°F or higher", "actual_temp": None}
        self.assertAlmostEqual(_resolved_temp_estimate(mkt), 80.0)

    def test_lower_tail_no_actual_temp_returns_hi_bound(self):
        # "55°F or lower" → lo=-999, hi=55; lo≤-999 → return hi=55
        mkt = {"resolved_outcome": "55°F or lower", "actual_temp": None}
        self.assertAlmostEqual(_resolved_temp_estimate(mkt), 55.0)

    def test_or_above_alias_no_actual_temp_returns_lo_bound(self):
        # "20°C or above" → lo=20, hi=999 → return 20
        mkt = {"resolved_outcome": "20°C or above", "actual_temp": None}
        self.assertAlmostEqual(_resolved_temp_estimate(mkt), 20.0)

    def test_or_below_alias_no_actual_temp_returns_hi_bound(self):
        # "10°C or below" → lo=-999, hi=10 → return 10
        mkt = {"resolved_outcome": "10°C or below", "actual_temp": None}
        self.assertAlmostEqual(_resolved_temp_estimate(mkt), 10.0)

    # --- Branch F: nothing available → None ---

    def test_no_resolved_no_actual_returns_none(self):
        mkt = {"resolved_outcome": None, "actual_temp": None}
        self.assertIsNone(_resolved_temp_estimate(mkt))

    def test_empty_market_returns_none(self):
        self.assertIsNone(_resolved_temp_estimate({}))

    # --- Edge cases ---

    def test_negative_temperature_range(self):
        # "-5-0°C" width=5 → wide, fallback to actual_temp
        mkt = {"resolved_outcome": "-5-0°C", "actual_temp": -2.5}
        self.assertAlmostEqual(_resolved_temp_estimate(mkt), -2.5)

    def test_negative_temperature_narrow(self):
        # "-2-0°C" width=2 → narrow, midpoint=-1.0
        mkt = {"resolved_outcome": "-2-0°C", "actual_temp": None}
        self.assertAlmostEqual(_resolved_temp_estimate(mkt), -1.0)


# ---------------------------------------------------------------------------
# calc_ev (from portfolio, same logic as weatherbet)
# ---------------------------------------------------------------------------

class TestCalcEV(unittest.TestCase):
    def test_positive_ev(self):
        # p=0.7, price=0.5 → 0.7*1 - 0.3 = 0.4
        self.assertAlmostEqual(calc_ev(0.7, 0.5), 0.4, places=4)

    def test_negative_ev(self):
        self.assertAlmostEqual(calc_ev(0.3, 0.5), -0.4, places=4)

    def test_zero_price_returns_zero(self):
        self.assertEqual(calc_ev(0.7, 0.0), 0.0)

    def test_price_one_returns_zero(self):
        self.assertEqual(calc_ev(0.7, 1.0), 0.0)

    def test_price_above_one_returns_zero(self):
        self.assertEqual(calc_ev(0.7, 1.5), 0.0)


# ---------------------------------------------------------------------------
# calc_kelly
# ---------------------------------------------------------------------------

class TestCalcKelly(unittest.TestCase):
    def test_no_edge_clamped_to_zero(self):
        self.assertEqual(calc_kelly(0.3, 0.5), 0.0)

    def test_result_in_unit_interval(self):
        result = calc_kelly(0.7, 0.4)
        self.assertGreaterEqual(result, 0.0)
        self.assertLessEqual(result, 1.0)

    def test_price_zero_returns_zero(self):
        self.assertEqual(calc_kelly(0.8, 0.0), 0.0)

    def test_price_one_returns_zero(self):
        self.assertEqual(calc_kelly(0.8, 1.0), 0.0)


# ---------------------------------------------------------------------------
# _bucket_probability (internal normal-distribution math)
# ---------------------------------------------------------------------------

class TestBucketProbability(unittest.TestCase):
    def test_symmetric_bucket_around_mean(self):
        # P(-1 ≤ N(0,1) ≤ 1) ≈ 0.6827
        p = _bucket_probability(-1.0, 1.0, 0.0, 1.0)
        self.assertAlmostEqual(p, 0.6827, places=3)

    def test_open_ended_upper(self):
        # P(X ≥ μ) ≈ 0.5
        p = _bucket_probability(0.0, 999.0, 0.0, 1.0)
        self.assertAlmostEqual(p, 0.5, places=3)

    def test_open_ended_lower(self):
        p = _bucket_probability(-999.0, 0.0, 0.0, 1.0)
        self.assertAlmostEqual(p, 0.5, places=3)

    def test_result_clamped_to_unit_interval(self):
        p = _bucket_probability(100.0, 110.0, 0.0, 1.0)
        self.assertGreaterEqual(p, 0.0)
        self.assertLessEqual(p, 1.0)

    def test_distant_bucket_near_zero(self):
        p = _bucket_probability(100.0, 110.0, 0.0, 1.0)
        self.assertAlmostEqual(p, 0.0, places=4)


# ---------------------------------------------------------------------------
# get_probability (integration: calibration → sigma/bias → bucket prob)
# ---------------------------------------------------------------------------

class TestGetProbability(unittest.TestCase):
    def test_no_calibration_still_returns_probability(self):
        p = get_probability("nyc", 70.0, 71.0, 70.5, "ecmwf", {})
        self.assertGreater(p, 0.0)
        self.assertLess(p, 1.0)

    def test_calibrated_mae_widens_sigma(self):
        # Higher MAE → larger sigma → flatter distribution → lower peak-bucket probability
        high_mae = {"nyc_ecmwf": {"mae": 10.0, "bias": 0.0, "n": 30}}
        low_mae  = {"nyc_ecmwf": {"mae": 1.0,  "bias": 0.0, "n": 30}}
        p_high = get_probability("nyc", 70.0, 71.0, 70.5, "ecmwf", high_mae)
        p_low  = get_probability("nyc", 70.0, 71.0, 70.5, "ecmwf", low_mae)
        self.assertLess(p_high, p_low)

    def test_bias_entry_present_returns_valid_probability(self):
        # BIAS_SCALE may be 0.0 in config (bias correction disabled), but the
        # function must still return a probability in [0, 1] regardless.
        warm_bias = {"nyc_ecmwf": {"mae": 3.0, "bias": 5.0, "n": 30}}
        p = get_probability("nyc", 73.0, 74.0, 70.0, "ecmwf", warm_bias)
        self.assertGreaterEqual(p, 0.0)
        self.assertLessEqual(p, 1.0)

    def test_open_ended_high_bucket_with_forecast_above_bound(self):
        # Forecast well above the lower bound of open-ended bucket → high probability
        p = get_probability("nyc", 80.0, 999.0, 90.0, "ecmwf", {})
        self.assertGreater(p, 0.5)

    def test_celsius_city_uses_celsius_default_sigma(self):
        # london uses °C defaults (2.5 sigma); result should still be a valid probability
        p = get_probability("london", 20.0, 21.0, 20.5, "ecmwf", {})
        self.assertGreater(p, 0.0)
        self.assertLess(p, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
