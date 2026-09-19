"""Configuration for the UK (LSE) valuation screener.

Thresholds are carried over from the Korea build unchanged - they are the
user's screening preferences, not a Korea-specific calibration. What differs
here is entirely universe hygiene: the vehicles that are structurally cheap on
the London market are not the ones that are structurally cheap in Seoul.
"""
from __future__ import annotations

from dataclasses import dataclass

VALUATION_METRICS = ("trailing_pe", "price_to_book", "ev_to_ebitda")

METRIC_LABELS = {
    "trailing_pe": "P/E",
    "price_to_book": "P/B",
    "ev_to_ebitda": "EV/EBITDA",
}

# Same reasoning as Korea: a zero or negative multiple means "no earnings" or
# "negative equity", never "cheap". yfinance already nulls most negatives, but
# it does report absurd trailing P/Es for near-zero earnings, so the upper
# bound matters more here than it did on KRX.
METRIC_BOUNDS = {
    "trailing_pe": (1.0, 200.0),
    "price_to_book": (0.05, 30.0),
    "ev_to_ebitda": (0.5, 100.0),
}


@dataclass
class ScreenConfig:
    # --- Size / liquidity, specified in USD then converted at live FX ---
    min_market_cap_usd: float = 600_000_000     # ~GBP 470m at 1.28
    min_adv_usd: float = 4_000_000              # ~GBP 3.1m
    adv_lookback_days: int = 60                 # trading days

    # --- Valuation test ---
    discount_threshold: float = 0.20
    metrics: tuple[str, ...] = VALUATION_METRICS
    min_metrics_passing: int = 2
    min_valid_metrics: int = 2

    # --- Quality floor ---
    min_roe_pct: float = 5.0
    roe_good_pct: float = 10.0

    # --- Absolute value screen ---
    abs_max_pbr: float = 1.0
    abs_max_ev_ebitda: float = 8.0
    abs_require_roe: bool = True
    abs_financials_pbr_only: bool = True

    abs_require_pbr_vs_roe: bool = True
    abs_cost_of_equity_pct: float = 10.0

    abs_min_div_yield: float = 2.0

    # --- Peer groups ---
    # Korea used industry x board because KOSDAQ carries structurally richer
    # multiples than KOSPI. The UK analogue is Main Market vs AIM, and the
    # same caution applies: AIM is a growth/junior board and pooling it with
    # the Main Market corrupts both cohorts.
    peer_keys: tuple[str, ...] = ("industry", "board")
    fallback_peer_keys: tuple[str, ...] = ("industry",)
    min_peers: int = 5
    winsor_pct: float = 0.05

    # --- UK-specific universe hygiene ---
    # Investment trusts are to London what preferred shares are to Seoul: a
    # large, permanently-discounted class that a naive screener ranks at the
    # top on every single run. See uk_filters.apply_uk_filters.
    exclude_investment_trusts: bool = True
    exclude_reits: bool = True          # NAV-based, not earnings-based
    exclude_nonvoting: bool = True      # 'A'/non-voting lines: no vote, no convergence
    exclude_shells: bool = True         # cash shells, SPACs, VCTs
    exclude_foreign_lines: bool = True  # GDRs and secondary listings of foreign issuers
    flag_holdcos: bool = True
    exclude_holdcos: bool = False       # flagged by default, not dropped

    exclude_sectors: tuple[str, ...] = ()
    boards: tuple[str, ...] = ("MAIN", "AIM")

    # --- Fetching ---
    # Yahoo throttles on sustained bulk access and the block is global, not
    # per-ticker, so it costs the whole run rather than one name. Two workers
    # with a real delay fetches 534 names in about three minutes and has been
    # the difference between completing and being cut off mid-roster.
    max_workers: int = 2
    request_delay: float = 0.25
    cache_dir: str = ".uk_cache"
    cache_ttl_hours: int = 20


