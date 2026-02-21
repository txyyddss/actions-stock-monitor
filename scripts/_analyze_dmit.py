"""Extract DMIT product name structure from cart.php HTML."""
import json
import re
from pathlib import Path
from bs4 import BeautifulSoup

debug_dir = Path("data/debug/www.dmit.io")
dirs = sorted([d for d in debug_dir.iterdir() if d.is_dir()])
latest = dirs[-1]

raw_pages = json.loads((latest / "raw_pages.json").read_text(encoding="utf-8"))

for p in raw_pages:
    if p["url"] == "https://www.dmit.io/cart.php" and p["ok"]:
        html = p["text"]
        soup = BeautifulSoup(html, "lxml")
        
        # Look at ALL cart-products-item elements
        items = soup.select('.cart-products-item')
        print(f"Total cart-products-item elements: {len(items)}")
        
        if not items:
            # Try different selectors
            print("Trying alternative selectors...")
            items = soup.select('[class*=cart-products]')
            print(f"Found {len(items)} elements matching [class*=cart-products]")
        
        # Print actual HTML of first few items
        for i, item in enumerate(items[:3]):
            print(f"\n--- Item {i} ---")
            print(str(item)[:500])
        
        # Extract names from the raw text
        # DMIT names look like: LAX.Pro.STARTER, HKG.AS3.Pro.MEDIUM, TYO.AS3.T1.GIANT
        # Let's find them in raw HTML
        print("\n\n=== Searching for dotted names directly in HTML ===")
        dmit_names = re.findall(r'(?:LAX|HKG|TYO|SJC)[.\-](?:[A-Za-z0-9]+[.\-]){1,3}[A-Za-z0-9]+(?:v\d+)?', html)
        unique_names = sorted(set(dmit_names))
        print(f"Found {len(unique_names)} unique dotted names:")
        for n in unique_names:
            print(f"  {n}")
        
        # Now find which PID maps to which name
        print("\n\n=== PID to name mapping from HTML ===")
        # Find pid links and their nearby dotted names
        for m in re.finditer(r'pid=(\d+)', html):
            pid = int(m.group(1))
            # Look for nearby dotted name
            start = max(0, m.start() - 2000)
            end = min(len(html), m.end() + 500)
            context = html[start:end]
            names_in_context = re.findall(r'(?:LAX|HKG|TYO|SJC)[.\-](?:[A-Za-z0-9]+[.\-]){1,3}[A-Za-z0-9]+(?:v\d+)?', context)
            if names_in_context:
                # Take the closest one (last one before the pid reference)
                pre_context = html[start:m.start()]
                names_b = re.findall(r'(?:LAX|HKG|TYO|SJC)[.\-](?:[A-Za-z0-9]+[.\-]){1,3}[A-Za-z0-9]+(?:v\d+)?', pre_context)
                if names_b:
                    print(f"  pid={pid:4d} -> {names_b[-1]}")
                else:
                    print(f"  pid={pid:4d} -> {names_in_context[0]} (after pid)")
        break
