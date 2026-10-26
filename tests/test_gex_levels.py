"""GEX levels script: parsing CBOE's format, the math, and the output files."""
import importlib.util
import json
import math
import os
from datetime import datetime

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("gex_levels", os.path.join(HERE, "..", "gex", "gex_levels.py"))
gex = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gex)

NOW = datetime(2026, 10, 21, 8, 45, tzinfo=gex.NY)


def occ(root, expiry, cp, strike):
    return f"{root}{expiry:%y%m%d}{cp}{int(round(strike * 1000)):08d}"


def chain(spot, put_oi, call_oi, strikes, iv=0.2, days=7, root="SPXW"):
    """A CBOE-shaped payload: heavy puts below spot, heavy calls above."""
    expiry = datetime(2026, 10, 21 + days)
    t = days / 365.0
    opts = []
    for k in strikes:
        for cp, oi in (("C", call_oi(k)), ("P", put_oi(k))):
            opts.append({"option": occ(root, expiry, cp, k), "open_interest": oi, "iv": iv,
                         "gamma": gex.bs_gamma(spot, k, t, iv)})
    return {"timestamp": "2026-10-21 08:45:00", "data": {"current_price": spot, "options": opts}}


def test_occ_parsing_handles_fractional_strikes_and_weeklies():
    payload = {"data": {"current_price": 245.3, "options": [
        {"option": occ("GLD", datetime(2026, 10, 23), "C", 245.5), "open_interest": 10, "iv": 0.2, "gamma": 0.1},
        {"option": occ("SPXW", datetime(2026, 10, 23), "P", 5800), "open_interest": 5, "iv": 0.2, "gamma": 0.1},
        {"option": "garbage", "open_interest": 5},
        {"option": occ("GLD", datetime(2027, 6, 18), "C", 250), "open_interest": 10, "iv": 0.2, "gamma": 0.1},
        {"option": occ("GLD", datetime(2026, 10, 23), "C", 250), "open_interest": 0, "iv": 0.2, "gamma": 0.1},
    ]}}
    spot, rows = gex.parse_chain(payload, NOW)
    assert spot == 245.3
    assert [(r["strike"], r["cp"]) for r in rows] == [(245.5, "C"), (5800.0, "P")]   # far expiry and 0 OI dropped


def test_contract_gex_sign_and_scale():
    # 1 call, gamma 0.01, OI 1000, spot 100: 0.01 * 1000 * 100 * 100^2 * 1% = 100,000
    assert gex.contract_gex(0.01, 1000, 100, "C") == pytest.approx(100_000)
    assert gex.contract_gex(0.01, 1000, 100, "P") == pytest.approx(-100_000)


def test_bs_gamma_matches_closed_form_at_the_money():
    s = k = 100.0
    t, iv = 0.25, 0.2
    d1 = 0.5 * iv * math.sqrt(t)
    expected = math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi) / (s * iv * math.sqrt(t))
    assert gex.bs_gamma(s, k, t, iv) == pytest.approx(expected)


def test_walls_and_flip_land_where_the_open_interest_is():
    spot = 5800.0
    strikes = [5600 + 25 * i for i in range(17)]                        # 5600 .. 6000
    payload = chain(spot,
                    put_oi=lambda k: 20000 if k == 5700 else (2000 if k < spot else 100),
                    call_oi=lambda k: 20000 if k == 5900 else (2000 if k > spot else 100),
                    strikes=strikes)
    s, rows = gex.parse_chain(payload, NOW)
    lv = gex.levels(s, rows)
    assert lv["cw"] == 5900 and lv["pw"] == 5700
    assert 5700 < lv["flip"] < 5900                                     # balanced book: flip between the walls
    assert {5700, 5900} <= {k for k, _ in lv["ladder"]}
    assert [k for k, _ in lv["ladder"]] == sorted(k for k, _ in lv["ladder"])


def test_put_heavy_book_puts_the_flip_above_spot():
    spot = 5800.0
    strikes = [5600 + 25 * i for i in range(17)]
    payload = chain(spot, put_oi=lambda k: 8000, call_oi=lambda k: 1000 if k > 5850 else 200, strikes=strikes)
    s, rows = gex.parse_chain(payload, NOW)
    lv = gex.levels(s, rows)
    assert lv["total"] < 0                                               # negative gamma at spot
    assert lv["flip"] is None or lv["flip"] > spot


