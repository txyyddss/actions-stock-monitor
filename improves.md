
**Role:** You are an expert Full-Stack Developer specializing in web scraping, Cloudflare bypass strategies (FlareSolverr), concurrency, and performance optimization.

**Context:** I have a stock monitoring web scraper project that tracks product availability across various hosting/VPS providers and sends alerts via a Telegram bot. The current codebase suffers from parsing bugs, inefficient scraping workflows, and UI/notification glitches.

**Objective:** Refactor and debug the codebase to maximize efficiency, reduce execution time for a single run, fix site-specific parsing logic, and generate comprehensive documentation.

Please execute the following requirements in phases:

You can use local flaresoverr instance `http://127.0.0.1:8191/`

**Phase 1: Architecture & Performance Optimization**

* **Direct Fetching First:** Modify the scraping logic to attempt direct data fetching first. Only use FlareSolverr as a fallback if the direct request is blocked.
* **FlareSolverr Efficiency:** Implement a temporary cookie storage system. Extract and reuse Cloudflare clearance cookies from FlareSolverr to minimize redundant challenge-solving.
* **Concurrency:** Introduce multi-threading/asynchronous data fetching to process multiple providers simultaneously.
* **Retry Logic:** Enhance the retry mechanism to handle timeouts and network failures gracefully.
* **Code Cleanup:** Audit the entire codebase to identify and remove useless fetch requests. Refactor any redundant logic to improve overall runtime efficiency.
* **Logging:** Implement clean, simplified console logs to indicate real-time fetching status without cluttering the terminal.

**Phase 2: Bug Fixes (Parsing & Logic)**
Audit the codebase and fix the following specific provider bugs:

* `wawo.wiki`: Scraper currently only lists the first product of each group; expand to fetch all.
* `wap.ac`: Product names are incorrectly mapped to category descriptions. Fix the CSS selector/API mapping.
* `bagevm.com`: Scraper only returns the group name instead of the actual products.
* `my.rfchost.com`: Only fetching products from the first group.
* `fachost.cloud`: Only listing in-stock items and incorrectly flagging their status as "Unknown". Update logic to capture out-of-stock items and correct the status.
* `backwaves.net`: Product name incorrectly scrapes as "cart.php" and out-of-stock items are falsely reported as "In Stock".
* `app.vmiss.com`: Fix the logic causing widespread "Unknown" statuses.
* **General OOS Bug:** Fix the global logic flaw preventing some out-of-stock products from being fetched correctly.

**Phase 3: UI & Telegram Enhancements**

* **Web UI:** Add the Telegram group link (`https://t.me/tx_stock_monitor`) to a prominent location on the web dashboard.
* **Web UI:** Improve the display layout and formatting of product details on the frontend.
* **Telegram Bot:** Inject more comprehensive product details into the alert messages.
* **Encoding Fix:** Fix the character encoding bug causing the "NEW PRODUCT" Telegram message to display broken characters (e.g., "馃啎" instead of the intended emoji like 🆕).

**Phase 4: Testing & QA**

* Run thorough local tests and use debugging tools to ensure all the above issues are resolved.
* After applying the fixes, run a full production-simulation test suite.
* **KPI:** Your optimizations must tangibly decrease the total time required for a single complete scraping run.

**Phase 5: Documentation**

* Write a comprehensive, detailed `README.md`.
* Include: A detailed Introduction, a technical breakdown of "How it Works" (including the FlareSolverr fallback logic), and explicit, step-by-step instructions for deploying the web interface to Cloudflare Pages.
