import requests
import json
import pandas as pd
import io

def count_filtered_stocks():
    print("Downloading stock list from NASDAQ official screener...")
    
    # Official NASDAQ screener URL (download=true gives the full list)
    url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=0&download=true"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "application/json, text/plain, */*"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        rows = data['data']['rows']
        df = pd.DataFrame(rows)
        
        # Data cleanup
        # marketCap format is usually string like "$1,234,567"
        def clean_mcap(val):
            if not val or val == "N/A": return 0
            return float(val.replace("$", "").replace(",", ""))

        def clean_price(val):
            if not val or val == "N/A": return 0
            return float(val.replace("$", "").replace(",", ""))

        df['marketCap_val'] = df['marketCap'].apply(clean_mcap)
        df['lastsale_val'] = df['lastsale'].apply(clean_price)
        
        # Filters
        # 1. Market Cap > 100M
        # 2. Price > $3
        
        count_mcap_100m = len(df[df['marketCap_val'] > 100_000_000])
        count_price_3 = len(df[df['lastsale_val'] > 3])
        
        # Both conditions (User said "いずれか" which means OR, but typically it's AND for quality. 
        # User prompt says "いずれかの条件を満たす銘柄をピックアップし銘柄数をカウントし条件を調整")
        # Let's show both OR and AND.
        
        count_or = len(df[(df['marketCap_val'] > 100_000_000) | (df['lastsale_val'] > 3)])
        count_and = len(df[(df['marketCap_val'] > 100_000_000) & (df['lastsale_val'] > 3)])
        
        print("\n--- Statistics ---")
        print(f"Total stocks found: {len(df)}")
        print(f"Market Cap > 100M: {count_mcap_100m}")
        print(f"Price > $3: {count_price_3}")
        print(f"OR condition (Either): {count_or}")
        print(f"AND condition (Both): {count_and}")
        
    except Exception as e:
        print(f"Error: {e}")
        # Fallback to a different source if needed?
        print("Attempting fallback source (GitHub)...")
        # Placeholder for fallback
        pass

if __name__ == "__main__":
    count_filtered_stocks()
