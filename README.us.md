<p align="center">
  <a href="README.md">🇨🇱 Español</a> |
  <a href="README.us.md">🇺🇸 English</a>
</p>

# 📡 WiFi Channel Optimizer

Automated Wi-Fi channel optimizer for **Huawei HG8145X6** (ONT WiFi 6) — tested with **Entel ISP (Chile)**.

Scans the surrounding RF spectrum, selects the least-congested channel for both 2.4 GHz and 5 GHz bands, and reconfigures the router automatically using browser automation.

> **Primary use case:** reduce latency for **multiplayer gaming** — the quality metrics and revert logic are tuned for low ping and low jitter, not throughput.

---

## ✨ Features

| Feature | Detail |
|---|---|
| **RF Scanning** | Uses `netsh wlan show networks mode=bssid` (Windows native) |
| **Congestion scoring** | Sum of dBm power across a channel **and its adjacent channels** |
| **Smart decision** | Only changes if improvement is **> 20 %** (hysteresis) |
| **2.4 GHz rules** | Restricts candidates to non-overlapping channels **1, 6, 11** |
| **5 GHz rules** | Prefers non-DFS channels (36–48, 149–161) for stability |
| **Router automation** | Headless Chromium via Playwright — no manual interaction needed |
| **Gaming-aware quality monitoring** | Measures **gateway RTT + jitter** before and after every change |
| **Auto-revert** | Reverts within 5 min if jitter or gateway ping degrade |
| **Daemon mode** | Runs continuously, re-scanning every 5 minutes |
| **RF Monitor mode** | Records Wi-Fi environment snapshots to SQLite for trend analysis |
| **`.env` config** | All credentials and tuning parameters live outside the source code |

---

## 🎮 Quality metrics — why they matter for gaming

The system measures three metrics **against the router gateway** (`192.168.100.1`), not an internet host. This isolates the Wi-Fi hop from ISP/backbone noise.

| Metric | What it measures | Why it matters for gaming |
|---|---|---|
| **Gateway RTT** (`ping_gw_ms`) | Round-trip time to `192.168.100.1` — the Wi-Fi hop only | High gateway RTT means the radio channel itself is congested. Target: **< 5 ms** |
| **Jitter** (`jitter_ms`) | Std-dev of individual RTT samples (8 pings) | More harmful than high-but-stable ping. Causes rubber-banding & hit-registration issues. Target: **< 5 ms** |
| **Download speed** (`speed_mbps`) | 1 MB probe via Cloudflare | Secondary signal only — game packets are < 1 KB, throughput is irrelevant to latency |

### Revert priority order

```
1. Jitter increased > JITTER_DEGRADATION_MS  →  revert  ← most sensitive
2. Gateway RTT increased > PING_DEGRADATION_MS  →  revert
3. Download speed dropped > SPEED_DEGRADATION_PCT  →  revert  ← least sensitive
```

### Why ping to 8.8.8.8 was NOT used

Pinging `8.8.8.8` measures the full path: Wi-Fi hop + modem + ISP backbone + Google's network.
A channel change that genuinely improves the radio can look "worse" if Google's servers are momentarily slower.
Pinging the gateway eliminates all variables except the channel itself.

---

## 🖥️ Requirements

