"""Observed wallet trade statistics, explicitly separate from audited wallet PnL."""

from collections import defaultdict
import json
import math
from pathlib import Path
import re
from statistics import mean, median
import time
from urllib.parse import urlencode

from .feeds import DATA, get_json


def download_trades(wallet, output, max_pages=10):
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", wallet):
        raise ValueError("Expected an EVM wallet address")
    if not 1 <= max_pages <= 10:
        raise ValueError("max_pages must be 1..10 (public trade endpoint offset cap)")
    trades, seen = [], set()
    exhausted = False
    for page in range(max_pages):
        query = urlencode(dict(user=wallet, takerOnly="false", limit=1000, offset=page * 1000))
        rows = get_json(f"{DATA}/trades?{query}")
        if not isinstance(rows, list):
            raise ValueError("Unexpected trade API response")
        for row in rows:
            identity = tuple(str(row.get(k, "")) for k in
                             ("transactionHash", "asset", "timestamp", "side", "size", "price"))
            if identity not in seen:
                seen.add(identity)
                trades.append(row)
        if len(rows) < 1000:
            exhausted = True
            break
        time.sleep(.1)
    result = dict(wallet=wallet, fetched_at=time.time(), endpoint=f"{DATA}/trades",
                  endpoint_exhausted=exhausted, complete_wallet_history=False,
                  warning="Trades only; capped public API sample. Excludes transfers, splits, merges, payouts and rebates.",
                  trades=trades)
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def analyze_trades(data):
    rows = data["trades"] if isinstance(data, dict) else data
    markets = defaultdict(lambda: {"Up": [0., 0.], "Down": [0., 0.]})
    notionals, timestamps, excluded = [], [], 0
    for row in rows:
        outcome = row.get("outcome")
        # Restrict statistics to the described short-term crypto strategy.
        slug = row.get("slug", "")
        if (not re.fullmatch(r"(btc|eth|sol|xrp)-updown-(5m|15m)-\d+", slug)
                or outcome not in ("Up", "Down") or row.get("side") not in ("BUY", "SELL")):
            excluded += 1
            continue
        size, price = float(row["size"]), float(row["price"])
        if not math.isfinite(size) or not math.isfinite(price) or size <= 0 or not 0 < price < 1:
            raise ValueError("Invalid wallet trade")
        notionals.append(size * price)
        timestamps.append(float(row["timestamp"]))
        if row["side"] == "BUY":
            item = markets[slug][outcome]
            item[0] += size
            item[1] += size * price
    stats, pair_costs, imbalances = [], [], []
    for slug, sides in markets.items():
        up, down = sides["Up"], sides["Down"]
        both = up[0] > 0 and down[0] > 0
        combined = up[1] / up[0] + down[1] / down[0] if both else None
        imbalance = abs(up[0] - down[0]) / (up[0] + down[0])
        if both:
            pair_costs.append(combined)
        imbalances.append(imbalance)
        stats.append(dict(slug=slug, bought_both=both, gross_buy_up_shares=up[0], gross_buy_down_shares=down[0],
                          gross_buy_vwap_pair_cost=combined, gross_buy_share_imbalance=imbalance))
    return dict(
        sample_trades=len(rows), included_crypto_trades=len(notionals), excluded_trades=excluded,
        first_observed_timestamp=min(timestamps) if timestamps else None,
        last_observed_timestamp=max(timestamps) if timestamps else None,
        average_trade_dollars=mean(notionals) if notionals else None,
        median_trade_dollars=median(notionals) if notionals else None,
        observed_markets_with_buys=len(stats),
        bought_both_fraction=sum(s["bought_both"] for s in stats) / len(stats) if stats else None,
        median_gross_buy_vwap_pair_cost=median(pair_costs) if pair_costs else None,
        median_gross_buy_share_imbalance=median(imbalances) if imbalances else None,
        audited_pnl=None, markets=stats,
        limitations=["Sample may omit earlier buys and entire markets; no lifetime extrapolation.",
                     "Gross purchase VWAP is not matched-lot inventory cost; sells/merges change holdings.",
                     "Maker/taker status, net fees, deposits, redemptions and rebates are not reconstructed.",
                     "The claimed +$246,578 profit is not verified by this report."],
    )
