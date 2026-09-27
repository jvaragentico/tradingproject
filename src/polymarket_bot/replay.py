"""Replay recorded observations without injecting future prices or resolutions."""

from dataclasses import asdict
import json
from pathlib import Path

from .core import Config, Engine, Market


def replay(path, config=None):
    with Path(path).open(encoding="utf-8") as recording:
        meta = json.loads(next(recording))
        if meta.get("kind") != "meta" or meta.get("schema") != 1:
            raise ValueError("Expected schema=1 meta header")
        engine = Engine(Market(**meta["market"]), config or Config(**meta["config"]))
        for line_number, line in enumerate(recording, 2):
            if line.strip():
                try:
                    engine.ingest(json.loads(line))
                except (ValueError, KeyError, TypeError) as error:
                    raise ValueError(f"Invalid event on line {line_number}: {error}") from error
    report = engine.report()
    report["source"] = meta.get("source", "unknown")
    return report


def write_demo(path):
    """Synthetic reversal fixture exercises mechanics; it is not a backtest."""
    market = Market("btc-updown-5m-0", "up", "down", 100, 0, 300)
    config = Config(warmup_seconds=5, model_window=120, max_loss=150)
    header = dict(kind="meta", schema=1, market=asdict(market), config=asdict(config),
                  source="synthetic_demo_not_performance_evidence")
    with Path(path).open("w", encoding="utf-8") as output:
        output.write(json.dumps(header) + "\n")
        for ts in range(300):
            # Up signal first, then a downward reversal. Refresh both books.
            price = 100 + (.015 if ts < 100 else -.025)
            for side, bid, ask in (("up", ".44", ".48"), ("down", ".45", ".49")):
                event = dict(kind="book", ts=ts, token=side,
                             bids=[[bid, "2"]], asks=[[ask, "100"]])
                output.write(json.dumps(event) + "\n")
            output.write(json.dumps(dict(kind="spot", ts=ts, price=price)) + "\n")
            for side, trade_price in (("up", ".44"), ("down", ".45")):
                event = dict(kind="trade", ts=ts + .5, token=side, price=trade_price,
                             size="12", side="SELL", id=f"{ts}:{side}")
                output.write(json.dumps(event) + "\n")
        output.write(json.dumps(dict(kind="resolution", ts=300, winner="down")) + "\n")
