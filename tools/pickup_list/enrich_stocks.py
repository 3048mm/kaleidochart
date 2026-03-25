import json
import os

# 業種・セクターの日本語マッピング
# NASDAQスクリーナーの Industry/Sector 名に対応させる
INDUSTRY_MAP_JP = {
    # Technology
    "Semiconductors": "半導体",
    "Computer Manufacturing": "ハードウェア",
    "Computer Software: Prepackaged Software": "アプリソフト",
    "Computer Software: Programming, Data Processing": "ITサービス",
    "Radio/Television Broadcasting & Communications Equipment": "通信機器",
    "Electronic Components": "電子部品",
    "EDP Services": "ITサービス",
    
    # Health Care
    "Biotechnology: Pharmaceutical Preparations": "製薬・バイオ",
    "Biotechnology: Laboratory Analytical Instruments": "分析機器",
    "Medical/Dental Instruments": "医療機器",
    "Medical/Nursing Services": "医療サービス",
    "Hospital/Nursing Management": "医療経営",
    "Medical Specialities": "専門医療",
    
    # Finance
    "Investment Managers": "資産運用",
    "Commercial Banks": "銀行",
    "Savings Institutions": "金融・貯蓄",
    "Life Insurance": "生命保険",
    "Property-Casualty Insurers": "損害保険",
    "Investment Bankers/Brokers/Service": "証券・投資銀行",
    "Finance: Consumer Services": "消費者金融",
    "Real Estate Investment Trusts": "REIT",
    
    # Consumer
    "Department/Specialty Retail Stores": "小売",
    "Auto & Home Supply Stores": "カー用品・小売",
    "Apparel": "アパレル",
    "Restaurants": "外食",
    "Beverages (Production/Distribution)": "飲料",
    "Packaged Foods": "食品",
    
    # Industrials
    "Industrial Machinery/Components": "産業機械",
    "Aerospace": "航空・宇宙",
    "Military/Government/Technical": "防衛",
    "Auto Manufacturers": "自動車製造",
    "Aluminum": "アルミニウム",
    "Metal Fabrications": "金属加工",
    "Electrical Products": "電気製品",
    
    # Energy
    "Oil & Gas Production": "石油・ガス開発",
    "Integrated Oil and Gas": "総合エネルギー",
    "Oil Refining/Marketing": "石油精製・販売",
}

SECTOR_MAP_JP = {
    "Technology": "情報技術",
    "Health Care": "ヘルスケア",
    "Finance": "金融",
    "Consumer Discretionary": "一般消費財",
    "Industrials": "産業",
    "Real Estate": "不動産",
    "Energy": "エネルギー",
    "Consumer Staples": "生活必需品",
    "Materials": "素材",
    "Utilities": "公益事業",
    "Telecommunications": "通信",
}

# 業種からテーマタグへのマッピング (代表的なもの)
INDUSTRY_TO_TAGS = {
    "Semiconductors": "SOXX,SMH",
    "Biotechnology: Pharmaceutical Preparations": "IBB,XBI",
    "Computer Software: Prepackaged Software": "IGV,CLOU",
    "Investment Managers": "IAI,GPZ",
    "Real Estate Investment Trusts": "VNQ",
    "Oil & Gas Production": "FCG,XLE",
    "Industrial Machinery/Components": "EXI,XLI",
    "Aerospace": "ITA,PPA",
    "Military/Government/Technical": "ITA,_DFTS_",
}

def enrich_and_merge():
    base_dir = os.path.dirname(__file__)
    filtered_json = os.path.join(base_dir, "market_data_filtered.json")
    existing_stocks_path = r"d:\My Documents\Programing\stocktool\stocks.txt"
    
    # 1. 既存の stocks.txt を読み込み、既知の情報を辞書化
    existing_info = {}
    if os.path.exists(existing_stocks_path):
        with open(existing_stocks_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\r\n").split("\t")
                if len(parts) >= 5: # Industry, Name, Exchange, Ticker, Tags
                    ind, name, exch, ticker, tags = parts[:5]
                    existing_info[ticker] = {
                        "industry": ind, "name": name, "exchange": exch, "tags": tags
                    }
                elif len(parts) == 4: # 旧フォーマット対策
                    name, exch, ticker, tags = parts[:4]
                    existing_info[ticker] = {
                        "industry": "", "name": name, "exchange": exch, "tags": tags
                    }

    # 2. フィルタリング済み銘柄を読み込み
    with open(filtered_json, "r", encoding="utf-8") as f:
        new_candidates = json.load(f)

    # 3. 属性付与・マージ
    final_stocks = []
    seen_tickers = set()

    # まず既存を優先して追加 (ソートは後で行う)
    # ※既存の stocks.txt はすでにテーマ順なので、マージ後に既存の順番を尊重しつつ新規分を充填する
    
    for ticker, info in existing_info.items():
        if ticker not in seen_tickers:
            final_stocks.append({
                "ticker": ticker,
                "name": info["name"],
                "exchange": info["exchange"],
                "industry": info["industry"],
                "tags": info["tags"]
            })
            seen_tickers.add(ticker)

    # 次に新規分を処理
    for s in new_candidates:
        ticker = s["symbol"].strip()
        if ticker in seen_tickers:
            continue
            
        name = s["name"].replace(" Common Stock", "").replace(" Inc.", "").strip()
        raw_ind = s["industry"]
        raw_sector = s["sector"]
        
        # 業種日本語化
        industry_jp = INDUSTRY_MAP_JP.get(raw_ind, SECTOR_MAP_JP.get(raw_sector, "その他"))
        
        # 交換所推定 (簡易)
        exchange = "NASDAQ" if len(ticker) >= 4 else "NYSE"
        
        # タグ自動付与
        tags = INDUSTRY_TO_TAGS.get(raw_ind, "")
        
        final_stocks.append({
            "ticker": ticker,
            "name": name,
            "exchange": exchange,
            "industry": industry_jp,
            "tags": tags
        })
        seen_tickers.add(ticker)

    print(f"Total stocks merged: {len(final_stocks)}")
    
    # 中間結果を保存
    output_merged = os.path.join(base_dir, "market_data_enriched.json")
    with open(output_merged, "w", encoding="utf-8") as f:
        json.dump(final_stocks, f, ensure_ascii=False, indent=2)
    
    print(f"Enriched and merged data saved to {output_merged}")

if __name__ == "__main__":
    enrich_and_merge()
