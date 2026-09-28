import copy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from scripts.live_btc_market import current_market


START = 1790580000


def market():
    stamp = lambda seconds: datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return dict(slug=f"btc-updown-5m-{START}", resolutionSource="https://data.chain.link/streams/btc-usd-twap-60s-streams",
                cryptoMarketConfig=dict(asset="btc", duration="5m", twapEnabled=True, twapLookbackSeconds=60),
                eventStartTime=stamp(START), endDate=stamp(START + 300), outcomes='["Up","Down"]',
                clobTokenIds='["up","down"]', negRisk=False, enableOrderBook=True, closed=False,
                acceptingOrders=True, feesEnabled=True, feeSchedule=dict(rate=0.07, exponent=1, takerOnly=True),
                orderPriceMinTickSize=0.01, orderMinSize=5, conditionId="condition")


class LiveMarketTests(unittest.TestCase):
    def check(self, responses):
        with patch("scripts.live_btc_market.time.time", return_value=START + 40), \
             patch("scripts.live_btc_market.time.sleep"), \
             patch("scripts.live_btc_market.get_json", side_effect=responses) as fetch:
            result = current_market()
        self.assertEqual(fetch.call_count, 3)
        self.assertIn("eventStartTime=2026-09-28T07%3A20%3A00Z", fetch.call_args_list[1].args[0])
        return result

    def test_accepts_two_stable_live_polymarket_open_prices(self):
        quote = dict(openPrice=83113.898357, timestamp=(START + 40) * 1000)
        self.assertEqual(self.check([market(), quote, quote])["strike"], quote["openPrice"])

    def test_rejects_changing_open_reference(self):
        quote = dict(openPrice=83113.89, timestamp=(START + 40) * 1000)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.check([market(), quote, dict(quote, openPrice=83114.2)])

    def test_rejects_stale_reference_timestamp(self):
        quote = dict(openPrice=83113.89, timestamp=(START - 1) * 1000)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.check([market(), quote, quote])

    def test_rejects_wrong_resolution_source(self):
        wrong = copy.deepcopy(market())
        wrong["resolutionSource"] = "https://other.example/btc"
        with patch("scripts.live_btc_market.time.time", return_value=START + 40), \
             patch("scripts.live_btc_market.get_json", return_value=wrong):
            with self.assertRaisesRegex(ValueError, "resolution source"):
                current_market()


if __name__ == "__main__":
    unittest.main()
