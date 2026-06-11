import sqlite3
import requests
import json
import sys

def test_rs_data_availability():
    db_path = 'data/stocktool.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 1. Get DUOL symbol_id and latest RS values from DB
    cursor.execute("SELECT id FROM symbols WHERE ticker='DUOL'")
    res = cursor.fetchone()
    if not res:
        print("DUOL not found in DB")
        return
    symbol_id = res[0]
    
    cursor.execute("""
        SELECT date, rs_ratio_e14, rs_momentum_e14, rs_trend_s14
        FROM indicators 
        WHERE symbol_id = ? 
        ORDER BY date DESC LIMIT 1
    """, (symbol_id,))
    db_row = cursor.fetchone()
    if not db_row:
        print("No indicator data for DUOL")
        return
    
    latest_date, db_ratio, db_mom, db_cond = db_row
    print(f"DB Latest ({latest_date}): rs_ratio_e14={db_ratio}, rs_momentum_e14={db_mom}, rs_trend_s14={db_cond}")
    conn.close()
    
    # 2. Call API (assuming the server is NOT running, we might need to use TestClient or mock)
    # Actually, let's try to use FastAPI's TestClient if possible, or just check the code.
    # Since I don't know if the server is running, I'll use a direct check or try to start it.
    
    # Alternatively, I can write a script that imports the router and calls the function directly.
    # This is more robust for TDD in this environment.
    
    print("\n--- Testing API Response (Direct function call) ---")
    sys.path.append('backend')
    from db.database import init_db
    init_db('data/stocktool.db')
    
    from api.routers import get_chart_data
    from db.database import SessionLocal
    
    db = SessionLocal()
    try:
        response = get_chart_data(symbol_id, db)
        # response is a fastapi.Response object because of the custom JSON dump
        content = json.loads(response.body)
        
        # Get the last data point
        last_point = content['data'][-1]
        print(f"API Latest Date: {last_point['time']}")
        
        keys_to_check = ['rs_ratio_e14', 'rs_momentum_e14', 'rs_trend_s14', 'change_1d_pct']
        missing = []
        for key in keys_to_check:
            val = last_point.get(key)
            print(f"API {key}: {val}")
            if val is None:
                missing.append(key)
        
        if missing:
            print(f"\n[FAIL] Missing keys in API response: {missing}")
            assert False, f"Missing keys in API response: {missing}"
        else:
            print("\n[PASS] All keys found in API response.")
            
    finally:
        db.close()

if __name__ == "__main__":
    test_rs_data_availability()
