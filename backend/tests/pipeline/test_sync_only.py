import sys
import codecs

def main():
    # Enforce UTF-8 encoding safely without detaching buffers
    sys.stdout.reconfigure(encoding='utf-8')
    
    from db.database import init_db, get_db
    from db.models import Symbol, ThemeConstituent
    from main_step3 import sync_symbols_to_db, load_config
    
    config = load_config()
    db_path = config['system']['db_path']
    init_db(db_path)
    spreadsheet_url = config['system'].get('spreadsheet_url', 'https://docs.google.com/spreadsheets/d/1pkwMVl6FaurinU2Z1Mm_fW_zepMe6YLXE0e-v-vcp1k/edit#gid=0')
    credentials_path = config['system'].get('credentials_path', 'credentials.json')
    
    with get_db() as db:
        print('--- Syncing spreadsheet to DB to test mapping ---')
        try:
            sheet_data, symbol_id_map = sync_symbols_to_db(db, credentials_path, spreadsheet_url)
        except Exception as e:
            print('Error during sync:', e)
            sys.exit(1)
            
        print('\n--- Virtual Index Mapping Result ---')
        virtuals = db.query(Symbol).filter(Symbol.theme_type == 'virtual').all()
        
        for v in virtuals:
            constituents = db.query(ThemeConstituent).filter(ThemeConstituent.theme_id == v.id).all()
            if not constituents:
                print(f'[No constituents] {v.ticker} ({v.name})')
            else:
                c_names = []
                for c in constituents:
                    sym = db.query(Symbol).filter(Symbol.id == c.symbol_id).first()
                    c_names.append(sym.ticker)
                print(f'[OK] {v.ticker} ({v.name}): ' + ', '.join(c_names))

if __name__ == '__main__':
    main()
