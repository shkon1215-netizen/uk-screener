# CLAUDE.md — UK (LSE) Valuation Screener

Screens the London Main Market + AIM for stocks ≥20% below industry-peer median
on P/E, P/B, EV/EBITDA, plus two independent screens: cheap outright, and cheap
against the company's own filed history. Gates: market cap ≥ USD 600M, median
daily traded value ≥ USD 4M. Ported from the Korea build and kept in step with
it; `screener.py` is unchanged maths.

## Run order

```bash
python test_uk.py         # offline logic check — must pass, no network needed
python check_setup.py     # tests every live call individually, Yahoo first
python main_uk.py -v      # full run, ~10 minutes cold, seconds from cache

# the two statement-based features cost three Yahoo calls per surviving name;
# skip either when you only want the peer and absolute screens
python main_uk.py --no-financials --no-history -v

# liquidity comes from Yahoo's averageVolume, which is a 3-month mean rather
# than a true 60-session ADV — skip it if you want size alone to gate
python main_uk.py --skip-liquidity -v

# each run rewrites uk_dashboard.html in place - open it, re-run, reload

# or serve it, and the Refresh button re-runs the screen for you
python serve.py                      # 127.0.0.1:8765, opens a browser
python serve.py                      # serves BOTH boards: /main and /aim
python serve.py --min-roe 8          # extra args go to every board's run

# AIM is a separate run with its own outputs
python main_uk.py --board AIM --skip-liquidity --all \
  --out aim_screen_results.csv --dashboard aim_dashboard.html
```

Always run `check_setup.py` before `main_uk.py`. Every failure mode in this
build is a *silent* one — see "Likely first failures".

## Files

| File | Role |
|---|---|
| `screener.py` | Core engine. `sanitize_metrics`, `compute_peer_benchmarks`, `score` are market-agnostic and byte-identical to Korea's. |
| `config_uk.py` | Thresholds, metric bounds, vehicle/share-class detection rules. |
| `providers_uk.py` | Roster (FTSE index tables + HL AIM 100), the AIC register, yfinance fundamentals. |
| `uk_filters.py` | UK vehicle hygiene, ROE, absolute screen. |
| `main_uk.py` | CLI. |
| `dashboard.py` | Renders a run into a self-contained HTML dashboard. |
| `serve.py` | Local server behind the dashboard's Refresh button. |
| `build_site.py` | Assembles `site/` for GitHub Pages (noindex, no Refresh button). |
| `dashboard.cmd` | Double-click launcher: starts serve.py and opens the browser. |
| `check_setup.py` | Pre-flight diagnostic. |
| `test_uk.py` | Offline tests with planted traps. Keep green. |
| `sync_dashboard_from_korea.py` | Re-derives `dashboard.py` from Korea's. See "Keeping in step with Korea". |

## Invariants — do not remove without understanding why

1. **Investment trusts are excluded, via the AIC register.** This is the UK's
   version of Korea's preferred-share trap and it is bigger: closed-end funds
   are ~22% of the Main Market roster by count (116 of 534 on the reference
   run) and they trade at a **median 8.3% discount to NAV** — permanently,
   because that is what closed-end funds do. Their "P/B" is price-to-NAV and
   it does not converge to 1. Leave them in and they fill the top of the
   table on every run.

   **No heuristic catches them.** Scottish Mortgage is filed by the index
   tables under "Collective investments", HarbourVest under "Equity
   Investments", BH Macro under "Hedge Funds", and Pershing Square under plain
   "Financial services". None of those strings contains "trust". Yahoo calls
   all four "Asset Management" — which is exactly what it calls Schroders and
   Jupiter, operating companies that must stay in. Revenue-to-market-cap does
   not separate them either (measured: ICG 0.167 vs HarbourVest 0.169).

   So the primary signal is `fetch_investment_companies()`, which reads the
   Association of Investment Companies register — ~300 entries keyed by **EPIC
   code**. This is strictly better than Korea's 관리종목 equivalent, which
   could only match on company name. The sector and name heuristics in
   `config_uk.TRUST_SECTORS` are the fallback for when that call fails, and
   `test_uk.py` plants one trust that *only* the register catches.

2. **P/E or P/B of 0 must be treated as missing, never as cheap.**
   `METRIC_BOUNDS` lower bounds enforce this. Removing them sorts every
   loss-maker to the top.

