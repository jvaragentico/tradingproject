from pathlib import Path
import unittest
from uuid import uuid4

from polymarket_bot.core import Book, Config, D, Engine, Market, Order, Portfolio, ProbabilityModel
from polymarket_bot.feeds import market_from_gamma, normalize_clob, normalize_coinbase, normalize_chainlink
from polymarket_bot.forensics import analyze_trades
from polymarket_bot.replay import replay, write_demo


def market(**changes):
    values = dict(slug="btc-updown-5m-0", up_token="up", down_token="down", strike=100,
                  start=0, end=300)
    return Market(**(values | changes))


class FixedModel:
    def __init__(self, q):
        self.q = q

    def update(self, *args):
        pass

    def probability(self, *args):
        return self.q


def book(side, ts=100, bid=".44", ask=".48", size="10"):
    return dict(kind="book", ts=ts, token=side, bids=[[bid, size]], asks=[[ask, "100"]])


def prepared(q=.8, **changes):
    engine = Engine(market(), Config(**changes))
    engine.model = FixedModel(q)
    engine.last_decision = float("inf")
    engine.ingest(book("up"))
    engine.ingest(book("down", bid=".45", ask=".49"))
    return engine


class AccountingTests(unittest.TestCase):
    def test_fifo_pairs_include_fees_and_preserve_residual(self):
        p = Portfolio(100)
        p.buy("Up", D(10), D(".4"), D(".1"), 1, "taker")
        p.buy("Up", D(10), D(".6"), D(0), 2, "maker")
        p.buy("Down", D(15), D(".5"), D(0), 3, "maker")
        self.assertEqual(p.pairs, [(D(10), D(".91")), (D(5), D("1.1"))])
        self.assertEqual(p.residual("Up"), D(5))
        self.assertEqual(p.paired, D(15))
        self.assertEqual(p.cash, D("82.4"))
        report = p.report({"Up": Book(), "Down": Book()})
        self.assertEqual(D(report["paired_terminal_pnl"]), D(".4"))
        self.assertEqual(report["inventory_imbalance"], 5 / 35)

    def test_resolution_pays_pairs_plus_winning_residual_once(self):
        p = Portfolio(100)
        p.buy("Up", D(10), D(".4"), D(0), 1, "maker")
        p.buy("Down", D(6), D(".5"), D(0), 2, "maker")
        p.settle("Up")
        self.assertEqual(p.realized, D(3))
        with self.assertRaises(ValueError):
            p.settle("Up")
        with self.assertRaises(ValueError):
            p.buy("Down", D(1), D(".2"), D(0), 3, "maker")

    def test_losing_residual_is_not_hidden_by_profitable_pairs(self):
        p = Portfolio(100)
        p.buy("Up", D(100), D(".5"), D(0), 1, "maker")
        p.buy("Down", D(20), D(".4"), D(0), 2, "maker")
        p.settle("Down")
        self.assertEqual(p.realized, D(-38))

    def test_fee_curve_and_fee_free_makers(self):
        m = market()
        self.assertEqual(m.fee(D(".5"), D(100)), D("1.75000"))
        self.assertEqual(m.fee(D(".3"), D(100)), m.fee(D(".7"), D(100)))
        self.assertEqual(m.fee(D(".5"), D(100), True), D(0))

    def test_invalid_configuration_and_numeric_values(self):
        for values in ({"capital": -1}, {"capital": float("nan")}, {"max_feed_age": 0},
                       {"allow_taker": "false"}, {"model_window": 5}, {"capital": 10}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Config(**values)
        with self.assertRaises(ValueError):
            D("NaN")


class ExecutionTests(unittest.TestCase):
    def order(self, engine, size=10, ready=100.25, price=".44", mode="maker"):
        item = Order("Up", D(price), D(size), ready, 100, mode)
        engine.orders.append(item)
        return item

    def trade(self, ts, size, price=".44", identity=None, side="SELL"):
        return dict(kind="trade", ts=ts, token="up", price=price, size=str(size), side=side, id=identity)

    def test_maker_waits_for_activation_and_queue_consumption(self):
        e = prepared()
        order = self.order(e)
        e.ingest(self.trade(100.1, 100))
        self.assertFalse(e.portfolio.fills)
        e.ingest(self.trade(100.3, 7))
        self.assertEqual(order.queue_ahead, D(3))
        self.assertFalse(e.portfolio.fills)
        e.ingest(self.trade(100.4, 8))
        self.assertEqual(e.portfolio.residual("Up"), D(5))
        self.assertEqual(order.remaining, D(5))

    def test_quote_touch_and_book_deletion_do_not_create_fills(self):
        e = prepared()
        order = self.order(e)
        e.ingest(dict(kind="clock", ts=100.3))
        e.ingest(dict(kind="delta", ts=100.35, token="up", side="BUY", price=".43", size="10"))
        e.ingest(dict(kind="delta", ts=100.4, token="up", side="BUY", price=".44", size="0"))
        e.ingest(self.trade(100.5, 4))
        self.assertEqual(order.queue_ahead, D(6))
        self.assertFalse(e.portfolio.fills)

    def test_cancel_latency_permits_in_flight_fill(self):
        e = prepared()
        order = self.order(e)
        e.ingest(dict(kind="clock", ts=100.3))
        e.cancel()
        e.ingest(self.trade(100.4, 15))
        self.assertEqual(e.portfolio.residual("Up"), D(5))
        e.ingest(self.trade(100.6, 100))
        self.assertEqual(e.portfolio.residual("Up"), D(5))
        self.assertFalse(e.orders)

    def test_duplicate_trade_does_not_double_fill(self):
        e = prepared()
        self.order(e, size=30)
        e.ingest(self.trade(100.3, 15, identity="one"))
        e.ingest(self.trade(100.4, 15, identity="one"))
        self.assertEqual(e.portfolio.residual("Up"), D(5))

    def test_post_only_rejects_cross_at_activation(self):
        e = prepared()
        self.order(e)
        e.ingest(book("up", 100.3, bid=".40", ask=".43"))
        self.assertFalse(e.orders)
        self.assertFalse(e.portfolio.fills)

    def test_wrong_aggressor_and_wrong_price_do_not_fill(self):
        e = prepared()
        self.order(e)
        e.ingest(self.trade(100.3, 100, side="BUY"))
        e.ingest(self.trade(100.4, 100, price=".43"))
        self.assertFalse(e.portfolio.fills)

    def test_stale_source_timestamp_blocks_execution(self):
        e = prepared()
        self.order(e)
        event = book("up", 100.3)
        event["source_ts"] = 80
        e.ingest(event)
        e.ingest(self.trade(100.4, 100))
        self.assertFalse(e.portfolio.fills)

    def test_expiry_blocks_fill_and_resolution_needs_official_event(self):
        e = prepared()
        self.order(e, ready=300)
        e.ingest(book("up", 300))
        e.ingest(book("down", 300))
        e.ingest(self.trade(300, 100))
        self.assertFalse(e.portfolio.fills)
        self.assertFalse(e.portfolio.settled)
        with self.assertRaises(ValueError):
            prepared().ingest(dict(kind="resolution", ts=100, winner="up"))

    def test_taker_walks_depth_at_delayed_arrival(self):
        e = prepared(q=.9, allow_taker=True)
        self.order(e, size=10, price=".55", mode="taker", ready=100.5)
        event = book("up", 100.5)
        event["asks"] = [[".5", "3"], [".55", "4"], [".6", "100"]]
        e.ingest(event)
        self.assertEqual(e.portfolio.residual("Up"), D(7))
        self.assertGreater(e.portfolio.fees, D(0))
        self.assertFalse(e.orders)  # IOC remainder canceled

    def test_taker_rechecks_signal_before_fill(self):
        e = prepared(q=.9, allow_taker=True)
        self.order(e, mode="taker", price=".55")
        e.model.q = .45
        e.ingest(dict(kind="clock", ts=100.3))
        self.assertFalse(e.portfolio.fills)

    def test_disconnect_halts_run_and_clears_orders(self):
        e = prepared()
        self.order(e)
        e.ingest(dict(kind="disconnect", ts=100.1))
        self.assertEqual(e.halted, "feed_disconnected")
        self.assertFalse(e.orders)
        self.assertFalse(e.books["Up"].initialized)

    def test_rejects_out_of_order_observations(self):
        with self.assertRaises(ValueError):
            prepared().ingest(dict(kind="spot", ts=99, price=100))

    def test_tick_changes_apply_only_to_the_affected_token(self):
        e = prepared()
        up = self.order(e)
        down = Order("Down", D(".45"), D(10), 100.25, 100)
        e.orders.append(down)
        e.ingest(dict(kind="tick", ts=100.1, token="up", tick=".001"))
        self.assertEqual(e.ticks["Up"], D(".001"))
        self.assertEqual(e.ticks["Down"], D(".01"))
        self.assertIsNotNone(up.cancel_at)
        self.assertIsNone(down.cancel_at)


class StrategyTests(unittest.TestCase):
    def test_signal_reversal_buys_opposite_and_keeps_original_inventory(self):
        e = prepared(q=.8, max_loss=100)
        e.last_decision = 0
        e.ingest(dict(kind="clock", ts=100.1))
        self.assertEqual(e.orders[0].side, "Up")
        e.ingest(dict(kind="trade", ts=100.4, token="up", price=".44", size="100", side="SELL"))
        up = e.portfolio.residual("Up")
        self.assertGreater(up, 0)
        e.model.q = .2
        e.ingest(dict(kind="clock", ts=100.8))
        self.assertTrue(any(o.side == "Down" for o in e.orders))
        e.ingest(dict(kind="trade", ts=101.1, token="down", price=".45", size="100", side="SELL"))
        self.assertGreater(e.portfolio.paired, 0)
        self.assertGreater(sum(D(f["shares"]) for f in e.portfolio.fills if f["outcome"] == "Up"), 0)
        self.assertTrue(all(f["mode"] == "maker" for f in e.portfolio.fills))

    def test_order_reservations_prevent_cash_and_worst_loss_overshoot(self):
        e = prepared(q=.8, capital=30, max_market_spend=30, max_loss=10, order_dollars=23.59)
        e.last_decision = 0
        e.ingest(dict(kind="clock", ts=100.1))
        reserved = sum(o.price * o.remaining for o in e.orders)
        self.assertLessEqual(reserved, D(10))
        e.ingest(dict(kind="trade", ts=100.4, token="up", price=".44", size="100", side="SELL"))
        self.assertLessEqual(e.portfolio.initial - e.portfolio.cash - e.portfolio.paired, D(10))
        self.assertFalse(e.orders)  # remaining budget is smaller than minimum order size

    def test_net_share_cap_and_market_budget(self):
        e = prepared(q=.99, max_net_shares=8, max_market_spend=10)
        e.last_decision = 0
        e.ingest(dict(kind="clock", ts=100.1))
        self.assertLessEqual(sum(o.remaining for o in e.orders), D(8))
        e.ingest(dict(kind="trade", ts=100.4, token="up", price=".44", size="100", side="SELL"))
        self.assertLessEqual(e.portfolio.spent, D(10))
        self.assertLessEqual(e.portfolio.net, D(8))

    def test_model_warmup_direction_and_stale_spot(self):
        model = ProbabilityModel(Config(warmup_seconds=5))
        self.assertIsNone(model.probability(0, market()))
        for ts in range(6):
            model.update(ts, 100.01 + ts * .01, ts)
        self.assertGreater(model.probability(5, market()), .5)
        self.assertIsNone(model.probability(11, market()))

    def test_old_spot_messages_do_not_replace_newer_prices_or_warm_model(self):
        model = ProbabilityModel(Config(warmup_seconds=5))
        model.update(100, 100, 100)
        model.update(101, 200, 99)
        model.update(102, 200, 100)
        self.assertEqual(len(model.samples), 1)
        self.assertEqual(model.source_ts, 100)
        self.assertIsNone(model.probability(102, market()))

    def test_demo_is_deterministic_and_has_both_sides(self):
        directory = Path.cwd() / "runs" / ("test-" + uuid4().hex)
        directory.mkdir(parents=True)
        path = directory / "events.jsonl"
        try:
            write_demo(path)
            first, second = replay(path), replay(path)
        finally:
            path.unlink(missing_ok=True)
            directory.rmdir()
        self.assertEqual(first, second)
        self.assertTrue(first["portfolio"]["resolved"])
        self.assertGreater(D(first["portfolio"]["paired_shares"]), 0)
        self.assertEqual({f["outcome"] for f in first["fills"]}, {"Up", "Down"})
        self.assertIn("synthetic", first["source"])


class AdapterTests(unittest.TestCase):
    def test_normalizes_snapshot_delta_trade_and_resolution(self):
        frames = [dict(event_type="book", asset_id="up", timestamp="100000", bids=[dict(price=".4", size="10")], asks=[]),
                  dict(event_type="price_change", timestamp="100001", price_changes=[dict(asset_id="up", price=".4", size="0", side="BUY")]),
                  dict(event_type="last_trade_price", timestamp="100002", asset_id="up", price=".4", size="2", side="SELL", transaction_hash="x"),
                  dict(event_type="market_resolved", timestamp="300000", winning_asset_id="down")]
        events = normalize_clob(frames, 301)
        self.assertEqual([e["kind"] for e in events], ["book", "delta", "trade", "resolution"])
        self.assertEqual(events[0]["source_ts"], 100)
        self.assertTrue(all(e["ts"] == 301 for e in events))

    def test_coinbase_product_and_timestamp(self):
        message = dict(type="ticker", product_id="BTC-USD", price="100", time="1970-01-01T00:01:40Z")
        self.assertEqual(normalize_coinbase(message, 100.1, "BTC-USD")[0]["source_ts"], 100)
        self.assertFalse(normalize_coinbase(message, 100.1, "ETH-USD"))

    def test_chainlink_ignores_historical_dump_and_other_symbols(self):
        message = dict(topic="crypto_prices_chainlink", type="update",
                       payload=dict(symbol="btc/usd", timestamp=100000, value=100))
        events = normalize_chainlink(message, 100.1, "btc/usd")
        self.assertEqual(events[0]["source_ts"], 100)
        self.assertEqual(events[0]["feed"], "polymarket_chainlink")
        self.assertFalse(normalize_chainlink(message, 100.1, "eth/usd"))
        message["payload"] = dict(symbol="btc/usd", data=[dict(timestamp=100000, value=100)])
        self.assertFalse(normalize_chainlink(message, 100.1, "btc/usd"))

    def test_delayed_old_trade_cannot_fill_a_new_quote(self):
        e = prepared()
        e.orders.append(Order("Up", D(".44"), D(10), 100.25, 100))
        e.ingest(dict(kind="clock", ts=100.3))
        e.ingest(dict(kind="trade", ts=100.4, source_ts=100.1, token="up", price=".44", size="100", side="SELL"))
        self.assertFalse(e.portfolio.fills)

    def test_gamma_maps_reversed_outcomes_and_rejects_unknown_fees(self):
        raw = dict(slug="btc-updown-5m-0", outcomes='["Down","Up"]', clobTokenIds='["down","up"]',
                   endDate="1970-01-01T00:05:00Z", enableOrderBook=True, acceptingOrders=True, closed=False,
                   feesEnabled=True, feeSchedule=dict(rate=.07, exponent=1, takerOnly=True),
                   orderPriceMinTickSize=.01, orderMinSize=5)
        m = market_from_gamma(raw, 100)
        self.assertEqual(m.up_token, "up")
        self.assertEqual(m.start, 0)
        raw.pop("feeSchedule")
        with self.assertRaises(ValueError):
            market_from_gamma(raw, 100)

    def test_forensic_statistics_are_gross_sample_not_wallet_pnl(self):
        rows = [dict(slug="btc-updown-5m-0", outcome="Up", side="BUY", size=10, price=.4, timestamp=1),
                dict(slug="btc-updown-5m-0", outcome="Down", side="BUY", size=5, price=.5, timestamp=2),
                dict(slug="btc-updown-5m-0", outcome="Up", side="SELL", size=2, price=.6, timestamp=3),
                dict(slug="politics", outcome="Yes", side="BUY", size=100, price=.5, timestamp=4)]
        report = analyze_trades(rows)
        self.assertEqual(report["included_crypto_trades"], 3)
        self.assertEqual(report["bought_both_fraction"], 1)
        self.assertAlmostEqual(report["median_gross_buy_vwap_pair_cost"], .9)
        self.assertIsNone(report["audited_pnl"])


if __name__ == "__main__":
    unittest.main()
