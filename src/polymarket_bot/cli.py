import argparse
import asyncio
import json
from pathlib import Path

from .core import Config
from .feeds import GAMMA, discover, get_json, market_from_gamma, shadow
from .forensics import analyze_trades, download_trades
from .replay import replay, write_demo


def load_config(path):
    return Config(**json.loads(Path(path).read_text(encoding="utf-8"))) if path else Config()


def save_report(report, path):
    if path:
        Path(path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    # Keep command output compact; the detailed action journal stays in the report.
    print(json.dumps({k: v for k, v in report.items() if k not in ("actions", "fills", "markets", "trades")}, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Polymarket paper-only dynamic hedging bot")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Generate and replay a synthetic reversal; offline")
    demo.add_argument("--output", default="runs/demo")
    replay_parser = commands.add_parser("replay", help="Replay schema=1 JSONL observations")
    replay_parser.add_argument("recording")
    replay_parser.add_argument("--config")
    replay_parser.add_argument("--output")
    discover_parser = commands.add_parser("discover", help="Fetch current and next short-term market metadata")
    discover_parser.add_argument("--asset", choices=["btc", "eth", "sol", "xrp"], default="btc")
    discover_parser.add_argument("--interval", choices=["5m", "15m"], default="5m")
    shadow_parser = commands.add_parser("shadow", help="Record public live feeds and simulate orders")
    shadow_parser.add_argument("--slug", required=True)
    shadow_parser.add_argument("--strike", type=float, required=True, help="Official price to beat, never a guessed spot price")
    shadow_parser.add_argument("--seconds", type=float, default=300)
    shadow_parser.add_argument("--config")
    shadow_parser.add_argument("--spot-feed", choices=["chainlink", "coinbase"], default="chainlink")
    shadow_parser.add_argument("--output", required=True)
    wallet_parser = commands.add_parser("wallet", help="Download a capped public wallet trade sample")
    wallet_parser.add_argument("--address", default="0xb0f85baa97990910a3e8ac2b4a58a322f01ecef5")
    wallet_parser.add_argument("--max-pages", type=int, default=10)
    wallet_parser.add_argument("--output", required=True)
    analyze_parser = commands.add_parser("analyze-wallet", help="Analyze an existing trade sample")
    analyze_parser.add_argument("sample")
    analyze_parser.add_argument("--output")
    dashboard_parser = commands.add_parser("dashboard", help="Open the local trading dashboard")
    dashboard_parser.add_argument("--port", type=int, default=8787)
    dashboard_parser.add_argument("--runs", default="runs")
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            output = Path(args.output)
            output.mkdir(parents=True, exist_ok=False)
            write_demo(output / "events.jsonl")
            save_report(replay(output / "events.jsonl"), output / "report.json")
        elif args.command == "replay":
            save_report(replay(args.recording, load_config(args.config) if args.config else None), args.output)
        elif args.command == "discover":
            print(json.dumps(discover(args.asset, args.interval), indent=2))
        elif args.command == "shadow":
            raw = get_json(f"{GAMMA}/markets/slug/{args.slug}")
            market = market_from_gamma(raw, args.strike)
            product = args.slug.split("-")[0].upper() + "-USD"
            if product not in {"BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD"}:
                raise ValueError("Unsupported Coinbase product")
            save_report(asyncio.run(shadow(market, load_config(args.config), product, args.seconds, args.output, args.spot_feed)), None)
        elif args.command == "wallet":
            data = download_trades(args.address, args.output, args.max_pages)
            save_report(analyze_trades(data), str(Path(args.output).with_suffix(".analysis.json")))
        elif args.command == "analyze-wallet":
            save_report(analyze_trades(json.loads(Path(args.sample).read_text(encoding="utf-8"))), args.output)
        elif args.command == "dashboard":
            from .dashboard import serve
            serve(args.runs, args.port)
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        parser.exit(1, f"Error: {error}\n")
    except KeyboardInterrupt:
        parser.exit(130, "Stopped. No real orders were submitted.\n")


if __name__ == "__main__":
    main()
