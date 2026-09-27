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
| User-launched authenticated automatic executor | No operational worker wired to the reconciliation engine yet | Incomplete |
| Startup ownership/funding/positions reconciliation | Existing manual wallet checks are insufficient for autonomous execution | Incomplete |
| Automatic order lifecycle, ambiguous submissions and shutdown cancellation | Transport-independent controller and mocked lifecycle tests; authenticated worker still missing | Controller implemented; integration incomplete |
| Real-money execution validation | No user-money trade submitted by the agent | Unverified; user must initiate trading |
| Strategy performance validation | Synthetic demonstration and short public-feed smoke recordings only | No live edge established |

The reconciliation layer never fills orders from public trade prints and retains
reservations until an exchange cancellation acknowledgement. Confirmed account
fills include unique IDs, order identity, quantity, execution price and actual
fees. Duplicate IDs cannot change their payload. Late fills after cancellation
are counted. Public market resolution does not credit an unobserved redemption.

This audit does not declare the automatic trader complete. The next engineering
step is a user-launched authenticated worker with fresh-price gating, explicit
capital/loss caps, account-order reconciliation and scoped shutdown cancellation.

`frontend/automatic-controller.js` implements sequential order submission,
preflight signal rechecks, own-order cancellations and a hold on new submissions
while fills await confirmation. Ambiguous submission failures halt the controller
without retrying or marking the order rejected. It needs an authenticated
transport and an operational worker; importing it cannot trade.
