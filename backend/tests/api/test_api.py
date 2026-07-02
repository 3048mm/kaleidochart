import requests
import json

try:
    r = requests.get('http://localhost:8000/api/chart/377')
    if r.status_code == 200:
        data = r.json()
        print(f"Success. Returned {len(data)} rows.")
        # print first row
        if len(data) > 0:
            print("Row 0:", {k: v for k, v in data[0].items()})
    else:
        print(f"Failed with {r.status_code}: {r.text}")
except Exception as e:
    print("Error:", e)
