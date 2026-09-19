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


# ---------------------------------------------------------------------------
# Filed statements: the three-year history and the own-history screen
# ---------------------------------------------------------------------------
YEARS = pd.to_datetime(["2021-12-31", "2022-12-31", "2023-12-31",
                        "2024-12-31", "2025-12-31"])


def stmt(rows: dict) -> pd.DataFrame:
    """A Yahoo-shaped statement: rows are line items, columns are fiscal
    year-ends NEWEST first, and the oldest column is entirely empty - exactly
    the padding Yahoo returns for every UK name checked."""
    df = pd.DataFrame({k: [np.nan] + list(v) for k, v in rows.items()},
                      index=YEARS).T
    return df[df.columns[::-1]]


def company(ni=(100e6, 110e6, 120e6, 130e6), shares=(1e9,) * 4,
            eq=(1000e6,) * 4, ebitda=(200e6, 210e6, 220e6, 230e6)):
    inc = stmt({"Total Revenue": (1000e6, 1100e6, 1200e6, 1300e6),
                "Operating Income": (150e6, 160e6, 170e6, 180e6),
                "EBITDA": ebitda, "Net Income Common Stockholders": ni})
    bs = stmt({"Common Stock Equity": eq, "Ordinary Shares Number": shares,
               "Total Debt": (300e6,) * 4, "Cash And Cash Equivalents": (100e6,) * 4})
    return inc, bs


def prices(pence: float) -> pd.Series:
    idx = pd.date_range("2020-01-01", "2026-09-18", freq="B")
    return pd.Series(pence, index=idx)


def check_statements() -> list[str]:
    from providers_uk import build_statement_record, cagr
    bad = []
    print("\n=== filed statements ===")

    def ok(cond, label):
        print(f"  {'OK  ' if cond else 'FAIL'} {label}")
        if not cond:
            bad.append(label)

    # A GBP reporter at 200p a share, 1bn shares: GBP 2bn market value every
    # year-end, against GBP 1bn of equity - so P/B 2.0 in every year.
    inc, bs = company()
    rec = build_statement_record(inc, bs, prices(200.0), None, "GBp", "GBP",
                                 close_now=2.0, mcap_now=2e9)
    ok(rec["hist_years"] == "2022,2023,2024,2025",
       "Yahoo's empty padded year is not read as a filed year")
    ok(rec["hist_pbr"] == [2.0] * 4, f"P/B history built in pounds, not pence ({rec['hist_pbr']})")
    ok(rec["fin_years"] == "2023,2024,2025" and rec["rev_y3"] == 1300.0,
       "3-year history is the last three filed years, in millions")
    ok(abs(rec["rev_cagr"] - ((1300 / 1100) ** 0.5 - 1)) < 1e-9, "revenue CAGR over the span covered")
    ok(np.isnan(cagr([-50, 10, 20])), "CAGR is undefined on a negative base, not sign-flipped")

    # A DOLLAR reporter quoted in pence - the Shell/Rio shape. GBP 2bn of
    # market value at 1.25 USD/GBP is USD 2.5bn against USD 1bn of equity:
    # P/B 2.5. Forgetting the conversion reads 2.0, a fake 20% discount.
    fx = pd.Series(1.25, index=prices(1).index)
    rec = build_statement_record(inc, bs, prices(200.0), fx, "GBp", "USD",
                                 close_now=2.0, mcap_now=2e9)
    ok(rec["hist_pbr"] == [2.5] * 4, f"USD reporter converted at each year-end rate ({rec['hist_pbr']})")
    rec = build_statement_record(inc, bs, prices(200.0), None, "GBp", "USD",
                                 close_now=2.0, mcap_now=2e9)
    ok(all(v is None for v in rec["hist_pbr"]),
       "USD reporter with no FX history gets NO benchmark, never an unconverted one")
    # Yahoo left out the reporting currency. Assuming sterling would scale a
    # dollar reporter's whole history by the exchange rate.
    rec = build_statement_record(inc, bs, prices(200.0), None, "GBp", "",
                                 close_now=2.0, mcap_now=2e9)
    ok(rec["hist_note"] == "no reporting currency" and rec["hist_pbr"] == [],
       "missing reporting currency -> no benchmark, never assumed to be sterling")
    ok(rec["rev_y3"] == 1300.0, "...while the 3-year history still ships")

    # A 1-for-10 consolidation between 2023 and 2024: share count drops 10x.
    inc2, bs2 = company(shares=(1e9, 1e9, 1e8, 1e8))
    rec = build_statement_record(inc2, bs2, prices(200.0), None, "GBp", "GBP",
                                 close_now=2.0, mcap_now=2e8)
    ok(rec["hist_note"] == "share-count break" and rec["hist_pbr"] == [],
       "share consolidation -> no history, not a 90% 'discount'")

    # Price series and filed shares on different bases: today's price x latest
    # shares is 10x today's market cap.
    rec = build_statement_record(inc, bs, prices(200.0), None, "GBp", "GBP",
                                 close_now=2.0, mcap_now=2e8)
    ok(rec["hist_note"] == "price/share basis mismatch" and rec["hist_per"] == [],
       "price/share basis mismatch -> no history")

    # A one-off gain in the latest year - the Reckitt shape. Reported net
    # income doubles; Yahoo's normalized line does not. The valuation must use
    # normalized, or the one-off reads as the stock halving its P/E.
    inc4, bs4 = company(ni=(100e6, 100e6, 100e6, 200e6))
    inc4.loc["Normalized Income"] = inc4.loc["Net Income Common Stockholders"]
    inc4.loc["Normalized Income", YEARS[-1]] = 100e6
    inc4.loc["Normalized EBITDA"] = inc4.loc["EBITDA"]
    rec = build_statement_record(inc4, bs4, prices(200.0), None, "GBp", "GBP",
                                 close_now=2.0, mcap_now=2e9)
    ok(rec["hist_earnings"] == "normalized" and rec["lf_ni"] == 100e6
       and rec["hist_per"][-1] == 20.0,
       "one-off gain: valuation uses normalized earnings, today and in history")
    ok(rec["np_y3"] == 200.0, "...while the 3-year history still reports what happened")

    # Normalized present for only some years: do not mix definitions.
    inc5, bs5 = company()
    inc5.loc["Normalized Income"] = np.nan
    inc5.loc["Normalized Income", YEARS[-1]] = 130e6
    rec = build_statement_record(inc5, bs5, prices(200.0), None, "GBp", "GBP",
                                 close_now=2.0, mcap_now=2e9)
    ok(rec["hist_earnings"] == "reported",
       "normalized for only part of the window -> reported throughout, never mixed")

    # Stale latest filing: FY2025 is the newest Yahoo has, and it is 2027.
    rec = build_statement_record(inc, bs, prices(200.0), None, "GBp", "GBP",
                                 close_now=2.0, mcap_now=2e9, asof="2027-12-31")
    ok(rec["hist_note"] == "stale filings" and rec["hist_per"] == [],
       "latest filing 2 years old -> no benchmark")
    rec = build_statement_record(inc, bs, prices(200.0), None, "GBp", "GBP",
                                 close_now=2.0, mcap_now=2e9, asof="2026-09-18")
    ok(rec["hist_note"] == "", "latest filing 9 months old -> benchmark kept")

    # A loss year: P/E for that year is missing, not negative or cheap.
    inc3, bs3 = company(ni=(100e6, -50e6, 120e6, 130e6))
    rec = build_statement_record(inc3, bs3, prices(200.0), None, "GBp", "GBP",
                                 close_now=2.0, mcap_now=2e9)
    ok(rec["hist_per"][1] is None, "loss year's P/E is missing, not negative")
    return bad


