"""
Per-Stock Trade Execution Regime Classifier — SGC Dip Engine v7 (May 13)

Classifies each stock into one of 5 trade execution regimes:
  - NORMAL              : Standard mean-reverting behaviour — dip-buy logic valid
  - MOMENTUM            : Legitimate strong trend — dip-buy disabled (won't fill)
  - SQUEEZE_RISK        : Forced rally characteristics — dip-buy disabled (fragile)
  - OVERSOLD_REVERSAL   : High-conviction bottom signal — dip-buy boosted
  - BREAKDOWN           : Sustained decline, no reversal yet — dip-buy disabled

CRITICAL: This is DISTINCT from hmm_regime.py which returns bull/sideways/drawdown
and is consumed by Monte Carlo for drift/vol multipliers. Two regime concepts,
two modules, two purposes. DO NOT CONFUSE.

The output of classify_trade_regime() is consumed by execution_logic.py to
modulate the BUY/WAIT signal. Thresholds live in config.yaml regime_classifier.

AI research (Anthropic web search) disambiguates MOMENTUM vs SQUEEZE_RISK using
fresh data (insider activity, analyst revisions, short interest news) when FMP
data is insufficient. Cached per-stock daily to control cost.
"""

import os
import json
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
from config_loader import get_config


# =============================================================
# AI RESEARCH CACHE (persistent across runs)
# Stored as JSON in data/regime_ai_cache.json — TTL controlled
# by config.yaml regime_classifier.ai_research.cache_hours
# =============================================================

def _get_cache_path():
    """Path to AI research cache file."""
    repo_root = Path(__file__).parent.parent
    cache_dir = repo_root / 'data'
    cache_dir.mkdir(exist_ok=True)
    return cache_dir / 'regime_ai_cache.json'


def _load_ai_cache():
    """Load AI research cache (returns dict ticker → {timestamp, result})."""
    path = _get_cache_path()
    if not path.exists():
        return {}
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_ai_cache(cache):
    """Persist AI research cache."""
    path = _get_cache_path()
    try:
        with open(path, 'w') as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"   ⚠️  Failed to save AI cache: {e}")


def _cache_hit(cache, ticker, max_age_hours):
    """Check if cached AI research is still fresh."""
    entry = cache.get(ticker)
    if not entry or not entry.get('timestamp'):
        return None
    try:
        cached_time = datetime.fromisoformat(entry['timestamp'])
        age_hours = (datetime.now() - cached_time).total_seconds() / 3600
        if age_hours < max_age_hours:
            return entry.get('result')
    except Exception:
        pass
    return None


# =============================================================
# COMPOSITE SIGNAL COMPUTATION
# Each is derived from FMP data already in portfolio_data
# =============================================================

def compute_signal_metrics(ticker, stock_data, sector_perf_map):
    """
    Compute 5 composite signals for regime classification.
    
    Returns dict with:
      rsi, momentum_5d, momentum_20d, drawdown_from_high,
      sector_decoupling, relative_volume
    
    Ref: config.yaml regime_classifier — thresholds defined here
    """
    metrics = {
        'rsi': None,
        'momentum_5d': None,
        'momentum_20d': None,
        'drawdown_from_high': None,
        'sector_decoupling': None,
        'relative_volume': None,
    }
    
    hist = stock_data.get('historical')
    if hist is None or len(hist) < 20:
        return metrics
    
    # §regime_classifier.momentum.rsi_min — RSI from existing data
    metrics['rsi'] = stock_data.get('rsi')
    
    closes = hist['Close'].values
    
    # §regime_classifier.momentum.momentum_5d_min — 5-day return
    if len(closes) >= 6:
        metrics['momentum_5d'] = float(closes[-1] / closes[-6] - 1)
    
    # §regime_classifier.breakdown.momentum_20d_max — 20-day return
    if len(closes) >= 21:
        metrics['momentum_20d'] = float(closes[-1] / closes[-21] - 1)
    
    # §regime_classifier.oversold_reversal.drawdown_from_high_min
    # Drawdown from 60-day rolling high
    if len(closes) >= 60:
        recent_high = float(np.max(closes[-60:]))
        if recent_high > 0:
            metrics['drawdown_from_high'] = float(closes[-1] / recent_high - 1)
    
    # §regime_classifier.momentum.sector_decoupling_min
    # Stock 5-day return minus sector 5-day return (in pp)
    profile = stock_data.get('profile', {}) or {}
    sector = profile.get('sector', '')
    if sector and metrics['momentum_5d'] is not None:
        sector_5d_return = sector_perf_map.get(sector)
        if sector_5d_return is not None:
            metrics['sector_decoupling'] = float(metrics['momentum_5d'] - sector_5d_return)
    
    # §regime_classifier.momentum.relative_volume_min
    # Today's volume / 30-day average volume
    if 'Volume' in hist.columns and len(hist) >= 30:
        try:
            volumes = hist['Volume'].values
            avg_30d = float(np.mean(volumes[-30:-1])) if len(volumes) > 1 else 0
            today_vol = float(volumes[-1])
            if avg_30d > 0:
                metrics['relative_volume'] = today_vol / avg_30d
        except Exception:
            pass
    
    return metrics


