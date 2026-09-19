"""Data providers for the UK (LSE) screener.

Korea had a two-source story: KRX behind a login, KIND+Naver in front of it.
The UK has no equivalent of either. What it has instead:

  roster       FTSE index constituent tables (Wikipedia, community-maintained
               but reliably formatted) for the Main Market, and Hargreaves
               Lansdown's FTSE AIM 100 page for AIM. Both carry the TIDM and,
               for the Main Market, the official ICB sector - which names
               "Investment Trust" and "REIT" outright. That sector string is
               the single most valuable field in this whole module; see
               uk_filters.

  fundamentals yfinance, one call per ticker. On UK lines .info returns market
               cap, all three multiples, EPS, book value, dividend yield,
               sector, industry and average volume together, so unlike Korea
               there is no cheap cross-section to gate on first. That is
               affordable here only because the roster is already
               index-bounded at ~550 names - see the invariant-7 note in
               main_uk.py.

Sources rejected, and why - each cost a probe, so they are recorded here
rather than left to be rediscovered:

  docs.londonstockexchange.com "Issuer list" xlsx
      Downloads fine and is the closest thing to a KIND analogue: 1,990
      companies with ICB industry, country of incorporation, an International
      Issuer flag and market cap. It is stamped "As at 30 September 2020".
      Six years stale, so useless as a live roster - but worth re-checking,
      because if LSE ever refreshes it, it is strictly better than everything
      above.
  api.londonstockexchange.com price-explorer
      Answers 200 with an empty array for every parameter shape tried. The
      endpoint moved.
  stockanalysis.com
      Its SvelteKit __data.json returns the top 500 LSE lines by market cap,
      but ignores the page parameter, carries no country or industry column,
      and 904 of the first 1,000 lines are foreign secondary listings
      (NVIDIA, Apple, Samsung GDR). Its public screener API answers 404 on
      every path tried.
  iShares / SSGA ETF holdings files
      Now return HTML, not CSV.

THE UNIT TRAP: Yahoo quotes UK lines in pence (currency "GBp") but reports
marketCap in pounds. Verified across nine tickers: marketCap / (price x
shares) is exactly 0.0100 every time. Anything derived from price - traded
value above all - is therefore 100x too large unless divided. _to_major() is
the only place that conversion happens.
"""
from __future__ import annotations

import concurrent.futures as cf
import io
import json
import logging
import os
import re
import time

import numpy as np
import pandas as pd
import requests

from config_uk import ScreenConfig

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA}

# Main Market, largest first. The tier is kept because it explains the funnel
# to a reader and because it is a legitimate alternative peer key.
WIKI_INDICES = [
    ("https://en.wikipedia.org/wiki/FTSE_100_Index", "FTSE 100"),
    ("https://en.wikipedia.org/wiki/FTSE_250_Index", "FTSE 250"),
    ("https://en.wikipedia.org/wiki/FTSE_SmallCap_Index", "FTSE SmallCap"),
]
AIM_URL = "https://www.hl.co.uk/shares/stock-market-summary/ftse-aim-100"
AIC_URL = "https://www.theaic.co.uk/aic/find-compare-investment-companies"

# Every field snapshot() promises to return. Declared rather than inferred so
# that a fully throttled fetch still produces a correctly-shaped frame - see
# the reindex in snapshot().
SNAPSHOT_FIELDS = (
    "yf_name", "currency", "market_cap_local", "close_local", "adv_local",
    "trailing_pe", "price_to_book", "ev_to_ebitda", "trailing_eps",
    "book_value_ps", "div_yield", "sector", "industry", "country",
    "quote_type", "yf_shares",
    # The currency the ACCOUNTS are in, which is not the quote currency for a
    # large slice of the London market: Shell, Rio, HSBC, Glencore and BP all
    # trade in pence and report in US dollars. Anything that divides a market
    # value by a reported figure needs both, and needs them converted.
    "fin_ccy",
)


