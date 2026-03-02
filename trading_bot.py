"""
╔══════════════════════════════════════════════════════════════════════╗
║       KOTAK NEO — NIFTY OPTIONS AUTO BOT                           ║
║  You choose CE or PE. Bot finds best strike by LTP range.          ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import sys, json, time, logging, threading, datetime, re, calendar
import requests, pyotp
from dotenv import load_dotenv
import os

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

_required = ["CONSUMER_KEY", "MOBILE_NUMBER", "UCC", "MPIN", "TOTP_SECRET"]
_missing  = [k for k in _required if not CONFIG.get(k)]
if _missing:
    print(f"\n  ❌ Missing in .env: {', '.join(_missing)}\n")
    sys.exit(1)

# ── Trade rules ───────────────────────────────────────────────────────
RULES = {
    "lot_size":            75,
    "exchange":            "nse_fo",
    "initial_sl_pts":      10,
    "initial_target_pts":  30,
    "tsl_activate_pts":    30,
    "tsl_activate_gap":    25,   # TSL = Entry + 25pts on activation
    "tsl_trail_every_pts": 10,
    "tsl_trail_gap":       10,
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

LOGIN_URL_1 = "https://mis.kotaksecurities.com/login/1.0/tradeApiLogin"
LOGIN_URL_2 = "https://mis.kotaksecurities.com/login/1.0/tradeApiValidate"
NEO_FIN_KEY = "neotradeapi"
MONTHS      = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
               "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"nifty_bot_{datetime.date.today()}.log", encoding="utf-8")
    ]
)
log = logging.getLogger("NiftyBot")

# ── Helpers ───────────────────────────────────────────────────────────
def clr(text, color="white"):
    codes = {"red":"\033[91m","green":"\033[92m","yellow":"\033[93m",
             "cyan":"\033[96m","white":"\033[97m","bold":"\033[1m","reset":"\033[0m"}
    return f"{codes.get(color,'')}{text}{codes['reset']}"

def divider(char="─", color="blue"):
    print(clr(char * 62, color))

def strip_emoji(text):
    return text.encode("ascii", "ignore").decode("ascii").strip()

def clean_rejection(reason):
    """Convert raw RMS rejection into human readable message."""
    if not reason:
        return "Unknown reason"
    r = reason
    # Insufficient funds
    if "Margin Exceeds" in r or "margin required" in r.lower():
        import re
        amt = re.search(r"required[:\s]+([\d.]+)", r)
        avl = re.search(r"Available[:\s]+([\d.]+)", r)
        needed = amt.group(1) if amt else "?"
        have   = avl.group(1) if avl else "0"
        return f"Insufficient funds — Have: ₹{have}  |  Need: ₹{needed} more"
    # Generic cleanup
    r = r.replace("RMS:", "").replace("across exchange across segment across product", "").strip(" ,")
    return r

def alert(msg):
    log.info(strip_emoji(msg))
    token = CONFIG.get("TELEGRAM_TOKEN")
    chat  = CONFIG.get("TELEGRAM_CHAT")
    if token and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                         data={"chat_id": chat, "text": f"NiftyBot\n{msg}"}, timeout=5)
        except: pass

def parse_expiry_from_symbol(sym):
    """Parse expiry date from trading symbol name."""
    s = sym.replace("NIFTY", "")
    # Monthly: YY + MMM + strike + CE/PE  e.g. NIFTY26MAR24800PE
    m = re.match(r'^(\d{2})([A-Z]{3})(\d+)(CE|PE)$', s)
    if m:
        yy, mon, _, _ = m.groups()
        year  = 2000 + int(yy)
        month = MONTHS.get(mon)
        if not month: return None
        last_day = calendar.monthrange(year, month)[1]
        for d in range(last_day, 0, -1):
            if datetime.date(year, month, d).weekday() == 1:
                return datetime.date(year, month, d)
    # Weekly: YY + M + D + strike + CE/PE  e.g. NIFTY2633XXXXXPE
    w = re.match(r'^(\d{2})(\d)(\d{1,2})(\d+)(CE|PE)$', s)
    if w:
        yy, mo, day, _, _ = w.groups()
        try: return datetime.date(2000+int(yy), int(mo), int(day))
        except: return None
    return None


# ── Kotak Session ─────────────────────────────────────────────────────
class KotakSession:
    def __init__(self):
        self.access_token = CONFIG["CONSUMER_KEY"]
        self.auth_token   = None
        self.sid          = None
        self.base_url     = None

    def login(self):
        print(clr("🔐 Logging into Kotak Neo...", "yellow"))
        totp = pyotp.TOTP(CONFIG["TOTP_SECRET"]).now()

        try:
            r1 = requests.post(LOGIN_URL_1,
                headers={"Authorization": self.access_token, "neo-fin-key": NEO_FIN_KEY,
                         "Content-Type": "application/json"},
                json={"mobileNumber": CONFIG["MOBILE_NUMBER"], "ucc": CONFIG["UCC"], "totp": totp},
                timeout=15)
            d1 = r1.json()["data"]
        except Exception as e:
            print(clr(f"❌ Login Step 1 failed: {e}", "red")); sys.exit(1)

        try:
            r2 = requests.post(LOGIN_URL_2,
                headers={"Authorization": self.access_token, "neo-fin-key": NEO_FIN_KEY,
                         "sid": d1["sid"], "Auth": d1["token"], "Content-Type": "application/json"},
                json={"mpin": CONFIG["MPIN"]}, timeout=15)
            d2 = r2.json()["data"]
        except Exception as e:
            print(clr(f"❌ Login Step 2 failed: {e}", "red")); sys.exit(1)

        if not d2.get("token"):
            print(clr(f"❌ Login failed: {d2}", "red")); sys.exit(1)

        self.auth_token = d2["token"]
        self.sid        = d2["sid"]
        self.base_url   = d2["baseUrl"]
        print(clr(f"  ✅ Login successful!\n", "green"))
        return self

    def quote_headers(self):
        return {"Authorization": self.access_token, "Content-Type": "application/json"}

    def trade_headers(self):
        return {"Auth": self.auth_token, "Sid": self.sid, "neo-fin-key": NEO_FIN_KEY,
                "accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}


# ── Scrip Master ──────────────────────────────────────────────────────
class ScripMaster:
    def __init__(self, session: KotakSession):
        self.session    = session
        self.token_map  = {}   # trd_symbol → token
        self.expiry_map = {}   # expiry_date → [(trd_sym, token, strike, opttype)]

    def load(self):
        # Use local cache if downloaded today already
        os.makedirs("temp", exist_ok=True)
        cache_file = f"temp/scrip_cache_{datetime.date.today()}.csv"
        try:
            if os.path.exists(cache_file):
                print(clr("  📂 Loading scrip master from cache...", "yellow"))
                with open(cache_file, "r", encoding="utf-8") as f:
                    raw = f.read()
                csv = raw.strip().split("\n")
            else:
                print(clr("  📥 Downloading scrip master...", "yellow"))
                r     = requests.get(f"{self.session.base_url}/script-details/1.0/masterscrip/file-paths",
                                     headers=self.session.quote_headers(), timeout=15)
                paths = r.json().get("data", {}).get("filesPaths", [])
                url   = next((p for p in paths if "nse_fo" in p), None)
                if not url:
                    print(clr("  ❌ nse_fo scrip master not found", "red")); return False
                raw = requests.get(url, timeout=30).text
                with open(cache_file, "w", encoding="utf-8") as f:
                    f.write(raw)
                csv = raw.strip().split("\n")
            hdrs = csv[0].split(",")
            idx_token   = hdrs.index("pSymbol")
            idx_trd     = hdrs.index("pTrdSymbol")
            idx_name    = hdrs.index("pSymbolName")
            idx_lotsize = hdrs.index("lLotSize") if "lLotSize" in hdrs else None

            today = datetime.date.today()
            count = 0
            for line in csv[1:]:
                cols = line.split(",")
                if len(cols) <= max(idx_token, idx_trd, idx_name): continue
                name    = cols[idx_name].strip()
                trd_sym = cols[idx_trd].strip()
                token   = cols[idx_token].strip()
                if name.upper() != "NIFTY": continue
                if not (trd_sym.endswith("CE") or trd_sym.endswith("PE")): continue
                if not token: continue

                self.token_map[trd_sym] = token

                # Grab lot size from scrip master
                if idx_lotsize and not hasattr(self, "lot_size"):
                    try:
                        ls = int(cols[idx_lotsize].strip())
                        if ls > 0:
                            self.lot_size = ls
                            log.info(f"Lot size from scrip master: {ls}")
                    except: pass

                exp = parse_expiry_from_symbol(trd_sym)
                if exp and exp >= today:
                    if exp not in self.expiry_map:
                        self.expiry_map[exp] = []
                    opt_type = "CE" if trd_sym.endswith("CE") else "PE"
                    self.expiry_map[exp].append((trd_sym, token, opt_type))
                    count += 1

            lot = getattr(self, "lot_size", "?")
            print(clr(f"  ✅ Loaded {count} upcoming Nifty contracts | Lot size: {lot}", "green"))

            # Show upcoming expiries
            upcoming = sorted(self.expiry_map.keys())[:5]
            print(clr(f"  📅 Upcoming expiries: {', '.join(str(e) for e in upcoming)}", "cyan"))
            return True

        except Exception as e:
            log.error(f"Scrip master error: {e}")
            print(clr(f"  ❌ Scrip master failed: {e}", "red"))
            return False

    def get_nearest_expiry(self):
        upcoming = sorted(self.expiry_map.keys())
        return upcoming[0] if upcoming else None

    def get_strikes_for_expiry(self, expiry, opt_type):
        """Return all (trd_sym, token) for given expiry and option type."""
        all_contracts = self.expiry_map.get(expiry, [])
        return [(sym, tok) for sym, tok, ot in all_contracts if ot == opt_type]


# ── Strike Scanner ────────────────────────────────────────────────────
class StrikeScanner:
    def __init__(self, session: KotakSession, scrip: ScripMaster):
        self.session = session
        self.scrip   = scrip

    def get_ltp(self, token):
        try:
            url  = f"{self.session.base_url}/script-details/1.0/quotes/neosymbol/nse_fo|{token}/ltp"
            resp = requests.get(url, headers=self.session.quote_headers(), timeout=8)
            data = resp.json()
            if isinstance(data, list) and data:
                ltp = data[0].get("ltp")
                if ltp and float(ltp) > 0:
                    return float(ltp)
        except Exception as e:
            log.debug(f"LTP error token={token}: {e}")
        return None

    def scan(self, opt_type):
        expiry  = self.scrip.get_nearest_expiry()
        if not expiry:
            print(clr("  ❌ No expiry found in scrip master", "red"))
            return None

        strikes = self.scrip.get_strikes_for_expiry(expiry, opt_type)
        if not strikes:
            print(clr(f"  ❌ No {opt_type} strikes found for {expiry}", "red"))
            return None

        print(clr(f"\n  📅 Expiry: {expiry}  ({expiry.strftime('%A')})  |  {len(strikes)} {opt_type} strikes", "yellow"))
        print(clr(f"  🔍 Fetching all LTPs in bulk...", "cyan"))

        # Fetch ALL LTPs in one API call — comma separated tokens
        # Max 200 per call (API limit), so batch if needed
        ltp_map    = {}   # trd_sym → (token, ltp)
        token_to_sym = {tok: sym for sym, tok in strikes}
        tokens     = [tok for _, tok in strikes]
        batch_size = 200

        for i in range(0, len(tokens), batch_size):
            batch   = tokens[i:i+batch_size]
            query   = ",".join(f"nse_fo|{tok}" for tok in batch)
            try:
                url  = f"{self.session.base_url}/script-details/1.0/quotes/neosymbol/{query}/ltp"
                resp = requests.get(url, headers=self.session.quote_headers(), timeout=15)
                data = resp.json()
                if isinstance(data, list):
                    for item in data:
                        tok = str(item.get("exchange_token",""))
                        ltp = item.get("ltp")
                        if tok and ltp and float(ltp) > 0:
                            sym = token_to_sym.get(tok)
                            if sym:
                                ltp_map[sym] = (tok, float(ltp))
            except Exception as e:
                log.error(f"Bulk LTP fetch error: {e}")

        print(clr(f"  ✅ Got LTPs for {len(ltp_map)} strikes", "green"))

        if not ltp_map:
            print(clr("  ❌ No LTPs fetched. Market may be closed.", "red"))
            return None

        print(clr(f"  ✅ Got LTPs for {len(ltp_map)} strikes\n", "green"))

        # Scan priority bands
        for band_low, band_high in RULES["ltp_bands"]:
            matches = {sym: (tok, ltp) for sym, (tok, ltp) in ltp_map.items()
                       if band_low <= ltp <= band_high}

            if matches:
                print(clr(f"  ✅ Found in band ₹{band_low}–₹{band_high}:", "green"))
                for sym, (tok, ltp) in sorted(matches.items(), key=lambda x: x[1][1], reverse=True):
                    print(clr(f"     {sym:<35} ₹{ltp:.2f}", "white"))

                # Pick HIGHER LTP
                best_sym = max(matches, key=lambda s: matches[s][1])
                best_tok, best_ltp = matches[best_sym]

                if len(matches) > 1:
                    print(clr(f"  ↑ Multiple found → Selected HIGHER LTP: {best_sym} @ ₹{best_ltp:.2f}", "cyan"))
                else:
                    print(clr(f"  → Selected: {best_sym} @ ₹{best_ltp:.2f}", "cyan"))

                return {
                    "symbol":   best_sym,
                    "token":    best_tok,
                    "ltp":      best_ltp,
                    "band":     (band_low, band_high),
                    "expiry":   expiry,
                    "opt_type": opt_type,
                }
            else:
                print(clr(f"  ✗ ₹{band_low}–₹{band_high} → nothing", "white"))

        print(clr("\n  ❌ No strike found in any LTP range.", "red"))
        return None


# ── Order Manager ─────────────────────────────────────────────────────
class OrderManager:
    def __init__(self, session: KotakSession):
        self.session = session

    def _post(self, endpoint, jdata):
        url  = f"{self.session.base_url}{endpoint}"
        resp = requests.post(url, headers=self.session.trade_headers(),
                             data={"jData": json.dumps(jdata)}, timeout=15)
        return resp.json()

    def _get(self, endpoint):
        url  = f"{self.session.base_url}{endpoint}"
        resp = requests.get(url, headers=self.session.trade_headers(), timeout=15)
        return resp.json()

    def place_order(self, symbol, qty, order_type, transaction, price="0", trigger="0", tag=""):
        jdata = {"am":"NO","dq":"0","es":RULES["exchange"],"mp":"0","pc":"MIS","pf":"N",
                 "pr":price,"pt":order_type,"qt":str(qty),"rt":"DAY","tp":trigger,
                 "ts":symbol,"tt":transaction,"tg":tag}
        try:
            resp = self._post("/quick/order/rule/ms/place", jdata)
            log.info(f"Place: stat={resp.get('stat')} ord={resp.get('nOrdNo','-')}")
            if resp.get("stat") == "Ok":
                return resp.get("nOrdNo")
            else:
                err = resp.get("emsg") or resp.get("errMsg") or resp.get("message") or resp.get("msg") or "Unknown error"
                print(clr(f"  ❌ Order rejected: {err}", "red"))
                return None
        except Exception as e:
            log.error(f"Place order: {e}"); return None

    def modify_order(self, order_no, symbol, qty, order_type, transaction, price="0", trigger="0"):
        jdata = {"no":order_no,"am":"NO","dq":"0","es":RULES["exchange"],"mp":"0","pc":"MIS",
                 "pr":price,"pt":order_type,"qt":str(qty),"rt":"DAY","tp":trigger,
                 "ts":symbol,"tt":transaction}
        try:
            resp = self._post("/quick/order/vr/modify", jdata)
            log.info(f"Modify: stat={resp.get('stat')}")
            return resp.get("stat") == "Ok"
        except Exception as e:
            log.error(f"Modify: {e}"); return False

    def cancel_order(self, order_no):
        try:
            resp = self._post("/quick/order/cancel", {"on": order_no, "am": "NO"})
            return resp.get("stat") == "Ok"
        except: return False

    def get_orders(self):
        try:
            resp = self._get("/quick/user/orders")
            return resp.get("data", []) if resp.get("stat") == "Ok" else []
        except: return []

    def get_order_status(self, order_no):
        for o in self.get_orders():
            if str(o.get("nOrdNo")) == str(order_no):
                return o.get("ordSt", "").upper()
        return None

    def get_fill_price(self, order_no):
        for o in self.get_orders():
            if str(o.get("nOrdNo")) == str(order_no):
                avg = o.get("avgPrc", "0")
                return float(avg) if avg and float(avg) > 0 else None
        return None

    def check_margin(self, ltp):
        jdata = {"brkName":"KOTAK","brnchId":"ONLINE","exSeg":RULES["exchange"],
                 "prc":str(ltp),"prcTp":"MKT","prod":"MIS",
                 "qty":str(RULES["lot_size"]),"tok":"0","trnsTp":"B"}
        try:
            url  = f"{self.session.base_url}/quick/user/check-margin"
            resp = requests.post(url, headers=self.session.trade_headers(),
                                 data={"jData": json.dumps(jdata)}, timeout=15).json()
            log.info(f"Margin: avl={resp.get('avlCash',0)} req={resp.get('reqdMrgn',0)}")
            avl   = float(resp.get("avlCash",0) or resp.get("avlMrgn",0) or 0)
            req   = float(resp.get("reqdMrgn",0) or resp.get("ordMrgn",0) or 0)
            insuf = float(resp.get("insufFund",0) or 0)
            valid = resp.get("rmsVldtd","NOT_OK").upper()
            return {"ok": valid == "OK" or insuf <= 0, "available": avl,
                    "required": req, "shortfall": max(insuf, 0)}
        except Exception as e:
            log.error(f"Margin check: {e}")
            return {"ok": True, "available": 0, "required": 0, "shortfall": 0}


# ── Trade Manager ─────────────────────────────────────────────────────
class TradeManager:
    def __init__(self, session, scanner, scrip, result):
        self.session  = session
        self.scanner  = scanner
        self.orders   = OrderManager(session)
        self.trade    = result
        self.symbol   = result["symbol"]
        self.token    = result["token"]
        self.qty      = getattr(scrip, "lot_size", RULES["lot_size"])
        self.active   = False
        self._lock    = threading.Lock()
        self.entry_id = self.sl_id = self.target_id = None
        self.entry_price = self.sl_price = self.target_price = None
        self.tsl_active = False
        self.tsl_price  = None
        self.tsl_last_high = None

    def enter(self):
        ltp    = self.trade["ltp"]
        margin = self.orders.check_margin(ltp)

        print(clr(f"\n  💰 Margin check:", "yellow"))
        print(clr(f"     Available: ₹{margin['available']:,.2f}  |  Required: ₹{margin['required']:,.2f}", "white"))

        if not margin["ok"]:
            print(clr(f"\n  ❌ INSUFFICIENT FUNDS! Shortfall: ₹{margin['shortfall']:,.2f}", "red"))
            alert(f"❌ INSUFFICIENT FUNDS\nShortfall: ₹{margin['shortfall']:,.2f}")
            return

        print(clr(f"  ✅ Funds OK\n", "green"))
        print(clr(f"  📥 Placing MARKET BUY: {self.symbol}  Qty: {self.qty}", "yellow"))

        oid = self.orders.place_order(self.symbol, self.qty, "MKT", "B", tag="BOT_ENTRY")
        if oid:
            self.entry_id = oid
            self.active   = True   # keep main loop alive while waiting for fill
            print(clr(f"  ✅ Order placed! ID: {oid}", "green"))
            print(clr(f"  ⏳ Waiting for fill...", "cyan"))
            threading.Thread(target=self._wait_fill, daemon=True).start()
        else:
            print(clr("  ❌ Order failed!", "red"))
            alert("Entry order FAILED!")

    def _wait_fill(self):
        for _ in range(60):   # wait max 60 seconds
            status = self.orders.get_order_status(self.entry_id)
            if status in ["COMPLETE", "TRADED"]:
                price = self.orders.get_fill_price(self.entry_id) or self.trade["ltp"]
                self._on_fill(price)
                return
            elif status in ["CANCELLED", "REJECTED"]:
                reason = ""
                for o in self.orders.get_orders():
                    if str(o.get("nOrdNo")) == str(self.entry_id):
                        raw    = (o.get("rejRsn") or o.get("rjRsn") or
                                  o.get("rejectionReason") or o.get("remarks") or
                                  o.get("txt") or "")
                        reason = clean_rejection(raw)
                        break
                print()
                print(clr(f"  ❌ ORDER REJECTED", "red"))
                print(clr(f"  Reason: {reason}", "red"))
                print(clr(f"  Order ID: {self.entry_id}", "white"))
                alert(f"Order REJECTED: {reason}")
                self.active = False
                return
            time.sleep(1)
        print(clr("\n  ⏰ Order not filled in 60s — cancelling", "red"))
        self.orders.cancel_order(self.entry_id)
        self.active = False

    def _on_fill(self, fill_price):
        self.entry_price   = fill_price
        self.sl_price      = round(fill_price - RULES["initial_sl_pts"], 2)
        self.target_price  = round(fill_price + RULES["initial_target_pts"], 2)
        self.tsl_last_high = fill_price

        print(clr(f"\n  ✅ FILLED at ₹{fill_price:.2f}", "green"))
        print(clr(f"  🛡️  SL     = ₹{self.sl_price:.2f}", "red"))
        print(clr(f"  🎯  Target = ₹{self.target_price:.2f}", "green"))
        print(clr(f"  📈  TSL activates at ₹{fill_price + RULES['tsl_activate_pts']:.2f}", "cyan"))
        divider()
        alert(f"✅ FILLED ₹{fill_price:.2f}\nSL:₹{self.sl_price:.2f} Target:₹{self.target_price:.2f}")

        self.sl_id = self.orders.place_order(self.symbol, self.qty, "SL-M", "S",
                                              trigger=str(self.sl_price), tag="BOT_SL")
        if self.sl_id:
            print(clr(f"  🛡️  SL order placed @ ₹{self.sl_price:.2f}", "cyan"))
        else:
            print(clr(f"  ❌ SL FAILED! Set manual SL at ₹{self.sl_price:.2f}", "red"))
            alert(f"❌ SL FAILED! Manual SL at ₹{self.sl_price:.2f}")

        self.target_id = self.orders.place_order(self.symbol, self.qty, "L", "S",
                                                   price=str(self.target_price), tag="BOT_TARGET")
        if self.target_id:
            print(clr(f"  🎯  Target order placed @ ₹{self.target_price:.2f}", "cyan"))
        else:
            print(clr(f"  ❌ TARGET FAILED! Manual target at ₹{self.target_price:.2f}", "red"))

        self.active = True
        threading.Thread(target=self._monitor, daemon=True).start()

    def _monitor(self):
        last_display = None
        while self.active:
            try:
                ltp = self.scanner.get_ltp(self.token)
                if ltp:
                    self._process_price(ltp)
                    if ltp != last_display:
                        profit  = ltp - self.entry_price
                        sign    = "+" if profit >= 0 else ""
                        color   = "green" if profit >= 0 else "red"
                        tsl_str = f"TSL=₹{self.tsl_price:.2f}" if self.tsl_active else "TSL=inactive"
                        sys.stdout.write(clr(
                            f"\r  LTP:₹{ltp:.2f} | P&L:{sign}{profit:.2f}pts"
                            f" | SL:₹{self.sl_price:.2f} | {tsl_str}"
                            f" | Tgt:₹{self.target_price:.2f}   ", color))
                        sys.stdout.flush()
                        last_display = ltp
                self._check_fills()
            except Exception as e:
                log.debug(f"Monitor: {e}")
            time.sleep(2)

    def _process_price(self, ltp):
        with self._lock:
            if not self.active or not self.entry_price: return
            profit = ltp - self.entry_price

            if not self.tsl_active:
                if profit >= RULES["tsl_activate_pts"]:
                    self.tsl_active    = True
                    self.tsl_price     = round(self.entry_price + RULES["tsl_activate_gap"], 2)
                    self.tsl_last_high = ltp
                    self.sl_price      = self.tsl_price
                    print(clr(f"\n\n  🔔 TSL ACTIVATED! LTP=₹{ltp:.2f} → TSL=₹{self.tsl_price:.2f} (Entry+25 locked)", "cyan"))
                    alert(f"🔔 TSL ACTIVATED\nTSL:₹{self.tsl_price:.2f}")
                    if self.target_id:
                        self.orders.cancel_order(self.target_id)
                        self.target_id = None
                    self._modify_sl(self.tsl_price)
                elif ltp <= self.sl_price:
                    self._exit("SL", ltp)
            else:
                if ltp >= self.tsl_last_high + RULES["tsl_trail_every_pts"]:
                    new_tsl = round(ltp - RULES["tsl_trail_gap"], 2)
                    print(clr(f"\n\n  📈 NEW HIGH ₹{ltp:.2f} → TSL: ₹{self.tsl_price:.2f}→₹{new_tsl:.2f}", "green"))
                    alert(f"📈 TSL moved ₹{self.tsl_price:.2f}→₹{new_tsl:.2f}")
                    self.tsl_price     = new_tsl
                    self.tsl_last_high = ltp
                    self.sl_price      = new_tsl
                    self._modify_sl(new_tsl)
                elif ltp <= self.tsl_price:
                    self._exit("TSL", ltp)

    def _modify_sl(self, new_sl):
        if not self.sl_id: return
        ok = self.orders.modify_order(self.sl_id, self.symbol, self.qty, "SL-M", "S", trigger=str(new_sl))
        if not ok:
            self.orders.cancel_order(self.sl_id)
            new_id = self.orders.place_order(self.symbol, self.qty, "SL-M", "S",
                                              trigger=str(new_sl), tag="BOT_SL")
            if new_id: self.sl_id = new_id
            else: alert(f"⚠️ SL re-place FAILED! Manual SL at ₹{new_sl:.2f}")

    def _check_fills(self):
        if not hasattr(self, "_last_check"): self._last_check = 0
        if time.time() - self._last_check < 5: return
        self._last_check = time.time()
        if self.sl_id:
            st = self.orders.get_order_status(self.sl_id)
            if st in ["COMPLETE","TRADED"]:
                p = self.orders.get_fill_price(self.sl_id) or self.sl_price
                self._exit("TSL" if self.tsl_active else "SL", p, exchange_exit=True)
                return
        if self.target_id:
            st = self.orders.get_order_status(self.target_id)
            if st in ["COMPLETE","TRADED"]:
                p = self.orders.get_fill_price(self.target_id) or self.target_price
                self._exit("TARGET", p, exchange_exit=True)

    def _exit(self, reason, exit_price, exchange_exit=False):
        with self._lock:
            if not self.active: return
            self.active = False

        profit = exit_price - self.entry_price
        pnl_rs = profit * self.qty
        sign   = "+" if profit >= 0 else ""
        is_win = reason in ["TARGET","TSL"]
        emoji  = "✅" if is_win else "🛑"

        print()
        divider("═", "green" if is_win else "red")
        print(clr(f"  {emoji} TRADE CLOSED — {reason}", "green" if is_win else "red"))
        print(clr(f"  Entry: ₹{self.entry_price:.2f}  Exit: ₹{exit_price:.2f}", "white"))
        print(clr(f"  P&L:   {sign}{profit:.2f} pts  |  {sign}₹{pnl_rs:.2f}", "green" if profit>=0 else "red"))
        divider("═", "green" if is_win else "red")
        alert(f"{emoji} {reason}\nEntry:₹{self.entry_price:.2f} Exit:₹{exit_price:.2f}\nP&L:{sign}{profit:.2f}pts|{sign}₹{pnl_rs:.2f}")

        if not exchange_exit:
            oid = self.orders.place_order(self.symbol, self.qty, "MKT", "S", tag="BOT_EXIT")
            if not oid:
                print(clr(f"  ⚠️ EXIT FAILED! Close {self.symbol} manually!", "red"))
                alert(f"⚠️ MANUAL EXIT NEEDED!\n{self.symbol}")

        if reason in ["SL","TSL"] and self.target_id:
            self.orders.cancel_order(self.target_id)
        elif reason == "TARGET" and self.sl_id:
            self.orders.cancel_order(self.sl_id)

        if CONFIG.get("AUTO_KILL_SWITCH"):
            print(clr("\n  🔴 Triggering Kill Switch...", "red"))
            try:
                from kill_switch import KillSwitch
                KillSwitch().trigger_web_killswitch()
            except Exception as e:
                log.error(f"Kill switch: {e}")

        print(clr("\n  ✅ Bot shut down. Good trading!", "green"))


# ── Main ──────────────────────────────────────────────────────────────
def main():
    os.system("cls" if os.name == "nt" else "clear")
    print(clr("""
