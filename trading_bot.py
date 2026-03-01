"""
╔══════════════════════════════════════════════════════════════════════╗
║       KOTAK NEO — NIFTY OPTIONS AUTO BOT (REST API v2)             ║
║  No SDK needed — uses direct REST API calls                        ║
║                                                                    ║
║  YOUR JOB: Run bot → Choose CE or PE → Done!                       ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import sys
import json
import time
import logging
import threading
import datetime
import requests
import pyotp
from dotenv import load_dotenv
import os

# ─────────────────────────────────────────────────────────────────────
# CONFIG — Loaded from .env file (never hardcode secrets here!)
# Create a .env file in the same folder with your credentials.
# ─────────────────────────────────────────────────────────────────────
load_dotenv()

CONFIG = {
    "CONSUMER_KEY":   os.getenv("CONSUMER_KEY"),
    "MOBILE_NUMBER":  os.getenv("MOBILE_NUMBER"),
    "UCC":            os.getenv("UCC"),
    "MPIN":           os.getenv("MPIN"),
    "TOTP_SECRET":    os.getenv("TOTP_SECRET"),
    "TELEGRAM_TOKEN": os.getenv("TELEGRAM_TOKEN", ""),
    "TELEGRAM_CHAT":  os.getenv("TELEGRAM_CHAT", ""),
    "AUTO_KILL_SWITCH": True,
}

# Validate all required keys are present
_required = ["CONSUMER_KEY", "MOBILE_NUMBER", "UCC", "MPIN", "TOTP_SECRET"]
_missing  = [k for k in _required if not CONFIG.get(k)]
if _missing:
    print(f"\n  ❌ Missing in .env file: {', '.join(_missing)}")
    print("  Open .env and fill in all required values.\n")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────
# NIFTY SETTINGS
# ─────────────────────────────────────────────────────────────────────
NIFTY = {
    "name":       "NIFTY",
    "exchange":   "nse_fo",
    "lot_size":   75,
    "strike_gap": 50,
    "ltp_bands": [
        (80, 90),
        (75, 95),
        (70, 100),
        (65, 105),
        (60, 110),
        (55, 115),
        (50, 120),
    ],
}

# SL / Target / Trailing SL rules
RULES = {
    "initial_sl_pts":      10,
    "initial_target_pts":  30,
    "tsl_activate_pts":    30,
    "tsl_activate_gap":    25,   # TSL = Entry + 25pts when activated
    "tsl_trail_every_pts": 10,
    "tsl_trail_gap":       10,
}

# Fixed login endpoints (do NOT use baseUrl for these)
LOGIN_URL_1 = "https://mis.kotaksecurities.com/login/1.0/tradeApiLogin"
LOGIN_URL_2 = "https://mis.kotaksecurities.com/login/1.0/tradeApiValidate"
NEO_FIN_KEY = "neotradeapi"

# ─────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"nifty_bot_{datetime.date.today()}.log")
    ]
)
log = logging.getLogger("NiftyBot")


# ─────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────
def clr(text, color="white"):
    codes = {
        "red": "\033[91m", "green": "\033[92m", "yellow": "\033[93m",
        "cyan": "\033[96m", "white": "\033[97m", "bold": "\033[1m",
        "reset": "\033[0m"
    }
    return f"{codes.get(color,'')}{text}{codes['reset']}"


def divider(char="─", color="blue"):
    print(clr(char * 62, color))


def alert(msg: str):
    log.info(msg)
    token = CONFIG.get("TELEGRAM_TOKEN")
    chat  = CONFIG.get("TELEGRAM_CHAT")
    if token and chat:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat, "text": f"🤖 NiftyBot\n{msg}"},
                timeout=5
            )
        except Exception:
            pass


def get_nearest_thursday() -> datetime.date:
    today = datetime.date.today()
    days  = (3 - today.weekday()) % 7
    return today if days == 0 else today + datetime.timedelta(days=days)


def build_symbol(strike: int, option_type: str, expiry: datetime.date) -> str:
    """Build trading symbol e.g. NIFTY25JAN2324000CE"""
    months = ["JAN","FEB","MAR","APR","MAY","JUN",
              "JUL","AUG","SEP","OCT","NOV","DEC"]
    yy  = str(expiry.year)[2:]
    mon = months[expiry.month - 1]
    dd  = str(expiry.day).zfill(2)
    return f"NIFTY{yy}{mon}{dd}{strike}{option_type}"


# ─────────────────────────────────────────────────────────────────────
# KOTAK SESSION — Direct REST login
# ─────────────────────────────────────────────────────────────────────
class KotakSession:
    def __init__(self):
        self.access_token = CONFIG["CONSUMER_KEY"]  # Consumer key = access token
        self.auth_token   = None   # Session token (Trade token)
        self.sid          = None   # Session SID
        self.base_url     = None   # Dynamic base URL from login

    def login(self):
        print(clr("🔐 Logging into Kotak Neo...", "yellow"))

        # Generate TOTP
        totp = pyotp.TOTP(CONFIG["TOTP_SECRET"])
        otp  = totp.now()
        log.info(f"TOTP generated: {otp}")

        # ── Step 1: TOTP Login ────────────────────────────────────────
        headers1 = {
            "Authorization": self.access_token,
            "neo-fin-key":   NEO_FIN_KEY,
            "Content-Type":  "application/json"
        }
        body1 = {
            "mobileNumber": CONFIG["MOBILE_NUMBER"],
            "ucc":          CONFIG["UCC"],
            "totp":         otp
        }

        try:
            resp1 = requests.post(LOGIN_URL_1, headers=headers1, json=body1, timeout=15)
            data1 = resp1.json()
            log.info(f"Login Step 1: {data1.get('data', {}).get('status', 'unknown')}")
        except Exception as e:
            print(clr(f"❌ Login Step 1 failed: {e}", "red"))
            sys.exit(1)

        if data1.get("data", {}).get("token") is None:
            print(clr(f"❌ Step 1 failed: {data1}", "red"))
            print(clr("  Check MOBILE_NUMBER, UCC, TOTP_SECRET in CONFIG", "yellow"))
            sys.exit(1)

        view_token = data1["data"]["token"]
        view_sid   = data1["data"]["sid"]
        print(clr("  ✅ Step 1 done (TOTP verified)", "green"))

        # ── Step 2: MPIN Validate ────────────────────────────────────
        headers2 = {
            "Authorization": self.access_token,
            "neo-fin-key":   NEO_FIN_KEY,
            "sid":           view_sid,
            "Auth":          view_token,
            "Content-Type":  "application/json"
        }
        body2 = {"mpin": CONFIG["MPIN"]}

        try:
            resp2 = requests.post(LOGIN_URL_2, headers=headers2, json=body2, timeout=15)
            data2 = resp2.json()
            log.info(f"Login Step 2: {data2.get('data', {}).get('kType', 'unknown')} | baseUrl: {data2.get('data', {}).get('baseUrl', '')}")
        except Exception as e:
            print(clr(f"❌ Login Step 2 failed: {e}", "red"))
            sys.exit(1)

        if data2.get("data", {}).get("token") is None:
            print(clr(f"❌ Step 2 failed: {data2}", "red"))
            print(clr("  Check MPIN in CONFIG", "yellow"))
            sys.exit(1)

        self.auth_token = data2["data"]["token"]
        self.sid        = data2["data"]["sid"]
        self.base_url   = data2["data"]["baseUrl"]

        print(clr("  ✅ Step 2 done (MPIN verified)", "green"))
        print(clr(f"  ✅ Login successful! Base URL: {self.base_url}\n", "green"))
        return self

    # ── Common headers for post-login APIs ───────────────────────────
    def trade_headers(self):
        return {
            "Auth":         self.auth_token,
            "Sid":          self.sid,
            "neo-fin-key":  NEO_FIN_KEY,
            "accept":       "application/json",
            "Content-Type": "application/x-www-form-urlencoded"
        }

    # ── Quotes headers ───────────────────────────────────────────────
    def quote_headers(self):
        return {
            "Authorization": self.access_token,
            "Content-Type":  "application/json"
        }


# ─────────────────────────────────────────────────────────────────────
# SCRIP MASTER — Downloads CSV and builds token lookup
# ─────────────────────────────────────────────────────────────────────
class ScripMaster:
    def __init__(self, session: KotakSession):
        self.session = session
        self._token_map = {}   # symbol → instrument_token

    def load(self):
        """Download NSE F&O scrip master CSV and build token map."""
        print(clr("  📥 Downloading scrip master (instrument tokens)...", "yellow"))
        try:
            # Step 1: Get file paths
            url  = f"{self.session.base_url}/script-details/1.0/masterscrip/file-paths"
            resp = requests.get(url, headers=self.session.quote_headers(), timeout=15)
            data = resp.json()
            paths = data.get("data", {}).get("filesPaths", [])

            # Find nse_fo CSV
            nse_fo_url = next((p for p in paths if "nse_fo" in p), None)
            if not nse_fo_url:
                print(clr("  ❌ nse_fo scrip master not found.", "red"))
                return False

            # Step 2: Download CSV
            csv_resp = requests.get(nse_fo_url, timeout=30)
            lines    = csv_resp.text.strip().split("\n")
            headers  = lines[0].split(",")

            # Find column indices
            try:
                idx_token  = headers.index("pSymbol")
                idx_trd    = headers.index("pTrdSymbol")
                idx_name   = headers.index("pSymbolName")
            except ValueError as e:
                print(clr(f"  ❌ CSV column not found: {e}", "red"))
                return False

            # Build map: trading_symbol → instrument_token
            count = 0
            for line in lines[1:]:
                cols = line.split(",")
                if len(cols) <= max(idx_token, idx_trd, idx_name):
                    continue
                token   = cols[idx_token].strip()
                trd_sym = cols[idx_trd].strip()
                name    = cols[idx_name].strip()
                if "NIFTY" in name.upper() and token:
                    self._token_map[trd_sym] = token
                    count += 1

            print(clr(f"  ✅ Loaded {count} Nifty F&O tokens from scrip master", "green"))
            return True

        except Exception as e:
            log.error(f"Scrip master error: {e}")
            print(clr(f"  ❌ Scrip master failed: {e}", "red"))
            return False

    def get_token(self, trading_symbol: str) -> str | None:
        return self._token_map.get(trading_symbol)


# ─────────────────────────────────────────────────────────────────────
# STRIKE SCANNER
# ─────────────────────────────────────────────────────────────────────
class StrikeScanner:
    def __init__(self, session: KotakSession, scrip_master: ScripMaster):
        self.session = session
        self.scrip   = scrip_master

    def get_ltp_by_token(self, token: str) -> float | None:
        """Fetch LTP using numeric instrument token."""
        try:
            url  = f"{self.session.base_url}/script-details/1.0/quotes/neosymbol/nse_fo|{token}/ltp"
            resp = requests.get(url, headers=self.session.quote_headers(), timeout=10)
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                ltp = data[0].get("ltp")
                if ltp:
                    return float(ltp)
        except Exception as e:
            log.debug(f"LTP error token={token}: {e}")
        return None

    def get_nifty_spot(self) -> float | None:
        """Get Nifty 50 spot price using index name."""
        try:
            url  = f"{self.session.base_url}/script-details/1.0/quotes/neosymbol/nse_cm|Nifty 50/ltp"
            resp = requests.get(url, headers=self.session.quote_headers(), timeout=10)
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                ltp = data[0].get("ltp")
                if ltp:
                    return float(ltp)
        except Exception as e:
            log.debug(f"Spot error: {e}")
        return None

    def get_atm_strike(self, spot: float) -> int:
        gap = NIFTY["strike_gap"]
        return int(round(spot / gap) * gap)

    def scan(self, option_type: str, expiry: datetime.date) -> dict | None:
        spot = self.get_nifty_spot()
        if not spot:
            print(clr("  ❌ Could not fetch Nifty spot price.", "red"))
            return None

        atm = self.get_atm_strike(spot)
        print(clr(f"\n  📊 Nifty Spot: {spot:.2f}  |  ATM: {atm}  |  Expiry: {expiry}", "yellow"))
        print(clr(f"  🔍 Scanning {option_type} strikes...\n", "cyan"))

        # Build strike list — ATM ± 10 strikes
        strikes = [atm + (i * NIFTY["strike_gap"]) for i in range(-10, 11) if atm + (i * NIFTY["strike_gap"]) > 0]

        # Fetch LTPs using instrument tokens
        strike_ltps  = {}
        strike_tokens = {}
        strike_syms  = {}

        print(clr("  Fetching LTPs... ", "white"), end="", flush=True)
        for strike in strikes:
            sym   = build_symbol(strike, option_type, expiry)
            token = self.scrip.get_token(sym)
            if not token:
                log.debug(f"No token for {sym}")
                continue
            ltp = self.get_ltp_by_token(token)
            if ltp and ltp > 0:
                strike_ltps[strike]   = ltp
                strike_tokens[strike] = token
                strike_syms[strike]   = sym
            time.sleep(0.1)
        print(clr("Done!\n", "green"))

        if not strike_ltps:
            print(clr("  ❌ No LTPs fetched.", "red"))
            print(clr("  This is normal outside market hours (9:15 AM – 3:30 PM).", "yellow"))
            print(clr("  Run again tomorrow during market hours.", "yellow"))
            return None

        # Show what we found
        print(clr("  Strike LTPs found:", "cyan"))
        for strike in sorted(strike_ltps.keys(), reverse=(option_type == "CE")):
            ltp    = strike_ltps[strike]
            marker = " ← ATM" if strike == atm else ""
            print(clr(f"    {strike}{option_type}  →  ₹{ltp:.2f}{marker}", "white"))

        # Scan priority bands
        print()
        for band_low, band_high in NIFTY["ltp_bands"]:
            matches = {s: l for s, l in strike_ltps.items() if band_low <= l <= band_high}

            if matches:
                print(clr(f"  ✅ Found in band ₹{band_low}–₹{band_high}:", "green"))
                for s, l in matches.items():
                    print(clr(f"     {s}{option_type}  →  ₹{l:.2f}", "white"))

                # Pick HIGHER LTP if multiple
                best_strike = max(matches, key=lambda s: matches[s])
                best_ltp    = matches[best_strike]
                best_symbol = strike_syms[best_strike]
                best_token  = strike_tokens[best_strike]

                if len(matches) > 1:
                    print(clr(f"  ↑ Multiple found → Selected HIGHER LTP: {best_strike}{option_type} @ ₹{best_ltp:.2f}", "cyan"))
                else:
                    print(clr(f"  → Selected: {best_strike}{option_type} @ ₹{best_ltp:.2f}", "cyan"))

                return {
                    "symbol":      best_symbol,
                    "token":       best_token,
                    "strike":      best_strike,
                    "ltp":         best_ltp,
                    "band":        (band_low, band_high),
                    "option_type": option_type,
                    "expiry":      expiry,
                    "spot":        spot,
                    "atm":         atm,
                }
            else:
                print(clr(f"  ✗ ₹{band_low}–₹{band_high} → nothing", "white"))

        print(clr("\n  ❌ No suitable strike found.", "red"))
        return None


# ─────────────────────────────────────────────────────────────────────
# ORDER MANAGER — Direct REST API calls
# ─────────────────────────────────────────────────────────────────────
class OrderManager:
    def __init__(self, session: KotakSession):
        self.session = session

    def _post(self, endpoint: str, jdata: dict) -> dict:
        url  = f"{self.session.base_url}{endpoint}"
        resp = requests.post(
            url,
            headers=self.session.trade_headers(),
            data={"jData": json.dumps(jdata)},
            timeout=15
        )
        return resp.json()

    def _get(self, endpoint: str) -> dict:
        url  = f"{self.session.base_url}{endpoint}"
        resp = requests.get(url, headers=self.session.trade_headers(), timeout=15)
        return resp.json()

    def place_order(self, symbol: str, qty: int, order_type: str,
                    transaction: str, price: str = "0",
                    trigger: str = "0", tag: str = "") -> str | None:
        """Place order. Returns order number or None."""
        jdata = {
            "am":  "NO",
            "dq":  "0",
            "es":  NIFTY["exchange"],
            "mp":  "0",
            "pc":  "MIS",
            "pf":  "N",
            "pr":  price,
            "pt":  order_type,    # L, MKT, SL-M
            "qt":  str(qty),
            "rt":  "DAY",
            "tp":  trigger,
            "ts":  symbol,
            "tt":  transaction,   # B or S
            "tg":  tag
        }
        try:
            resp = self._post("/quick/order/rule/ms/place", jdata)
            log.info(f"Place order: stat={resp.get('stat')} ordNo={resp.get('nOrdNo','-')}")
            if resp.get("stat") == "Ok":
                return resp.get("nOrdNo")
            else:
                log.error(f"Order failed: {resp.get('emsg', resp)}")
                return None
        except Exception as e:
            log.error(f"Place order exception: {e}")
            return None

    def modify_order(self, order_no: str, symbol: str, qty: int,
                     order_type: str, transaction: str,
                     price: str = "0", trigger: str = "0") -> bool:
        """Modify existing order. Returns True if success."""
        jdata = {
            "no":  order_no,
            "am":  "NO",
            "dq":  "0",
            "es":  NIFTY["exchange"],
            "mp":  "0",
            "pc":  "MIS",
            "pr":  price,
            "pt":  order_type,
            "qt":  str(qty),
            "rt":  "DAY",
            "tp":  trigger,
            "ts":  symbol,
            "tt":  transaction,
        }
        try:
            resp = self._post("/quick/order/vr/modify", jdata)
            log.info(f"Modify order: stat={resp.get('stat')} ordNo={resp.get('nOrdNo','-')}")
            return resp.get("stat") == "Ok"
        except Exception as e:
            log.error(f"Modify order exception: {e}")
            return False

    def cancel_order(self, order_no: str) -> bool:
        """Cancel order. Returns True if success."""
        jdata = {"on": order_no, "am": "NO"}
        try:
            resp = self._post("/quick/order/cancel", jdata)
            log.info(f"Cancel order: stat={resp.get('stat')}")
            return resp.get("stat") == "Ok"
        except Exception as e:
            log.error(f"Cancel order exception: {e}")
            return False

    def get_orders(self) -> list:
        """Fetch order book."""
        try:
            resp = self._get("/quick/user/orders")
            return resp.get("data", []) if resp.get("stat") == "Ok" else []
        except Exception as e:
            log.error(f"Get orders error: {e}")
            return []

    def get_order_status(self, order_no: str) -> str | None:
        """Get status of a specific order."""
        orders = self.get_orders()
        for o in orders:
            if str(o.get("nOrdNo")) == str(order_no):
                return o.get("ordSt", "").upper()
        return None

    def get_fill_price(self, order_no: str) -> float | None:
        """Get average fill price of a completed order."""
        orders = self.get_orders()
        for o in orders:
            if str(o.get("nOrdNo")) == str(order_no):
                avg = o.get("avgPrc", "0")
                return float(avg) if avg and float(avg) > 0 else None
        return None


    def check_margin(self, symbol: str, qty: int, ltp: float) -> dict:
        """
        Check if sufficient margin available before placing order.
        Returns dict with: ok (bool), available (float), required (float), shortfall (float)
        """
        token = None
        # Try to get token from trade result if available
        jdata = {
            "brkName": "KOTAK",
            "brnchId": "ONLINE",
            "exSeg":   NIFTY["exchange"],
            "prc":     str(ltp),
            "prcTp":   "MKT",
            "prod":    "MIS",
            "qty":     str(qty),
            "tok":     "0",       # token not mandatory for estimate
            "trnsTp":  "B"
        }
        try:
            url  = f"{self.session.base_url}/quick/user/check-margin"
            resp = requests.post(
                url,
                headers=self.session.trade_headers(),
                data={"jData": json.dumps(jdata)},
                timeout=15
            )
            data = resp.json()
            log.info(f"Margin check: avl={data.get('avlCash',0)} req={data.get('reqdMrgn',0)} valid={data.get('rmsVldtd','?')}")

            avl  = float(data.get("avlCash", 0) or data.get("avlMrgn", 0) or 0)
            req  = float(data.get("reqdMrgn", 0) or data.get("ordMrgn", 0) or 0)
            insuf = float(data.get("insufFund", 0) or 0)
            valid = data.get("rmsVldtd", "NOT_OK").upper()

            return {
                "ok":        valid == "OK" or insuf <= 0,
                "available": avl,
                "required":  req,
                "shortfall": max(insuf, 0),
                "raw":       data
            }
        except Exception as e:
            log.error(f"Margin check error: {e}")
            # If margin check fails, allow trade with warning
            return {"ok": True, "available": 0, "required": 0, "shortfall": 0, "raw": {}}


# ─────────────────────────────────────────────────────────────────────
# TRADE MANAGER
# ─────────────────────────────────────────────────────────────────────
class TradeManager:
    def __init__(self, session: KotakSession, scan_result: dict):
        self.session  = session
        self.orders   = OrderManager(session)
        self.scanner  = None  # set after init via set_scanner()
        self.trade    = scan_result
        self.symbol   = scan_result["symbol"]
        self.qty      = NIFTY["lot_size"]
        self.active   = False
        self._lock    = threading.Lock()

        # Order IDs
        self.entry_id  = None
        self.sl_id     = None
        self.target_id = None

        # Price tracking
        self.entry_price  = None
        self.sl_price     = None
        self.target_price = None
        self.current_ltp  = None

        # TSL state
        self.tsl_active    = False
        self.tsl_price     = None
        self.tsl_last_high = None

    def enter(self):
        """Check margin then place market entry order."""

        # ── Margin check BEFORE placing order ────────────────────────
        ltp = self.trade.get("ltp", 100)
        est_cost = ltp * self.qty  # rough estimate

        print(clr(f"\n  💰 Checking margin...", "yellow"))
        margin = self.orders.check_margin(self.symbol, self.qty, ltp)

        print(clr(f"  Available funds: ₹{margin['available']:,.2f}", "white"))
        print(clr(f"  Required margin: ₹{margin['required']:,.2f}", "white"))
        print(clr(f"  Est. cost (LTP × lot): ₹{est_cost:,.2f}", "white"))

        if not margin["ok"]:
            shortfall = margin["shortfall"]
            print(clr(f"\n  ❌ INSUFFICIENT FUNDS!", "red"))
            print(clr(f"  Shortfall: ₹{shortfall:,.2f}", "red"))
            print(clr(f"  Add ₹{shortfall:,.2f} to your Kotak account and try again.", "yellow"))
            alert(f"❌ INSUFFICIENT FUNDS\nShortfall: ₹{shortfall:,.2f}\nAdd funds and retry.")
            return

        print(clr(f"  ✅ Sufficient funds available!", "green"))

        # ── Place order ───────────────────────────────────────────────
        print(clr(f"\n  📥 Placing MARKET order: {self.symbol}  |  Qty: {self.qty}", "yellow"))
        alert(f"📥 Placing order\n{self.symbol}\n1 lot ({self.qty} qty)\nFunds: ₹{margin['available']:,.0f}")

        order_id = self.orders.place_order(
            symbol=self.symbol,
            qty=self.qty,
            order_type="MKT",
            transaction="B",
            tag="BOT_ENTRY"
        )

        if order_id:
            self.entry_id = order_id
            print(clr(f"  ✅ Entry order placed! ID: {order_id}", "green"))
            threading.Thread(target=self._wait_for_fill, daemon=True).start()
        else:
            print(clr("  ❌ Entry order failed!", "red"))
            print(clr("  Possible reasons: Insufficient funds | Segment not activated | Market closed", "yellow"))
            alert("❌ Entry order FAILED!\nCheck funds and F&O segment activation.")

    def _wait_for_fill(self):
        """Wait for entry order to fill."""
        log.info("👁️ Waiting for entry fill...")
        for _ in range(300):
            status = self.orders.get_order_status(self.entry_id)
            if status in ["COMPLETE", "TRADED"]:
                fill_price = self.orders.get_fill_price(self.entry_id)
                if not fill_price:
                    # Fallback to scan LTP
                    fill_price = self.trade["ltp"]
                self._on_fill(fill_price)
                return
            elif status in ["CANCELLED", "REJECTED"]:
                print(clr(f"\n  ❌ Entry order {status}", "red"))
                alert(f"❌ Entry {status}")
                return
            time.sleep(1)
        print(clr("\n  ⏰ Entry not filled in 5 min", "red"))

    def _on_fill(self, fill_price: float):
        """Entry filled — set up SL and Target."""
        self.entry_price  = fill_price
        self.sl_price     = round(fill_price - RULES["initial_sl_pts"], 2)
        self.target_price = round(fill_price + RULES["initial_target_pts"], 2)
        self.tsl_last_high = fill_price

        print(clr(f"\n  ✅ FILLED at ₹{fill_price:.2f}", "green"))
        print(clr(f"  🛡️  SL     = ₹{self.sl_price:.2f}  (-{RULES['initial_sl_pts']} pts)", "red"))
        print(clr(f"  🎯  Target = ₹{self.target_price:.2f}  (+{RULES['initial_target_pts']} pts)", "green"))
        print(clr(f"  📈  TSL activates at ₹{fill_price + RULES['tsl_activate_pts']:.2f}  (+{RULES['tsl_activate_pts']} pts)", "cyan"))
        divider()

        alert(f"✅ FILLED at ₹{fill_price:.2f}\nSL: ₹{self.sl_price:.2f}\nTarget: ₹{self.target_price:.2f}")

        # Place SL order
        self.sl_id = self.orders.place_order(
            symbol=self.symbol, qty=self.qty,
            order_type="SL-M", transaction="S",
            trigger=str(self.sl_price), tag="BOT_SL"
        )
        if self.sl_id:
            print(clr(f"  🛡️  SL order placed @ ₹{self.sl_price:.2f} | ID: {self.sl_id}", "cyan"))
        else:
            print(clr(f"  ❌ SL ORDER FAILED! Set manual SL at ₹{self.sl_price:.2f}", "red"))
            alert(f"❌ SL FAILED! Manual SL at ₹{self.sl_price:.2f}")

        # Place Target order
        self.target_id = self.orders.place_order(
            symbol=self.symbol, qty=self.qty,
            order_type="L", transaction="S",
            price=str(self.target_price), tag="BOT_TARGET"
        )
        if self.target_id:
            print(clr(f"  🎯  Target order placed @ ₹{self.target_price:.2f} | ID: {self.target_id}", "cyan"))
        else:
            print(clr(f"  ❌ TARGET FAILED! Set manual target at ₹{self.target_price:.2f}", "red"))
            alert(f"❌ Target FAILED! Manual target at ₹{self.target_price:.2f}")

        # Start monitoring
        self.active = True
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _monitor_loop(self):
        """Poll LTP every 2 seconds. Handle TSL and order completion."""
        last_display = None

        while self.active:
            try:
                ltp = self.scanner.get_ltp_by_token(self.trade.get("token", ""))
                if ltp:
                    self.current_ltp = ltp
                    self._process_price(ltp)

                    # Live display
                    if ltp != last_display:
                        profit = ltp - self.entry_price
                        tsl_str = f"TSL=₹{self.tsl_price:.2f}" if self.tsl_active else "TSL=inactive"
                        color   = "green" if profit >= 0 else "red"
                        sign    = "+" if profit >= 0 else ""
                        sys.stdout.write(clr(
                            f"\r  LTP:₹{ltp:.2f} | P&L:{sign}{profit:.2f}pts"
                            f" | SL:₹{self.sl_price:.2f} | {tsl_str}"
                            f" | Tgt:₹{self.target_price:.2f}   ", color
                        ))
                        sys.stdout.flush()
                        last_display = ltp

                # Check order completion every 5 seconds
                self._check_order_completion()

            except Exception as e:
                log.debug(f"Monitor: {e}")

            time.sleep(2)

    def _process_price(self, ltp: float):
        """TSL logic."""
        with self._lock:
            if not self.active or not self.entry_price:
                return

            profit = ltp - self.entry_price

            if not self.tsl_active:
                # TSL activation check
                if profit >= RULES["tsl_activate_pts"]:
                    self.tsl_active    = True
                    self.tsl_price     = round(self.entry_price + RULES["tsl_activate_gap"], 2)
                    self.tsl_last_high = ltp

                    print(clr(f"\n\n  🔔 TSL ACTIVATED at +{RULES['tsl_activate_pts']}pts!", "cyan"))
                    print(clr(f"     LTP=₹{ltp:.2f} → TSL=₹{self.tsl_price:.2f} (Entry+25pts locked!)", "cyan"))
                    alert(f"🔔 TSL ACTIVATED\nLTP: ₹{ltp:.2f}\nTSL: ₹{self.tsl_price:.2f} (25pt profit locked)")

                    # Cancel target, move SL to TSL
                    if self.target_id:
                        self.orders.cancel_order(self.target_id)
                        self.target_id = None
                    self._modify_sl(self.tsl_price)
                    self.sl_price = self.tsl_price

                # Backup: manual SL check
                elif ltp <= self.sl_price:
                    self._exit_trade("SL", ltp)

            else:
                # TSL trailing — every 10pt new high
                if ltp >= self.tsl_last_high + RULES["tsl_trail_every_pts"]:
                    new_tsl = round(ltp - RULES["tsl_trail_gap"], 2)
                    print(clr(f"\n\n  📈 NEW HIGH ₹{ltp:.2f} → TSL: ₹{self.tsl_price:.2f} → ₹{new_tsl:.2f}", "green"))
                    alert(f"📈 TSL moved\n₹{self.tsl_price:.2f} → ₹{new_tsl:.2f}")
                    self.tsl_price     = new_tsl
                    self.tsl_last_high = ltp
                    self.sl_price      = new_tsl
                    self._modify_sl(new_tsl)

                # TSL hit check
                elif ltp <= self.tsl_price:
                    self._exit_trade("TSL", ltp)

    def _modify_sl(self, new_sl: float):
        """Modify SL order to new price."""
        if not self.sl_id:
            return
        ok = self.orders.modify_order(
            order_no=self.sl_id,
            symbol=self.symbol,
            qty=self.qty,
            order_type="SL-M",
            transaction="S",
            trigger=str(new_sl)
        )
        if ok:
            log.info(f"✅ SL modified to ₹{new_sl:.2f}")
        else:
            # Cancel and re-place
            log.warning(f"SL modify failed — re-placing at ₹{new_sl:.2f}")
            self.orders.cancel_order(self.sl_id)
            new_id = self.orders.place_order(
                symbol=self.symbol, qty=self.qty,
                order_type="SL-M", transaction="S",
                trigger=str(new_sl), tag="BOT_SL_TRAIL"
            )
            if new_id:
                self.sl_id = new_id
                log.info(f"✅ New SL placed at ₹{new_sl:.2f}")
            else:
                alert(f"⚠️ CRITICAL: SL re-place failed! Manual SL at ₹{new_sl:.2f}")

    def _check_order_completion(self):
        """Check if SL or Target got filled by exchange."""
        if not hasattr(self, "_last_check"):
            self._last_check = 0
        if time.time() - self._last_check < 5:
            return
        self._last_check = time.time()

        if self.sl_id:
            status = self.orders.get_order_status(self.sl_id)
            if status in ["COMPLETE", "TRADED"]:
                price = self.orders.get_fill_price(self.sl_id) or self.sl_price
                label = "TSL" if self.tsl_active else "SL"
                self._exit_trade(label, price, exchange_exit=True)
                return

        if self.target_id:
            status = self.orders.get_order_status(self.target_id)
            if status in ["COMPLETE", "TRADED"]:
                price = self.orders.get_fill_price(self.target_id) or self.target_price
                self._exit_trade("TARGET", price, exchange_exit=True)

    def _exit_trade(self, reason: str, exit_price: float, exchange_exit: bool = False):
        """Handle trade exit."""
        with self._lock:
            if not self.active:
                return
            self.active = False

        print()
        is_profit = reason in ["TARGET", "TSL"]
        divider("═", "green" if is_profit else "red")
        emoji = "✅" if is_profit else "🛑"
        print(clr(f"  {emoji} TRADE CLOSED — {reason}", "green" if is_profit else "red"))
        print(clr(f"  Entry:  ₹{self.entry_price:.2f}", "white"))
        print(clr(f"  Exit:   ₹{exit_price:.2f}", "white"))

        profit = exit_price - self.entry_price
        pnl_rs = profit * self.qty
        sign   = "+" if profit >= 0 else ""
        color  = "green" if profit >= 0 else "red"
        print(clr(f"  P&L:    {sign}{profit:.2f} pts  |  {sign}₹{pnl_rs:.2f}", color))
        divider("═", "green" if is_profit else "red")

        alert(f"{emoji} TRADE CLOSED — {reason}\nEntry: ₹{self.entry_price:.2f}\nExit: ₹{exit_price:.2f}\nP&L: {sign}{profit:.2f}pts | {sign}₹{pnl_rs:.2f}")

        # Cancel opposite leg
        if not exchange_exit:
            self._place_market_exit()
        if reason in ["SL", "TSL"] and self.target_id:
            self.orders.cancel_order(self.target_id)
        elif reason == "TARGET" and self.sl_id:
            self.orders.cancel_order(self.sl_id)

        # Kill Switch
        if CONFIG.get("AUTO_KILL_SWITCH"):
            print(clr("\n  🔴 Triggering Kill Switch...", "red"))
            from kill_switch import KillSwitch
            KillSwitch().trigger_web_killswitch()

        print(clr("\n  ✅ Bot shut down. Good trading!", "green"))

    def _place_market_exit(self):
        """Emergency market exit."""
        oid = self.orders.place_order(
            symbol=self.symbol, qty=self.qty,
            order_type="MKT", transaction="S",
            tag="BOT_EXIT"
        )
        if not oid:
            print(clr(f"  ⚠️ EXIT FAILED! Close {self.symbol} manually on app!", "red"))
            alert(f"⚠️ MANUAL EXIT NEEDED!\n{self.symbol}")


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────
def main():
    import os
    os.system("cls")
    print(clr("""