def test_output_line_and_files(tmp_path):
    spot = 5800.0
    strikes = [5600 + 25 * i for i in range(17)]
    payload = chain(spot, put_oi=lambda k: 3000 if k < spot else 300, call_oi=lambda k: 3000 if k > spot else 300,
                    strikes=strikes)
    src = tmp_path / "chains"
    src.mkdir()
    for name in gex.UNDERLYINGS:
        (src / f"{name}.json").write_text(json.dumps(payload))
    out = tmp_path / "out"
    assert gex.main(["--file", str(src), "--now", "2026-10-21T08:45", "--out", str(out)]) == 0
    latest = (out / "latest.txt").read_text().splitlines()
    assert [ln.split(" ")[1] for ln in latest] == list(gex.UNDERLYINGS)
    first = latest[0].split(" ")
    assert first[0] == "2026-10-21T08:45"
    fields = dict(p.split("=", 1) for p in first[2:])
    assert set(fields) == {"spot", "flip", "cw", "pw", "total", "k", "src"}
    assert fields["src"] == "SPX+SPY"
    for pair in fields["k"].split(","):
        k, g = pair.split(":")
        float(k), float(g)
    # A second run the next day appends; a rerun of the same stamp replaces.
    gex.main(["--file", str(src), "--now", "2026-10-22T08:45", "--out", str(out)])
    gex.main(["--file", str(src), "--now", "2026-10-22T08:45", "--out", str(out)])
    hist = (out / "history.txt").read_text().splitlines()
    assert len(hist) == 2 * len(gex.UNDERLYINGS)
    assert hist == sorted(hist, key=lambda s: s.split(" ")[0])


def test_gex_numbers_never_use_exponents():
    # Pine's str.tonumber can't parse "4e-05", so tiny GLD values must stay plain decimals.
    assert gex.fmt_gex(10.934) == "+10.9"
    assert gex.fmt_gex(-4.8) == "-4.8"
    assert gex.fmt_gex(0.03124) == "+0.0312"
    assert gex.fmt_gex(-0.00004) == "-0.00004"
    assert gex.fmt_gex(1234.5) in ("+1234", "+1235")
    assert gex.fmt_gex(0.0) == "+0"
    for x in (3e-9, -7.7e-6, 0.5, 12345.678):
        s = gex.fmt_gex(x)
        assert "e" not in s.lower() and float(s) == pytest.approx(x, rel=0.01, abs=1e-9)


def test_history_fits_in_one_pine_string(tmp_path, monkeypatch):
    monkeypatch.setattr(gex, "MAX_HISTORY_CHARS", 30_000)                # small cap so it binds
    path = tmp_path / "history.txt"
    pad = "k=" + ",".join(f"{7000 + 25 * i}:+1.23" for i in range(15))
    lines = [f"2026-{m:02d}-{d:02d}T08:45 {u} spot=1 flip=1 cw=1 pw=1 total=+1 {pad}"
             for m in (8, 9) for d in range(1, 29) for u in gex.UNDERLYINGS]
    gex.merge_history(str(path), lines)
    text = path.read_text()
    assert len(text) <= gex.MAX_HISTORY_CHARS
    kept = text.splitlines()
    stamps = sorted({ln.split(" ")[0] for ln in kept})
    assert 10 < len(stamps) < gex.KEEP_DAYS                               # trimmed by size, not count
    assert kept[-1].startswith("2026-09-28T08:45")                        # newest runs survive
    assert all(sum(ln.startswith(s) for ln in kept) == len(gex.UNDERLYINGS) for s in stamps)  # whole runs only


def test_one_bad_feed_does_not_stop_the_rest(tmp_path):
    src = tmp_path / "chains"
    src.mkdir()
    good = chain(5800.0, lambda k: 1000, lambda k: 1000, [5700, 5800, 5900])
    for name in gex.UNDERLYINGS:
        (src / f"{name}.json").write_text(json.dumps(good) if name != "GLD" else "{}")
    out = tmp_path / "out"
    assert gex.main(["--file", str(src), "--now", "2026-10-21T08:45", "--out", str(out)]) == 0
    names = [ln.split(" ")[1] for ln in (out / "latest.txt").read_text().splitlines()]
    assert "GLD" not in names and "SPX" in names


def test_etf_chain_adds_the_same_dollar_gamma_on_the_index_scale():
    spot = 5800.0
    strikes = [5600 + 25 * i for i in range(17)]
    spx = chain(spot, lambda k: 3000 if k < spot else 300, lambda k: 3000 if k > spot else 300, strikes)
    _, rows = gex.parse_chain(spx, NOW)
    ratio = 10.0
    etf_rows = [dict(r, strike=r["strike"] / ratio, gamma=r["gamma"] * ratio, oi=r["oi"] * ratio) for r in rows]
    both = rows + gex.rescale(etf_rows, ratio)
    single = gex.gex_by_strike(spot, rows)
    double = gex.gex_by_strike(spot, both)
    for k, g in single.items():
        assert double[k] == pytest.approx(2 * g)                           # same dollars, same strikes
    assert gex.total_gex_at(spot, both) == pytest.approx(2 * gex.total_gex_at(spot, rows))


