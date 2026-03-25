import json
import os

# ============================================================
# 包括的 INDUSTRY_TO_TAGS マッピング (全196業種対応)
# NASDAQスクリーナーの "industry [sector]" → テーマETFタグ
# ============================================================

INDUSTRY_TO_TAGS = {
    # ── 金融 (Finance) ──────────────────────────────────
    "Major Banks": "KBWB",
    "Commercial Banks": "KRE",
    "Banks": "KRE",
    "Savings Institutions": "KRE",
    "Investment Managers": "IAI,GPZ",
    "Investment Bankers/Brokers/Service": "KCE",
    "Finance: Consumer Services": "FINX,IPAY",
    "Finance Companies": "FINX",
    "Finance/Investors Services": "IAI",
    "Property-Casualty Insurers": "IAK",
    "Life Insurance": "IAK",
    "Specialty Insurers": "IAK",
    "Accident &Health Insurance": "IAK",
    "Real Estate Investment Trusts": "VNQ",
    "Real Estate": "IYR",
    "Trusts Except Educational Religious and Charitable": "IAI",
    "Diversified Financial Services": "FINX",
    "Blank Checks": "FINX",

    # ── テクノロジー (Technology) ────────────────────────
    "Semiconductors": "SOXX,SMH",
    "Computer Manufacturing": "IYW",
    "Computer Software: Prepackaged Software": "IGV,CLOU",
    "Computer Software: Programming Data Processing": "IGV,CLOU",
    "Computer Software: Programming, Data Processing": "IGV,CLOU",
    "EDP Services": "IGV,CLOU",
    "Radio/Television Broadcasting & Communications Equipment": "IXP",
    "Radio And Television Broadcasting And Communications Equipment": "IXP",
    "Electronic Components": "SOXX,_ELEC_",
    "Electronic Components: Resistors": "SOXX,_ELEC_",
    "Retail: Computer Software & Peripheral Equipment": "IGV",
    "Electrical Products": "_ELEC_,POWR",
    "Consumer Electronics/Appliances": "XRT",
    "Consumer Electronics/Video Chains": "XRT",
    "Electronics Distribution": "XRT",

    # ── ヘルスケア (Health Care) ─────────────────────────
    "Biotechnology: Pharmaceutical Preparations": "IBB,XBI",
    "Biotechnology: Biological Products (No Diagnostic Substances)": "IBB,XBI",
    "Biotechnology: Commercial Physical & Biological Resarch": "IBB,ARKG",
    "Biotechnology: Laboratory Analytical Instruments": "IHI",
    "Biotechnology: Electromedical & Electrotherapeutic Apparatus": "IHI",
    "Medical/Dental Instruments": "IHI",
    "Medical Specialities": "XHS",
    "Medical/Nursing Services": "IHF",
    "Hospital/Nursing Management": "IHF",
    "Other Pharmaceuticals": "XPH",
    "Pharmaceuticals and Biotechnology": "IBB",
    "Ophthalmic Goods": "IHI",
    "Misc Health and Biotechnology Services": "XHS",
    "Medical Electronics": "IHI",
    "Precision Instruments": "IHI",

    # ── エネルギー (Energy) ──────────────────────────────
    "Oil & Gas Production": "FCG,XLE",
    "Integrated Oil and Gas": "CRAK,XLE",
    "Oil Refining/Marketing": "CRAK,XLE",
    "Oilfield Services/Equipment": "IEZ,XLE",
    "Oil and Gas Field Machinery": "IEZ,XLE",
    "Natural Gas Distribution": "AMLP,XLE",
    "Coal Mining": "XLE",
    "Electric Utilities: Central": "IDU,UTES",
    "Power Generation": "POWR,UTES",
    "Water Supply": "PHO",

    # ── 産業 (Industrials) ──────────────────────────────
    "Industrial Machinery/Components": "EXI,XLI",
    "Aerospace": "ITA,PPA",
    "Military/Government/Technical": "ITA,_DFTS_",
    "Auto Manufacturers": "CARZ,DRIV",
    "Auto Parts:O.E.M.": "CARZ,DRIV",
    "Automotive Aftermarket": "CARZ",
    "Steel": "SLX",
    "Steel/Iron Ore": "SLX",
    "Aluminum": "SLX",
    "Metal Fabrications": "EXI",
    "Trucking Freight/Courier Services": "IYT",
    "Air Freight/Delivery Services": "IYT",
    "Marine Transportation": "SEA",
    "Railroads": "IYT",
    "Transportation Services": "IYT",
    "Integrated Freight & Logistics": "IYT",
    "Engineering & Construction": "IFRA,PAVE",
    "Building Materials": "PKB",
    "Building Products": "PKB",
    "Homebuilding": "ITB,XHB",
    "Containers/Packaging": "EXI",
    "Diversified Commercial Services": "EXI",
    "Professional Services": "EXI",
    "Industrial Specialties": "EXI",
    "Fluid Controls": "EXI",
    "Pollution Control Equipment": "EVX",
    "Environmental Services": "EVX",
    "Ordnance And Accessories": "ITA,_DFTS_",
    "Mining & Quarrying of Nonmetallic Minerals (No Fuels)": "PICK",
    "Water Sewer Pipeline Comm & Power Line Construction": "IFRA,GRID",
    "Construction/Ag Equipment/Trucks": "PAVE",
    "General Bldg Contractors - Nonresidential Bldgs": "IFRA",
    "Misc Corporate Leasing Services": "EXI",
    "Rental/Leasing Companies": "EXI",
    "Rental & Leasing Services": "EXI",

    # ── 素材 (Basic Materials / Materials) ──────────────
    "Precious Metals": "GDX,SIL",
    "Metal Mining": "PICK",
    "Other Metals and Minerals": "PICK,REMX",
    "Major Chemicals": "XLB",
    "Agricultural Chemicals": "MOO",
    "Specialty Chemicals": "XLB",
    "Paints/Coatings": "XLB",
    "Forest Products": "WOOD",
    "Paper": "WOOD",
    "Mining": "PICK",

    # ── 一般消費財 (Consumer Discretionary) ──────────────
    "Department/Specialty Retail Stores": "XRT",
    "Auto & Home Supply Stores": "XRT",
    "Other Specialty Stores": "XRT",
    "Catalog/Specialty Distribution": "XRT,ONLN",
    "Apparel": "XRT,KLXY",
    "Clothing/Shoe/Accessory Stores": "XRT",
    "Shoe Manufacturing": "XRT",
    "Textiles": "XRT",
    "Restaurants": "EATZ",
    "Hotels/Resorts": "BEDZ,BJK",
    "Services-Misc. Amusement & Recreation": "PEJ",
    "Movies/Entertainment": "GGME",
    "Recreational Games/Products/Toys": "HERO",
    "Broadcasting": "IXP",
    "Cable & Other Pay Television Services": "IXP,VOX",
    "Telecommunications Equipment": "IXP,VOX",
    "Publishing": "XLC",
    "Newspapers/Magazines": "XLC",
    "Books": "XLC",
    "Motor Vehicles": "CARZ",
    "Home Furnishings": "XHB",
    "Consumer Specialties": "XRT",
    "Food Distributors": "PBJ",
    "Package Goods/Cosmetics": "FTXG",
    "Retail-Auto Dealers and Gas Stations": "XRT",
    "Retail-Drug Stores and Proprietary Stores": "XRT",
    "Advertising Agencies": "MRAD",
    "Durable Goods": "XRT",
    "Wholesale Distributors": "XRT",
    "Miscellaneous manufacturing industries": "EXI",
    "Garments and Clothing": "XRT",
    "Building operators": "IYR",
    "Professional and commerical equipment": "EXI",
    "Tools/Hardware": "EXI",

    # ── 生活必需品 (Consumer Staples) ───────────────────
    "Beverages (Production/Distribution)": "PBJ",
    "Packaged Foods": "PBJ",
    "Specialty Foods": "PBJ",
    "Farming/Seeds/Milling": "VEGI,MOO",
    "Meat/Poultry/Fish": "PBJ,_MEAT_",
    "Tobacco": "XLP",

    # ── 通信 (Telecommunications) ───────────────────────
    "Telecom Services": "VOX,IXP",

    # ── その他 ──────────────────────────────────────────
    "Office Equipment/Supplies/Services": "EXI",
    "Multi-Sector Companies": "EXI",
    "Miscellaneous": "",
    "Conglomerates": "EXI",
    "Specialty Business Services": "EXI",
    "Consulting Services": "EXI",
    "Scientific & Technical Instruments": "EXI",
    "Airports & Air Services": "JETS",
    "Other Consumer Services": "XRT",
    "Biotechnology: In Vitro & In Vivo Diagnostic Substances": "IHI,IBB",
    "Food Chains": "EATZ,PBJ",
    "Advertising": "MRAD",
    "Oil/Gas Transmission": "AMLP,XLE",
    "Medicinal Chemicals and Botanical Products": "IBB,XPH",
    "Plastic Products": "EXI",
}