def out_schema(df: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Coerce whatever we have into the full snapshot shape, restricted to the
    requested tickers. Used on the early-exit paths so a caller never has to
    guess whether a column exists."""
    if df is None or df.empty:
        df = pd.DataFrame({"ticker": tickers})
    df = df.reindex(columns=["ticker"] + list(SNAPSHOT_FIELDS))
    return df[df["ticker"].isin(tickers)].reset_index(drop=True)


# ---------------------------------------------------------------------------
# The authoritative investment-company list
# ---------------------------------------------------------------------------
def fetch_investment_companies() -> dict[str, dict]:
    """Every UK-listed investment company, keyed by EPIC code.

    This is the UK counterpart of Korea's fetch_admin_issue_names, and it is
    the single most important call in this module - it is what stops the
    screen from being a list of closed-end funds. The Association of
    Investment Companies publishes the full register on its comparison page
    as an embedded JSON array, ~300 entries, each carrying Name, EPICCode,
    AIC sector, market cap and DiscFairCum (the discount to NAV).

    Two things make it strictly better than the Korean equivalent:

      * It matches on TICKER, not name. Korea's 관리종목 list gave only company
        names, so a renamed company slipped through. An EPIC code does not
        drift.
      * It is unambiguous where every heuristic fails. Scottish Mortgage is
        filed by the index tables under "Collective investments", HarbourVest
        under "Equity Investments" and Pershing Square under plain "Financial
        services"; none contain the word "trust" and Yahoo calls all three
        "Asset Management" - exactly what it calls Schroders and Jupiter,
        which are operating companies that must stay in. Revenue-to-market-cap
        does not separate them either (tested: ICG 0.167 vs HarbourVest
        0.169). The register does, cleanly.

    Returns {} on any failure. The caller falls back to the sector and name
    heuristics, which catch most but demonstrably not all of the class - so a
    failure here degrades the screen rather than breaking it, and the funnel
    reports which path was used.
    """
    try:
        r = requests.get(AIC_URL, headers=HEADERS, timeout=40)
        r.raise_for_status()
        html = r.text
    except Exception as e:
        log.warning("AIC register fetch failed: %s", e)
        return {}

    i = html.find('"EPICCode"')
    if i < 0:
        log.warning("AIC register: no EPICCode field found - page markup changed")
        return {}
    start = html.rfind("[", 0, i)
    depth = 0
    end = -1
    for j in range(start, len(html)):
        if html[j] == "[":
            depth += 1
        elif html[j] == "]":
            depth -= 1
            if depth == 0:
                end = j + 1
                break
    if end < 0:
        log.warning("AIC register: unbalanced array")
        return {}
    try:
        arr = json.loads(html[start:end])
    except Exception as e:
        log.warning("AIC register: JSON parse failed: %s", e)
        return {}

    out: dict[str, dict] = {}
    for rec in arr:
        epic = str(rec.get("EPICCode") or "").strip().upper()
        if not epic:
            continue
        out[epic] = {
            "aic_name": rec.get("Name"),
            "aic_sector": rec.get("AICSECTOR"),
            "aic_structure": rec.get("Structure"),
            "discount_to_nav": rec.get("DiscFairCum"),
        }
    log.info("AIC register: %d investment companies", len(out))
    return out


# ---------------------------------------------------------------------------
# Ticker normalisation
# ---------------------------------------------------------------------------
def to_yahoo(tidm: str) -> str:
    """LSE TIDM -> Yahoo symbol.

    Rolls-Royce is "RR." on the exchange and "RR.L" on Yahoo; BT Group is
    "BT.A" and "BT-A.L". So: drop a trailing dot, turn any remaining dot into
    a hyphen, append ".L".
    """
    t = str(tidm).strip().upper()
    t = re.sub(r"[^A-Z0-9.\-]", "", t).rstrip(".")
    t = t.replace(".", "-")
    return f"{t}.L" if t else ""


def _to_major(value, currency: str):
    """Pence -> pounds. The only place the GBp/GBP trap is handled."""
    if value is None:
        return None
    return value / 100.0 if str(currency) == "GBp" else value


class LSEProvider:
    def __init__(self, cfg: ScreenConfig):
        self.cfg = cfg
        self.s = requests.Session()
        self.s.headers.update(HEADERS)

    # -- calendar ----------------------------------------------------------
    def recent_business_days(self, n: int) -> list[str]:
        """Real LSE sessions, read off a liquid line rather than assumed from
        a weekday calendar - UK bank holidays match no generic one."""
        import yfinance as yf
        try:
            h = yf.Ticker("SHEL.L").history(period=f"{max(n * 2, 120)}d")
            if not h.empty:
                return [d.strftime("%Y-%m-%d") for d in h.index[-n:]]
        except Exception as e:
            log.warning("calendar via yfinance failed: %s", e)
        days = pd.bdate_range(end=pd.Timestamp.today(), periods=n)
        return [d.strftime("%Y-%m-%d") for d in days]

    # -- roster ------------------------------------------------------------
    def _wiki_index(self, url: str, tier: str) -> pd.DataFrame:
        empty = pd.DataFrame(columns=["tidm", "name", "icb_sector", "tier"])
        try:
            html = self.s.get(url, timeout=40).text
            tables = pd.read_html(io.StringIO(html))
        except Exception as e:
            log.error("index table %s failed: %s", tier, e)
            return empty

        best = None
        for t in tables:
            cols = [str(c) for c in t.columns]
            has_tick = any("icker" in c or "EPIC" in c for c in cols)
            has_name = any("Company" in c for c in cols)
            if has_tick and has_name and len(t) > 20:
                if best is None or len(t) > len(best):
                    best = t
        if best is None:
            log.error("no constituent table found on %s", tier)
            return empty

        col = {}
        for c in best.columns:
            s = str(c)
            if "icker" in s or "EPIC" in s:
                col["tidm"] = c
            elif "Company" in s:
                col["name"] = c
            elif "sector" in s.lower() or "classification" in s.lower():
                col["icb_sector"] = c
        out = best.rename(columns={v: k for k, v in col.items()})
        for need in ("tidm", "name", "icb_sector"):
            if need not in out.columns:
                out[need] = ""
        out = out[["tidm", "name", "icb_sector"]].copy()
        out["tier"] = tier
        log.info("  %-14s %3d constituents", tier, len(out))
        return out

    def _aim(self) -> pd.DataFrame:
        """AIM's top 100. AIM's largest company is well under GBP 5bn, so the
        AIM 100 already contains every AIM line that could clear a USD 600m
        floor - widening it would only add names the size gate then removes."""
        empty = pd.DataFrame(columns=["tidm", "name", "icb_sector", "tier"])
        try:
            html = self.s.get(AIM_URL, timeout=40).text
            tables = pd.read_html(io.StringIO(html))
        except Exception as e:
            log.error("AIM 100 fetch failed: %s", e)
            return empty
        for t in tables:
            cols = [str(c) for c in t.columns]
            if any("EPIC" in c for c in cols) and len(t) > 20:
                epic = [c for c in t.columns if "EPIC" in str(c)][0]
                nm = [c for c in t.columns if str(c) == "Name"]
                out = pd.DataFrame({
                    "tidm": t[epic],
                    "name": t[nm[0]] if nm else "",
                })
                out["icb_sector"] = ""      # HL does not publish ICB here
                out["tier"] = "FTSE AIM 100"
                log.info("  %-14s %3d constituents", "FTSE AIM 100", len(out))
                return out
        log.error("no AIM table found")
        return empty

    def listing_roster(self, date: str = "") -> pd.DataFrame:
        frames = [self._wiki_index(u, tier) for u, tier in WIKI_INDICES]
        parts = [f for f in frames if not f.empty]
        main = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        if not main.empty:
            main["board"] = "MAIN"
        aim = self._aim()
        if not aim.empty:
            aim["board"] = "AIM"

        keep = [d for d in (main, aim) if not d.empty]
        if not keep:
            return pd.DataFrame(columns=["ticker", "tidm", "name", "board",
                                         "tier", "icb_sector"])
        df = pd.concat(keep, ignore_index=True)

        df["tidm"] = df["tidm"].astype(str).str.strip().str.upper()
        df["name"] = df["name"].astype(str).str.strip()
        df["icb_sector"] = df["icb_sector"].fillna("").astype(str).str.strip()
        # Wikipedia and HL both emit a pagination row inside the table body.
        df = df[df["tidm"].ne("") & ~df["tidm"].str.contains("PAGE|NAN", na=False)]
        df["ticker"] = df["tidm"].map(to_yahoo)
        df = df[df["ticker"].ne("")]
        # A company promoted or relegated mid-review appears on two index
        # pages at once. Concatenation is largest-index-first, so keeping the
        # first occurrence keeps the more liquid classification.
        before = len(df)
        df = df.drop_duplicates(subset=["ticker"], keep="first")
        if before != len(df):
            log.info("  dropped %d duplicate index listings", before - len(df))
        return df.reset_index(drop=True)

    # -- fundamentals ------------------------------------------------------
    def _cache_path(self, asof: str) -> str:
        return os.path.join(self.cfg.cache_dir, f"snapshot_{asof or 'latest'}.csv")

    def snapshot(self, tickers: list[str], asof: str = "") -> pd.DataFrame:
        """One yfinance .info per ticker: everything the screen needs.

        Values come back already converted out of pence. A ticker that answers
        with no market cap yields a row of NaN rather than being dropped, so
        the funnel can count the misses instead of hiding them.

        CACHED PER DAY, and not as an optimisation. Yahoo rate-limits on
        repeated bulk access, and it does not fail loudly when it does - it
        returns an .info dict with the fields missing. Two runs half a minute
        apart produced 253 and then 160 priced names off the same 534-ticker
        roster, which would read as 93 companies having failed the size gate
        rather than as the data source throttling. Anything that changes the
        universe silently is worse than an error, so successful rows are kept
        on disk for the session date and only genuinely missing tickers are
        re-requested.
        """
        import yfinance as yf

        cached = pd.DataFrame()
        path = self._cache_path(asof)
        if os.path.exists(path):
            age_h = (time.time() - os.path.getmtime(path)) / 3600.0
            if age_h <= self.cfg.cache_ttl_hours:
                try:
                    cached = pd.read_csv(path)
                    log.info("  cache: %d rows from %s (%.1fh old)",
                             len(cached), os.path.basename(path), age_h)
                except Exception as e:
                    log.warning("  cache unreadable (%s), refetching", e)
                # A cache written before a field was added has that column
                # missing, and reindexing would quietly fill it with NaN for
                # every cached row - "all tickers served from cache" with a
                # field that nothing downstream can use. Discard it instead.
                stale = [f for f in SNAPSHOT_FIELDS if f not in cached.columns]
                if stale and not cached.empty:
                    log.info("  cache predates field(s) %s - refetching",
                             ", ".join(stale))
                    cached = pd.DataFrame()

        have = set()
        if not cached.empty and "market_cap_local" in cached.columns:
            have = set(cached.loc[cached["market_cap_local"].notna(), "ticker"])
        todo = [t for t in tickers if t not in have]
        if not todo:
            log.info("  all %d tickers served from cache", len(tickers))
            return cached[cached["ticker"].isin(tickers)].reset_index(drop=True)

        # Yahoo's rate limit is global, not per-ticker: it rejects the crumb
        # request that authenticates the session, before any symbol is looked
        # up. So probe once, single-threaded, before opening the pool. Without
        # this a throttled run fires ~1,000 requests that cannot succeed,
        # takes two minutes to say so, and deepens the block it is caught in.
        try:
            yf.Ticker(todo[0]).info
        except Exception as e:
            if "RateLimit" in type(e).__name__ or "Too Many Requests" in str(e):
                log.error("Yahoo is rate-limiting this IP (%s). Nothing was "
                          "fetched; wait a few minutes and re-run - the cache "
                          "keeps whatever has already arrived.", type(e).__name__)
                return out_schema(cached, tickers)
            log.debug("probe ticker %s failed non-fatally: %s", todo[0], e)

        def one(t: str) -> dict:
            rec = {"ticker": t}
            i = {}
            # Pace every request, not just retries. Without this the pool
            # empties the roster as fast as the network allows and Yahoo cuts
            # the session off partway through, which costs the whole run.
            if self.cfg.request_delay:
                time.sleep(self.cfg.request_delay)
            # One retry, for transient failures only. A rate-limit error is
            # not transient and not per-ticker, so retrying it just doubles
            # the load on an endpoint that has already said no.
            for attempt in (0, 1):
                try:
                    i = yf.Ticker(t).info or {}
                    if i.get("marketCap") is not None:
                        break
                except Exception as e:
                    log.debug("%s attempt %d: %s", t, attempt, e)
                    if "RateLimit" in type(e).__name__:
                        return rec
                if attempt == 0:
                    time.sleep(0.5 + self.cfg.request_delay)
            if not i:
                return rec
            ccy = i.get("currency")
            price = _to_major(i.get("regularMarketPrice") or i.get("currentPrice"), ccy)
            vol = i.get("averageVolume") or i.get("averageDailyVolume3Month")
            rec.update({
                "yf_name": i.get("longName") or i.get("shortName"),
                "currency": ccy,
                "market_cap_local": i.get("marketCap"),   # already major units
                "close_local": price,
                # Traded value, not share count - the gate is denominated in
                # money, same as Korea's.
                "adv_local": (price * vol) if (price and vol) else None,
                "trailing_pe": i.get("trailingPE"),
                "price_to_book": i.get("priceToBook"),
                "ev_to_ebitda": i.get("enterpriseToEbitda"),
                # EPS and BPS stay in quote units on purpose: uk_filters
                # derives ROE as their ratio, which is unit-free, and that
                # keeps ROE consistent with the P/E and P/B being screened.
                "trailing_eps": i.get("trailingEps"),
                "book_value_ps": i.get("bookValue"),
                "div_yield": i.get("dividendYield"),
                "sector": i.get("sector"),
                "industry": i.get("industry"),
                "country": i.get("country"),
                "quote_type": i.get("quoteType"),
                "yf_shares": i.get("sharesOutstanding"),
                "fin_ccy": i.get("financialCurrency"),
            })
            return rec

        log.info("  fetching %d tickers (%d already cached)", len(todo), len(have))
        rows: list[dict] = []
        with cf.ThreadPoolExecutor(max_workers=self.cfg.max_workers) as ex:
            for n, rec in enumerate(ex.map(one, todo), 1):
                rows.append(rec)
                if n % 100 == 0:
                    log.info("  fetched %d/%d", n, len(todo))

        fresh = pd.DataFrame(rows)
        parts = [d for d in (cached, fresh) if not d.empty]
        out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

        # Guarantee the schema. When every request is throttled, `one` returns
        # {"ticker": t} and nothing else, so the frame has one column and every
        # downstream reference to market_cap_local raises a KeyError a long way
        # from the cause. Reindexing turns a rate-limit into missing data the
        # funnel can report, instead of a traceback.
        out = out.reindex(columns=["ticker"] + list(SNAPSHOT_FIELDS))

        # A cached hit and a fresh miss for the same ticker must not both
        # survive. Unmerged duplicates inflate the roster - 534 tickers came
        # back as 786 rows - and every funnel line then counts them twice.
        # Sorting puts priced rows first, so keep="first" retains real data
        # over a throttled blank.
        out = out.sort_values("market_cap_local", na_position="last")
        out = out.drop_duplicates(subset=["ticker"], keep="first")

        try:
            os.makedirs(self.cfg.cache_dir, exist_ok=True)
            out.to_csv(self._cache_path(asof), index=False, encoding="utf-8")
        except Exception as e:
            log.warning("  could not write cache: %s", e)

        out = out[out["ticker"].isin(tickers)].reset_index(drop=True)
        got = int(out["market_cap_local"].notna().sum())
        log.info("  %d/%d tickers have a market cap", got, len(tickers))
        if got < 0.8 * len(tickers):
            log.warning("  only %.0f%% of the roster priced - Yahoo is "
                        "throttling. Re-run in a few minutes: the cache keeps "
                        "what did arrive, so successive runs fill the gaps "
                        "instead of starting over.",
                        100.0 * got / max(len(tickers), 1))
        return out

    # -- FX ----------------------------------------------------------------
    def gbp_to_usd(self) -> float:
        """USD per GBP.

        Inverted relative to Korea's krw_to_usd, which returned USD per KRW.
        Sterling is quoted the other way round and carrying Korea's convention
        over unexamined would be a silent 1.6x error in the size gate.

        Two live sources before the constant, because the whole size gate
        hangs off this one number. Yahoo is tried first for consistency with
        the prices, but it shares the rate limit that throttles the
        fundamentals fetch - and a run that has just been throttled is exactly
        when the FX call also fails. Frankfurter (ECB reference rates, no key)
        is on an unrelated host, so it answers when Yahoo will not.
        """
        import yfinance as yf
        try:
            h = yf.Ticker("GBPUSD=X").history(period="5d")
            if not h.empty:
                return float(h["Close"].iloc[-1])
        except Exception as e:
            log.info("FX via Yahoo failed (%s), trying Frankfurter", e)

        try:
            r = self.s.get("https://api.frankfurter.app/latest?from=GBP&to=USD",
                           timeout=20)
            rate = float(r.json()["rates"]["USD"])
            if 1.0 < rate < 2.0:
                log.info("FX from Frankfurter (ECB reference)")
                return rate
        except Exception as e:
            log.warning("FX via Frankfurter failed: %s", e)

        # Last resort. Logged as a warning rather than used quietly: every
        # market cap in the run is scaled by this, so a stale constant moves
        # the size gate by however far sterling has travelled since.
        log.warning("FX: both live sources failed, using the hardcoded 1.27. "
                    "The USD size gate is only as good as this number.")
        return 1.27


# ---------------------------------------------------------------------------
# Filed statements: three-year history and the own-history benchmark
# ---------------------------------------------------------------------------
# Row names Yahoo uses in its annual statements, first match wins. Net income
# is taken to COMMON shareholders where Yahoo separates it: NatWest's differs
# by ~6% because of AT1 coupons, and a P/E is a price for the common equity.
IS_ROWS = {
    "rev": ("Total Revenue", "Operating Revenue"),
    "op": ("Operating Income", "Total Operating Income As Reported"),
    "ebitda": ("EBITDA", "Normalized EBITDA"),
    "np": ("Net Income Common Stockholders", "Net Income"),
    # Yahoo's "normalized" lines strip exactly its Total Unusual Items row. The
    # 3-year growth history stays on the REPORTED rows above, as Korea's does
    # - that is what happened. The own-history VALUATION uses these instead,
    # because a one-off is not a change in what the business is worth: Reckitt
    # sold Essential Home in 2025, reported EBITDA jumped 4,760 vs 3,966
    # normalized and net income 3,182 vs 2,535, and on reported figures it
    # passed the history screen on P/E and EV/EBITDA while its P/B - which a
    # disposal gain does not move - sat only 15% below its history. For an
    # ordinary year the two lines agree to within a few percent.
    "np_norm": ("Normalized Income",),
    "ebitda_norm": ("Normalized EBITDA",),
}
BS_ROWS = {
    "equity": ("Common Stock Equity", "Stockholders Equity"),
    "shares": ("Ordinary Shares Number", "Share Issued"),
    "debt": ("Total Debt",),
    "cash": ("Cash And Cash Equivalents",
             "Cash Cash Equivalents And Short Term Investments"),
    "mi": ("Minority Interest",),
}

# Quote currencies Yahoo reports in minor units, and the major unit each is.
MINOR_CCY = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0),
             "ZAc": ("ZAR", 100.0), "ILA": ("ILS", 100.0)}

# Consecutive filed share counts outside this band are treated as a corporate
# action (consolidation, split, rights issue) rather than buybacks. Shell's
# heavy buybacks move ~7% a year, so the band is wide enough to leave them
# alone. It cannot catch small consolidations; it catches the ones that would
# otherwise manufacture a 30%+ "discount" out of a unit change.
SHARE_BREAK_BAND = (0.67, 1.5)
# Today's price x latest filed shares must land near today's market cap. If it
# does not, the price series and the share count are on different bases and
# every historical market value built from them is wrong by the same factor.
NOW_BASIS_BAND = (0.6, 1.6)
# A latest filing older than this is not the latest filing - Yahoo has missed
# one. "Today" is then today's price over earnings from two years ago, while
# yfinance's own twelve-month figure knows better: Craneware read 52.6x on
# the same basis against 28.6x trailing, and passed the history screen on that
# gap. 13 of 251 survivors (Sept 2026) had a newest filed year of 2024. Eighteen
# months leaves room for a late filer without admitting a skipped year.
MAX_FILING_AGE_DAYS = 548

# The statements cache stores DERIVED records, not raw frames (seven years of
# daily prices per name would be ~40x larger). So a change to
# build_statement_record does not reach names already cached - bump this and
# the next run refetches instead of serving numbers the new code would not
# produce.
STATEMENTS_CACHE_VERSION = 3


def cagr(values: list) -> float:
    """Compound annual growth across the span the values actually cover.

    Identical to the Korea build: undefined when the base is zero or negative.
    A company that lost money three years ago has no meaningful growth RATE,
    and inventing one is worse than reporting nothing. The yearly figures
    always ship alongside, so nothing is hidden by this.
    """
    vals = [v for v in values if v is not None and np.isfinite(v)]
    if len(vals) < 2 or vals[0] <= 0 or vals[-1] <= 0:
        return np.nan
    return (vals[-1] / vals[0]) ** (1.0 / (len(vals) - 1)) - 1.0


def major_ccy(ccy) -> tuple[str, float]:
    """(major currency, divisor) for a Yahoo quote currency."""
    c = str(ccy or "")
    return MINOR_CCY.get(c, (c, 1.0))


def _row(df: pd.DataFrame, names) -> pd.Series:
    """First matching statement row, indexed by fiscal year-end, oldest first."""
    if df is None or df.empty:
        return pd.Series(dtype=float)
    for n in names:
        if n in df.index:
            s = pd.to_numeric(df.loc[n], errors="coerce")
            s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
            return s.sort_index()
    return pd.Series(dtype=float)


def _at(series: pd.Series, when: pd.Timestamp, tolerance_days: int = 10) -> float:
    """Last value on or before `when`, if it is within `tolerance_days`. A
    fiscal year-end that falls in a data gap is missing, not the nearest price
    from months earlier."""
    if series is None or series.empty:
        return np.nan
    s = series.loc[:when].dropna()
    if s.empty or (when - s.index[-1]).days > tolerance_days:
        return np.nan
    return float(s.iloc[-1])


def _num(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return np.nan
    return f if np.isfinite(f) else np.nan


def build_statement_record(inc: pd.DataFrame, bs: pd.DataFrame,
                           px: pd.Series, fx: pd.Series | None,
                           quote_ccy: str, fin_ccy: str,
                           close_now: float, mcap_now: float,
                           fin_years: int = 3, max_hist: int = 5,
                           asof: str | pd.Timestamp | None = None) -> dict:
    """Everything the two statement-based features need, from raw frames.

    Pure: no network, so test_uk.py can exercise every trap offline. `px` is
    the daily close in QUOTE units (pence for a GBp line); `fx` converts the
    quote's MAJOR currency into the reporting currency (None when they are the
    same); `close_now` and `mcap_now` are in the quote's major currency.

    Every multiple is a market value over a reported total, never a price over
    a per-share figure. Yahoo's per-share rows are in reporting-currency units
    against a price in pence, and its EPS row is frequently rounded to zero;
    totals over totals sidestep both.

    Three-year history: the last `fin_years` filed years, oldest first, in
    millions of the REPORTING currency. Deliberately not converted - a growth
    rate should describe the business, not sterling against the dollar.

    Own history: for each filed year, that year-end market value over that
    year's filed figures, converted into the reporting currency at that
    year-end rate. Plus the latest filing's components, so the screen can put
    TODAY's market value over them on exactly the same definitions - see
    uk_filters.apply_history_screen for why that matters.
    """
    rec: dict = {"fin_ccy": fin_ccy or ""}

    rows = {k: _row(inc, v) for k, v in IS_ROWS.items()}
    rows.update({k: _row(bs, v) for k, v in BS_ROWS.items()})

    # A fiscal year counts as filed when it has revenue or net income. Yahoo
    # pads the frame with an extra, entirely empty oldest column (2021 in
    # every UK name checked), and that must not be read as a zero year.
    dates = sorted(set(rows["rev"].dropna().index) | set(rows["np"].dropna().index))
    if not dates:
        rec["hist_note"] = "no filed years"
        return rec

    # ---- three-year history ---------------------------------------------
    fy = dates[-fin_years:]
    rec["fin_years"] = ",".join(d.strftime("%Y") for d in fy)
    rec["fin_n"] = len(fy)
    for key in ("rev", "op", "ebitda", "np"):
        series = [_num(rows[key].get(d)) / 1e6 for d in fy]
        for i, v in enumerate(series, 1):
            rec[f"{key}_y{i}"] = round(v, 1) if np.isfinite(v) else np.nan
        rec[f"{key}_cagr"] = cagr(series)

    # ---- own history ------------------------------------------------------
    hy = dates[-max_hist:]
    rec["hist_years"] = ",".join(d.strftime("%Y") for d in hy)
    q_major, q_div = major_ccy(quote_ccy)
    same_ccy = bool(fin_ccy) and fin_ccy == q_major
    # No reporting currency is not the same as sterling. Assuming it would
    # treat a dollar reporter's accounts as pounds and scale every historical
    # multiple by the exchange rate - a fake discount or premium of 20-35%.
    # Missing means no benchmark (invariant 2), never a guess.
    if not fin_ccy:
        rec["hist_note"] = "no reporting currency"

    def fx_at(when):
        if same_ccy:
            return 1.0
        return _at(fx, when) if fx is not None else np.nan

    shares = [_num(rows["shares"].get(d)) for d in hy]
    lf = hy[-1]

    # Earnings basis for the VALUATION: normalized where Yahoo has it for
    # every year in the window, reported otherwise - never a mix within one
    # company's series, because a history that switches definition part-way
    # compares a year with itself on two different measures (see IS_ROWS).
    def pick(norm_key, rep_key):
        n = rows[norm_key]
        if all(np.isfinite(_num(n.get(d))) for d in hy):
            return n, "normalized"
        return rows[rep_key], "reported"
    ni_row, ni_basis = pick("np_norm", "np")
    eb_row, _ = pick("ebitda_norm", "ebitda")
    rec["hist_earnings"] = ni_basis

    # Guard 0: a stale latest filing. Checked first, because every other
    # number in the record would be struck against it.
    if asof is not None and "hist_note" not in rec:
        age = (pd.Timestamp(asof).normalize() - lf).days
        if age > MAX_FILING_AGE_DAYS:
            rec["hist_note"] = "stale filings"

    # Guard 1: a share count that jumps between filings is a corporate action.
    # The price series is split-adjusted while filed counts are not
    # necessarily, so a market value built across the break is wrong by the
    # split ratio - which the screen would read as a huge discount.
    valid = [s for s in shares if np.isfinite(s) and s > 0]
    for a, b in zip(valid, valid[1:]):
        if not (SHARE_BREAK_BAND[0] <= b / a <= SHARE_BREAK_BAND[1]):
            rec["hist_note"] = "share-count break"
            break

    # Guard 2: today's price x latest filed shares against today's market cap.
    s_lf = _num(rows["shares"].get(lf))
    if "hist_note" not in rec and np.isfinite(s_lf) and s_lf > 0:
        mc = _num(mcap_now)
        ratio = (_num(close_now) * s_lf / mc) if mc else np.nan
        if not (np.isfinite(ratio) and NOW_BASIS_BAND[0] <= ratio <= NOW_BASIS_BAND[1]):
            rec["hist_note"] = "price/share basis mismatch"

    per, pbr, evx = [], [], []
    for d, sh in zip(hy, shares):
        p = _at(px, d) / q_div if px is not None else np.nan
        mv = p * sh * fx_at(d)        # year-end market value, reporting ccy
        ni, eq = _num(ni_row.get(d)), _num(rows["equity"].get(d))
        eb = _num(eb_row.get(d))
        debt, cash = _num(rows["debt"].get(d)), _num(rows["cash"].get(d))
        mi = _num(rows["mi"].get(d))
        mi = 0.0 if not np.isfinite(mi) else mi
        # Non-positive denominators become NaN here; bounds are applied by the
        # screen, which is also where a loss year drops out of the benchmark.
        per.append(mv / ni if np.isfinite(mv) and ni > 0 else np.nan)
        pbr.append(mv / eq if np.isfinite(mv) and eq > 0 else np.nan)
        ev = mv + debt - cash + mi
        evx.append(ev / eb if np.isfinite(ev) and eb > 0 else np.nan)

    if "hist_note" in rec:
        per = pbr = evx = []
    rec["hist_per"] = [round(v, 2) if np.isfinite(v) else None for v in per]
    rec["hist_pbr"] = [round(v, 3) if np.isfinite(v) else None for v in pbr]
    rec["hist_evx"] = [round(v, 2) if np.isfinite(v) else None for v in evx]

    # Latest filing's components, for today's multiples on the same basis.
    fx_last = np.nan
    if same_ccy:
        fx_last = 1.0
    elif fx is not None and not fx.dropna().empty:
        fx_last = float(fx.dropna().iloc[-1])
    rec.update({
        # On the same earnings basis as the history, or today's value would be
        # compared across two definitions - the thing this whole design avoids.
        "lf_ni": _num(ni_row.get(lf)), "lf_equity": _num(rows["equity"].get(lf)),
        "lf_ebitda": _num(eb_row.get(lf)), "lf_debt": _num(rows["debt"].get(lf)),
        "lf_cash": _num(rows["cash"].get(lf)), "lf_mi": _num(rows["mi"].get(lf)),
        # Today's quote-major -> reporting-currency rate, carried so the screen
        # does not have to look it up again.
        "fx_now": fx_last,
    })
    rec.setdefault("hist_note", "")
    return rec


def _json_safe(rec: dict) -> dict:
    out = {}
    for k, v in rec.items():
        if isinstance(v, (float, np.floating)):
            out[k] = float(v) if np.isfinite(float(v)) else None
        elif isinstance(v, np.integer):
            out[k] = int(v)
        else:
            out[k] = v
    return out


def fetch_statements(snap: pd.DataFrame, cfg: ScreenConfig,
                     asof: str = "") -> pd.DataFrame:
    """Filed statements for the size-gated survivors. Invariant 7: this is the
    slowest work in the run, so it only ever sees names that already cleared
    every cheap filter.

    Three Yahoo calls per ticker (income statement, balance sheet, seven years
    of daily closes) plus one FX history per foreign reporting currency, paced
    like snapshot() and cached per session date for the same reason: a
    throttled statement call returns an empty frame, not an error, and an
    empty frame reads as "no history" - which quietly removes a name from the
    third screen instead of failing loudly.
    """
    import yfinance as yf

    tickers = snap["ticker"].tolist()
    path = os.path.join(cfg.cache_dir,
                        f"statements_v{STATEMENTS_CACHE_VERSION}_{asof or 'latest'}.json")
    cached: dict = {}
    if os.path.exists(path):
        age_h = (time.time() - os.path.getmtime(path)) / 3600.0
        if age_h <= cfg.cache_ttl_hours:
            try:
                with open(path, encoding="utf-8") as fh:
                    cached = json.load(fh)
            except Exception as e:
                log.warning("  statements cache unreadable (%s), refetching", e)

    # FX: one history per reporting currency that differs from the quote's.
    pairs = set()
    for _, r in snap.iterrows():
        q_major, _ = major_ccy(r.get("currency"))
        f = r.get("fin_ccy")
        if isinstance(f, str) and f and f != q_major:
            pairs.add((q_major, f))
    fxs: dict = {}
    for q, f in sorted(pairs):
        try:
            h = yf.Ticker(f"{q}{f}=X").history(period="7y", interval="1d")
            s = h["Close"].copy()
            s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
            fxs[(q, f)] = s
            log.info("  FX history %s->%s: %d days", q, f, len(s))
        except Exception as e:
            log.warning("  FX history %s->%s failed: %s - those reporters get "
                        "no own-history benchmark", q, f, e)

    todo = [t for t in tickers if t not in cached]
    rows = {t: r for t, r in snap.set_index("ticker").iterrows()}

    def one(t: str):
        if cfg.request_delay:
            time.sleep(cfg.request_delay)
        r = rows[t]
        try:
            tk = yf.Ticker(t)
            inc, bs = tk.income_stmt, tk.balance_sheet
            h = tk.history(period="7y", interval="1d", auto_adjust=False)
        except Exception as e:
            log.debug("%s statements: %s", t, e)
            return t, None
        if inc is None or inc.empty:
            return t, None
        px = pd.Series(dtype=float)
        if h is not None and not h.empty:
            px = h["Close"].copy()
            px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
        q_major, _ = major_ccy(r.get("currency"))
        f = r.get("fin_ccy") if isinstance(r.get("fin_ccy"), str) else ""
        rec = build_statement_record(
            inc, bs, px, fxs.get((q_major, f)), r.get("currency"), f,
            _num(r.get("close_local")), _num(r.get("market_cap_local")),
            asof=asof or None)
        return t, _json_safe(rec)

    if todo:
        log.info("  statements: fetching %d tickers (%d cached)", len(todo),
                 len(tickers) - len(todo))
        with cf.ThreadPoolExecutor(max_workers=cfg.max_workers) as ex:
            for n, (t, rec) in enumerate(ex.map(one, todo), 1):
                if rec is not None:
                    cached[t] = rec
                if n % 50 == 0:
                    log.info("  statements %d/%d", n, len(todo))
        try:
            os.makedirs(cfg.cache_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(cached, fh)
        except Exception as e:
            log.warning("  could not write statements cache: %s", e)

    got = [t for t in tickers if t in cached]
    log.info("  statements for %d/%d survivors", len(got), len(tickers))
    if len(got) < 0.8 * len(tickers):
        log.warning("  only %.0f%% have statements - Yahoo is probably "
                    "throttling. The two statement features will be thin; "
                    "re-run to fill the gaps from cache.",
                    100.0 * len(got) / max(len(tickers), 1))
    if not got:
        return pd.DataFrame(columns=["ticker"])
    out = pd.DataFrame([{"ticker": t, **cached[t]} for t in got])
    for k in ("hist_per", "hist_pbr", "hist_evx"):
        if k in out.columns:
            out[k] = out[k].map(lambda v: v if isinstance(v, list) else [])
    # fin_ccy also comes from the snapshot, and the snapshot's is the one kept.
    return out.drop(columns=["fin_ccy"], errors="ignore")