# =============================================================
# RULE-BASED REGIME CLASSIFICATION
# Pure logic, no AI required — runs on every stock
# =============================================================

def _classify_rule_based(metrics):
    """
    Apply rule-based regime classification using metrics + config thresholds.
    Returns regime name + confidence + reasoning.
    
    Order matters: most specific (SQUEEZE_RISK) before more general (MOMENTUM).
    """
    rsi = metrics.get('rsi')
    mom_5d = metrics.get('momentum_5d')
    mom_20d = metrics.get('momentum_20d')
    dd = metrics.get('drawdown_from_high')
    decoup = metrics.get('sector_decoupling')
    rel_vol = metrics.get('relative_volume')
    
    # Pull thresholds from config
    sq_cfg = get_config('regime_classifier', 'squeeze_risk', default={})
    mom_cfg = get_config('regime_classifier', 'momentum', default={})
    osr_cfg = get_config('regime_classifier', 'oversold_reversal', default={})
    bd_cfg = get_config('regime_classifier', 'breakdown', default={})
    
    # ---------- SQUEEZE_RISK (check first — tightest thresholds) ----------
    # §regime_classifier.squeeze_risk — all four conditions AND logic
    if (rsi is not None and rsi >= sq_cfg.get('rsi_min', 80) and
        mom_5d is not None and mom_5d >= sq_cfg.get('momentum_5d_min', 0.15) and
        decoup is not None and decoup >= sq_cfg.get('sector_decoupling_min', 0.10) and
        rel_vol is not None and rel_vol >= sq_cfg.get('relative_volume_min', 1.8)):
        return {
            'regime': 'SQUEEZE_RISK',
            'confidence': 0.70,  # Probabilistic without short interest data
            'reasoning': (
                f"RSI {rsi:.0f} extreme + 5d move +{mom_5d*100:.1f}% + "
                f"sector decoupling +{decoup*100:.1f}pp + volume {rel_vol:.1f}x"
            ),
            'metrics': metrics,
        }
    
    # ---------- MOMENTUM (more permissive than SQUEEZE) ----------
    # §regime_classifier.momentum — all four conditions AND logic
    if (rsi is not None and rsi >= mom_cfg.get('rsi_min', 75) and
        mom_5d is not None and mom_5d >= mom_cfg.get('momentum_5d_min', 0.10) and
        decoup is not None and decoup >= mom_cfg.get('sector_decoupling_min', 0.05) and
        rel_vol is not None and rel_vol >= mom_cfg.get('relative_volume_min', 1.3)):
        return {
            'regime': 'MOMENTUM',
            'confidence': 0.80,
            'reasoning': (
                f"RSI {rsi:.0f} overbought + 5d +{mom_5d*100:.1f}% + "
                f"sector +{decoup*100:.1f}pp + volume {rel_vol:.1f}x"
            ),
            'metrics': metrics,
        }
    
    # ---------- OVERSOLD_REVERSAL (check before BREAKDOWN — more specific) ----------
    # §regime_classifier.oversold_reversal — all three conditions AND logic
    # Volume confirmation distinguishes capitulation (reversal candidate) from
    # slow-grinding downtrend (breakdown). High volume on oversold prints =
    # forced selling, often the bottom. Without volume, it's just a falling stock.
    if (rsi is not None and rsi <= osr_cfg.get('rsi_max', 30) and
        dd is not None and dd <= -osr_cfg.get('drawdown_from_high_min', 0.10) and
        rel_vol is not None and rel_vol >= osr_cfg.get('relative_volume_min', 1.2)):
        return {
            'regime': 'OVERSOLD_REVERSAL',
            'confidence': 0.75,
            'reasoning': (
                f"RSI {rsi:.0f} oversold + drawdown {dd*100:.1f}% + volume {rel_vol:.1f}x"
            ),
            'metrics': metrics,
        }
    
    # ---------- BREAKDOWN (sustained decline, no reversal) ----------
    # §regime_classifier.breakdown — all three conditions AND logic
    if (dd is not None and dd <= -bd_cfg.get('drawdown_from_high_min', 0.15) and
        mom_20d is not None and mom_20d <= bd_cfg.get('momentum_20d_max', -0.10) and
        rsi is not None and rsi <= bd_cfg.get('rsi_max', 45)):
        return {
            'regime': 'BREAKDOWN',
            'confidence': 0.75,
            'reasoning': (
                f"Drawdown {dd*100:.1f}% + 20d {mom_20d*100:.1f}% + RSI {rsi:.0f} weak"
            ),
            'metrics': metrics,
        }
    
    # ---------- NORMAL (default) ----------
    return {
        'regime': 'NORMAL',
        'confidence': 0.85,
        'reasoning': 'No extreme regime signals',
        'metrics': metrics,
    }


