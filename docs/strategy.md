# A.W. Strategy on TradingView

`tradingview/aw_strategy.pine` is a Pine Script v6 indicator for index and metal futures (NQ/MNQ,
ES/MES, RTY, YM, GC/MGC) on 5 to 30 minute charts. It marks setups, draws ranked GEX and options-flow levels with a plan for each, and fires alerts. It
never places orders.

- [Install](#install)
- [What's on the chart](#whats-on-the-chart)
- [The A.W. signal](#the-aw-signal)
- [Entries and exits](#entries-and-exits)
- [GEX and options-flow levels](#gex-and-options-flow-levels)
- [Ten more playbooks](#ten-more-playbooks)
- [GEX + anchored VWAP](#gex--anchored-vwap)
- [Optional tables](#optional-tables)
- [HaP AVWAP in English](#hap-avwap-in-english)
- [Alerts](#alerts)
- [Limits of TradingView](#limits-of-tradingview)

## Install

1. Open the Pine Editor, paste `tradingview/aw_strategy.pine` over everything in it, save it as
   "A.W. Strategy" and click "Add to chart".
2. Copy [`gex/history.txt`](../gex/history.txt) and paste it into the settings under GEX levels →
   "GEX levels (paste gex/history.txt)". Pine can't download data, so paste it again whenever you
   want the newest levels; the [data pipeline](data-pipeline.md) refreshes the file during the
   session. Until it's pasted, the chart shows a reminder where the levels would be.

On TradingView's free plan CME and COMEX futures are delayed (the "D" badge on the chart). Live
signals need TradingView's real-time CME data add-on.

## What's on the chart

Nothing covers the chart. The three numbers next to "A.W. Strategy" in the status line (top left)
are:

| Value | Meaning |
|---|---|
| Delta | This bar's buying minus selling volume, green when buyers won the bar |
| Flow score | −1 (sellers in control) to +1 (buyers in control); ±0.5 is in control, ±0.2 leaning |
| Z-score | This bar's delta against the last 30 bars; past ±1.5 is unusual |

On the chart itself: breaks of structure (BOS, CHoCH), the 79% retracement, fair value gaps and
inverse gaps, big-print bubbles, LONG / SHORT signals with their exits, the playbooks' small
labels, the GEX + AVWAP line and the [GEX and options-flow levels](#gex-and-options-flow-levels).
Hover any label for the details.

## The A.W. signal

Eight factors, each met or not, scored on candle close:

| Factor | Met when |
|---|---|
| Structure | The last break of a 5-bar swing went the trade's way and the 30-minute structure agrees (the trend filter is a setting). A break against the trend is labelled CHoCH. |
| Fibonacci 79% | Price recently tagged the 79% retracement of the leg that broke structure and is still in the leg's lower half (upper half for shorts). |
| Gap touched | Price touched a fair value gap or inverse gap this bar or the last (three-candle gaps of at least 0.15 ATR; one closed through flips into an inverse gap). |
| Reaction | The candle closed back off it: past the gap's midpoint, or a bullish (bearish) close back above (below) the 79% level. |
| Order flow | More of the last 10 gap reactions were bullish than bearish (bearish for shorts): support respected or resistance broken. |
| Delta | Buyers dominate the bar (delta z-score past +1.5 or 60%+ of the volume) and cumulative delta rose over the last 5 bars or a bullish CVD divergence formed (mirror for shorts). Delta comes from 1-minute bars inside each candle. |
| Volume | At least the 20-bar average. |
| Session | London 02:00–05:00, New York AM 08:30–11:00 or PM 13:30–15:30, New York time. |

A LONG or SHORT needs structure, a touch of the 79% level or a gap, a reaction there, a session (a
setting), a stop no wider than 3 ATR and a score of at least 5 of 8 (a setting). Hover the label
to see which factors lined up.

The flow score follows [SuperFlow](https://github.com/profitnotiondaily/atasx-superflow) with its
weights: absorption, delta z-score, big prints, effort versus result, CVD slope, rolling CVD and
volume. Big prints are 1-minute bars that traded 4x the usual volume, drawn as circles in five
sizes.

## Entries and exits

- **Entry** on the close of the signal candle.
- **Stop** beyond the reaction candle and the zone it came from, plus 0.25 ATR. Setups whose stop
  is wider than 3 ATR are skipped.
- **Targets** at 1R and 2R. The stop moves to breakeven after 1R.
- **Exits** (SELL or COVER) on the stop, the second target, a structure flip against the trade, or
  order flow disrespected (a support gap closed through while long, a resistance gap while short).

## GEX and options-flow levels

Gamma exposure (GEX) estimates where options dealers' hedging pushes price or pins it. The levels
come from the [data pipeline](data-pipeline.md) in the options' own prices and are moved onto the
futures with the futures-to-index ratio from regular-hours bars.

**Strong levels (the default):**

| Tag | Meaning |
|---|---|
| Call wall, Put wall | The strikes with the most positive and most negative GEX (thick lines). Grade A. |
| γ flip | Where dealers' total gamma changes sign (dashed orange). Above it moves tend to be calm and levels hold more; below it moves are faster and levels break more often. |
| #1 to #8 | The 8 biggest strikes by GEX, ranked by size. #1 to #3 are grade A, #4 to #8 grade B. |
| ★ OV #n | The level is also one of today's busiest option strikes (#1 is the busiest), so it's more likely to matter. |

L marks support below price (look for a bounce long), S resistance above (look for a bounce
short). Each level gets a status for the day: ✓ held (a test closed back with absorption or a
delta flip), ✗ broke (closed through with delta behind it: don't fade it) or · retest (touched
before without a result, so weaker). Hover a level for its plan: wait for the candle at the level
to close with absorption or delta flipping your way, then enter on the pullback, not the spike.

**Options-flow levels.** OV #1 to #4 mark today's busiest option strikes near price as dotted
lines: teal when calls dominate, pink when puts do. TradingView's Options Volume Profile only
draws on daily charts and Pine can't read another indicator's data, so these come from the same
CBOE data and show on every timeframe. They use the index and ETF options (NDX + QQQ for NQ), not
the CME futures options, so the strikes are close to the Options Volume Profile's but not
identical. Hover one for its plan.

Other settings under "GEX levels to show":

| Setting | Drawn |
|---|---|
| Trusted only | The walls and the flip, plus a big strike only with ★ OV and only until it breaks. Gold, silver, crude, gas and bonds show only their walls and flip. |
| Full ladder | Also every strike within 1.5% of price, green for positive GEX and red for negative, bolder the more gamma it holds |

When the newest levels are more than 36 hours old, the walls and the flip say "old" with their
date.

## Ten more playbooks

Each takes its own trades on candle close, prints its own small labels (▲ BO, ▼ RG and so on;
hover one for the reason) and keeps its own record.

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

## Optional tables

Both are off so nothing covers the chart; switch them on in the settings.

- **Dashboard** (Dashboard → "Show the dashboard table"): structure, the 5/15/30-minute trend, the
  79% level, the nearest support and resistance gaps, order flow, delta, flow score, volume, GEX,
  session, the long and short scores, what the setup is waiting for, the signal's record, GEX +
  AVWAP and the Kronos 2-hour forecast (an experiment: grey until at least 10 calls are scored and
  55% were right).
- **Best long / short** (Strategies → "Show the best long / short table"): for each side, the plan
  with the best record on the current chart among those within 4 ATR of price that offer at least
  1R. Records with few trades are discounted. Levels are estimates, not orders.

## HaP AVWAP in English

`tradingview/hap_avwap_english.pine` is an English translation of "HaP AVWAP / Anchored VWAP" by
agahakanaga (Mozilla Public License 2.0, kept with the author's credit). The logic and defaults
are the original's; every setting, label, tooltip and alert is in English, and its info panel (a
table) starts off. Install it the same way and save it as "HaP AVWAP (English)".

## Alerts

Create alert → Condition "A.W. Strategy" → "Any alert() function call". One alert then covers
every A.W. entry and exit and every playbook entry and exit, with the stop and target in the
message. Single conditions are listed too: long signal, short signal, sell (exit long), cover (exit
short) and any playbook entry.

## Limits of TradingView

- **Order flow:** Pine has no order book, time and sales or footprint data on regular plans, so
  delta comes from 1-minute bars and volume profiles from bar ranges.
- **News:** Pine has no economic calendar. The News playbook watches the usual release times and
  only trades a real move there.
- **Spread:** both legs are shown, but TradingView can't place a two-leg order.
- **GEX data:** Pine can't download files, so the levels have to be pasted into the settings.
