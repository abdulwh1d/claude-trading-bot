# Paper-trading research bot

A Python bot that pairs a language model with hard-coded risk rules, built from the X article
"How to Build a 24/7 Trading System" (@eng_khairallah1, Oct 11 2026) plus tips from
r/BuildWithClaude. Claude Opus 5.5 reads prices and news and writes a structured trade thesis.
Plain Python decides whether a trade happens and how big it is. Alpaca's paper account executes
with the stop attached to the order. A journal records every decision, including the trades the
system refused, and a scorecard measures how accurate the analyst really is.

It is the research side of [A.W. Strategy](../README.md): stocks (AAPL, MSFT, NVDA, SPY, QQQ) on
a paper account, graded forward only.

> An engineering project, not financial advice. It trades paper money only. Most trading
> strategies lose money, automated ones included, and an AI in the loop doesn't change that.

## Setup

1. Install Python 3.10 or newer.
2. In the repository root:
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
5. To stop everything, create a file named `KILL` in the repository root (from a phone through any
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