# =============================================================
# AI RESEARCH ENRICHMENT (Layer 2 — disambiguates MOMENTUM vs SQUEEZE)
# Called only for ambiguous classifications. Uses sentiment.py client.
# =============================================================

def _ai_disambiguate_regime(ticker, stock_data, rule_result, client, cost_tracker):
    """
    Use Claude with web search to disambiguate MOMENTUM vs SQUEEZE_RISK.
    
    Returns updated result dict (regime, confidence, reasoning, ai_research).
    Falls through to rule_result if AI fails or cost cap hit.
    """
    daily_cap = get_config('regime_classifier', 'ai_research', 'daily_cost_cap_usd', default=5.0)
    if cost_tracker['total'] >= daily_cap:
        print(f"      💰 Daily AI cap reached (${cost_tracker['total']:.2f}) — using rule result")
        return rule_result
    
    profile = stock_data.get('profile', {}) or {}
    company_name = profile.get('companyName', ticker)
    sector = profile.get('sector', 'Unknown')
    
    metrics = rule_result.get('metrics', {})
    rsi = metrics.get('rsi')
    mom_5d = metrics.get('momentum_5d', 0) or 0
    decoup = metrics.get('sector_decoupling', 0) or 0
    rel_vol = metrics.get('relative_volume', 0) or 0
    
    rsi_str = f"{rsi:.0f}" if rsi is not None else "N/A"
    
    model = get_config('regime_classifier', 'ai_research', 'model',
                       default='claude-sonnet-4-20250514')
    max_tokens = get_config('regime_classifier', 'ai_research', 'max_tokens',
                            default=600)
    
    today_str = datetime.now().strftime('%Y-%m-%d')

    # §2026-05-15 regime AI prompt upgrade — same skepticism pattern as
    # sentiment.py emergency search: explicit source-quality rules,
    # multi-source confirmation, dated short-interest verification,
    # defined regime criteria. Reduces SQUEEZE/MOMENTUM mis-labels from
    # single-source rumours or stale short-interest data.
    prompt = f"""Disambiguate the trade regime for {company_name} ({ticker}, {sector}) as of {today_str}.
Rule-based classifier flagged this as {rule_result['regime']}.

STOCK METRICS FOR CONTEXT:
- RSI: {rsi_str}
- 5-day move: {mom_5d*100:+.1f}%
- Sector decoupling: {decoup*100:+.1f}pp (vs {sector})
- Relative volume: {rel_vol:.1f}x normal

SEARCH PRIORITIES (in order):
1. Short interest from last 14 days (FINRA / exchange data, % of float)
2. Recent insider transactions (Form 4 filings, last 30 days)
3. Analyst rating changes (last 14 days, major outlets)
4. Material news catalysts (last 14 days)
5. Broader sector context (sector ETF performance over same period)

SKEPTICISM RULES:
- Require >= 2 independent reputable sources for any thesis-affecting claim
- PRIMARY (SEC filings, exchange data, official announcements)
  > REPUTABLE (Reuters, Bloomberg, WSJ, FT, CNBC)
  > SPECULATIVE (blogs, social media, single-source rumours)
- If short interest data is older than 14 days, mark "stale" in REASONING
- If the broader sector is also rallying with this stock, lean
  NORMAL or MOMENTUM (not SQUEEZE_RISK)

REGIME CRITERIA:
- MOMENTUM: real fundamental drivers (earnings beat, product launch,
  analyst upgrades, sector tailwind) AND short interest is LOW (< 5%).
- SQUEEZE_RISK: short interest > 10% AND no fundamental catalyst AND
  unusual options/social activity. Forced rally, likely to reverse.
- NORMAL: the move is ordinary (within typical volatility, sector-driven,
  or technically explained without thesis impact).

OUTPUT EXACTLY (6 lines):
REGIME: MOMENTUM / SQUEEZE_RISK / NORMAL
CONFIDENCE: HIGH / MEDIUM / LOW
SHORT_INTEREST: [% of float if found within last 14 days, else "not found" or "stale"]
SOURCE_QUALITY: PRIMARY / REPUTABLE / SPECULATIVE / NONE_FOUND
SOURCES_COUNT: <integer — distinct credible sources>
REASONING: [max 200 chars — cite top source by name]"""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}]
        )
        
        # Extract text from response (may have web_search tool use blocks)
        text_parts = [block.text for block in response.content if hasattr(block, 'text')]
        text = "\n".join(text_parts) if text_parts else ""
        
        # §2026-05-14 cost optimisation: compute real cost from response.usage
        # rather than hardcoded estimate. Sonnet 4 pricing + web search tool.
        from sentiment import compute_call_cost
        cost_tracker['total'] += compute_call_cost(response, had_web_search=True)
        
        # Parse structured response
        ai_result = _parse_ai_regime_response(text)
        
        # Override rule result if AI is confident
        if ai_result.get('regime') and ai_result.get('confidence', 0) >= 0.60:
            return {
                'regime': ai_result['regime'],
                'confidence': ai_result['confidence'],
                'reasoning': ai_result.get('reasoning', rule_result['reasoning']),
                'metrics': metrics,
                'ai_research': {
                    'short_interest': ai_result.get('short_interest', 'not found'),
                    'sources': ai_result.get('sources', ''),
                    'researched_at': datetime.now().isoformat(),
                }
            }
        
        return rule_result
        
    except Exception as e:
        print(f"      ⚠️  AI disambiguation failed for {ticker}: {e}")
        return rule_result


