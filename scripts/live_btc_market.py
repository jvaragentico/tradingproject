"""Read a current BTC 5m market and Polymarket's live opening reference.

No wallet access or orders. If the opening reference cannot be validated, fail
closed instead of substituting a spot quote or an estimated strike.
"""

from datetime import datetime, timezone
import json
import math
import sys
import time
from urllib.parse import urlencode

from polymarket_bot.feeds import GAMMA, get_json, market_from_gamma


OPEN_PRICE_API = "https://polymarket.com/api/crypto/crypto-price"
RESOLUTION_SOURCE = "https://data.chain.link/streams/btc-usd-twap-60s-streams"


def current_market():
    now = time.time()
    elapsed = now % 300
    # Give the opening reference time to appear, and leave at least 150 seconds
    # for startup, warmup, trading and cancellation. Wait for the next interval
    # when the current one is already too old.
    delay = (30 - elapsed) if elapsed < 30 else (330 - elapsed if elapsed > 140 else 0)
    if delay > 0:
        print(f"Waiting {math.ceil(delay)} seconds for a fresh BTC market...", file=sys.stderr, flush=True)
        time.sleep(delay)
    now = time.time()
    start = int(now) // 300 * 300
    slug = f"btc-updown-5m-{start}"
    raw = get_json(f"{GAMMA}/markets/slug/{slug}")
    if raw.get("slug") != slug or raw.get("resolutionSource") != RESOLUTION_SOURCE:
        raise ValueError("Market identity or TWAP resolution source changed")
    config = raw.get("cryptoMarketConfig") or {}
    if config.get("asset") != "btc" or config.get("duration") != "5m" or config.get("twapEnabled") is not True or config.get("twapLookbackSeconds") != 60:
        raise ValueError("BTC 5m TWAP configuration is missing or changed")
    if not 30 <= now - start < 145:
        raise ValueError("Market is not in the safe startup window; run again")
    event_start = datetime.fromtimestamp(start, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    event_end = datetime.fromtimestamp(start + 300, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if raw.get("eventStartTime") != event_start or raw.get("endDate") != event_end:
        raise ValueError("Gamma market interval does not match the requested price interval")
    url = OPEN_PRICE_API + "?" + urlencode(dict(symbol="BTC", eventStartTime=event_start,
                                               variant="fiveminute", endDate=event_end))
    observations = []
    for attempt in range(2):
        quote = get_json(url)
        price, stamp = quote.get("openPrice"), quote.get("timestamp")
        if (isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0 or
                isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or
                not start * 1000 <= stamp <= (time.time() + 5) * 1000):
            raise ValueError("Live Polymarket opening reference is unavailable or stale")
        observations.append(price)
        if attempt == 0:
            time.sleep(0.25)
    if abs(observations[0] - observations[1]) > 0.005:
        raise ValueError("Live Polymarket opening reference changed between checks")
    market = market_from_gamma(raw, observations[1])
    if market.slug != slug or time.time() >= market.end - 155:
        raise ValueError("Market changed or is too close to expiry")
    return dict(slug=slug, strike=observations[1], conditionId=raw["conditionId"],
                source="Polymarket live crypto-price openPrice, BTC 5m TWAP market",
                observedAt=datetime.now(timezone.utc).isoformat(timespec="seconds"))


if __name__ == "__main__":
    try:
        print(json.dumps(current_market()), flush=True)
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"No live BTC market was approved: {error}", file=sys.stderr)
        sys.exit(1)
