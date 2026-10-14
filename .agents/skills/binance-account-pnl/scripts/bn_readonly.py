"""Read-only Binance USD-M futures client. GET only - there is deliberately no POST/PUT/DELETE path.

Keys come from the environment variables BN_KEY / BN_SECRET, which the agent passes inline on the
command line by secret name (never exported, never written to disk, never printed).
"""
import hashlib, hmac, json, os, time, urllib.error, urllib.parse, urllib.request

BASES = {
    "testnet": "https://testnet.binancefuture.com",
    "demo": "https://demo-fapi.binance.com",
    "live": "https://fapi.binance.com",
}
# Endpoint allow-list: everything here is a read. Anything else raises.
ALLOWED = {
    "/fapi/v2/account", "/fapi/v3/account", "/fapi/v2/balance", "/fapi/v3/balance",
    "/fapi/v2/positionRisk", "/fapi/v3/positionRisk", "/fapi/v1/openOrders",
    "/fapi/v1/income", "/fapi/v1/userTrades", "/fapi/v1/klines", "/fapi/v1/premiumIndex",
    "/fapi/v1/ping", "/fapi/v1/time",
}


class ReadOnlyClient:
    def __init__(self, env="testnet"):
        self.base = BASES[env] if env in BASES else env
        self.key = os.environ.get("BN_KEY", "")
        self.secret = os.environ.get("BN_SECRET", "")
        if not self.key or not self.secret:
            raise SystemExit("BN_KEY / BN_SECRET not set (pass them inline by secret name)")

    def get(self, path, params=None, signed=True):
        if path not in ALLOWED:
            raise PermissionError(f"{path} is not on the read-only allow-list")
        p = dict(params or {})
        if signed:
            p["timestamp"] = int(time.time() * 1000)
            p["recvWindow"] = 10000
        q = urllib.parse.urlencode(p)
        if signed:
            q += "&signature=" + hmac.new(self.secret.encode(), q.encode(), hashlib.sha256).hexdigest()
        req = urllib.request.Request(f"{self.base}{path}?{q}", method="GET",
                                     headers={"X-MBX-APIKEY": self.key})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                body = e.read().decode()[:300]
                if e.code in (418, 429) or e.code >= 500:
                    time.sleep(5 * (attempt + 1))
                    continue
                # never echo the URL: it carries the signature
                raise SystemExit(f"HTTP {e.code} on {path}: {body}")
        raise SystemExit(f"gave up on {path} after retries")