def _parse_ai_regime_response(text):
    """Parse Claude's structured regime response.
    §2026-05-15 upgraded format adds SOURCE_QUALITY + SOURCES_COUNT and uses
    categorical CONFIDENCE (HIGH/MEDIUM/LOW). Legacy numeric CONFIDENCE still
    accepted for backward-compat."""
    import re
    result = {}
    confidence_word_map = {'HIGH': 0.85, 'MEDIUM': 0.65, 'LOW': 0.45}

    for line in text.split('\n'):
        line = line.strip()
        if line.startswith('REGIME:'):
            val = line.split('REGIME:', 1)[1].strip().upper()
            for r in ['MOMENTUM', 'SQUEEZE_RISK', 'NORMAL']:
                if r in val:
                    result['regime'] = r
                    break
        elif line.startswith('CONFIDENCE:'):
            val = line.split('CONFIDENCE:', 1)[1].strip().upper()
            # New format: HIGH / MEDIUM / LOW
            mapped = next((confidence_word_map[w] for w in confidence_word_map if w in val), None)
            if mapped is not None:
                result['confidence'] = mapped
            else:
                # Legacy numeric fallback
                m = re.search(r'(\d+\.?\d*)', val)
                if m:
                    conf = float(m.group(1))
                    if conf > 1.0:
                        conf = conf / 100.0
                    result['confidence'] = min(max(conf, 0.0), 1.0)
        elif line.startswith('SHORT_INTEREST:'):
            result['short_interest'] = line.split('SHORT_INTEREST:', 1)[1].strip()[:60]
        elif line.startswith('SOURCE_QUALITY:'):
            val = line.split('SOURCE_QUALITY:', 1)[1].strip().upper()
            if val in ('PRIMARY', 'REPUTABLE', 'SPECULATIVE', 'NONE_FOUND'):
                result['source_quality'] = val
        elif line.startswith('SOURCES_COUNT:'):
            try:
                result['sources_count'] = int(
                    ''.join(c for c in line.split(':', 1)[1] if c.isdigit()) or '0'
                )
            except Exception:
                result['sources_count'] = 0
        elif line.startswith('REASONING:'):
            result['reasoning'] = line.split('REASONING:', 1)[1].strip()[:200]
        elif line.startswith('SOURCES:'):
            # Legacy field — keep for backward-compat with older cached entries
            result['sources'] = line.split('SOURCES:', 1)[1].strip()[:200]

    # §2026-05-15 quality gate: SPECULATIVE source single-handedly cannot upgrade
    # the regime label. If AI says SQUEEZE_RISK from one speculative source, fall
    # back to MOMENTUM (less drastic). NONE_FOUND can't override at all.
    if result.get('source_quality') == 'NONE_FOUND':
        # AI failed to find anything credible; trust the rule-based result
        result.pop('regime', None)
        result.pop('confidence', None)
    elif (result.get('source_quality') == 'SPECULATIVE'
          and result.get('sources_count', 0) < 2
          and result.get('regime') == 'SQUEEZE_RISK'):
        result['regime'] = 'MOMENTUM'

    return result


