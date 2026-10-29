# A.W. Strategy on TradingView

`tradingview/aw_strategy.pine` is a Pine Script v6 strategy for index and metal futures (NQ/MNQ,
ES/MES, RTY, YM, GC/MGC) on 1 to 30 minute charts. It marks setups, fires alerts and backtests in
TradingView's Strategy Tester. It never sends orders to a broker.

- [Install](#install)
- [The A.W. signal](#the-aw-signal)
- [Entries, exits and risk](#entries-exits-and-risk)
- [Folded-in indicators](#folded-in-indicators)
- [Dashboard](#dashboard)
- [Strategy Tester](#strategy-tester)
- [Backtest results](#backtest-results)
- [Ten more playbooks](#ten-more-playbooks)
- [GEX + anchored VWAP](#gex--anchored-vwap)
- [GEX, options-flow and forecast levels on the chart](#gex-options-flow-and-forecast-levels-on-the-chart)
- [Alerts](#alerts)
- [Limits of TradingView](#limits-of-tradingview)

## Install

1. Open the Pine Editor, paste `tradingview/aw_strategy.pine` over everything in it, save it as
   "A.W. Strategy" and click "Add to chart".
2. Copy [`gex/history.txt`](../gex/history.txt) and paste it into the strategy's settings under
   GEX levels → "GEX levels (paste gex/history.txt)". Paste it again whenever you want the newest
   levels; the [data pipeline](data-pipeline.md) refreshes it during the session.

On TradingView's free plan CME and COMEX futures are delayed (the "D" badge on the chart). Live
signals need TradingView's real-time CME data add-on.

## The A.W. signal

Ten factors, each met or not, scored on candle close:

| Factor | Met when |
|---|---|
| Structure | The last break of a 5-bar swing went the trade's way and the 30-minute structure agrees (the trend filter is a setting). A break against the trend is labelled CHoCH. |
| Fibonacci 79% | Price tagged the 79% retracement of the leg that broke structure in the last 3 bars and is still in the leg's lower half (upper half for shorts). |
| Level touched | Price touched a fair value gap or inverse gap (three-candle gaps of at least 0.15 ATR), the swing-low AVWAP (swing-high for shorts) or a volume-profile support (resistance). |
| Reaction | The candle closed back off that level: past a gap's midpoint, above the 79% level, or a bullish (bearish) close at the AVWAP or volume level. |
| Order flow | More of the last 10 gap reactions were bullish than bearish (bearish for shorts): support respected or resistance broken. |
| Delta | Buyers dominate the bar (delta z-score past +1.5 or 60%+ of the volume) and cumulative delta rose over the last 5 bars or a bullish CVD divergence formed (mirror for shorts). Delta comes from 1-minute bars inside each candle. |
| Volume | At least the 20-bar average. |
| Session | London 02:00–05:00, New York AM 08:30–11:00 or PM 13:30–15:30, New York time. |
| Major structure | The last break of a 20-bar swing went the trade's way. |
| Swing-AVWAP regime | Price is above the swing-low AVWAP (below the swing-high AVWAP for shorts). |

A signal needs structure, a touch of the 79% level or another level, a reaction there, a session
(a setting), a stop no wider than 3 ATR and a score of at least 7 of 10 (a setting). With
"Label A.W. entries and exits" on, hovering a signal shows its checklist.

Order flow is judged three ways rather than on single spikes: the delta z-score against the last
30 bars, the buyers' share of the volume, and the CVD trend. A SuperFlow-style flow score (−1 to
+1: absorption, delta z-score, big prints, effort versus result, CVD slope, rolling CVD and
volume) and big-print bubbles (1-minute bars that traded 4x the usual volume, in five sizes)
show the same picture at a glance.

## Entries, exits and risk

- **Entry** on the close of the signal candle. The Strategy Tester fills at the next bar's open.
- **Stop** beyond the reaction candle and the zone it came from, plus 0.25 ATR. Setups whose stop
  is wider than 3 ATR are skipped.
- **Targets** at 1R and 2R. The stop moves to breakeven after 1R.
- **Early exits** on a structure flip against the trade or when order flow is disrespected (a
  support gap closed through while long, a resistance gap while short).

## Folded-in indicators

Three community indicators, rebuilt in English as parts of the strategy (their ideas, not their
code):

| Indicator | In A.W. Strategy |
|---|---|
| Market Structure, by Leviathan | 20-bar swings labelled HH, HL, LH and LL, with major BOS and CHoCH lines. The last major break is the major-structure factor. |
| HaP AVWAP / Anchored VWAP, by agahakanaga (Turkish/English) | Two VWAPs anchored at the most important swing high and swing low of the last 500 bars (ranked by the move away from them, volume, wick, recency and whether they broke), with ±1 standard deviation bands. S and R mark bounces off the swing-low AVWAP and rejections at the swing-high AVWAP. Price above both is a bullish regime, below both bearish, in between balance. |
| Buyers & Sellers Profile + Dynamic S/R, by Zeiierman | Each 5-bar swing is checked against a volume profile of the 300 bars before it. A swing sitting on a heavy volume node becomes support or resistance, scored 0–100 by the node's size, its buying (or selling) share, how close it is, and the volume and wick at the swing. A close beyond a level removes it. The profile itself can be drawn too. |

Each has its own settings group and can be switched off.

## Dashboard

The default compact table (top right) shows four rows:

| Row | Shows |
|---|---|
| Delta | This bar's buying minus selling volume, the buyers' share, the CVD trend and any divergence |
| Z-score | This bar's delta against the last 30 bars, flagged past ±1.5 |
| Flow score | −1 (sellers in control) to +1 (buyers in control), hover for its parts |
| Now | The open trade, or what the setup is waiting for; hover for the long and short scores |

"Table: Full" in the settings shows every reading (structure, timeframes, Fibonacci, support and
resistance gaps, order flow, volume, GEX, session, score, history, GEX + AVWAP, Kronos, major
structure, swing AVWAPs, volume levels). A second table with the best long and short setup across
all playbooks can be switched on under Strategies.

## Strategy Tester

The Strategy Tester trades one set of signals at a time: the A.W. signal by default, or any
playbook picked under "Strategy Tester" in the settings. Defaults, all editable in the strategy's
Properties:

| Setting | Default |
|---|---|
| Initial capital | $50,000 |
| Size | 1 contract per trade |
| Commission | $1.25 per contract per order |
| Slippage | 1 tick |
| Margin | 5% |

Stops and targets rest as orders, so they fill inside the bar; rule-based exits (a structure
flip, order flow disrespected, a time stop) fill at the next bar's open.

## Backtest results

The A.W. signal with default settings, from TradingView's Strategy Tester on October 29, 2026,
including commission and slippage:

| Market | Chart | Period | Trades | Won | Profit factor | Net (1 contract) | Max drawdown |
|---|---|---|---|---|---|---|---|
| MNQ | 15 min | Apr 30 – Oct 29, 2026 | 281 | 31.3% | 0.99 | −$206 | $2,559 |
| NQ | 5 min | Sep 6 – Oct 29, 2026 | 177 | 31.1% | 0.97 | −$1,959 | $13,156 |
| ES | 5 min | Sep 6 – Oct 29, 2026 | 162 | 26.5% | 0.82 | −$4,667 | $5,476 |

That is roughly breakeven on MNQ 15 minutes and a loss on 5-minute NQ and ES after costs: no
edge yet. On MNQ 15 minutes longs made money (profit factor 1.25) and shorts lost (0.80) over a
rising market.

How the minimum score was chosen, on MNQ 15 minutes:

| Setting | Trades | Profit factor | Net |
|---|---|---|---|
| The old eight factors only (the folded-in indicators off) | 292 | 0.93 | −$1,411 |
| All ten factors, 6 of 10 | 314 | 0.94 | −$1,351 |
| All ten factors, 7 of 10 (default) | 281 | 0.99 | −$206 |
| All ten factors, 8 of 10 | 187 | 0.89 | −$1,482 |

7 of 10 also beat 6 of 10 on NQ 5 minutes (0.97 against 0.92) and ES 5 minutes (0.82 against
0.80). These are a few months of one market regime, and choosing the best of four settings on the
same data flatters it. Treat the strategy as a checklist that marks setups, not as proof of an
edge. How far back a backtest goes depends on how much history TradingView loads for the chart.

## Ten more playbooks

Each takes its own trades on candle close, prints its own small labels (▲ BO, ▼ RG and so on;
hover one for the reason) and keeps its own record. Any of them except Spread can be backtested by
picking it under "Strategy Tester".

| Playbook | Entry | Stop | Exit |
|---|---|---|---|
| Breakout | A close outside a tight 20-bar range (under 3 ATR tall) on 1.5x volume | Inside the range, under the breakout candle | 2R or the range height projected, whichever is further |
| Pullback | Trend up (structure plus 20 EMA over 50 EMA): a reversal candle at the 20 EMA, the 38–62% zone, or old resistance turned support | Under the last 5 bars | The last swing high |
| Range | 2+ touches of each side over 40 bars with ADX under 25: a reversal candle or RSI divergence near an edge | Just outside the edge, plus 0.25 ATR | The middle first, then the far edge |
| Mean reversion | A push outside the Bollinger band (20, 2) with RSI past 30/70, then a close back inside | Beyond the extreme of the push | The band middle (or VWAP) |
| News | A real move at a usual release time (8:30, 10:00, 14:00 ET): wait 10 minutes, then buy or sell the first pullback | 0.5 ATR beyond the pullback | 0.75R (stop to breakeven), then 1.5R, or out after an hour |
| Spread | NQ vs ES, or gold vs silver: the price ratio's z-score passes ±2 | z-score past ±3.5 | z-score back to 0. Scored in percent, not R |
| Scalping | 1–5 minute charts only: 9 EMA over 21 and a break of the last micro swing | 0.5 ATR | 1.25R, or a close back through the 9 EMA |
| Order flow | Absorption: heavy one-sided delta on high volume at a key level (session high or low, VWAP, the previous POC, a gap) while price doesn't follow, then a close back through that bar | Beyond the absorption bar | VWAP or the point of control, else 2R |
| GEX + order flow | Price tests a GEX level (the flip, a wall or a big strike) and closes back on its side, with absorption or a delta reversal | Beyond the level, plus 0.25 ATR | The next GEX level at least 1R away, else 2R |
| GEX + AVWAP | See below | Beyond the test's extreme (reversal) or the level (continuation), plus 0.25 ATR | The next GEX level at least 1R away, else 2R |

The optional best long / short table proposes, for each side, the plan with the best record on
the current chart among those within 4 ATR of price that offer at least 1R. Records with few
trades are discounted. Levels are estimates, not orders.

## GEX + anchored VWAP

From the video "Stop Guessing If a Gamma Exposure (GEX) Level Will Reverse — Use AVWAP" (Grateful
Futures Trading University): GEX tells you *where* price may turn, and a VWAP anchored at the start
of the move into the level tells you *whether* that move is still in control. The move's start is
the highest high (for a drop into support) or lowest low (for a rally into resistance) of the last
48 bars, and the move must be at least 1.5 ATR.

- **Reversal:** price holds the level and closes back through the AVWAP: the move lost control.
- **Continuation:** the level breaks while the AVWAP holds, price moves away, then a retest of the
  level holds. A close back through the AVWAP ends the trade.

An idea expires after 24 bars without a trigger. The AVWAP is drawn in purple: solid while a level
is being tested or a trade is open, dotted from the current move's start otherwise.

## GEX, options-flow and forecast levels on the chart

The levels come from the [data pipeline](data-pipeline.md), in the options' own prices, and are
moved onto the futures with the futures-to-index ratio from regular-hours bars.

"GEX levels to show" picks how many:

| Setting | Drawn |
|---|---|
| Trusted only (default) | The call wall (thick green), the put wall (thick red) and the gamma flip (dashed orange), plus any of the 8 biggest GEX strikes where today's option volume is heavy too (★ flow), until it breaks. Gold, silver, crude, gas and bonds show only their walls and flip. The GEX signals use the same levels. |
| Strong levels | The walls, the flip and the 8 biggest strikes |
| Full ladder | Also every strike within 1.5% of price, green for positive GEX and red for negative, bolder the more gamma it holds |

The walls and the 3 biggest strikes are grade A, the next 5 grade B. L marks support below price,
S resistance above, with a status for the day: ✓ held (a test closed back with absorption or a
delta flip), ✗ broke (closed through with delta behind it: don't fade it) or · retest (touched
before without a result). Above the flip dealers tend to damp moves; below it they amplify them.

**Options-flow (OV) levels.** TradingView's Options Volume Profile only draws on 1D and up, and
Pine can't read another indicator's data, so the busiest option strikes near price come from the
same CBOE data and are drawn on every timeframe as dotted lines: teal when calls dominate, pink
when puts do. They come from the index and ETF options (NDX + QQQ for NQ), not the CME futures
options, so the strikes are close to the Options Volume Profile's but not identical.

**Kronos forecast.** With "Table: Full", the "Kronos 2h" row shows the forecast close in 1 and 2
hours and how many of its recent 1-hour direction calls were right. It stays grey until at least
10 calls are scored and 55% were right. An experiment: don't trade it on its own.

## Alerts

Create alert → Condition "A.W. Strategy" → "alert() function calls only". One alert then covers
every A.W. entry and exit and every playbook entry, with the stop and target in the message.
Order-fill alerts from the Strategy Tester work too.

## Limits of TradingView

- **Order flow:** Pine has no order book, time and sales or footprint data on regular plans, so
  delta comes from 1-minute bars and volume profiles from bar ranges.
- **News:** Pine has no economic calendar. The News playbook watches the usual release times and
  only trades a real move there.
- **Spread:** both legs are shown, but TradingView can't place a two-leg order, so Spread isn't
  available in the Strategy Tester.
