"""UK-specific filters layered on top of the generic screener.

Everything downstream of tag_share_classes is a near-copy of korea_filters,
because the quality floor and the absolute screen are not country-specific.
The part that had to be rewritten is the top: which listed vehicles are
structurally cheap on the London market.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

import config_uk as K

log = logging.getLogger(__name__)


def tag_share_classes(df: pd.DataFrame) -> pd.DataFrame:
    """Tag every vehicle whose cheapness is structural rather than a mispricing.

    The ICB sector that ships with the FTSE constituent tables does most of
    the work - it names "Investment Trust" and "REIT" outright, which beats
    any name heuristic. The name tokens are the fallback for AIM lines, where
    the source publishes no sector at all.
    """
    df = df.copy()
    name = df["name"].fillna("").astype(str)
    sect = df.get("icb_sector", pd.Series("", index=df.index)).fillna("").astype(str)
    # yfinance's own industry string catches trusts the index table missed:
    # a closed-end fund is invariably classified "Asset Management".
    yind = df.get("industry", pd.Series("", index=df.index)).fillna("").astype(str)
    qtype = df.get("quote_type", pd.Series("", index=df.index)).fillna("").astype(str)

    df["is_trust"] = [K.is_investment_trust(n, s) for n, s in zip(name, sect)]
    # A name ending in "Trust" that Yahoo also calls Asset Management is a
    # closed-end fund even when the sector cell was blank.
    extra = name.str.upper().str.contains("TRUST", na=False) & \
        yind.str.contains("Asset Management", case=False, na=False)
    df["is_trust"] = df["is_trust"] | extra

    df["is_reit"] = [K.is_reit(n, s) for n, s in zip(name, sect)]
    df["is_reit"] = df["is_reit"] | yind.str.contains("REIT", case=False, na=False)

    df["is_shell"] = name.map(K.is_shell)
    df["is_holdco"] = name.map(K.is_holdco)
    df["is_foreign_line"] = name.map(K.is_foreign_line)
    # quoteType is the last guard: an ETF or fund that slipped into a
    # constituent table is never an operating company.
    df["is_fund_quote"] = qtype.str.upper().isin(["ETF", "MUTUALFUND", "FUND"])

    # Restricted-voting lines. Keyed on the TIDM, not the Yahoo symbol: the
    # TIDM is what actually encodes the share class (Schroders is SDR, its
    # non-voting line SDRC).
    #
    # Unlike KRX's 6-digit codes there is no digit convention to rely on, so
    # stripping a trailing C or A is only weak evidence. It counts as
    # non-voting solely when the ordinary line it would belong to is itself
    # present in the roster - otherwise a company whose ticker merely ends in
    # A or C gets deleted for no reason.
    tidm = df["tidm"].astype(str).str.strip().str.upper() \
        if "tidm" in df.columns \
        else df["ticker"].astype(str).str.replace(r"\.L$", "", regex=True).str.upper()
    df["base_line"] = tidm.map(K.base_line_of)
    present = set(tidm)
    by_name = name.map(K.is_nonvoting_name)
    by_ticker = (df["base_line"] != tidm) & df["base_line"].isin(present)
    df["is_nonvoting"] = by_name | by_ticker
    return df


def apply_investment_company_filter(df: pd.DataFrame,
                                    aic: dict) -> tuple[pd.DataFrame, dict]:
    """Drop everything on the AIC register of investment companies.

    Korea's analogue, apply_admin_issue_filter, could only match on company
    name because KIND published no code. This one matches on EPIC, so a
    renamed company cannot slip through - and unlike the sector heuristics it
    is definitive rather than suggestive.

    Runs before the sector and name fallbacks, and annotates survivors with
    the register's discount-to-NAV so the funnel can show what was removed and
    why it had to be. Returns unchanged if the register could not be fetched;
    the caller then leans on the heuristics alone and says so.
    """
    if not aic:
        return df, {"dropped_aic_investment_company": 0, "aic_register": 0}
    df = df.copy()
    epic = df["tidm"].astype(str).str.strip().str.upper()
    hit = epic.isin(aic.keys())
    n = int(hit.sum())
    if n:
        discs = [aic[e].get("discount_to_nav") for e in epic[hit]
                 if isinstance(aic[e].get("discount_to_nav"), (int, float))]
        med = float(np.median(discs)) if discs else float("nan")
        log.info("AIC register removed %d investment companies "
                 "(median discount to NAV %.1f%%)", n, med)
        log.debug("  e.g. %s", ", ".join(df.loc[hit, "name"].astype(str).head(8)))
    return df[~hit].copy(), {"dropped_aic_investment_company": n,
                             "aic_register": len(aic)}


def apply_uk_filters(df: pd.DataFrame, cfg: K.ScreenConfig) -> tuple[pd.DataFrame, dict]:
    """Remove vehicles whose discount is structural, not a mispricing.

    Investment trusts are to London what preferred shares are to Seoul, and
    for the same reason: a large class that trades persistently below its own
    stated book with nothing forcing convergence. A closed-end fund's "P/B" is
    price-to-NAV, and the long-run average of that number is a discount, not
    par - UK trusts have sat at 5-15% below NAV for most of the last decade.
    Screen them against operating-company peers on P/B and they do not merely
    appear once, they fill the entire top of the list on every run: 95 of the
    434 Main Market constituents here are trusts, and the FTSE SmallCap index
    is more than half of them.

    REITs are excluded for the reason Korea excluded them - they are valued on
    NAV and rental yield, not on earnings. Cash shells have no operations to
    value at all.
    """
    df = tag_share_classes(df)
    stats = {"listings": len(df)}

    def drop(flag: str, key: str, on: bool):
        nonlocal df
        if not on:
            return
        n = int(df[flag].sum())
        df = df[~df[flag]]
        stats[key] = n

    drop("is_trust", "dropped_investment_trust", cfg.exclude_investment_trusts)
    drop("is_reit", "dropped_reit", cfg.exclude_reits)
    drop("is_nonvoting", "dropped_nonvoting", cfg.exclude_nonvoting)
    drop("is_shell", "dropped_shell", cfg.exclude_shells)
    drop("is_foreign_line", "dropped_foreign_line", cfg.exclude_foreign_lines)
    drop("is_fund_quote", "dropped_fund_quote", True)

    if cfg.exclude_holdcos:
        n = int(df["is_holdco"].sum())
        df = df[~df["is_holdco"]]
        stats["dropped_holdco"] = n
    else:
        stats["flagged_holdco"] = int(df["is_holdco"].sum())

    stats["after_uk_filters"] = len(df)
    return df.copy(), stats


def add_valueup_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Low-P/B flags.

    Korea had a live policy catalyst attached to almost exactly this screen -
    KRX publicly identifies firms whose PBR sits in the bottom 20% of their
    industry. The UK has no counterpart: there is no disclosure regime keyed
    to valuation, and the nearest thing to a forcing mechanism is takeover
    interest, which is not computable from this data.

    So these two columns are kept for continuity and for sorting, but the
    reader should not attach a catalyst to them the way the Korea dashboard
    legitimately could. What actually closes UK discounts is a bid.
    """
    df = df.copy()
    rank = df.get("price_to_book_pct_rank")
    df["pbr_bottom20_industry"] = (rank <= 0.20) if rank is not None else False
    df["pbr_below_1"] = df["price_to_book"] < 1.0
    return df


