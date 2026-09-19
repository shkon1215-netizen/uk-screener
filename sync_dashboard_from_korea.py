"""Re-derive the UK dashboard from the Korea build's current dashboard.py.

    python sync_dashboard_from_korea.py ../Korea/dashboard.py

The UK dashboard is not a fork that drifts: it is Korea's dashboard.py with a
fixed list of UK substitutions applied (P/E and P/B labels, tidm instead of
ticker, UK boards and notes, the undefined guard in cell(), same-basis
per_now/pbr_now for the own-history screen, the reporting currency in growth
tooltips, four filed years instead of five). When Korea gains a feature, copy
its dashboard.py over and run this.

Every substitution must match exactly once. Anything that no longer matches is
printed and the exit code is 1 - Korea's text moved, and that substitution
needs updating by hand rather than being silently skipped. Afterwards, grep
the result for Korean text, KOSPI, PER/PBR and "ticker" to catch anything new
Korea added that this list does not know about yet, and re-check that the
browser's verdict matches the Python funnel at default thresholds.
"""
import io
import sys

if len(sys.argv) != 2:
    sys.exit("usage: python sync_dashboard_from_korea.py <path to Korea dashboard.py>")
SRC = sys.argv[1]
P = "dashboard.py"
s = io.open(SRC, encoding="utf-8").read()


