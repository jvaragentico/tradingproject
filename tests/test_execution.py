import unittest

from polymarket_bot.core import Config, D, Market
from polymarket_bot.execution import ExecutionEngine


class FixedModel:
    def probability(self, *args):
        return .8


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.engine = ExecutionEngine(Market('btc-updown-5m-0','up','down',100,0,300),
                                      Config(capital=30,max_loss=20,max_market_spend=30))
        self.engine.model = FixedModel()
        self.engine.last_decision = float('inf')
        for token, bid, ask in [('up','.44','.48'),('down','.45','.49')]:
            self.engine.ingest(dict(kind='book',ts=100,token=token,bids=[[bid,'2']],asks=[[ask,'100']]))
        self.engine.last_decision = 0
        self.engine.ingest(dict(kind='clock',ts=100.1))
        self.intent = self.engine.intents()[0]
        self.local_id = self.intent['local_id']

    def test_public_prints_never_fill_external_inventory(self):
        self.engine.ingest(dict(kind='trade',ts=100.4,token='up',price='.44',size='100',side='SELL'))
        self.assertFalse(self.engine.portfolio.fills)
        self.assertEqual(self.engine.portfolio.cash,D(30))
        self.assertFalse(self.engine.orders[0].active)

    def test_cancel_reservation_held_until_exchange_ack(self):
        self.engine.acknowledge(self.local_id,'exchange-1')
        self.engine.ingest(dict(kind='disconnect',ts=101,feed='clob'))
        self.engine.ingest(dict(kind='clock',ts=110))
        self.assertEqual(len(self.engine.orders),1)
        self.assertTrue(self.engine.intents()[0]['cancel'])
        self.engine.canceled(self.local_id)
        self.assertFalse(self.engine.orders)

    def test_confirmed_partial_fills_and_duplicate_reconciliation(self):
        self.engine.acknowledge(self.local_id,'exchange-1')
        quantity=self.engine.orders[0].remaining
        self.assertEqual(quantity,quantity.quantize(D('.01')))
        self.assertTrue(self.engine.execution_fill(self.local_id,'fill-1','5','.44','0'))
        self.assertFalse(self.engine.execution_fill(self.local_id,'fill-1','5','.44','0'))
        with self.assertRaises(ValueError):
            self.engine.execution_fill(self.local_id,'fill-1','6','.44','0')
        self.assertEqual(self.engine.portfolio.residual('Up'),D(5))
        self.assertEqual(self.engine.orders[0].remaining,quantity-D(5))

    def test_invalid_fills_cannot_mutate_inventory(self):
        with self.assertRaises(ValueError):
            self.engine.execution_fill(self.local_id,'fill-1','5','.44','0')
        self.engine.acknowledge(self.local_id,'exchange-1')
        for shares,price,fee,status in [('1000','.44','0','CONFIRMED'),('5','.45','0','CONFIRMED'),('5','.44','0','MATCHED'),('5','.44','-1','CONFIRMED')]:
            with self.assertRaises(ValueError):
                self.engine.execution_fill(self.local_id,'fill-1',shares,price,fee,status)
        self.assertFalse(self.engine.portfolio.fills)

    def test_late_confirmed_fill_after_cancel_is_accounted(self):
        self.engine.acknowledge(self.local_id,'exchange-1')
        self.engine.canceled(self.local_id)
        self.engine.execution_fill(self.local_id,'fill-1','5','.44','0')
        self.assertEqual(self.engine.portfolio.residual('Up'),D(5))

    def test_resolution_does_not_credit_unobserved_redemption(self):
        self.engine.acknowledge(self.local_id,'exchange-1')
        self.engine.execution_fill(self.local_id,'fill-1','5','.44','0')
        cash=self.engine.portfolio.cash
        self.engine.ingest(dict(kind='resolution',ts=300,winner='up'))
        self.assertEqual(self.engine.portfolio.cash,cash)
        self.assertFalse(self.engine.portfolio.settled)
        self.assertIsNone(self.engine.portfolio.realized)
        self.engine.execution_fill(self.local_id,'fill-2','5','.44','0')
        self.assertEqual(self.engine.portfolio.residual('Up'),D(10))


if __name__ == '__main__':
    unittest.main()
