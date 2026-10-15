import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradebot.sim import isolated_paths  # noqa: E402
from tradebot.thesis import TradeThesis  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path):
    """Every test writes its journal, state and reports into a temp folder."""
    with isolated_paths(tmp_path):
        yield tmp_path


def make_thesis(**overrides) -> TradeThesis:
    data = dict(symbol="AAPL", action="buy", confidence="high", entry_reason="test", stop_loss=170.0,
                invalidation="closes below 170", sources=["news id 1"])
    data.update(overrides)
    return TradeThesis(**data)