3. **Market cap is in pounds; price is in pence.** Yahoo quotes UK lines with
   `currency == "GBp"` but reports `marketCap` in GBP. Verified across nine
   tickers: `marketCap / (price × shares) == 0.0100` every time. Traded value
   computed from price is therefore 100× too large unless divided.
   `providers_uk._to_major()` is the only place that conversion happens, and
   `check_setup.py` re-verifies the ratio on every run — if Yahoo ever
   switches to pounds, that check fails loudly instead of shrinking every ADV
   by 100×.

4. **FX is USD per GBP — the opposite direction to Korea's.**
   `krw_to_usd()` returned USD per KRW; `gbp_to_usd()` returns USD per GBP.
   Both are *multiplied* in `apply_usd_conversion`. Carrying Korea's
   convention over unexamined would be a silent 1.6× error in the size gate.

5. **Peer counts exclude the stock itself**; benchmark is a *winsorized
   median*, not a mean.

6. **`avg_discount` averages across all metrics with data**, including failed
   ones.

7. **EV/EBITDA is suppressed for financials**, and financials therefore clear
   the absolute screen on P/B + ROE alone (`abs_financials_pbr_only`). On the
   reference run **9 of 10 absolute passes came via that carve-out** — a
   strict both-metrics rule would have returned one name (FirstGroup).
   `--abs-strict-financials` restores the strict reading.

8. **The ROE floor runs after scoring, never before.** It narrows `passes` and
   leaves `avg_discount` alone, so peer cohorts still contain the low-ROE
   names that make them representative. Missing ROE fails the gate. Default
   5%, `--min-roe 0` disables.

9. **Three independent screens.** Relative (peer median), absolute
   (`apply_absolute_screen`) and own-history (`apply_history_screen`) are
   scored separately and unioned into `passes_any`; `screen` lists every one a
   name cleared, joined with " + ". None gates another. Identical in structure
   to the Korea build.

10. **The absolute screen's multiples are calibrated to the UK, the quality
    floor is not.** `abs_max_pbr` 1.42 and `abs_max_ev_ebitda` 7.5 are the
    cheapest quartile of this universe, measured on the 2026-08-28 run;
    Korea's 1.0 and 8.0 do not transfer, because UK median P/B is 2.43 against
    KOSPI's 1.15. `min_roe_pct` stays at 5% and `abs_min_div_yield` at 2%
    because those encode a preference rather than a market level. The
    percentile table lives on `ScreenConfig.abs_max_pbr` and should be
    re-measured if the universe or the size floor changes - a threshold set to
    a percentile is only meaningful against the distribution it was drawn
    from.

11. **Yahoo results are cached per session date.** Not an optimisation — see
    "Likely first failures". Removing the cache makes the universe vary
    silently between runs.

## Known gaps (ranked by value of fixing)

1. **The universe is index-based, not exchange-based.** Korea screened every
   KRX listing because pykrx/Naver gave a free cross-section. London has no
   such source (see the rejected-sources list in `providers_uk.py`), so the
   roster is FTSE 100 + 250 + SmallCap + AIM 100 = **534 lines, not ~1,900
   listed companies**. At a USD 600m floor this costs little — the FTSE 350
   boundary sits near £450m and the AIM 100 contains every AIM company that
   could clear the gate — but it means `--min-mcap` below ~500e6 returns a
   universe that is *incomplete rather than merely smaller*, and the funnel
   cannot tell you what is missing. Fixing this needs a live roster with
   market caps; the LSE's own issuer list would be ideal and is currently six
   years stale.

2. **Wikipedia is the roster source for the Main Market.** It is
   community-maintained, reliably formatted, and has no API contract. A table
   restructure breaks the roster; `check_setup.py` catches it, and
   `_wiki_index` matches columns by substring rather than position to survive
   reordering.

3. **Industry classification comes from yfinance**, not ICB. The index tables
   *do* carry official ICB sectors and the code already reads them into
   `icb_sector`, but peer groups still use Yahoo's `industry` because ICB
   super-sectors are too coarse to benchmark within (11 industries for 534
   names). Using ICB subsectors properly would be the highest-value upgrade —
   same gap as Korea, one step further along.

4. **Liquidity is a proxy.** `adv_local` is Yahoo's 3-month `averageVolume` ×
   price, not a true 60-session median traded value. `--adv-days` is accepted
   and recorded but does not change the calculation.

5. **No suspended/cancelled-listing filter.** Korea filtered 관리종목; there is
   no equivalent free UK feed found. A suspended line typically stops
   returning a market cap and drops out for that reason, but not reliably.

6. No forward estimates; trailing multiples only.

## Likely first failures