╔════════════════════════════════════════════════════════════╗
║        KOTAK NEO — NIFTY OPTIONS AUTO BOT                ║
║  You choose direction. Bot finds strike & manages trade. ║
╚════════════════════════════════════════════════════════════╝""", "cyan"))
    print(clr(f"\n  📅 {datetime.datetime.now().strftime('%d %b %Y  %I:%M %p')}\n", "yellow"))

    session = KotakSession().login()

    print(clr("  What is your trade direction?\n", "yellow"))
    print(clr("    [1]  PUT  (PE) — Nifty going DOWN", "white"))
    print(clr("    [2]  CALL (CE) — Nifty going UP", "white"))
    print(clr("    [3]  Exit\n", "white"))

    while True:
        choice = input(clr("  → Your choice: ", "cyan")).strip()
        if choice == "1":   opt_type = "PE"; break
        elif choice == "2": opt_type = "CE"; break
        elif choice == "3": sys.exit(0)
        else: print(clr("  Enter 1, 2 or 3", "red"))

    divider()
    scrip = ScripMaster(session)
    if not scrip.load():
        print(clr("  ❌ Scrip master failed.", "red")); sys.exit(1)

    scanner = StrikeScanner(session, scrip)
    result  = scanner.scan(opt_type)

    if not result:
        print(clr("\n  ❌ No suitable strike found. Try again during market hours.", "red"))
        sys.exit(0)

    print()
    divider("═", "green")
    print(clr("  📋 TRADE READY", "bold"))
    print(clr(f"  Symbol:  {result['symbol']}", "white"))
    print(clr(f"  LTP:     ₹{result['ltp']:.2f}  (band ₹{result['band'][0]}–₹{result['band'][1]})", "white"))
    print(clr(f"  Qty:     {RULES['lot_size']} (1 lot)  |  Order: MARKET", "white"))
    print(clr(f"  SL:      Entry - 10 pts", "red"))
    print(clr(f"  Target:  Entry + 30 pts", "green"))
    print(clr(f"  TSL:     Entry+25pts when +30pts hit | trails 10pts every new 10pt high", "cyan"))
    divider("═", "green")

    while True:
        confirm = input(clr("  Place trade? (Y/N): ", "cyan")).strip().upper()
        if confirm == "Y": break
        elif confirm == "N":
            print(clr("\n  Cancelled.", "red")); sys.exit(0)
        else:
            print(clr("  Type Y or N", "red"))

    manager = TradeManager(session, scanner, scrip, result)
    manager.enter()

    print(clr("\n  ⏳ Monitoring trade... Press Ctrl+C to stop.\n", "cyan"))
    try:
        while manager.active:
            time.sleep(1)
    except KeyboardInterrupt:
        print(clr("\n\n  🛑 Stopped manually.", "yellow"))
        if manager.active:
            print(clr("  ⚠️ Open position! Close manually on Kotak Neo app.", "red"))
            alert(f"⚠️ Bot stopped!\nClose {result['symbol']} on app!")

if __name__ == "__main__":
    main()
