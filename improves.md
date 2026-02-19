Add Sites, be aware of the hidden promotion products, you can run local tests to understand each website structure:
- https://cloud.colocrossing.com/
- https://www.dmit.io/
- https://bill.hostdare.com/
- https://clients.zgovps.com/
- https://vps.hosting/
- https://my.racknerd.com/
- https://clientarea.gigsgigscloud.com/
- https://cloud.boil.network/
- https://www.vps.soy/
- https://cloud.bffyun.com/

Fix Bugs:
- my.rfchost.com Only listed part of the products (Only listed the first product group)
- fachost.cloud Only listed products in stock
- acck.io & akile.io Console output products is always 0 but the website shows the correct stock, need to fix the console output
- Console output of "Run monitor" is not realtime
- The element on website "Run:  → " does not fit small-sized screen like phones
- wawo.wiki & my.rfchost.com & fachost.cloud does not display all the products
- Many products are wrongly marked as "STALE"
- Stock status of most of the products of app.vmiss.com are wrongly marked as "Unknown" and "STALE"
- The pie chart is wrong at all, always fully green

Add Features:
- Github functions
    - Dependabot 
    - Code scanning
    - Issue templates
- Display available payment cycles and prices of each product both on website and telegram messages
- Integrate the "options"(like locations) of product into the button or description of the product instead of showing a standalone "Unknown" product
- Check the "options" display of all sites if it can display correctly
- Make the telegram message similar to the sample message below(You don't need to 100% follow, just for example):
```
#ZgoCloud

Los Angeles Ryzen9 Performance- Lite

• 1 Core AMD Ryzen9 7950X
• 512 MB DDR5 RAM
• 15G NVMe SSD
• 500G/Month/200Mbps, Fair Use

ℹ️ China Optimized, CN2GIA&9929&CMIN2

💰 Price: $38.9 USD Annually

👉 Order Now (https://clients.zgovps.com/?cmd=cart&action=add&affid=806&id=101)

🕒 2026-02-18 11:10:56
```

Run full local tests after changes
Run customized local tests for investigation and analysis of the sites
Check all codes throughly to discover and fix bugs

Local flaresoverr url: http://127.0.0.1:8191/