- **Yahoo rate-limits, and it does not fail loudly.** This is the UK
  equivalent of Korea's KRX login wall, and the more dangerous of the two,
  because a throttled `.info` returns a dict with the fields *missing* rather
  than an error. A partially-throttled run therefore looks exactly like a
  market where most companies failed the size gate. Observed during
  development: two runs 30 seconds apart returned 253 and then 160 priced
  names off the same 534-ticker roster.

  Three defences, all deliberate:
  * `snapshot()` **probes one ticker single-threaded** before opening the
    pool. The limit is global — it rejects the crumb request that
    authenticates the session, before any symbol is looked up — so a
    throttled run would otherwise fire ~1,000 doomed requests and deepen the
    block.
  * Successful rows are **cached per session date** in `.uk_cache/`, so
    successive runs fill gaps instead of starting over.
  * `main_uk.py` **refuses to screen** when under half the roster is priced,
    rather than reporting a plausible-looking lie.

  Recovery is just waiting — minutes, sometimes an hour. `check_setup.py`
  tests this first, before anything that depends on it.

- **Wikipedia table layout changes** → empty roster. `check_setup.py` reports
  per-index counts.
- **The AIC register moves** → investment trusts reappear in the results.
  `check_setup.py` asserts four known trusts (SMT, PSH, HVPE, INPP) are
  present in the register.
- **Statements throttled.** `fetch_statements` makes three Yahoo calls per
  surviving name. A throttled call returns an empty frame rather than an error,
  and an empty frame reads as "no history" - the name quietly drops out of the
  third screen. Watch `hist_with_benchmark` in the funnel (219 of 251 on
  2026-09-18); a run far below that was throttled, not a market where history
  stopped existing. Re-running fills gaps from the cache.
- **pandas 3.x** → `pip install 'pandas<3'`.
- **yfinance returns empty `.info`** → `pip install -U yfinance`.

## AIM behaves differently from KOSDAQ, and that is worth knowing

Korea's headline finding was that KOSDAQ carried structurally richer multiples
than KOSPI (median P/B 5.56 vs 1.15), which is why `peer_keys` includes the
board. **The UK does not reproduce that**, and the numbers say so:

| | Main Market | AIM |
|---|---|---|
| cleared the USD 600m floor | 228 | **25** |
| median P/B | 2.40 | 2.57 |
| median P/E | 18.2 | 17.6 |
| median EV/EBITDA | 9.9 | 8.9 |
| P/B below 1 | 31 | 2 |
| median ROE | 12.4% | 11.6% |
| absolute screen passes | 5 | **0** |

Two consequences:

1. **The board dimension earns much less here than in Korea.** AIM and the
   Main Market trade at almost the same multiples, because the only AIM
   companies clearing a USD 600m floor are its 25 largest and most mature —
   which look like Main Market companies. `peer_keys` still defaults to
   `industry,board` for consistency with the Korea build and because the
   fallback to `industry` alone fires automatically whenever a board cohort is
   too thin. If you widen the universe downward, re-measure this table before
   trusting either setting.

2. **Only about a third of survivors get scored relatively** — 82 of 253 have
   a P/E benchmark, 87 a P/B one — because `min_peers = 5` refuses to
   benchmark against noise. The largest scored cohorts are Asset Management
   (18), Specialty Industrial Machinery (9) and Engineering & Construction
   (8). This is the same shape as Korea's KOSDAQ problem, milder. Lowering
   `--min-peers` does not fix it, it just benchmarks against noise.

3. **`--board AIM` is not the same as AIM inside a `BOTH` run, and the AIM
   page is the weaker of the two readings.** Run together, an AIM name whose
   own board cohort is too thin falls back to `industry` alone — which then
   contains Main Market companies, and that is a legitimate comparison for
   the 25 AIM names large enough to be here. Cohort plc clears the relative
   screen that way. Run alone, the fallback has only 25 rows to draw on, no
   industry reaches five peers, and **nothing passes either screen at all**.

   So the zero on the published AIM page is mostly an artefact of screening
   25 names in isolation, not a finding about AIM. The board comparison above
   is taken from the combined run for exactly this reason. If the AIM page
   ever matters more than it does now, screen it with `--peer-keys industry`
   inside a full run rather than with `--board AIM`.

## Three-year history

`fetch_statements` returns revenue, operating profit, net profit and EBITDA for
the last three filed years, oldest first, plus a compound rate per metric -
the same shape as Korea's `fetch_financials`, so Korea's dashboard renders it
unchanged.