# 業種日本語マッピング
INDUSTRY_MAP_JP = {
    "Semiconductors": "半導体",
    "Computer Manufacturing": "ハードウェア",
    "Computer Software: Prepackaged Software": "アプリソフト",
    "Computer Software: Programming, Data Processing": "ITサービス",
    "Computer Software: Programming Data Processing": "ITサービス",
    "Radio/Television Broadcasting & Communications Equipment": "通信機器",
    "Radio And Television Broadcasting And Communications Equipment": "通信機器",
    "Electronic Components": "電子部品",
    "Electronic Components: Resistors": "電子部品",
    "EDP Services": "ITサービス",
    "Retail: Computer Software & Peripheral Equipment": "IT小売",
    "Electrical Products": "電気製品",
    "Biotechnology: Pharmaceutical Preparations": "製薬・バイオ",
    "Biotechnology: Biological Products (No Diagnostic Substances)": "バイオ製品",
    "Biotechnology: Commercial Physical & Biological Resarch": "バイオ研究",
    "Biotechnology: Laboratory Analytical Instruments": "分析機器",
    "Biotechnology: Electromedical & Electrotherapeutic Apparatus": "電子医療機器",
    "Medical/Dental Instruments": "医療機器",
    "Medical Specialities": "専門医療",
    "Medical/Nursing Services": "医療サービス",
    "Hospital/Nursing Management": "医療経営",
    "Other Pharmaceuticals": "医薬品",
    "Pharmaceuticals and Biotechnology": "製薬",
    "Ophthalmic Goods": "眼科用品",
    "Misc Health and Biotechnology Services": "ヘルスケアサービス",
    "Medical Electronics": "医療エレクトロニクス",
    "Precision Instruments": "精密機器",
    "Major Banks": "メガバンク",
    "Commercial Banks": "銀行",
    "Banks": "銀行",
    "Savings Institutions": "金融・貯蓄",
    "Investment Managers": "資産運用",
    "Investment Bankers/Brokers/Service": "証券・投資銀行",
    "Finance: Consumer Services": "消費者金融",
    "Finance Companies": "ファイナンス会社",
    "Finance/Investors Services": "金融サービス",
    "Property-Casualty Insurers": "損害保険",
    "Life Insurance": "生命保険",
    "Specialty Insurers": "特殊保険",
    "Accident &Health Insurance": "傷害・健康保険",
    "Real Estate Investment Trusts": "REIT",
    "Real Estate": "不動産",
    "Trusts Except Educational Religious and Charitable": "信託",
    "Diversified Financial Services": "総合金融",
    "Blank Checks": "SPAC",
    "Oil & Gas Production": "石油・ガス開発",
    "Integrated Oil and Gas": "総合エネルギー",
    "Oil Refining/Marketing": "石油精製・販売",
    "Oilfield Services/Equipment": "油田サービス",
    "Oil and Gas Field Machinery": "油田機器",
    "Natural Gas Distribution": "ガス流通",
    "Coal Mining": "石炭",
    "Electric Utilities: Central": "電力",
    "Power Generation": "発電",
    "Water Supply": "水インフラ",
    "Industrial Machinery/Components": "産業機械",
    "Aerospace": "航空・宇宙",
    "Military/Government/Technical": "防衛",
    "Auto Manufacturers": "自動車製造",
    "Auto Parts:O.E.M.": "自動車部品",
    "Automotive Aftermarket": "自動車アフターマーケット",
    "Steel": "鉄鋼",
    "Steel/Iron Ore": "鉄鋼・鉄鉱",
    "Aluminum": "アルミニウム",
    "Metal Fabrications": "金属加工",
    "Trucking Freight/Courier Services": "陸運",
    "Air Freight/Delivery Services": "空運・配送",
    "Marine Transportation": "海運",
    "Railroads": "鉄道",
    "Transportation Services": "運輸サービス",
    "Integrated Freight & Logistics": "物流",
    "Engineering & Construction": "建設・エンジ",
    "Building Materials": "建材",
    "Building Products": "建築製品",
    "Homebuilding": "住宅建設",
    "Containers/Packaging": "容器・包装",
    "Diversified Commercial Services": "ビジネスサービス",
    "Professional Services": "専門サービス",
    "Industrial Specialties": "産業特殊品",
    "Fluid Controls": "流体制御",
    "Pollution Control Equipment": "環境機器",
    "Environmental Services": "環境サービス",
    "Ordnance And Accessories": "兵器・付属品",
    "Mining & Quarrying of Nonmetallic Minerals (No Fuels)": "非金属鉱業",
    "Water Sewer Pipeline Comm & Power Line Construction": "パイプライン建設",
    "Construction/Ag Equipment/Trucks": "建機・農機",
    "General Bldg Contractors - Nonresidential Bldgs": "建設業",
    "Misc Corporate Leasing Services": "リース・サービス",
    "Rental/Leasing Companies": "レンタル・リース",
    "Rental & Leasing Services": "レンタル・リース",
    "Precious Metals": "貴金属鉱山",
    "Metal Mining": "金属鉱業",
    "Other Metals and Minerals": "その他金属",
    "Major Chemicals": "化学",
    "Agricultural Chemicals": "農業化学",
    "Specialty Chemicals": "特殊化学",
    "Paints/Coatings": "塗料・コーティング",
    "Forest Products": "林産品",
    "Paper": "製紙",
    "Mining": "鉱業",
    "Department/Specialty Retail Stores": "小売",
    "Auto & Home Supply Stores": "カー用品・小売",
    "Other Specialty Stores": "専門小売",
    "Catalog/Specialty Distribution": "通販",
    "Apparel": "アパレル製造",
    "Clothing/Shoe/Accessory Stores": "衣料品店",
    "Shoe Manufacturing": "靴製造",
    "Textiles": "繊維",
    "Restaurants": "外食",
    "Hotels/Resorts": "ホテル・リゾート",
    "Services-Misc. Amusement & Recreation": "レジャー",
    "Movies/Entertainment": "映画・エンタメ",
    "Recreational Games/Products/Toys": "ゲーム・玩具",
    "Broadcasting": "放送",
    "Cable & Other Pay Television Services": "ケーブルTV",
    "Telecommunications Equipment": "通信機器",
    "Telecom Services": "通信サービス",
    "Publishing": "出版",
    "Newspapers/Magazines": "新聞・雑誌",
    "Books": "書籍",
    "Motor Vehicles": "車両",
    "Home Furnishings": "家具・インテリア",
    "Consumer Specialties": "消費者向け特殊品",
    "Food Distributors": "食品流通",
    "Package Goods/Cosmetics": "日用品・化粧品",
    "Retail-Auto Dealers and Gas Stations": "自動車ディーラー",
    "Retail-Drug Stores and Proprietary Stores": "ドラッグストア",
    "Advertising Agencies": "広告",
    "Durable Goods": "耐久消費財",
    "Wholesale Distributors": "卸売",
    "Miscellaneous manufacturing industries": "雑製造",
    "Garments and Clothing": "衣料品製造",
    "Building operators": "ビル管理",
    "Professional and commerical equipment": "業務用機器",
    "Tools/Hardware": "工具・金物",
    "Beverages (Production/Distribution)": "飲料",
    "Packaged Foods": "食品",
    "Specialty Foods": "特殊食品",
    "Farming/Seeds/Milling": "農業",
    "Meat/Poultry/Fish": "食肉・水産",
    "Tobacco": "タバコ",
    "Office Equipment/Supplies/Services": "事務用品",
    "Multi-Sector Companies": "複合企業",
    "Miscellaneous": "その他",
    "Conglomerates": "コングロマリット",
    "Specialty Business Services": "特殊ビジネスサービス",
    "Consulting Services": "コンサルティング",
    "Scientific & Technical Instruments": "科学機器",
    "Airports & Air Services": "空港サービス",
    "Consumer Electronics/Appliances": "家電",
    "Consumer Electronics/Video Chains": "家電量販",
    "Electronics Distribution": "電子機器流通",
    "Other Consumer Services": "消費者サービス",
    "Biotechnology: In Vitro & In Vivo Diagnostic Substances": "体外診断",
    "Food Chains": "外食チェーン",
    "Advertising": "広告",
    "Oil/Gas Transmission": "石油・ガス輸送",
    "Medicinal Chemicals and Botanical Products": "医薬化学",
    "Plastic Products": "プラスチック製品",
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
    "Basic Materials": "素材",
    "Utilities": "公益事業",
    "Telecommunications": "通信",
    "Miscellaneous": "雑",
}

