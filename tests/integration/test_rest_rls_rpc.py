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
    psql(f"insert into app_users (user_id, email, active) values "
         f"('{ADMIN}','admin@example.test',true), ('{FORMER}','former@example.test',true) "
         f"on conflict (user_id) do nothing")
    yield


auth, anon = client("authenticated"), client("anon")
DENIED = (401, 403)


def new_line(order="T1", category="SANTAF", supply="stock", length=2000, qty=4, **extra):
    row_ = {"customer_order_number": order, "category": category, "supply": supply,
            "length_mm": length, "quantity": qty}
    if category == "SANTAF":
        row_.update(item_spec="סנטף BH", color="שקוף")
    else:
        row_.update(item_spec="LED חם", supply="supplier")
    row_.update(extra)
    r = auth.post("/order_lines", json=row_)
    assert r.status_code == 201, r.text
    return r.json()[0]


def rpc(c, name, **params):
    return c.post(f"/rpc/{name}", json=params)


def pick(c, line_id, qty, staging="B-20"):
    return rpc(c, "pick_stock_line", p_line_id=line_id, p_qty=qty, p_date="2026-10-25", p_staging=staging)


def issue(c, ids, who="לקוח"):
    return rpc(c, "issue_order_lines", p_ids=ids if isinstance(ids, list) else [ids],
               p_date="2026-10-25", p_recipient=who)


def avail(length):
    return int(psql(f"select coalesce(quantity,0) from santaf_inventory where length={length}"))


def set_stock(length, qty):
    psql(f"update santaf_inventory set quantity={qty} where length={length}")


def movements(length, typ):
    return int(psql(f"select coalesce(sum(quantity),0) from santaf_movements where length={length} and type='{typ}'"))


def row(line_id):
    return auth.get(f"/order_lines?id=eq.{line_id}").json()[0]


# ================= STAGE A: after 001 only (today's legacy state: RLS off) =================
def test_anon_has_no_access_to_orders_or_functions():
    assert anon.get("/order_lines").status_code in DENIED
    assert anon.post("/order_lines", json={"customer_order_number": "x", "category": "LED",
                                           "length_mm": 1, "quantity": 1}).status_code in DENIED
    line = new_line(length=1500)
    for name, params in [("pick_stock_line", dict(p_line_id=line["id"], p_qty=1, p_date="2026-10-25", p_staging="B-1")),
                         ("unpick_stock_line", dict(p_line_id=line["id"], p_date="2026-10-25")),
                         ("issue_order_lines", dict(p_ids=[line["id"]], p_date="2026-10-25", p_recipient="x")),
                         ("is_approved_user", {})]:
        assert rpc(anon, name, **params).status_code in (401, 403, 404), name
    assert row(line["id"])["picked_qty"] == 0


def test_only_the_approved_owner_can_use_the_database():
    mine = lambda sub: client("authenticated", sub)
    assert rpc(mine(ADMIN), "is_approved_user").json() is True
    assert rpc(mine(STRANGER), "is_approved_user").json() is False
    line = new_line(length=2500)
    s = mine(STRANGER)
    assert s.get("/order_lines").json() == []
    assert s.post("/order_lines", json={"customer_order_number": "x", "category": "LED", "length_mm": 1,
                                        "quantity": 1}).status_code in DENIED
    assert s.patch(f"/order_lines?id=eq.{line['id']}", json={"notes": "hacked"}).json() == []
    assert s.delete(f"/order_lines?id=eq.{line['id']}").json() == []
    r = pick(s, line["id"], 1)
    assert r.status_code >= 400 and "אין הרשאה" in r.text
    assert issue(s, line["id"]).status_code >= 400
    assert row(line["id"])["picked_qty"] == 0 and row(line["id"])["notes"] is None
    assert s.get("/app_users").status_code in DENIED and mine(ADMIN).get("/app_users").status_code in DENIED


def test_deactivated_owner_loses_access_immediately():
    c = client("authenticated", FORMER)
    new_line(length=2500)
    assert c.get("/order_lines").json()
    psql(f"update app_users set active=false where user_id='{FORMER}'")
    try:
        assert c.get("/order_lines").json() == []
        assert rpc(c, "is_approved_user").json() is False
    finally:
        psql(f"update app_users set active=true where user_id='{FORMER}'")


def test_stage_a_login_app_works_on_legacy_tables_before_002():
    """No lock-out window: authenticated already has grants on legacy tables and RLS is off."""
    assert auth.get("/santaf_inventory?select=length,quantity").status_code == 200
    assert auth.get("/led_inventory?select=id&limit=1").status_code == 200
    assert auth.patch("/santaf_inventory?length=eq.8000", json={"min_quantity": 20}).status_code == 200


