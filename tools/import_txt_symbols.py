import os
import sys
import logging
import pandas as pd
from db.database import init_db, get_db
from db.models import Symbol, ThemeConstituent

import tomli

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def load_system_config():
    with open("config.toml", "rb") as f:
        return tomli.load(f)

def import_txt_files():
    config = load_system_config()
    db_path = config["system"]["db_path"]
    init_db(db_path)
    
    with get_db() as db:
        # 1. Soft-delete all non-market symbols. We keep '市場' and '指標' as they are not in txt.
        db.query(Symbol).filter(Symbol.category.notin_(['市場', '指標'])).update({"active": 0})
        db.commit()
        
        symbol_ids = {}
        
        def upsert_symbol(item):
            sym = db.query(Symbol).filter(Symbol.ticker == item['ticker'], Symbol.exchange == item['exchange']).first()
            if sym:
                sym.name = item['name']
                sym.category = item['category']
                sym.asset_class = item['asset_class']
                sym.theme_type = item.get('theme_type')
                sym.tags = item.get('tags', '')
                sym.active = 1
            else:
                sym = Symbol(
                    ticker=item['ticker'],
                    exchange=item['exchange'],
                    name=item['name'],
                    category=item['category'],
                    asset_class=item['asset_class'],
                    theme_type=item.get('theme_type'),
                    tags=item.get('tags', ''),
                    active=1
                )
                db.add(sym)
            db.flush()
            symbol_ids[(sym.ticker, sym.exchange)] = sym.id
            return sym

        # 2. Parse sectors.txt
        if os.path.exists('sectors.txt'):
            with open('sectors.txt', 'r', encoding='utf-8') as f:
                for line in f:
                    parts = line.strip().split('\t')
                    if len(parts) >= 3:
                        name, exch, ticker = parts[0].strip(), parts[1].strip(), parts[2].strip()
                        upsert_symbol({
                            'ticker': ticker, 'exchange': exch, 'name': name,
                            'category': 'セクタ', 'asset_class': 'ETF', 'theme_type': 'etf'
                        })
            logger.info("Imported sectors.txt")

        # 3. Parse themes.txt
        virtual_items = []
        if os.path.exists('themes.txt'):
            with open('themes.txt', 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip(): continue
                    parts = line.strip().split('\t')
                    if len(parts) >= 3:
                        name, exch, ticker = parts[0].strip(), parts[1].strip(), parts[2].strip()
                        tags = parts[3].strip() if len(parts) > 3 else ''
                        t_type = 'virtual' if exch.upper() == 'VIRTUAL' else 'etf'
                        a_class = 'Index' if exch.upper() == 'VIRTUAL' else 'ETF'
                        
                        sym = upsert_symbol({
                            'ticker': ticker, 'exchange': exch, 'name': name,
                            'category': 'テーマ', 'asset_class': a_class, 'theme_type': t_type,
                            'tags': tags
                        })
                        if t_type == 'virtual':
                            virtual_items.append({'ticker': ticker, 'exchange': exch, 'name': name})
            logger.info("Imported themes.txt")

        # 4. Parse stocks.txt
        if os.path.exists('stocks.txt'):
            with open('stocks.txt', 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip(): continue
                    parts = line.strip().split('\t')
                    if len(parts) >= 4:
                        ind, name, exch, ticker = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
                        tags = parts[4].strip() if len(parts) > 4 else ''
                        full_tags = f"{ind},{tags}" if tags else ind # Include industry as tag as well
                        
                        upsert_symbol({
                            'ticker': ticker, 'exchange': exch, 'name': name,
                            'category': '個別', 'asset_class': 'Equity', 'theme_type': None,
                            'tags': full_tags
                        })
            logger.info("Imported stocks.txt")

        db.commit()
        
        # 5. Rebuild ThemeConstituents (VIRTUAL mappings)
        logger.info("Rebuilding Theme Constituents mapping...")
        db.query(ThemeConstituent).delete()
        db.commit()
        
        constituents_to_add = []
        for v_item in virtual_items:
            v_id = symbol_ids.get((v_item['ticker'], v_item['exchange']))
            if not v_id: continue
            
            name_tag = v_item['name'].strip()
            ticker_tag = v_item['ticker'].strip()
                
            matching_symbols = db.query(Symbol).filter(
                Symbol.active == 1,
                (Symbol.tags.like(f"%{ticker_tag}%")) | (Symbol.tags.like(f"%{name_tag}%")),
                (Symbol.theme_type != 'virtual') | (Symbol.theme_type.is_(None))
            ).all()
            
            if matching_symbols:
                logger.info(f"  Virtual theme '{v_item['ticker']}' ({name_tag}) matched {len(matching_symbols)} constituents.")
                weight = 1.0 / len(matching_symbols)
                for m_sym in matching_symbols:
                    constituents_to_add.append(
                        ThemeConstituent(theme_id=v_id, symbol_id=m_sym.id, weight=weight)
                    )
            else:
                logger.warning(f"  Virtual theme '{v_item['ticker']}' ({name_tag}) has NO matching constituent symbols.")
                
        if constituents_to_add:
            db.bulk_save_objects(constituents_to_add)
            db.commit()

        active_count = db.query(Symbol).filter(Symbol.active == 1).count()
        logger.info(f"Total active symbols in DB: {active_count}")


if __name__ == "__main__":
    import_txt_files()
