"""Every rule and setting in one place.

Change these numbers to fit your situation, but write them down before you ever
see a trade. Rules decided in advance are rules. Rules decided in the moment are
moods.
"""
from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paper vs live
# --------------------------------------------------------------------------- #
# Never change this until you have weeks of paper results you trust.
# Even then, the bot refuses to trade live unless a file named GO-LIVE exists
# (see README, "Going live").
PAPER = True

# --------------------------------------------------------------------------- #
# Watchlist and risk rules (Step 5). Plain code enforces these; no model can.
# --------------------------------------------------------------------------- #
WATCHLIST = ["AAPL", "MSFT", "NVDA", "SPY", "QQQ"]
ALLOWED_SYMBOLS = set(WATCHLIST)

MAX_POSITION_PCT = 0.05     # no single position above 5% of equity
MAX_RISK_PER_TRADE = 0.01   # never risk more than 1% of equity on one trade
MAX_DAILY_LOSS_PCT = 0.03   # no new trades after a 3% loss on the day
MAX_OPEN_POSITIONS = 5
ENTRY_CONFIDENCE = "high"   # only high-confidence theses open positions
EXIT_CONFIDENCE = "medium"  # closing a position reduces risk, so the bar is lower

# The article leaves these out; they close real gaps in its risk engine.
ALLOW_SHORTS = False            # "sell" closes a long. It never opens a short unless True.
MIN_STOP_DISTANCE_PCT = 0.005   # a stop closer than 0.5% is noise, not a plan
MAX_STOP_DISTANCE_PCT = 0.15    # a stop further than 15% away is not a plan either
TAKE_PROFIT_R = 2.0             # broker-held take-profit at 2x the risk (bracket order).
                                # Set to None for stop-only OTO orders, exactly as in the article.

# Stricter first phase from the article: approved trades wait in a pending file
# until you approve them with `python bot.py approve <id>`.
MANUAL_APPROVAL = False
PENDING_EXPIRY_MINUTES = 30

# --------------------------------------------------------------------------- #
# Models (Step 4 and Step 10)
# --------------------------------------------------------------------------- #
ANALYST_MODEL = "claude-opus-5-5"
ANALYST_EFFORT = "medium"        # Opus 5.5's default. Don't raise it by habit; test it
ANALYST_MAX_TOKENS = 16000       # (`python bot.py compare-effort`) against the journal first.

SUMMARIZER_MODEL = "claude-sonnet-5-5"   # overnight filings and headlines, via the Batch API
SUMMARIZER_EFFORT = "medium"
SUMMARIZER_MAX_TOKENS = 8000     # thinking counts toward max_tokens, so leave room

REVIEW_MODEL = "claude-sonnet-5-5"       # after-close review: one falsifiable hypothesis
REVIEW_WITH_LLM = True

# USD per million tokens. Checked against Anthropic's docs in October 2026.
# Batch requests cost 50% of these. Cache prices are Opus 5.5's published
# multipliers (write 1.25x, read 0.05x); the Sonnet ones assume the same.
PRICING = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
}
BATCH_DISCOUNT = 0.50
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.05

# --------------------------------------------------------------------------- #
# Cost controls. The article's loop calls Opus for every symbol every 15
# minutes (about 130 calls a day for 5 symbols). These keep it sane.
# --------------------------------------------------------------------------- #
MAX_DAILY_LLM_USD = 5.00          # hard cap per day; above it the analyst stays quiet (= hold)
REANALYZE_MOVE_PCT = 0.01         # re-analyze a symbol when price moved 1% since its last thesis,
REANALYZE_MAX_AGE_MIN = 120       # or its last thesis is 2 hours old,
                                  # or a new headline arrived. Otherwise skip the call.

# --------------------------------------------------------------------------- #
# Loop timing (Step 9) and the 24/7 schedule
# --------------------------------------------------------------------------- #
CYCLE_MINUTES = 15
KILL_POLL_SECONDS = 5                    # how often the KILL file is checked while sleeping
OVERNIGHT_SUBMIT_HOURS_BEFORE_OPEN = 10  # submit the overnight batch ~10h before the open
PREMARKET_MINUTES_BEFORE_OPEN = 60       # build the pre-market brief 1h before the open
PREMARKET_THESES = True                  # also write candidate theses for flagged symbols
PREMARKET_GAP_FLAG_PCT = 0.01            # flag a symbol that gapped 1% overnight

# --------------------------------------------------------------------------- #
# Data inputs. Keep them tight: specific, recent information, not a day of noise.
# --------------------------------------------------------------------------- #
DATA_FEED = "iex"            # free Alpaca accounts get real-time IEX data
NEWS_LOOKBACK_HOURS = 24
MAX_NEWS_ITEMS = 8
DAILY_BARS = 30
STALE_PRICE_MINUTES = 15     # don't size a trade off a price older than this

# SEC EDGAR (overnight filings). The SEC requires a User-Agent with your name and
# email: set SEC_USER_AGENT="Jane Doe jane@example.com" in .env. Leave it unset to
# skip filings (headlines are still summarized).
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "").strip()
FILING_FORMS = ("8-K", "10-Q", "10-K")
FILING_LOOKBACK_DAYS = 3
FILING_MAX_CHARS = 40_000

# --------------------------------------------------------------------------- #
# Scorecard (accuracy)
# --------------------------------------------------------------------------- #
HORIZONS = (1, 5)            # score each thesis 1 and 5 full sessions later
BIG_MOVE_PCT = 0.02          # a hold followed by a 2%+ move counts as a big move missed
MIN_SAMPLE = 50              # below this, a hit rate is noise; the report says so

# --------------------------------------------------------------------------- #
# Files
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
JOURNAL_FILE = DATA_DIR / "journal.jsonl"   # every decision, holds and rejections included
EVENTS_FILE = DATA_DIR / "events.jsonl"     # kill switch, errors, protective stops, restarts
COSTS_FILE = DATA_DIR / "costs.jsonl"       # every model call and what it cost
PENDING_FILE = DATA_DIR / "pending.jsonl"
STATE_FILE = DATA_DIR / "state.json"
BRIEFS_DIR = DATA_DIR / "briefs"
REPORTS_DIR = ROOT / "reports"
LOG_DIR = ROOT / "logs"
KILL_FILE = ROOT / "KILL"
GO_LIVE_FILE = ROOT / "GO-LIVE"