def check_history_screen() -> list[str]:
    bad = []
    print("\n=== own-history screen ===")
    cfg = ScreenConfig()

    def row(tidm, pers, pbrs, evxs, now_mcap, lf_ni=100e6, lf_eq=1000e6,
            lf_eb=200e6, sector="Industrials", roe_ok=True):
        return dict(tidm=tidm, ticker=tidm + ".L", sector=sector,
                    hist_per=pers, hist_pbr=pbrs, hist_evx=evxs,
                    market_cap_local=now_mcap, fx_now=1.0, lf_ni=lf_ni,
                    lf_equity=lf_eq, lf_ebitda=lf_eb, lf_debt=300e6,
                    lf_cash=100e6, lf_mi=0.0, passes=False, abs_passes=False,
                    roe_ok=roe_ok, avg_discount=0.0, hist_note="")

    df = pd.DataFrame([
        # De-rated premium name: always ~20x, 2x book, 10x EBITDA; now 10x,
        # 1.0x, ~6x. Cheap against itself on all three, and nothing else flags it.
        row("DRTD", [20, 21, 19, 20], [2.0, 2.1, 1.9, 2.0], [10, 11, 9, 10], 1.0e9),
        # Same history, same price, but ROE below the floor.
        row("LOWR", [20, 21, 19, 20], [2.0, 2.1, 1.9, 2.0], [10, 11, 9, 10], 1.0e9,
            roe_ok=False),
        # Only two usable years: no benchmark, however cheap it looks.
        row("THIN", [20, None, None, 21], [2.0, None, None, 2.1],
            [10, None, None, 11], 1.0e9),
        # A freak year (150x P/E, inside the bounds so only the median can stop
        # it) must not drag the benchmark: the mean would be 46, the median 12.
        row("FREK", [150, 12, 11, 12], [1.2, 1.1, 1.2, 1.1], [7, 6, 7, 6], 1.2e9),
        # A bank: EV/EBITDA must be skipped even though the numbers exist.
        row("BANK", [20, 21, 19, 20], [2.0, 2.1, 1.9, 2.0], [10, 11, 9, 10], 1.0e9,
            sector="Financial Services"),
    ])
    res, stats = UF.apply_history_screen(df, cfg)
    idx = res.set_index("tidm")

    def ok(cond, label):
        print(f"  {'OK  ' if cond else 'FAIL'} {label}")
        if not cond:
            bad.append(label)

    d = idx.loc["DRTD"]
    ok(bool(d["hist_passes"]) and d["screen"] == "history",
       f"de-rated premium name passes on history alone (n_pass={d['hist_n_pass']})")
    ok(abs(d["per_now"] - 10.0) < 1e-9 and abs(d["pbr_now"] - 1.0) < 1e-9,
       "today is measured on the history's basis: market value / latest filing")
    ok(not bool(idx.loc["LOWR", "hist_passes"]), "ROE floor applies to the history screen")
    ok(idx.loc["THIN", "hist_n_valid"] == 0, "fewer than 3 usable years -> no benchmark")
    ok(abs(idx.loc["FREK", "hist_per_med"] - 12.0) < 1e-9,
       f"freak year does not drag the benchmark (median {idx.loc['FREK', 'hist_per_med']})")
    b = idx.loc["BANK"]
    ok(pd.isna(b["hist_evx_med"]) and pd.isna(b["evx_now"]),
       "financials: EV/EBITDA skipped in history and today")
    return bad


def main() -> int:
    failures = check_units()
    failures += check_statements()
    failures += check_history_screen()

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