PAIRS = [
    # ---- module docstring --------------------------------------------------
    ("Reads the CSV that main_kr.py writes", "Reads the CSV that main_uk.py writes"),
    ("re-run main_kr.py to refresh it in place.", "re-run main_uk.py to refresh it in place."),

    # ---- funnel + table ----------------------------------------------------
    ('("after_korea_filters", "After share-class hygiene", None),',
     '("after_uk_filters", "After share-class hygiene", None),'),
    ('("ticker", "Code", "l"), ("name", "Name", "l"), ("industry", "업종 industry", "l"),',
     '("tidm", "Code", "l"), ("name", "Name", "l"), ("industry", "Industry", "l"),'),
    ('("mcap_musd", "Cap $m", ""), ("trailing_pe", "PER", ""), ("price_to_book", "PBR", ""),',
     '("mcap_musd", "Cap $m", ""), ("trailing_pe", "P/E", ""), ("price_to_book", "P/B", ""),'),
    ('("hist_avg_disc", "vs own 5y", ""),', '("hist_avg_disc", "vs own history", ""),'),
    ('("abs_pbr_ok", "PBR below {abs_max_pbr:g}"),', '("abs_pbr_ok", "P/B below {abs_max_pbr:g}"),'),
    ('("abs_pbr_vs_roe_ok", "PBR below fair value (ROE ÷ {abs_cost_of_equity_pct:g}% CoE)"),',
     '("abs_pbr_vs_roe_ok", "P/B below fair value (ROE ÷ {abs_cost_of_equity_pct:g}% CoE)"),'),

    # ---- row payload -------------------------------------------------------
    ('            "ticker": str(r.get("ticker", "")),\n',
     '            # The TIDM, not the Yahoo symbol: SHEL is what a UK reader looks\n'
     '            # up, SHEL.L is a vendor detail. This key must stay in step with\n'
     '            # TABLE_COLS - see the guard in cell() for what a mismatch costs.\n'
     '            "tidm": str(r.get("tidm", "") or r.get("ticker", "")),\n'),
    ('            "evx_now": _f(r.get("evx_now")),\n',
     '            "evx_now": _f(r.get("evx_now")),\n'
     '            # Today\'s P/E and P/B on the SAME basis as the history (market\n'
     '            # value over the latest filing), which is not the basis of the\n'
     '            # trailing_pe/price_to_book columns - see add_history_now.\n'
     '            "per_now": _f(r.get("per_now")),\n'
     '            "pbr_now": _f(r.get("pbr_now")),\n'
     '            # The reporting currency the 3-year figures are in. Not the quote\n'
     '            # currency: Shell trades in pence and reports in dollars.\n'
     '            "fin_ccy": str(r.get("fin_ccy", "") or ""),\n'),
    ('            # Three-year history, oldest first, in 억원. The yearly values',
     '            # Three-year history, oldest first, in millions of the REPORTING\n'
     '            # currency (fin_ccy), not sterling. The yearly values'),
    ('            # Own five-year history. The medians and today\'s values let the',
     '            # Own filed history. The medians and today\'s values let the'),

    # ---- drop labels -------------------------------------------------------
    ('''    for key, label in [("dropped_preferred", "우선주 preferred"),
                       ("dropped_reit", "리츠 REIT"),
                       ("dropped_spac", "스팩 SPAC")]:''',
     '''    for key, label in [("dropped_aic_investment_company", "investment trusts"),
                       ("dropped_investment_trust", "trusts caught by sector"),
                       ("dropped_reit", "REITs"),
                       ("dropped_nonvoting", "non-voting lines"),
                       ("dropped_shell", "cash shells")]:'''),

    # ---- boards ------------------------------------------------------------
    ('''BOARD_FILES = {"KOSPI": "kr_dashboard.html",
               "KOSDAQ": "kq_dashboard.html",
               "BOTH": "krkq_dashboard.html"}''',
     '''BOARD_FILES = {"MAIN": "uk_dashboard.html",
               "AIM": "aim_dashboard.html",
               "BOTH": "uk_dashboard.html"}'''),
    ('''# its own. KOSPI keeps the original name - renaming a published artifact makes
# it unrecognisable to anyone who bookmarked it.
BOARD_TITLES = {"KOSDAQ": "KOSDAQ Discount Screen"}''',
     '''# its own. The Main Market keeps the original name - renaming a published
# artifact makes it unrecognisable to anyone who bookmarked it.
BOARD_TITLES = {"AIM": "AIM Discount Screen"}'''),

    # ---- FX field ----------------------------------------------------------
    ('"krw_per_usd": meta.get("krw_per_usd"),', '"usd_per_gbp": meta.get("usd_per_gbp"),'),

    # ---- titles ------------------------------------------------------------
    ('head.replace("<title>Korea Discount Screen</title>",',
     'head.replace("<title>UK Discount Screen</title>",'),
    ('body.replace("<h1>Korea Discount Screen</h1>",', 'body.replace("<h1>UK Discount Screen</h1>",'),
    ('_HEAD = """<title>Korea Discount Screen</title>', '_HEAD = """<title>UK Discount Screen</title>'),
    ('<p class="eyebrow">KRX relative valuation</p>', '<p class="eyebrow">LSE relative valuation</p>'),
    ('<h1>Korea Discount Screen</h1>', '<h1>UK Discount Screen</h1>'),

    # ---- fonts: the Korean subset is ~200KB of glyphs nothing here renders -
    ('family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans+KR:wght@300;400;500;600;700&display=swap',
     'family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap'),
    ('--sans:"IBM Plex Sans KR", system-ui, -apple-system, "Segoe UI", "Malgun Gothic", sans-serif;',
     '--sans:"IBM Plex Sans", system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;'),
    ('''/* Korean breaks at any character by default, so 현대지에프홀딩스 shatters across
   four lines in a narrow column. keep-all breaks at word boundaries instead. */''',
     '''/* Long UK company names ("International Public Partnerships") need to wrap
   at spaces, not mid-word, in a narrow column. */'''),

    # ---- commands / search -------------------------------------------------
    ('<code id="cmd">python main_kr.py</code>', '<code id="cmd">python main_uk.py</code>'),
    ('placeholder="name, code or 업종"', 'placeholder="name, code or industry"'),
    ('$("cmd").textContent = M.cmd || ("python main_kr.py --board "',
     '$("cmd").textContent = M.cmd || ("python main_uk.py --board "'),

    # ---- history panel, selector and control labels ------------------------
    ('<h2>Cheap vs its own 5 years</h2>', '<h2>Cheap vs its own history</h2>'),
    ('<span class="hint">median of filed years</span>',
     '<span class="hint">median of 4 filed years</span>'),
    ('<option value="history">Cheap vs own 5y</option>',
     '<option value="history">Cheap vs own history</option>'),
    ('<div class="tg"><label for="t_hdisc">Below own 5y by at least %</label>',
     '<div class="tg"><label for="t_hdisc">Below own history by at least %</label>'),

    # ---- threshold controls ------------------------------------------------
    ('<div class="tg"><label for="t_pbr">PBR below</label>',
     '<div class="tg"><label for="t_pbr">P/B below</label>'),
    ('<div class="tg"><label for="t_per">PER below</label>',
     '<div class="tg"><label for="t_per">P/E below</label>'),
    ('id="t_fair"> Require PBR below fair value', 'id="t_fair"> Require P/B below fair value'),
    ('id="t_carve"> Financials qualify on PBR + ROE', 'id="t_carve"> Financials qualify on P/B + ROE'),
    ('tests.push(["PBR below " + T.pbr,', 'tests.push(["P/B below " + T.pbr,'),
    ('tests.push(["PER below " + T.per,', 'tests.push(["P/E below " + T.per,'),
    ('tests.push([`PBR below fair value (ROE ÷ ${T.coe}% CoE)`,',
     'tests.push([`P/B below fair value (ROE ÷ ${T.coe}% CoE)`,'),
    ('financials qualified on PBR and ROE ', 'financials qualified on P/B and ROE '),
    ('${hist.length} vs own 5y`},', '${hist.length} vs own history`},'),

    # ---- chips -------------------------------------------------------------
    ('["Source", M.source === "naver" ? "KIND + Naver" : "KRX"],', '["Source", "FTSE indices + Yahoo"],'),
    ('["USD/KRW", M.krw_per_usd],', '["USD/GBP", M.usd_per_gbp],'),

    # ---- interpretation copy -----------------------------------------------
    ('''    <p><b>Cheap vs its own five years</b> compares today's PER, PBR and EV/EBITDA
    with the median of the company's last five filed years. It catches what the
    other two miss - a company that always traded at a premium and has just
    de-rated. Loss years drop out of the benchmark rather than dragging it, and
    fewer than three usable years means no benchmark at all. <b>One caution:</b>
    these are trailing multiples, so when earnings are surging the latest filing
    lags the price and a stock reads <i>expensive</i> against its history until
    the next filing catches up. A one-off gain does the opposite.</p>''',
     '''    <p><b>Cheap vs its own history</b> compares today's P/E, P/B and EV/EBITDA
    with the median of the company's last four filed years - four, not Korea's
    five, because that is all Yahoo holds for UK companies. It catches what the
    other two miss: a company that always traded at a premium and has just
    de-rated. Loss years drop out of the benchmark rather than dragging it, and
    fewer than three usable years means no benchmark at all.</p>
    <p><b>Today's multiples in that column are not the ones in the P/E and P/B
    columns.</b> Each past year is that year-end market value over that year's
    filed accounts, so today is measured the same way: today's market value over
    the latest filed year. The P/E column uses the last twelve months instead,
    and for Shell the two differ by a third - comparing across them would report
    the gap between two definitions as a discount. Dollar reporters are converted
    at each year-end's rate. <b>One caution:</b> when earnings are rising, the
    latest filing lags the price and a stock reads <i>expensive</i> against its
    history until the next filing catches up. A one-off gain does the opposite.</p>'''),
    ('''    own 5 years</b> asks whether it is cheap against itself. Korea needs all
    three:''',
     '''    own history</b> asks whether it is cheap against itself. The UK needs all
    three:'''),
    ('''how far below book it traded. Those names clear the absolute screen on PBR and
    ROE alone and are marked <span class="tag">pbr+roe</span>.</p>''',
     '''how far below book it traded. Those names clear the absolute screen on P/B and
    ROE alone and are marked <span class="tag">pbr+roe</span>.</p>'''),
    ('''<p><b>Low PBR with low ROE is not a discount.</b> It is a company not earning
    its cost of capital, priced accordingly. Much of what gets called the Korea
    Discount is this.''',
     '''<p><b>Low P/B with low ROE is not a discount.</b> It is a company not earning
    its cost of capital, priced accordingly. Much of what gets called the UK
    discount is this.'''),
    ('''<p><b>업종 files holding companies under 기타 금융업.</b> That pools operating
    holdcos with bank holdcos in one peer group, and suppresses EV/EBITDA for
    both — enterprise value is meaningless for a bank, but not for an operating
    company. Holdcos are tagged so you can see which rows this touches.</p>''',
     '''<p><b>Investment trusts are excluded, and that is the biggest single
    decision here.</b> Closed-end funds are a fifth of the London market by
    count and trade persistently below their own stated book — a median 8%
    discount to NAV — because that is what closed-end funds do, not because they
    are mispriced. Left in, they would fill this table on every run. The list
    comes from the AIC register, matched on ticker, so operating asset managers
    like Schroders and Jupiter stay in while Scottish Mortgage and Pershing
    Square come out.</p>'''),

    # ---- JS: history reads same-basis values -------------------------------
    ('  const cur = {per: r.trailing_pe, pbr: r.price_to_book, evx: r.evx_now};',
     '  const cur = {per: r.per_now, pbr: r.pbr_now, evx: r.evx_now};'),
    ('  const now = {per: r.trailing_pe, pbr: r.price_to_book, evx: r.evx_now};',
     '  const now = {per: r.per_now, pbr: r.pbr_now, evx: r.evx_now};'),
    ('''  const passTag = r.ev.hist ? ' <span class="tag">5y low</span>' : "";''',
     '''  const passTag = r.ev.hist ? ' <span class="tag">hist low</span>' : "";'''),
    # Korea ships five history slots and always fills them. Yahoo has four
    # filed years for UK names, so the fifth slot is null with no year label
    # and rendered as "? —" at the end of every tooltip. Show only the years
    # that exist.
    ('    const ser = (r.h_ser && r.h_ser[k] || []).map((v, i) =>',
     '    const ser = (r.h_ser && r.h_ser[k] || []).slice(0, yrs.length).map((v, i) =>'),
    ('  // Own five-year history. Mirrors korea_filters.apply_history_screen: the',
     '  // Own filed history. Mirrors uk_filters.apply_history_screen: the'),
    ('/* Today\'s value against the five-year median, per metric.',
     '/* Today\'s value against the filed-year median, per metric.'),
    ('/* Average discount to the company\'s own five-year median, across every metric',
     '/* Average discount to the company\'s own filed-year median, across every metric'),

    # ---- JS: growth tooltip names the reporting currency -------------------
    ('  const tip = GROWTH_LABEL[key] + " (억원)\\\\n"',
     '  const tip = GROWTH_LABEL[key] + " (" + (r.fin_ccy || "reporting ccy") + " m)\\\\n"'),

    # ---- JS: tidm, and the guard that stops one bad key blanking the table --
    ('''function cell(r, k) {
  const v = r[k];
  if (k === "ticker") return `<span style="color:var(--ink-3)">${esc(v)}</span>`;''',
     '''function cell(r, k) {
  const v = r[k];
  /* A key present in TABLE_COLS but absent from the row payload arrives as
     undefined, which is not null and so slipped past the guard below and hit
     .toFixed() - one renamed column threw on the first row and left the whole
     table empty with no visible error. Missing reads as missing. Computed
     columns (growth, history) read other fields and are exempt. */
  if (v === undefined && !(k in GROWTH) && k !== "hist_avg_disc"
      && k !== "screen" && k !== "name") return '<span class="na">—</span>';
  if (k === "tidm") return `<span style="color:var(--ink-3)">${esc(v)}</span>`;'''),
    ('r.name.toLowerCase().includes(q) || r.ticker.includes(q)',
     'r.name.toLowerCase().includes(q) || (r.tidm || "").toLowerCase().includes(q)'),

    # ---- storage key and comments ------------------------------------------
    ('const STORE = "kr-thresholds-" + (M.board || "x");',
     'const STORE = "uk-thresholds-" + (M.board || "x");'),
    ('/* One row against the current thresholds. Mirrors korea_filters.apply_roe_gate',
     '/* One row against the current thresholds. Mirrors uk_filters.apply_roe_gate'),
    ('''   Only serve.py can actually re-run the screen: it shells out to main_kr.py,
   which scrapes KIND and Naver. A page opened straight off disk, or published''',
     '''   Only serve.py can actually re-run the screen: it shells out to main_uk.py,
   which scrapes the index tables and Yahoo. A page opened straight off disk,
   or published'''),
]

# Labels that appear more than once, replaced everywhere.
ALL = [
    ('const lab = {per: "PER", pbr: "PBR", evx: "EV/EBITDA"};',
     'const lab = {per: "P/E", pbr: "P/B", evx: "EV/EBITDA"};'),
]

missed = []
for old, new in PAIRS:
    if old in s:
        s = s.replace(old, new, 1)
    else:
        missed.append(old[:72])
for old, new in ALL:
    n = s.count(old)
    if not n:
        missed.append(old[:72])
    s = s.replace(old, new)

io.open(P, "w", encoding="utf-8").write(s)
print(f"applied {len(PAIRS) + len(ALL) - len(missed)}/{len(PAIRS) + len(ALL)}")
for m in missed:
    print("  MISS:", m)
sys.exit(1 if missed else 0)
