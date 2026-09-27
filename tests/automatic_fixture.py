"""Offline integration fixture; no SDK, credentials or external network."""
import json
from pathlib import Path
import sys
import threading
import time

from polymarket_bot.automatic_worker import Session, make_server
from polymarket_bot.core import Config, Market


class FixedModel:
    q = .8

    def probability(self, *args):
        return self.q


now = time.time()
session = Session(Market('btc-updown-5m-fixture', 'up', 'down', 100, now-100, now+200),
                  Config(capital=10, max_market_spend=10, max_loss=10,
                         order_dollars=3, max_net_shares=20, quote_lifetime=60,
                         decision_interval=.001, max_feed_age=30), Path(sys.argv[1]), 'condition')
session.engine.model = FixedModel()


def refresh():
    session.engine.last_decision = float('inf')
    for token, bid, ask in [('up', '.44', '.48'), ('down', '.45', '.49')]:
        session.receive(dict(kind='book', ts=time.time(), token=token,
                             bids=[[bid, '100']], asks=[[ask, '100']]))
    session.engine.last_decision = 0
    session.receive(dict(kind='clock', ts=time.time()))


refresh()
server = make_server(session, 'offline-integration')
threading.Thread(target=server.serve_forever, daemon=True).start()
print(json.dumps(dict(url=f'http://127.0.0.1:{server.server_port}')), flush=True)
try:
    for line in sys.stdin:
        if line.strip() == 'reverse':
            with session.lock:
                session.engine.model.q = .2
                refresh()
            print(json.dumps(dict(reversed=True)), flush=True)
finally:
    server.server_close()
    session.journal.close()
