import os
import sqlite3

db_path = "d:/My Documents/Programing/stocktool/data/stocktool.db"

def main():
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get the theme symbols in the Communication Services sector (VOX, IXP, SOCL, MRAD, GGME, HERO, _ESPT_, _MUSC_ etc.)
    # We will search based on tags = 'XLC' or specific list we discovered.
    target_themes = ['IXP', 'VOX', 'SOCL', 'MRAD', 'GGME', 'HERO', '_ESPT_', '_MUSC_']
    
    # We can fetch their info
    placeholders = ",".join([f"'{t}'" for t in target_themes])
    cursor.execute(f"""
        SELECT id, ticker, name 
        FROM symbols 
        WHERE category = 'テーマ' AND ticker IN ({placeholders})
    """)
    themes = cursor.fetchall()
    
    theme_info = {row[0]: {"ticker": row[1], "name": row[2], "stocks": set()} for row in themes}
    theme_ids = list(theme_info.keys())
    
    # Fetch all constituents for these themes
    theme_ids_str = ",".join([str(tid) for tid in theme_ids])
    cursor.execute(f"""
        SELECT tc.theme_id, s.ticker, s.name
        FROM theme_constituents tc
        JOIN symbols s ON tc.symbol_id = s.id
        WHERE tc.theme_id IN ({theme_ids_str})
    """)
    consts = cursor.fetchall()
    
    for tid, ticker, name in consts:
        theme_info[tid]["stocks"].add(ticker)
        
    print("=== Theme Constituent Summary ===")
    for tid, info in theme_info.items():
        print(f"Theme: {info['ticker']} ({info['name']}) -> {len(info['stocks'])} stocks")
        
    print("\n=== Overlap Matrix (Jaccard Index) ===")
    tickers = [info['ticker'] for info in theme_info.values()]
    print("      " + " ".join([f"{t:6}" for t in tickers]))
    for tid1, info1 in theme_info.items():
        row_str = f"{info1['ticker']:5} "
        for tid2, info2 in theme_info.items():
            s1 = info1['stocks']
            s2 = info2['stocks']
            union_len = len(s1.union(s2))
            if union_len == 0:
                jaccard = 0.0
            else:
                jaccard = len(s1.intersection(s2)) / union_len
            row_str += f"{jaccard*100:5.1f}% "
        print(row_str)

    print("\n=== Stocks Belonging to Multiple Themes ===")
    stock_themes = {}
    for tid, info in theme_info.items():
        for s in info['stocks']:
            stock_themes.setdefault(s, []).append(info['ticker'])
            
    multi_theme_stocks = {s: t for s, t in stock_themes.items() if len(t) > 1}
    print(f"Total unique stocks: {len(stock_themes)}")
    print(f"Stocks in multiple themes: {len(multi_theme_stocks)}")
    
    sorted_multi = sorted(multi_theme_stocks.items(), key=lambda x: len(x[1]), reverse=True)
    for stock, ths in sorted_multi[:30]:
        print(f"  - {stock:5} : in {len(ths)} themes -> {', '.join(ths)}")

    print("\n=== Specific Overlaps ===")
    # Print detail of overlaps between highly correlated themes, e.g. HERO, GGME, _ESPT_
    groups = [
        ('Gaming/Esports', ['GGME', 'HERO', '_ESPT_']),
        ('Streaming/Music', ['GGME', '_MUSC_']),
        ('Global/Broad', ['IXP', 'VOX'])
    ]
    for label, ths in groups:
        present_ths = [t for t in ths if t in tickers]
        if len(present_ths) < 2:
            continue
        print(f"\nGroup: {label} ({', '.join(present_ths)})")
        # Find intersection
        sets = [theme_info[tid]["stocks"] for tid in theme_ids if theme_info[tid]["ticker"] in present_ths]
        common = set.intersection(*sets)
        print(f"  Common stocks in ALL ({len(common)}): {', '.join(common)}")
        
        # Unique to each
        for t in present_ths:
            tid = [k for k, v in theme_info.items() if v["ticker"] == t][0]
            other_sets = [theme_info[oid]["stocks"] for oid in theme_ids if theme_info[oid]["ticker"] in present_ths and oid != tid]
            others_union = set.union(*other_sets) if other_sets else set()
            unique = theme_info[tid]["stocks"] - others_union
            print(f"  Unique to {t} ({len(unique)}): {', '.join(sorted(unique)[:20])}" + (f" ... and {len(unique)-20} more" if len(unique) > 20 else ""))

    conn.close()

if __name__ == '__main__':
    main()
