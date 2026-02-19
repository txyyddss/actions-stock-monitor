# Implementation Plan for improves.md

## Phase 1: Bug Fixes (Priority - High)

### 1.1 Fix STALE marking issue
- Dashboard marks many products as "STALE" incorrectly
- The stale check compares `last_seen` to `updated_at` with a 180-minute window
- Products from successful runs should never be stale in the same run

### 1.2 Fix pie chart (always fully green)
- The pie chart `conic-gradient` uses hardcoded percentages in CSS
- JS `updatePie()` updates it dynamically but the initial CSS always shows green
- Need to verify the JS logic is correctly calculating percentages

### 1.3 Fix "Run: →" element for small screens
- The pill showing run timestamps doesn't fit on phones
- Needs responsive CSS fix

### 1.4 Fix rfchost.com only listing first product group
- The discovery logic may stop too early with `stop_after_no_new` 
- Need to ensure all product groups (gid=) are crawled

### 1.5 Fix fachost.cloud only listing in-stock products
- Already has special handling but may need to also parse OOS products

### 1.6 Fix acck.io & akile.io console output always 0
- The SPA parser works on API JSON, but `_scrape_target` logs `len(run.products)` 
- The initial HTML parse returns 0 products; API products come from discovery pages
- Console log happens before discovery in `run_monitor`, but actually in `_scrape_target` discovery is included

### 1.7 Fix console output not realtime
- Python stdout buffering issue - need to flush or use unbuffered output

### 1.8 Fix wawo.wiki & rfchost.com & fachost.cloud not displaying all products
- Related to discovery issues; need deeper investigation

### 1.9 Fix vmiss.com wrongly marked as Unknown/STALE
- The app.vmiss.com products may not have availability data extracted properly

## Phase 2: Add New Sites

### 2.1 Add new target URLs:
- cloud.colocrossing.com
- www.dmit.io
- bill.hostdare.com
- clients.zgovps.com
- vps.hosting
- my.racknerd.com
- clientarea.gigsgigscloud.com
- cloud.boil.network
- www.vps.soy
- cloud.bffyun.com

## Phase 3: Feature Additions

### 3.1 GitHub features (Dependabot, Code scanning, Issue templates)
### 3.2 Display billing cycles/prices
### 3.3 Integrate options into product display
### 3.4 Improve Telegram message format

## Phase 4: Testing & Polish
