import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Config map for what each sheet represents
# Key: Sheet title, Value: Target base Category
SHEET_CONFIG = {
    "MarketList": "市場",
    "LeadingList": "指標",
    "SectorList": "セクタ",
    "ThemeList": "テーマ",
    "StockList": "個別"
}

def fetch_symbols_from_sheet(credentials_path: str, spreadsheet_url: str) -> List[Dict[str, Any]]:
    """
    Connects to Google Sheets, reads the configured worksheets starting from row 4,
    and returns a list of symbol dictionaries ready for DB Upsert.
    """
    logger.info("Connecting to Google Sheets...")
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_path, scope)
    client = gspread.authorize(creds)
    
    spreadsheet = client.open_by_url(spreadsheet_url)
    
    all_symbols = []
    
    for sheet_title, base_category in SHEET_CONFIG.items():
        try:
            worksheet = spreadsheet.worksheet(sheet_title)
            data = worksheet.get_all_values()
            
            # Start from 4th row (index 3)
            rows = data[3:]
            
            added_count = 0
            for row in rows:
                # Pad row to ensure we don't get index errors
                row = row + [''] * max(0, 6 - len(row))
                
                asset_class = row[1].strip()  # B列 業界/業種
                name = row[2].strip()         # C列 名称
                exchange = row[3].strip()     # D列 取引所コード
                ticker = row[4].strip()       # E列 ティッカー
                tags = row[5].strip()         # F列 属性 (タグ)
                
                if not ticker:
                    continue  # Skip rows without a ticker
                
                # Determine theme_type
                theme_type = None
                if exchange.upper() == 'VIRTUAL':
                    theme_type = 'virtual'
                elif base_category == 'セクタ':
                    theme_type = 'sector'
                elif base_category == 'テーマ':
                    theme_type = 'theme'  # Categorize as theme for better identification
                elif base_category == '指標' or base_category == '市場':
                    theme_type = 'etf' if exchange.upper() != 'VIRTUAL' else 'virtual'
                
                all_symbols.append({
                    "ticker": ticker,
                    "exchange": exchange,
                    "name": name,
                    "category": base_category,    # The root category
                    "asset_class": asset_class,   # Specific industry/class
                    "tags": tags if tags else None,
                    "theme_type": theme_type
                })
                added_count += 1
                
            logger.info(f"Loaded {added_count} active symbols from sheet: {sheet_title}")
            
        except gspread.exceptions.WorksheetNotFound:
            logger.warning(f"Worksheet '{sheet_title}' not found in the spreadsheet. Skipping.")
        except Exception as e:
            logger.error(f"Error parsing sheet '{sheet_title}': {str(e)}")
            
    return all_symbols

if __name__ == "__main__":
    # Test script usage
    logging.basicConfig(level=logging.INFO)
    url = "https://docs.google.com/spreadsheets/d/1pkwMVl6FaurinU2Z1Mm_fW_zepMe6YLXE0e-v-vcp1k/edit#gid=0"
    creds_path = "../credentials.json"
    import os
    if os.path.exists(creds_path):
        syms = fetch_symbols_from_sheet(creds_path, url)
        print(f"\nTotal symbols parsed: {len(syms)}")
        print("First 3 records:")
        for s in syms[:3]:
            print(s)
        
        # Look for a virtual theme
        virtuals = [s for s in syms if s['theme_type'] == 'virtual']
        print(f"\nFound {len(virtuals)} virtual indexes. First 3:")
        for v in virtuals[:3]:
            print(v)