@pytest.mark.parametrize("bad", [
    dict(color="אדום"),                                   # colored santaf is never standard stock
    dict(supplier_status="ordered"),                      # stock lines are not supplier-ordered
    dict(category="LED"),                                 # LED with supply=stock
    dict(quantity=0), dict(length_mm=0), dict(package_no=0),
    dict(supply="elsewhere"), dict(category="X"), dict(customer_order_number="  "),
])
def test_stock_line_rules_enforced_by_database(bad):
    row_ = {"customer_order_number": "B", "category": "SANTAF", "supply": "stock", "length_mm": 2000,
            "quantity": 1, "color": "שקוף", **bad}
    assert auth.post("/order_lines", json=row_).status_code == 400, bad


@pytest.mark.parametrize("bad", [
    dict(supplier_status="received"),                     # received needs a receipt date
    dict(received_date="2026-10-20"),                     # date only when received
    dict(supplier_status="done"),                         # only the 4 statuses exist
    dict(issued_on="2026-10-25", recipient="x"),          # cannot be created already issued
    dict(picked_qty=1),                                   # only supply=stock can be picked
])
def test_supplier_line_rules_enforced_by_database(bad):
    row_ = {"customer_order_number": "B", "category": "SANTAF", "supply": "supplier", "length_mm": 4500,
            "quantity": 9, "color": "אדום", **bad}
    assert auth.post("/order_lines", json=row_).status_code == 400, bad


def test_four_supplier_statuses_and_independence_from_stock():
    stock_before, led_before = avail(4500), psql("select sum(quantity) from led_inventory")
    for status, extra in [(None, {}), ("ordered", {}), ("pending", {}),
                          ("received", {"received_date": "2026-10-20", "storage_location": "B-02"})]:
        line = new_line(order="ST", supply="supplier", length=4500, qty=9, supplier_status=status, color="אדום", **extra)
        assert row(line["id"])["supplier_status"] == status
    new_line(order="ST", category="LED", length=3000, qty=2, supplier_status="received",
             received_date="2026-10-20", storage_location="A-03")
    assert avail(4500) == stock_before                     # ordering/receiving never moves stock
    assert psql("select sum(quantity) from led_inventory") == led_before


def test_multiple_lines_one_order_both_categories_stay_separate():
    for cat, sup, ln, q in [("LED", "supplier", 1500, 2), ("LED", "supplier", 3000, 1),
                            ("SANTAF", "stock", 2000, 4), ("SANTAF", "stock", 3000, 2)]:
        new_line(order="844122", category=cat, supply=sup, length=ln, qty=q)
    rows = auth.get("/order_lines?customer_order_number=eq.844122").json()
    assert len(rows) == 4 and {r["category"] for r in rows} == {"LED", "SANTAF"}


def test_direct_updates_cannot_fake_stock_state():
    line = new_line(length=3500, qty=2)
    for change in ({"picked_qty": 2}, {"issued_on": "2026-10-25", "recipient": "x"}, {"recipient": "x"},
                   {"picked_on": "2026-10-25"}):
        assert auth.patch(f"/order_lines?id=eq.{line['id']}", json=change).status_code >= 400, change
    assert row(line["id"])["picked_qty"] == 0


# ---- the 100 -> 88 -> 88 accounting, on real lengths
def test_pick_deducts_available_immediately_and_issue_does_not_deduct_again():
    set_stock(2000, 100)
    line = new_line(order="ACC", length=2000, qty=12)
    r = pick(auth, line["id"], 12, staging="B-20")
    assert r.status_code == 200, r.text
    assert avail(2000) == 88                                           # available
    got = row(line["id"])
    assert got["picked_qty"] == 12 and got["staging_location"] == "B-20" and got["issued_on"] is None
    assert avail(2000) + got["picked_qty"] == 100                      # still physically in the warehouse
    assert movements(2000, "PICK") >= 12
    out_before = movements(2000, "OUT")
    r = issue(auth, line["id"], who="דוד")
    assert r.status_code == 200 and r.json() == 1
    assert avail(2000) == 88                                           # NOT deducted twice
    assert movements(2000, "OUT") == out_before + 12
    done = row(line["id"])
    assert done["issued_on"] == "2026-10-25" and done["recipient"] == "דוד"
    assert issue(auth, line["id"]).status_code >= 400                  # no duplicate issuance
    assert pick(auth, line["id"], 1).status_code >= 400                # no pick after issuance
    assert avail(2000) == 88 and movements(2000, "OUT") == out_before + 12


