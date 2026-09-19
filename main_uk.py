"""UK (LSE) relative-valuation screener.

  python main_uk.py                          # full Main Market + AIM run
  python main_uk.py --board MAIN             # Main Market only
  python main_uk.py --include-trusts         # see why this is off by default
  python main_uk.py --discount 0.15 --min-metrics 1
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime

import pandas as pd

import uk_filters as UF
from config_uk import ScreenConfig
from providers_uk import LSEProvider, fetch_investment_companies
from screener import run_screen


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="uk_screen_results.csv")
    p.add_argument("--board", choices=["MAIN", "AIM", "BOTH"], default="BOTH")
    p.add_argument("--min-mcap", type=float, default=600e6, help="USD")
    p.add_argument("--min-adv", type=float, default=4e6, help="USD")
    p.add_argument("--discount", type=float, default=0.20)
    p.add_argument("--min-metrics", type=int, default=2)
    p.add_argument("--min-peers", type=int, default=5)
    p.add_argument("--peer-keys", default="industry,board")
    p.add_argument("--fx", type=float, help="USD per GBP (default: live)")
    p.add_argument("--include-trusts", action="store_true",
                   help="keep investment trusts and closed-end funds; they will "
                        "fill the top of the list, which is the point of the default")
    p.add_argument("--include-reits", action="store_true")
    p.add_argument("--exclude-holdcos", action="store_true")
    p.add_argument("--adv-days", type=int, default=60)
    p.add_argument("--skip-liquidity", action="store_true")
    p.add_argument("--min-roe", type=float, default=5.0,
                   help="ROE%% floor applied to survivors; 0 disables")
    p.add_argument("--abs-pbr", type=float, default=1.0,
                   help="absolute screen: P/B below this")
    p.add_argument("--abs-ev", type=float, default=8.0,
                   help="absolute screen: EV/EBITDA below this")
    p.add_argument("--abs-strict-financials", action="store_true",
                   help="require EV/EBITDA of financials too (they have none, so "
                        "none will pass)")
    p.add_argument("--no-abs-roe", action="store_true",
                   help="do not apply the ROE floor to the absolute screen")
    p.add_argument("--no-abs-fair-pbr", action="store_true",
                   help="drop the P/B < ROE/CoE test")
    p.add_argument("--coe", type=float, default=10.0,
                   help="cost of equity %% for the fair-P/B test")
    p.add_argument("--abs-min-div", type=float, default=2.0,
                   help="absolute screen: dividend yield %% floor; 0 disables")
    p.add_argument("--dashboard", default="uk_dashboard.html",
                   help="self-contained HTML dashboard; pass '' to skip")
    p.add_argument("--all", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    a = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    log = logging.getLogger("uk")

    cfg = ScreenConfig(
        min_market_cap_usd=a.min_mcap,
        min_adv_usd=0.0 if a.skip_liquidity else a.min_adv,
        discount_threshold=a.discount,
        min_metrics_passing=a.min_metrics,
        min_peers=a.min_peers,
        min_roe_pct=a.min_roe,
        abs_max_pbr=a.abs_pbr,
        abs_max_ev_ebitda=a.abs_ev,
        abs_require_roe=not a.no_abs_roe,
        abs_financials_pbr_only=not a.abs_strict_financials,
        abs_require_pbr_vs_roe=not a.no_abs_fair_pbr,
        abs_cost_of_equity_pct=a.coe,
        abs_min_div_yield=a.abs_min_div,
        peer_keys=tuple(k.strip() for k in a.peer_keys.split(",") if k.strip()),
        exclude_investment_trusts=not a.include_trusts,
        exclude_reits=not a.include_reits,
        exclude_holdcos=a.exclude_holdcos,
        adv_lookback_days=a.adv_days,
        boards=("MAIN", "AIM") if a.board == "BOTH" else (a.board,),
    )

    prov = LSEProvider(cfg)
    days = prov.recent_business_days(cfg.adv_lookback_days)
    asof = days[-1] if days else datetime.now().strftime("%Y-%m-%d")
    log.info("as of %s", asof)

    # 1. roster: index constituent tables, ~4 HTTP requests for the market
    log.info("building roster...")
    roster = prov.listing_roster(asof)
    if roster.empty:
        log.error("empty roster - the index tables did not parse")
        return 1
    roster = roster[roster["board"].isin(cfg.boards)]
    log.info("roster: %d listings (%s)", len(roster),
             ", ".join(f"{b}={int((roster['board'] == b).sum())}" for b in cfg.boards))

    # 2. fundamentals: one yfinance call per ticker.
    #
    #    INVARIANT 7 READS DIFFERENTLY HERE. Korea gated on size and liquidity
    #    using cheap cross-sectional pykrx calls before any per-ticker work,
    #    because its roster was 2,400 names and the enrichment was the whole
    #    runtime. On the LSE there is no free cross-section to gate with, but
    #    the roster is index-bounded at ~530 names and one .info call returns
    #    market cap AND every multiple together - so splitting into two passes
    #    would double the requests to save nothing. The ordering principle is
    #    unchanged (never pay per-ticker for names a cheap filter can remove);
    #    it is the roster itself, not a pre-gate, that does the bounding.
    log.info("fetching fundamentals for %d tickers...", len(roster))
    snap = prov.snapshot(roster["ticker"].tolist(), asof)
    priced = int(snap["market_cap_local"].notna().sum())
    if priced < 0.5 * len(roster):
        # Refusing here rather than screening on a third of the market. A
        # throttled fetch does not error - it returns rows with the fields
        # missing, which look exactly like companies that failed the size
        # gate, and the funnel would report a plausible-looking lie.
        log.error("only %d of %d tickers priced. Yahoo is rate-limiting; the "
                  "cache has kept what arrived, so re-running in a few minutes "
                  "will fill the rest. Refusing to screen a partial universe.",
                  priced, len(roster))
        return 2
    df = roster.merge(snap, on="ticker", how="left")

    # 3. UK share-class hygiene. Runs after the fetch, not before, because the
    #    trust test uses yfinance's industry string as well as the ICB sector.
    #
    #    The AIC register goes first and does the heavy lifting; the sector and
    #    name heuristics in apply_uk_filters are the fallback for when it is
    #    unreachable. See uk_filters.apply_investment_company_filter.
    ustats: dict = {}
    if cfg.exclude_investment_trusts:
        aic = fetch_investment_companies()
        if not aic:
            log.warning("AIC register unavailable - falling back to sector and "
                        "name heuristics, which miss closed-end funds filed "
                        "under plain 'Financial services'")
        df, aicstats = UF.apply_investment_company_filter(df, aic)
        ustats.update(aicstats)
    df, ufstats = UF.apply_uk_filters(df, cfg)
    ustats.update(ufstats)
    log.info("after UK share-class hygiene: %d", len(df))

    gbp_usd = a.fx if a.fx else prov.gbp_to_usd()
    log.info("FX: 1 GBP = %.4f USD", gbp_usd)

    if a.skip_liquidity:
        df["adv_local"] = float("nan")

    df["market_cap_usd"] = pd.to_numeric(df["market_cap_local"], errors="coerce") * gbp_usd
    df["adv_usd"] = pd.to_numeric(df.get("adv_local"), errors="coerce") * gbp_usd
    pre = df[df["market_cap_usd"] >= cfg.min_market_cap_usd]
    if not a.skip_liquidity:
        pre = pre[pre["adv_usd"] >= cfg.min_adv_usd]
    log.info("%d of %d listings cleared size/liquidity", len(pre), len(df))
    ustats["cleared_size_liquidity"] = len(pre)
    if pre.empty:
        print("Nothing cleared the size and liquidity gates.")
        return 0

    # 4. screen
    res, stats = run_screen(pre, gbp_usd, cfg)
    if res.empty:
        print("Nothing survived screening.")
        return 0
    res = UF.add_quality_context(res)
    res = UF.add_valueup_flags(res)
    if cfg.min_roe_pct > 0:
        res, roestats = UF.apply_roe_gate(res, cfg)
        stats = {**stats, **roestats}
    else:
        res["roe_ok"], res["roe_tier"] = True, ""

    res, absstats = UF.apply_absolute_screen(res, cfg)
    stats = {**stats, **absstats}

    funnel = {**ustats, **stats}
    print("\n--- funnel ---")
    for k, v in funnel.items():
        print(f"  {k:<26} {v}")

    out = res if a.all else res[res["passes"]]
    cols = [c for c in UF.uk_output_columns(cfg) if c in res.columns]
    out[cols].to_csv(a.out, index=False, encoding="utf-8-sig")

    hits = res[res["passes"]]
    print(f"\n--- {len(hits)} stock(s) >={cfg.discount_threshold:.0%} below "
          f"industry peers on >={cfg.min_metrics_passing} metrics ---")
    if not hits.empty:
        show = hits[["tidm", "name", "board", "industry", "market_cap_usd",
                     "trailing_pe", "price_to_book", "roe_pct", "div_yield",
                     "avg_discount", "metrics_passing"]].head(30).copy()
        show["mcap_$m"] = (show.pop("market_cap_usd") / 1e6).round(0).astype("Int64")
        show["avg_discount"] = show["avg_discount"].map(lambda x: f"{x:.1%}")
        print(show.to_string(index=False))

    meta = {
        "asof": asof, "source": "lse-index+yfinance", "board": a.board,
        "cmd": "python main_uk.py " + " ".join(sys.argv[1:]),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "usd_per_gbp": round(gbp_usd, 4),
        "funnel": funnel,
        "thresholds": {
            "min_mcap_usd": cfg.min_market_cap_usd,
            "min_adv_usd": 0 if a.skip_liquidity else cfg.min_adv_usd,
            "skip_liquidity": bool(a.skip_liquidity),
            "discount": cfg.discount_threshold,
            "min_metrics": cfg.min_metrics_passing,
            "min_peers": cfg.min_peers,
            "min_valid_metrics": cfg.min_valid_metrics,
            "min_roe_pct": cfg.min_roe_pct,
            "roe_good_pct": cfg.roe_good_pct,
            "abs_max_pbr": cfg.abs_max_pbr,
            "abs_max_ev_ebitda": cfg.abs_max_ev_ebitda,
            "abs_require_roe": cfg.abs_require_roe,
            "abs_financials_pbr_only": cfg.abs_financials_pbr_only,
            "abs_require_pbr_vs_roe": cfg.abs_require_pbr_vs_roe,
            "abs_cost_of_equity_pct": cfg.abs_cost_of_equity_pct,
            "abs_min_div_yield": cfg.abs_min_div_yield,
        },
    }
    meta_path = os.path.splitext(a.out)[0] + "_meta.json"
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    print(f"\nwrote {a.out} and {meta_path}")

    if a.dashboard:
        try:
            from dashboard import build_dashboard, sibling_boards
            build_dashboard(a.out, meta_path, a.dashboard,
                            boards=sibling_boards(a.board, a.dashboard))
            print(f"wrote {a.dashboard}   <- open this")
        except Exception as e:
            log.error("dashboard build failed: %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
