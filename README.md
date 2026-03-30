# 📡 WiFi Channel Optimizer

Automated Wi-Fi channel optimizer for **Huawei HG8145X6** (ONT WiFi 6) — tested with **Entel ISP (Chile)**.

Scans the surrounding RF spectrum, selects the least-congested channel for both 2.4 GHz and 5 GHz bands, and reconfigures the router automatically using browser automation.

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
| **Quality monitoring** | Measures ping + download speed before and after the change |
| **Auto-revert** | Reverts to the previous channel if quality degrades after 30 min |
| **Daemon mode** | Runs continuously, re-scanning every 5 minutes |
| **`.env` config** | All credentials and tuning parameters live outside the source code |

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
```

### CLI flags

| Flag | Description |
|---|---|
| *(none)* | Daemon mode — continuous loop, Ctrl+C to stop |
| `--once` | Single optimization cycle and exit |
| `--dry-run` | Full cycle (scan + score + ping) but **no router changes** |
| `--inspect` | Opens Chromium in headed mode + dumps diagnostic HTML files |

---

## 🔧 Configuration reference (`.env`)

| Variable | Default | Description |
|---|---|---|
| `ROUTER_URL` | `http://192.168.100.1` | Router admin panel URL |
| `ROUTER_USER` | `admin` | Admin username |
| `ROUTER_PASS` | `admin` | Admin password |
| `SCAN_INTERVAL_SECONDS` | `300` | Seconds between scans in daemon mode |
| `TRIAL_PERIOD_SECONDS` | `1800` | Seconds to wait before quality check after a channel change |
| `PING_DEGRADATION_MS` | `100` | Extra latency (ms) that triggers a revert |
| `SPEED_DEGRADATION_PCT` | `0.40` | Download speed drop fraction (0.40 = 40 %) that triggers a revert |

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
2026-03-29 21:47:01 [INFO] Canal actual 2.4 GHz: ch6
2026-03-29 21:47:01 [INFO] Congestion scores 2.4 GHz: {1: -563.0, 6: -227.0, 11: -222.5}
2026-03-29 21:47:01 [INFO] 2.4 GHz — actual: ch6 (-227.0), óptimo: ch11 (-222.5), mejora: 2.0% → mantener
2026-03-29 21:47:01 [INFO] Canal ya óptimo o dentro del umbral de histéresis. Sin cambios.
```

---

## 📁 Project structure

```
WifiChannelOptimizer/
├── main.py              # All logic (scanner, decision, Playwright automation)
├── .env.example         # Config template — copy to .env and fill in
├── .env                 # Your local credentials (git-ignored)
├── pyproject.toml       # Project metadata and dependencies
├── .gitignore
├── README.md
└── wifi_optimizer.log   # Runtime log (git-ignored)
```

---

## 📜 License

MIT
