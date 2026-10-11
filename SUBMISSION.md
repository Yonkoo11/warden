# Warden: submission to BNB Hack, Tokenized Stocks Edition

Filed late on 2026-10-11 (the form closed at 12:00 UTC and was still accepting entries that evening). Public repo: https://github.com/Yonkoo11/warden. Live: https://warden-production-e5c3.up.railway.app.

## Form answers

**Team or project name:** Warden

**Tracks:** Main track: Tokenized Stocks Products & Agents; Best Use of Agentic Wallet / Wallet Skills

**Public repository URL:** https://github.com/Yonkoo11/warden

**Deployed link:** https://warden-production-e5c3.up.railway.app (the judge page is /judge; receipts are /r/<id>)

**What did you build?**

Warden lets someone who holds a tokenized US stock on BNB Chain write one rule around an earnings report, for example "sell half of my APLDon 60 minutes before the print", and have it carried out through the Binance Agentic Wallet they paired in the Binance app. The people it is for hold bStocks, Ondo or xStocks tokens and cannot sit up for a 4:05pm New York print. Binance has said most bStocks volume in July traded while US markets were closed. That is when these holders are asleep.

Before anything is signed, Warden runs a gate and writes the result on a receipt. The gate checks the wallet session (baw wallet status and settings, with the 48 hour expiry), the trading lock, the issuer's live status from Binance's RWA endpoints, the balance, a market-order quote against the published per-share price, and the wallet's daily quota. An ASSET_LIMITED earnings flag is a refusal. MARKET_CLOSED waits and records the next open. A refusal gets the same receipt as an executed order. Earnings dates come from the Nasdaq calendar joined to Binance's token list, so a holder only sees prints for tokens in their wallet.

Binance Web3 API parts used: the public RWA Data endpoints (market status, per-asset status with reason codes, the token list with multipliers, dynamic v2 for holders and fundamentals, K-lines), the signed aggregator quote during research, and the Agentic Wallet CLI for everything that touches money: wallet status, settings, tx-lock, balance, market-order quote, swap and list.

Tokenized stocks it touches: APLDon and PEPon (Ondo) in the live test and probes, plus any BSC stock token Binance lists. The judge page currently shows JPM, GS, ASML, TSM, TSLA, NFLX and about 30 more reporting in the next two weeks across the on, x and B wrappers.

On 5 Oct I ran the live test from my own wallet: bought 5 USDT of APLDon and sold half back through the Agentic Wallet. Three market orders finished on BSC mainnet. The hashes are in the README and on the home page. Two things are not done. A scheduled rule has not yet fired on its own, because the 7 Oct rule missed when the wallet session expired before the one hour check. And the hosted copy's wallet session has to be re-paired by me every 48 hours.

## What is actually running

- Live: https://warden-production-e5c3.up.railway.app, Docker on Railway, scheduler on, started 2026-10-05
- Executor: Binance Agentic Wallet CLI (file-session mode); the session on the hosted copy is paired from the owner's phone and expires after 48 h
- Public pages: home (holdings and prints for any BSC address), /judge, /r/<id> receipts, /health

## Proof

| claim | evidence |
|---|---|
| Agentic Wallet execution on BSC mainnet | 0x49ba5e6a19af7544eb45bdb8f32b46e67af51cb101173b96eed1dd31ebf56985, 0xb16358f5bc6d2ea9e19289d018e04847b5498ad29d0803b95c588496f2b330b6, 0x6ac479545c300e5fbb58d46408b0f99af46b9b71804b9f5eb33e90f170007eb4 (2026-10-05 03:36 UTC); raw responses in probe/swaps.jsonl |
| Round trip cost | buy +0.27% vs mid, sell −0.49% vs mid on 5 USDT; gas about 0.00003 BNB |
| Gate and scheduler | 29 tests, fake executor and fake clock, including a full rule run that ends "executed" with a receipt |
| Live data | every page load reads Binance's public RWA endpoints and the Nasdaq calendar |

## What it does not do yet

A scheduled rule has not fired live. Transaction API simulation is not used, because Agentic Wallet market orders have no preview step to feed it. There are no limit orders, no Agent Studio agent and no Telegram bot. Each holding gets one rule, and rules only sell. Warden never suggests a rule or a ticker, so there is no alpha claim to check.

## Sponsor integration depth

If the Binance RWA status endpoint went away, the earnings refusal would go with it. Without the Agentic Wallet CLI nothing can be signed, since I built no fallback executor. The token list and its multipliers are what make the per-share price right for Ondo tokens with a total-return multiplier (PEPon's is 1.035). Every receipt records which Binance call said what.
