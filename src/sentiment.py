"""
Trigger-Based AI Intelligence — SGC Dip Engine v7 (Session 5)

Architecture:
  Layer 0: Free structural enrichment (earnings proximity, analyst spread, insider signal)
           → Applied in compute_enrichment_modifiers (monte_carlo.py), no AI needed
  
  Layer 1: Catalyst triggers (targeted AI, no web search, structured data only)
           → Post-earnings interpretation: VOL_REGIME + CROSS_RISK
           → Unusual move diagnosis: THESIS_INTACT / THESIS_RISK  
           → BUY signal prioritization: rank deployment order
  
  Layer 2: Emergency web search (3-sigma move + no FMP explanation)
           → Fires ~2-3 times/month across entire portfolio

Cost model:
  Old: £5/run (blanket web search × 13 stocks) = £110/month
  New: £0.01-0.20/run (0-6 targeted calls) = £0.20-0.50/month

Key insight: AI modifies VOL REGIME (which changes dip depth), not DRIFT (which is negligible).
"""

import os
import numpy as np
from datetime import datetime


def get_client():
    """Initialize Anthropic client only when needed (lazy init for GitHub Actions)"""
    try:
        from anthropic import Anthropic
        api_key = os.getenv('ANTHROPIC_API_KEY')
        if api_key:
            return Anthropic(api_key=api_key)
        else:
            print("⚠️  ANTHROPIC_API_KEY not set")
            return None
    except Exception as e:
        print(f"⚠️  Failed to initialize Anthropic client: {e}")
        return None


# =============================================================
# COST TRACKING (Anthropic published pricing — Sonnet 4)
# §2026-05-14 cost optimisation: replaces hardcoded estimates
# =============================================================
# Sonnet 4 token pricing: $3 / 1M input, $15 / 1M output
# Web search tool: $10 / 1000 uses ($0.01 per use)
# Source: anthropic.com/pricing

INPUT_PRICE_PER_TOKEN = 3.00 / 1_000_000
OUTPUT_PRICE_PER_TOKEN = 15.00 / 1_000_000
WEB_SEARCH_PER_USE = 0.01


def compute_call_cost(response, had_web_search=False):
    """Compute actual USD cost from an Anthropic response object.
    Falls back to a small estimate if usage fields are unavailable."""
    try:
        u = response.usage
        cost = (u.input_tokens * INPUT_PRICE_PER_TOKEN
                + u.output_tokens * OUTPUT_PRICE_PER_TOKEN)
        # Server tool use (web search) — count actual invocations if reported
        ws_uses = 0
        stu = getattr(u, 'server_tool_use', None)
        if stu is not None:
            ws_uses = getattr(stu, 'web_search_requests', 0) or 0
        if not ws_uses and had_web_search:
            ws_uses = 1  # conservative fallback for single-call sites
        cost += ws_uses * WEB_SEARCH_PER_USE
        return float(cost)
    except Exception:
        return 0.05 if had_web_search else 0.005  # safe conservative fallback


# =============================================================
# CATALYST DETECTION (Layer 0 — Free, runs every day)
# =============================================================