def add_quality_context(df: pd.DataFrame) -> pd.DataFrame:
    """ROE derived from EPS and BPS rather than taken as a vendor field.

    Same reasoning as Korea: the ratio is then consistent with the very P/E
    and P/B being screened on, and it is unit-free, so the pence-versus-pounds
    trap in providers_uk cannot reach it.
    """
    df = df.copy()
    eps = pd.to_numeric(df.get("trailing_eps"), errors="coerce")
    bps = pd.to_numeric(df.get("book_value_ps"), errors="coerce")
    df["roe_pct"] = np.where((bps > 0) & eps.notna(), eps / bps * 100.0, np.nan)
    df["div_yield"] = pd.to_numeric(df.get("div_yield"), errors="coerce")
    df["pays_dividend"] = df["div_yield"].fillna(0) > 0
    return df


def apply_roe_gate(df: pd.DataFrame, cfg: K.ScreenConfig) -> tuple[pd.DataFrame, dict]:
    """Require survivors to actually earn something.

    Runs after scoring, never before: it narrows `passes` and leaves
    avg_discount alone, so the peer cohorts still contain the low-ROE names
    that make them representative. Missing ROE fails.
    """
    df = df.copy()
    roe = pd.to_numeric(df.get("roe_pct"), errors="coerce")
    df["roe_ok"] = roe.notna() & (roe >= cfg.min_roe_pct)
    df["roe_tier"] = np.select(
        [roe >= 15.0, roe >= cfg.roe_good_pct, roe >= cfg.min_roe_pct],
        ["strong", "good", "marginal"], default="fail")

    before = int(df["passes"].sum())
    df["passes"] = df["passes"] & df["roe_ok"]
    after = int(df["passes"].sum())
    stats = {f"dropped_roe_below_{cfg.min_roe_pct:g}": before - after,
             "passing_after_roe": after}
    df = df.sort_values(["passes", "avg_discount"], ascending=[False, False])
    return df, stats


