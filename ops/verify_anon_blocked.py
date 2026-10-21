"""Post-release check: is the production database closed to anonymous (public) writes?

Uses only the PUBLIC anon key from .streamlit/secrets.toml (never printed). Every probe is a read of
one row, a write that matches no row (id = -1), or a function call with an impossible id/length, so no
data can change whether the database is open or closed. (An 'insert' probe is deliberately NOT used.)
Usage:  python ops/verify_anon_blocked.py [path-to-secrets.toml]   (read-only intent; stdlib only)
Exit code 0 = anonymous access is blocked, 1 = something is still open."""
import json
import sys
import tomllib
import urllib.error
import urllib.request

path = sys.argv[1] if len(sys.argv) > 1 else ".streamlit/secrets.toml"
secrets = tomllib.load(open(path, "rb"))
base, key = secrets["SUPABASE_URL"].rstrip("/") + ("" if "127.0.0.1" in secrets["SUPABASE_URL"] else "/rest/v1"), secrets["SUPABASE_KEY"]


def call(method, url, body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"apikey": key, "Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json", "Prefer": "return=minimal"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


probes = [
    ("read led_inventory", "GET", "/led_inventory?select=id&limit=1", None),
    ("read santaf_inventory", "GET", "/santaf_inventory?select=id&limit=1", None),
    ("read leds_inventory", "GET", "/leds_inventory?select=id&limit=1", None),
    ("read order_lines", "GET", "/order_lines?select=id&limit=1", None),
    ("update santaf_inventory (matches no row)", "PATCH", "/santaf_inventory?id=eq.-1", {"quantity": 0}),
    ("update led_inventory (matches no row)", "PATCH", "/led_inventory?id=eq.-1", {"quantity": 0}),
    ("delete led_inventory (matches no row)", "DELETE", "/led_inventory?id=eq.-1", None),
    ("update santaf_movements (matches no row)", "PATCH", "/santaf_movements?id=eq.-1", {"quantity": 0}),
    ("call led_issue_stock", "POST", "/rpc/led_issue_stock", {"p_id": -1, "p_qty": 1}),
    ("call santaf_receive_stock", "POST", "/rpc/santaf_receive_stock", {"p_lengths": [-1], "p_qtys": [1], "p_date": "2026-01-01"}),
]
open_ = []
for name, method, path_, body in probes:
    status = call(method, base + path_, body)
    blocked = status in (401, 403, 404)
    print(f"{'BLOCKED' if blocked else 'OPEN   '}  {status}  {name}")
    if not blocked:
        open_.append(name)
print("\nRESULT:", "anonymous access is blocked" if not open_ else f"STILL OPEN: {len(open_)} probe(s)")
sys.exit(1 if open_ else 0)