# 銘柄名の修正マッピング
NAME_FIXES = {
    "MSTR": "MicroStrategy",
    "HUT": "Hut 8",
    "RIOT": "Riot Platforms",
    "TSM": "TSMC (Taiwan Semiconductor)",
    "ASML": "ASML Holding",
    "MARA": "MARA Holdings",
}

def finalize_stocks():
    base_dir = os.path.dirname(__file__)
    filtered_json = os.path.join(base_dir, "market_data_filtered.json")
    themes_path = r"d:\My Documents\Programing\stocktool\tmp\themes.txt"
    stocks_path = r"d:\My Documents\Programing\stocktool\tmp\stocks.txt"
    
    # 1. Themes順序
    theme_order = {}
    with open(themes_path, "r", encoding="utf-8") as f:
        order = 0
        for line in f:
            parts = line.split("\t")
            if len(parts) >= 3 and parts[2].strip():
                t_ticker = parts[2].strip()
                if t_ticker not in theme_order:
                    theme_order[t_ticker] = order
                    order += 1

    # 2. 既存の stocks.txt (タグを継承)
    existing_stocks = {}
    if os.path.exists(stocks_path):
        with open(stocks_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\r\n").split("\t")
                if len(parts) >= 4:
                    ticker = parts[-2].strip()
                    existing_stocks[ticker] = parts

    # 3. 公式データ
    with open(filtered_json, "r", encoding="utf-8") as f:
        official_data = json.load(f)

    final_dict = {}
    seen = set()

    for s in official_data:
        ticker = s["symbol"].strip()
        if ticker in seen: continue
        
        name = NAME_FIXES.get(ticker, 
            s["name"].replace(" Common Stock", "").replace(" Inc.", "")
            .replace(" Corp.", "").replace(" Incorporated", "")
            .replace(" Class A", "").strip())
        raw_ind = s["industry"].strip()
        raw_sector = s["sector"].strip()
        
        industry_jp = INDUSTRY_MAP_JP.get(raw_ind, SECTOR_MAP_JP.get(raw_sector, "その他"))
        exchange = "NASDAQ" if len(ticker) >= 4 else "NYSE"
        
        # タグ: 自動マッピングで生成
        auto_tags = INDUSTRY_TO_TAGS.get(raw_ind, "")
        
        final_dict[ticker] = {
            "industry": industry_jp, "name": name, "exchange": exchange, 
            "ticker": ticker, "tags": auto_tags
        }
        seen.add(ticker)

    # 4. 既存データのタグを優先マージ (手動設定を上書きしない)
    for ticker, parts in existing_stocks.items():
        if len(parts) >= 5:
            ind, name, exch, t, tags = parts[:5]
        else:
            name, exch, t, tags = parts[:4]
            ind = "未分類"
            
        if ticker in final_dict:
            # 既存に手動タグがあればそちらを優先
            if tags.strip():
                final_dict[ticker]["tags"] = tags
        else:
            # 公式リストにない銘柄(OTC等)を保持
            final_dict[ticker] = {
                "industry": ind, "name": name, "exchange": exch, "ticker": ticker, "tags": tags
            }

    final_list = list(final_dict.values())

    # 5. ソート
    def sort_key(x):
        tags = [t.strip() for t in x["tags"].split(",") if t.strip()]
        primary = tags[0] if tags else "ZZZ_UNMAPPED"
        order = theme_order.get(primary, 999999)
        return (order, x["ticker"])

    final_list.sort(key=sort_key)

    # 6. 書き出し
    output_lines = []
    current_theme = None
    for s in final_list:
        tags = [t.strip() for t in s["tags"].split(",") if t.strip()]
        primary = tags[0] if tags else "ZZZ_UNMAPPED"
        
        if primary != current_theme:
            if current_theme is not None:
                output_lines.append("\t\t\t\t\r\n")
            current_theme = primary
            
        line = f"{s['industry']}\t{s['name']}\t{s['exchange']}\t{s['ticker']}\t{s['tags']}\r\n"
        output_lines.append(line)

    with open(stocks_path, "w", encoding="utf-8", newline="") as f:
        f.writelines(output_lines)

    # 7. カバレッジ表示
    tagged = sum(1 for s in final_list if s["tags"].strip())
    total = len(final_list)
    print(f"Final Count: {total}")
    print(f"Tagged: {tagged} ({tagged*100//total}%)")
    print(f"Untagged: {total - tagged} ({(total-tagged)*100//total}%)")

if __name__ == "__main__":
    finalize_stocks()