# ---------------------------------------------------------------------------
# Vehicle and share-class detection
# ---------------------------------------------------------------------------
# These are the FALLBACK. The primary signal for investment companies is the
# AIC register in providers_uk.fetch_investment_companies, which matches on
# ticker and is unambiguous. Everything below is what the screen degrades to
# when that call fails.
#
# The sector list is long because the FTSE constituent tables are inconsistent
# about what they call a closed-end fund: Scottish Mortgage is filed under
# "Collective investments", HarbourVest under "Equity Investments", BH Macro
# under "Hedge Funds". None of them contains the word "trust", and Pershing
# Square is filed as plain "Financial services", which nothing here can catch.
# That residue is precisely why the register is the primary signal.

TRUST_SECTORS = (
    "investment trust", "investment trusts", "equity investment instruments",
    "equity investments", "closed end investment", "collective investment",
    "hedge fund", "nonequity investment instruments", "investment company",
)
REIT_SECTORS = ("reit", "real estate investment trust")

TRUST_TOKENS = (
    "INVESTMENT TRUST", "INV TRUST", " IT PLC", "INVESTMENT COMPANY",
    "INVESTMENT CO", "VCT", "VENTURE CAPITAL TRUST", "INCOME & GROWTH",
    "INCOME AND GROWTH", "SPLIT LEVEL", "INVESTMENT FUND", " TRUST PLC",
)
REIT_TOKENS = ("REIT", "REAL ESTATE INVESTMENT")
SHELL_TOKENS = ("SPAC", "ACQUISITION CORP", "ACQUISITION COMPANY", "CASH SHELL",
                "CAPITAL PARTNERS PLC")
HOLDCO_TOKENS = ("HOLDINGS", "HOLDING", "GROUP HOLDINGS", "HLDGS")

# Non-voting / restricted-voting lines. UK equivalents of Korea's 우선주:
# Schroders' non-voting line (SDRC) has traded persistently below the ordinary
# (SDR) for the same reason - it carries no vote and nothing forces the gap to
# close. Same for the classic "'A' Ordinary" structures.
NONVOTING_TOKENS = ("NON-VTG", "NON VTG", "NON-VOTING", "NON VOTING",
                    "'A' ORD", "A ORD", "NV ORD")
NONVOTING_SUFFIXES = ("C", "A")   # only meaningful with a matching base line

# GDRs and secondary listings of foreign issuers. Index membership in the FTSE
# UK Series already requires UK nationality, so this mostly matters when the
# roster is widened; it stays on as a cheap guard.
FOREIGN_TOKENS = ("GDR", "ADR", "DEPOSITARY RECEIPT", "REG S", "144A")


def _has(name: str, tokens) -> bool:
    n = str(name).upper()
    return any(tok.upper() in n for tok in tokens)


def _sector_is(sector: str, needles) -> bool:
    s = str(sector).strip().lower()
    return any(nd in s for nd in needles)


def is_investment_trust(name: str, sector: str = "") -> bool:
    # REITs first: "Real Estate Investment Trusts" contains "investment trust",
    # so without this every REIT would be counted in the trust line of the
    # funnel and the REIT line would read zero. Both are excluded either way -
    # this only keeps the funnel honest about which rule did it.
    if is_reit(name, sector):
        return False
    return _sector_is(sector, TRUST_SECTORS) or _has(name, TRUST_TOKENS)


def is_reit(name: str, sector: str = "") -> bool:
    return _sector_is(sector, REIT_SECTORS) or _has(name, REIT_TOKENS)


def is_shell(name: str) -> bool:
    return _has(name, SHELL_TOKENS)


def is_holdco(name: str) -> bool:
    return _has(name, HOLDCO_TOKENS)


def is_foreign_line(name: str) -> bool:
    return _has(name, FOREIGN_TOKENS)


def is_nonvoting_name(name: str) -> bool:
    return _has(name, NONVOTING_TOKENS)


def base_line_of(ticker: str) -> str:
    """The ordinary line a restricted-voting ticker belongs to.

    LSE TIDMs have no digit convention like KRX's, so this is a weaker signal
    than Korea's `common_line_of`: it strips a trailing 'C'/'A' and is only
    trusted when the stripped ticker actually exists in the same roster.
    """
    t = str(ticker).strip().upper()
    if len(t) >= 3 and t[-1] in NONVOTING_SUFFIXES:
        return t[:-1]
    return t
