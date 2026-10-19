"""Integration tests against a LOCAL throwaway Postgres + PostgREST (see tests/sql/rest_setup.sql).
Skipped unless PGREST_URL and PGREST_JWT_SECRET are set. Never use against production."""
import base64
import concurrent.futures as cf
import hashlib
import hmac
import json
import os
import subprocess
import time

import httpx
import pytest

URL = os.environ.get("PGREST_URL")
SECRET = os.environ.get("PGREST_JWT_SECRET")
PG_CONTAINER = os.environ.get("PG_CONTAINER", "pgtest")
pytestmark = pytest.mark.skipif(not (URL and SECRET), reason="local PostgREST not configured")


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def token(role):
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps({"role": role, "exp": int(time.time()) + 3600}).encode())
    sig = hmac.new(SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(sig)}"


def client(role):
    return httpx.Client(base_url=URL, timeout=30,
                        headers={"Authorization": f"Bearer {token(role)}", "Prefer": "return=representation"})


def psql(sql):
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    out = subprocess.run(["docker", "exec", PG_CONTAINER, "psql", "-U", "postgres", "-At", "-v",
                          "ON_ERROR_STOP=1", "-c", sql], capture_output=True, text=True, encoding="utf-8", env=env)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


auth, anon = client("authenticated"), client("anon")


def new_line(length=2000, qty=4, category="SANTAF", order="T1", **extra):
    row = {"customer_order_number": order, "category": category, "length_mm": length,
           "quantity": qty, "item_spec": "x" if category == "SANTAF" else "LED", **extra}
    r = auth.post("/order_lines", json=row)
    assert r.status_code == 201, r.text
    return r.json()[0]


def receive(c, line_id, qty=4, loc="B-01"):
    return c.post("/rpc/receive_order_line",
                  json={"p_line_id": line_id, "p_qty": qty, "p_date": "2026-10-20", "p_location": loc})


def santaf_qty(length):
    return int(psql(f"select coalesce(quantity,0) from santaf_inventory where length={length}"))


# ---------------- access control ----------------
def test_anon_cannot_read_or_write_order_lines():
    assert anon.get("/order_lines").status_code in (401, 403)
    r = anon.post("/order_lines", json={"customer_order_number": "x", "category": "LED",
                                        "length_mm": 1, "quantity": 1})
    assert r.status_code in (401, 403)


def test_anon_cannot_call_receive_rpc():
    line = new_line(length=1500)
    assert receive(anon, line["id"]).status_code in (401, 403, 404)
    assert psql(f"select status from order_lines where id='{line['id']}'") == "ordered"


def test_nobody_can_delete_order_lines():
    line = new_line(length=1500)
    assert auth.delete(f"/order_lines?id=eq.{line['id']}").status_code in (401, 403)
    assert psql(f"select count(*) from order_lines where id='{line['id']}'") == "1"


def test_authenticated_can_register_multiple_lines_for_one_order_without_stock_change():
    before, led_before = santaf_qty(2500), psql("select count(*) from led_inventory")
    for ln, cat in [(2500, "SANTAF"), (3000, "SANTAF"), (15000, "LED"), (3000, "LED")]:
        new_line(length=ln, category=cat, order="844122")
    rows = auth.get("/order_lines?customer_order_number=eq.844122").json()
    assert len(rows) == 4
    assert santaf_qty(2500) == before
    assert psql("select count(*) from led_inventory") == led_before


def test_db_constraints_reject_bad_rows():
    for bad in ({"quantity": 0}, {"length_mm": 0}, {"category": "X"}, {"customer_order_number": "  "},
                {"stock_applied": True}):
        row = {"customer_order_number": "B", "category": "LED", "length_mm": 1000, "quantity": 1, **bad}
        assert auth.post("/order_lines", json=row).status_code == 400, bad


# ---------------- receiving: correctness ----------------
def test_receive_santaf_increments_once_and_logs_movement():
    before, mv = santaf_qty(3500), int(psql("select count(*) from santaf_movements"))
    line = new_line(length=3500, qty=6)
    r = receive(auth, line["id"], qty=6)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["status"] == "received" and got["stock_applied"] and got["storage_location"] == "B-01"
    assert santaf_qty(3500) == before + 6
    assert int(psql("select count(*) from santaf_movements")) == mv + 1


def test_receive_led_creates_batch_row():
    line = new_line(category="LED", length=1500, qty=2, order="844500", supplier_order_ref="S-9")
    assert receive(auth, line["id"], qty=2, loc="A-05").status_code == 200
    row = psql("select order_number||'|'||led_type||'|'||quantity||'|'||notes from led_inventory "
               "where order_number='S-9'")
    assert row.startswith("S-9|LED 1.5 מ'|2|") and "844500" in row and "A-05" in row


