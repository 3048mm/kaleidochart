import os
import sys
from sqlalchemy import func, or_, desc

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from db import database
from db.models import Symbol, Indicator, DailyPrice
from sqlalchemy.orm import aliased

def check_rrg_transition_strict():
    # Initialize DB
    db_path = os.path.join(os.getcwd(), "data", "stocktool.db")
    database.init_db(db_path)
    
    db = database.SessionLocal()
    
    # Get latest date
    latest_date = db.query(func.max(Indicator.date)).scalar()
    # Get previous date
    previous_date = db.query(func.max(Indicator.date)).filter(Indicator.date < latest_date).scalar()
    
    print(f"Latest Date: {latest_date}")
    print(f"Previous Date: {previous_date}")
    
    IndPrev = aliased(Indicator)
    
    # Query for rrg_leading_in with ALL filters
    query = db.query(Symbol.ticker).join(
        Indicator, Symbol.id == Indicator.symbol_id
    ).join(
        IndPrev, (Symbol.id == IndPrev.symbol_id) & (IndPrev.date == previous_date)
    ).filter(
        Symbol.active == True,
        Indicator.date == latest_date,
        Indicator.rs_ratio_21 > 0,
        Indicator.rs_momentum_21 > 0,
        or_(IndPrev.rs_ratio_21 <= 0, IndPrev.rs_momentum_21 <= 0),
        Indicator.vol_surge_21 >= 1.0,
        Indicator.adr_pct_21 >= 4.0,
        Indicator.dist_sma50_atr <= 6.0,
        Indicator.market_cap >= 1_000_000_000
    )
    
    leading_in = query.all()
    print(f"\n--- RRG Leading In STRICT ({len(leading_in)} found) ---")
    for r in leading_in[:10]:
        print(r[0])

    # Query for rrg_lagging_in with ALL filters
    query_lagging = db.query(Symbol.ticker).join(
        Indicator, Symbol.id == Indicator.symbol_id
    ).join(
        DailyPrice, (Symbol.id == DailyPrice.symbol_id) & (Indicator.date == DailyPrice.date)
    ).join(
        IndPrev, (Symbol.id == IndPrev.symbol_id) & (IndPrev.date == previous_date)
    ).filter(
        Symbol.active == True,
        Indicator.date == latest_date,
        Indicator.rs_ratio_21 < 0,
        Indicator.rs_momentum_21 < 0,
        or_(IndPrev.rs_ratio_21 >= 0, IndPrev.rs_momentum_21 >= 0),
        ((DailyPrice.close - DailyPrice.open) / DailyPrice.open * 100) < -2.0,
        Indicator.vol_surge_21 >= 1.0
    )
    
    lagging_in = query_lagging.all()
    print(f"\n--- RRG Lagging In STRICT ({len(lagging_in)} found) ---")
    for r in lagging_in[:10]:
        print(r[0])

    db.close()

if __name__ == '__main__':
    check_rrg_transition_strict()
