"""
HTML Dashboard Generator — Session 3
Creates the daily decision table with:
- Collapsible warning banner (COLLAPSED by default, click to expand)
- Collapsible backtest results section (COLLAPSED by default, click to expand)
- Per-ticker hit rate buckets (Strong / Medium / Weak / Insufficient)
- Post-earnings anchor suppression flags
- Catalyst-aware date display
- Currency-aware price display (€ for .MI tickers, $ for US, € for ASML)
- Fallback signals (80th percentile high-conviction alternative)
- Trading 212 ticker hyperlinks (opens in new tab)

Sort: BUY first (shallowest dip = strongest buy), then WAIT (deepest dip first).
"""

from datetime import datetime, timedelta
from pytz import timezone
import os
from config import OUTPUT_DIR, OUTPUT_FILE, PERCENTILE_TARGET


# Per-ticker bucket display thresholds
# Rationale: tickers with <3 testable signals cannot support ranked accuracy
# claims — small samples (n<3) are within margin of error and would mislead.
# Tickers with >=3 signals are bucketed by hit rate into Strong/Medium/Weak.
MIN_TICKER_SAMPLE = 3


def get_currency_symbol(ticker, portfolio_data=None):
    """Return € for European stocks and ASML (displayed in EUR), £ for UK, $ otherwise."""
    if ticker.endswith('.MI') or ticker == 'ASML':  # Session 3: ASML displays in EUR
        return '€'
    elif ticker.endswith('.L'):
        return '£'
    else:
        return '$'


def get_trading212_url(ticker):
    """
    Build Trading 212 URL for a ticker.
    Pattern: https://www.trading212.com/trading-instruments/invest/{TICKER}.{EXCHANGE}
    Default suffix is .US for US-listed equities.
    Add new exchange mappings here when adding non-US tickers.
    """
    # Exchange suffix mapping for non-US tickers
    # Pattern: ticker as listed in config → Trading 212 path suffix
    SUFFIX_MAP = {
        'LDO.MI': 'LDO.IT',     # Milan
        'IGLN.L': 'IGLN.GB',    # London — iShares Physical Gold ETC
        'ASML':   'ASML.NL',    # Amsterdam-listed (we model USD ADR; T212 routes EU)
        # Add more mappings here as portfolio expands:
        # 'BARC.L': 'BARC.GB',
        # 'FMNB.DE': 'FMNB.DE',
    }
    base = "https://www.trading212.com/trading-instruments/invest"
    suffix = SUFFIX_MAP.get(ticker, f"{ticker}.US")
    return f"{base}/{suffix}"


def _bucket_tickers(by_ticker):
    """
    Bucket tickers by hit rate accuracy.

    Returns: (strong, medium, weak, insufficient) — four lists of formatted labels.
    Rationale: small samples (n<MIN_TICKER_SAMPLE) cannot support ranked accuracy
    claims. Tickers with >=MIN_TICKER_SAMPLE signals are grouped by hit rate.
    Within buckets: sorted by hit rate desc, then sample size desc.
    """
    strong, medium, weak, insufficient = [], [], [], []
    for t, stats in by_ticker.items():
        n = stats.get('signals', 0)
        if n < MIN_TICKER_SAMPLE:
            insufficient.append(t)
            continue
        hr = stats.get('hit_rate', 0)
        label = f"{t} {hr:.0%} ({stats['hits']}/{n})"
        if hr >= 0.75:
            strong.append((hr, n, label))
        elif hr >= 0.50:
            medium.append((hr, n, label))
        else:
            weak.append((hr, n, label))

    # Sort each ranked bucket: hit rate desc, then sample size desc
    strong.sort(key=lambda x: (-x[0], -x[1]))
    medium.sort(key=lambda x: (-x[0], -x[1]))
    weak.sort(key=lambda x: (-x[0], -x[1]))
    insufficient.sort()

    return (
        [x[2] for x in strong],
        [x[2] for x in medium],
        [x[2] for x in weak],
        insufficient,
    )


