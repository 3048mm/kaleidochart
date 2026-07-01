import os
import sys
import tomllib
from datetime import datetime

# Windows encoding safety
import codecs
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

# Add project root for config.toml access
project_root = os.path.dirname(backend_dir)
if project_root not in sys.path:
    sys.path.append(project_root)

from db.database import init_db, get_db
from db.models import Symbol, ThemeConstituent

def load_config():
    config_path = os.path.join(project_root, "config.toml")
    with open(config_path, "rb") as f:
        return tomllib.load(f)

def generate_report():
    config = load_config()
    db_path = config["system"]["db_path"]
    init_db(db_path)
    
    output_dir = os.path.join(project_root, "output")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    report_file_path = os.path.join(output_dir, "theme_report.txt")
    
    report_lines = []
    
    def log(message=""):
        print(message)
        report_lines.append(message)
        
    log("=========================================================")
    log("          THEME CONSTITUENTS REPORT (テーマ銘柄数レポート)")
    log("=========================================================")
    log(f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"Database:     {os.path.abspath(db_path)}")
    log("=========================================================\n")
    
    with get_db() as db:
        # Get all themes (category == 'テーマ')
        themes = db.query(Symbol).filter(Symbol.category == 'テーマ').order_by(Symbol.ticker).all()
        
        # Build constituents counts
        constituent_counts = {}
        active_constituent_counts = {}
        constituents_list = {}
        
        for t in themes:
            # Query constituents
            const_symbols = db.query(Symbol).join(
                ThemeConstituent, ThemeConstituent.symbol_id == Symbol.id
            ).filter(ThemeConstituent.theme_id == t.id).all()
            
            constituent_counts[t.id] = len(const_symbols)
            active_constituent_counts[t.id] = sum(1 for s in const_symbols if s.active == 1)
            constituents_list[t.id] = [s.ticker for s in const_symbols]
            
        # Summary statistics
        total_themes = len(themes)
        active_themes = sum(1 for t in themes if t.active == 1)
        inactive_themes = total_themes - active_themes
        
        themes_with_constituents = sum(1 for t in themes if constituent_counts[t.id] > 0)
        themes_without_constituents = total_themes - themes_with_constituents
        
        # Unique active constituents total
        unique_constituents = db.query(ThemeConstituent.symbol_id).distinct().all()
        total_unique_constituents = len(unique_constituents)
        
        log("--- SUMMARY STATISTICS (サマリー統計) ---")
        log(f"Total Themes (総テーマ数):                  {total_themes}")
        log(f"  - Active (有効):                          {active_themes}")
        log(f"  - Inactive (無効):                        {inactive_themes}")
        log(f"Themes with constituents (構成銘柄あり):    {themes_with_constituents}")
        log(f"Themes without constituents (構成銘柄なし): {themes_without_constituents}")
        log(f"Total Unique Constituent Symbols (ユニーク構成銘柄数): {total_unique_constituents}")
        
        total_con_counts = sum(constituent_counts.values())
        avg_constituents = total_con_counts / total_themes if total_themes > 0 else 0
        log(f"Average constituents per theme (テーマ別平均構成銘柄数): {avg_constituents:.2f}")
        log("\n---------------------------------------------------------")
        
        # Top 10 Themes by constituent count
        log("\n--- TOP 10 THEMES BY CONSTITUENT COUNT (構成銘柄数上位10テーマ) ---")
        sorted_themes_by_count = sorted(themes, key=lambda t: constituent_counts[t.id], reverse=True)
        for i, t in enumerate(sorted_themes_by_count[:10], 1):
            log(f"{i:2d}. {t.ticker:<10} | {t.name:<30} | Counts: {constituent_counts[t.id]} (Active: {active_constituent_counts[t.id]})")
            
        # Empty Themes (Warning/Maintenance)
        empty_themes = [t for t in themes if constituent_counts[t.id] == 0]
        if empty_themes:
            log(f"\n--- WARNING: THEMES WITH 0 CONSTITUENTS (構成銘柄数0のテーマ: {len(empty_themes)}件) ---")
            for t in empty_themes:
                log(f"- {t.ticker:<10} | {t.name:<30} | ThemeType: {t.theme_type} | Active: {t.active}")
        
        # All Themes Details Table
        log("\n--- DETAILED THEME LIST (テーマ別詳細リスト) ---")
        log(f"{'Ticker':<12} | {'Theme Name':<35} | {'Type':<8} | {'Active':<6} | {'Total':<6} | {'Active_C':<8} | {'Constituents (Max 10 shown)':<50}")
        log("-" * 140)
        
        for t in themes:
            consts_str = ", ".join(constituents_list[t.id][:10])
            if len(constituents_list[t.id]) > 10:
                consts_str += f" ... (+{len(constituents_list[t.id]) - 10} more)"
            if not consts_str:
                consts_str = "(None)"
                
            active_str = "Yes" if t.active == 1 else "No"
            log(f"{t.ticker:<12} | {t.name:<35} | {str(t.theme_type):<8} | {active_str:<6} | {constituent_counts[t.id]:<6} | {active_constituent_counts[t.id]:<8} | {consts_str}")
            
    # Write to file
    try:
        with open(report_file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(report_lines))
        print(f"\n[SUCCESS] Report successfully saved to: {os.path.abspath(report_file_path)}")
    except Exception as e:
        print(f"\n[ERROR] Failed to save report file: {e}")

if __name__ == "__main__":
    generate_report()