def detect_catalysts(portfolio_data, previous_prices=None, unmodelable=None):
    """
    Scan all stocks for catalyst triggers. Returns dict of ticker → catalyst_info.

    Trigger A: Post-earnings (0-24h after earnings)
    Trigger B: Unusual move (residual Z-score > 2.5, beta-adjusted; 3.0 for vol-excluded)
    Trigger C: Emergency (residual Z-score > 3.0, no FMP explanation)
    Trigger D: BUY prioritization (≥2 BUY signals same day)

    Args:
        portfolio_data: dict of ticker → stock data (from data_fetcher)
        previous_prices: dict of ticker → yesterday's close (for move detection)
        unmodelable: set of tickers excluded by vol gate. For these, the
            unusual_move Z-threshold is raised to 3.0 (their high baseline
            volatility means lower thresholds produce noisy triggers).

    Returns: dict of ticker → {'trigger': str, 'details': dict}
    """
    if unmodelable is None:
        unmodelable = set()
    catalysts = {}
    today = datetime.now().date()

    for ticker, data in portfolio_data.items():
        if data.get('_skip'):
            continue

        current_price = data.get('current_price')
        if not current_price:
            continue
        # §2026-05-14 cost optimisation: tighter Z-threshold for vol-excluded
        # stocks whose normal-day moves can themselves register Z ~2-3.
        unusual_z_threshold = 3.0 if ticker in unmodelable else 2.5
        
        earnings_date = data.get('earnings_date')
        hist = data.get('historical')
        
        # --- Trigger A: Post-earnings (earnings within last 3 days) ---
        if earnings_date:
            try:
                ed = datetime.strptime(earnings_date, '%Y-%m-%d').date() if isinstance(earnings_date, str) else earnings_date
                days_since = (today - ed).days
                if 0 <= days_since <= 3:
                    catalysts[ticker] = {
                        'trigger': 'post_earnings',
                        'details': {
                            'days_since_earnings': days_since,
                            'earnings_date': str(ed)
                        }
                    }
                    continue
            except (ValueError, TypeError):
                pass
        
        # --- Trigger B/C: Unusual move (beta-adjusted residual Z-score) ---
        if hist is not None and len(hist) >= 60:
            returns = hist['Close'].pct_change().dropna()
            if len(returns) >= 20:
                today_return = float(returns.iloc[-1])
                
                # Get stock beta for residual calculation
                profile = data.get('profile', {}) or {}
                beta = profile.get('beta', 1.0)
                if beta is None:
                    beta = 1.0
                
                # Estimate market return from SPY (approximate from momentum)
                # In production, SPY return would come from macro data
                # For now, use raw return with beta-adjusted threshold
                historical_vol = float(returns.tail(60).std())
                
                if historical_vol > 0:
                    raw_z = abs(today_return) / historical_vol
                    # Beta-adjusted: higher beta stocks need larger moves to trigger
                    adjusted_z = raw_z / max(beta, 0.5)
                    
                    if adjusted_z > 3.0:
                        # Check if FMP has an explanation (earnings, grade change)
                        has_fmp_explanation = bool(
                            data.get('analyst_grade') or
                            (earnings_date and abs((today - datetime.strptime(earnings_date, '%Y-%m-%d').date()).days) <= 3 if isinstance(earnings_date, str) else False)
                        )
                        
                        if not has_fmp_explanation:
                            # Trigger C: Emergency — web search needed
                            catalysts[ticker] = {
                                'trigger': 'emergency_search',
                                'details': {
                                    'z_score': round(adjusted_z, 2),
                                    'return_pct': round(today_return * 100, 2),
                                    'beta': round(beta, 2)
                                }
                            }
                            continue
                    
                    if adjusted_z > unusual_z_threshold:
                        # Trigger B: Unusual move — structured data diagnosis
                        catalysts[ticker] = {
                            'trigger': 'unusual_move',
                            'details': {
                                'z_score': round(adjusted_z, 2),
                                'return_pct': round(today_return * 100, 2),
                                'beta': round(beta, 2)
                            }
                        }
    
    return catalysts


# =============================================================
# LAYER 1: TARGETED AI CALLS (No web search, structured data)
# =============================================================

