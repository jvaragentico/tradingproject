import json
from pathlib import Path
import shutil
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from uuid import uuid4
from unittest.mock import patch

from polymarket_bot.dashboard import DashboardStore, RunMonitor, make_server, live_market, funding_quote
from polymarket_bot.replay import replay, write_demo


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.root = Path('runs') / ('dashboard-test-' + uuid4().hex)
        self.store = DashboardStore(self.root)

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_dashboard_matches_causal_replay(self):
        data = self.store.snapshot('demo')
        expected = replay(self.root / 'demo' / 'events.jsonl')
        self.assertEqual(data['report']['portfolio'], expected['portfolio'])
        self.assertEqual(data['report']['fills'], expected['fills'])
        self.assertEqual(data['history'][-1]['pnl'], float(expected['portfolio']['realized_pnl']))
        self.assertIn('synthetic', data['report']['source'])
        self.assertEqual(data, self.store.snapshot('demo'))

    def test_partial_line_consumed_only_when_complete(self):
        lines = (self.root / 'demo' / 'events.jsonl').read_bytes().splitlines(keepends=True)
        path = self.root / 'partial.jsonl'
        path.write_bytes(lines[0] + lines[1][:10])
        monitor = RunMonitor(path)
        monitor.snapshot()
        self.assertEqual(monitor.counts.total(), 0)
        with path.open('ab') as file:
            file.write(lines[1][10:])
        monitor.snapshot()
        self.assertEqual(monitor.counts.total(), 1)

    def test_path_and_input_validation(self):
        for name in ('../demo', '..', 'a/b', 'a\\b', '', None):
            with self.assertRaises((ValueError, TypeError)):
                self.store.directory(name)
        with patch('polymarket_bot.dashboard.get_json') as fetch:
            with self.assertRaises(ValueError):
                live_market('https://other-host')
            fetch.assert_not_called()
        with self.assertRaises(ValueError):
            self.store.start(dict(slug='btc-updown-5m-0', strike=float('nan')))

    def test_http_origin_assets_and_report(self):
        server = make_server(self.root, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(url + '/api/run?run=demo') as response:
                self.assertTrue(json.load(response)['report']['paper_only'])
                self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
            for asset in ('/', '/app.js', '/style.css', '/wallet.js'):
                with urlopen(url + asset) as response:
                    self.assertEqual(response.status, 200)
            request = Request(url + '/api/shadow', data=b'{}', headers={'Content-Type':'application/json'}, method='POST')
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, 403)
            request.add_header('Origin', url)
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_funding_quote_rejects_unsupported_and_invalid_amount(self):
        asset = dict(token=dict(address='0x'+'1'*40, decimals=18), minCheckoutUsd=2)
        with patch('polymarket_bot.dashboard.funding_assets', return_value=[asset]), patch('polymarket_bot.dashboard.urlopen') as send:
            for token, amount in [('other','10'), (asset['token']['address'],'NaN'), (asset['token']['address'],'-1')]:
                with self.assertRaises(ValueError):
                    funding_quote(dict(recipient='0x'+'2'*40, token=token, amount=amount))
            send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
