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
import logging
import os
import re
import time

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
        import json
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