def test_second_receive_of_same_line_fails_and_does_not_increment():
    line = new_line(length=4000, qty=5)
    before = santaf_qty(4000)
    assert receive(auth, line["id"], qty=5).status_code == 200
    r = receive(auth, line["id"], qty=5)
    assert r.status_code >= 400 and "ORDERERR" in r.text
    assert santaf_qty(4000) == before + 5


def test_validation_errors_leave_everything_unchanged():
    line = new_line(length=4500, qty=5)
    before = santaf_qty(4500)
    assert receive(auth, line["id"], qty=0).status_code >= 400
    assert receive(auth, line["id"], qty=5, loc="  ").status_code >= 400
    assert santaf_qty(4500) == before
    assert psql(f"select status from order_lines where id='{line['id']}'") == "ordered"


def test_missing_inventory_row_fails_cleanly():
    line = new_line(length=9999, qty=1)
    r = receive(auth, line["id"], qty=1)
    assert r.status_code >= 400
    assert psql(f"select status from order_lines where id='{line['id']}'") == "ordered"


def test_cancelled_line_cannot_be_received():
    line = new_line(length=5000, qty=2)
    assert auth.patch(f"/order_lines?id=eq.{line['id']}&status=eq.ordered",
                      json={"status": "cancelled"}).json()
    before = santaf_qty(5000)
    assert receive(auth, line["id"], qty=2).status_code >= 400
    assert santaf_qty(5000) == before


# ---------------- receiving: atomicity & concurrency ----------------
def test_failure_after_stock_update_rolls_everything_back():
    """Movement insert is made to fail (qty 777 trigger) AFTER the stock update ran."""
    line = new_line(length=5500, qty=777)
    before, mv = santaf_qty(5500), int(psql("select count(*) from santaf_movements"))
    r = receive(auth, line["id"], qty=777)
    assert r.status_code >= 400
    assert santaf_qty(5500) == before
    assert int(psql("select count(*) from santaf_movements")) == mv
    assert psql(f"select status||'|'||stock_applied from order_lines where id='{line['id']}'") == "ordered|false"


def test_parallel_double_submit_of_one_line_increments_exactly_once():
    line = new_line(length=6000, qty=7)
    before = santaf_qty(6000)
    with cf.ThreadPoolExecutor(16) as ex:
        results = list(ex.map(lambda _: receive(client("authenticated"), line["id"], qty=7).status_code,
                              range(16)))
    assert results.count(200) == 1, results
    assert santaf_qty(6000) == before + 7
    assert psql(f"select count(*) from santaf_movements where length=6000 and quantity=7") == "1"


def test_parallel_receipts_of_different_lines_lose_no_updates():
    lines = [new_line(length=6500, qty=q) for q in range(1, 21)]      # 1..20 => sum 210
    before = santaf_qty(6500)
    with cf.ThreadPoolExecutor(20) as ex:
        codes = list(ex.map(lambda ln: receive(client("authenticated"), ln["id"], qty=ln["quantity"]).status_code,
                            lines))
    assert codes == [200] * 20, codes
    assert santaf_qty(6500) == before + 210


# ---------------- stage B: legacy tables locked ----------------
def test_zz_stage_b_migration_locks_legacy_tables():
    # Before: anon can still write legacy tables (today's presumed state)
    r = anon.post("/led_inventory", json={"order_number": "pre", "led_type": "t", "quantity": 1})
    assert r.status_code == 201
    sql = open(os.path.join(os.path.dirname(__file__), "..", "..", "migrations",
                            "002_lock_legacy_tables.sql"), encoding="utf-8").read()
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    out = subprocess.run(["docker", "exec", "-i", PG_CONTAINER, "psql", "-U", "postgres", "-v",
                          "ON_ERROR_STOP=1", "-q"], input=sql, capture_output=True, text=True, encoding="utf-8", env=env)
    assert out.returncode == 0, out.stderr
    for tbl, row in [("led_inventory", {"order_number": "x", "led_type": "t", "quantity": 1}),
                     ("santaf_movements", {"length": 2000, "quantity": 1, "type": "IN"})]:
        assert anon.get(f"/{tbl}").status_code in (401, 403)
        assert anon.post(f"/{tbl}", json=row).status_code in (401, 403)
        assert anon.patch(f"/{tbl}?id=gt.0", json={"quantity": 99}).status_code in (401, 403)
    # authenticated keeps working
    assert auth.post("/led_inventory", json={"order_number": "post", "led_type": "t", "quantity": 1}).status_code == 201
    assert auth.get("/santaf_inventory?select=length,quantity,min_quantity").status_code == 200
    assert auth.patch("/santaf_inventory?length=eq.8000", json={"min_quantity": 25}).status_code == 200
    assert auth.delete("/led_inventory?order_number=eq.post").status_code in (401, 403)
    # receiving still works after hardening
    line = new_line(length=7000, qty=3)
    assert receive(auth, line["id"], qty=3).status_code == 200