- Windows 10/11 (requires `netsh`)
- Python ≥ 3.13
- [`uv`](https://github.com/astral-sh/uv) (recommended) **or** pip

---

## ⚡ Installation

### 1 — Clone the repo

```bash
git clone https://github.com/YOUR_USER/WifiChannelOptimizer.git
cd WifiChannelOptimizer
```

### 2 — Create virtual environment & install dependencies

**With uv (recommended):**
```bash
uv venv
uv pip install -e .
```

**With pip:**
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

### 3 — Install Playwright's Chromium browser

```bash
python -m playwright install chromium
```

### 4 — Configure credentials

```bash
copy .env.example .env
```

Open `.env` and set your router credentials:

```dotenv
ROUTER_URL=http://192.168.100.1
ROUTER_USER=admin
ROUTER_PASS=YOUR_ROUTER_PASSWORD
```

> ⚠️ **Never commit `.env` to Git.** It is already listed in `.gitignore`.

---

## 🚀 Usage

```bash
# Run once (scan + decide + apply)
python main.py --once

# Daemon mode — re-scans every 5 minutes (default)
python main.py

# Dry run — full cycle without touching the router
python main.py --once --dry-run

# Inspect mode — opens a visible browser window for debugging selectors
python main.py --inspect

# Monitor mode — records RF snapshots to wifi_monitor.db every 30 s
python main.py --monitor

# Monitor with custom interval and duration
python main.py --monitor --interval 60 --duration 3600
```

### CLI flags

| Flag | Description |
|---|---|
| *(none)* | Daemon mode — continuous loop, Ctrl+C to stop |
| `--once` | Single optimization cycle and exit |
| `--dry-run` | Full cycle (scan + score + ping) but **no router changes** |
| `--inspect` | Opens Chromium in headed mode + dumps diagnostic HTML files |
| `--monitor` | Observatory mode — records RF snapshots to `wifi_monitor.db` |
| `--interval N` | (with `--monitor`) Seconds between scans. Default: `30` |
| `--duration N` | (with `--monitor`) Stop after N seconds. Default: unlimited |

---

## 🔧 Configuration reference (`.env`)

| Variable | Default | Description |
|---|---|---|
| `ROUTER_URL` | `http://192.168.100.1` | Router admin panel URL |
| `ROUTER_USER` | `admin` | Admin username |
| `ROUTER_PASS` | `admin` | Admin password |
| `ROUTER_DRIVER` | `huawei_hg8145x6` | Router automation driver key (see [Adding a new router](#-adding-a-new-router-model)) |
| `SCAN_INTERVAL_SECONDS` | `300` | Seconds between daemon scans |
| `TRIAL_PERIOD_SECONDS` | `300` | Seconds after a channel change before quality is evaluated. 5 min is enough to stabilize without ruining a full match. |
| `PING_DEGRADATION_MS` | `20` | Gateway RTT increase (ms) that triggers a revert. 20 ms is perceptible in competitive gaming. |
| `JITTER_DEGRADATION_MS` | `15` | Jitter increase (ms) that triggers a revert. 15 ms of extra jitter causes rubber-banding in most games. |
| `SPEED_DEGRADATION_PCT` | `0.40` | Download speed drop fraction that triggers a revert (secondary, non-gaming signal). |

---

## 🛠️ Router compatibility

This project was built and tested against the **Huawei HG8145X6** ONT provided by **Entel (Chile)**.

The automation relies on the following confirmed HTML selectors:

| Element | Selector |
|---|---|
| Username field | `#txt_Username` |
| Password field | `#txt_Password` |
| Login button | `#loginbutton` (`.nth(1)` — there are two in the DOM) |
| WLAN menu entry | `#name_wlanconfig` |
| 2.4 GHz Advanced sub-menu | `#wlan2adv` |
| 5 GHz Advanced sub-menu | `#wlan5adv` |
| Channel dropdown | `#Channel` (same ID for both bands, loaded in separate iframes) |
| Apply button | `#applyButton` (executes `Submit()`) |
| Panel iframe | `WlanAdvance.asp?2G` / `WlanAdvance.asp?5G` |

### Other ISPs / firmware variants

If your ISP (Movistar, Claro, WOM, etc.) ships a different firmware skin, run `--inspect` to open a visible browser and check the selectors. The HTML dumps saved as `router_*.html` will guide you.

---

## 📄 Logs

Every run appends to `wifi_optimizer.log`:

```
2026-03-29 21:47:01 [INFO] Current 2.4 GHz channel: ch6
2026-03-29 21:47:01 [INFO] Congestion scores 2.4 GHz: {1: -563.0, 6: -227.0, 11: -222.5}
2026-03-29 21:47:01 [INFO] 2.4 GHz — current: ch6 (-227.0), optimal: ch11 (-222.5), improvement: 2.0% → keep
2026-03-29 21:47:01 [INFO] Already on optimal channels (or within hysteresis). No changes.
2026-03-29 21:47:01 [INFO] Wi-Fi quality → gateway ping: 11.0 ms | jitter: 4.2 ms | speed: 85.3 Mbps
```

---

## 📁 Project structure

```
WifiChannelOptimizer/
├── main.py                        # Entry point — config loading, driver selection, daemon loop
├── wifi_optimizer/
│   ├── scanner.py                 # Phase 1: netsh Wi-Fi scan + dBm conversion
│   ├── decision.py                # Phase 2: congestion scoring, hysteresis, channel selection
│   ├── quality.py                 # Gaming metrics: gateway RTT, jitter, download speed
│   ├── optimizer.py               # Core cycle: ties scanner + decision + quality + router together
│   ├── monitor.py                 # Observatory mode: records RF snapshots to SQLite
│   └── routers/
│       ├── base.py                # BaseRouter ABC — the contract every driver must implement
│       └── huawei_hg8145x6.py    # Concrete driver for Huawei HG8145X6 (Entel, Chile)
├── .env.example                   # Config template — copy to .env and fill in
├── .env                           # Your local credentials (git-ignored)
├── pyproject.toml                 # Project metadata and dependencies
├── PROMPT.md                      # Agent instructions & business rules specification
├── .gitignore
├── README.md                      # Versión en Español (primary)
├── README.us.md                   # This file (English)
├── wifi_optimizer.log             # Runtime log (git-ignored)
└── wifi_monitor.db                # RF monitor database (git-ignored)
```

---

## 📡 RF Monitor mode

The monitor mode is completely independent from the optimizer — **it does not touch the router, requires no credentials, and makes no changes**. It only scans and records.

### When to use it

- To **understand your RF environment** before enabling the optimizer
- To detect **time-of-day congestion patterns** (do neighbours saturate channel 6 at night?)
- To **validate** that a manual or automatic channel change had a real effect
- To keep **historical evidence** when troubleshooting connectivity issues

### SQLite database (`wifi_monitor.db`)

Table `snapshots`:

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER | Auto-increment PK |
| `ts` | TEXT | ISO-8601 UTC timestamp |
| `ssid` | TEXT | Network name |
| `bssid` | TEXT | Access point MAC address |
| `channel` | INTEGER | Wi-Fi channel |
| `band` | TEXT | `'2.4'` or `'5'` |
| `signal_pct` | INTEGER | Signal in % (raw netsh value) |
| `signal_dbm` | REAL | Signal in dBm (`pct/2 - 100`) |

### Query examples

```python
import sqlite3
import pandas as pd

con = sqlite3.connect("wifi_monitor.db")
df  = pd.read_sql("SELECT * FROM snapshots", con, parse_dates=["ts"])

# Average congestion per channel
df.groupby(["channel", "band"])["signal_dbm"].mean()

# Signal strength of a specific network over time
df[df["ssid"] == "MyNeighbour"].set_index("ts")["signal_dbm"].plot()

# Hour of day with the most active networks
df["hour"] = df["ts"].dt.hour
df.groupby("hour")["bssid"].nunique().plot(kind="bar", title="Unique networks per hour")
```

```sql
-- Top 5 most congested channels (average dBm)
SELECT channel, band, COUNT(*) as scans, AVG(signal_dbm) as avg_dbm
FROM snapshots
GROUP BY channel, band
ORDER BY avg_dbm ASC
LIMIT 5;
```

---

## 🔌 Adding a new router model

The optimizer core (`optimizer.py`) never talks to the router directly — it only calls two methods on a `BaseRouter` instance. Everything router-specific is isolated inside a single driver file.

### The `BaseRouter` contract

Every driver must implement exactly two methods:

```python
def read_channels(self) -> tuple[int | None, int | None]:
    """
    Log in to the router, read the currently active channel for each band,
    log out and return (channel_2_4ghz, channel_5ghz).
    Return None for a band if it could not be read.
    Called once at startup to initialise the hysteresis state.
    """

def apply_channels(
    self,
    channel_24: int | None,
    channel_5:  int | None,
    *,
    headed: bool = False,
) -> None:
    """
    Log in, set the requested channels, confirm the change, log out.
    Pass None for a band to leave it unchanged.
    headed=True opens the browser visibly (triggered by --inspect).
    """
```

The base class also provides `self.url`, `self.username`, `self.password`, and a
`gateway_host` property (the LAN IP derived from `self.url`) that the quality
monitor uses to measure ping/jitter.

---

### Step-by-step guide

#### Step 1 — Discover your router's selectors with `--inspect`

```bash
python main.py --inspect
```

This opens a **visible Chromium window** and saves four HTML snapshots to the project root:

| File | When it is saved |
|---|---|
| `router_login_page.html` | Immediately after navigating to `ROUTER_URL` |
| `router_post_login.html` | After a successful login |
| `router_wlan24.html` | After opening the 2.4 GHz settings panel |
| `router_wlan5.html` | After opening the 5 GHz settings panel |

Open these files in a browser and use DevTools (`F12 → Inspector`) to find:

- The **username / password field IDs**
- The **login button** ID or type
- The **navigation path** to Wi-Fi channel settings (menu items, links, iframes)
- The **channel dropdown** selector (`<select>` or `<input>`)
- The **Apply / Save button** ID

> **Tip:** many routers load the settings panel inside a hidden `<iframe>`.
> Check `page.frames` in the log output — the driver needs to target the correct frame.

---

#### Step 2 — Create the driver file

```python
# wifi_optimizer/routers/my_router.py
"""
Driver for <Router Brand> <Model> — <ISP, Country>.

Confirmed selectors (firmware version X.Y.Z):
  Login:     #username, #password, #loginBtn
  Channel:   select#ch_2g (2.4 GHz), select#ch_5g (5 GHz)
  Apply:     #btnApply
"""
from __future__ import annotations
import logging
from playwright.sync_api import sync_playwright
from .base import BaseRouter

log = logging.getLogger(__name__)


class MyRouter(BaseRouter):

    def read_channels(self) -> tuple[int | None, int | None]:
        ch24 = ch5 = None
        try:
            with sync_playwright() as p:
                page = self._open(p)
                self._login(page)

                page.click("#wifiMenu")
                page.wait_for_timeout(1_500)

                ch24 = int(page.locator("select#ch_2g").input_value())
                log.info("Current 2.4 GHz channel: ch%s", ch24)

                ch5 = int(page.locator("select#ch_5g").input_value())
                log.info("Current 5 GHz channel: ch%s", ch5)

                page.context.browser.close()
        except Exception as exc:
            log.warning("Could not read channels: %s", exc)
        return ch24, ch5

    def apply_channels(
        self,
        channel_24: int | None,
        channel_5:  int | None,
        *,
        headed: bool = False,
    ) -> None:
        if channel_24 is None and channel_5 is None:
            return
        try:
            with sync_playwright() as p:
                page = self._open(p, headless=not headed)
                self._login(page)

                page.click("#wifiMenu")
                page.wait_for_timeout(1_500)

                if channel_24 is not None:
                    page.locator("select#ch_2g").select_option(str(channel_24))
                    log.info("2.4 GHz → ch%s", channel_24)

                if channel_5 is not None:
                    page.locator("select#ch_5g").select_option(str(channel_5))
                    log.info("5 GHz → ch%s", channel_5)

                page.click("#btnApply")
                page.wait_for_timeout(3_000)   # wait for radio restart
                page.context.browser.close()
        except Exception as exc:
            log.error("Router automation error: %s", exc)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _open(self, p, *, headless: bool = True):
        browser = p.chromium.launch(headless=headless)
        page    = browser.new_context(ignore_https_errors=True).new_page()
        page.set_default_timeout(20_000)
        return page

    def _login(self, page) -> None:
        page.goto(self.url, wait_until="domcontentloaded")
        page.wait_for_selector("#username", state="visible", timeout=10_000)
        page.fill("#username", self.username)
        page.fill("#password", self.password)
        page.click("#loginBtn")
        # Wait for a known post-login element instead of networkidle —
        # most router UIs have constant JS polling that prevents networkidle.
        page.wait_for_selector("#wifiMenu", state="visible", timeout=15_000)
        log.info("Login successful.")
```

> **Important:** avoid `wait_for_load_state("networkidle")` — router admin UIs
> almost always have background JS polling that prevents this state from firing.
> Instead, `wait_for_selector` on a known post-login element.

---

#### Step 3 — Register the driver in `main.py`

```python
# main.py
from wifi_optimizer.routers.my_router import MyRouter   # ← add import

ROUTER_DRIVERS = {
    "huawei_hg8145x6": HuaweiHG8145X6,
    "my_router":        MyRouter,                        # ← add entry
}
```

#### Step 4 — Select it in `.env`

```dotenv
ROUTER_DRIVER=my_router
ROUTER_URL=http://192.168.1.1
ROUTER_USER=admin
ROUTER_PASS=your_password
```

#### Step 5 — Test it

```bash
# Verify read_channels works
python main.py --once --dry-run

# Verify apply_channels works (visible browser)
python main.py --inspect
```

#### Step 6 — Document your selectors in `README.us.md`

Add a row to the **Router compatibility** table and list your confirmed selectors in a new sub-section. This helps other users with the same router/ISP.

---

### Common pitfalls

| Problem | Likely cause | Fix |
|---|---|---|
| Login button not found | Multiple buttons in DOM, only one visible | Use `.nth(1)` or `.filter()` to target the visible one |
| Channel dropdown not found | Panel loads inside an `<iframe>` | Iterate `page.frames` and target the frame that contains the dropdown |
| Timeout after login | Router never reaches `networkidle` due to JS polling | Use `wait_for_selector` on a known post-login element instead |
| Apply does nothing | Submit fires JS, not a real form submit | Use `page.click("#applyBtn")` — avoid `page.evaluate("form.submit()")` |
| Radio restarts and connection drops | Expected behaviour after channel change | Wrap the post-apply wait in `try/except` and log it as informational |

---

## 📜 License

MIT

---

## ☕ Support the project

If this script helped you stabilize your connection, lower your ping, or simply saved you the headache of manually configuring the router, consider supporting me to keep improving this tool and add support for more models.

### 🇨🇱 For Chile (Webpay / Debit / Credit)
You can make an open-amount donation via **Flow**:

[![Flow Pago](https://img.shields.io/badge/Donate_via-Flow-00c0f3?style=for-the-badge&logo=opsgenie&logoColor=white)](https://www.flow.cl/btn.php?token=b7cbabd1861bcf3cbbb6bf9b8cf05af15cce8fc)

### 🌎 International (PayPal / Credit Card)
Support the development via **Buy Me a Coffee**:

[![Buy Me a Coffee](https://img.shields.io/badge/Buy_Me_a_Coffee-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://www.buymeacoffee.com/matiasmlforever)

> **Tip:** You can also use the **Sponsor** button at the top of this repository for recurring donations via GitHub Sponsors.
