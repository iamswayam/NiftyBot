# 🤖 Nifty Options Auto Trading Bot
### Kotak Neo API — Fully Automated F&O Trade Management

![Python](https://img.shields.io/badge/Python-3.12-blue)
![Broker](https://img.shields.io/badge/Broker-Kotak%20Neo-red)
![Market](https://img.shields.io/badge/Market-NSE%20F%26O-green)
![Status](https://img.shields.io/badge/Status-Active-brightgreen)

---

## 📌 What is this?

A semi-automated options trading bot for **Nifty 50** on **Kotak Neo** broker platform.

**Your job:** Run the bot → Choose CE or PE → Confirm trade
**Bot's job:** Find the right strike, place order, manage SL, trail profits, exit, activate Kill Switch

No more sitting and watching charts. No more manually moving stop losses. No emotional exits.

---

## ⚡ Features

- 🔍 **Smart Strike Scanner** — Scans all Nifty strikes and finds the best one based on LTP range
- 📥 **Auto Order Placement** — Places market order on the selected strike
- 🛡️ **Auto Stop Loss** — Places SL-M order immediately after entry fills
- 🎯 **Auto Target** — Places limit target order simultaneously
- 📈 **Trailing Stop Loss** — Automatically trails SL as price moves in your favour
- 💰 **Margin Check** — Verifies available funds before placing any order
- 🔴 **Kill Switch** — Auto-activates Kotak Kill Switch after trade closes
- 📱 **Telegram Alerts** — Optional trade alerts on your phone
- 📋 **Clean Logging** — Daily log file (no sensitive data logged)

---

## 🧠 How Strike Selection Works

Bot scans Nifty option strikes and picks the best one based on LTP:

```
Priority 1 → LTP between ₹80 – ₹90   (sweet spot)
Priority 2 → LTP between ₹75 – ₹95   (acceptable)
Priority 3 → LTP between ₹70 – ₹100  (wider)
... keeps widening until a strike is found

If 2 strikes found in same range → picks HIGHER LTP
Expiry auto-detected from Kotak scrip master — no hardcoding
```

This automatically selects slightly **OTM options** — better R:R than ATM, not too cheap like deep OTM.

---

## 📊 SL / Target / Trailing SL Logic

```
Entry fills at ₹91.00 (market price)

Initial SL     = Entry - 10 pts  →  ₹81.00
Initial Target = Entry + 30 pts  →  ₹121.00

When price hits +30 pts (₹121.00):
  → TSL activates at Entry + 25 pts = ₹116.00
  → 25 pts of profit is now LOCKED IN
  → Target order cancelled

After TSL activates — every new 10 pt high:
  → TSL moves up, always 10 pts below new high

Example:
  Entry    = ₹91.00   SL = ₹81.00   Target = ₹121.00
  +30 hit  = ₹121.00  →  TSL = ₹116.00  (locked!)
  New high = ₹131.00  →  TSL = ₹121.00
  New high = ₹141.00  →  TSL = ₹131.00
  Price drops ₹129    →  TSL hit ₹131.00  →  EXIT ✅
  Final P&L = +40 pts
```

---

## 🔴 Kill Switch — Important Note

> Kotak Neo Kill Switch **does NOT block API orders**.
> It only blocks orders placed manually on the app/website.

This bot handles safety two ways:
1. **Bot stops itself** after trade closes → no more API orders possible
2. **Selenium automation** clicks Kill Switch on Kotak website → blocks manual orders too

---

## 📁 Project Structure

```
NiftyBot/
├── trading_bot.py      # Main bot — run this every trading day
├── kill_switch.py      # Automated Kill Switch via browser
├── .env                # Your credentials (NEVER pushed to Git)
├── .env.example        # Credential template (safe to push)
├── .gitignore          # Protects .env, logs and cache from Git
├── README.md           # This file
└── temp/               # Auto-created — scrip master daily cache (not pushed)
```

---

## ⚙️ Setup

### Prerequisites
- Python 3.12+
- Kotak Neo account with F&O trading activated
- Kotak Neo Trade API access (free — generate from app)
- TOTP registered on Kotak Neo
- Chrome browser (for Kill Switch automation)

### 1. Clone the repo
```bash
git clone https://github.com/yourusername/NiftyBot.git
cd NiftyBot
```

### 2. Create virtual environment
```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Mac/Linux
source venv/bin/activate
```

### 3. Install dependencies
```bash
pip install pyotp requests selenium webdriver-manager python-dotenv
```

### 4. Configure credentials
```bash
cp .env.example .env
```

Open `.env` and fill in your details:
```env
CONSUMER_KEY=your_consumer_key
MOBILE_NUMBER=+919999999999
UCC=your_client_code
MPIN=123456
TOTP_SECRET=your_totp_secret
TELEGRAM_TOKEN=          # optional
TELEGRAM_CHAT=           # optional
```

#### Where to get these:
| Field | Where to find |
|---|---|
| `CONSUMER_KEY` | Kotak Neo app → Invest → Trade API → Generate Application |
| `MOBILE_NUMBER` | Your registered mobile with +91 |
| `UCC` | Kotak Neo app → Profile → Account Details (Client ID) |
| `MPIN` | Your 6-digit Kotak MPIN |
| `TOTP_SECRET` | kotaksecurities.com → Platform → Trade API → Register TOTP → text below QR code |

---

## 🚀 Running the Bot

```bash
# Every trading day:
cd NiftyBot
venv\Scripts\activate        # Windows
python trading_bot.py
```

**What you'll see:**
```
╔════════════════════════════════════════════════════════════╗
║        KOTAK NEO — NIFTY OPTIONS AUTO BOT                ║
╚════════════════════════════════════════════════════════════╝

🔐 Logging into Kotak Neo...
  ✅ Login successful!

  What is your trade direction?
    [1]  PUT  (PE) — Nifty going DOWN
    [2]  CALL (CE) — Nifty going UP

  → Your choice: 1

  📥 Downloading scrip master...
  ✅ Loaded 11031 Nifty F&O tokens

  📊 Nifty Spot: 25178  |  ATM: 25200  |  Expiry: 05 Mar 2026

  Strike LTPs found:
    25100PE  →  ₹87.75   ← selected (band ₹80–90)
    25150PE  →  ₹65.20
    25200PE  →  ₹48.40 ← ATM

  💰 Available: ₹15,420  Required: ₹6,581  ✅

  Confirm? yes

  ✅ FILLED at ₹88.20  |  SL: ₹78.20  |  Target: ₹118.20
  LTP:₹94.50 | P&L:+6.30pts | SL:₹78.20 | TSL=inactive
```

---

## 📊 Nifty Settings

| Setting | Value |
|---|---|
| Lot size | 75 units |
| Strike gap | 50 points |
| Expiry | Nearest Thursday (weekly) |
| Order type | Market |
| Product | MIS (intraday) |
| LTP target range | ₹80–90 (priority) |

---

## ⚠️ Disclaimer

> **This bot is for personal educational use only.**
>
> - Trading F&O involves significant risk. 9 out of 10 retail traders lose money in F&O (SEBI study, Jan 2023).
> - Past performance does not guarantee future results.
> - Always trade with money you can afford to lose.
> - The author is not responsible for any financial losses.
> - This is not financial advice.

---

## 🛠️ Tech Stack

| Tool | Purpose |
|---|---|
| Python 3.12 | Core language |
| Kotak Neo Trade API v2 | Broker integration |
| pyotp | TOTP authentication |
| Selenium | Kill Switch automation |
| python-dotenv | Secure credential management |
| Telegram Bot API | Trade alerts |

---

## 📄 License

MIT License — free to use, modify and distribute.

---

*Built for personal use. Trade responsibly.*
