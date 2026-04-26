"""
Signal Archiver — SGC Dip Engine v7
Appends daily signals to CSV for backtest analysis.

Session 2 fix: Deduplicates on write — if today's signals already exist
(from a prior run today), they are replaced, not duplicated.
This prevents inflated backtest denominators from multiple daily runs.
"""

import csv
from datetime import datetime
from pathlib import Path


def archive_signals(execution_data, portfolio_data):
    """
    Write today's signals to signal_history.csv.
    If today's signals already exist, replace them (dedup).

    Args:
        execution_data: Dict of {ticker: {signal, dip_target, ...}}
        portfolio_data: Dict of {ticker: {current_price, rsi, earnings_date, ...}}

    Returns:
        int: Number of signals archived
    """
    # Find data directory
    # Skip archiving on weekends — weekend runs generate dashboard only.
    # Saturday/Sunday prices are Friday's close; archiving creates duplicate
    # signal rows with identical data, inflating the backtest denominator.
    # weekday(): Monday=0 … Friday=4, Saturday=5, Sunday=6
    if datetime.now().weekday() >= 5:
        print("📅 Weekend run — dashboard generated, backtest archive skipped.")
        return 0

    # Find data directory
    repo_root = Path(__file__).parent.parent
    data_dir = repo_root / 'data'
    data_dir.mkdir(exist_ok=True)

    csv_path = data_dir / 'signal_history.csv'
    today = datetime.now().strftime('%Y-%m-%d')

    headers = [
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
    ]

    # §Session 2: Read existing rows, remove today's entries (dedup)
    existing_rows = []
    if csv_path.exists():
        try:
            with open(csv_path, 'r', newline='') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Keep all rows that are NOT from today
                    if row.get('date') != today:
                        existing_rows.append(row)
        except Exception:
            existing_rows = []

    # Build today's rows
    today_rows = []
    for ticker, exec_data in execution_data.items():
        port_data = portfolio_data.get(ticker, {})

        # Extract values with defaults
        signal = exec_data.get('signal', 'UNKNOWN')
        current_price = port_data.get('current_price', 0.0)
        dip_target = exec_data.get('target_price', 0.0)
        dip_pct = exec_data.get('dip_pct', 0.0)
        conviction = exec_data.get('conviction', 60)
        rsi = port_data.get('rsi', 0.0)

        # Calculate days to earnings
        earnings_date = port_data.get('earnings_date')
        earnings_days = ''
        if earnings_date:
            try:
                if isinstance(earnings_date, str):
                    ed = datetime.strptime(earnings_date, '%Y-%m-%d')
                else:
                    ed = earnings_date
                earnings_days = (ed - datetime.now()).days
            except:
                earnings_days = ''

        regime = port_data.get('regime', '')
        validation_flags = '|'.join(port_data.get('_validation_flags', []))

        today_rows.append({
            'date': today,
            'ticker': ticker,
            'signal': signal,
            'current_price': f"{current_price:.2f}",
            'dip_target': f"{dip_target:.2f}",
            'dip_pct': f"{dip_pct:.4f}",
            'conviction': conviction,
            'rsi': f"{rsi:.1f}" if rsi else '',
            'earnings_days': earnings_days,
            'regime': regime,
            'validation_flags': validation_flags
        })

    # Write all rows: existing (minus today) + today's fresh rows
    all_rows = existing_rows + today_rows

    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)

    print(f"📝 Archived {len(today_rows)} signals to {csv_path} ({len(existing_rows)} prior days preserved)")
    return len(today_rows)
