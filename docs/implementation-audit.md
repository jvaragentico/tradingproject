# Strategy replication audit

The implementation reproduces an observable strategy structure. The advertised
profit, average trade size, 90% two-sided participation, 94.5-cent pair cost and
40% imbalance are observations about another trader, not acceptance targets or
results of this bot. Its private model cannot be reconstructed from those figures.

| Requirement | Current evidence | Status |
| --- | --- | --- |
| Repeated fair probability estimates from crypto prices and time to expiry | `ProbabilityModel`, source adapters, tests | Implemented baseline; calibration unproven |
| Buy an outcome with estimated edge exceeding costs | `Engine.decide`, edge/fee/risk tests | Implemented in paper engine |
| Accumulate the opposite outcome after a reversal | Signal reversal test | Implemented in paper engine |
| Preserve initial inventory and account for complete sets | FIFO lot accounting, fee and resolution tests | Implemented |
| Preserve a directional residual and its loss risk | Inventory/portfolio tests and dashboard | Implemented |
| Real-time public data and causal recording/replay | Feed smoke check and dashboard/replay tests | Implemented |
| Dashboard reflecting actual recorded observations | HTTP, reconciliation and rendered controls checks | Implemented |
| Browser/local wallet manual signing | Official SDK integration and local masked-key launcher | Implemented; funded authentication unverified |
| Automatic execution using account fills rather than public prints | `ExecutionEngine`, partial/duplicate/late fill and cancel tests | Reconciliation layer implemented |
| User-launched authenticated automatic executor | Python worker and Node SDK runner with offline HTTP integration test | Implemented; funded authentication unverified |
| Startup ownership/funding/positions reconciliation | Startup collateral, open-order and market-position checks in SDK adapter | Implemented; tested with mocks |
| Automatic order lifecycle, ambiguous submissions and shutdown cancellation | Controller, SDK adapter, worker journal and scoped shutdown cancellation | Implemented; tested with mocks |
| Real-money execution validation | No user-money trade submitted by the agent | Unverified; user must initiate trading |
| Strategy performance validation | Synthetic demonstration and short public-feed smoke recordings only | No live edge established |

The reconciliation layer never fills orders from public trade prints and retains
reservations until an exchange cancellation acknowledgement. Confirmed account
fills include unique IDs, order identity, quantity, execution price and actual
fees. Duplicate IDs cannot change their payload. Late fills after cancellation
are counted. Public market resolution does not credit an unobserved redemption.

The user-launched worker now connects the external inventory engine to the SDK
adapter through capability-protected loopback HTTP. Its launcher requires explicit
local confirmation and caps. The end-to-end offline fixture exercises accepted
orders, opposite-side accumulation, partial fills, complete sets, directional
residuals, duplicate reconciliation and cancellation. Public TWAP/book connectivity
was checked with all order creation disabled. No account credentials or financial
transactions were used in these tests.

Funded authentication, live execution, model calibration, multi-market operation
and reliable automatic strike discovery are not established by these checks.
Shutdown warns if final settlement or cancellations cannot be fully reconciled.
An ambiguous submission requires the user to inspect account orders before a
restart. Importing the controller or runner never launches a trading session.
