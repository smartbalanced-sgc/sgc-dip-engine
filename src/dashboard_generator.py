"""
HTML Dashboard Generator — Session 3
Creates the daily decision table with:
- Collapsible warning banner (COLLAPSED by default, click to expand)
- Backtest results section with description
- Post-earnings anchor suppression flags
- Catalyst-aware date display
- Currency-aware price display (€ for .MI tickers, $ for US, € for ASML)
- Fallback signals (80th percentile high-conviction alternative)

Sort: BUY first (shallowest dip = strongest buy), then WAIT (deepest dip first).
"""

from datetime import datetime, timedelta
import os
from config import OUTPUT_DIR, OUTPUT_FILE, PERCENTILE_TARGET


def get_currency_symbol(ticker, portfolio_data=None):
    """Return € for European stocks and ASML (displayed in EUR), £ for UK, $ otherwise."""
    if ticker.endswith('.MI') or ticker == 'ASML':  # Session 3: ASML displays in EUR
        return '€'
    elif ticker.endswith('.L'):
        return '£'
    else:
        return '$'


def generate_html(execution_data, macro_regime, vix, portfolio_data,
                  warnings=None, backtest_results=None):
    if warnings is None:
        warnings = []

    run_time = datetime.now().strftime("%b %d, %Y %I:%M %p BST")
    end_date = (datetime.now() + timedelta(days=60)).strftime("%b %d, %Y")

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

    # §Session 2: Backtest results section with description
    backtest_html = ""
    if backtest_results:
        if backtest_results.get('status') == 'insufficient_data':
            days_have = backtest_results.get('days_available', 0)
            days_need = backtest_results.get('days_needed', 14)
            backtest_html = f"""
            <div class="backtest">
                <h3>📊 Backtest <span class="bt-desc">— Tracks whether past WAIT signals correctly predicted actual dips</span></h3>
                <p class="backtest-pending">Collecting data: {days_have}/{days_need} days ({backtest_results.get('message', f'need {days_need - days_have} more')})</p>
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

            backtest_html = f"""
            <div class="backtest">
                <h3>📊 Backtest <span class="bt-desc">— Tracks whether past WAIT signals correctly predicted actual dips</span></h3>
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
        conviction_display = f"Conviction: {PERCENTILE_TARGET}%"

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

        table_rows += f"""
        <tr>
            <td class="ticker">{ticker} {stock_warn}</td>
            <td>
                <div class="signal-row">
                    <span class="signal-icon">{signal_icon}</span>
                    <span class="signal-text">{data['signal']}</span>
                    {rsi_display}
                </div>
                <div class="price-row">{ccy}{display_price:.2f} (today)</div>
                <div class="target-row">{target_display}</div>
                <div class="confidence-row">{conviction_display}</div>
                <div class="oneliner">{data['one_liner']}</div>
                {fallback_html}
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
        .backtest h3 {{ color: #4a9eff; margin-bottom: 12px; font-size: 1em; }}
        .bt-desc {{ color: #a0a5b0; font-weight: 400; font-size: 0.85em; }}
        .backtest-stats {{ display: flex; gap: 30px; margin-bottom: 10px; }}
        .bt-stat {{ text-align: center; }}
        .bt-value {{ display: block; font-size: 1.5em; font-weight: 700; color: #e8eaed; }}
        .bt-label {{ display: block; font-size: 0.8em; color: #a0a5b0; }}
        .bt-calibration {{ font-size: 0.9em; color: #a0a5b0; font-style: italic; }}
        .cal-good {{ color: #66bb6a; }}
        .cal-warn {{ color: #ffa726; }}
        .backtest-pending {{ color: #a0a5b0; font-size: 0.9em; }}

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
        .signal-row {{ display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }}
        .signal-icon {{ font-size: 1.3em; }}
        .signal-text {{ font-weight: 700; font-size: 1.1em; }}
        .price-row {{ color: #e8eaed; margin-bottom: 6px; }}
        .target-row {{ color: #ffa726; margin-bottom: 6px; font-weight: 500; }}
        .confidence-row {{ color: #a0a5b0; font-size: 0.9em; margin-bottom: 8px; }}
        .oneliner {{
            background: #2d3548; padding: 10px; border-radius: 6px;
            font-size: 0.9em; color: #b0b5c0; font-style: italic; margin-top: 8px;
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
            <h2>TODAY'S DEPLOYMENT ({datetime.now().strftime("%b %d")})</h2>
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
                    <th>Signal & Target</th>
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
