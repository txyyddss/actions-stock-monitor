import json
from pathlib import Path

p = Path("data/debug/www.dmit.io/20260221T160108Z/parsed_monitor.json")
data = json.loads(p.read_text(encoding="utf-8"))
products = data["products"]
oos = [x for x in products if not x.get("available")]
ins = [x for x in products if x.get("available")]

print(f"Total: {len(products)} products")
print(f"In Stock: {len(ins)}, OOS: {len(oos)}")
print()
print("OOS products:")
for x in oos:
    print(f"  {x['name']:40s} {x.get('price','')}")
print()
print("Sample In-Stock products:")
for x in ins[:10]:
    print(f"  {x['name']:40s} {x.get('price','')}")
