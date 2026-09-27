"""Exchange-driven inventory for a user-launched executor.

Public prints never create fills here. Pending cancellations retain reservations
until the exchange acknowledges them. This module does not sign or send orders.
"""

from decimal import Decimal, ROUND_DOWN
import math

from .core import Engine, D, ZERO


class ExecutionEngine(Engine):
    def __init__(self, market, config=None):
        super().__init__(market, config)
        self.next_id = 1
        self.registry = {}
        self.exchange_ids = {}
        self.confirmed_fills = {}

    def ingest(self, event):
        if event.get("kind", "").startswith("execution_"):
            ts = float(event["ts"])
            if not math.isfinite(ts) or ts < self.now:
                raise ValueError("Invalid execution update timestamp")
            previous_time = self.now
            self.now = ts
            try:
                kind = event["kind"]
                local_id = event.get("local_id")
                if kind == "execution_ack":
                    self.acknowledge(local_id, event["exchange_id"])
                elif kind == "execution_reject":
                    self.rejected(local_id)
                elif kind == "execution_cancel":
                    self.canceled(local_id)
                elif kind == "execution_fill":
                    self.execution_fill(local_id, event["fill_id"], event["shares"], event["price"], event["fee"], event["status"])
                elif kind == "execution_stop":
                    self.halted = "executor_stopped"
                    self.cancel()
                else:
                    raise ValueError("Unknown execution update")
            except (ValueError, KeyError, TypeError):
                self.now = previous_time
                raise
            return super().ingest(dict(kind="clock", ts=ts))
        if event.get("kind") != "resolution":
            return super().ingest(event)
        ts = float(event["ts"])
        if not math.isfinite(ts) or ts < self.now or ts < self.market.end:
            raise ValueError("Invalid execution resolution timestamp")
        self.market.outcome(event["winner"])
        self.now = ts
        self.events += 1
        self.halted = "resolution_requires_account_reconciliation"
        self.cancel()
        # A public resolution is not an actual collateral redemption. Continue
        # accepting confirmed fills and never credit an unobserved cash payout.
        self.log("resolution_observed", winner=event["winner"])

    def cancel(self, immediate=False):
        # Even on a disconnect, retain risk reservations until cancellation ACK.
        for order in self.orders:
            if order.cancel_at is None:
                order.cancel_at = self.now
                self.log("cancel_requested", side=order.side, local_id=self.local_id(order))

    def local_id(self, order):
        return next((key for key, value in self.registry.items() if value is order), None)

    def advance(self, healthy):
        for order in self.orders:
            if order.cancel_at is None and self.now - order.created >= self.config.quote_lifetime:
                order.cancel_at = self.now
                self.log("cancel_requested", side=order.side, local_id=self.local_id(order), reason="expired_quote")

    def trade(self, event):
        pass  # Public trades provide no evidence that this account was filled.

    def fill(self, *args):
        raise RuntimeError("Only confirmed account execution updates may change inventory")

    def decide(self):
        before = {id(order) for order in self.orders}
        super().decide()
        for order in list(self.orders):
            if id(order) in before:
                continue
            # Official SDK share precision; never increase the reserved quantity.
            order.remaining = order.remaining.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            if order.mode != "maker" or order.remaining < D(self.market.min_size):
                self.orders.remove(order)
                self.log("execution_intent_rejected", reason="maker_only_or_below_minimum")
                continue
            local_id = str(self.next_id)
            self.next_id += 1
            self.registry[local_id] = order
            self.log("execution_intent", local_id=local_id, side=order.side,
                     shares=str(order.remaining), price=str(order.price))

    def acknowledge(self, local_id, exchange_id):
        order = self.registry[local_id]
        if not isinstance(exchange_id, str) or not exchange_id:
            raise ValueError("Missing exchange order ID")
        if local_id in self.exchange_ids:
            if self.exchange_ids[local_id] != exchange_id:
                raise ValueError("Order acknowledgement changed identity")
            return
        if exchange_id in self.exchange_ids.values():
            raise ValueError("Exchange order ID is already assigned")
        self.exchange_ids[local_id] = exchange_id
        order.active = True
        self.log("exchange_ack", local_id=local_id, exchange_id=exchange_id)

    def rejected(self, local_id):
        if local_id in self.exchange_ids:
            raise ValueError("Cannot treat an accepted order as rejected")
        order = self.registry[local_id]
        if order in self.orders:
            self.orders.remove(order)
        self.log("exchange_rejected", local_id=local_id)

    def canceled(self, local_id):
        if local_id not in self.exchange_ids:
            raise ValueError("Cancellation requires a known exchange order")
        order = self.registry[local_id]
        if order in self.orders:
            self.orders.remove(order)
        self.log("exchange_canceled", local_id=local_id)

    def execution_fill(self, local_id, fill_id, shares, price, fee, status="CONFIRMED"):
        if status != "CONFIRMED":
            raise ValueError("Inventory requires a confirmed, reconciled account fill")
        if not isinstance(fill_id, str) or not fill_id:
            raise ValueError("Missing unique fill ID")
        key = (local_id, fill_id)
        quantity, price, fee = D(shares), D(price), D(fee)
        payload = (quantity, price, fee)
        if key in self.confirmed_fills:
            if self.confirmed_fills[key] != payload:
                raise ValueError("Confirmed fill changed its payload")
            return False
        if local_id not in self.exchange_ids:
            raise ValueError("Fill belongs to an unacknowledged order")
        order = self.registry[local_id]
        if quantity <= ZERO or quantity > order.remaining or price > order.price or fee < ZERO:
            raise ValueError("Fill violates the submitted order")
        if self.portfolio.settled:
            raise ValueError("Reconcile fills before settling the portfolio")
        self.portfolio.buy(order.side, quantity, price, fee, self.now, "exchange_maker")
        order.remaining -= quantity
        self.confirmed_fills[key] = payload
        if order.remaining == ZERO and order in self.orders:
            self.orders.remove(order)
        self.log("confirmed_fill", local_id=local_id, fill_id=fill_id, side=order.side,
                 shares=str(quantity), price=str(price), fee=str(fee))
        return True

    def intents(self):
        return [dict(local_id=self.local_id(o), exchange_id=self.exchange_ids.get(self.local_id(o)),
                     side=o.side, price=str(o.price), shares=str(o.remaining),
                     cancel=o.cancel_at is not None) for o in self.orders]

    def report(self):
        result = super().report()
        result.update(paper_only=False, execution_mode="exchange_confirmed", execution_intents=self.intents())
        result["limitations"][2] = "Inventory requires confirmed account fills; public prints never fill orders."
        return result
