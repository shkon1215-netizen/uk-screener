"""Offline validation of the UK screener. No network required.

Run: python test_uk.py

Every planted case is something a naive screener gets wrong. The investment
trusts are the important ones - they are the UK's counterpart of Korea's
preferred lines, and three of them are planted with deliberately different
disguises because no single signal catches all three.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import uk_filters as UF
from config_uk import ScreenConfig
from providers_uk import _to_major, to_yahoo
from screener import run_screen

rng = np.random.default_rng(7)

GBP_USD = 1.27
MCAP = 8.0e8           # ~USD 1.0bn, clears the gate
ADV = 6.0e6            # ~USD 7.6m, clears the gate


def base(**kw):
    r = dict(name="", tidm="", board="MAIN", tier="FTSE 250", icb_sector="",
             sector="Industrials", industry="Specialty Industrial Machinery",
             quote_type="EQUITY", country="United Kingdom",
             market_cap_local=MCAP, adv_local=ADV, close_local=5.00,
             currency="GBp",
             trailing_pe=np.nan, price_to_book=np.nan, ev_to_ebitda=np.nan,
             trailing_eps=50.0, book_value_ps=400.0, div_yield=1.5)
    r.update(kw)
    return r


def make_universe() -> pd.DataFrame:
    rows = []
    # Two cohorts with deliberately different multiple levels by board, so the
    # board-separation mechanism has something to separate. Real UK boards sit
    # much closer together than this - see CLAUDE.md - but the machinery must
    # still work when they do not.
    specs = [
        ("Specialty Industrial Machinery", "MAIN", 14.0, 1.6, 8.0, 18),
        ("Biotechnology", "AIM", 40.0, 5.0, 27.0, 16),
    ]
    n = 0
    for industry, board, pe, pb, ev, count in specs:
        for _ in range(count):
            n += 1
            rows.append(base(
                tidm=f"PR{n:02d}", name=f"Peer {n} plc",
                board=board, industry=industry, tier="FTSE AIM 100" if board == "AIM" else "FTSE 250",
                sector="Healthcare" if board == "AIM" else "Industrials",
                trailing_pe=pe * rng.uniform(0.93, 1.09),
                price_to_book=pb * rng.uniform(0.93, 1.09),
                ev_to_ebitda=ev * rng.uniform(0.93, 1.09),
                market_cap_local=MCAP * rng.uniform(0.9, 2.5),
                adv_local=ADV * rng.uniform(0.9, 2.5)))

    # --- planted cases ------------------------------------------------
    # PASS: genuinely cheap vs Main Market machinery, good ROE, pays a dividend
    rows.append(base(tidm="GOOD", name="Genuine Value plc",
                     trailing_pe=8.5, price_to_book=0.75, ev_to_ebitda=5.0,
                     trailing_eps=66.0, book_value_ps=500.0, div_yield=4.2))

    # FAIL: investment trust, caught by NAME. The easy case.
    rows.append(base(tidm="TRST", name="Aberforth Smaller Companies Investment Trust plc",
                     icb_sector="Investment Trusts", industry="Asset Management",
                     sector="Financial Services",
                     trailing_pe=5.0, price_to_book=0.88, ev_to_ebitda=4.0,
                     trailing_eps=80.0, book_value_ps=455.0, div_yield=3.0))

    # FAIL: investment trust with NO "trust" in the name, caught only by the
    # ICB sector. This is the Scottish Mortgage / HarbourVest shape.
    rows.append(base(tidm="SCMO", name="Caledonian Global Growth",
                     icb_sector="Collective Investments", industry="Asset Management",
                     sector="Financial Services",
                     trailing_pe=6.0, price_to_book=0.82, ev_to_ebitda=4.5,
                     trailing_eps=68.0, book_value_ps=415.0, div_yield=2.5))

    # FAIL: investment trust that NEITHER the name NOR the sector catches -
    # filed under plain "Financial services", exactly like Pershing Square.
    # Only the AIC register removes this one, which is why the register is the
    # primary signal and the heuristics are the fallback.
    rows.append(base(tidm="PSHX", name="Marlborough Square Holdings",
                     icb_sector="Financial services", industry="Asset Management",
                     sector="Financial Services",
                     trailing_pe=5.5, price_to_book=0.70, ev_to_ebitda=4.2,
                     trailing_eps=76.0, book_value_ps=400.0, div_yield=2.2))

    # SURVIVE: an OPERATING asset manager. Same sector, same yfinance industry
    # and a similar-looking multiple set as the three trusts above. If any
    # filter is written loosely enough to catch this, it has broken the screen
    # rather than protected it.
    rows.append(base(tidm="SDRX", name="Schroders Asset Management plc",
                     icb_sector="Financial Services", industry="Asset Management",
                     sector="Financial Services",
                     trailing_pe=11.0, price_to_book=1.9, ev_to_ebitda=7.5,
                     trailing_eps=100.0, book_value_ps=580.0, div_yield=4.0))

    # FAIL: REIT - valued on NAV and rent, not earnings
    rows.append(base(tidm="REIT", name="Supermarket Income REIT plc",
                     icb_sector="Real Estate Investment Trusts",
                     sector="Real Estate", industry="REIT - Retail",
                     trailing_pe=9.0, price_to_book=0.65, ev_to_ebitda=6.0))

    # FAIL: non-voting line of Schroders above. Trades ~25% below the ordinary
    # because it has no vote - the UK's 우선주.
    rows.append(base(tidm="SDRXC", name="Schroders Asset Management plc Non-Vtg",
                     icb_sector="Financial Services", industry="Asset Management",
                     sector="Financial Services",
                     trailing_pe=8.2, price_to_book=1.4, ev_to_ebitda=5.6))

    # FLAGGED not dropped: holding company
    rows.append(base(tidm="HLDG", name="Caledonia Holdings plc",
                     trailing_pe=7.0, price_to_book=0.55, ev_to_ebitda=4.0,
                     trailing_eps=40.0, book_value_ps=500.0, div_yield=2.0))

    # FAIL: loss-maker. A P/E of 0 must read as missing, never as cheap.
    rows.append(base(tidm="LOSS", name="Lossmaker plc",
                     trailing_pe=0.0, price_to_book=0.60, ev_to_ebitda=0.0,
                     trailing_eps=-20.0))

    # FAIL: cheap AIM biotech against MAIN machinery, but normal for its board.
    rows.append(base(tidm="BIOX", name="Fair Value Bio plc", board="AIM",
                     tier="FTSE AIM 100", sector="Healthcare", industry="Biotechnology",
                     trailing_pe=38.0, price_to_book=4.8, ev_to_ebitda=26.0))
    # PASS: genuinely cheap AIM biotech against its own board cohort
    rows.append(base(tidm="BIOC", name="Cheap Bio plc", board="AIM",
                     tier="FTSE AIM 100", sector="Healthcare", industry="Biotechnology",
                     trailing_pe=28.0, price_to_book=3.4, ev_to_ebitda=20.0))

    # FAIL: too small
    rows.append(base(tidm="TINY", name="Small Co plc", market_cap_local=2.0e8,
                     trailing_pe=8.5, price_to_book=0.75, ev_to_ebitda=5.0))
    # FAIL: illiquid
    rows.append(base(tidm="ILLQ", name="Illiquid plc", adv_local=1.0e5,
                     trailing_pe=8.5, price_to_book=0.75, ev_to_ebitda=5.0))
    return pd.DataFrame(rows)


def check_units() -> list[str]:
    """The GBp/GBP trap and the TIDM mapping, tested directly."""
    bad = []
    print("=== units and ticker mapping ===")
    # Yahoo quotes UK lines in pence; anything derived from price must be
    # divided by 100 before it is compared with a market cap in pounds.
    if _to_major(3344.5, "GBp") != 33.445:
        bad.append("GBp_not_converted")
    if _to_major(33.44, "GBP") != 33.44:
        bad.append("GBP_wrongly_converted")
    if _to_major(None, "GBp") is not None:
        bad.append("None_not_preserved")
    print(f"  {'OK  ' if not bad else 'FAIL'} pence -> pounds "
          f"(3344.5 GBp -> {_to_major(3344.5, 'GBp')})")

    cases = {"III": "III.L", "RR.": "RR.L", "BT.A": "BT-A.L",
             "SHEL": "SHEL.L", "3IN": "3IN.L", "BA.": "BA.L"}
    for tidm, want in cases.items():
        got = to_yahoo(tidm)
        if got != want:
            bad.append(f"tidm_{tidm}")
            print(f"  FAIL {tidm} -> {got}, expected {want}")
    print(f"  {'OK  ' if not any(b.startswith('tidm_') for b in bad) else 'FAIL'} "
          f"TIDM -> Yahoo symbol ({len(cases)} cases)")
    return bad


def main() -> int:
    failures = check_units()

    cfg = ScreenConfig()
    df = make_universe()

    # A stand-in for the AIC register. The real one is keyed on EPIC exactly
    # like this, so the only thing this fakes is the network call.
    aic = {
        "TRST": {"aic_name": "Aberforth", "discount_to_nav": -11.0},
        "SCMO": {"aic_name": "Caledonian Global Growth", "discount_to_nav": -8.2},
        "PSHX": {"aic_name": "Marlborough Square", "discount_to_nav": -28.4},
    }
    df, aicstats = UF.apply_investment_company_filter(df, aic)
    df, ustats = UF.apply_uk_filters(df, cfg)
    res, stats = run_screen(df, GBP_USD, cfg)
    res = UF.add_quality_context(res)
    res = UF.add_valueup_flags(res)
    res, roestats = UF.apply_roe_gate(res, cfg)
    res, absstats = UF.apply_absolute_screen(res, cfg)

    print("\n=== funnel ===")
    for k, v in {**aicstats, **ustats, **stats, **roestats}.items():
        print(f"  {k:<32} {v}")

    idx = res.set_index("tidm")
    expected = {
        "GOOD": True,  "BIOC": True,
        "TRST": False, "SCMO": False, "PSHX": False, "REIT": False,
        "SDRXC": False, "LOSS": False, "BIOX": False, "TINY": False,
        "ILLQ": False,
    }

    print("\n=== planted cases ===")
    for t, want in expected.items():
        present = t in idx.index
        got = bool(idx.loc[t, "passes"]) if present else False
        ok = got == want
        why = "" if present else "  (removed before scoring)"
        print(f"  {'OK  ' if ok else 'FAIL'} {t:<6} expected={want!s:<5} got={got!s:<5}{why}")
        if not ok:
            failures.append(t)

    # The three trusts must be GONE, not merely failing. A trust that survives
    # to be scored is one bad threshold away from topping the table.
    for t in ("TRST", "SCMO", "PSHX"):
        if t in idx.index:
            failures.append(f"{t}_survived_to_scoring")
            print(f"  FAIL {t:<6} still present after hygiene - must be removed")

    # ...and the operating asset manager must NOT have been swept up with them.
    if "SDRX" not in idx.index:
        failures.append("operating_asset_manager_dropped")
        print("  FAIL SDRX   operating asset manager was removed as a trust")
    else:
        print("  OK   SDRX   operating asset manager retained")

    # Holdco must survive as flagged, not silently dropped.
    if "HLDG" not in idx.index:
        failures.append("holdco_was_dropped")
    elif not bool(idx.loc["HLDG", "is_holdco"]):
        failures.append("holdco_not_flagged")
    else:
        print(f"  OK   HLDG   holdco retained and flagged "
              f"(passes={bool(idx.loc['HLDG', 'passes'])})")

    # A P/E of 0 must have been nulled, not read as the cheapest stock present.
    if "LOSS" in idx.index and pd.notna(idx.loc["LOSS", "trailing_pe"]):
        failures.append("zero_pe_not_nulled")
        print("  FAIL LOSS   P/E of 0 survived as a number")
    else:
        print("  OK   LOSS   P/E of 0 read as missing")

    # Board separation: peer medians must differ sharply by board.
    main_m = res[(res["board"] == "MAIN") & res["trailing_pe_peer_median"].notna()]
    aim_m = res[(res["board"] == "AIM") & res["trailing_pe_peer_median"].notna()]
    if not main_m.empty and not aim_m.empty:
        mm = main_m["trailing_pe_peer_median"].median()
        am = aim_m["trailing_pe_peer_median"].median()
        print(f"\n  MAIN machinery peer P/E: {mm:.1f}   AIM biotech peer P/E: {am:.1f}")
        if not (am > mm + 10):
            failures.append("board_separation")
    else:
        failures.append("board_cohorts_missing")

    print("\n=== passing ===")
    hits = res[res["passes"]][["name", "board", "industry", "trailing_pe",
                               "price_to_book", "roe_pct", "div_yield",
                               "avg_discount"]]
    print(hits.round(2).to_string(index=False) if not hits.empty else "  (none)")

    print("\n" + ("ALL CHECKS PASSED" if not failures else f"FAILURES: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