def generate_html(execution_data, macro_regime, vix, portfolio_data,
                  warnings=None, backtest_results=None):
    if warnings is None:
        warnings = []

    # Get current time in London timezone (BST/GMT)
    london_tz = timezone('Europe/London')
    now_london = datetime.now(london_tz)
    
    run_time = now_london.strftime("%b %d, %Y %I:%M %p %Z")  # %Z shows BST or GMT
    end_date = (now_london + timedelta(days=60)).strftime("%b %d, %Y")

    buy_tickers = [t for t, d in execution_data.items() if d['signal'] == 'BUY']
    wait_tickers = [t for t, d in execution_data.items() if d['signal'] == 'WAIT']

    regime_display = {
        'risk_on': 'RISK-ON',
        'neutral': 'NEUTRAL',
        'risk_off': 'RISK-OFF'
    }

    # §Session 2: Warning banner — COLLAPSED by default (click to expand)
    warning_html = ""
    if warnings:
        warning_items = "\n".join(f"<li>{w}</li>" for w in warnings)
        warning_html = f"""
        <div class="warnings">
            <details>
                <summary>⚠️ DATA QUALITY WARNINGS ({len(warnings)}) — click to expand</summary>
                <ul>{warning_items}</ul>
            </details>
        </div>
        """

    # §Session 2: Backtest results section — COLLAPSED by default (click to expand)
    backtest_html = ""
    if backtest_results:
        if backtest_results.get('status') == 'insufficient_data':
            days_have = backtest_results.get('days_available', 0)
            days_need = backtest_results.get('days_needed', 14)
            msg = backtest_results.get('message', f'need {days_need - days_have} more')
            backtest_html = f"""
            <div class="backtest">
                <details>
                    <summary>📊 BACKTEST — Collecting data: {days_have}/{days_need} days — click to expand</summary>
                    <div class="bt-body">
                        <p class="bt-desc-block">Tracks whether past WAIT signals correctly predicted actual dips.</p>
                        <p class="backtest-pending">Collecting data: {days_have}/{days_need} days ({msg})</p>
                    </div>
                </details>
            </div>
            """
        elif backtest_results.get('status') == 'complete':
            hit_rate = backtest_results.get('hit_rate', 0)
            total = backtest_results.get('total_wait_signals', 0)
            hits = backtest_results.get('hits', 0)
            avg_error = backtest_results.get('avg_error', 0)
            roi_adv = backtest_results.get('avg_roi_advantage', 0)
            calibration = backtest_results.get('calibration', '')
            recommendation = backtest_results.get('recommendation', '')

            cal_class = ''
            if calibration == 'well_calibrated':
                cal_class = 'cal-good'
            elif calibration in ('overconfident', 'underconfident'):
                cal_class = 'cal-warn'

            # Per-ticker bucket display (Strong / Medium / Weak / Insufficient)
            by_ticker = backtest_results.get('by_ticker', {})
            strong, medium, weak, insufficient = _bucket_tickers(by_ticker)

            strong_html = ' · '.join(strong) if strong else '<span class="bt-none">none</span>'
            medium_html = ' · '.join(medium) if medium else '<span class="bt-none">none</span>'
            weak_html = ' · '.join(weak) if weak else '<span class="bt-none">none</span>'
            insuff_html = ', '.join(insufficient) if insufficient else 'none'

            backtest_html = f"""
            <div class="backtest">
                <details>
                    <summary>📊 BACKTEST — Hit rate {hit_rate:.0%} ({hits}/{total}) — click to expand</summary>
                    <div class="bt-body">
                        <p class="bt-desc-block">Tracks whether past WAIT signals correctly predicted actual dips.</p>
                        <div class="backtest-stats">
                            <div class="bt-stat">
                                <span class="bt-value">{hit_rate:.0%}</span>
                                <span class="bt-label">Hit Rate ({hits}/{total})</span>
                            </div>
                            <div class="bt-stat">
                                <span class="bt-value">{avg_error:+.1%}</span>
                                <span class="bt-label">Avg Error</span>
                            </div>
                            <div class="bt-stat">
                                <span class="bt-value">{roi_adv:+.1%}</span>
                                <span class="bt-label">ROI vs Naive</span>
                            </div>
                        </div>
                        <p class="bt-calibration {cal_class}">{recommendation}</p>
                        <div class="bt-tickers">
                            <p class="bt-tickers-header">Hit rate by ticker <span class="bt-note">— minimum {MIN_TICKER_SAMPLE} signals required for ranking</span></p>
                            <div class="bt-bucket"><span class="bt-tag bt-tag-strong">STRONG ≥75%</span> {strong_html}</div>
                            <div class="bt-bucket"><span class="bt-tag bt-tag-medium">MEDIUM 50-74%</span> {medium_html}</div>
                            <div class="bt-bucket"><span class="bt-tag bt-tag-weak">WEAK &lt;50%</span> {weak_html}</div>
                            <div class="bt-bucket bt-bucket-insuff"><span class="bt-tag bt-tag-insuff">INSUFFICIENT &lt;{MIN_TICKER_SAMPLE} signals</span> {insuff_html}</div>
                        </div>
                    </div>
                </details>
            </div>
            """

    # Sort: BUY first (smallest dip = strongest buy),
    # then WAIT (deepest dip first = most rewarding wait)
    sorted_tickers = sorted(execution_data.keys(), key=lambda t: (
        0 if execution_data[t]['signal'] == 'BUY' else 1,
        execution_data[t].get('dip_pct', 0) if execution_data[t]['signal'] == 'BUY'
            else -execution_data[t].get('dip_pct', 0),
    ))

    # Build table rows
    table_rows = ""
    for ticker in sorted_tickers:
        data = execution_data[ticker]
        p_data = portfolio_data.get(ticker, {})
        earnings = p_data.get('earnings_date', '')
        earnings_display = f"{earnings} ⚡" if earnings else "—"

        signal_icon = "🟢" if data['signal'] == 'BUY' else "⏳"
        ccy = get_currency_symbol(ticker, portfolio_data)

        # Per-stock warning indicator
        stock_warn = ""
        if data.get('_extreme_dip'):
            stock_warn = '<span class="stock-warn" title="Extreme dip predicted">⚠️</span>'
        # §Session 2: Post-earnings anchor suppression flag
        if data.get('_anchor_suppressed'):
            stock_warn += '<span class="stock-warn" title="Post-earnings: anchor suppressed">🔇</span>'

        # Session 3: Display price (EUR for ASML, native currency for others)
        if ticker == 'ASML' and p_data.get('_price_eur'):
            display_price = p_data['_price_eur']
        else:
            display_price = data['current_price']

        # Dip percentage
        dip_pct = data.get('dip_pct', 0)
        dip_display = f"{dip_pct*100:.1f}%"

        # Target display with correct currency
        if data.get('_no_dip') or data.get('reason_code') == 'no_dip':
            target_display = "No dip expected in window"
        elif data.get('reason_code') == 'immaterial':
            target_display = f"⬇️ {ccy}{data['target_price']:.2f} · {data['date_range']} ({dip_display} — immaterial)"
        else:
            target_display = f"⬇️ {ccy}{data['target_price']:.2f} · {data['date_range']} ({dip_display})"

        # Session 5: Rally line (⬆️ expected rally target, 60% conviction)
        rally_display = ""
        rally_price = data.get('rally_price')
        rally_pct = data.get('rally_pct', 0)
        rally_date_range = data.get('rally_date_range', '')
        if rally_price and rally_pct > 0.01:  # Only show if >1% rally expected
            rally_display = f"⬆️ {ccy}{rally_price:.2f} · {rally_date_range} (+{rally_pct*100:.1f}% rally, 60% conviction)"

        # 🔮 Analyst consensus line
        consensus_display = ""
        ac = data.get('analyst_consensus')
        if ac and ac.get('median'):
            upside = ac['upside_pct']
            upside_str = f"+{upside:.1f}%" if upside >= 0 else f"{upside:.1f}%"
            trend_str = f"; Trend: {ac['trend']}" if ac.get('trend') else ""
            consensus_display = f"🔮 Analyst consensus (12-mo): {ccy}{ac['median']:.2f} median ({upside_str}){trend_str}"
        ai_badge = ""
        ai_result = p_data.get('ai_result', {})
        if isinstance(ai_result, dict) and ai_result.get('narrative'):
            vol_regime = ai_result.get('vol_regime', '')
            thesis = ai_result.get('thesis_status', '')
            if vol_regime:
                regime_icon = '🟢' if vol_regime == 'LOW' else '🔴' if vol_regime == 'HIGH' else '🟡'
                ai_badge = f'<div style="font-size: 12px; color: #aaa; margin-top: 4px;">✨ AI: Vol {vol_regime} {regime_icon} — {ai_result["narrative"][:60]}</div>'
            elif thesis:
                thesis_icon = '🟢' if thesis == 'INTACT' else '🔴' if thesis == 'CRITICAL' else '🟡'
                ai_badge = f'<div style="font-size: 12px; color: #aaa; margin-top: 4px;">✨ AI: Thesis {thesis} {thesis_icon} — {ai_result["narrative"][:60]}</div>'

        # RSI badge
        rsi_val = p_data.get('rsi')
        rsi_display = ""
        if rsi_val is not None:
            rsi_class = ""
            if rsi_val > 70:
                rsi_class = "rsi-high"
            elif rsi_val < 30:
                rsi_class = "rsi-low"
            rsi_display = f'<span class="rsi {rsi_class}">RSI {rsi_val:.0f}</span>'

        # Conviction display (fixed at PERCENTILE_TARGET%)
        conviction_display = f" Dip conviction: {PERCENTILE_TARGET}%"

        # Session 3: Fallback signal rendering
        fallback_html = ""
        if data.get('fallback'):
            fb = data['fallback']
            fb_action = 'BUY NOW' if fb['signal'] == 'BUY' else f"BUY at {ccy}{fb['price']:.2f}"
            fallback_html = f'''
                <div style="font-size: 13px; color: #888; margin-top: 8px; padding-left: 16px; border-left: 2px solid #444;">
                    └─ Fallback: {fb_action} ({fb['dip_pct']*100:.1f}% dip, {fb['confidence']*100:.0f}% conviction) {fb['date_range']}
                </div>
            '''

        # Trading 212 hyperlink for ticker (opens in new tab)
        t212_url = get_trading212_url(ticker)

        table_rows += f"""
        <tr>
            <td class="ticker"><a href="{t212_url}" target="_blank" rel="noopener noreferrer" class="ticker-link" title="Open {ticker} on Trading 212">{ticker}</a> {stock_warn}</td>
            <td>
                <div class="signal-row">
                    <span class="signal-icon">{signal_icon}</span>
                    <span class="signal-text">{data['signal']}</span>
                    {rsi_display}
                </div>
                <div class="price-row">{ccy}{display_price:.2f} (today)</div>
                <div class="target-row">{target_display}</div>
                <div class="target-row" style="color: #4ade80;">{rally_display}</div>
                <div class="confidence-row">{conviction_display}</div>
                <div class="oneliner">{data['one_liner']}</div>
                {fallback_html}
                {ai_badge}
                <div class="consensus-row">{consensus_display}</div>
            </td>
            <td class="earnings">{earnings_display}</td>
        </tr>
        """

    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
    <title>SGC Dip Engine</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0e1a; color: #e8eaed; padding: 20px; line-height: 1.6;
        }}

        .container {{ max-width: 1400px; margin: 0 auto; }}

        .header {{
            text-align: center; margin-bottom: 30px; padding: 20px;
            background: linear-gradient(135deg, #1a1f35 0%, #2d3548 100%);
            border-radius: 12px;
        }}
        .header h1 {{ font-size: 2.5em; margin-bottom: 10px; color: #4a9eff; }}
        .header .meta {{ font-size: 0.9em; color: #a0a5b0; }}

        .regime {{
            display: inline-block; padding: 4px 12px;
            background: #2d3548; border-radius: 6px;
            margin-left: 10px; font-weight: 600;
        }}

        .warnings {{
            background: #2a1a0a; border: 1px solid #ff9800; border-radius: 12px;
            padding: 15px 20px; margin-bottom: 20px;
        }}
        .warnings details summary {{
            color: #ff9800; font-weight: 600; font-size: 1em;
            cursor: pointer; list-style: none; padding: 2px 0;
        }}
        .warnings details summary::-webkit-details-marker {{ display: none; }}
        .warnings details summary::before {{
            content: '▶ '; font-size: 0.8em;
        }}
        .warnings details[open] summary::before {{
            content: '▼ ';
        }}
        .warnings ul {{ list-style: none; padding: 10px 0 0 0; }}
        .warnings li {{
            color: #ffb74d; font-size: 0.85em; padding: 3px 0;
            border-bottom: 1px solid #3a2a1a;
        }}
        .warnings li:last-child {{ border-bottom: none; }}

        .backtest {{
            background: #1a2535; border: 1px solid #4a9eff; border-radius: 12px;
            padding: 15px 20px; margin-bottom: 20px;
        }}
        .backtest details summary {{
            color: #4a9eff; font-weight: 600; font-size: 1em;
            cursor: pointer; list-style: none; padding: 2px 0;
        }}
        .backtest details summary::-webkit-details-marker {{ display: none; }}
        .backtest details summary::before {{
            content: '▶ '; font-size: 0.8em;
        }}
        .backtest details[open] summary::before {{
            content: '▼ ';
        }}
        .bt-body {{ padding-top: 12px; }}
        .bt-desc-block {{ color: #a0a5b0; font-size: 0.85em; margin-bottom: 10px; font-style: italic; }}
        .bt-desc {{ color: #a0a5b0; font-weight: 400; font-size: 0.85em; }}
        .backtest-stats {{ display: flex; gap: 30px; margin-bottom: 10px; }}
        .bt-stat {{ text-align: center; }}
        .bt-value {{ display: block; font-size: 1.5em; font-weight: 700; color: #e8eaed; }}
        .bt-label {{ display: block; font-size: 0.8em; color: #a0a5b0; }}
        .bt-calibration {{ font-size: 0.9em; color: #a0a5b0; font-style: italic; }}
        .cal-good {{ color: #66bb6a; }}
        .cal-warn {{ color: #ffa726; }}
        .backtest-pending {{ color: #a0a5b0; font-size: 0.9em; }}

        .bt-tickers {{ margin-top: 16px; padding-top: 12px; border-top: 1px solid #2d3548; }}
        .bt-tickers-header {{ font-size: 0.85em; color: #a0a5b0; margin-bottom: 10px; font-weight: 600; }}
        .bt-note {{ color: #707580; font-weight: 400; font-style: italic; }}
        .bt-bucket {{ font-size: 0.85em; color: #b0b5c0; padding: 6px 0; line-height: 1.7; }}
        .bt-bucket-insuff {{ color: #707580; }}
        .bt-tag {{
            display: inline-block; padding: 2px 8px; border-radius: 4px;
            font-size: 0.75em; font-weight: 700; margin-right: 8px;
            letter-spacing: 0.5px;
        }}
        .bt-tag-strong {{ background: #1a3a1a; color: #66bb6a; }}
        .bt-tag-medium {{ background: #3a2d1a; color: #ffa726; }}
        .bt-tag-weak {{ background: #4a1a1a; color: #ff6b6b; }}
        .bt-tag-insuff {{ background: #2d3548; color: #909598; }}
        .bt-none {{ color: #707580; font-style: italic; }}

        .deployment {{
            background: #1a1f35; padding: 20px; border-radius: 12px;
            margin-bottom: 30px; border-left: 4px solid #4a9eff;
        }}
        .deployment h2 {{ font-size: 1.3em; margin-bottom: 15px; color: #4a9eff; }}
        .deployment-row {{ display: flex; gap: 40px; margin-bottom: 10px; }}
        .deployment-row strong {{ color: #4a9eff; min-width: 150px; }}
        .buy-list {{ color: #66bb6a; }}
        .wait-list {{ color: #ffa726; }}

        table {{
            width: 100%; border-collapse: collapse;
            background: #1a1f35; border-radius: 12px; overflow: hidden;
        }}
        thead {{ background: #2d3548; }}
        th {{
            padding: 15px; text-align: left; font-weight: 600;
            color: #4a9eff; border-bottom: 2px solid #3a4556;
        }}
        tr {{ border-bottom: 1px solid #2d3548; }}
        tr:last-child {{ border-bottom: none; }}
        td {{ padding: 20px 15px; vertical-align: top; }}

        .ticker {{ font-weight: 700; font-size: 1.1em; color: #4a9eff; }}
        .ticker-link {{
            color: inherit; text-decoration: none;
            border-bottom: 1px dashed #4a9eff66;
            transition: color 0.15s, border-color 0.15s;
        }}
        .ticker-link:hover {{
            color: #6db5ff; border-bottom-color: #6db5ff;
        }}
        .signal-row {{ display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }}
        .signal-icon {{ font-size: 1.3em; }}
        .signal-text {{ font-weight: 700; font-size: 1.1em; }}
        .price-row {{ color: #e8eaed; margin-bottom: 6px; }}
        .target-row {{ color: #ffa726; margin-bottom: 6px; font-weight: 500; }}
        .confidence-row {{ color: #a0a5b0; font-size: 0.9em; margin-bottom: 8px; }}
        .consensus-row {{ color: #b0b5c0; font-size: 0.85em; margin-top: 6px; }}
        .oneliner {{
            background: #2d3548; padding: 10px; border-radius: 6px;
            font-size: 0.9em; color: #b0b5c0; margin-top: 8px;
        }}
        .earnings {{ text-align: center; font-weight: 500; }}

        .stock-warn {{ margin-left: 5px; }}
        .rsi {{
            font-size: 0.75em; padding: 2px 8px; border-radius: 4px;
            background: #2d3548; color: #a0a5b0; margin-left: 8px;
        }}
        .rsi-high {{ background: #4a1a1a; color: #ff6b6b; }}
        .rsi-low {{ background: #1a3a1a; color: #66bb6a; }}

        .run-btn {{
            display: inline-block; padding: 6px 18px; background: #4a9eff;
            color: #0a0e1a; text-decoration: none; border-radius: 6px;
            font-weight: 700; font-size: 0.85em;
        }}
        .run-btn:hover {{ background: #6bb3ff; }}

        @media (max-width: 768px) {{
            .deployment-row {{ flex-direction: column; gap: 10px; }}
            .backtest-stats {{ flex-direction: column; gap: 10px; }}
            .bt-bucket {{ font-size: 0.8em; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>SGC DIP ENGINE</h1>
            <div class="meta">
                Last Run: {run_time}
                <span class="regime">Market: {regime_display.get(macro_regime, 'UNKNOWN')} (VIX {vix:.1f})</span>
            </div>
            <div class="meta" style="margin-top: 8px;">
                Window: 60 days remaining (ends {end_date})
            </div>
            <div style="margin-top: 12px;">
                <a href="https://github.com/smartbalanced-sgc/sgc-dip-engine/actions" target="_blank" class="run-btn">▶ Run Now</a>
            </div>
        </div>

        {warning_html}

        {backtest_html}

        <div class="deployment">
            <h2>TODAY'S DEPLOYMENT ({now_london.strftime("%b %d")})</h2>
            <div class="deployment-row">
                <strong>BUY TODAY ({len(buy_tickers)}):</strong>
                <span class="buy-list">{', '.join(buy_tickers) if buy_tickers else 'None'}</span>
            </div>
            <div class="deployment-row">
                <strong>WAIT FOR DIP ({len(wait_tickers)}):</strong>
                <span class="wait-list">{', '.join(wait_tickers) if wait_tickers else 'None'}</span>
            </div>
        </div>

        <table>
            <thead>
                <tr>
                    <th>Stock</th>
                    <th>Signal & Target (today + 60 days)</th>
                    <th>Earnings</th>
                </tr>
            </thead>
            <tbody>
                {table_rows}
            </tbody>
        </table>
    </div>
</body>
</html>
    """

    return html


def save_html(html_content, output_dir=OUTPUT_DIR, filename=OUTPUT_FILE):
    """Save HTML to docs/ for GitHub Pages."""
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"✅ Dashboard saved to {filepath}")