def analyze_post_earnings(ticker, stock_data, client):
    """
    Post-earnings AI interpretation.
    Uses structured FMP data only. No web search.
    
    Returns: {'vol_regime': 'HIGH'/'MEDIUM'/'LOW', 'cross_risk': str, 'narrative': str}
    Cost: ~£0.002 per call
    """
    profile = stock_data.get('profile', {}) or {}
    company_name = profile.get('companyName', ticker)
    
    # Build structured context from already-fetched data
    analyst_grade = stock_data.get('analyst_grade', {}) or {}
    price_targets = stock_data.get('price_targets', {}) or {}
    momentum = stock_data.get('momentum', {}) or {}
    insider = stock_data.get('insider_stats', {}) or {}
    rsi = stock_data.get('rsi')
    
    grade_str = f"Action: {analyst_grade.get('action', 'N/A')}, Grade: {analyst_grade.get('newGrade', 'N/A')}" if analyst_grade else "No recent grade"
    target_str = f"Mean: ${price_targets.get('targetMean', 'N/A')}, High: ${price_targets.get('targetHigh', 'N/A')}, Low: ${price_targets.get('targetLow', 'N/A')}" if price_targets.get('targetMean') else "No targets"
    mom_str = f"1M: {momentum.get('1M', 'N/A')}%, 3M: {momentum.get('3M', 'N/A')}%" if momentum.get('1M') else "N/A"
    
    prompt = f"""TICKER: {ticker} ({company_name})
EVENT: Just reported earnings
STRUCTURED DATA:
- Current price: ${stock_data.get('current_price', 'N/A'):.2f}
- RSI: {rsi if rsi else 'N/A'}
- Analyst targets: {target_str}
- Recent analyst action: {grade_str}
- Momentum: {mom_str}
- Insider activity: {insider.get('change', 'N/A')} change

QUESTION: Based on this structured data, what is the post-earnings volatility regime for next 30 days?
Answer in EXACTLY this format (3 lines only):
VOL_REGIME: HIGH / MEDIUM / LOW
CROSS_RISK: [list any portfolio stocks at narrative risk, or NONE]
REASON: [max 80 chars citing specific data above]"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}]
        )
        
        text = response.content[0].text
        result = _parse_ai_response(text, 'post_earnings')
        result['cost'] = compute_call_cost(response, had_web_search=False)
        return result
        
    except Exception as e:
        print(f"   ⚠️  Post-earnings AI failed for {ticker}: {e}")
        return {'vol_regime': 'MEDIUM', 'cross_risk': 'NONE', 'narrative': f'AI unavailable: {str(e)[:40]}', 'cost': 0.0}


def analyze_unusual_move(ticker, stock_data, catalyst_details, client):
    """
    Diagnose unusual stock-specific move using structured data only.
    
    Returns: {'thesis_status': 'INTACT'/'AT_RISK', 'category': str, 'narrative': str}
    Cost: ~£0.002 per call
    """
    profile = stock_data.get('profile', {}) or {}
    company_name = profile.get('companyName', ticker)
    sector = profile.get('sector', 'Unknown')
    
    z_score = catalyst_details.get('z_score', 0)
    return_pct = catalyst_details.get('return_pct', 0)
    
    analyst_grade = stock_data.get('analyst_grade', {}) or {}
    grade_str = f"Action: {analyst_grade.get('action')}, Grade: {analyst_grade.get('newGrade')}" if analyst_grade.get('action') else "No recent grade"
    
    prompt = f"""TICKER: {ticker} ({company_name}, {sector})
EVENT: {return_pct:+.1f}% move today (Z-score: {z_score:.1f})
FMP DATA: {grade_str}. Insider: {stock_data.get('insider_stats', {}).get('change', 'neutral')}.

QUESTION: Is this move likely:
A) MACRO_CONTAGION (sector/market-wide)
B) COMPETITOR_EVENT (peer company news)
C) THESIS_RISK (fundamental deterioration)
D) TECHNICAL (overbought/oversold correction)

Answer in EXACTLY this format (2 lines only):
CATEGORY: [A/B/C/D]
REASON: [max 80 chars]"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=60,
            messages=[{"role": "user", "content": prompt}]
        )
        
        text = response.content[0].text
        result = _parse_ai_response(text, 'unusual_move')
        result['cost'] = compute_call_cost(response, had_web_search=False)
        return result
        
    except Exception as e:
        print(f"   ⚠️  Unusual move AI failed for {ticker}: {e}")
        return {'thesis_status': 'INTACT', 'category': 'UNKNOWN', 'narrative': f'AI unavailable: {str(e)[:40]}', 'cost': 0.0}