def apply_absolute_screen(df: pd.DataFrame, cfg: K.ScreenConfig) -> tuple[pd.DataFrame, dict]:
    """Absolute cheapness, independent of the peer comparison.

    Identical in structure to the Korea version, including the financials
    carve-out: EV/EBITDA is suppressed for banks and insurers, so a strict
    both-metrics rule would exclude every UK bank and life insurer - which on
    this market is a large share of everything trading below book.
    """
    df = df.copy()
    pbr = pd.to_numeric(df.get("price_to_book"), errors="coerce")
    ev = pd.to_numeric(df.get("ev_to_ebitda"), errors="coerce")
    roe = pd.to_numeric(df.get("roe_pct"), errors="coerce")
    dy = pd.to_numeric(df.get("div_yield"), errors="coerce")
    fin = df.get("sector", pd.Series("", index=df.index)).fillna("") \
            .str.contains("Financial", case=False)

    df["abs_pbr_ok"] = pbr.notna() & (pbr < cfg.abs_max_pbr)
    df["abs_ev_ok"] = ev.notna() & (ev < cfg.abs_max_ev_ebitda)
    df["abs_ev_missing"] = ev.isna()
    df["abs_roe_ok"] = roe.notna() & (roe >= cfg.min_roe_pct)

    fair_pbr = roe / cfg.abs_cost_of_equity_pct
    df["abs_fair_pbr"] = fair_pbr
    df["abs_pbr_vs_roe_ok"] = pbr.notna() & fair_pbr.notna() & (pbr < fair_pbr)
    df["abs_div_ok"] = dy.notna() & (dy >= cfg.abs_min_div_yield)

    core = df["abs_pbr_ok"] & df["abs_ev_ok"]
    if cfg.abs_financials_pbr_only:
        core = core | (fin & df["abs_pbr_ok"])
        df["abs_via_carveout"] = fin & df["abs_pbr_ok"] & ~df["abs_ev_ok"]
    else:
        df["abs_via_carveout"] = False

    if cfg.abs_require_roe:
        core = core & df["abs_roe_ok"]
    if cfg.abs_require_pbr_vs_roe:
        core = core & df["abs_pbr_vs_roe_ok"]
    if cfg.abs_min_div_yield > 0:
        core = core & df["abs_div_ok"]
    df["abs_passes"] = core

    rel = df["passes"].astype(bool)
    absp = df["abs_passes"].astype(bool)
    df["screen"] = np.select([rel & absp, rel & ~absp, ~rel & absp],
                             ["both", "relative", "absolute"], default="")
    df["passes_any"] = rel | absp

    stats = {
        f"abs_pbr_under_{cfg.abs_max_pbr:g}": int(df["abs_pbr_ok"].sum()),
        f"abs_ev_under_{cfg.abs_max_ev_ebitda:g}": int(df["abs_ev_ok"].sum()),
        "abs_pbr_below_fair": int(df["abs_pbr_vs_roe_ok"].sum()),
        f"abs_div_over_{cfg.abs_min_div_yield:g}pct": int(df["abs_div_ok"].sum()),
        "abs_passing": int(absp.sum()),
        "abs_via_financial_carveout": int((absp & df["abs_via_carveout"]).sum()),
        "abs_new_vs_relative": int((absp & ~rel).sum()),
        "passing_either_screen": int(df["passes_any"].sum()),
    }
    df = df.sort_values(["passes_any", "avg_discount"], ascending=[False, False])
    return df, stats


def uk_output_columns(cfg: K.ScreenConfig) -> list[str]:
    cols = ["ticker", "tidm", "name", "board", "tier", "sector", "industry",
            "icb_sector", "market_cap_usd", "adv_usd", "close_local", "currency"]
    for m in cfg.metrics:
        cols += [m, f"{m}_peer_median", f"{m}_discount",
                 f"{m}_peer_n", f"{m}_pct_rank"]
    cols += ["screen", "passes_any", "abs_passes", "abs_pbr_ok", "abs_ev_ok",
             "abs_pbr_vs_roe_ok", "abs_div_ok", "abs_roe_ok", "abs_fair_pbr",
             "abs_via_carveout",
             "roe_pct", "roe_ok", "roe_tier", "div_yield", "pays_dividend",
             "pbr_below_1", "pbr_bottom20_industry", "is_holdco",
             "n_valid_metrics", "n_metrics_passing", "metrics_passing",
             "avg_discount", "median_pct_rank", "passes"]
    return cols
