import json
from pathlib import Path
import shutil
import threading
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from uuid import uuid4

from polymarket_bot.automatic_worker import Session, make_server
from polymarket_bot.core import Market, Config
from polymarket_bot.replay import replay, write_demo
from polymarket_bot.feeds import normalize_chainlink_twap


class WorkerTests(unittest.TestCase):
    def test_twap_uses_decimal_price_not_scaled_integer(self):
        message = dict(topic='crypto_prices_twap_sixty',type='update',payload=dict(
            symbol='btc/usd',timestamp=1790530893000,window_s=60,
            value=84453.66654152559,full_accuracy_value='84453666541525585100800'))
        event, = normalize_chainlink_twap(message,1790530894,'BTC/USD')
        self.assertAlmostEqual(event['price'],84453.66654152559)
        self.assertEqual(event['source_ts'],1790530893)
        self.assertEqual(normalize_chainlink_twap(message,1790530894,'ETH/USD'),[])
        message['payload']['window_s']=30
        self.assertEqual(normalize_chainlink_twap(message,1790530894,'BTC/USD'),[])

    def setUp(self):
        self.root=Path('runs')/('worker-test-'+uuid4().hex)
        self.session=None

    def tearDown(self):
        if self.session:self.session.journal.close()
        if self.root.exists():shutil.rmtree(self.root)

    def test_public_and_account_journal_replays_exactly(self):
        self.root.mkdir(parents=True)
        source=self.root/'fixture.jsonl'
        write_demo(source)
        rows=[json.loads(line) for line in source.read_text().splitlines()]
        self.session=Session(Market(**rows[0]['market']),Config(**rows[0]['config']),self.root/'account')
        for row in rows[1:]:
            self.session.receive(row)
            intents=self.session.engine.intents()
            for intent in intents:
                if not intent['exchange_id']:
                    self.session.receive(dict(kind='execution_ack',ts=row['ts'],local_id=intent['local_id'],exchange_id='order-'+intent['local_id']))
                if intent['cancel']:
                    self.session.receive(dict(kind='execution_cancel',ts=row['ts'],local_id=intent['local_id']))
                elif self.session.engine.registry[intent['local_id']].remaining>=5:
                    self.session.receive(dict(kind='execution_fill',ts=row['ts'],local_id=intent['local_id'],fill_id='fill-'+intent['local_id'],shares='5',price=intent['price'],fee='0',status='CONFIRMED'))
        expected=self.session.engine.report()
        actual=replay(self.session.root/'events.jsonl')
        self.assertEqual(expected['portfolio'],actual['portfolio'])
        self.assertEqual(expected['actions'],actual['actions'])
        self.assertGreater(len(actual['fills']),0)
        self.assertEqual({f['outcome'] for f in actual['fills']},{'Up','Down'})
        self.assertGreater(float(actual['portfolio']['paired_shares']),0)
        self.assertFalse(actual['portfolio']['resolved'])

    def test_worker_endpoint_requires_capability_and_rejects_browser_origin(self):
        now=time.time()
        self.session=Session(Market('btc-updown-5m-0','up','down',100,now-100,now+200),Config(),self.root)
        server=make_server(self.session,'test-capability')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        try:
            with self.assertRaises(HTTPError) as error:urlopen(url+'/state')
            self.assertEqual(error.exception.code,403)
            request=Request(url+'/state',headers={'Authorization':'Bearer test-capability'})
            with urlopen(request) as response:self.assertEqual(json.load(response)['intents'],[])
            request.add_header('Origin','http://127.0.0.1:8787')
            with self.assertRaises(HTTPError) as error:urlopen(request)
            self.assertEqual(error.exception.code,403)
            request=Request(url+'/update',data=b'{"kind":"execution_stop"}',headers={'Authorization':'Bearer test-capability','Content-Type':'application/json'})
            with urlopen(request) as response:self.assertEqual(json.load(response)['halted'],'executor_stopped')
            self.assertTrue(self.session.stop_event.is_set())
        finally:server.shutdown();server.server_close();thread.join()
