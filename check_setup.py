"""Pre-flight diagnostic for the UK screener. Tests every live call on its own.

    python check_setup.py

Run this before main_uk.py. A full run is not slow enough to be painful, but
every failure mode this build has is a silent one: a rate-limited Yahoo returns
rows with the fields missing rather than an error, a changed Wikipedia table
layout yields an empty roster, and a moved AIC register turns the investment
trust filter off without saying so. Each of those produces a plausible-looking
screen that is wrong. This file makes them loud.

Ordered by dependency, cheapest and most-likely-to-fail first. Yahoo's rate
limit is checked before anything that depends on it, the same way the Korea
build checks KRX credentials before the calls that need them.
"""
from __future__ import annotations

import sys
import traceback

OK, BAD, WARN = "  OK  ", " FAIL ", " WARN "
results: list[tuple[str, str]] = []


def check(label: str):
    def deco(fn):
        print(f"\n--- {label} ---")
        try:
            status, detail = fn()
        except Exception as e:                          # noqa: BLE001
            status, detail = BAD, f"{type(e).__name__}: {e}"
            traceback.print_exc(limit=2)
        print(f"{status} {detail}")
        results.append((label, status))
        return fn
    return deco


def main() -> int:
    print("UK screener pre-flight")

    # ---------------------------------------------------------------- deps
    @check("python packages")
    def _():
        import pandas as pd
        missing = []
        for mod in ("numpy", "requests", "yfinance", "lxml"):
            try:
                __import__(mod)
            except ImportError:
                missing.append(mod)
        if missing:
            return BAD, f"missing: {', '.join(missing)}  ->  pip install {' '.join(missing)}"
        if int(pd.__version__.split(".")[0]) >= 3:
            return WARN, f"pandas {pd.__version__}; this build is tested on 2.x"
        return OK, f"pandas {pd.__version__}, all imports present"

    # ------------------------------------------------------- yahoo, first
    # Everything downstream needs this, and it is the call that actually
    # fails in practice - so it is checked before the things that depend on
    # it, not after.
    @check("yfinance / Yahoo rate limit")
    def _():
        import yfinance as yf
        try:
            i = yf.Ticker("SHEL.L").info or {}
        except Exception as e:
            if "RateLimit" in type(e).__name__ or "Too Many Requests" in str(e):
                return BAD, ("Yahoo is rate-limiting this IP. Nothing will fetch "
                             "until it clears - usually minutes, sometimes an "
                             "hour. Re-run this check before main_uk.py.")
            raise
        mc = i.get("marketCap")
        if not mc:
            return BAD, ("SHEL.L returned no marketCap. Either throttled or "
                         "Yahoo changed its payload; try `pip install -U yfinance`.")
        return OK, (f"SHEL.L marketCap={mc:,}  currency={i.get('currency')}  "
                    f"P/B={i.get('priceToBook')}  industry={i.get('industry')!r}")

    # ------------------------------------------------- the GBp/GBP trap
    @check("pence/pounds unit convention")
    def _():
        import yfinance as yf
        i = yf.Ticker("SHEL.L").info or {}
        p, s, m = (i.get("regularMarketPrice"), i.get("sharesOutstanding"),
                   i.get("marketCap"))
        if not (p and s and m):
            return WARN, "could not read price/shares/marketCap to verify"
        ratio = m / (p * s)
        # Yahoo quotes UK lines in pence but reports marketCap in pounds, so
        # this ratio is 0.01. If it ever becomes 1.0, Yahoo has changed
        # convention and providers_uk._to_major must stop dividing.
        if abs(ratio - 0.01) < 0.002:
            return OK, f"marketCap/(price x shares) = {ratio:.4f} - pence, as expected"
        if abs(ratio - 1.0) < 0.2:
            return BAD, (f"ratio = {ratio:.4f}. Yahoo now reports price in POUNDS. "
                         "providers_uk._to_major divides by 100 and would make "
                         "every traded value 100x too small.")
        return WARN, f"ratio = {ratio:.4f} - neither 0.01 nor 1.0; investigate"

    # ---------------------------------------------------------- roster
    @check("FTSE constituent tables (roster)")
    def _():
        from config_uk import ScreenConfig
        from providers_uk import LSEProvider
        prov = LSEProvider(ScreenConfig())
        rows = []
        for url, tier in __import__("providers_uk").WIKI_INDICES:
            d = prov._wiki_index(url, tier)
            rows.append(f"{tier}={len(d)}")
            if d.empty:
                return BAD, (f"{tier} produced no rows. The Wikipedia table layout "
                             f"changed; fix the column matching in "
                             f"providers_uk._wiki_index. ({', '.join(rows)})")
        return OK, ", ".join(rows)

    @check("AIM 100 (Hargreaves Lansdown)")
    def _():
        from config_uk import ScreenConfig
        from providers_uk import LSEProvider
        d = LSEProvider(ScreenConfig())._aim()
        if d.empty:
            return WARN, ("no AIM table. The Main Market screen still works; "
                          "AIM will simply be absent.")
        return OK, f"{len(d)} AIM constituents"

    # ------------------------------------------- the single most important
    @check("AIC investment-company register")
    def _():
        from providers_uk import fetch_investment_companies
        aic = fetch_investment_companies()
        if not aic:
            return BAD, ("register unavailable. The screen will fall back to "
                         "sector and name heuristics, which do NOT catch "
                         "closed-end funds filed under plain 'Financial "
                         "services' - expect investment trusts in the results.")
        known = [t for t in ("SMT", "PSH", "HVPE", "INPP") if t in aic]
        if len(known) < 3:
            return WARN, (f"{len(aic)} entries but only matched {known} of the "
                          "four control tickers; the payload shape may have changed")
        return OK, (f"{len(aic)} investment companies; control tickers "
                    f"{', '.join(known)} all present")

    # ------------------------------------------------------------- FX
    @check("GBP/USD rate")
    def _():
        from config_uk import ScreenConfig
        from providers_uk import LSEProvider
        fx = LSEProvider(ScreenConfig()).gbp_to_usd()
        if not (1.0 < fx < 2.0):
            return BAD, (f"1 GBP = {fx} USD is outside any plausible range. "
                         "Check the direction - this must be USD per GBP.")
        if abs(fx - 1.27) < 1e-9:
            return WARN, "using the hardcoded 1.27 fallback; live lookup failed"
        return OK, f"1 GBP = {fx:.4f} USD"

    # ----------------------------------------------------------- offline
    @check("offline logic (test_uk.py)")
    def _():
        import subprocess
        r = subprocess.run([sys.executable, "test_uk.py"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            tail = (r.stdout or "").strip().splitlines()[-3:]
            return BAD, "test_uk.py failed: " + " | ".join(tail)
        return OK, "all planted cases behave"

    print("\n" + "=" * 66)
    bad = [l for l, s in results if s == BAD]
    warn = [l for l, s in results if s == WARN]
    for label, status in results:
        print(f"{status} {label}")
    if bad:
        print(f"\n{len(bad)} blocking problem(s): {', '.join(bad)}")
        print("Fix these before running main_uk.py.")
        return 1
    if warn:
        print(f"\nready, with {len(warn)} warning(s): {', '.join(warn)}")
        return 0
    print("\nready - run:  python main_uk.py --skip-liquidity -v")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
