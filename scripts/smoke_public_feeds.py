"""Bounded connectivity test, deliberately configured to create no orders."""
import asyncio
from collections import Counter
import json
from pathlib import Path
import time

from polymarket_bot.core import Config
from polymarket_bot.feeds import discover, market_from_gamma, shadow
from polymarket_bot.replay import replay


def main():
    candidates = discover()
    raw = next(item for item in candidates if item.get("acceptingOrders") and not item.get("closed"))
    # A dummy strike is suitable ONLY for connectivity testing. All order edges
    # exceed 100%, which prevents both directional and pairing orders.
    market = market_from_gamma(raw, 1)
    config = Config(maker_edge=2, taker_edge=2, pair_edge=2)
    output = Path("runs") / f"live-smoke-{int(time.time())}"
    spot_feed = "chainlink_twap" if "twap-60s-streams" in raw.get("resolutionSource", "") else "chainlink"
    product = market.slug.split('-')[0].upper() + "-USD"
    report = asyncio.run(shadow(market, config, product, 42, output, spot_feed=spot_feed))
    recorded = [json.loads(line) for line in (output / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    counts = Counter(row["kind"] for row in recorded)
    assert counts["spot"] >= 3, counts
    assert all(0 < float(row['price']) < 10000000 for row in recorded if row['kind']=='spot')
    tokens = {row["token"] for row in recorded if row["kind"] == "book"}
    assert {market.up_token, market.down_token} <= tokens, tokens
    assert report["portfolio"]["fills"] == 0
    assert not any(row["action"] == "order_created" for row in report["actions"])
    assert not report["feed_errors"], report["feed_errors"]
    replayed = replay(output / "events.jsonl")
    assert replayed["portfolio"] == report["portfolio"]
    assert replayed["actions"] == report["actions"]
    summary = dict(passed=True, seconds=42, counts=dict(counts), output=str(output),
                   market=market.slug, spot_feed=spot_feed, paper_only=True, orders_created=0,
                   note="Connectivity and deterministic replay only; no performance inference.")
    (output / "smoke.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
