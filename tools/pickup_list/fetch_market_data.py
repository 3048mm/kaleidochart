import requests
import json
import pandas as pd
import os

def fetch_market_data():
    print("Downloading full US stock list from NASDAQ...")
    
    url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=0&download=true"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "application/json, text/plain, */*"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=60)
        response.raise_for_status()
        data = response.json()
        rows = data['data']['rows']
        
        # Cleanup and filtering
        df = pd.DataFrame(rows)
        
        def clean_val(val):
            if not val or val == "N/A": return 0.0
            return float(str(val).replace("$", "").replace(",", ""))

        df['marketCap_val'] = df['marketCap'].apply(clean_val)
        df['lastsale_val'] = df['lastsale'].apply(clean_val)
        
        # Apply approved criteria: AND condition
        # Market Cap > 100M AND Price > $3
        filtered_df = df[(df['marketCap_val'] > 100_000_000) & (df['lastsale_val'] > 3)].copy()
        
        # Keep relevant columns
        # symbol, name, marketCap, lastsale, sector, industry
        output_data = filtered_df[['symbol', 'name', 'marketCap', 'lastsale', 'sector', 'industry']].to_dict(orient='records')
        
        output_file = os.path.join(os.path.dirname(__file__), "market_data_filtered.json")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
            
        print(f"Successfully downloaded and filtered {len(output_data)} stocks.")
        print(f"Saved to {output_file}")
        
    except Exception as e:
        print(f"Error during fetching: {e}")

if __name__ == "__main__":
    fetch_market_data()
