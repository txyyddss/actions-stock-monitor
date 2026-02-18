

**Role:** You are an expert Full-Stack Developer and DevOps Engineer specializing in high-performance web automation and CI/CD pipelines.

**Objective:** Develop a "Restock Monitor & Aggregate Buying Program" that tracks product availability across specific VPS hosting domains, sends real-time notifications via Telegram, and generates a static dashboard. The solution must be efficient, robust, and fully automated using GitHub Actions.

---

### 1. Architecture & Tech Stack

* **Language:** Python (recommended for rich scraping ecosystem) or Go (for high concurrency). Choose the most efficient option for handling multiple domains.
* **Core Components:**
* **Scraper Engine:** Fetches data from target domains.
* **State Manager:** Tracks previous stock states to identify changes (restocks/new items).
* **Notifier:** Pushes updates to Telegram.
* **Dashboard Generator:** Builds a static HTML page.
* **CI/CD:** GitHub Actions for scheduled execution.



### 2. Data Scraping Module

* **Targeting:** For each domain in the target list, fetch:
* Availability Status (In Stock/Out of Stock)
* Product Name/Title
* Price (Currency and Amount)
* Detailed Description (RAM, CPU, Disk, Bandwidth, etc.)
* Direct Purchase Link
* Bypass cloudflare challenges by integrating flaresoverr
* Include time out logic


* **Customization:** Implement modular parsers for each domain to handle unique HTML structures.
* **Network & Resilience:**
* Implement `User-Agent` rotation and header management to mimic legitimate traffic.
* **Proxy Support:** Integrate SOCKS5 proxy support for requests to manage network identity.
* **Error Handling:** Include retry logic for timeouts or 5xx errors.



### 3. Restock Monitoring Logic

* **State Comparison:** Store the previous scrape results (e.g., in a JSON file or lightweight database). Compare current data against this state.
* **Triggers:**
* **Restock:** Status changes from "Out of Stock" to "In Stock".
* **New Product:** A product ID/URL appears that was not in the previous state.


* **Notification:**
* Send a formatted message to Telegram immediately upon trigger.
* **Message Format:**
* **Header:** 🟢 RESTOCK ALERT or 🆕 NEW PRODUCT
* **Details:** Name, Price, Specs.
* **Action:** Direct Purchase Link.
* **Timestamp:** Time of detection.





### 4. Telegram Integration

* **Configuration:** Do not hardcode credentials. Read the following from Environment Variables:
* `TELEGRAM_BOT_TOKEN`
* `TELEGRAM_CHAT_ID`


* **Testing:** ensure the message formatting (HTML/Markdown) works correctly.

### 5. Aggregate Buying Dashboard

* **Output:** Generate a single static HTML file (`index.html`).
* **Design:** Futuristic, "Cyberpunk" or "High-Tech" aesthetic. Dark mode by default.
* **Features:**
* Sortable table or grid view of all monitored products.
* Status indicators (Green for Stock, Red for OOS).
* "Buy Now" buttons.
* Last updated timestamp.


* **Responsiveness:** Must be mobile-friendly.

### 6. GitHub Actions Integration

* **Workflow File:** Create a `.github/workflows/main.yml` file.
* **Schedule:** Configure a CRON trigger to run the scraper frequently (e.g., every 15 or 30 minutes).
* **Secrets:** Map GitHub Secrets to the environment variables (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `PROXY_URL`).
* **Persistence:**
* Commit the updated state file (database) and the generated `index.html` back to the repository after every run.
* Deploy the static page using GitHub Pages (optional, but code should support it).



### 7. Target Domains

Support the following domains with modular parsing logic:

* `https://fachost.cloud/`
* `https://my.rfchost.com/`
* `https://app.vmiss.com/`
* `https://acck.io/`
* `https://console.po0.com/`
* `https://akile.io/`
* `https://greencloudvps.com/`
* `https://kaze.cloud/`
* `https://bgp.gd/`
* `https://nmcloud.cc/`
* `https://my.frantech.ca/`
* `https://wawo.wiki/`
* `https://backwaves.net/`
* `https://cloud.ggvision.net/`
* `https://wap.ac/`
* `https://www.bagevm.com/`

### 8. Deliverables

* Complete source code organized by modules.
* `requirements.txt` (or `go.mod`).
* GitHub Actions workflow file.
* Instructions on setting up GitHub Secrets.

---

