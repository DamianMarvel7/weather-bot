"""
Tests for src/weatherbot/polymarket.py

Run with:
    uv run pytest tests/test_polymarket.py -v
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.weatherbot.polymarket import (
    _gamma_get,
    check_gamma_resolved,
    find_matching_bucket,
    get_clob_prices,
    get_polymarket_event,
    get_polymarket_historical_resolved,
    parse_bucket_bounds,
)


# ---------------------------------------------------------------------------
# parse_bucket_bounds — pure function, no mocking required
# ---------------------------------------------------------------------------

class TestParseBucketBounds(unittest.TestCase):
    """All label formats observed on Polymarket weather markets."""

    # --- Range formats ---

    def test_fahrenheit_range(self):
        self.assertEqual(parse_bucket_bounds("56-57°F"), (56.0, 57.0))

    def test_fahrenheit_range_with_unit_on_both(self):
        self.assertEqual(parse_bucket_bounds("40°F-45°F"), (40.0, 45.0))

    def test_celsius_range(self):
        self.assertEqual(parse_bucket_bounds("20-22°C"), (20.0, 22.0))

    def test_to_range_no_unit(self):
        self.assertEqual(parse_bucket_bounds("40 to 45"), (40.0, 45.0))

    def test_negative_to_positive_range(self):
        lo, hi = parse_bucket_bounds("-5-0°C")
        self.assertAlmostEqual(lo, -5.0)
        self.assertAlmostEqual(hi, 0.0)

    def test_negative_range(self):
        lo, hi = parse_bucket_bounds("-10--5°C")
        self.assertAlmostEqual(lo, -10.0)
        self.assertAlmostEqual(hi, -5.0)

    # --- "X or higher / above" tail formats ---

    def test_or_higher(self):
        self.assertEqual(parse_bucket_bounds("58°F or higher"), (58.0, 999.0))

    def test_or_above(self):
        self.assertEqual(parse_bucket_bounds("20°C or above"), (20.0, 999.0))

    def test_above_prefix(self):
        lo, hi = parse_bucket_bounds("above 33")
        self.assertAlmostEqual(lo, 33.0)
        self.assertEqual(hi, 999.0)

    def test_over_prefix(self):
        lo, hi = parse_bucket_bounds("over 100")
        self.assertAlmostEqual(lo, 100.0)
        self.assertEqual(hi, 999.0)

    # --- "X or lower / below" tail formats ---

    def test_or_below(self):
        self.assertEqual(parse_bucket_bounds("33°C or below"), (-999.0, 33.0))

    def test_or_lower(self):
        self.assertEqual(parse_bucket_bounds("10°F or lower"), (-999.0, 10.0))

    def test_below_prefix(self):
        lo, hi = parse_bucket_bounds("below 0")
        self.assertEqual(lo, -999.0)
        self.assertAlmostEqual(hi, 0.0)

    def test_under_prefix(self):
        lo, hi = parse_bucket_bounds("under 32")
        self.assertEqual(lo, -999.0)
        self.assertAlmostEqual(hi, 32.0)

    # --- Single value: ±0.5 window ---

    def test_single_celsius_value(self):
        self.assertEqual(parse_bucket_bounds("34°C"), (33.5, 34.5))

    def test_single_fahrenheit_value(self):
        self.assertEqual(parse_bucket_bounds("17°F"), (16.5, 17.5))

    def test_single_zero(self):
        self.assertEqual(parse_bucket_bounds("0°C"), (-0.5, 0.5))

    def test_single_negative_value(self):
        self.assertEqual(parse_bucket_bounds("-3°C"), (-3.5, -2.5))

    # --- Whitespace handling ---

    def test_leading_trailing_whitespace(self):
        lo, hi = parse_bucket_bounds("  56-57°F  ")
        self.assertEqual((lo, hi), (56.0, 57.0))

    # --- Fallback ---

    def test_unrecognised_label_returns_full_range(self):
        self.assertEqual(parse_bucket_bounds("something unexpected"), (-999.0, 999.0))

    def test_empty_string_fallback(self):
        self.assertEqual(parse_bucket_bounds(""), (-999.0, 999.0))


# ---------------------------------------------------------------------------
# find_matching_bucket — pure function
# ---------------------------------------------------------------------------

class TestFindMatchingBucket(unittest.TestCase):
    _outcomes = [
        {"label": "low",  "lo": -999.0, "hi": 60.0},
        {"label": "mid",  "lo":  60.0,  "hi": 70.0},
        {"label": "high", "lo":  70.0,  "hi": 999.0},
    ]

    def test_hits_first_bucket(self):
        result = find_matching_bucket(self._outcomes, 55.0)
        self.assertEqual(result["label"], "low")

    def test_hits_middle_bucket(self):
        result = find_matching_bucket(self._outcomes, 65.0)
        self.assertEqual(result["label"], "mid")

    def test_hits_last_bucket(self):
        result = find_matching_bucket(self._outcomes, 80.0)
        self.assertEqual(result["label"], "high")

    def test_boundary_included_in_bucket(self):
        # lo=60 is ≤ 60 ≤ hi=70 → "mid" wins (first match)
        result = find_matching_bucket(self._outcomes, 60.0)
        self.assertIsNotNone(result)

    def test_no_match_returns_none(self):
        # None of these buckets cover 50 when lo>50 for all
        narrow = [{"label": "x", "lo": 60.0, "hi": 70.0}]
        self.assertIsNone(find_matching_bucket(narrow, 50.0))

    def test_empty_outcomes_returns_none(self):
        self.assertIsNone(find_matching_bucket([], 70.0))


# ---------------------------------------------------------------------------
# _gamma_get — mocks requests.get
# ---------------------------------------------------------------------------

class TestGammaGet(unittest.TestCase):
    @patch("src.weatherbot.polymarket.requests.get")
    def test_returns_json_on_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"id": "1"}]
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        result = _gamma_get("/events", params={"closed": "true"})
        self.assertEqual(result, [{"id": "1"}])

    @patch("src.weatherbot.polymarket.requests.get")
    def test_returns_none_on_http_error(self, mock_get):
        mock_get.side_effect = Exception("timeout")
        self.assertIsNone(_gamma_get("/events"))

    @patch("src.weatherbot.polymarket.requests.get")
    def test_calls_correct_url(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        _gamma_get("/markets/123")
        called_url = mock_get.call_args[0][0]
        self.assertIn("/markets/123", called_url)


# ---------------------------------------------------------------------------
# get_clob_prices — mocks requests.get
# ---------------------------------------------------------------------------

class TestGetClobPrices(unittest.TestCase):
    def _mock_book(self, bids, asks):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"bids": bids, "asks": asks}
        mock_resp.raise_for_status.return_value = None
        return mock_resp

    @patch("src.weatherbot.polymarket.requests.get")
    def test_returns_best_bid_and_ask(self, mock_get):
        # /book returns bids ascending, asks descending — best at end
        mock_get.return_value = self._mock_book(
            bids=[{"price": "0.40"}, {"price": "0.45"}],
            asks=[{"price": "0.55"}, {"price": "0.50"}],
        )
        bid, ask = get_clob_prices("token123")
        self.assertAlmostEqual(bid, 0.45)
        self.assertAlmostEqual(ask, 0.50)

    @patch("src.weatherbot.polymarket.requests.get")
    def test_empty_bids_returns_none_bid(self, mock_get):
        mock_get.return_value = self._mock_book(
            bids=[],
            asks=[{"price": "0.52"}],
        )
        bid, ask = get_clob_prices("token123")
        self.assertIsNone(bid)
        self.assertAlmostEqual(ask, 0.52)

    @patch("src.weatherbot.polymarket.requests.get")
    def test_empty_asks_returns_none_ask(self, mock_get):
        mock_get.return_value = self._mock_book(
            bids=[{"price": "0.48"}],
            asks=[],
        )
        bid, ask = get_clob_prices("token123")
        self.assertAlmostEqual(bid, 0.48)
        self.assertIsNone(ask)

    @patch("src.weatherbot.polymarket.requests.get")
    def test_request_exception_returns_none_tuple(self, mock_get):
        mock_get.side_effect = Exception("network error")
        bid, ask = get_clob_prices("token123")
        self.assertIsNone(bid)
        self.assertIsNone(ask)

    @patch("src.weatherbot.polymarket.requests.get")
    def test_both_sides_empty_returns_none_tuple(self, mock_get):
        mock_get.return_value = self._mock_book(bids=[], asks=[])
        bid, ask = get_clob_prices("token123")
        self.assertIsNone(bid)
        self.assertIsNone(ask)


# ---------------------------------------------------------------------------
# check_gamma_resolved — mocks _gamma_get
# ---------------------------------------------------------------------------

class TestCheckGammaResolved(unittest.TestCase):
    @patch("src.weatherbot.polymarket._gamma_get")
    def test_yes_outcome_returns_true(self, mock_gamma):
        mock_gamma.return_value = {
            "closed": True,
            "outcomePrices": json.dumps(["0.97", "0.03"]),
        }
        self.assertTrue(check_gamma_resolved("mkt1"))

    @patch("src.weatherbot.polymarket._gamma_get")
    def test_no_outcome_returns_false(self, mock_gamma):
        mock_gamma.return_value = {
            "closed": True,
            "outcomePrices": json.dumps(["0.02", "0.98"]),
        }
        self.assertFalse(check_gamma_resolved("mkt1"))

    @patch("src.weatherbot.polymarket._gamma_get")
    def test_market_not_closed_returns_none(self, mock_gamma):
        mock_gamma.return_value = {
            "closed": False,
            "outcomePrices": json.dumps(["0.97", "0.03"]),
        }
        self.assertIsNone(check_gamma_resolved("mkt1"))

    @patch("src.weatherbot.polymarket._gamma_get")
    def test_ambiguous_price_returns_none(self, mock_gamma):
        mock_gamma.return_value = {
            "closed": True,
            "outcomePrices": json.dumps(["0.50", "0.50"]),
        }
        self.assertIsNone(check_gamma_resolved("mkt1"))

    @patch("src.weatherbot.polymarket._gamma_get")
    def test_api_returns_none_returns_none(self, mock_gamma):
        mock_gamma.return_value = None
        self.assertIsNone(check_gamma_resolved("mkt1"))

    @patch("src.weatherbot.polymarket._gamma_get")
    def test_exactly_at_yes_threshold(self, mock_gamma):
        mock_gamma.return_value = {
            "closed": True,
            "outcomePrices": json.dumps(["0.95", "0.05"]),
        }
        self.assertTrue(check_gamma_resolved("mkt1"))

    @patch("src.weatherbot.polymarket._gamma_get")
    def test_exactly_at_no_threshold(self, mock_gamma):
        mock_gamma.return_value = {
            "closed": True,
            "outcomePrices": json.dumps(["0.05", "0.95"]),
        }
        self.assertFalse(check_gamma_resolved("mkt1"))


# ---------------------------------------------------------------------------
# get_polymarket_historical_resolved — mocks _gamma_get
# ---------------------------------------------------------------------------

class TestGetPolymarketHistoricalResolved(unittest.TestCase):
    def _make_child(self, question, yes_price):
        return {
            "question": question,
            "outcomePrices": json.dumps([str(yes_price), str(1 - yes_price)]),
        }

    @patch("src.weatherbot.polymarket._gamma_get")
    def test_returns_label_and_midpoint_for_resolved_event(self, mock_gamma):
        child = self._make_child(
            "Will the highest temperature in Atlanta be 73-74°F on March 27?", 0.97
        )
        mock_gamma.return_value = [
            {"title": "Atlanta March 27", "markets": [child]}
        ]
        label, midpoint = get_polymarket_historical_resolved("atlanta", "2026-03-27")
        self.assertIsNotNone(label)
        self.assertAlmostEqual(midpoint, 73.5)

    @patch("src.weatherbot.polymarket._gamma_get")
    def test_returns_none_when_no_winner_settled(self, mock_gamma):
        child = self._make_child(
            "Will the highest temperature in Atlanta be 73-74°F on March 27?", 0.50
        )
        mock_gamma.return_value = [
            {"title": "Atlanta March 27", "markets": [child]}
        ]
        label, midpoint = get_polymarket_historical_resolved("atlanta", "2026-03-27")
        self.assertIsNone(label)
        self.assertIsNone(midpoint)

    @patch("src.weatherbot.polymarket._gamma_get")
    def test_returns_none_when_city_not_found(self, mock_gamma):
        child = self._make_child(
            "Will the highest temperature in Chicago be 50-51°F on March 27?", 0.97
        )
        mock_gamma.return_value = [
            {"title": "Chicago March 27", "markets": [child]}
        ]
        label, midpoint = get_polymarket_historical_resolved("atlanta", "2026-03-27")
        self.assertIsNone(label)
        self.assertIsNone(midpoint)

    @patch("src.weatherbot.polymarket._gamma_get")
    def test_returns_none_when_api_returns_none(self, mock_gamma):
        mock_gamma.return_value = None
        label, midpoint = get_polymarket_historical_resolved("atlanta", "2026-03-27")
        self.assertIsNone(label)
        self.assertIsNone(midpoint)

    @patch("src.weatherbot.polymarket._gamma_get")
    def test_upper_tail_midpoint_uses_lo_bound(self, mock_gamma):
        # "80°F or higher" → lo=80, hi=999 → midpoint=80.0
        child = self._make_child(
            "Will the highest temperature in Atlanta be 80°F or higher on March 27?", 0.97
        )
        mock_gamma.return_value = [
            {"title": "Atlanta March 27", "markets": [child]}
        ]
        label, midpoint = get_polymarket_historical_resolved("atlanta", "2026-03-27")
        self.assertAlmostEqual(midpoint, 80.0)

    @patch("src.weatherbot.polymarket._gamma_get")
    def test_lower_tail_midpoint_uses_hi_bound(self, mock_gamma):
        # "55°F or below" → lo=-999, hi=55 → midpoint=55.0
        child = self._make_child(
            "Will the highest temperature in Atlanta be 55°F or below on March 27?", 0.97
        )
        mock_gamma.return_value = [
            {"title": "Atlanta March 27", "markets": [child]}
        ]
        label, midpoint = get_polymarket_historical_resolved("atlanta", "2026-03-27")
        self.assertAlmostEqual(midpoint, 55.0)

    @patch("src.weatherbot.polymarket._gamma_get")
    def test_paginates_when_first_page_full(self, mock_gamma):
        # First call returns 200 events (none matching), second call returns fewer
        no_match_events = [{"title": "Chicago March 27", "markets": []}] * 200
        child = self._make_child(
            "Will the highest temperature in Atlanta be 73-74°F on March 27?", 0.97
        )
        match_events = [{"title": "Atlanta March 27", "markets": [child]}]
        mock_gamma.side_effect = [no_match_events, match_events]

        label, midpoint = get_polymarket_historical_resolved("atlanta", "2026-03-27")
        self.assertIsNotNone(label)
        self.assertEqual(mock_gamma.call_count, 2)


# ---------------------------------------------------------------------------
# get_polymarket_event — mocks _gamma_get and get_clob_prices
# ---------------------------------------------------------------------------

class TestGetPolymarketEvent(unittest.TestCase):
    def _make_event(self, title, end_date, children):
        return {
            "title": title,
            "id": "evt1",
            "endDate": end_date,
            "markets": children,
        }

    def _make_child(self, question, clob_ids, volume=5000, outcome_prices=None):
        return {
            "id": "mkt1",
            "question": question,
            "clobTokenIds": json.dumps(clob_ids),
            "volume": str(volume),
            "outcomePrices": json.dumps(outcome_prices or ["0.50", "0.50"]),
        }

    @patch("src.weatherbot.polymarket.get_clob_prices")
    @patch("src.weatherbot.polymarket._gamma_get")
    def test_returns_event_dict_on_match(self, mock_gamma, mock_clob):
        # 24 hours from now
        from datetime import datetime, timedelta, timezone
        end = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

        child = self._make_child(
            "Will the highest temperature in Atlanta be 73-74°F on April 3?",
            ["tok1"],
        )
        mock_gamma.return_value = [self._make_event("Atlanta April 3", end, [child])]
        mock_clob.return_value = (0.45, 0.50)

        result = get_polymarket_event("atlanta", "2026-04-03")
        self.assertIsNotNone(result)
        self.assertIn("outcomes", result)
        self.assertEqual(len(result["outcomes"]), 1)
        self.assertAlmostEqual(result["outcomes"][0]["ask"], 0.50)

    @patch("src.weatherbot.polymarket._gamma_get")
    def test_returns_none_when_city_not_in_title(self, mock_gamma):
        from datetime import datetime, timedelta, timezone
        end = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

        child = self._make_child(
            "Will the highest temperature in Chicago be 50°F on April 3?", ["tok1"]
        )
        mock_gamma.return_value = [self._make_event("Chicago April 3", end, [child])]

        result = get_polymarket_event("atlanta", "2026-04-03")
        self.assertIsNone(result)

    @patch("src.weatherbot.polymarket._gamma_get")
    def test_returns_none_when_date_not_in_title(self, mock_gamma):
        from datetime import datetime, timedelta, timezone
        end = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

        child = self._make_child(
            "Will the highest temperature in Atlanta be 73°F on April 5?", ["tok1"]
        )
        mock_gamma.return_value = [self._make_event("Atlanta April 5", end, [child])]

        result = get_polymarket_event("atlanta", "2026-04-03")
        self.assertIsNone(result)

    @patch("src.weatherbot.polymarket._gamma_get")
    def test_returns_none_when_hours_left_too_low(self, mock_gamma):
        from datetime import datetime, timedelta, timezone
        # Already closed
        end = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        child = self._make_child(
            "Will the highest temperature in Atlanta be 73°F on April 3?", ["tok1"]
        )
        mock_gamma.return_value = [self._make_event("Atlanta April 3", end, [child])]

        result = get_polymarket_event("atlanta", "2026-04-03")
        self.assertIsNone(result)

    @patch("src.weatherbot.polymarket._gamma_get")
    def test_returns_none_when_hours_left_too_high(self, mock_gamma):
        from datetime import datetime, timedelta, timezone
        # 15 days away — above MAX_HOURS
        end = (datetime.now(timezone.utc) + timedelta(days=15)).isoformat()

        child = self._make_child(
            "Will the highest temperature in Atlanta be 73°F on April 3?", ["tok1"]
        )
        mock_gamma.return_value = [self._make_event("Atlanta April 3", end, [child])]

        result = get_polymarket_event("atlanta", "2026-04-03")
        self.assertIsNone(result)

    @patch("src.weatherbot.polymarket.get_clob_prices")
    @patch("src.weatherbot.polymarket._gamma_get")
    def test_returns_none_when_volume_too_low(self, mock_gamma, mock_clob):
        from datetime import datetime, timedelta, timezone
        end = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

        child = self._make_child(
            "Will the highest temperature in Atlanta be 73°F on April 3?",
            ["tok1"],
            volume=10,  # below MIN_VOLUME
        )
        mock_gamma.return_value = [self._make_event("Atlanta April 3", end, [child])]
        mock_clob.return_value = (0.45, 0.50)

        result = get_polymarket_event("atlanta", "2026-04-03")
        self.assertIsNone(result)

    @patch("src.weatherbot.polymarket._gamma_get")
    def test_returns_none_when_api_returns_none(self, mock_gamma):
        mock_gamma.return_value = None
        result = get_polymarket_event("atlanta", "2026-04-03")
        self.assertIsNone(result)

    @patch("src.weatherbot.polymarket.get_clob_prices")
    @patch("src.weatherbot.polymarket._gamma_get")
    def test_falls_back_to_outcome_prices_when_clob_bid_is_none(self, mock_gamma, mock_clob):
        from datetime import datetime, timedelta, timezone
        end = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

        child = self._make_child(
            "Will the highest temperature in Atlanta be 73-74°F on April 3?",
            ["tok1"],
            outcome_prices=["0.60", "0.40"],
        )
        mock_gamma.return_value = [self._make_event("Atlanta April 3", end, [child])]
        # CLOB returns no bid, no ask
        mock_clob.return_value = (None, None)

        result = get_polymarket_event("atlanta", "2026-04-03")
        # bid should fall back to outcomePrices[0]
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["outcomes"][0]["bid"], 0.60)
        self.assertIsNone(result["outcomes"][0]["ask"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
