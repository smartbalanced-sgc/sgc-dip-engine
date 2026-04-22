"""
HTML Dashboard Generator
Creates the daily decision table
"""

from datetime import datetime, timedelta
import os

def generate_html(execution_data, macro_regime, vix, portfolio_data):
    """
    Generate complete HTML dashboard
    
    Args:
        execution_data: dict of signals per ticker
        macro_regime: 'risk_on', 'neutral', or 'risk_off'
        vix: current VIX level
        portfolio_data: dict with earnings dates
    
    Returns: HTML string
    """
    
    run_time = datetime.now().strftime("%b %d, %Y %I:%M %p BST")
    end_date = (datetime.now() + timedelta(days=60)).strftime("%b %d, %Y")
    
    # Count BUY vs WAIT
    buy_tickers = [t for t, d in execution_data.items() if d['signal'] == 'BUY']
    wait_tickers = [t for t, d in execution_data.items() if d['signal'] == 'WAIT']
    
    # Macro regime display
    regime_display = {
        'risk_on': 'RISK-ON',
        'neutral': 'NEUTRAL',
        'risk_off': 'RISK-OFF'
    }
    
    # Build table rows
    table_rows = ""
    for ticker in execution_data.keys():
        data = execution_data[ticker]
        earnings = portfolio_data[ticker].get('earnings_date', '')
        earnings_display = f"{earnings} ⚡" if earnings else "—"
        
        signal_icon = "🟢" if data['signal'] == 'BUY' else "⏳"
        
        table_rows += f"""
        <tr>
            <td class="ticker">{ticker}</td>
            <td>
                <div class="signal-row">
                    <span class="signal-icon">{signal_icon}</span>
                    <span class="signal-text">{data['signal']}</span>
                </div>
                <div class="price-row">${data['current_price']:.2f} (today)</div>
                <div class="target-row">⬇️ ${data['target_price']:.2f} · {data['date_range']}</div>
                <div class="confidence-row">Confidence: {int(data['confidence']*100)}%</div>
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
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0e1a;
            color: #e8eaed;
            padding: 20px;
            line-height: 1.6;
        }}
        
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        
        .header {{
            text-align: center;
            margin-bottom: 30px;
            padding: 20px;
            background: linear-gradient(135deg, #1a1f35 0%, #2d3548 100%);
            border-radius: 12px;
        }}
        
        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
            color: #4a9eff;
        }}
        
        .header .meta {{
            font-size: 0.9em;
            color: #a0a5b0;
        }}
        
        .regime {{
            display: inline-block;
            padding: 4px 12px;
            background: #2d3548;
            border-radius: 6px;
            margin-left: 10px;
            font-weight: 600;
        }}
        
        .deployment {{
            background: #1a1f35;
            padding: 20px;
            border-radius: 12px;
            margin-bottom: 30px;
            border-left: 4px solid #4a9eff;
        }}
        
        .deployment h2 {{
            font-size: 1.3em;
            margin-bottom: 15px;
            color: #4a9eff;
        }}
        
        .deployment-row {{
            display: flex;
            gap: 40px;
            margin-bottom: 10px;
        }}
        
        .deployment-row strong {{
            color: #4a9eff;
            min-width: 150px;
        }}
        
        .buy-list {{
            color: #66bb6a;
        }}
        
        .wait-list {{
            color: #ffa726;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            background: #1a1f35;
            border-radius: 12px;
            overflow: hidden;
        }}
        
        thead {{
            background: #2d3548;
        }}
        
        th {{
            padding: 15px;
            text-align: left;
            font-weight: 600;
            color: #4a9eff;
            border-bottom: 2px solid #3a4556;
        }}
        
        tr {{
            border-bottom: 1px solid #2d3548;
        }}
        
        tr:last-child {{
            border-bottom: none;
        }}
        
        td {{
            padding: 20px 15px;
            vertical-align: top;
        }}
        
        .ticker {{
            font-weight: 700;
            font-size: 1.1em;
            color: #4a9eff;
        }}
        
        .signal-row {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 8px;
        }}
        
        .signal-icon {{
            font-size: 1.3em;
        }}
        
        .signal-text {{
            font-weight: 700;
            font-size: 1.1em;
        }}
        
        .price-row {{
            color: #e8eaed;
            margin-bottom: 6px;
        }}
        
        .target-row {{
            color: #ffa726;
            margin-bottom: 6px;
            font-weight: 500;
        }}
        
        .confidence-row {{
            color: #a0a5b0;
            font-size: 0.9em;
            margin-bottom: 8px;
        }}
        
        .oneliner {{
            background: #2d3548;
            padding: 10px;
            border-radius: 6px;
            font-size: 0.9em;
            color: #b0b5c0;
            font-style: italic;
            margin-top: 8px;
        }}
        
        .earnings {{
            text-align: center;
            font-weight: 500;
        }}
        
        @media (max-width: 768px) {{
            .deployment-row {{
                flex-direction: column;
                gap: 10px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>SGC DIP ENGINE</h1>
            <div class="meta">
                Last Run: {run_time} 
                <span class="regime">Market: {regime_display[macro_regime]} (VIX {vix:.1f})</span>
            </div>
            <div class="meta" style="margin-top: 8px;">
                Window: 60 days remaining (ends {end_date})
            </div>
        </div>
        
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

def save_html(html_content, output_dir="../docs", filename="index.html"):
    """
    Save HTML to file
    """
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"✅ Dashboard saved to {filepath}")
