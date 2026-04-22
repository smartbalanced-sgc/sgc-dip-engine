"""
Main Orchestrator
Runs the full pipeline: data → simulation → sentiment → execution → dashboard
"""

import sys
from datetime import datetime

# Import all modules
from config import PORTFOLIO
from data_fetcher import fetch_portfolio_data
from garch_model import calculate_forward_volatility
from hmm_regime import detect_regime_simple, get_regime_adjustments
from macro_regime import fetch_macro_indicators, classify_macro_regime, get_macro_adjustments
from correlation import build_correlation_matrix
from monte_carlo import simulate_portfolio
from sentiment import analyze_stock_sentiment
from execution_logic import process_execution_signals
from dashboard_generator import generate_html, save_html

def main():
    """
    Main execution pipeline
    """
    
    print("=" * 60)
    print("SGC DIP ENGINE v6 - Starting Run")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # Step 1: Fetch all data
    print("\n📊 STEP 1: Fetching portfolio data...")
    portfolio_data = fetch_portfolio_data()
    
    # Step 2: Detect regimes
    print("\n🔍 STEP 2: Detecting regimes...")
    
    # Macro regime
    macro_indicators = fetch_macro_indicators()
    macro_regime = classify_macro_regime(macro_indicators)
    macro_adj = get_macro_adjustments(macro_regime)
    
    print(f"   Macro regime: {macro_regime.upper()} (VIX: {macro_indicators['vix']:.1f})")
    
    # Stock-level regimes
    stock_regimes = {}
    for ticker, data in portfolio_data.items():
        if data['historical'] is not None:
            regime = detect_regime_simple(data['historical'])
            stock_regimes[ticker] = get_regime_adjustments(regime)
            print(f"   {ticker}: {regime}")
    
    regime_info = {
        'stock_regimes': stock_regimes,
        'macro_adjustments': macro_adj
    }
    
    # Step 3: Build correlation matrix
    print("\n🔗 STEP 3: Building correlation matrix...")
    corr_matrix, ticker_order = build_correlation_matrix(portfolio_data)
    print(f"   Correlation matrix: {corr_matrix.shape}")
    
    # Step 4: Run Monte Carlo simulations
    print("\n🎲 STEP 4: Running Monte Carlo simulations...")
    simulation_results = simulate_portfolio(portfolio_data, corr_matrix, ticker_order, regime_info)
    print(f"   Simulated {len(simulation_results)} stocks")
    
    # Step 5: Analyze sentiment (optional - can be disabled if API quota is low)
    print("\n🤖 STEP 5: Analyzing sentiment (Claude API)...")
    try:
        sentiment_scores = {}
        for ticker, data in portfolio_data.items():
            if ticker in simulation_results:
                sentiment = analyze_stock_sentiment(
                    ticker,
                    data['current_price'],
                    data['earnings_date'],
                    data['analyst_grade']
                )
                sentiment_scores[ticker] = sentiment
                print(f"   {ticker}: {sentiment['sentiment_score']:.1f} - {sentiment['narrative']}")
    except Exception as e:
        print(f"   ⚠️  Sentiment analysis skipped: {e}")
        sentiment_scores = {}
    
    # Step 6: Generate execution signals
    print("\n⚡ STEP 6: Generating execution signals...")
    execution_data = process_execution_signals(simulation_results)
    
    for ticker, data in execution_data.items():
        print(f"   {ticker}: {data['signal']} - {data['one_liner']}")
    
    # Step 7: Generate dashboard
    print("\n📈 STEP 7: Generating HTML dashboard...")
    html = generate_html(
        execution_data,
        macro_regime,
        macro_indicators['vix'],
        portfolio_data
    )
    save_html(html)
    
    print("\n" + "=" * 60)
    print("✅ SGC DIP ENGINE - Run Complete")
    print("=" * 60)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
