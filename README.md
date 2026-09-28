# Polymarket dynamic hedging shadow bot

A Python research implementation of the observable strategy described for `pspspsps5`: estimate Up probability, buy an underpriced outcome, accumulate the opposite side as the signal changes, and track complete sets separately from the directional residual.

**Paper mode is the default.** The dashboard supports manually reviewed orders, and a separate user-launched local session can place automatic maker orders through Polymarket's official SDK. It does not reproduce the original trader's private model or verify the advertised +$246,578 profit. The probability model is an untrained baseline, and positive simulated returns are not evidence of a live edge.

## Install and run

Python 3.11 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m unittest discover -s tests -v
polybot demo --output runs/demo
```

On macOS/Linux, activate with `source .venv/bin/activate`. You can also use `python -m polymarket_bot` instead of `polybot`. Each demo/shadow output directory must be new to prevent recordings being overwritten.

## Dashboard and wallets

Run `polybot dashboard` and open http://127.0.0.1:8787/. The dashboard starts with a visibly labeled synthetic demo. Select public recordings or start a shadow capture with a current market slug and its official price to beat. Charts, inventory, simulated P&L, book depth and the fill ledger come from causal replay of the selected recording. The execution map is schematic. Export downloads that run's report.

For MetaMask, open the local URL in your normal extension-enabled browser. Some embedded browsers do not provide MetaMask or BroadcastChannel. Connect the signer, switch to Polygon and reconnect after account/network changes. Set the trading wallet address from your Polymarket profile when it differs from the signer. Use an existing funded account with its trading approvals configured through Polymarket. No deployment, token approval, bridging or private-key export runs automatically.

Real orders require a review checkbox and wallet signature, use post-only GTC BUY limits, and are capped at $23.59 each. The integration checks current market identity, expiration, tick, minimum size, collateral and eligibility. Prices are revalidated after signing; delayed signatures can expire. Open orders and cancel actions query the actual authenticated exchange. Disconnecting does not cancel resting orders. The paper strategy does not autonomously submit wallet trades.

The funding panel loads currently supported BNB Chain assets and requests a quote to Polygon pUSD collateral, showing destination, estimated output and fees. Quotes do not move funds. Complete the deposit through Polymarket after reviewing the quote and destination. A signer address may differ from the Polymarket trading wallet. Current funding documentation: https://docs.polymarket.com/trading/bridge/deposit.

### Masked PowerShell private-key alternative

Install the pinned Node dependencies with `npm ci` (Node 24+). Run the following from this project in your own PowerShell terminal:

```powershell
.\scripts\wallet.ps1
```

The default action derives the public address locally and exits. `Read-Host -AsSecureString` masks input; an anonymous stdin pipe carries the key to the local Node signer. No key is placed in argv, environment variables, browser storage, project files or logs. The key still exists temporarily in the signing process's memory. Do not paste it into chat.

Optional `-ExpectedSigner 0x...` fails if the entered key belongs to another address. Other commands, which each prompt for the key again:

```powershell
.\scripts\wallet.ps1 -Action balance -Wallet 0xYourPolymarketTradingWallet
.\scripts\wallet.ps1 -Action orders -Wallet 0xYourPolymarketTradingWallet
.\scripts\wallet.ps1 -Action buy -Wallet 0xYourPolymarketTradingWallet -Slug btc-updown-5m-CURRENT_TIMESTAMP -Outcome Up -Price 0.45 -Size 5
.\scripts\wallet.ps1 -Action cancel -Wallet 0xYourPolymarketTradingWallet -OrderId YOUR_ORDER_ID
```

Replace placeholders. `buy` requires typing BUY after its public order review, the running local dashboard for market checks, sufficient funded collateral, and an eligible location. The key does not bridge BNB Chain funds automatically. CLI errors are redacted to avoid revealing signing payloads. Actual funded authentication, bridging and execution require user-held credentials and have not been tested with user money.

Rebuild the bundled browser wallet module after edits with `npm run build`; run its guards with `npm test`. The generated bundle is included for Python installs.

The demo generates a **synthetic** market with a reversal. Its fabricated quotes deliberately illustrate paired inventory and do not represent historical Polymarket performance. Partial fills mean the average executed fill size will not equal the configured $23.59 order budget.

## Live shadow trading

```powershell
polybot discover --asset btc --interval 5m
```

Choose a current `btc-updown-5m-<UTC-start-seconds>` slug from the result. Read that market's **official price to beat** on Polymarket. Supply the actual value; do not substitute the latest exchange price. For example, replacing both placeholders:

```powershell
polybot shadow --slug YOUR_MARKET_SLUG --strike OFFICIAL_PRICE_TO_BEAT --seconds 300 --config config.example.json --output runs/shadow-001
polybot replay runs/shadow-001/events.jsonl --output runs/shadow-001/replayed.json
```

BTC, ETH, SOL and XRP 5/15-minute markets are supported. Token IDs are mapped by outcome labels, not array order. The runner verifies the interval against the official expiry and loads the market-specific tick, minimum order size and fee schedule. Unknown fee metadata stops the run rather than assuming free trades. The CLI rejects nonbinary and negative-risk markets.

The default runner subscribes to the public Polymarket CLOB WebSocket and Polymarket RTDS `crypto_prices_chainlink` stream. It accepts live updates for the selected asset and ignores the initial historical dump. Verify that the selected stream matches the specific market's resolution rules; freshness checks cannot eliminate delivery delays. Use `--spot-feed coinbase` to select the optional Coinbase Exchange ticker adapter. **Coinbase is a spot-price proxy, not the settlement oracle**, and basis differences can remove apparent edge. There is no automatic strike scraping or interval rollover: each new interval needs its own verified strike and new run. The prototype ends at the requested duration or observed official resolution. Expiry alone never invents a winner.

Recordings and reports are written to `runs/` and ignored by Git. A disconnect halts the run and preserves its partial recording. Start a new run after checking the failure. This research runner does not claim to recover real outstanding orders across disconnects.

## Strategy and execution assumptions

The model estimates

`P(Up) = NormalCDF((log(spot / strike) + bounded_drift * time_remaining) / (volatility * sqrt(time_remaining)))`.

It uses only observations already received, a rolling volatility estimate, a volatility floor, bounded momentum drift, and a warmup period. The estimated probability sets a continuously changing target net share position. The bot quotes the underpriced side when the fair-value edge exceeds its configured threshold and uncertainty allowance; after a reversal it can buy the opposite outcome instead of selling the existing inventory. It can also quote the opposite outcome when completing the oldest unmatched lot has sufficient all-in pair edge.

The reported 40% imbalance, 90% two-sided rate and 94.5-cent combined cost are **observations to investigate, not targets hard-coded into the strategy**.

- Maker orders are post-only, join the displayed best bid, and become active after simulated order latency. Quotes are canceled on an invalid signal or lifetime expiry, with cancellation latency.
- Queue ahead is the displayed size at activation. Only subsequently observed aggressive sells **at the exact quote price** can consume that queue and then partially fill our hypothetical order. Quote touches, bid deletions, cancellations ahead, and trades at another price do not create fills. Old and duplicate trade messages are rejected for matching.
- This public-L2 queue model is deliberately conservative but still cannot prove real fills. It does not model hidden liquidity, every trade allocation, market impact or production-grade exchange reconciliation. Missed trades can undercount fills.
- Taker execution is disabled by default. If enabled, it waits both order latency and the configured taker delay, rechecks its edge at arrival, walks displayed depth up to its limit, charges the actual market fee curve, and cancels the unfilled IOC remainder. The slippage buffer is an extra decision allowance, not fabricated cash expenditure.
- No maker or taker rebates and no liquidity rewards are credited. Configured latency values are assumptions, not measured infrastructure performance.
- All pending orders, including those awaiting cancellation, reserve cash and risk budget. Limits cover gross market spending, net shares, and worst-case terminal loss. Limits are per run/per market; this version does not coordinate a multi-market portfolio or a daily loss budget.
- The market and spot feeds must both be fresh by receive and source timestamps. Crossing/empty books pause new execution. The runner stops placing orders two seconds before expiry.

The default starting capital is $1,000, maximum gross spend is $250, worst-case loss cap is $50, and order budget is $23.59. These are research settings, not recommendations for funding a live account.

## Complete-set and P&L accounting

Fill costs include fees and are kept as decimal FIFO lots. Equal Up and Down quantities form complete sets; all matched pairs, including pairs costing more than $1, remain visible. The unmatched balance is directional exposure.

`paired terminal P&L = paired shares - their all-in acquisition cost`

Complete sets are valued at their terminal $1 payout. This is **not credited to available cash before resolution**, since no real or simulated merge is executed. Residual inventory is marked at the best bid for a conservative mark; this is not a depth-aware liquidation quote. `realized_pnl` stays null until an official resolution event. At resolution, paired shares plus the winning residual pay out once in the simulator. A profitable paired component can coexist with a losing overall position.

## Wallet trade investigation

```powershell
polybot wallet --address 0xb0f85baa97990910a3e8ac2b4a58a322f01ecef5 --max-pages 1 --output runs/wallet.json
polybot analyze-wallet runs/wallet.json --output runs/wallet-analysis.json
```

The address comes from the supplied brief; its identity and claimed lifetime P&L are not independently certified by the tool. Public samples are capped at 10 pages/10,000 returned rows and include maker trades (`takerOnly=false`). The API may return less. Pagination of a changing live dataset can omit rows even after deduplication.

The analysis restricts statistics to supported short-term crypto markets and reports observed trade sizes, gross buy VWAP pair costs, two-sided gross purchase frequency and gross purchase imbalance. **These are sample statistics, not remaining inventory or audited P&L.** Sales, transfers, splits, merges, settlement payouts and rebates need a full on-chain ledger reconstruction before making lifetime profitability or capital-requirement claims. The output explicitly marks complete wallet history as false and audited P&L as null.

## Recording format

JSONL begins with `{ "kind": "meta", "schema": 1, "market": {...}, "config": {...}, "source": "..." }`. Subsequent observations have `kind` and `ts` (UTC receive time in seconds). Supported kinds are `spot`, `book`, `delta`, `trade`, `tick`, `clock`, `disconnect` and `resolution`. Feed observations also retain `source_ts`.

Replay preserves receive order and rejects backward timestamps; it never sorts historical observations using future knowledge. Reports contain the configuration, action journal, actual simulated fills, queue behavior, fees, paired payoff and residual exposure. Do not describe a candle-only or synthetic replay as an HFT backtest. A useful evaluation needs timestamped order books, aggressive trades, the correct reference feed and official strikes over many independently resolved markets.

## Validation and next stage

```powershell
python -m unittest discover -s tests -v
python scripts/smoke_public_feeds.py
```

The live smoke test reads both public feeds for 42 seconds, checks both outcome snapshots, and verifies deterministic replay. It deliberately uses impossible edge thresholds so it creates zero orders; its dummy strike is **only for connectivity testing**. It needs internet access. Unit tests and the synthetic demo run offline, and GitHub Actions runs them on Python 3.11 and 3.13.

Remaining research includes the original wallet's full ledger audit, a calibrated probability model evaluated out of sample, verified strike automation and long-duration multi-market evaluation. Automatic execution is implemented for one explicitly selected market; funded authentication and real execution remain unverified. This is not evidence of a profitable live strategy.

Current source references checked on 2026-09-27:

- [Polymarket fees](https://docs.polymarket.com/trading/fees)
- [Market fee schedule and trading parameters](https://docs.polymarket.com/market-data/market-details)
- [Public market WebSocket and event schemas](https://docs.polymarket.com/market-data/realtime-data)
- [Market discovery](https://docs.polymarket.com/market-data/discover-markets)
- [Merging complete sets](https://docs.polymarket.com/trading/positions/manage)
- [Coinbase public ticker](https://docs.cdp.coinbase.com/exchange/websocket-feed/channels)
- [Official Polymarket RTDS client and Chainlink schemas](https://github.com/Polymarket/real-time-data-client)

## User-launched automatic trading

Automatic mode continuously feeds the strategy from public outcome books and the market's Chainlink reference stream. TWAP 60-second markets use `crypto_prices_twap_sixty`; unsupported resolution sources fail closed. The opening reference comes from the exact live Polymarket market interval or must be supplied explicitly; it is never substituted with a spot-price guess.

For a first, capped BTC 5-minute session, use the one-command PowerShell launcher. It discovers the current market, verifies its TWAP resolution metadata, reads Polymarket's live `openPrice` for that exact interval twice, and stops if either reading is missing, stale or inconsistent. `-CheckOnly` is read-only and needs no wallet. The live run prompts once for the private key locally and signs only through the existing wallet runner; do not paste the key into chat or a command line. The dashboard and browser-wallet connection are not required for this launcher.

```powershell
.\scripts\start-btc.ps1 -CheckOnly
.\scripts\start-btc.ps1
```

The live test session caps total market spend and loss at $3, uses the $50 account-value floor, and stops after 90 seconds or market expiry. It may place **zero** orders if the strategy finds no acceptable signal. It does not make a forced test bet. The opening-price endpoint is part of Polymarket's public site rather than a documented trading API; if its response changes or is unavailable, the launcher refuses to trade. The launcher runs one market, not a continuous sequence.

Run this from your own PowerShell after replacing the two placeholders with a current market slug and its official price to beat:

```powershell
.\scripts\wallet.ps1 -Action auto -Slug "btc-updown-5m-CURRENT_START_TIMESTAMP" -Strike OFFICIAL_PRICE_TO_BEAT -ExpectedSigner "0x8041Cc720aBC7DA28B056439aa2932Dbb879c408" -OrderDollars 3 -MaxSpend 10 -MaxLoss 3 -StopFloor 50 -Seconds 120
```

Use `-Wallet "PROFILE_TRADING_WALLET_ADDRESS"` if your funded Polymarket wallet differs from the signer. Verify it in your profile. The launcher displays the caps and requires `START`, then accepts the key in a hidden local prompt. Never send the key in chat. Existing account orders or positions in the selected market prevent startup. The account must already have funding and trading approvals configured through Polymarket.

After that local launch, each strategy intent is reviewed automatically for freshness, book identity, share precision, price tick, available collateral, eligibility and limits. Buys are post-only. Only confirmed account fills change inventory. Unconfirmed fills hold new submissions; ambiguous submission failures stop the session without retrying. The bot requests cancellation of its own orders on Ctrl+C, a feed failure, a risk halt or the session deadline, and checks for late fills afterward. If cancellation or final reconciliation is incomplete, it prints the IDs or a warning to inspect Polymarket before restarting.

This launcher accepts **BTC Up/Down 5-minute markets only**. `$100` is a goal to monitor, not an automatic stopping point or promised result. `-StopFloor 50` checks the funded trading wallet's collateral plus SDK-marked position value. It halts new buys at or below $50 and rejects a buy whose full cost would exceed the current cushion above $50. If the floor is reached while the session runs, it cancels its orders and attempts bounded fill-or-kill sells of only the BTC positions opened by that session. It records each sell attempt and confirmed settlement in `exit-attempts.jsonl`. Market value can fall through $50 before the next check, and an exit can fail for lack of liquidity, token approval, or account access. The floor is a circuit breaker, **not a guaranteed $50 balance**. Inspect the account after any floor exit warning.

Session journals are written under `runs/automatic-*`. Select that recording in the dashboard to see confirmed buy fills and session accounting; session cash is not the entire wallet balance, and the session report does not include any separate floor-triggered sale. Paired terminal value is not available cash or a recorded redemption. Automatic sessions stop after one market and do not bridge funds, deploy wallets, configure approvals, redeem resolved tokens or roll over to another market. Start each next BTC market with its verified price to beat; do not reuse the previous strike.

Validation includes offline SDK/controller checks and a Node-to-Python loopback integration test covering reversal hedging, partial/duplicate fills, complete sets, residual inventory and shutdown. Public-feed smoke checks disable all order creation. No funded account or real-money execution was used to validate this implementation.