╔════════════════════════════════════════════════════════════╗
║        KOTAK NEO — NIFTY OPTIONS AUTO BOT                ║
║  You choose direction. Bot finds strike & manages trade. ║
╚════════════════════════════════════════════════════════════╝
    """, "cyan"))
    print(clr(f"  📅 {datetime.datetime.now().strftime('%d %b %Y  %I:%M %p')}\n", "yellow"))

    # Login
    session = KotakSession().login()

    # Choose direction
    print(clr("  What is your trade direction?\n", "yellow"))
    print(clr("    [1]  PUT  (PE) — Nifty going DOWN", "white"))
    print(clr("    [2]  CALL (CE) — Nifty going UP", "white"))
    print(clr("    [3]  Exit\n", "white"))

    while True:
        choice = input(clr("  → Your choice: ", "cyan")).strip()
        if choice == "1":
            option_type = "PE"
            break
        elif choice == "2":
            option_type = "CE"
            break
        elif choice == "3":
            sys.exit(0)
        else:
            print(clr("  Enter 1, 2 or 3", "red"))

    # Get expiry
    expiry = get_nearest_thursday()
    print(clr(f"\n  Expiry: {expiry.strftime('%d %b %Y')} (nearest Thursday)\n", "yellow"))

    # Load scrip master (instrument tokens)
    divider()
    scrip = ScripMaster(session)
    if not scrip.load():
        print(clr("\n  ❌ Could not load scrip master. Check connection.", "red"))
        sys.exit(1)

    # Scan strikes
    scanner = StrikeScanner(session, scrip)
    result  = scanner.scan(option_type, expiry)

    if not result:
        print(clr("\n  ❌ No suitable strike found. Try again later.", "red"))
        sys.exit(0)

    # Confirm
    print()
    divider("═", "green")
    print(clr("  📋 TRADE READY TO PLACE", "bold"))
    print(clr(f"  Symbol:   {result['symbol']}", "white"))
    print(clr(f"  LTP now:  ₹{result['ltp']:.2f}  (band ₹{result['band'][0]}–₹{result['band'][1]})", "white"))
    print(clr(f"  Order:    MARKET", "white"))
    print(clr(f"  Qty:      {NIFTY['lot_size']} (1 lot)", "white"))
    print(clr(f"  SL:       Entry - 10 pts", "red"))
    print(clr(f"  Target:   Entry + 30 pts", "green"))
    print(clr(f"  TSL:      Entry+25pts when +30pts hit | Then trails 10pts every new 10pt high", "cyan"))
    divider("═", "green")

    confirm = input(clr("  Confirm and place trade? (yes/no): ", "cyan")).strip().lower()
    if confirm not in ["yes", "y"]:
        print(clr("\n  ❌ Trade cancelled.", "red"))
        sys.exit(0)

    # Execute
    manager = TradeManager(session, result)
    manager.scanner = StrikeScanner(session, scrip)
    manager.enter()

    print(clr("\n  ⏳ Bot monitoring your trade...", "cyan"))
    print(clr("  Press Ctrl+C to stop manually.\n", "yellow"))

    try:
        while manager.active:
            time.sleep(1)
    except KeyboardInterrupt:
        print(clr("\n\n  🛑 Bot stopped manually.", "yellow"))
        if manager.active:
            print(clr("  ⚠️  Open position! Close manually on Kotak Neo app.", "red"))
            alert(f"⚠️ Bot stopped manually!\nClose {result['symbol']} on app!")


if __name__ == "__main__":
    main()