- **Millions of the REPORTING currency, deliberately not converted.** Shell
  and Experian report in dollars, so their figures are in USD m and the
  tooltip says so (`fin_ccy`). Converting to sterling would put currency moves
  into a growth rate that should describe the business.
- **Reported, not normalized.** This column records what happened. The
  own-history screen uses normalized earnings for valuation (below); the two
  are different questions.
- **CAGR is undefined on a zero or negative base**, reported as missing rather
  than as a number with a meaningless sign - identical to Korea. The yearly
  figures always ship alongside it.
- **YoY and CAGR share one control** ("Growth shown as"), as in Korea.
- Banks have no EBITDA in Yahoo's statements, consistent with invariant 6.

## Own filed history

The third screen, ported from Korea's `apply_history_screen` with its rules
intact: >= 30% below the company's own median on >= 2 of P/E, P/B and
EV/EBITDA, plus the ROE floor. Median, not mean (invariant 4); loss years and
out-of-bounds values drop out of the benchmark (invariant 2); EV/EBITDA
skipped for financials (invariant 6); fewer than 3 usable years is no
benchmark. On 2026-09-18 it found 9 names, **none of which either of the other
two screens flags** - taking the total from 19 to 28.

What had to change for the UK, each measured rather than assumed:

- **Four filed years, not five.** Yahoo carries four for UK companies. Every
  frame has a fifth, oldest column and it is empty in every name checked, so
  it is dropped rather than read as a zero year. The dashboard says "own
  history", not "5y".
- **Built from totals, on one basis end to end.** Each past year is that
  year-end's market value over that year's filed totals; today is today's
  market value over the latest filing (`uk_filters.add_history_now`). NOT
  yfinance's `trailingPE`, which uses twelve months including interims: for
  Shell that is 10.5 against 15.2 on the filed-year basis, a 31% gap that
  would read as a discount by itself. Korea learned the same lesson on
  EV/EBITDA. The P/E and P/B columns keep yfinance's figures - they compare
  companies with each other, not a company with itself.
- **Dollar reporters are converted at each year-end's rate.** Shell, Rio,
  Experian and Hikma trade in pence and report in dollars; a market value in
  pounds over profits in dollars is off by the exchange rate. Checked:
  Experian's and Hikma's same-basis P/E land within 10% of yfinance's own.
- **Valuation uses Yahoo's normalized earnings.** They strip exactly its
  "Total Unusual Items" row. Reckitt's 2025 disposal gain doubled reported net
  income and lifted EBITDA from 3,966 to 4,760; on reported figures it passed
  on P/E and EV/EBITDA while its P/B, which a one-off cannot move, sat only 15%
  below history. Normalized also corrected Hikma, whose old impairments had
  inflated its historical P/E. Used only when available for every year in the
  window - never mixed within one company's series. It was available for all
  251 on the reference run.

Four guards refuse a benchmark rather than build a wrong one. Each is counted
in the funnel:

| guard | trigger | 2026-09-18 |
|---|---|---|
| `share-count break` | filed shares move outside 0.67-1.5x between years | 8 |
| `price/share basis mismatch` | today's price x latest shares far from market cap | 2 |
| `stale filings` | latest filing more than 18 months old | 13 |
| `no reporting currency` | Yahoo gives no `financialCurrency` | 1 |

The share breaks are all real corporate actions (Harbour Energy's
Wintershall deal, Rathbones/Investec, Metro Bank's recapitalisation, Pinewood's
consolidation). Stale filings are Yahoo missing a year that has been published:
Craneware read 52.6x on the same basis against 28.6x trailing and passed on
that gap alone. The Georgian lari reporters (TBC Bank, Lion Finance) get no
benchmark either - Yahoo holds one day of GEL rates, directly or via USD.

Known weakness, same as Korea's: 2 of 3 lets a name pass while one metric is
meaningless. Pennon passes on a genuine EV/EBITDA discount plus a P/E history
of [167, 138, -, 20] from years when a water utility earned almost nothing.
The tooltip shows every year, and `hist_avg_disc` overstates it - read it.

**The statements cache holds derived records**, so a change to
`build_statement_record` does not reach cached names. Bump
`STATEMENTS_CACHE_VERSION` whenever that function's output changes.

## Keeping in step with Korea

`dashboard.py` is Korea's `dashboard.py` with a fixed set of UK substitutions,
applied by `sync_dashboard_from_korea.py`. When Korea's dashboard changes:

```bash
python sync_dashboard_from_korea.py ../Korea/dashboard.py
```

