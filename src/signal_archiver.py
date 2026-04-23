"""
Signal Archiver — SGC Dip Engine v7
Appends daily signals to CSV for backtest analysis.
"""

import csv
from datetime import datetime
from pathlib import Path


def archive_signals(execution_data, portfolio_data):
    """
    Append today's signals to signal_history.csv.
    
    Args:
        execution_data: Dict of {ticker: {signal, dip_target, ...}}
        portfolio_data: Dict of {ticker: {current_price, rsi, earnings_date, ...}}
    
    Returns:
        int: Number of signals archived
    """
    # Find data directory
    repo_root = Path(__file__).parent.parent
    data_dir = repo_root / 'data'
    data_dir.mkdir(exist_ok=True)
    
    csv_path = data_dir / 'signal_history.csv'
    
    # Check if file exists (if not, headers will be created on first write)
    file_exists = csv_path.exists()
    
    today = datetime.now().strftime('%Y-%m-%d')
    rows_written = 0
    
    with open(csv_path, 'a', newline='') as f:
        writer = csv.writer(f)
        
        # Write header if new file
        if not file_exists:
            writer.writerow([
                'date',
                'ticker',
                'signal',
                'current_price',
                'dip_target',
                'dip_pct',
                'conviction',
                'rsi',
                'earnings_days',
                'regime',
                'validation_flags'
            ])
        
        # Write signal rows
        for ticker, exec_data in execution_data.items():
            port_data = portfolio_data.get(ticker, {})
            
            # Extract values with defaults
            signal = exec_data.get('signal', 'UNKNOWN')
            current_price = port_data.get('current_price', 0.0)
            dip_target = exec_data.get('dip_target', 0.0)
            dip_pct = exec_data.get('dip_pct', 0.0)
            conviction = exec_data.get('conviction', 60)  # Default from config
            rsi = port_data.get('rsi', 0.0)
            
            # Calculate days to earnings
            earnings_date = port_data.get('earnings_date')
            if earnings_date:
                try:
                    days_to_earnings = (earnings_date - datetime.now()).days
                except:
                    days_to_earnings = ''
            else:
                days_to_earnings = ''
            
            regime = port_data.get('regime', '')
            validation_flags = '|'.join(port_data.get('_validation_flags', []))
            
            writer.writerow([
                today,
                ticker,
                signal,
                f"{current_price:.2f}",
                f"{dip_target:.2f}",
                f"{dip_pct:.4f}",
                conviction,
                f"{rsi:.1f}" if rsi else '',
                days_to_earnings,
                regime,
                validation_flags
            ])
            
            rows_written += 1
    
    print(f"📝 Archived {rows_written} signals to {csv_path}")
    return rows_written
