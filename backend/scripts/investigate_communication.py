import os
import sqlite3

db_path = "d:/My Documents/Programing/stocktool/data/stocktool.db"

def main():
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("=== ALL THEMES ===")
    cursor.execute("SELECT id, ticker, name, theme_type, tags FROM symbols WHERE category = 'テーマ' ORDER BY ticker")
    rows = cursor.fetchall()
    for row in rows:
        print(f"ID: {row[0]} | Ticker: {row[1]} | Name: {row[2]} | Type: {row[3]} | Tags: {row[4]}")

    print("\n=== ALL SECTORS ===")
    cursor.execute("SELECT id, ticker, name, theme_type, tags FROM symbols WHERE category = 'セクタ' ORDER BY ticker")
    rows = cursor.fetchall()
    for row in rows:
        print(f"ID: {row[0]} | Ticker: {row[1]} | Name: {row[2]} | Type: {row[3]} | Tags: {row[4]}")

    print("\n=== SEARCH FOR COMMUNICATION IN TAGS ===")
    # Look for tags like 'communication', 'telecom', 'media', 'social', 'streaming', etc.
    search_terms = ['comm', 'tele', 'media', 'social', 'stream', 'broad', 'internet', 'vox', 'xlc']
    query_parts = " OR ".join([f"tags LIKE '%{t}%'" for t in search_terms])
    cursor.execute(f"SELECT id, ticker, name, category, tags FROM symbols WHERE {query_parts} LIMIT 100")
    rows = cursor.fetchall()
    print(f"Found {len(rows)} symbols matching search terms:")
    for row in rows:
        print(f"ID: {row[0]} | Ticker: {row[1]} | Name: {row[2]} | Category: {row[3]} | Tags: {row[4]}")

    conn.close()

if __name__ == '__main__':
    main()