Every substitution must match exactly once; any that no longer match are
listed and the exit code is 1. Then grep the result for Korean text, `KOSPI`,
`PER`/`PBR` and `ticker` to catch anything Korea added that the list does not
know about, and confirm in a browser that the page's verdict equals the Python
funnel at default thresholds.

The data side has no such shortcut - Korea's features arrive through Naver and
WiseReport, which have no UK equivalent. Port the screen logic into
`uk_filters.py`, rebuild the inputs from Yahoo, and measure whether the two
sides of any comparison are on the same basis before trusting it.

## Publishing

Live: https://shkon1215-netizen.github.io/uk-screener/ (AIM at `/aim.html`).
Unlisted - `noindex` plus a blanket `robots.txt` - but the repo itself is
public, which free Pages requires. No screen output is committed; results are
regenerated on every run.

`.github/workflows/screen.yml` runs both boards at 17:00 UTC on weekdays,
builds `site/`, and deploys to GitHub Pages. AIM is `continue-on-error`.

**Yahoo does answer GitHub's runners** - confirmed on the first run,
2026-09-19: 425 of 434 Main Market and 100 of 100 AIM tickers priced, no
retry needed. The published Main Market page is a `--board MAIN` run, so its
counts differ slightly from a local `BOTH` run (233 vs 251 past the size gate
on the same close) - AIM names are simply not in it.

**The first push did not trigger a run.** The workflow ignores `**.md` pushes,
and the tip of the initial push was a README-only commit, so GitHub evaluated
the filter against that and skipped it. It was started with
`gh workflow run screen.yml`. Any later push that touches code triggers
normally; a docs-only push deliberately does not.

The published pages have no Refresh button — there is no Python behind static
hosting — so `build_site.py` replaces it with the rebuild schedule. Pages are
`noindex, nofollow` plus a blanket `robots.txt`: unlisted, not secret.

**CI runs are more exposed to Yahoo's rate limit than local ones**, because
shared runner IPs are already busy. The workflow retries once after a pause
for this reason, and the refusal-to-screen guard means a throttled run fails
the job rather than publishing a thin universe.

## Adjustable thresholds

Identical to the Korea build: the dashboard re-evaluates ALL THREE screens in
the browser, every input travels with each row, and `evaluate()` mirrors
`apply_roe_gate`, `apply_absolute_screen` and `apply_history_screen` including
the rule that a missing value fails a test it is subject to. For the history
screen only the threshold and metric count are live; the medians, and today's
same-basis values (`per_now`, `pbr_now`, `evx_now`, already bounded in Python),
travel with the row. Verified 2026-09-19: 28 / 11 / 11 / 9 in the browser at
default thresholds, identical to the Python funnel, with no row disagreeing on
the history verdict. Settings persist per board in
localStorage; Reset returns to the published run.

Peer medians and sub-floor market caps remain NOT adjustable, for the same
reasons as Korea — they cannot be recomputed from the shipped rows.

If the client-side verdict at default settings disagrees with the Python
funnel, that is a real bug.

Every value a threshold is applied to ships at **6dp, not the 2dp the table
displays** — `trailing_pe`, `price_to_book`, `ev_to_ebitda`, `roe_pct`,
`div_yield`, `per_now`, `pbr_now`, `evx_now`, the per-metric discounts and the
history medians. That is the fix Korea carries and this build inherits through
the sync. The 28 / 11 / 11 / 9 above always agreed, but two per-test counts did
not: Frasers at P/B 1.398222 vs fair value 1.4044 rounds to 1.40 vs 1.40 and
drops out (35 → 34), and Genuit at ROE 4.969% rounds UP to 5.0 and passes a 5%
floor it fails. Re-verified 2026-09-19 with the six-figure payload: every
per-test count matches too.

## Interpretation

Sort by `avg_discount`, then read `roe_pct` immediately. Low P/B + high ROE is
a possible mispricing; low P/B + low ROE is arithmetic.

Two UK-specific cautions:

- **There is no Value-Up catalyst here.** Korea's screen sat alongside a KRX
  disclosure regime keyed to low P/B, which gave the cheapness a forcing
  mechanism. The UK has no counterpart. What actually closes UK discounts is a
  takeover bid — which is not computable from this data, and is why the
  `pbr_bottom20_industry` flag is kept for sorting but should not be read as a
  catalyst.
- **The cheap-UK trade is well known.** UK large caps have traded below global
  peers for most of a decade and the reasons are widely rehearsed. Assume the
  obvious names are found.

Research tool, not investment advice.
