# Claude trading bot (paper trading)

Built from the X article "How to Build a 24/7 Trading System" (@eng_khairallah1, Oct 11 2026) plus
tips from r/BuildWithClaude. Claude Opus 5.5 reads prices and news and writes a structured trade
thesis. Plain Python decides whether a trade happens and how big it is. Alpaca's paper account
executes with the stop attached to the order. A journal records every decision, including the
trades the system refused, and a scorecard measures how accurate the analyst really is.

> An engineering project, not financial advice. It trades paper money only. Most trading
> strategies lose money, automated ones included, and an AI in the loop doesn't change that.

The `tradingview/` folder holds a separate futures dashboard for TradingView. It's described
[at the end](#tradingview-futures-dashboard).

## Setup

1. Install Python 3.10 or newer.
2. In this folder:
   ```
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill it in:
   - `ANTHROPIC_API_KEY` from console.anthropic.com
   - `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` from your **paper** account at app.alpaca.markets
   - optional: `SEC_USER_AGENT="Your Name you@example.com"` adds SEC filings to the overnight research
4. Try it, step by step:
   ```
   python bot.py simulate         # offline run against a fake market. No keys needed.
   python bot.py check            # keys work, account is paper, data and models reachable
   python bot.py analyze NVDA     # one thesis and its risk verdict. No order.
   python watchdog.py             # run 24/7; restarts the bot if it crashes
   ```
5. To stop everything, create a file named `KILL` in this folder (from a phone through any
   file-sync app works too), or run `python bot.py kill`. Open orders are canceled, positions are
   closed, and the bot exits.

The computer has to stay on and awake for "24/7". That's how the Reddit builder ran it too.

## How it maps to the article

| Article | Where |
|---|---|
| Step 1: stack, paper connection | `tradebot/broker.py`, `python bot.py check` |
| Step 2: the thesis as a type | `tradebot/thesis.py` |
| Step 3: news is untrusted input | `tradebot/untrusted.py` |
| Step 4: the analyst (Opus 5.5) | `tradebot/analyst.py` |
| Step 5: risk engine in plain code | `tradebot/risk.py`, numbers in `config.py` |
| Step 6: execute on paper, stop attached | `tradebot/executor.py`, `tradebot/broker.py` |
| Step 7: journal every decision | `tradebot/journal.py` |
| Step 8: kill switch | `tradebot/killswitch.py` |
| Step 9: the loop | `tradebot/loop.py` |
| Step 10: cheap overnight shift (Sonnet 5.5, Batch API) | `tradebot/overnight.py`, `tradebot/filings.py` |
| Before the open / after the close | `tradebot/premarket.py`, `tradebot/review.py` |
| The backtesting trap | `tradebot/scorecard.py` grades forward, never on past news |
| Reddit: watchdog, bracket orders, skipped trades, baselines, one falsifiable hypothesis a day | `watchdog.py`, `executor.py`, `scorecard.py`, `review.py` |

## What I changed in the article's code, and why

1. **Its enum fix can't run.** Anthropic's docs confirm enum capitalization isn't guaranteed
   ("Buy" instead of "buy"). But Pydantic rejects "Buy" inside `messages.parse()` before the
   article's `.lower()` checks ever run, and its loop has no error handling, so the bot would
   crash. The thesis model now normalizes case before validating.
2. **The stop can expire overnight.** The article's order uses `TimeInForce.DAY`. Alpaca users
   report the attached stop takes the entry's time in force, so it can lapse at the close.
   Orders here are GTC, which is what Alpaca's own OTO and bracket examples use, and every cycle
   re-attaches any stop that went missing.
3. **It keeps buying the same stock.** The risk engine sizes each order, not each position, and
   the loop runs every 15 minutes. A model that keeps saying "buy AAPL" adds another 5% each cycle.
   Now there's one position per symbol.
4. **"sell" was ambiguous.** The article's executor turns any non-buy into a sell order with a stop
   attached, which opens a short. It also only checks stop direction for buys. Here "sell" closes
   the long you hold. Shorting is off unless you enable it, and then a short's stop must sit above
   entry.
5. **Exits were blocked after a bad day.** The daily loss limit blocked every trade, including
   selling to cut risk. Exits are always allowed now.
6. **A hijacked thesis could trade the wrong stock.** If an injected headline makes the model
   return NVDA while it's analyzing AAPL, the article sizes NVDA with AAPL's price. That's
   rejected now.
7. **Cost.** As written, the loop calls Opus for all 5 symbols every 15 minutes: about 130 calls a
   day, mostly on identical inputs. Now a symbol is re-analyzed only on a new headline, a 1% move,
   or a 2-hour-old thesis, and a hard daily cap ($5) applies. In simulation that cut calls by about 80%.
8. **The kill switch took up to 15 minutes.** It's checked every 5 seconds now.
9. **One error stopped everything.** A failed API call crashed the loop. Errors are journaled
   instead, and a watchdog restarts the bot if it really does crash.
10. **The journal didn't record what the system read.** The article promises that record, but its
    code leaves it out. The price, headlines, model, effort and cost are now saved with every
    decision, and the scorecard needs them.
11. `messages.parse()` raises before you can read the token usage or `stop_reason` when the output
    fails validation, which is exactly when refusals and `max_tokens` cutoffs happen. The analyst
    does the same two steps by hand (send the schema, validate the text), so those calls are still
    counted and journaled.

**What the article got right**, checked against Anthropic's docs and SDK source (anthropic 1.14.0)
in October 2026. Opus 5.5 costs $4 / $20 per million tokens, and $2 / $10 through the Batch API.
Sonnet 5.5 costs $2 / $10. Opus 5.5 has adaptive thinking always on, with a default effort of
`medium`. Forced `tool_choice` returns a 400 error on Opus 5.5. Structured outputs through
`client.messages.parse(output_format=...)` work as described. The enum capitalization caveat is
real.

## Measuring accuracy

You can't backtest this honestly. The model was trained on the past, so shown a 2025 headline it
"predicts" what it already knows happened. So `scorecard.py` grades only forward: every thesis is
saved when it's written and scored later against prices that came after it.

`python bot.py scorecard` (also rebuilt after every close) reports:

- **Directional hit rate**: did the price move the way the thesis said, 1 and 5 sessions later?
  It comes with a 95% confidence interval, compared against the always-buy baseline from the same
  moments.
- **Calibration**: are "high" confidence calls right more often than "medium" ones? It also
  compares effort levels after `python bot.py compare-effort`.
- **Approved vs rejected**: the hypothetical R-multiple of both groups. If the ideas the rules
  rejected beat the ones they approved, the rules are filtering out winners.
- **Holds**: how often a big move followed a "hold" (the moves you didn't trade).
- **Account vs SPY buy-and-hold** over the same days, after API costs (before taxes).

Twenty identical "hold AAPL" calls in one morning count as one opinion, not twenty. Below 50
observations the report labels every hit rate as noise. Expect mostly holds, so reaching a real
sample will take a while. Waiting it out is how you find out whether this is skill or luck.

## What it costs

Alpaca's paper trading and its IEX data and news feed are free. Anthropic usage is billed per
token. My rough estimate at the defaults is $15 to $35 a month: about 20 to 25 Opus calls per
trading day, a few cents each, plus a small overnight batch and one daily review. The article's
loop as written would cost about 5 times that. Actual spend is logged per call in
`data/costs.jsonl` and summarized in the scorecard.

## Commands

| Command | What it does |
|---|---|
| `python bot.py check` | Verify keys, paper account, market data, model access |
| `python bot.py analyze NVDA MSFT` | Thesis and risk verdict now, no order (journaled as "shadow") |
| `python bot.py compare-effort NVDA --levels medium,high` | Same inputs, several effort levels |
| `python bot.py once` | One loop cycle now |
| `python bot.py run` | Run 24/7 (prefer `python watchdog.py`) |
| `python bot.py overnight --wait` | Submit the overnight research batch now and wait for it |
| `python bot.py premarket` | Write today's pre-market brief now |
| `python bot.py review` | After-close review: scorecard, daily CSV, one hypothesis |
| `python bot.py scorecard` | Accuracy report |
| `python bot.py pending` / `approve ID` / `reject ID` | Manual approval mode (see below) |
| `python bot.py kill` | Emergency stop |
| `python bot.py simulate --days 30 --kill-day 20` | Offline end-to-end run on a fake market |

## Key settings (`config.py`)

- Watchlist and allowed symbols: AAPL, MSFT, NVDA, SPY, QQQ.
- Risk: 5% max position, 1% max risk per trade, 3% daily loss limit, 5 positions max, entries need
  "high" confidence.
- `TAKE_PROFIT_R = 2.0` puts a broker-side target at 2x the risk (a bracket order, the Reddit
  tip). Set it to `None` for stop-only orders exactly as in the article.
- `MANUAL_APPROVAL = True` is the article's stricter first phase. Approved entries wait in
  `data/pending.jsonl` until you run `python bot.py approve <id>`, which re-checks them against
  fresh prices first.
- `ANALYST_EFFORT = "medium"`. Don't raise it by habit. Compare effort levels against the journal
  first.
- `MAX_DAILY_LLM_USD = 5.00`.

## Files it writes

- `data/journal.jsonl`: every decision (thesis, verdict, order, what it read, cost)
- `data/events.jsonl`: kill switch, errors, re-attached stops, restarts
- `data/costs.jsonl`: every model call and its price
- `data/briefs/`: overnight research
- `reports/scorecard.md`, `reports/daily_summary.csv`, `reports/hypotheses.jsonl`, `reports/premarket_*.md`
- `logs/bot.log`

## Going live

Don't, until weeks of paper results have earned it. Even then the bot refuses unless
`config.PAPER = False`, a file named `GO-LIVE` contains the sentence in `tradebot/golive.py`, and
the journal shows at least 20 paper sessions.

## Tests

```
pip install pytest
pytest
```

67 tests. They cover the article's worked example ($100,000 account, $180 entry, $170 stop:
27 shares), every risk rule, injection handling, the kill switch, stop re-attachment, the budget
cap and the scorecard math. Contract tests run the real `anthropic` and `alpaca-py` clients
against local fake servers, so the exact requests the bot sends are checked without touching your
accounts.

## TradingView futures dashboard

`tradingview/ict_confluence_dashboard.pine` is a Pine Script v6 indicator for NQ/MNQ, ES/MES and
GC/MGC on 5 to 30 minute charts. It draws on your chart and fires alerts. It never places orders.

**Install:** open the Pine Editor, paste the file over everything in it, save, then click
"Add to chart".

**What it reads**

| Factor | How |
|---|---|
| Break of structure | A candle closes beyond the last pivot swing (5 bars each side). A break against the trend is labeled CHoCH. |
| Fibonacci 79% | The 79% retracement of the leg that broke structure, drawn as a dashed line. |
| FVG / IFVG | Three-candle gaps of at least 0.15 ATR. A gap that price closes through flips into an inverse FVG on the other side. Only the 6 nearest are drawn. |
| Order flow | Each gap tap is *respected* (a reaction candle closes past its midpoint) or *disrespected* (closed through). The last 10 reactions set the bias. |
| Delta | Built from 1-minute bars inside each candle: up-bar volume counts as buying and down-bar volume as selling. This approximates bid/ask delta, which TradingView only exposes on its top plans. It's judged three ways: a z-score against the last 30 bars (past ±1.5 is unusual), the buyers' share of the volume (60%+ means one side dominates), and the CVD trend over 5 bars (net delta the same way on most of them), not single spikes. Also flags CVD divergence at swings. |
| Flow score | A −1 to +1 composite after [SuperFlow](https://github.com/profitnotiondaily/atasx-superflow), with its weights: absorption, delta z-score, big prints, effort vs result, CVD slope, rolling CVD and volume. ±0.5 is "in control", ±0.2 "leaning". Hover the row for each part. Its footprint-only parts (stacked imbalances, big resting orders) aren't available in Pine. |
| Big prints | Circles where one 1-minute bar traded 4x the usual 1-minute volume, green if it closed up and red if down. The bigger the print, the bigger the circle: five sizes, at 1x, 1.5x, 2.25x, 3.5x and 5x that threshold. A bar-level stand-in for the trade bubbles of Bookmap-style tools like [OrderFlowMap](https://github.com/Azhagesan-dev/OrderFlowMap). |
| Volume | Relative to the 20-bar average. |
| GEX | Dealer gamma levels from the option chains: the gamma flip, the call and put walls, and the biggest GEX strikes. See "GEX levels" below. |
| Timeframes | 5m, 15m and 30m structure. By default the 30m has to agree with the trade. Higher timeframes only use closed bars, and signals print on candle close, so they don't repaint. |
| Session | London 02:00–05:00, NY AM 08:30–11:00, NY PM 13:30–15:30, New York time. |

**Entries and exits.** A LONG or SHORT prints on candle close when structure and the 30m agree,
price reacts at the 79% level or a gap, it's inside a session, and at least 5 of the 8 factors
line up. Hover the label to see which ones. The stop goes beyond the reaction candle and its zone
plus 0.25 ATR. Targets are 1R and 2R, and the stop moves to breakeven after 1R. SELL or COVER
prints on the stop, the second target, a structure flip, or order flow being disrespected (a
support gap closed through while long, a resistance gap while short). The "Now" row says what to
wait for while you're flat.

**Nine more strategies.** Nine playbooks run alongside the ICT signals. Each takes its own trades
on candle close, prints its own small labels (▲ BO, ▼ RG and so on; hover one for the reason), and
keeps its own record, which shows in the best long / short table.

| Strategy | Entry | Stop | Exit |
|---|---|---|---|
| Breakout | A close outside a tight 20-bar range (under 3 ATR tall) on 1.5x volume | Inside the range, under the breakout candle | 2R or the range height projected, whichever is further |
| Pullback | Trend up (structure plus 20 EMA over 50 EMA): a reversal candle at the 20 EMA, the 38–62% zone, or old resistance turned support | Under the last 5 bars | The last swing high |
| Range | 2+ touches of each side over 40 bars with ADX under 25: a reversal candle or RSI divergence near an edge | Just outside the edge, plus 0.25 ATR | The middle first, then the far edge |
| Mean reversion | A push outside the Bollinger band (20, 2) with RSI past 30/70, then a close back inside | Beyond the extreme of the push | The band middle (or VWAP) |
| News | A real move at a usual release time (8:30, 10:00, 14:00 ET): wait 10 minutes, then buy or sell the first pullback | 0.5 ATR beyond the pullback | 0.75R (stop to breakeven), then 1.5R, or out after an hour |
| Spread | NQ vs ES, or gold vs silver: the price ratio's z-score passes ±2 | z-score past ±3.5 | z-score back to 0. Scored in percent, not R |
| Scalping | 1–5 minute charts only: 9 EMA over 21 and a break of the last micro swing | 0.5 ATR | 1.25R, or a close back through the 9 EMA |
| Order flow | Absorption: heavy one-sided delta on high volume at a key level (session high or low, VWAP, the previous POC, a gap) while price doesn't follow, then a close back through that bar | Beyond the absorption bar | VWAP or the point of control, else 2R |
| GEX + order flow | Price tests a GEX level (the flip, a wall or a big strike) and closes back on its side, with absorption on that bar or a delta reversal (sellers dominated the bar before and buyers dominate this one, or the reverse) | Beyond the level, plus 0.25 ATR | The next GEX level at least 1R away, else 2R |

What TradingView can't do, and what stands in for it:

- **Order flow:** there's no order book (DOM), time and sales, or footprint data in Pine on regular plans. Delta comes from 1-minute bars and the volume profile from 1-minute closes.
- **News:** there's no economic calendar in Pine. It watches the usual release times and only trades when a real move happens there. Check a calendar for the day's events.
- **Spread:** it shows both legs, but TradingView can't place a two-leg order.

**Best long / short table.** A second table (bottom right) shows, for each side, the best plan
right now: the strategy, entry, stop, exit, reward-to-risk, distance from price, and that
strategy's record. Every strategy proposes a plan, and the table keeps those within 4 ATR of price
that offer at least 1R. It then picks the one with the best record on the chart you're looking at.
Records with few trades are discounted. An open trade always counts. Levels are estimates, not
orders. GEX + order flow proposes a limit entry at the nearest GEX level on each side.

**GEX levels.** Gamma exposure (GEX) estimates where options dealers' hedging pushes price or pins
it, and order flow shows whether buyers or sellers actually win at those levels. The levels come
from CBOE's free, delayed option chains: SPX plus SPY for ES, NDX plus QQQ for NQ, GLD for gold.
The ETF's options are moved onto the index's scale with the same dollar gamma and added in, since
much of the hedging sits there (QQQ's open interest dwarfs NDX's), and strikes are grouped into
5-point (SPX) and 25-point (NDX) buckets.
`gex/gex_levels.py` computes them the way
[Options-Gamma-Exposure-Analysis-Tool](https://github.com/CodeCraftIA/Options-Gamma-Exposure-Analysis-Tool)
and [gex-explained](https://github.com/FlashAlpha-lab/gex-explained) do: GEX per contract = gamma ×
open interest × 100 × spot² × 1%, calls positive and puts negative, expiries up to 60 days out. The
gamma flip is where total GEX changes sign as spot moves (a Black-Scholes sweep from −10% to +10%).
The call wall is the strike with the most positive GEX within 5% of spot and the put wall the
most negative. The ladder is every strike within 2% of spot (the range a day trades in) holding
at least 1% of the biggest one's GEX: about 60 levels for SPX, 30+ for NDX and 15 for GLD.

A GitHub Action (`.github/workflows/gex-levels.yml`) runs it before the open (12:45 UTC) and
every 30 minutes through the session, and commits `gex/latest.txt` and `gex/history.txt`. To
refresh by hand: Actions → "GEX levels" → "Run workflow". Pine can't download files, so paste
before the open and again whenever you want the latest levels (mid-morning and after lunch is
plenty):

1. Open `gex/history.txt` on GitHub and copy all of it.
2. In the indicator's settings, paste it into "GEX levels (paste gex/history.txt)" under GEX levels.

The levels are in the index's or ETF's own prices. The indicator moves them onto the futures with
the futures-to-index ratio from regular-hours bars, so futures' premium over the index is taken
care of. On the chart: every strike within 1.5% of price ("Ladder range" in the settings, up
to 2%) is a line, green for positive GEX and red for negative, bolder and thicker the more gamma
it holds (scaled to the 85th percentile in range, so one huge wall doesn't wash the rest out).
The call wall is a thick green line, the put wall thick red, the flip dashed orange. The 12
biggest strikes ("GEX levels to label") get a label with the price and GEX in $ billions per 1%
move; labels too close to another are skipped. Lines start at the day's first snapshot, and the
chart always shows the newest snapshot, even one taken over the weekend or before the open. The GEX row says whether
price is above the flip (positive gamma: dealers damp moves, walls tend to hold) or below it
(negative gamma: dealers amplify moves) and gives the next level above and below price; hover it
for the walls. Levels more than 36 hours old are flagged.

history.txt keeps every run from the latest day and the first run of each earlier day, with the
full ladder only on each underlying's newest run (older runs keep their 8 biggest strikes, which
is all the signals use), so it stays under Pine's 40,960-character limit, so the
GEX + order flow record covers every day since the Action started. There's no free source of past
GEX, so that record only builds forward from Oct 21 2026: give it a few weeks before trusting it.
GEX rests on assumptions about who is long and short the options (dealers long calls and short
puts), the chains are 15 minutes delayed and open interest updates once a day. Both GEX and order
flow are probabilistic, and order flow is noisy outside liquid sessions.

Panel positions, text size, which strategies run and every threshold above are in the
indicator's settings.

**Alerts.** Create alert → Condition "ICT Confluence Dashboard" → "Any alert() function call".
One alert then covers every entry and exit, ICT and the nine strategies, with the stop and
target in the message. The named conditions (long, short, sell, cover, strategy entry) work too.

**Data.** On TradingView's free plan, CME and COMEX futures are delayed (the "D" badge on the
chart). Live signals need TradingView's real-time CME data add-on.

**History.** These are the dashboard's own stats over the history TradingView loaded on
Oct 15 2026, before fees and slippage:

| | 5 min | 15 min | 30 min |
|---|---|---|---|
| NQ | 92 trades, 33% won, +0.04R | 161 trades, 28% won, −0.02R | 327 trades, 33% won, +0.07R |
| ES | 81 trades, 30% won, +0.05R | 175 trades, 27% won, −0.03R | 317 trades, 29% won, 0R |
| GC | 70 trades, 23% won, −0.14R | 175 trades, 24% won, −0.11R | 326 trades, 27% won, −0.08R |

That's roughly breakeven on the index futures and negative on gold. Use it as a checklist that
marks the setups, not as proof of an edge.

How far back the records go depends on how much history TradingView loads for the chart. On the
free plan in October 2026 that was about four weeks on 5 minutes and back to January on 30
minutes. The History row prints its start date. Records built on a handful of trades mean little.
