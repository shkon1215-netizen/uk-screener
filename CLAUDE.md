# CLAUDE.md — UK (LSE) Valuation Screener

Screens the London Main Market + AIM for stocks ≥20% below industry-peer median
on P/E, P/B, EV/EBITDA. Gates: market cap ≥ USD 600M, median daily traded value
≥ USD 4M. Ported from the Korea build; `screener.py` is unchanged maths.

## Run order

```bash
python test_uk.py         # offline logic check — must pass, no network needed
python check_setup.py     # tests every live call individually, Yahoo first
python main_uk.py -v      # full run, 2–4 minutes

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

9. **Two independent screens**, unioned into `passes_any`; `screen` records
   which one a name cleared. Neither gates the other.

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

## Publishing

`.github/workflows/screen.yml` runs both boards at 17:00 UTC on weekdays,
builds `site/`, and deploys to GitHub Pages. AIM is `continue-on-error`.

The published pages have no Refresh button — there is no Python behind static
hosting — so `build_site.py` replaces it with the rebuild schedule. Pages are
`noindex, nofollow` plus a blanket `robots.txt`: unlisted, not secret.

**CI runs are more exposed to Yahoo's rate limit than local ones**, because
shared runner IPs are already busy. The workflow retries once after a pause
for this reason, and the refusal-to-screen guard means a throttled run fails
the job rather than publishing a thin universe.

## Adjustable thresholds

Identical to the Korea build: the dashboard re-evaluates BOTH screens in the
browser, every input travels with each row, and `evaluate()` mirrors
`apply_roe_gate` and `apply_absolute_screen` including the rule that a missing
value fails a test it is subject to. Settings persist per board in
localStorage; Reset returns to the published run.

Peer medians and sub-floor market caps remain NOT adjustable, for the same
reasons as Korea — they cannot be recomputed from the shipped rows.

If the client-side verdict at default settings disagrees with the Python
funnel, that is a real bug.

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