def analyze_emergency(ticker, stock_data, catalyst_details, client):
    """
    Emergency web search — 3-sigma move with no FMP explanation.
    Only fires ~2-3 times per month across entire portfolio.
    
    Returns: {'thesis_status': 'INTACT'/'AT_RISK'/'CRITICAL', 'narrative': str}
    Cost: ~£0.08 per call (web search)
    """
    profile = stock_data.get('profile', {}) or {}
    company_name = profile.get('companyName', ticker)
    return_pct = catalyst_details.get('return_pct', 0)
    
    prompt = f"""Search for news about {company_name} ({ticker}) in the last 24 hours.
The stock moved {return_pct:+.1f}% today with no visible catalyst in analyst data.

What caused this move? Is the investment thesis at risk?

Answer in EXACTLY this format (2 lines only):
THESIS: INTACT / AT_RISK / CRITICAL
REASON: [max 100 chars citing specific news found]"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=150,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search"
            }],
            messages=[{"role": "user", "content": prompt}]
        )
        
        # Extract text from response (may have tool use blocks)
        text_parts = [block.text for block in response.content if block.type == "text"]
        text = "\n".join(text_parts) if text_parts else ""
        
        result = _parse_ai_response(text, 'emergency')
        result['cost'] = compute_call_cost(response, had_web_search=True)
        return result
        
    except Exception as e:
        print(f"   ⚠️  Emergency search failed for {ticker}: {e}")
        return {'thesis_status': 'INTACT', 'narrative': f'Search unavailable: {str(e)[:40]}', 'cost': 0.0}


def prioritize_buy_signals(buy_tickers, portfolio_data, client):
    """
    Rank multiple BUY signals for deployment priority.
    Uses structured data only, one call for all BUY stocks.
    
    Returns: list of tickers in priority order + rationale dict
    Cost: ~£0.002 (single call regardless of count)
    """
    # §2026-05-14 cost optimisation: raised gate from <2 to <3.
    # Ranking only adds value when there are 3+ BUYs competing for capital;
    # for 2 BUYs the dashboard side-by-side comparison is sufficient.
    if len(buy_tickers) < 3:
        return buy_tickers, {t: f'Only {len(buy_tickers)} BUY signal(s) — ranking skipped' for t in buy_tickers}, 0.0
    
    # Build structured summary for each BUY stock
    lines = []
    for ticker in buy_tickers:
        data = portfolio_data.get(ticker, {})
        rsi = data.get('rsi', 'N/A')
        insider = data.get('insider_stats', {}) or {}
        insider_str = insider.get('change', 'neutral')
        momentum = data.get('momentum', {}) or {}
        mom_1m = momentum.get('1M', 'N/A')
        earnings = data.get('earnings_date', 'none')
        
        # Days to earnings
        days_to_earn = 'N/A'
        if earnings:
            try:
                ed = datetime.strptime(earnings, '%Y-%m-%d').date()
                days_to_earn = (ed - datetime.now().date()).days
            except:
                pass
        
        lines.append(f"- {ticker}: RSI {rsi}, 1M momentum {mom_1m}%, insider {insider_str}, earnings in {days_to_earn}d")
    
    stocks_block = "\n".join(lines)
    
    prompt = f"""{len(buy_tickers)} BUY signals today for a buy-and-hold DCA investor:
{stocks_block}

Rank by deployment priority. Consider: insider buying > analyst upgrades > momentum > earnings proximity.
Answer format (one line per stock, ranked #1 = deploy first):
RANK: TICKER - [max 60 chars reason citing data above]"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}]
        )
        
        text = response.content[0].text
        
        # Parse ranked list
        ranked = []
        rationale = {}
        for line in text.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            for t in buy_tickers:
                if t in line:
                    if t not in ranked:
                        ranked.append(t)
                        # Extract rationale after ticker
                        parts = line.split(t, 1)
                        reason = parts[1].strip(' -:') if len(parts) > 1 else ''
                        rationale[t] = reason[:80]
        
        # Add any missing tickers at end
        for t in buy_tickers:
            if t not in ranked:
                ranked.append(t)
                rationale[t] = 'Unranked'
        
        return ranked, rationale, compute_call_cost(response, had_web_search=False)
        
    except Exception as e:
        print(f"   ⚠️  BUY prioritization failed: {e}")
        return buy_tickers, {t: 'Prioritization unavailable' for t in buy_tickers}, 0.0


# =============================================================
# RESPONSE PARSING
# =============================================================

def _parse_ai_response(text, trigger_type):
    """Parse structured AI response into dict."""
    result = {}
    
    if trigger_type == 'post_earnings':
        result['vol_regime'] = 'MEDIUM'
        result['cross_risk'] = 'NONE'
        result['narrative'] = ''
        
        for line in text.split('\n'):
            line = line.strip()
            if 'VOL_REGIME:' in line:
                val = line.split('VOL_REGIME:')[1].strip().upper()
                if val in ['HIGH', 'MEDIUM', 'LOW']:
                    result['vol_regime'] = val
            elif 'CROSS_RISK:' in line:
                result['cross_risk'] = line.split('CROSS_RISK:')[1].strip()
            elif 'REASON:' in line:
                result['narrative'] = line.split('REASON:')[1].strip()[:100]
    
    elif trigger_type == 'unusual_move':
        result['thesis_status'] = 'INTACT'
        result['category'] = 'TECHNICAL'
        result['narrative'] = ''
        
        for line in text.split('\n'):
            line = line.strip()
            if 'CATEGORY:' in line:
                cat = line.split('CATEGORY:')[1].strip().upper()
                if cat.startswith('C'):
                    result['thesis_status'] = 'AT_RISK'
                    result['category'] = 'THESIS_RISK'
                elif cat.startswith('A'):
                    result['category'] = 'MACRO_CONTAGION'
                elif cat.startswith('B'):
                    result['category'] = 'COMPETITOR_EVENT'
                else:
                    result['category'] = 'TECHNICAL'
            elif 'REASON:' in line:
                result['narrative'] = line.split('REASON:')[1].strip()[:100]
    
    elif trigger_type == 'emergency':
        result['thesis_status'] = 'INTACT'
        result['narrative'] = ''
        
        for line in text.split('\n'):
            line = line.strip()
            if 'THESIS:' in line:
                val = line.split('THESIS:')[1].strip().upper()
                if 'CRITICAL' in val:
                    result['thesis_status'] = 'CRITICAL'
                elif 'AT_RISK' in val or 'RISK' in val:
                    result['thesis_status'] = 'AT_RISK'
                else:
                    result['thesis_status'] = 'INTACT'
            elif 'REASON:' in line:
                result['narrative'] = line.split('REASON:')[1].strip()[:120]
    
    return result


