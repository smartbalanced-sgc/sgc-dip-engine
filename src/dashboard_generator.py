"""
HTML Dashboard Generator
Creates the daily decision table with guardrail warning banner.
Sort: BUY first (shallowest dip = strongest buy), then WAIT (deepest dip first).
"""

from datetime import datetime, timedelta
import os
from config import OUTPUT_DIR, OUTPUT_FILE, PERCENTILE_TARGET


def generate_html(execution_data, macro_regime, vix, portfolio_data, warnings=None):
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

    # Warning banner
    warning_html = ""
    if warnings:
        warning_items = "\n".join(f"<li>{w}</li>" for w in warnings)
        warning_html = f"""
        <div class="warnings">
            <h3>⚠️ DATA QUALITY WARNINGS ({len(warnings)})</h3>
            <ul>{warning_items}</ul>
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

        # Per-stock warning indicator
        stock_warn = ""
        if data.get('_extreme_dip'):
            stock_warn = '<span class="stock-warn" title="Extreme dip predicted">⚠️</span>'

        # Dip percentage
        dip_pct = data.get('dip_pct', 0)
        dip_display = f"{dip_pct*100:.1f}%"

        # Target display
        if data.get('_no_dip') or data.get('reason_code') == 'no_dip':
            target_display = "No dip expected in window"
        elif data.get('reason_code') == 'immaterial':
            target_display = f"⬇️ ${data['target_price']:.2f} · {data['date_range']} ({dip_display} — immaterial)"
        else:
            target_display = f"⬇️ ${data['target_price']:.2f} · {data['date_range']} ({dip_display})"

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

        table_rows += f"""
        <tr>
            <td class="ticker">{ticker} {stock_warn}</td>
            <td>
                <div class="signal-row">
                    <span class="signal-icon">{signal_icon}</span>
                    <span class="signal-text">{data['signal']}</span>
                    {rsi_display}
                </div>
                <div class="price-row">${data['current_price']:.2f} (today)</div>
                <div class="target-row">{target_display}</div>
                <div class="confidence-row">{conviction_display}</div>
                <div class="oneliner">{data['one_liner']}</div>
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
        .warnings h3 {{ color: #ff9800; margin-bottom: 10px; font-size: 1em; }}
        .warnings ul {{ list-style: none; padding: 0; }}
        .warnings li {{
            color: #ffb74d; font-size: 0.85em; padding: 3px 0;
            border-bottom: 1px solid #3a2a1a;
        }}
        .warnings li:last-child {{ border-bottom: none; }}

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

        @media (max-width: 768px) {{
            .deployment-row {{ flex-direction: column; gap: 10px; }}
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
        </div>

        {warning_html}

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