def test_pick_more_than_available_or_remaining_is_refused_without_change():
    set_stock(2500, 5)
    line = new_line(order="LOW", length=2500, qty=9)
    r = pick(auth, line["id"], 9)
    assert r.status_code >= 400 and "אין מספיק מלאי" in r.text
    assert avail(2500) == 5 and row(line["id"])["picked_qty"] == 0
    assert pick(auth, line["id"], 10).status_code >= 400               # more than the line needs
    assert pick(auth, line["id"], 0).status_code >= 400
    assert pick(auth, line["id"], 2, staging="  ").status_code >= 400  # staging location required
    assert pick(auth, line["id"], 5).status_code == 200                # partial pick of what exists
    assert avail(2500) == 0 and row(line["id"])["picked_qty"] == 5
    assert pick(auth, line["id"], 1).status_code >= 400                # stock exhausted, never negative
    assert avail(2500) == 0
    assert issue(auth, line["id"]).status_code >= 400                  # not fully picked yet


def test_unpick_restores_stock_once_and_not_after_issue():
    set_stock(3000, 10)
    line = new_line(order="UNP", length=3000, qty=4)
    assert pick(auth, line["id"], 4).status_code == 200 and avail(3000) == 6
    r = rpc(auth, "unpick_stock_line", p_line_id=line["id"], p_date="2026-10-25")
    assert r.status_code == 200 and avail(3000) == 10 and row(line["id"])["picked_qty"] == 0
    assert rpc(auth, "unpick_stock_line", p_line_id=line["id"], p_date="2026-10-25").status_code >= 400
    assert avail(3000) == 10                                           # no double restore
    assert pick(auth, line["id"], 4).status_code == 200
    assert issue(auth, line["id"]).status_code == 200
    assert rpc(auth, "unpick_stock_line", p_line_id=line["id"], p_date="2026-10-25").status_code >= 400
    assert avail(3000) == 6


def test_pick_failure_after_stock_update_rolls_everything_back():
    """Movement insert fails (trigger on quantity 777) AFTER stock was decremented."""
    set_stock(5500, 800)
    line = new_line(order="RB", length=5500, qty=777)
    mv = int(psql("select count(*) from santaf_movements"))
    assert pick(auth, line["id"], 777).status_code >= 400
    assert avail(5500) == 800 and row(line["id"])["picked_qty"] == 0
    assert int(psql("select count(*) from santaf_movements")) == mv


def test_parallel_double_pick_of_one_line_deducts_exactly_once():
    set_stock(6000, 50)
    line = new_line(order="PAR", length=6000, qty=7)
    with cf.ThreadPoolExecutor(16) as ex:
        codes = list(ex.map(lambda _: pick(client("authenticated"), line["id"], 7).status_code, range(16)))
    assert codes.count(200) == 1, codes
    assert avail(6000) == 43 and row(line["id"])["picked_qty"] == 7


def test_parallel_picks_competing_for_limited_stock_never_go_negative():
    set_stock(6500, 10)
    lines = [new_line(order=f"C{i}", length=6500, qty=1) for i in range(20)]
    with cf.ThreadPoolExecutor(20) as ex:
        codes = list(ex.map(lambda ln: pick(client("authenticated"), ln["id"], 1).status_code, lines))
    assert codes.count(200) == 10, codes
    assert avail(6500) == 0
    assert sum(r["picked_qty"] for r in auth.get("/order_lines?length_mm=eq.6500").json()) == 10


def test_parallel_double_issue_logs_one_movement():
    set_stock(7000, 10)
    line = new_line(order="DI", length=7000, qty=3)
    assert pick(auth, line["id"], 3).status_code == 200
    out_before = movements(7000, "OUT")
    with cf.ThreadPoolExecutor(12) as ex:
        codes = list(ex.map(lambda _: issue(client("authenticated"), line["id"]).status_code, range(12)))
    assert codes.count(200) == 1, codes
    assert movements(7000, "OUT") == out_before + 3 and avail(7000) == 7


