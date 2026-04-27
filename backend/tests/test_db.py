import sqlite3
import pandas as pd
conn = sqlite3.connect('data/stocktool.db')
print('=== Database Verification ===')

# Symbols overview
df_sym = pd.read_sql_query('SELECT theme_type, count(*) as count FROM symbols GROUP BY theme_type', conn)
print('\n--- Symbols by Theme Type ---')
print(df_sym)

# Theme constituents
df_tc = pd.read_sql_query('''
    SELECT 
        s1.ticker as Theme, 
        COUNT(tc.symbol_id) as Constituents 
    FROM theme_constituents tc 
    JOIN symbols s1 ON tc.theme_id = s1.id 
    GROUP BY tc.theme_id
''', conn)
print('\n--- Virtual Theme Constituents Count ---')
print(df_tc)

# Virtual Data Checks
print('\n--- Virtual Index Data (T2) Check ---')
df_vdata = pd.read_sql_query('''
    SELECT 
        s.ticker, 
        MIN(d.date) as min_date, 
        MAX(d.date) as max_date, 
        COUNT(*) as days, 
        MAX(d.close) as max_val 
    FROM daily_prices d 
    JOIN symbols s ON d.symbol_id = s.id 
    WHERE s.theme_type = 'virtual' 
    GROUP BY s.ticker
''', conn)
print(df_vdata)

print('\n--- T3 Indicator Rows Check ---')
df_t3 = pd.read_sql_query('SELECT count(*) as total_T3_records FROM indicators', conn)
print(df_t3)

conn.close()
