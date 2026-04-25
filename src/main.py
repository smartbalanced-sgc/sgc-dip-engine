"""
Main Orchestrator — SGC Dip Engine v7 (Session 3 Update)
Pipeline with Phase 2 enrichment + Session 2 intelligence + Session 3 AI upgrade:
  Data → GATE 1 → Regimes → GATE 2 → Sentiment → Correlation → MC → GATE 3 → Signals → GATE 4 → Dashboard

Session 2 changes:
  - macro_events passed to MC for time-varying volatility
  - portfolio_data + macro_events passed to execution signals for catalyst dates
  - Backtest runs after archive (if ≥14 days of data)
  - Backtest results displayed on dashboard

Session 3 changes:
  - Sentiment analysis upgraded with web search enrichment
  - Company name and sector passed to sentiment analysis
  - Sentiment cost tracking and display
"""

import sys
from datetime import datetime

from config import PORTFOLIO, MIN_VALID_STOCKS
from config_loader import get_config
from data_fetcher import fetch_portfolio_data
from garch_model import calculate_forward_volatility
from hmm_regime import detect_regime_simple, get_regime_adjustments
from macro_regime import fetch_macro_indicators, classify_macro_regime, get_macro_adjustments
from correlation import build_correlation_matrix
from monte_carlo import simulate_portfolio
from sentiment import detect_catalysts, run_ai_intelligence, prioritize_buy_signals
from execution_logic import process_execution_signals
from dashboard_generator import generate_html, save_html
from signal_archiver import archive_signals
from backtest import run_backtest
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
    print("SGC DIP ENGINE v7 - Starting Run")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    all_warnings = []

    # =========================================================
    # STEP 1: Fetch all data (15 FMP endpoints + Eulerpool/yfinance + macro)
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
    # STEP 3: AI Intelligence (Session 5 — Trigger-Based)
    # Layer 0: Free structural enrichment (handled in monte_carlo.py)
    # Layer 1: Catalyst triggers (targeted AI, no web search)
    # Layer 2: Emergency web search (3-sigma only, ~2-3×/month)
    # =========================================================
    print("\n🤖 STEP 3: Detecting catalysts for AI intelligence...")
    modelable_data = {t: d for t, d in valid_stocks.items() if t not in unmodelable}
    try:
        # Detect which stocks have catalysts today
        catalysts = detect_catalysts(portfolio_data)
        
        if catalysts:
            trigger_summary = {t: c['trigger'] for t, c in catalysts.items()}
            for ticker, trigger in trigger_summary.items():
                print(f"   ⚡ {ticker}: {trigger}")
            
            # Run targeted AI on catalyst stocks only
            ai_results = run_ai_intelligence(portfolio_data, catalysts)
            
            # Attach AI results to portfolio_data for Monte Carlo consumption
            for ticker, result in ai_results.items():
                portfolio_data[ticker]['ai_result'] = result
        else:
            print("   ✅ No catalysts detected — AI skipped (£0 cost)")
        
    except Exception as e:
        print(f"   ⚠️  AI intelligence skipped: {e}")

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
    # STEP 5: Run Monte Carlo simulations
    # §Session 2: macro_events passed for time-varying vol schedule
    # =========================================================
    print("\n🎲 STEP 5: Running Monte Carlo simulations...")
    simulation_results = simulate_portfolio(
        modelable_data, corr_matrix, ticker_order, regime_info,
        macro_events=macro_events  # §Session 2: time-varying vol
    )
    print(f"   Simulated {len(simulation_results)} stocks")

    # ----- GATE 3: Simulation output validation -----
    print("\n🔒 GATE 3: Validating simulation outputs...")
    simulation_results, gate3_warnings = validate_simulation_results(simulation_results)
    all_warnings.extend(gate3_warnings)
    for w in gate3_warnings:
        print(f"   {w}")

    # =========================================================
    # STEP 6: Generate execution signals
    # §Session 2: portfolio_data + macro_events for anchor suppression + catalyst dates
    # =========================================================
    print("\n⚡ STEP 6: Generating execution signals...")
    execution_data = process_execution_signals(
        simulation_results,
        portfolio_data=portfolio_data,  # §Session 2: anchor suppression
        macro_events=macro_events       # §Session 2: catalyst dates
    )

    for ticker, data in execution_data.items():
        print(f"   {ticker}: {data['signal']} - {data['one_liner']}")
        if data.get('_anchor_suppressed'):
            print(f"      🔇 {data['_suppress_reason']}")

    # Session 5: BUY signal prioritization (Trigger D)
    buy_tickers = [t for t, d in execution_data.items() if d['signal'] == 'BUY']
    if len(buy_tickers) >= 2:
        print(f"\n   🎯 Prioritizing {len(buy_tickers)} BUY signals...")
        try:
            ranked, rationale, priority_cost = prioritize_buy_signals(buy_tickers, portfolio_data, None)
            for i, ticker in enumerate(ranked):
                execution_data[ticker]['_priority_rank'] = i + 1
                execution_data[ticker]['_priority_reason'] = rationale.get(ticker, '')
                print(f"      #{i+1} {ticker}: {rationale.get(ticker, '')[:60]}")
        except Exception as e:
            print(f"      ⚠️  Prioritization skipped: {e}")

    # ----- GATE 4: Portfolio-level signal validation -----
    print("\n🔒 GATE 4: Validating portfolio signals...")
    execution_data, gate4_warnings = validate_signals_portfolio(execution_data, macro_indicators)
    all_warnings.extend(gate4_warnings)
    for w in gate4_warnings:
        print(f"   {w}")

    # =========================================================
    # STEP 7: Generate dashboard
    # §Session 2: backtest results passed to dashboard
    # =========================================================
    # §Session 2: Run backtest if enabled and sufficient data
    backtest_results = None
    if get_config('backtest', 'enabled', default=False):
        print("\n📊 Running backtest...")
        try:
            backtest_results = run_backtest(portfolio_data=portfolio_data)
        except Exception as e:
            print(f"   ⚠️  Backtest failed: {e}")

    print("\n📈 STEP 7: Generating HTML dashboard...")
    html = generate_html(
        execution_data,
        macro_regime,
        macro_indicators['vix'],
        portfolio_data,
        warnings=all_warnings,
        backtest_results=backtest_results  # §Session 2: backtest on dashboard
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

    # Summary
    print("\n" + "=" * 60)
    buy_count = sum(1 for d in execution_data.values() if d['signal'] == 'BUY')
    wait_count = sum(1 for d in execution_data.values() if d['signal'] == 'WAIT')
    print(f"✅ SGC DIP ENGINE - Run Complete")
    print(f"   Stocks modeled: {len(simulation_results)}")
    print(f"   Unmodelable: {len(unmodelable)} ({', '.join(unmodelable) if unmodelable else 'none'})")
    print(f"   Warnings: {len(all_warnings)}")
    print(f"   BUY: {buy_count} | WAIT: {wait_count}")
    if backtest_results and backtest_results.get('status') == 'complete':
        print(f"   Backtest: {backtest_results['hit_rate']:.0%} hit rate ({backtest_results['hits']}/{backtest_results['total_wait_signals']})")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
