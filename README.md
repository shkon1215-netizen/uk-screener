# UK (LSE) Relative Valuation Screener

**Live:** https://shkon1215-netizen.github.io/uk-screener/ · rebuilt each
weekday after the LSE close

Finds London Main Market and AIM stocks trading at a discount to their industry
peers on P/E, P/B, and EV/EBITDA — and, independently, stocks that are cheap in
absolute terms, and stocks that are cheap against their own filed history. Each
row also carries three years of revenue, EBITDA and net profit.

**Defaults:** market cap ≥ USD 600M (~£440M) · median daily traded value ≥
USD 4M · ≥20% below peer median on ≥2 of 3 metrics · ROE ≥ 5%.

Ported from the [Korea screener](https://github.com/shkon1215-netizen/korea-screener). `screener.py` is unchanged — the
relative-valuation maths does not care which market it points at. What was
rewritten is the data layer and the local share-class hygiene.

## Setup

```bash
pip install -r requirements.txt
python test_uk.py        # offline logic check, no network needed
python check_setup.py    # tests every live call individually
python main_uk.py -v     # full Main Market + AIM run, 2-4 minutes
```

```bash
python main_uk.py --board MAIN                       # Main Market only
python main_uk.py --discount 0.15 --min-metrics 1    # looser
python main_uk.py --include-trusts --all             # see why this is off
python main_uk.py --fx 1.36                          # pin the FX rate
```

Results cache to `.uk_cache/` per session date, so re-runs are fast — and, more
importantly, so a rate-limited Yahoo cannot silently shrink the universe.

## The dashboard

Every run writes a self-contained `uk_dashboard.html`. Open it directly, or:

```bash
python serve.py
```

which serves both boards and makes the **Refresh** button work — it shells out
to `main_uk.py` and streams progress back. Opened off disk the button is
replaced by the command line instead, because a `file://` page cannot start a
process. On Windows, double-click `dashboard.cmd`.

Every threshold on the page is adjustable and re-evaluated **in the browser**:
both screens are recomputed from the shipped rows as you move a number. Peer
medians and market caps below the run's floor are deliberately not adjustable,
because they cannot be recomputed after the fact — the page says so.

## UK-specific traps this handles

**Investment trusts — the big one.** Closed-end funds are about a fifth of the
London market by count. They trade persistently below their own stated book —
a median **8.3% discount to NAV** on the reference run — because that is what
closed-end funds do, not because they are mispriced. Their "P/B" is
price-to-NAV and it does not converge to 1. A screener that ignores this puts
investment trusts at the top of the list on every single run.

They are also genuinely hard to identify. Scottish Mortgage is filed by the
index tables under "Collective investments", HarbourVest under "Equity
Investments", BH Macro under "Hedge Funds", and Pershing Square under plain
"Financial services" — none of which contains the word "trust". Yahoo labels
all four "Asset Management", which is exactly what it calls Schroders and
Jupiter, operating companies that must stay in.

So the screener reads the **Association of Investment Companies register** —
~300 entries keyed by EPIC code — as the primary signal, with sector and name
heuristics only as a fallback. `--include-trusts` puts them back if you want to
see the difference.

**Pence versus pounds.** Yahoo quotes UK lines in pence (`currency == "GBp"`)
but reports `marketCap` in pounds. Verified across nine tickers:
`marketCap / (price × shares)` is exactly `0.0100` every time. Any traded value
computed from price is 100× too large unless divided, and `check_setup.py`
re-verifies the ratio on every run.

**Non-voting lines.** The UK's version of Korea's 우선주 — Schroders' non-voting
line trades below the ordinary for the same reason, no vote and nothing forcing
convergence. Excluded when the ordinary line is present in the same roster.

**REITs and cash shells** are excluded; **holding companies** are flagged, not
dropped.

## Three independent screens

The **relative** screen asks whether a name is cheap against its own industry
peers. The **own-history** screen asks whether it is cheap against *itself*:
at least 30% below the median of its last four filed years on at least two of
P/E, P/B and EV/EBITDA, plus the ROE floor. It catches a premium company that has
de-rated, which neither of the other two notice. The **absolute** screen ignores
the neighbours: P/B < 1.42,
EV/EBITDA < 7.5, P/B below fair value (ROE ÷ cost of equity), dividend
yield ≥ 2%, ROE ≥ 5%.

Those first two are the **cheapest quartile of this universe**, measured rather
than inherited — Korea's P/B < 1 does not transfer, because UK median P/B is
2.43 against KOSPI's 1.15. See `config_uk.py` for the percentile table.

None gates another; results are unioned and a `screen` column lists every
one a name cleared. Financials clear the absolute screen on P/B + ROE
alone, because enterprise value is meaningless for a bank — on the reference
run 9 of 10 absolute passes came through that carve-out, so the strict
reading would have returned one name.

## What differs from the Korea build

**There is no free cross-section of the London market.** Korea got its whole
~2,600-name universe from one pykrx call. London has no equivalent — the LSE's
own issuer list is six years stale, its price-explorer API returns empty, and
the aggregators either paginate to 500 rows or answer 404. The roster is
therefore **index-based**: FTSE 100 + 250 + SmallCap + AIM 100 = 534 lines. At a
USD 600M floor that costs little, but it is the build's main structural
limitation and it is documented in `CLAUDE.md`.

**AIM is not KOSDAQ.** Korea's headline finding was that KOSDAQ carried
structurally richer multiples than KOSPI (median P/B 5.56 vs 1.15), which
justified splitting peer groups by board. The UK does not reproduce that — AIM
and the Main Market sit at a median P/B of 2.57 and 2.40. The board dimension
is kept for consistency, but it earns much less here.

**Yahoo rate-limits, and does so silently.** This is the UK's version of
Korea's KRX login wall and it is the more dangerous of the two, because a
throttled request returns a dict with the fields *missing* rather than an
error — which looks exactly like a company that failed the size gate. The
screener probes once before fetching, caches every success, and **refuses to
screen** when under half the roster is priced.

## Interpretation

Sort by `avg_discount`, then read `roe_pct` immediately. Low P/B with high ROE
is a possible mispricing; low P/B with low ROE is arithmetic — a company not
earning its cost of capital, priced accordingly.

Unlike Korea, there is **no policy catalyst** here. KRX runs a disclosure
regime keyed to low P/B; the UK has no counterpart. What closes UK discounts is
a takeover bid, which this data cannot predict.

Research tool, not investment advice.
