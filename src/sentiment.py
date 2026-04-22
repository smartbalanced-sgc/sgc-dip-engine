"""
Claude API Sentiment Analysis
Per-stock narrative scoring with analyst grade cross-validation
"""

import os
from anthropic import Anthropic

ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
client = Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

def analyze_stock_sentiment(ticker, current_price, earnings_date, analyst_grade):
    """
    Call Claude API to score stock sentiment
    Cross-validates with analyst grade if available
    
    Returns: dict with sentiment_score (-5 to +5), narrative (brief explanation)
    """
    
    if not client:
        print(f"⚠️  Anthropic API key not set, skipping sentiment for {ticker}")
        return {
            'sentiment_score': 0.0,
            'narrative': "Sentiment unavailable (no API key)"
        }
    
    # Build context for Claude
    grade_context = ""
    if analyst_grade:
        action = analyst_grade.get('action', 'N/A')
        to_grade = analyst_grade.get('toGrade', 'N/A')
        price_action = analyst_grade.get('priceTargetAction', 'N/A')
        days_old = analyst_grade.get('days_old', 'N/A')
        
        grade_context = f"""
Recent analyst action ({days_old} days ago):
- Action: {action} (up/down/main/init/reit)
- Grade: {to_grade}
- Price target: {price_action}
"""
    
    context = f"""
You are analyzing {ticker} for short-term (60-day) price movement sentiment.

Current price: ${current_price:.2f}
Upcoming earnings: {earnings_date if earnings_date else 'None in next 60 days'}
{grade_context}

Based on recent news, market positioning, and narrative momentum, score this stock's near-term sentiment:
- Score: -5 (very bearish) to +5 (very bullish)
- Narrative: One sentence explaining the key driver

Respond in this exact format:
SCORE: [number]
NARRATIVE: [one sentence]
"""
    
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=150,
            messages=[
                {"role": "user", "content": context}
            ]
        )
        
        # Parse response
        text = response.content[0].text
        
        score_line = [line for line in text.split('\n') if 'SCORE:' in line]
        narrative_line = [line for line in text.split('\n') if 'NARRATIVE:' in line]
        
        score = 0.0
        narrative = "Neutral sentiment"
        
        if score_line:
            try:
                score = float(score_line[0].split('SCORE:')[1].strip())
            except:
                score = 0.0
        
        if narrative_line:
            narrative = narrative_line[0].split('NARRATIVE:')[1].strip()
        
        # Apply analyst grade modifier (cross-validation)
        if analyst_grade:
            action = analyst_grade.get('action')
            price_action = analyst_grade.get('priceTargetAction')
            
            modifier = 0.0
            if action == 'down':
                modifier = -0.3  # Downgrade signal
            elif action == 'up':
                modifier = +0.3  # Upgrade signal
            elif price_action == 'Lowers':
                modifier = -0.15  # Price target cut
            elif price_action == 'Raises':
                modifier = +0.15  # Price target raised
            
            # Adjust score
            score = max(-5.0, min(5.0, score + modifier))
            
            if modifier != 0:
                narrative += f" (Analyst {action}/{price_action} applied)"
        
        return {
            'sentiment_score': score,
            'narrative': narrative
        }
        
    except Exception as e:
        print(f"⚠️  Sentiment analysis failed for {ticker}: {e}")
        return {
            'sentiment_score': 0.0,
            'narrative': "Sentiment unavailable"
        }