# =============================================================
# PUBLIC ENTRY POINT
# =============================================================

def classify_trade_regime(ticker, stock_data, sector_perf_map, client=None, cost_tracker=None):
    """
    Classify a single stock into a trade regime.
    
    Args:
        ticker: stock symbol
        stock_data: dict from data_fetcher (must include historical, profile, rsi)
        sector_perf_map: dict mapping GICS sector name → 5-day sector return
        client: Anthropic client (optional — falls back to rule-based only)
        cost_tracker: dict {'total': float} for daily cost cap enforcement
    
    Returns:
        dict with: regime, confidence, reasoning, metrics, [ai_research]
    """
    # §regime_classifier.enabled — master toggle
    if not get_config('regime_classifier', 'enabled', default=False):
        return {'regime': 'NORMAL', 'confidence': 1.0, 'reasoning': 'Classifier disabled',
                'metrics': {}}
    
    # Step 1: Compute composite metrics
    metrics = compute_signal_metrics(ticker, stock_data, sector_perf_map)
    
    # Step 2: Rule-based classification
    rule_result = _classify_rule_based(metrics)
    
    # Step 3: AI disambiguation only for MOMENTUM/SQUEEZE_RISK (the ambiguous pair)
    ai_enabled = get_config('regime_classifier', 'ai_research', 'enabled', default=False)
    is_ambiguous = rule_result['regime'] in ('MOMENTUM', 'SQUEEZE_RISK')
    
    if ai_enabled and is_ambiguous and client and cost_tracker is not None:
        # Check cache first
        cache = _load_ai_cache()
        cache_hours = get_config('regime_classifier', 'ai_research', 'cache_hours',
                                 default=24)
        cached = _cache_hit(cache, ticker, cache_hours)
        
        if cached:
            print(f"      💾 {ticker}: Using cached AI research")
            return cached
        
        print(f"      🔎 {ticker}: AI disambiguation (rule said {rule_result['regime']})...")
        enriched = _ai_disambiguate_regime(ticker, stock_data, rule_result,
                                           client, cost_tracker)
        
        # Cache successful result
        if enriched.get('ai_research'):
            cache[ticker] = {
                'timestamp': datetime.now().isoformat(),
                'result': enriched,
            }
            _save_ai_cache(cache)
        
        return enriched
    
    return rule_result


