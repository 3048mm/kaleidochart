"""Analyze untagged stocks by NASDAQ industry and count distribution."""
import json
from collections import Counter

filtered_json = r"d:\My Documents\Programing\stocktool\tools\pickup_list\market_data_filtered.json"
stocks_path = r"d:\My Documents\Programing\stocktool\tmp\stocks.txt"

# 1. Read existing tags from stocks.txt
existing_tags = {}
with open(stocks_path, "r", encoding="utf-8") as f:
    for line in f:
        parts = line.rstrip("\r\n").split("\t")
        if len(parts) >= 4:
            ticker = parts[-2].strip()
            tags = parts[-1].strip()
            existing_tags[ticker] = tags

# 2. Load official data
with open(filtered_json, "r", encoding="utf-8") as f:
    official = json.load(f)

# 3. Find untagged stocks grouped by industry
untagged_by_industry = Counter()
tagged_by_industry = Counter()
all_industries = Counter()

for s in official:
    ticker = s["symbol"].strip()
    ind = s["industry"].strip() if s["industry"] else "(empty)"
    sector = s["sector"].strip() if s["sector"] else "(empty)"
    label = f"{ind} [{sector}]"
    all_industries[label] += 1
    
    tags = existing_tags.get(ticker, "").strip()
    if tags:
        tagged_by_industry[label] += 1
    else:
        untagged_by_industry[label] += 1

# 4. Print results
total_stocks = len(official)
total_tagged = sum(1 for t in official if existing_tags.get(t["symbol"].strip(), "").strip())
total_untagged = total_stocks - total_tagged

print(f"=== Tag Coverage Summary ===")
print(f"Total stocks: {total_stocks}")
print(f"Tagged: {total_tagged} ({total_tagged*100//total_stocks}%)")
print(f"Untagged: {total_untagged} ({total_untagged*100//total_stocks}%)")
print()

print(f"=== Untagged Industries (sorted by count, top 60) ===")
for ind, count in untagged_by_industry.most_common(60):
    total = all_industries[ind]
    print(f"  {count:4d} untagged / {total:4d} total  |  {ind}")

print()
print(f"=== All Industries (full list, {len(all_industries)} unique) ===")
for ind, count in all_industries.most_common():
    tagged = tagged_by_industry.get(ind, 0)
    untagged = untagged_by_industry.get(ind, 0)
    marker = "***" if untagged > 0 and tagged == 0 else ""
    print(f"  {count:4d} ({tagged:3d} tagged, {untagged:3d} untagged)  |  {ind} {marker}")
