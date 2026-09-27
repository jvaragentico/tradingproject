"""Loopback-only dashboard backed by causal replay and live shadow recordings."""

import asyncio
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from .core import Config, Engine, Market
from .feeds import GAMMA, get_json, market_from_gamma, shadow
from .replay import write_demo

ASSETS = Path(__file__).parent / "web"


def funding_assets():
    return [a for a in get_json("https://bridge.polymarket.com/supported-assets")["supportedAssets"]
            if str(a["chainId"]) == "56"]


def funding_quote(body):
    recipient = body.get("recipient", "")
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", recipient):
        raise ValueError("Enter the Polygon trading wallet address from your Polymarket profile")
    token = body.get("token", "")
    if not isinstance(token, str):
        raise ValueError("Invalid source token")
    assets = funding_assets()
    asset = next((a for a in assets if a["token"]["address"].lower() == token.lower()), None)
    if asset is None:
        raise ValueError("Token is not currently supported from BNB Chain")
    try:
        amount = Decimal(str(body.get("amount", "")))
        scaled = amount * 10 ** int(asset["token"]["decimals"])
        if not scaled.is_finite() or scaled <= 0 or scaled != scaled.to_integral_value():
            raise ValueError("Use a positive amount within the token's decimal precision")
    except InvalidOperation as error:
        raise ValueError("Invalid token amount") from error
    payload = dict(fromAmountBaseUnit=str(int(scaled)), fromChainId="56", fromTokenAddress=asset["token"]["address"],
                   recipientAddress=recipient, toChainId="137", toTokenAddress="0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB")
    request = Request("https://bridge.polymarket.com/quote", data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json", "User-Agent": "polyterminal/1"}, method="POST")
    with urlopen(request, timeout=15) as response:
        quote = json.load(response)
    if float(quote["estInputUsd"]) < float(asset["minCheckoutUsd"]):
        raise ValueError("Amount is below the bridge's minimum deposit")
    return dict(request=payload, quote=quote, sourceToken=asset["token"], fetchedAt=time.time(),
                reviewOnly=True, destination="Polygon pUSD trading collateral")


def live_market(slug):
    if not re.fullmatch(r"(btc|eth|sol|xrp)-updown-(5m|15m)-\d+", slug):
        raise ValueError("Use a supported short-term crypto market slug")
    market = market_from_gamma(get_json(f"{GAMMA}/markets/slug/{slug}"), 1)
    if not market.start <= time.time() < market.end:
        raise ValueError("Market is not currently open")
    outcomes = {}
    for side, token in (("Up", market.up_token), ("Down", market.down_token)):
        book = get_json(f"https://clob.polymarket.com/book?token_id={token}")
        if str(book.get("asset_id")) != token or book.get("neg_risk") is not False:
            raise ValueError("Unexpected market order book")
        outcomes[side] = dict(tokenId=token, tick=book["tick_size"], minSize=book["min_order_size"])
    return dict(slug=slug, endsAt=market.end, fetchedAt=time.time(), outcomes=outcomes)


class RunMonitor:
    """Incrementally read complete JSONL lines; never rescan a live run per poll."""

    def __init__(self, path):
        self.path = Path(path)
        self.offset = 0
        self.engine = None
        self.meta = None
        self.history = []
        self.counts = Counter()
        self.sample_ts = -math.inf
        self.lock = threading.Lock()

    def point(self):
        e = self.engine
        return dict(ts=e.now, pnl=float(e.portfolio.report(e.books)["conservative_mark_pnl"]),
                    cash=float(e.portfolio.cash), paired=float(e.portfolio.paired),
                    net=float(e.portfolio.net), up=float(e.portfolio.residual("Up")),
                    down=float(e.portfolio.residual("Down")), q=e.q,
                    spot=math.exp(e.model.samples[-1][1]) if e.model.samples else None,
                    up_bid=float(e.books["Up"].bid) if e.books["Up"].bids else None,
                    up_ask=float(e.books["Up"].ask) if e.books["Up"].asks else None,
                    down_bid=float(e.books["Down"].bid) if e.books["Down"].bids else None,
                    down_ask=float(e.books["Down"].ask) if e.books["Down"].asks else None)

    def update(self):
        if self.path.stat().st_size < self.offset:
            raise ValueError("Recording was truncated; create a new run")
        with self.path.open("rb") as recording:
            recording.seek(self.offset)
            while True:
                line = recording.readline()
                if not line or not line.endswith(b"\n"):
                    break  # In-flight partial writes are consumed on the next poll.
                row = json.loads(line)
                if self.engine is None:
                    if row.get("kind") != "meta" or row.get("schema") != 1:
                        raise ValueError("Unsupported recording header")
                    self.meta = row
                    engine_class = Engine
                    if row.get("execution_mode") == "exchange_confirmed":
                        from .execution import ExecutionEngine
                        engine_class = ExecutionEngine
                    self.engine = engine_class(Market(**row["market"]), Config(**row["config"]))
                else:
                    self.engine.ingest(row)
                    self.counts[row["kind"]] += 1
                    if self.engine.now - self.sample_ts >= .5 or row["kind"] == "resolution":
                        self.history.append(self.point())
                        self.sample_ts = self.engine.now
                self.offset = recording.tell()

    def snapshot(self, status="recorded"):
        with self.lock:
            self.update()
            if self.engine is None:
                return dict(status="connecting", history=[], report=None)
            e = self.engine
            history = list(self.history)
            if math.isfinite(e.now):
                final = self.point()
                if history and history[-1]["ts"] == final["ts"]:
                    history[-1] = final
                else:
                    history.append(final)
            # Retain chronological endpoints while bounding browser payload size.
            stride = max(1, math.ceil(len(history) / 1200))
            history = history[::stride] + ([history[-1]] if history and history[-1] not in history[::stride] else [])
            books = {}
            for side, book in e.books.items():
                books[side] = dict(bids=[[str(p), str(book.bids[p])] for p in sorted(book.bids, reverse=True)[:8]],
                                   asks=[[str(p), str(book.asks[p])] for p in sorted(book.asks)[:8]],
                                   received=book.received if math.isfinite(book.received) else None,
                                   source=book.source if math.isfinite(book.source) else None)
            report = e.report()
            report["source"] = self.meta.get("source", "unknown")
            report_path = self.path.with_name("report.json")
            errors = []
            if report_path.exists():
                try:
                    errors = json.loads(report_path.read_text(encoding="utf-8")).get("feed_errors", [])
                except (ValueError, OSError):
                    pass
            return dict(status=status, report=report, history=history, books=books,
                        orders=[dict(side=o.side, price=str(o.price), shares=str(o.remaining),
                                     queue_ahead=str(o.queue_ahead), active=o.active,
                                     cancel_pending=o.cancel_at is not None, mode=o.mode) for o in e.orders],
                        counts=dict(self.counts), feed_errors=errors,
                        recording_bytes=self.offset, captured_until=e.now if math.isfinite(e.now) else None,
                        provenance=dict(recording=self.path.parent.name + "/events.jsonl",
                                        source=self.meta.get("source", "unknown"),
                                        history_sample_seconds=.5, buildStatus="complete"))


class DashboardStore:
    def __init__(self, root, create_demo=True):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.monitors = {}
        self.jobs = {}
        self.lock = threading.RLock()
        if create_demo and not (self.root / "demo" / "events.jsonl").exists():
            directory = self.root / "demo"
            directory.mkdir(exist_ok=True)
            write_demo(directory / "events.jsonl")

    def directory(self, run):
        if not isinstance(run, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}", run):
            raise ValueError("Invalid run identifier")
        directory = (self.root / run).resolve()
        if directory.parent != self.root:
            raise ValueError("Run must be inside the runs directory")
        return directory

    def list_runs(self):
        result = []
        with self.lock:
            names = {p.name for p in self.root.iterdir() if p.is_dir() and (p / "events.jsonl").exists()}
            names.update(self.jobs)
            for name in sorted(names):
                try:
                    directory = self.directory(name)
                    path = directory / "events.jsonl"
                    meta = {}
                    if path.exists():
                        with path.open(encoding="utf-8") as recording:
                            meta = json.loads(recording.readline())
                    job = self.jobs.get(name, {})
                    result.append(dict(id=name, market=meta.get("market", {}).get("slug", job.get("slug", "Connecting")),
                                       source=meta.get("source", "live_public"),
                                       status=job.get("status", "recorded"), error=job.get("error")))
                except (ValueError, OSError):
                    continue
        return result

    def snapshot(self, run):
        directory = self.directory(run)
        with self.lock:
            job = dict(self.jobs.get(run, {}))
            path = directory / "events.jsonl"
            if not path.exists():
                if job:
                    return dict(id=run, status=job["status"], error=job.get("error"), report=None, history=[])
                raise FileNotFoundError("Run not found")
            monitor = self.monitors.setdefault(run, RunMonitor(path))
        snapshot = monitor.snapshot(job.get("status", "recorded"))
        return dict(snapshot, id=run, error=job.get("error"))

    def start(self, body):
        slug = body.get("slug", "")
        if not isinstance(slug, str) or not re.fullmatch(r"(btc|eth|sol|xrp)-updown-(5m|15m)-\d+", slug):
            raise ValueError("Enter a BTC/ETH/SOL/XRP 5m or 15m Up/Down market slug")
        strike, seconds = float(body.get("strike", 0)), float(body.get("seconds", 300))
        if not math.isfinite(strike) or strike <= 0 or not math.isfinite(seconds) or not 5 <= seconds <= 900:
            raise ValueError("Use a positive official strike and a duration of 5–900 seconds")
        with self.lock:
            if any(j["status"] in ("connecting", "running", "stopping") for j in self.jobs.values()):
                raise ValueError("Stop the current shadow run before starting another")
            run = "shadow-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
            stop = threading.Event()
            self.jobs[run] = dict(status="connecting", slug=slug, stop=stop, error=None)

        def worker():
            try:
                raw = get_json(f"{GAMMA}/markets/slug/{slug}")
                market = market_from_gamma(raw, strike)
                if market.end <= time.time():
                    raise ValueError("Market has already expired")
                config_path = Path("config.example.json")
                config = Config(**json.loads(config_path.read_text())) if config_path.exists() else Config()
                with self.lock:
                    if stop.is_set():
                        self.jobs[run]["status"] = "stopped"
                        return
                    self.jobs[run]["status"] = "running"
                asyncio.run(shadow(market, config, slug.split("-")[0].upper() + "-USD", seconds,
                                   self.directory(run), stop_event=stop))
                with self.lock:
                    self.jobs[run]["status"] = "stopped" if stop.is_set() else "finished"
            except Exception as error:
                with self.lock:
                    self.jobs[run].update(status="error", error=str(error))

        threading.Thread(target=worker, name=run, daemon=True).start()
        return dict(id=run, status="connecting")

    def stop(self, run):
        self.directory(run)
        with self.lock:
            job = self.jobs.get(run)
            if not job or job["status"] not in ("connecting", "running", "stopping"):
                raise ValueError("That run is not active")
            job["stop"].set()
            job["status"] = "stopping"
        return dict(id=run, status="stopping")


def make_server(root="runs", port=8787, create_demo=True):
    store = DashboardStore(root, create_demo)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def local_request(self):
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            return self.headers.get("Host") in allowed

        def send(self, data, status=200, content_type="application/json; charset=utf-8"):
            raw = data if isinstance(data, bytes) else json.dumps(data, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' https://*.polymarket.com; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if not self.local_request():
                return self.send(dict(error="Localhost access only"), 403)
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/api/runs":
                    return self.send(dict(runs=store.list_runs()))
                if parsed.path == "/api/geoblock":
                    result = get_json("https://polymarket.com/api/geoblock")
                    if not isinstance(result.get("blocked"), bool):
                        raise ValueError("Eligibility service returned an invalid response")
                    return self.send({key: result.get(key) for key in ("blocked", "country", "region")})
                if parsed.path == "/api/funding-assets":
                    return self.send(dict(assets=funding_assets()))
                if parsed.path == "/api/market":
                    return self.send(live_market(parse_qs(parsed.query).get("slug", [""])[0]))
                if parsed.path in ("/api/run", "/api/export"):
                    run = parse_qs(parsed.query).get("run", ["demo"])[0]
                    data = store.snapshot(run)
                    return self.send(data["report"] if parsed.path == "/api/export" else data)
                files = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/wallet.js": "wallet.js"}
                if parsed.path not in files:
                    return self.send(dict(error="Not found"), 404)
                mime = {"/": "text/html; charset=utf-8", "/app.js": "text/javascript; charset=utf-8",
                        "/style.css": "text/css; charset=utf-8", "/wallet.js": "text/javascript; charset=utf-8"}[parsed.path]
                return self.send((ASSETS / files[parsed.path]).read_bytes(), content_type=mime)
            except FileNotFoundError as error:
                self.send(dict(error=str(error)), 404)
            except (ValueError, KeyError, OSError) as error:
                self.send(dict(error=str(error)), 400)

        def do_POST(self):
            origin = self.headers.get("Origin")
            if (not self.local_request() or origin not in {
                    f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}):
                return self.send(dict(error="Same-origin localhost requests only"), 403)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send(dict(error="Expected application/json"), 415)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 4096:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("Expected JSON object")
                if self.path == "/api/shadow":
                    return self.send(store.start(body), 202)
                if self.path == "/api/funding-quote":
                    return self.send(funding_quote(body))
                if self.path == "/api/stop":
                    return self.send(store.stop(body.get("run")), 202)
                self.send(dict(error="Not found"), 404)
            except (ValueError, TypeError, KeyError, OSError) as error:
                self.send(dict(error=str(error)), 400)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.store = store
    return server


def serve(root="runs", port=8787):
    server = make_server(root, port)
    print(f"Polymarket dashboard: http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for job in server.store.jobs.values():
            job["stop"].set()
        server.server_close()
