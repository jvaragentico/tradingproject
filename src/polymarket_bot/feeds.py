"""Read-only public APIs. No credentials, signatures, or trading endpoints."""

import asyncio
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

from .core import Engine, Market

GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"
CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
COINBASE_WS = "wss://ws-feed.exchange.coinbase.com"
RTDS_WS = "wss://ws-live-data.polymarket.com"


def get_json(url):
    request = Request(url, headers={"User-Agent": "polymarket-shadow-bot/0.1", "Accept": "application/json"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def epoch(value):
    if isinstance(value, (int, float)):
        return float(value)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def array(value):
    return json.loads(value) if isinstance(value, str) else value


def market_from_gamma(raw, strike):
    outcomes, tokens = array(raw["outcomes"]), array(raw["clobTokenIds"])
    if len(outcomes) != 2 or len(tokens) != 2 or set(outcomes) != {"Up", "Down"}:
        raise ValueError("Only binary Up/Down markets are supported")
    if raw.get("negRisk"):
        raise ValueError("Negative-risk markets are not supported")
    if not raw.get("enableOrderBook") or raw.get("closed") or not raw.get("acceptingOrders"):
        raise ValueError("Market is not accepting order-book trades")
    # Never silently assume a zero fee when metadata is missing.
    if raw.get("feesEnabled") is False:
        schedule = {"rate": 0, "exponent": 1, "takerOnly": True}
    elif raw.get("feesEnabled") is True and isinstance(raw.get("feeSchedule"), dict):
        schedule = raw["feeSchedule"]
    else:
        raise ValueError("Fee schedule missing; refusing to guess")
    mapping = dict(zip(outcomes, tokens))
    slug = raw["slug"]
    # The startDate field can be a listing date. Short-term slugs encode the
    # actual UTC interval start, which must agree with the official endDate.
    pieces = slug.split("-")
    if len(pieces) != 4 or pieces[1] != "updown" or pieces[2] not in ("5m", "15m"):
        raise ValueError("Use a BTC/ETH/SOL/XRP 5m or 15m Up/Down slug")
    start = float(pieces[-1])
    end = epoch(raw["endDate"])
    expected = 300 if pieces[2] == "5m" else 900
    if abs(end - start - expected) > 1:
        raise ValueError("Slug interval does not agree with official market expiry")
    return Market(slug, mapping["Up"], mapping["Down"], strike, start, end,
                  str(raw["orderPriceMinTickSize"]), str(raw["orderMinSize"]),
                  str(schedule["rate"]), float(schedule["exponent"]), schedule["takerOnly"])


def discover(asset="btc", interval="5m", now=None):
    now = now if now is not None else time.time()
    duration = 300 if interval == "5m" else 900
    start = int(now) // duration * duration
    # Return metadata for both the current and next interval; strike is never
    # invented from the local spot feed.
    results = []
    for offset in (0, duration):
        slug = f"{asset}-updown-{interval}-{start + offset}"
        try:
            raw = get_json(f"{GAMMA}/markets/slug/{quote(slug, safe='')}")
            results.append(raw)
        except Exception as error:
            results.append({"slug": slug, "error": str(error)})
    return results


def normalize_clob(message, received):
    """Map raw CLOB frames to a replayable, receive-time-ordered event stream."""
    output = []
    messages = message if isinstance(message, list) else [message]
    for frame in messages:
        kind = frame.get("event_type")
        source = float(frame.get("timestamp", received * 1000)) / 1000
        base = dict(ts=received, source_ts=source)
        if kind == "book":
            output.append(dict(base, kind="book", token=frame["asset_id"],
                               bids=[[x["price"], x["size"]] for x in frame["bids"]],
                               asks=[[x["price"], x["size"]] for x in frame["asks"]]))
        elif kind == "price_change":
            for change in frame["price_changes"]:
                output.append(dict(base, kind="delta", token=change["asset_id"],
                                   price=change["price"], size=change["size"], side=change["side"]))
        elif kind == "last_trade_price":
            # A transaction can contain multiple fills. Include all distinguishing
            # fields rather than suppressing an entire transaction after its first fill.
            identity = ":".join(str(frame.get(k, "")) for k in
                                ("transaction_hash", "timestamp", "asset_id", "price", "size", "side"))
            output.append(dict(base, kind="trade", token=frame["asset_id"],
                               price=frame["price"], size=frame["size"], side=frame["side"], id=identity))
        elif kind == "tick_size_change":
            output.append(dict(base, kind="tick", token=frame["asset_id"], tick=frame["new_tick_size"]))
        elif kind == "market_resolved":
            output.append(dict(base, kind="resolution", winner=frame["winning_asset_id"]))
    return output


def normalize_coinbase(message, received, product):
    if message.get("type") == "error":
        raise RuntimeError(f"Coinbase feed error: {message.get('message')}")
    if message.get("type") != "ticker" or message.get("product_id") != product:
        return []
    return [dict(kind="spot", ts=received, source_ts=epoch(message["time"]), price=message["price"], feed="coinbase_proxy")]


def normalize_chainlink(message, received, symbol):
    if message.get("topic") != "crypto_prices_chainlink" or message.get("type") != "update":
        return []
    payload = message.get("payload", {})
    if payload.get("symbol", "").lower() != symbol.lower() or "value" not in payload:
        return []  # Ignore initial historical dumps; never inject them into warmup.
    return [dict(kind="spot", ts=received, source_ts=float(payload["timestamp"]) / 1000,
                 price=payload["value"], feed="polymarket_chainlink")]


async def shadow(market, config, product, seconds, output, spot_feed="chainlink", stop_event=None):
    from websockets.asyncio.client import connect

    if seconds <= 0:
        raise ValueError("seconds must be positive")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    engine = Engine(market, config)
    header = dict(kind="meta", schema=1, market=asdict(market), config=asdict(config),
                  source="live_public", product=product, spot_feed=spot_feed, captured_at=time.time())
    errors = []
    deadline = time.monotonic() + seconds
    with (output / "events.jsonl").open("w", encoding="utf-8") as recording:
        recording.write(json.dumps(header) + "\n")

        def emit(event):
            # Receive time is assigned in this event loop, never sorted later.
            recording.write(json.dumps(event) + "\n")
            recording.flush()
            engine.ingest(event)

        async def reader(url, subscription, normalizer, label, ping_text=None, ping_seconds=10):
            # Disconnect halts this run permanently. Restart creates a new run,
            # avoiding stale orders and ambiguous fills across feed gaps.
            try:
                async with connect(url, ping_interval=20, ping_timeout=20, proxy=None,
                                   open_timeout=15, max_queue=4096) as websocket:
                    await websocket.send(json.dumps(subscription))

                    async def heartbeat():
                        while True:
                            await asyncio.sleep(ping_seconds)
                            await websocket.send(ping_text)

                    heartbeat_task = asyncio.create_task(heartbeat()) if ping_text else None
                    try:
                        async for raw in websocket:
                            if raw in ("PONG", "PING", "pong", "ping", ""):
                                continue
                            message = json.loads(raw)
                            if isinstance(message, dict) and message.get("type") == "error":
                                raise RuntimeError(str(message))
                            for event in normalizer(message, time.time()):
                                emit(event)
                    finally:
                        if heartbeat_task:
                            heartbeat_task.cancel()
                            await asyncio.gather(heartbeat_task, return_exceptions=True)
                    raise RuntimeError("WebSocket closed")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                errors.append(f"{label}: {error}")
                emit(dict(kind="disconnect", ts=time.time(), feed=label, error=str(error)))

        symbol = product.split("-")[0].lower() + "/usd"
        if spot_feed == "chainlink":
            spot_reader = reader(RTDS_WS, dict(action="subscribe", subscriptions=[
                dict(topic="crypto_prices_chainlink", type="*")]),
                lambda msg, ts: normalize_chainlink(msg, ts, symbol), "chainlink", ping_text="ping", ping_seconds=5)
        elif spot_feed == "coinbase":
            spot_reader = reader(COINBASE_WS, dict(type="subscribe", product_ids=[product],
                                 channels=["ticker", "heartbeat"]),
                                 lambda msg, ts: normalize_coinbase(msg, ts, product), "coinbase")
        else:
            raise ValueError("Unsupported spot feed")
        tasks = [
            asyncio.create_task(reader(CLOB_WS, dict(assets_ids=[market.up_token, market.down_token],
                                                     type="market", custom_feature_enabled=True),
                                       normalize_clob, "polymarket", ping_text="PING")),
            asyncio.create_task(spot_reader),
        ]
        try:
            while time.monotonic() < deadline and not errors and not (stop_event and stop_event.is_set()):
                await asyncio.sleep(min(.25, max(0, deadline - time.monotonic())))
                emit(dict(kind="clock", ts=time.time()))
                if time.monotonic() > deadline - seconds + 15 and (
                        not engine.model.samples or not all(b.initialized for b in engine.books.values())):
                    errors.append("Feed startup timed out: missing spot or outcome snapshots")
                    emit(dict(kind="disconnect", ts=time.time(), feed="startup", error=errors[-1]))
                if engine.portfolio.settled:
                    break
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            emit(dict(kind="clock", ts=time.time()))
            if not errors and not (stop_event and stop_event.is_set()) and (not engine.model.samples or not all(b.initialized for b in engine.books.values())):
                errors.append("Missing spot or outcome snapshots; run is not a valid shadow evaluation")
            report = engine.report()
            report["feed_errors"] = errors
            (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if errors:
        raise RuntimeError("; ".join(errors) + f". Partial recording preserved in {output}")
    return report
