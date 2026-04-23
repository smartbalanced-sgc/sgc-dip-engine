"""
Main Orchestrator — SGC Dip Engine v7
Pipeline with Phase 2 enrichment:
  Data → GATE 1 → Regimes → GATE 2 → Sentiment → Correlation → MC → GATE 3 → Signals → GATE 4 → Dashboard

Phase 2 change: Sentiment runs BEFORE MC so Claude API scores
feed into the simulation as drift modifiers.
"""

import sys
from datetime import datetime

from config import PORTFOLIO, MIN_VALID_STOCKS
from data_fetcher import fetch_portfolio_data
from garch_model import calculate_forward_volatility
from hmm_regime import detect_regime_simple, get_regime_adjustments
from macro_regime import fetch_macro_indicators, classify_macro_regime, get_macro_adjustments
from correlation import build_correlation_matrix
from monte_carlo import simulate_portfolio
from sentiment import analyze_stock_sentiment
from execution_logic import process_execution_signals
from dashboard_generator import generate_html, save_html
from signal_archiver import archive_signals
from validators import (
    validate_input_data,
    validate_volatility,
    validate_anchor,
    validate_correlation_matrix,
    validate_simulation_results,
    validate_signals_portfolio
)


def main():
    print("=" * 60)
    print("SGC DIP ENGINE v6 - Starting Run")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    all_warnings = []

    # =========================================================
    # STEP 1: Fetch all data (15 FMP endpoints + yfinance + macro)
    # =========================================================
    print("\n📊 STEP 1: Fetching portfolio data...")
    portfolio_data, macro_events = fetch_portfolio_data()

    # ----- GATE 1: Input data validation -----
    print("\n🔒 GATE 1: Validating input data...")
    portfolio_data, gate1_warnings = validate_input_data(portfolio_data)
    all_warnings.extend(gate1_warnings)
    for w in gate1_warnings:
        print(f"   {w}")

    valid_stocks = {t: d for t, d in portfolio_data.items() if not d.get('_skip')}
    if len(valid_stocks) < MIN_VALID_STOCKS:
        print(f"\n❌ Only {len(valid_stocks)} valid stocks — below minimum {MIN_VALID_STOCKS}")
        html = generate_html({}, 'neutral', 0, portfolio_data, warnings=all_warnings)
        save_html(html)
        return 1

    # =========================================================
    # STEP 2: Detect regimes (macro + per-stock)
    # =========================================================
    print("\n🔍 STEP 2: Detecting regimes...")

    macro_indicators = fetch_macro_indicators()
    macro_regime = classify_macro_regime(macro_indicators)
    macro_adj = get_macro_adjustments(macro_regime)
    print(f"   Macro regime: {macro_regime.upper()} (VIX: {macro_indicators['vix']:.1f})")

    stock_regimes = {}
    for ticker, data in valid_stocks.items():
        if data['historical'] is not None:
            regime = detect_regime_simple(data['historical'])
            stock_regimes[ticker] = get_regime_adjustments(regime)
            print(f"   {ticker}: {regime}")

    regime_info = {
        'stock_regimes': stock_regimes,
        'macro_adjustments': macro_adj
    }

    # ----- GATE 2: Model output validation -----
    print("\n🔒 GATE 2: Validating model outputs...")
    unmodelable = set()
    for ticker, data in valid_stocks.items():
        if data['historical'] is None:
            continue
        vol = calculate_forward_volatility(data['historical'])
        vol, is_modelable, vol_warnings = validate_volatility(ticker, vol)
        all_warnings.extend(vol_warnings)
        for w in vol_warnings:
            print(f"   {w}")
        if not is_modelable:
            unmodelable.add(ticker)

    for ticker, data in valid_stocks.items():
        if ticker in unmodelable:
            continue
        price = data['current_price']
        targets = data.get('price_targets', {})
        target_mean = targets.get('targetMean')

        if data.get('_anchor_suspect') or not target_mean:
            if data['historical'] is not None and len(data['historical']) >= 50:
                fallback = float(data['historical']['Close'].tail(50).mean())
                anchor, anchor_warnings = validate_anchor(ticker, fallback, price, "MA50")
            else:
                anchor = price
                anchor_warnings = [f"[GATE2] {ticker}: No valid anchor — no mean reversion"]
            all_warnings.extend(anchor_warnings)
        else:
            anchor, anchor_warnings = validate_anchor(ticker, target_mean, price, "analyst_target")
            all_warnings.extend(anchor_warnings)

    # =========================================================
    # STEP 3: Analyze sentiment (BEFORE MC — Phase 2)
    # Scores are attached to portfolio_data so MC can use them.
    # =========================================================
    print("\n🤖 STEP 3: Analyzing sentiment (Claude API)...")
    modelable_data = {t: d for t, d in valid_stocks.items() if t not in unmodelable}
    try:
        for ticker, data in modelable_data.items():
            sentiment = analyze_stock_sentiment(
                ticker,
                data['current_price'],
                data['earnings_date'],
                data['analyst_grade']
            )
            # Attach to portfolio_data so MC can read it
            portfolio_data[ticker]['sentiment'] = sentiment
            print(f"   {ticker}: {sentiment['sentiment_score']:.1f} - {sentiment['narrative']}")
    except Exception as e:
        print(f"   ⚠️  Sentiment analysis skipped: {e}")

    # =========================================================
    # STEP 4: Build correlation matrix
    # =========================================================
    print("\n🔗 STEP 4: Building correlation matrix...")
    corr_matrix, ticker_order = build_correlation_matrix(modelable_data)

    corr_matrix, corr_warnings = validate_correlation_matrix(corr_matrix, ticker_order)
    all_warnings.extend(corr_warnings)
    for w in corr_warnings:
        print(f"   {w}")
    print(f"   Correlation matrix: {corr_matrix.shape} ({len(unmodelable)} stocks excluded)")

    # =========================================================
    # STEP 5: Run Monte Carlo simulations (with Phase 2 enrichment)
    # =========================================================
    print("\n🎲 STEP 5: Running Monte Carlo simulations...")
    simulation_results = simulate_portfolio(modelable_data, corr_matrix, ticker_order, regime_info)
    print(f"   Simulated {len(simulation_results)} stocks")

    # ----- GATE 3: Simulation output validation -----
    print("\n🔒 GATE 3: Validating simulation outputs...")
    simulation_results, gate3_warnings = validate_simulation_results(simulation_results)
    all_warnings.extend(gate3_warnings)
    for w in gate3_warnings:
        print(f"   {w}")

    # =========================================================
    # STEP 6: Generate execution signals
    # =========================================================
    print("\n⚡ STEP 6: Generating execution signals...")
    execution_data = process_execution_signals(simulation_results)

    for ticker, data in execution_data.items():
        print(f"   {ticker}: {data['signal']} - {data['one_liner']}")

    # ----- GATE 4: Portfolio-level signal validation -----
    print("\n🔒 GATE 4: Validating portfolio signals...")
    execution_data, gate4_warnings = validate_signals_portfolio(execution_data, macro_indicators)
    all_warnings.extend(gate4_warnings)
    for w in gate4_warnings:
        print(f"   {w}")

    # =========================================================
    # STEP 7: Generate dashboard
    # =========================================================
    print("\n📈 STEP 7: Generating HTML dashboard...")
    html = generate_html(
        execution_data,
        macro_regime,
        macro_indicators['vix'],
        portfolio_data,
        warnings=all_warnings
    )
    save_html(html)

    # =========================================================
    # STEP 8: Archive signals for backtest
    # =========================================================
    print("\n📝 STEP 8: Archiving signals...")
    try:
        archive_signals(execution_data, portfolio_data)
    except Exception as e:
        print(f"   ⚠️  Signal archive failed: {e}")
        # Continue - archiving is not critical for daily operation

    # Summary
    print("\n" + "=" * 60)
    buy_count = sum(1 for d in execution_data.values() if d['signal'] == 'BUY')
    wait_count = sum(1 for d in execution_data.values() if d['signal'] == 'WAIT')
    print(f"✅ SGC DIP ENGINE - Run Complete")
    print(f"   Stocks modeled: {len(simulation_results)}")
    print(f"   Unmodelable: {len(unmodelable)} ({', '.join(unmodelable) if unmodelable else 'none'})")
    print(f"   Warnings: {len(all_warnings)}")
    print(f"   BUY: {buy_count} | WAIT: {wait_count}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
