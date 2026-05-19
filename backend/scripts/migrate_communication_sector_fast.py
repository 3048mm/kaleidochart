import os
import sqlite3
from datetime import datetime

db_path = "d:/My Documents/Programing/stocktool/data/stocktool.db"

def update_tags(tags_str, mappings_to_apply):
    if not tags_str:
        return tags_str
        
    tags = [t.strip() for t in tags_str.split(',') if t.strip()]
    updated_tags = []
    
    for t in tags:
        matched = False
        for old_tag, (new_tag, extra_tag) in mappings_to_apply.items():
            if t == old_tag:
                updated_tags.append(new_tag)
                if extra_tag:
                    updated_tags.append(extra_tag)
                matched = True
                break
        if not matched:
            updated_tags.append(t)
            
    seen = set()
    clean_tags = []
    for t in updated_tags:
        if t not in seen:
            seen.add(t)
            clean_tags.append(t)
            
    return ",".join(clean_tags)

def main():
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        return

    print("Connecting to database...")
    conn = sqlite3.connect(db_path, timeout=10.0)
    cursor = conn.cursor()
    
    # Map definition for migration
    tag_mappings = {
        'IXP': ('VOX', 'telecom'),
        'HERO': ('GGME', 'gaming'),
        '_ESPT_': ('GGME', 'esports'),
        'ESPT': ('GGME', 'esports'),
        '_MUSC_': ('GGME', 'music'),
        'MUSC': ('GGME', 'music'),
        'MRAD': ('SOCL', 'advertising')
    }
    
    themes_to_deactivate = ['IXP', 'HERO', '_ESPT_', '_MUSC_', 'MRAD']
    
    print("\n--- Starting Fast Database Transaction (No File Copy) ---")
    
    try:
        # Retrieve all active individual stocks (using '個別' category)
        cursor.execute("SELECT id, ticker, name, tags FROM symbols WHERE active = 1 AND category = '個別'")
        stocks = cursor.fetchall()
        
        updated_count = 0
        for s_id, ticker, name, tags_str in stocks:
            if not tags_str:
                continue
                
            new_tags = update_tags(tags_str, tag_mappings)
            if new_tags != tags_str:
                cursor.execute("UPDATE symbols SET tags = ?, updated_at = ? WHERE id = ?", (new_tags, datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'), s_id))
                updated_count += 1
                print(f"Updated {ticker:5} | Tags: {tags_str} -> {new_tags}")
                
        print(f"\nSuccessfully migrated tags for {updated_count} individual stocks.")
        
        # Deactivate overlapping themes (Soft Delete)
        placeholders = ",".join([f"'{t}'" for t in themes_to_deactivate])
        cursor.execute(f"UPDATE symbols SET active = 0, updated_at = ? WHERE category = 'テーマ' AND ticker IN ({placeholders})", (datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),))
        deactivated_count = cursor.rowcount
        print(f"Deactivated {deactivated_count} redundant themes: {', '.join(themes_to_deactivate)}")
        
        conn.commit()
        print("\nDatabase transaction COMMITTED successfully.")
        
    except Exception as e:
        conn.rollback()
        print(f"\n[ERROR] Migration failed: {e}. Transaction rolled back.")
        conn.close()
        return

    # Post-migration Verification
    print("\n--- Post-migration Verification ---")
    cursor.execute("SELECT ticker, name, active FROM symbols WHERE category = 'テーマ' AND ticker IN ('VOX', 'GGME', 'SOCL', 'IXP', 'HERO', 'MRAD')")
    rows = cursor.fetchall()
    print("Theme Status:")
    for row in rows:
        print(f"  Theme: {row[0]:6} | Name: {row[1]:25} | Active: {row[2]}")
        
    conn.close()
    print("\nMigration Script Finished.")

if __name__ == '__main__':
    main()
