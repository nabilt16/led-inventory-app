"""Integration tests against a LOCAL throwaway Postgres + PostgREST (see tests/sql/rest_setup.sql,
which mirrors the production state found by the read-only audit).
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

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ADMIN = "11111111-1111-1111-1111-111111111111"      # approved
STRANGER = "22222222-2222-2222-2222-222222222222"   # logged in, never approved
FORMER = "33333333-3333-3333-3333-333333333333"     # approved, then deactivated


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def token(role, sub=ADMIN):
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    claims = {"role": role, "exp": int(time.time()) + 3600}
    if role == "authenticated":
        claims["sub"] = sub
    body = _b64(json.dumps(claims).encode())
    sig = hmac.new(SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(sig)}"


def client(role, sub=ADMIN):
    return httpx.Client(base_url=URL, timeout=30,
                        headers={"Authorization": f"Bearer {token(role, sub)}", "Prefer": "return=representation"})


def _docker_psql(args, sql=None, db="postgres"):
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    out = subprocess.run(["docker", "exec", "-i", PG_CONTAINER, "psql", "-U", "postgres", "-d", db, "-v",
                          "ON_ERROR_STOP=1", *args], input=sql, capture_output=True, text=True,
                         encoding="utf-8", env=env)
    return out


def psql(sql, db="postgres"):
    out = _docker_psql(["-At", "-c", sql], db=db)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def run_file(relpath, db="postgres", expect_ok=True):
    with open(os.path.join(ROOT, relpath), encoding="utf-8") as f:
        out = _docker_psql(["-q"], sql=f.read(), db=db)
    if expect_ok:
        assert out.returncode == 0, out.stderr
    return out


@pytest.fixture(scope="module", autouse=True)
def approve_test_users():
    psql(f"insert into app_users (user_id, email, role, active) values "
         f"('{ADMIN}','admin@example.test','admin',true), ('{FORMER}','former@example.test','warehouse',true) "
         f"on conflict (user_id) do nothing")
    yield


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


DENIED = (401, 403)


# ================= STAGE A: after 001 only (today's legacy state: RLS off) =================
def test_anon_cannot_read_or_write_order_lines():
    assert anon.get("/order_lines").status_code in DENIED
    r = anon.post("/order_lines", json={"customer_order_number": "x", "category": "LED",
                                        "length_mm": 1, "quantity": 1})
    assert r.status_code in DENIED


def test_anon_cannot_call_receive_or_approval_rpc():
    line = new_line(length=1500)
    assert receive(anon, line["id"]).status_code in (401, 403, 404)
    assert anon.post("/rpc/is_approved_user", json={}).status_code in (401, 403, 404)
    assert psql(f"select status from order_lines where id='{line['id']}'") == "ordered"


def test_approval_allowlist_is_enforced_in_the_database():
    mine = lambda sub: client("authenticated", sub)
    assert mine(ADMIN).post("/rpc/is_approved_user", json={}).json() is True
    assert mine(STRANGER).post("/rpc/is_approved_user", json={}).json() is False
    line = new_line(length=2500)
    for sub in (STRANGER,):
        c = mine(sub)
        assert c.get("/order_lines").json() == []                         # sees nothing
        assert c.post("/order_lines", json={"customer_order_number": "x", "category": "LED",
                                            "length_mm": 1, "quantity": 1}).status_code in DENIED
        assert c.patch(f"/order_lines?id=eq.{line['id']}", json={"notes": "hacked"}).json() == []
        r = receive(c, line["id"])
        assert r.status_code >= 400 and "אין הרשאה" in r.text
    assert psql(f"select status||'|'||coalesce(notes,'') from order_lines where id='{line['id']}'") == "ordered|"
    assert mine(STRANGER).get("/app_users").status_code in DENIED          # allowlist unreadable via API
    assert mine(ADMIN).get("/app_users").status_code in DENIED
    assert mine(ADMIN).post("/app_users", json={"user_id": STRANGER, "email": "x"}).status_code in DENIED


def test_deactivated_user_loses_access_immediately():
    c = client("authenticated", FORMER)
    line = new_line(length=2500)
    assert c.get("/order_lines").status_code == 200 and c.get("/order_lines").json()
    psql(f"update app_users set active=false where user_id='{FORMER}'")
    try:
        assert c.get("/order_lines").json() == []
        assert receive(c, line["id"]).status_code >= 400
        assert c.post("/rpc/is_approved_user", json={}).json() is False
    finally:
        psql(f"update app_users set active=true where user_id='{FORMER}'")


def test_stage_a_login_app_works_on_legacy_tables_before_002():
    """No lock-out window: authenticated users already have table grants and RLS is off."""
    assert auth.get("/santaf_inventory?select=length,quantity").status_code == 200
    assert auth.get("/led_inventory?select=id&limit=1").status_code == 200
    assert auth.patch("/santaf_inventory?length=eq.8000", json={"min_quantity": 20}).status_code == 200
    r = auth.post("/led_inventory", json={"order_number": "A-test", "led_type": "t", "quantity": 0})
    assert r.status_code == 201
    psql("delete from led_inventory where order_number='A-test'")


def test_nobody_can_delete_order_lines():
    line = new_line(length=1500)
    assert auth.delete(f"/order_lines?id=eq.{line['id']}").status_code in DENIED
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


def test_receive_santaf_increments_once_and_logs_movement():
    before, mv = santaf_qty(3500), int(psql("select count(*) from santaf_movements"))
    line = new_line(length=3500, qty=6)
    r = receive(auth, line["id"], qty=6)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["status"] == "received" and got["stock_applied"] and got["storage_location"] == "B-01"
    assert santaf_qty(3500) == before + 6
    assert int(psql("select count(*) from santaf_movements")) == mv + 1
    # movement keeps the app's timestamp convention (date at midnight)
    assert psql("select \"date\"::text from santaf_movements order by id desc limit 1") == "2026-10-20 00:00:00"


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
    assert receive(auth, line["id"], qty=1).status_code >= 400
    assert psql(f"select status from order_lines where id='{line['id']}'") == "ordered"


def test_cancelled_line_cannot_be_received():
    line = new_line(length=5000, qty=2)
    assert auth.patch(f"/order_lines?id=eq.{line['id']}&status=eq.ordered", json={"status": "cancelled"}).json()
    before = santaf_qty(5000)
    assert receive(auth, line["id"], qty=2).status_code >= 400
    assert santaf_qty(5000) == before


def test_failure_after_stock_update_rolls_everything_back():
    """Movement insert is made to fail (qty 777 trigger) AFTER the stock update ran."""
    line = new_line(length=5500, qty=777)
    before, mv = santaf_qty(5500), int(psql("select count(*) from santaf_movements"))
    assert receive(auth, line["id"], qty=777).status_code >= 400
    assert santaf_qty(5500) == before
    assert int(psql("select count(*) from santaf_movements")) == mv
    assert psql(f"select status||'|'||stock_applied from order_lines where id='{line['id']}'") == "ordered|false"


def test_parallel_double_submit_of_one_line_increments_exactly_once():
    line = new_line(length=6000, qty=7)
    before = santaf_qty(6000)
    with cf.ThreadPoolExecutor(16) as ex:
        results = list(ex.map(lambda _: receive(client("authenticated"), line["id"], qty=7).status_code, range(16)))
    assert results.count(200) == 1, results
    assert santaf_qty(6000) == before + 7
    assert psql("select count(*) from santaf_movements where length=6000 and quantity=7") == "1"


def test_parallel_receipts_of_different_lines_lose_no_updates():
    lines = [new_line(length=6500, qty=q) for q in range(1, 21)]      # 1..20 => sum 210
    before = santaf_qty(6500)
    with cf.ThreadPoolExecutor(20) as ex:
        codes = list(ex.map(lambda ln: receive(client("authenticated"), ln["id"], qty=ln["quantity"]).status_code, lines))
    assert codes == [200] * 20, codes
    assert santaf_qty(6500) == before + 210


# ================= STAGE B: after 002 =================
def test_zz_stage_b_closes_anonymous_access_and_keeps_approved_users_working():
    # Before: anon can still write legacy tables (today's real production state)
    assert anon.post("/led_inventory", json={"order_number": "pre", "led_type": "t", "quantity": 1}).status_code == 201
    psql("delete from led_inventory where order_number='pre'")
    run_file("migrations/002_lock_legacy_tables.sql")
    for tbl, row in [("led_inventory", {"order_number": "x", "led_type": "t", "quantity": 1}),
                     ("santaf_inventory", {"length": 1, "quantity": 1}),
                     ("santaf_movements", {"length": 2000, "quantity": 1, "type": "IN"}),
                     ("leds_inventory", {"order_number": "x", "led_type": "t", "quantity": 1})]:
        assert anon.get(f"/{tbl}").status_code in DENIED, tbl
        assert anon.post(f"/{tbl}", json=row).status_code in DENIED, tbl
        assert anon.patch(f"/{tbl}?id=gt.0", json={"quantity": 99}).status_code in DENIED, tbl
        assert anon.delete(f"/{tbl}?id=gt.0").status_code in DENIED, tbl
    # logged in but NOT approved: sees nothing, writes nothing
    s = client("authenticated", STRANGER)
    assert s.get("/led_inventory").json() == [] and s.get("/santaf_inventory").json() == []
    assert s.post("/led_inventory", json={"order_number": "x", "led_type": "t", "quantity": 1}).status_code in DENIED
    assert s.patch("/santaf_inventory?length=eq.8000", json={"min_quantity": 1}).json() == []
    # approved: legacy screens keep working
    assert auth.get("/led_inventory?select=id,order_number,led_type,quantity,notes,created_at&order=created_at.desc").status_code == 200
    assert len(auth.get("/santaf_inventory?select=*&order=length").json()) == 14
    assert auth.patch("/santaf_inventory?length=eq.8000", json={"min_quantity": 20}).status_code == 200
    assert auth.post("/led_inventory", json={"order_number": "post", "led_type": "t", "quantity": 1}).status_code == 201
    assert auth.post("/santaf_movements", json={"length": 2000, "quantity": 1, "type": "IN", "date": "2026-10-20"}).status_code == 201
    # nobody deletes through the API; unused leds_inventory is read-only
    assert auth.delete("/led_inventory?order_number=eq.post").status_code in DENIED
    assert auth.get("/leds_inventory").status_code == 200
    assert auth.post("/leds_inventory", json={"order_number": "x"}).status_code in DENIED
    assert psql("select has_table_privilege('authenticated','led_inventory','TRUNCATE')::text") == "false"
    assert psql("select has_table_privilege('authenticated','led_inventory','DELETE')::text") == "false"
    assert psql("select has_table_privilege('anon','santaf_inventory','SELECT')::text") == "false"
    # receiving still works after hardening, exactly once
    line = new_line(length=7000, qty=3)
    before = santaf_qty(7000)
    assert receive(auth, line["id"], qty=3).status_code == 200
    assert receive(auth, line["id"], qty=3).status_code >= 400
    assert santaf_qty(7000) == before + 3


# ================= Release lifecycle on a separate database (checksums, idempotence, rollback) =================
def _checksums(db):
    q = " union all ".join(f"select '{t}', count(*)::text, coalesce(md5(string_agg(x::text, '|' order by id)), '-') from {t} x"
                           for t in ("led_inventory", "leds_inventory", "santaf_inventory", "santaf_movements"))
    return psql(q, db=db)


def test_zzz_release_lifecycle_preserves_data_and_recovers_without_public_writes():
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    subprocess.run(["docker", "exec", PG_CONTAINER, "psql", "-U", "postgres", "-c", "drop database if exists lifecycle"],
                   env=env, capture_output=True)
    assert subprocess.run(["docker", "exec", PG_CONTAINER, "psql", "-U", "postgres", "-c", "create database lifecycle"],
                          env=env, capture_output=True).returncode == 0
    # fresh copy of the production-like schema (roles already exist cluster-wide)
    with open(os.path.join(ROOT, "tests", "sql", "rest_setup.sql"), encoding="utf-8") as f:
        setup = "\n".join(l for l in f.read().split("\n") if not l.startswith(("CREATE ROLE", "GRANT anon, authenticated TO")))
    out = _docker_psql(["-q"], sql=setup, db="lifecycle")
    assert out.returncode == 0, out.stderr
    psql("select 1", db="lifecycle")

    baseline = _checksums("lifecycle")
    assert "led_inventory|18|" in baseline and "santaf_movements|31|" in baseline
    assert psql("select sum(quantity) from led_inventory", db="lifecycle") == "50"
    assert psql("select sum(quantity) from santaf_inventory", db="lifecycle") == "179"

    # 002 must refuse to run before 001 / before anyone is approved (anti lock-out)
    assert run_file("migrations/002_lock_legacy_tables.sql", db="lifecycle", expect_ok=False).returncode != 0
    run_file("migrations/001_order_lines.sql", db="lifecycle")
    run_file("migrations/001_order_lines.sql", db="lifecycle")                   # idempotent
    assert _checksums("lifecycle") == baseline                                  # 001 changed no legacy data
    out = run_file("migrations/002_lock_legacy_tables.sql", db="lifecycle", expect_ok=False)
    assert out.returncode != 0 and "no approved active user" in out.stderr
    assert psql("select count(*) from pg_policies", db="lifecycle") == "3"      # only order_lines policies, nothing half-applied
    assert psql("select has_table_privilege('anon','led_inventory','INSERT')::text", db="lifecycle") == "true"

    psql(f"insert into app_users (user_id,email,role) values ('{ADMIN}','admin@example.test','admin')", db="lifecycle")
    run_file("migrations/002_lock_legacy_tables.sql", db="lifecycle")
    run_file("migrations/002_lock_legacy_tables.sql", db="lifecycle")           # idempotent
    assert _checksums("lifecycle") == baseline                                  # 002 changed no data either
    for t in ("led_inventory", "leds_inventory", "santaf_inventory", "santaf_movements"):
        assert psql(f"select relrowsecurity::text from pg_class where relname='{t}'", db="lifecycle") == "true"
        assert psql(f"select has_table_privilege('anon','{t}','SELECT')::text", db="lifecycle") == "false"

    # Recovery 1 (preferred): lock-out fixed by approving a user; public access stays closed
    psql(f"update app_users set active=false", db="lifecycle")
    out = run_file("migrations/002_lock_legacy_tables.sql", db="lifecycle", expect_ok=False)
    assert out.returncode != 0                                                  # refuses while nobody is approved
    approve = open(os.path.join(ROOT, "ops", "approve_user.sql"), encoding="utf-8").read().replace(
        "PUT-THE-USER-EMAIL-HERE@example.com", "admin@example.test")
    assert _docker_psql(["-q"], sql=approve, db="lifecycle").returncode == 0
    assert psql("select count(*) from app_users where active", db="lifecycle") == "1"
    assert psql("select has_table_privilege('anon','led_inventory','INSERT')::text", db="lifecycle") == "false"

    # Recovery 2 (last resort): emergency revert restores exactly the audited original state
    run_file("ops/emergency_revert_002.sql", db="lifecycle")
    assert _checksums("lifecycle") == baseline
    for t in ("led_inventory", "leds_inventory", "santaf_inventory", "santaf_movements"):
        assert psql(f"select relrowsecurity::text from pg_class where relname='{t}'", db="lifecycle") == "false"
        assert psql(f"select count(*) from pg_policies where tablename='{t}'", db="lifecycle") == "0"
        assert psql(f"select has_table_privilege('anon','{t}','DELETE')::text", db="lifecycle") == "true"
    run_file("migrations/002_lock_legacy_tables.sql", db="lifecycle")           # and re-locks cleanly
    assert psql("select has_table_privilege('anon','led_inventory','INSERT')::text", db="lifecycle") == "false"

    # rollback_001 refuses while 002 policies depend on it, refuses to destroy real orders, works when empty
    assert run_file("ops/rollback_001.sql", db="lifecycle", expect_ok=False).returncode != 0
    run_file("ops/emergency_revert_002.sql", db="lifecycle")
    psql("insert into order_lines (customer_order_number,category,length_mm,quantity) values ('X','LED',1000,1)", db="lifecycle")
    assert run_file("ops/rollback_001.sql", db="lifecycle", expect_ok=False).returncode != 0
    psql("delete from order_lines", db="lifecycle")
    run_file("ops/rollback_001.sql", db="lifecycle")
    assert psql("select to_regclass('public.order_lines') is null", db="lifecycle") == "t"
    assert _checksums("lifecycle") == baseline

    # the reset script also works on the real-shaped schema (not executed in production)
    run_file("migrations/001_order_lines.sql", db="lifecycle")
    run_file("ops/reset_stock_to_zero.sql", db="lifecycle")
    assert psql("select coalesce(sum(quantity),0) from led_inventory", db="lifecycle") == "0"
    assert psql("select coalesce(sum(quantity),0) from santaf_inventory", db="lifecycle") == "0"
    assert psql("select count(*) from led_inventory", db="lifecycle") == "18"
    assert psql("select count(*) from santaf_movements", db="lifecycle") == "31"