def build_sector_perf_map(macro_events_unused, sector_perf_data):
    """
    Build {sector_name: 5d_return} dict from FMP historical-sector-performance.
    
    Args:
        macro_events_unused: kept for signature compatibility (unused)
        sector_perf_data: list of {date, sector, averageChange} from FMP
    
    Returns: dict mapping sector name → 5-day average return (decimal)
    
    Note: Falls back to single-day if 5-day window unavailable.
    Self-computes since FMP doesn't provide pre-computed relative strength.
    """
    if not sector_perf_data:
        return {}
    
    # Group by sector and take recent 5 days
    sector_returns = {}
    df = pd.DataFrame(sector_perf_data) if not isinstance(sector_perf_data, pd.DataFrame) else sector_perf_data
    
    if 'sector' not in df.columns or 'averageChange' not in df.columns:
        return {}
    
    df = df.sort_values('date') if 'date' in df.columns else df
    
    for sector in df['sector'].unique():
        sector_df = df[df['sector'] == sector].tail(5)
        if len(sector_df) > 0:
            # averageChange from FMP is daily % change — sum approximates 5d return
            total_return = sector_df['averageChange'].sum() / 100.0  # to decimal
            sector_returns[sector] = float(total_return)
    
    return sector_returns


def classify_portfolio(portfolio_data, sector_perf_data, client=None, unmodelable=None):
    """
    Run regime classification across the full portfolio.

    Args:
        portfolio_data: dict {ticker: stock_data} from data_fetcher
        sector_perf_data: list/df from fetch_sector_performance
        client: Anthropic client (optional)
        unmodelable: set of tickers excluded by vol gate. For these, the
            rule-based classification still runs (cheap, useful for dashboard
            badges), but the AI disambiguation step is skipped — that step's
            only purpose is to refine MOMENTUM/SQUEEZE_RISK labels which then
            modulate BUY/WAIT signals, and vol-excluded stocks have no signal
            to modulate. Skipping the AI step saves ~$0.20-0.40 per excluded
            triggering stock without losing any decision-support value.
            §2026-05-14 cost optimisation.

    Returns: dict {ticker: regime_result}
    """
    if not get_config('regime_classifier', 'enabled', default=False):
        return {}, 0.0

    if unmodelable is None:
        unmodelable = set()

    sector_perf_map = build_sector_perf_map(None, sector_perf_data)
    cost_tracker = {'total': 0.0}

    results = {}
    for ticker, data in portfolio_data.items():
        if data is None or data.get('_skip'):
            continue
        # §2026-05-14: pass client=None for vol-excluded stocks so AI step
        # is short-circuited inside classify_trade_regime. Rule-based path
        # still produces the regime label for the dashboard.
        effective_client = None if ticker in unmodelable else client
        try:
            result = classify_trade_regime(ticker, data, sector_perf_map,
                                           client=effective_client, cost_tracker=cost_tracker)
            results[ticker] = result
        except Exception as e:
            print(f"   ⚠️  Regime classification failed for {ticker}: {e}")
            results[ticker] = {
                'regime': 'NORMAL', 'confidence': 0.0,
                'reasoning': f'Classifier error: {str(e)[:60]}',
                'metrics': {}
            }

    if cost_tracker['total'] > 0:
        print(f"   💰 Regime AI research cost: ${cost_tracker['total']:.4f}")

    return results, float(cost_tracker['total'])