def test_ladder_is_every_bucket_near_spot():
    spot = 5800.0
    strikes = [5500 + 5 * i for i in range(121)]                          # 5500 .. 6100 every 5
    payload = chain(spot, lambda k: 3000 if k < spot else 300, lambda k: 3000 if k > spot else 300, strikes)
    s, rows = gex.parse_chain(payload, NOW)
    lv = gex.levels(s, rows, bucket=25.0)
    ks = [k for k, _ in lv["ladder"]]
    assert all(k % 25 == 0 for k in ks)
    assert all(abs(k / s - 1) <= gex.LADDER_BAND for k in ks)
    assert len(ks) >= 8                                                    # 5700 .. 5900 but the empty 5800
    lv5 = gex.levels(s, rows, bucket=5.0)
    assert len(lv5["ladder"]) > 3 * gex.TOP_LEVELS                         # SPX-size buckets: dozens of levels
    assert abs(lv["cw"] / s - 1) <= gex.WALL_BAND and abs(lv["pw"] / s - 1) <= gex.WALL_BAND


def test_history_keeps_each_days_first_run_and_the_newest(tmp_path):
    path = tmp_path / "history.txt"
    stamps = ["2026-10-21T08:45", "2026-10-21T12:15", "2026-10-22T08:45", "2026-10-22T09:45", "2026-10-22T10:15"]
    gex.merge_history(str(path), [f"{st} SPX spot=1 flip=1 cw=1 pw=1 total=+1 k=1:+1" for st in stamps])
    kept = [ln.split(" ")[0] for ln in path.read_text().splitlines()]
    assert kept == ["2026-10-21T08:45", "2026-10-22T08:45", "2026-10-22T10:15"]


def test_only_the_newest_run_keeps_its_full_ladder(tmp_path):
    path = tmp_path / "history.txt"
    ladder = "k=" + ",".join(f"{7600 + 5 * i}:{'+' if i % 2 else '-'}{i + 1}" for i in range(30))
    lines = [f"{st} SPX spot=7650 flip=7700 cw=7800 pw=7600 total=-5 {ladder}"
             for st in ("2026-10-22T09:15", "2026-10-23T09:15", "2026-10-23T09:45")]
    gex.merge_history(str(path), lines)
    kept = path.read_text().splitlines()
    n_levels = [len(ln.split("k=")[1].split(" ")[0].split(",")) for ln in kept]
    assert n_levels == [gex.TOP_LEVELS, gex.TOP_LEVELS, 30]
    biggest = sorted(kept[1].split("k=")[1].split(","), key=lambda kv: float(kv.split(":")[0]))
    assert [kv.split(":")[0] for kv in biggest] == [str(7600 + 5 * i) for i in range(22, 30)]   # 8 largest |GEX|


def test_volume_levels_are_the_busiest_strikes_near_spot():
    spot = 5800.0
    strikes = [5600 + 25 * i for i in range(17)]
    payload = chain(spot, lambda k: 1000, lambda k: 1000, strikes)
    for o in payload["data"]["options"]:
        k = int(o["option"][-8:]) / 1000
        o["volume"] = 5000 if k == 5750 and o["option"][-9] == "P" else (3000 if k == 5850 else 10)
    payload["data"]["options"].append({"option": occ("SPXW", datetime(2026, 10, 21), "C", 5825), "open_interest": 0,
                                       "volume": 2500, "iv": 0.2, "gamma": 0.0})   # new 0DTE strike, no OI yet
    s, rows = gex.parse_chain(payload, NOW)
    lv = gex.levels(s, rows, bucket=25.0)
    vol = {k: (c, p) for k, c, p in lv["volume"]}
    assert len(vol) == gex.N_VOLUME and all(abs(k / s - 1) <= gex.LADDER_BAND for k in vol)
    assert vol[5750] == (10, 5000) and vol[5850] == (3000, 3000) and vol[5825] == (2510, 10)   # plus the regular 5825 contracts
    ln = gex.line("2026-10-21T10:15", "SPX", lv)
    assert " v=5700:" in ln and "5750:10/5000" in ln
    assert " v=" not in gex.compact(ln)                                # older runs drop the volume levels


def test_etf_volume_counts_in_index_sized_contracts():
    rows = [{"strike": 580.0, "cp": "P", "t": 0.01, "gamma": 0.1, "oi": 100.0, "iv": 0.2, "vol": 1000.0}]
    assert gex.rescale(rows, 10.0)[0]["vol"] == pytest.approx(100.0)