# =============================================================
# MAIN ENTRY POINT — Replaces old analyze_stock_sentiment()
# =============================================================

def run_ai_intelligence(portfolio_data, catalysts):
    """
    Execute targeted AI calls based on detected catalysts.
    
    Args:
        portfolio_data: dict of ticker → stock data
        catalysts: dict of ticker → catalyst info (from detect_catalysts)
    
    Returns: dict of ticker → ai_result (attached to portfolio_data by caller)
    """
    if not catalysts:
        print("   No catalysts detected — skipping AI (£0 cost)")
        return {}
    
    client = get_client()
    if not client:
        print("   ⚠️  No Anthropic client — AI intelligence disabled")
        return {}
    
    results = {}
    total_cost = 0.0
    
    for ticker, catalyst in catalysts.items():
        trigger = catalyst['trigger']
        details = catalyst['details']
        stock_data = portfolio_data.get(ticker, {})
        
        if trigger == 'post_earnings':
            print(f"   ⚡ {ticker}: Post-earnings interpretation...")
            result = analyze_post_earnings(ticker, stock_data, client)
            print(f"      VOL_REGIME: {result.get('vol_regime')} | {result.get('narrative', '')[:60]}")
            
        elif trigger == 'unusual_move':
            print(f"   📊 {ticker}: Unusual move diagnosis (Z={details.get('z_score', '?')})...")
            result = analyze_unusual_move(ticker, stock_data, details, client)
            print(f"      {result.get('category')}: {result.get('narrative', '')[:60]}")
            
        elif trigger == 'emergency_search':
            print(f"   🚨 {ticker}: Emergency web search (Z={details.get('z_score', '?')}, no FMP explanation)...")
            result = analyze_emergency(ticker, stock_data, details, client)
            status = result.get('thesis_status', 'UNKNOWN')
            icon = '🔴' if status == 'CRITICAL' else '🟡' if status == 'AT_RISK' else '🟢'
            print(f"      {icon} Thesis: {status} | {result.get('narrative', '')[:60]}")
        else:
            continue
        
        results[ticker] = result
        total_cost += result.get('cost', 0)
    
    print(f"   AI cost: £{total_cost * 0.85:.3f} ({len(results)} calls)")
    return results


# =============================================================
# LAYER 0: FREE STRUCTURAL ENRICHMENT
# These functions feed into compute_enrichment_modifiers in monte_carlo.py
# No AI calls — uses already-fetched FMP/Eulerpool data
# =============================================================

def compute_analyst_spread(price_targets):
    """
    Analyst disagreement as uncertainty proxy.
    High spread → high uncertainty → wider vol.
    
    Returns: spread ratio (0-1) or None
    """
    if not price_targets:
        return None
    
    high = price_targets.get('targetHigh')
    low = price_targets.get('targetLow')
    mean = price_targets.get('targetMean')
    
    if high and low and mean and mean > 0:
        return (high - low) / mean
    
    return None


def compute_implied_vol_ratio(quote_data, historical_vol):
    """
    Implied volatility vs historical vol ratio.
    IV > HV suggests market prices in upcoming catalyst (earnings).
    
    Returns: ratio (>1 means market expects higher vol than history)
    """
    if not quote_data:
        return None
    
    iv = quote_data.get('impliedVolatility')
    if iv and historical_vol and historical_vol > 0:
        return iv / historical_vol
    
    return None
