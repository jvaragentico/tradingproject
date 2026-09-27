# Validation record

The target of this first version is the **paper research implementation**, not a proven profitable strategy or a live-money trader.

Local verification on Windows, Python 3.13.7:

- Package installed into a workspace virtual environment with `websockets` 16.1.1.
- All 30 automated tests passed. Coverage includes FIFO all-in pair accounting, losing residuals, resolution, fee symmetry, invalid settings, model warmup, stale and duplicate spot data, signal reversal, order reservations, net share limits, queue position, partial fills, post-only rejection, cancellation latency, per-token tick changes, stale and old trades, deduplication, IOC depth and fee accounting, feed disconnects, receive-order validation, metadata mapping, and sample wallet analysis.
- Synthetic demo exercises buying both outcomes, pairing fills, leaving a residual, and official-event-style settlement. Replaying the same input gives identical actions and accounting. Its artificial profit is not market evidence.
- Live market discovery was checked against current BTC five-minute Gamma metadata, including the 0.07 fee rate, exponent 1, five-share minimum and 0.01 tick.
- The wallet downloader successfully fetched 1,000 recent public trade rows from the provided address. Sample statistics differ from the advertised lifetime figures; this does not certify or disprove the lifetime claim. The analyzer leaves audited P&L null.
- The optional Coinbase adapter's parser is unit tested. Its remote feed was unreachable in this environment. The default price feed is Polymarket's public Chainlink RTDS stream, whose live frame schema was independently inspected against the official client.

The 42-second default-feed smoke test passed on market `btc-updown-5m-1790526300`: 184 book snapshots, 28,438 deltas, 91 trades and 40 Chainlink spot updates. It created zero orders and replay produced identical actions and portfolio accounting. This checks connectivity and causality, not profitability. Its local artifacts are in `runs/live-smoke-1790526405/` and are ignored by Git.

Run `python scripts/smoke_public_feeds.py` to repeat that check. A successful run writes `runs/live-smoke-<timestamp>/smoke.json`.

GitHub Actions is configured for Python 3.11 and 3.13; local test success should not be confused with a remotely completed CI run. Long-duration shadow results, calibration, a full wallet ledger audit, live execution and live profitability remain unvalidated.


## Dashboard and wallet integration (2026-09-28)

35 Python tests pass, including dashboard/replay accounting reconciliation, partial JSONL writes, localhost HTTP assets, same-origin request restrictions, path validation and funding input rejection. Eight Node tests pass for exact decimal limits, ticks/minimum size, review acknowledgment, market expiry, missing MetaMask, BNB network rejection, account invalidation and geoblocking before signing. The official SDK 0.11.0 browser bundle builds with pinned dependencies; npm reports no known vulnerabilities.

The masked PowerShell launcher parses successfully and its default address action was exercised using a disposable, publicly known test key. It printed the expected address and exited without contacting the exchange. No user private key was inspected, stored or used in agent tests.

Rendered browser checks verified narrow and desktop layouts, causal demo P&L, Up-only ledger filtering (46 rows), Down book values, public recording source labels and the absent-MetaMask fallback. The official bridge supported-assets endpoint returned current BNB tokens. Funding quotes have input guard coverage.

Not verified: funded user authentication, live order acceptance, cancellations on a funded account, a user-specific bridge quote or transfer, and strategy profitability. The live interface is manually reviewed; the strategy engine remains paper-only. User signing and a funded, eligible account are required for real execution.
