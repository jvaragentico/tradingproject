"""Local, capability-protected strategy worker. No keys or order submission."""
import asyncio
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import secrets
import sys
import threading
import time
from uuid import uuid4
from urllib.parse import urlparse

from .core import Config, Market
from .execution import ExecutionEngine
from .feeds import GAMMA, get_json, market_from_gamma, shadow


class Session:
    def __init__(self, market, config, root, condition_id="", spot_feed="chainlink"):
        self.engine = ExecutionEngine(market, config)
        self.condition_id = condition_id
        self.spot_feed = spot_feed
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=False)
        self.journal = (self.root / "events.jsonl").open("w", encoding="utf-8")
        self.journal.write(json.dumps(dict(kind="meta",schema=1,market=asdict(market),config=asdict(config),
                                          source="live_account",execution_mode="exchange_confirmed"))+"\n")
        self.journal.flush()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.feed_done = False
        self.error = None

    def receive(self, event):
        with self.lock:
            event = dict(event, ts=max(float(event["ts"]),self.engine.now))
            self.engine.ingest(event)
            self.journal.write(json.dumps(event)+"\n")
            self.journal.flush()

    def state(self):
        with self.lock:
            e=self.engine
            return dict(receivedAt=e.now if math.isfinite(e.now) else time.time(),maxAge=e.config.max_feed_age,
                        halted=e.halted or self.error or ("feeds_finished" if self.feed_done else None),
                        market=asdict(e.market), conditionId=self.condition_id,
                        intents=[dict(localId=i["local_id"],exchangeId=i["exchange_id"],side=i["side"],
                                      price=i["price"],shares=i["shares"],cancel=i["cancel"]) for i in e.intents()],
                        config=asdict(e.config),portfolio=e.portfolio.report(e.books),run=str(self.root))

    def update(self, body):
        kind=body.get("kind")
        if kind not in {"execution_ack","execution_reject","execution_cancel","execution_fill","execution_stop"}:
            raise ValueError("Unsupported execution update")
        self.receive(dict(body,ts=time.time()))
        if kind=="execution_stop":
            self.stop_event.set()
        self.save()
        return self.state()

    def save(self):
        with self.lock:
            report=self.engine.report()
            report.update(source="live_account",feed_errors=[self.error] if self.error else [])
            (self.root/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")

    def feeds(self, seconds):
        session=self
        class Receiver:
            def __getattr__(self,name):
                return getattr(session.engine,name)
            def ingest(self,event):
                session.receive(event)
            def report(self):
                with session.lock:
                    return session.engine.report()
        try:
            m=self.engine.market
            asyncio.run(shadow(m,self.engine.config,m.slug.split('-')[0].upper()+'-USD',seconds,
                               self.root/'public',spot_feed=self.spot_feed,stop_event=self.stop_event,engine=Receiver()))
        except Exception:
            self.error="public_feed_failed"
        finally:
            self.feed_done=True
            self.receive(dict(kind="execution_stop",ts=time.time()))
            self.save()


def make_server(session, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def send(self,data,status=200):
            raw=json.dumps(data,allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(raw)
        def authorized(self):
            return (self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}' and
                    secrets.compare_digest(self.headers.get('Authorization',''),f'Bearer {token}') and
                    self.headers.get('Origin') is None)
        def do_GET(self):
            if not self.authorized(): return self.send(dict(error='Unauthorized'),403)
            if self.path!='/state':return self.send(dict(error='Not found'),404)
            self.send(session.state())
        def do_POST(self):
            if not self.authorized():return self.send(dict(error='Unauthorized'),403)
            try:
                size=int(self.headers.get('Content-Length','0'))
                if self.path!='/update' or not 0<size<=4096 or self.headers.get('Content-Type')!='application/json':
                    raise ValueError('Invalid request')
                body=json.loads(self.rfile.read(size))
                if not isinstance(body,dict):raise ValueError('Invalid update')
                self.send(session.update(body))
            except (ValueError,TypeError,KeyError):
                self.send(dict(error='Invalid execution update'),400)
    return ThreadingHTTPServer(('127.0.0.1',0),Handler)


def main():
    options=json.loads(sys.stdin.readline())
    raw=get_json(f"{GAMMA}/markets/slug/{options['slug']}")
    market=market_from_gamma(raw,float(options['strike']))
    if not market.slug.startswith('btc-updown-5m-'):
        raise ValueError('Automatic trading supports BTC Up/Down 5m only')
    source=urlparse(raw.get('resolutionSource',''))
    asset=market.slug.split('-')[0]
    if source.scheme!='https' or source.hostname!='data.chain.link':
        raise ValueError('Unsupported resolution source')
    if source.path==f'/streams/{asset}-usd-twap-60s-streams':
        spot_feed='chainlink_twap'
    elif source.path==f'/streams/{asset}-usd':
        spot_feed='chainlink'
    else:
        raise ValueError('Unsupported resolution price feed')
    if not market.start<=time.time()<market.end-35:
        raise ValueError('Use a current market with time to warm up the model')
    config=Config(capital=float(options['capital']),order_dollars=float(options['orderDollars']),
                  max_market_spend=float(options['maxSpend']),max_loss=float(options['maxLoss']),
                  max_net_shares=float(options.get('maxNetShares',20)),allow_taker=False)
    if not 0<config.max_loss<=config.max_market_spend<=config.capital or not 0<config.order_dollars<=23.59:
        raise ValueError('Invalid execution caps')
    root=Path('runs')/('automatic-'+uuid4().hex[:12])
    session=Session(market,config,root,raw['conditionId'],spot_feed)
    token=secrets.token_urlsafe(32)
    server=make_server(session,token)
    print(json.dumps(dict(url=f'http://127.0.0.1:{server.server_port}',token=token,run=str(root))),flush=True)
    threading.Thread(target=session.feeds,args=(min(float(options['seconds']),market.end-time.time()),),daemon=True).start()
    try: server.serve_forever()
    finally:
        session.stop_event.set()
        session.save()
        server.server_close()


if __name__=='__main__':
    try:main()
    except Exception:
        print('Strategy worker startup failed; check market, strike, caps and network.',file=sys.stderr)
        sys.exit(1)
