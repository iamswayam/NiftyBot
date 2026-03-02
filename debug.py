"""
Debug — Parse expiry from pTrdSymbol name, find nearest, fetch LTP
"""
import requests, pyotp, os, datetime, re, calendar
from dotenv import load_dotenv
load_dotenv()

CONSUMER_KEY  = os.getenv("CONSUMER_KEY")
MOBILE_NUMBER = os.getenv("MOBILE_NUMBER")
UCC           = os.getenv("UCC")
MPIN          = os.getenv("MPIN")
TOTP_SECRET   = os.getenv("TOTP_SECRET")
NEO_FIN_KEY   = "neotradeapi"

print("🔐 Logging in...")
totp = pyotp.TOTP(TOTP_SECRET).now()
r1 = requests.post("https://mis.kotaksecurities.com/login/1.0/tradeApiLogin",
    headers={"Authorization": CONSUMER_KEY, "neo-fin-key": NEO_FIN_KEY, "Content-Type": "application/json"},
    json={"mobileNumber": MOBILE_NUMBER, "ucc": UCC, "totp": totp})
d1 = r1.json()["data"]
r2 = requests.post("https://mis.kotaksecurities.com/login/1.0/tradeApiValidate",
    headers={"Authorization": CONSUMER_KEY, "neo-fin-key": NEO_FIN_KEY,
             "sid": d1["sid"], "Auth": d1["token"], "Content-Type": "application/json"},
    json={"mpin": MPIN})
d2 = r2.json()["data"]
base_url = d2["baseUrl"]
print(f"✅ Logged in")

quote_headers = {"Authorization": CONSUMER_KEY, "Content-Type": "application/json"}

r          = requests.get(f"{base_url}/script-details/1.0/masterscrip/file-paths", headers=quote_headers)
paths      = r.json().get("data", {}).get("filesPaths", [])
nse_fo_url = next((p for p in paths if "nse_fo" in p), None)
csv        = requests.get(nse_fo_url).text.strip().split("\n")
hdrs       = csv[0].split(",")
idx_token  = hdrs.index("pSymbol")
idx_trd    = hdrs.index("pTrdSymbol")
idx_name   = hdrs.index("pSymbolName")

MONTHS = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
          "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}

def parse_expiry(sym):
    s = sym.replace("NIFTY", "")
    # Monthly: YY + MMM + strike + CE/PE
    m = re.match(r'^(\d{2})([A-Z]{3})(\d+)(CE|PE)$', s)
    if m:
        yy, mon, _, _ = m.groups()
        year = 2000 + int(yy)
        month = MONTHS.get(mon)
        if not month: return None
        last_day = calendar.monthrange(year, month)[1]
        for d in range(last_day, 0, -1):
            if datetime.date(year, month, d).weekday() == 1:
                return datetime.date(year, month, d)
    # Weekly: YY + M(1digit) + D(1-2digits) + strike + CE/PE
    w = re.match(r'^(\d{2})(\d)(\d{1,2})(\d+)(CE|PE)$', s)
    if w:
        yy, mo, day, _, _ = w.groups()
        try: return datetime.date(2000+int(yy), int(mo), int(day))
        except: return None
    return None

today      = datetime.date.today()
expiry_map = {}

for line in csv[1:]:
    cols = line.split(",")
    if len(cols) <= max(idx_token, idx_trd, idx_name): continue
    name    = cols[idx_name].strip()
    trd_sym = cols[idx_trd].strip()
    token   = cols[idx_token].strip()
    if name.upper() != "NIFTY": continue
    if not (trd_sym.endswith("CE") or trd_sym.endswith("PE")): continue
    exp = parse_expiry(trd_sym)
    if exp and exp >= today:
        if exp not in expiry_map:
            expiry_map[exp] = []
        expiry_map[exp].append((trd_sym, token))

upcoming = sorted(expiry_map.keys())
print(f"\n── Upcoming Nifty expiries ──")
for e in upcoming[:10]:
    print(f"  {e}  ({e.strftime('%A')})  — {len(expiry_map[e])} strikes")

if upcoming:
    nearest = upcoming[0]
    print(f"\n✅ Nearest: {nearest} ({nearest.strftime('%A')})")
    print(f"\n── Sample strikes for {nearest} ──")
    for sym, tok in sorted(expiry_map[nearest])[:5]:
        print(f"  {sym:<35} token: {tok}")

    sym, tok = expiry_map[nearest][0]
    print(f"\n── LTP test for {sym} token={tok} ──")
    r = requests.get(f"{base_url}/script-details/1.0/quotes/neosymbol/nse_fo|{tok}/ltp",
                     headers=quote_headers, timeout=10)
    print(f"Response: {r.text[:200]}")