# ---- customer-specific Santaf and LED packages: tracked, never general stock
def test_custom_santaf_and_led_package_lifecycle_never_touches_general_stock():
    stock0 = psql("select sum(quantity) from santaf_inventory")
    led0 = psql("select sum(quantity) from led_inventory")
    mv0 = psql("select count(*) from santaf_movements")
    santaf = new_line(order="CUS", supply="supplier", length=9000, qty=25, color="אדום", supplier_status="ordered")
    led1 = new_line(order="CUS", category="LED", length=1500, qty=2, package_no=1, supplier_status="ordered")
    led2 = new_line(order="CUS", category="LED", length=3000, qty=1, package_no=1, supplier_status="ordered")
    led3 = new_line(order="CUS", category="LED", length=3000, qty=1, package_no=2)
    # not received yet: cannot be issued
    assert issue(auth, santaf["id"]).status_code >= 400
    assert issue(auth, [led1["id"], led2["id"]]).status_code >= 400
    # receive the package (both lines together, one statement) and the santaf line
    r = auth.patch("/order_lines?customer_order_number=eq.CUS&category=eq.LED&package_no=eq.1",
                   json={"supplier_status": "received", "received_date": "2026-10-20", "storage_location": "A-03"})
    assert r.status_code == 200 and len(r.json()) == 2
    assert auth.patch(f"/order_lines?id=eq.{santaf['id']}",
                      json={"supplier_status": "received", "received_date": "2026-10-20",
                            "storage_location": "B-02"}).status_code == 200
    assert row(led3["id"])["supplier_status"] is None                  # other package untouched
    # staging + issue: package is issued as one unit, all or nothing
    assert auth.patch(f"/order_lines?id=in.({led1['id']},{led2['id']})", json={"staging_location": "A-11"}).status_code == 200
    assert issue(auth, [led1["id"], led2["id"], led3["id"]]).status_code >= 400   # led3 not received -> nothing issued
    assert row(led1["id"])["issued_on"] is None and row(led2["id"])["issued_on"] is None
    assert issue(auth, [led1["id"], led2["id"]], who="הלקוח").json() == 2
    assert issue(auth, santaf["id"], who="הלקוח").json() == 1
    assert psql("select sum(quantity) from santaf_inventory") == stock0
    assert psql("select sum(quantity) from led_inventory") == led0
    assert psql("select count(*) from santaf_movements") == mv0         # no movements either
    assert issue(auth, santaf["id"]).status_code >= 400                  # no duplicate issuance
    assert issue(auth, [led3["id"]], who="").status_code >= 400          # recipient required


def test_issued_lines_are_locked_except_notes():
    line = new_line(order="LCK", supply="supplier", length=9500, qty=1, color="כחול", supplier_status="received",
                    received_date="2026-10-20")
    assert issue(auth, line["id"]).status_code == 200
    for change in ({"quantity": 5}, {"supplier_status": "pending", "received_date": None},
                   {"storage_location": "X"}, {"coordination_date": "2026-11-01"}):
        assert auth.patch(f"/order_lines?id=eq.{line['id']}", json=change).status_code >= 400, change
    assert auth.patch(f"/order_lines?id=eq.{line['id']}", json={"notes": "תיקון הערה"}).status_code == 200
    assert auth.delete(f"/order_lines?id=eq.{line['id']}").json() == []


def test_delete_only_lines_without_stock_effect():
    set_stock(7500, 10)
    free = new_line(order="DEL", length=7500, qty=2)
    assert auth.delete(f"/order_lines?id=eq.{free['id']}").json() != []
    picked = new_line(order="DEL", length=7500, qty=2)
    assert pick(auth, picked["id"], 2).status_code == 200
    assert auth.delete(f"/order_lines?id=eq.{picked['id']}").json() == []
    assert row(picked["id"])["picked_qty"] == 2
    assert avail(7500) == 8
    assert anon.delete(f"/order_lines?id=eq.{picked['id']}").status_code in DENIED


def test_cannot_change_size_of_a_picked_line():
    set_stock(8000, 10)
    line = new_line(order="SZ", length=8000, qty=3)
    assert pick(auth, line["id"], 3).status_code == 200
    assert auth.patch(f"/order_lines?id=eq.{line['id']}", json={"quantity": 1}).status_code >= 400
    assert auth.patch(f"/order_lines?id=eq.{line['id']}", json={"length_mm": 2000}).status_code >= 400


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
    # order workflow keeps working after hardening (pick/issue are SECURITY DEFINER)
    set_stock(1500, 10)
    line = new_line(order="POST", length=1500, qty=3)
    assert pick(auth, line["id"], 3).status_code == 200 and avail(1500) == 7
    assert issue(auth, line["id"]).status_code == 200 and avail(1500) == 7


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
    assert psql("select count(*) from pg_policies", db="lifecycle") == "4"      # only the 4 order_lines policies, nothing half-applied
    assert psql("select has_table_privilege('anon','led_inventory','INSERT')::text", db="lifecycle") == "true"

    psql(f"insert into app_users (user_id,email) values ('{ADMIN}','admin@example.test')", db="lifecycle")
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
