"""
Claude API Sentiment Analysis with Web Search Enrichment (Session 3)
Provides stock-specific sentiment scoring grounded in recent news/developments
"""
 
import os
 
def get_client():
    """Initialize Anthropic client only when needed"""
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
 
 
def analyze_stock_sentiment(ticker, current_price, earnings_date, analyst_grade, company_name=None, sector=None):
    """
    Call Claude API with web search enrichment to score stock sentiment.
    
    Args:
        ticker: Stock symbol
        current_price: Current price
        earnings_date: Upcoming earnings date (or None)
        analyst_grade: Recent analyst action (or None)
        company_name: Full company name (extracted from profile)
        sector: Sector name
    
    Returns: dict with sentiment_score (-5 to +5), narrative (brief explanation), cost
    """
    
    client = get_client()
    
    if not client:
        return {
            'sentiment_score': 0.0,
            'narrative': "Sentiment unavailable (no API key)",
            'cost': 0.0
        }
    
    # Build company context
    company_desc = company_name if company_name else ticker
    sector_context = f" ({sector})" if sector else ""
    
    total_cost = 0.0
    
    try:
        # STEP 1: Web search for recent developments (last 30 days)
        search_prompt = f"""Search for recent news and developments (last 30 days) about {company_desc}{sector_context}.
 
Focus on:
- Quarterly earnings results and guidance
- Product launches or major announcements
- Analyst upgrades/downgrades
- Competitive moves or market share changes
- Regulatory news or major contracts
- Management changes or strategic shifts
 
Provide 3-5 key recent facts that would impact stock price in next 60 days."""
 
        search_response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search"
            }],
            messages=[{"role": "user", "content": search_prompt}]
        )
        
        # Extract search results
        research_context = []
        for block in search_response.content:
            if block.type == "text":
                research_context.append(block.text)
        
        research_text = "\n".join(research_context) if research_context else "No recent developments found."
        
        # Track cost (rough estimate: search = ~$0.05-0.10)
        total_cost += 0.08
        
        # STEP 2: Score sentiment with enriched context
        # Build analyst grade context
        grade_context = ""
        if analyst_grade:
            action = analyst_grade.get('action', 'N/A')
            to_grade = analyst_grade.get('toGrade', 'N/A')
            price_action = analyst_grade.get('priceTargetAction', 'N/A')
            days_old = analyst_grade.get('days_old', 'N/A')
            
            grade_context = f"""
Recent analyst action ({days_old} days ago):
- Action: {action}
- Grade: {to_grade}
- Price target: {price_action}
"""
        
        sentiment_prompt = f"""You are analyzing {ticker} ({company_desc}{sector_context}) for 60-day price movement sentiment.
 
STRUCTURED DATA:
- Current price: ${current_price:.2f}
- Upcoming earnings: {earnings_date if earnings_date else 'None in next 60 days'}
{grade_context}
 
RECENT DEVELOPMENTS (last 30 days):
{research_text}
 
Based on ALL available information above, score this stock's near-term (60-day) sentiment:
- Score: -5 (very bearish) to +5 (very bullish)
- Narrative: One sentence explaining the key driver using SPECIFIC recent facts
 
CRITICAL RULES:
1. Use concrete facts from recent developments, not generic statements
2. If bullish, cite what's driving optimism (e.g., "Azure AI revenue beat by 30%")
3. If bearish, cite the specific concern (e.g., "Margin compression from competitive pricing")
4. If neutral, cite mixed signals or lack of catalysts
5. Match score intensity to strength of evidence: minor positive = +1 to +2, major catalyst = +4 to +5
 
Format (respond ONLY with these two lines):
SCORE: [number from -5 to +5]
NARRATIVE: [one sentence with specific recent facts, max 100 chars]"""
        
        sentiment_response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{"role": "user", "content": sentiment_prompt}]
        )
        
        # Parse response
        text = sentiment_response.content[0].text
        
        score_line = [line for line in text.split('\n') if 'SCORE:' in line]
        narrative_line = [line for line in text.split('\n') if 'NARRATIVE:' in line]
        
        score = 0.0
        narrative = "Neutral sentiment"
        
        if score_line:
            try:
                score = float(score_line[0].split('SCORE:')[1].strip())
                # Clamp to valid range
                score = max(-5.0, min(5.0, score))
            except:
                score = 0.0
        
        if narrative_line:
            narrative = narrative_line[0].split('NARRATIVE:')[1].strip()
        
        # Track cost (sentiment call = ~$0.02-0.03)
        total_cost += 0.02
        
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
            
            if modifier != 0:
                score = max(-5.0, min(5.0, score + modifier))
                narrative += f" (Analyst {action}/{price_action} applied)"
        
        return {
            'sentiment_score': score,
            'narrative': narrative,
            'cost': total_cost
        }
        
    except Exception as e:
        print(f"⚠️  Sentiment analysis failed for {ticker}: {e}")
        return {
            'sentiment_score': 0.0,
            'narrative': f"Sentiment unavailable ({str(e)[:50]})",
            'cost': total_cost
        }
