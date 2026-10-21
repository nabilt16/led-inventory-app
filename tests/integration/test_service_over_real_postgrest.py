"""order_service driven through the real postgrest-py client against local PostgREST+Postgres."""
import base64
import hashlib
import hmac
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from test_rest_rls_rpc import SECRET, URL, psql, token  # noqa: E402

import order_service as svc  # noqa: E402

pytestmark = pytest.mark.skipif(not (URL and SECRET), reason="local PostgREST not configured")

LENGTHS = [1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000, 5500, 6000, 6500, 7000, 7500, 8000]


def make_client(role="authenticated"):
    from postgrest import SyncPostgrestClient
    c = SyncPostgrestClient(base_url=URL, headers={})
    c.auth(token(role))
    return c


@pytest.fixture()
def c():
    return make_client()


def avail(length):
    return int(psql(f"select coalesce(quantity,0) from santaf_inventory where length={length}"))


def led(c, order, length, qty=1, **kw):
    return svc.create_line(c, svc.build_line_payload(order, "LED", qty, length, item_spec="LED חם", **kw))


def santaf(c, order, length, qty, supply="stock", **kw):
    return svc.create_line(c, svc.build_line_payload(
        order, "SANTAF", qty, length, supply=supply,
        santaf_lengths=LENGTHS if supply == "stock" else (), **kw))


def test_order_with_led_packages_and_both_santaf_modes_end_to_end(c):
    psql("update santaf_inventory set quantity=100 where length=2000")
    psql("update santaf_inventory set quantity=4 where length=4500")
    stock0 = psql("select sum(quantity) from santaf_inventory")
    led0 = psql("select sum(quantity) from led_inventory")
    o = "E2E-844122"

    # --- enter the order: 2 LED lengths in package 1, 1 in package 2, standard + customer-specific santaf
    a = led(c, o, 1500, 2, coordination_date="2026-10-25", supplier_status="ordered", supplier_ref="S-1",
            supplier_order_date="2026-10-19")
    b = led(c, o, 3000, 1)                                              # joins package 1 (no coordination given)
    p2 = led(c, o, 3000, 1, package_no=2)
    s_std = santaf(c, o, 2000, 12)
    s_low = santaf(c, o, 4500, 9)
    s_cus = santaf(c, o, 9000, 25, supply="supplier", color="אדום", supplier_status="pending")
    assert (b["supplier_status"], b["supplier_ref"], b["coordination_date"]) == ("ordered", "S-1", "2026-10-25")
    assert p2["supplier_status"] is None and p2["coordination_date"] == "2026-10-25"
    assert psql("select sum(quantity) from santaf_inventory") == stock0     # entering lines changed nothing
    assert psql("select sum(quantity) from led_inventory") == led0

    # --- lookup, filters (NULL status = לא הוזמן), coordination date
    assert len(svc.search_lines(c, order_number="e2e-844122")) == 6
    assert {r["id"] for r in svc.search_lines(c, order_number=o, status="none")} == {p2["id"], s_std["id"], s_low["id"]}
    assert [r["id"] for r in svc.search_lines(c, order_number=o, status="pending")] == [s_cus["id"]]
    assert len(svc.search_lines(c, coordination_date="2026-10-25", category="LED", order_number=o)) == 3

    # --- package status/receipt updates the whole package only; stock untouched
    svc.update_supplier_info(c, a, "received", "S-1", "2026-10-19", "2026-10-20", "A-03")
    by = {r["id"]: r for r in svc.search_lines(c, order_number=o)}
    assert by[a["id"]]["storage_location"] == by[b["id"]]["storage_location"] == "A-03"
    assert by[p2["id"]]["supplier_status"] is None
    with pytest.raises(svc.OrderError) as e:
        svc.update_supplier_info(c, by[p2["id"]], "received")           # receipt date required
    assert "תאריך קבלה" in str(e.value)
    with pytest.raises(svc.OrderError):
        svc.update_supplier_info(c, by[s_std["id"]], "ordered")         # stock lines are not supplier-ordered

    # --- daily preparation: per-length stock sufficiency
    stock = svc.stock_levels(c)
    day = svc.daily_summary(svc.search_lines(c, coordination_date="2026-10-25"), stock)
    assert day["shortages"] == [{"length": 4500, "required": 9, "available": 4, "shortfall": 5}]

    # --- pick standard stock: 100 -> 88 immediately, staged 12
    svc.pick_line(c, s_std["id"], 12, "2026-10-25", "B-20")
    assert avail(2000) == 88
    with pytest.raises(svc.OrderError) as e:
        svc.pick_line(c, s_low["id"], 9, "2026-10-25", "B-20")
    assert "אין מספיק מלאי" in str(e.value) and avail(4500) == 4         # refused, nothing changed

    # --- location changes are explicit; coordination date change moves nothing
    svc.set_location(c, by[a["id"]], "staging_location", "A-11")
    svc.set_coordination_date(c, o, "2026-11-02")
    after = {r["id"]: r for r in svc.search_lines(c, order_number=o)}
    assert after[a["id"]]["staging_location"] == after[b["id"]]["staging_location"] == "A-11"
    assert after[a["id"]]["storage_location"] == "A-03"
    assert all(r["coordination_date"] == "2026-11-02" for r in after.values())

    # --- issue: the package as one unit, standard stock without a second deduction
    ids = svc.issue_target_ids(c, after[a["id"]])
    assert sorted(ids) == sorted([a["id"], b["id"]])
    with pytest.raises(svc.OrderError):
        svc.issue_lines(c, [p2["id"]], "2026-10-25", "הלקוח")           # not received
    svc.issue_lines(c, ids, "2026-10-25", "הלקוח")
    svc.issue_lines(c, [s_std["id"]], "2026-10-25", "הלקוח")
    assert avail(2000) == 88                                           # still 88: no double deduction
    with pytest.raises(svc.OrderError) as e:
        svc.issue_lines(c, [s_std["id"]], "2026-10-25", "הלקוח")       # duplicate issuance refused
    assert "כבר נופקה" in str(e.value)
    with pytest.raises(svc.OrderError):
        svc.update_supplier_info(c, after[a["id"]], "pending")          # issued lines are frozen
    assert psql("select sum(quantity) from led_inventory") == led0       # LED packages never entered general stock

    # --- customer-specific santaf: receive, stage, issue, never general stock
    svc.update_supplier_info(c, s_cus, "received", "S-9", "2026-10-18", "2026-10-21", "B-02")
    svc.issue_lines(c, [s_cus["id"]], "2026-10-25", "הלקוח")
    assert avail(2000) == 88 and avail(4500) == 4

    # --- delete: mistaken line yes, stock-affecting line no
    svc.delete_line(c, p2["id"])
    with pytest.raises(svc.OrderError):
        svc.delete_line(c, s_std["id"])
    assert psql(f"select count(*) from order_lines where customer_order_number='{o}'") == "5"


def test_unpick_roundtrip_and_errors_are_hebrew(c):
    psql("update santaf_inventory set quantity=10 where length=3000")
    line = santaf(c, "UNP-1", 3000, 4)
    svc.pick_line(c, line["id"], 4, "2026-10-25", "B-20")
    assert avail(3000) == 6
    svc.unpick_line(c, line["id"])
    assert avail(3000) == 10
    with pytest.raises(svc.OrderError) as e:
        svc.unpick_line(c, line["id"])
    assert "ORDERERR" not in str(e.value) and "אין איסוף להחזרה" in str(e.value)
    assert avail(3000) == 10


def test_database_constraint_errors_become_friendly_messages(c):
    line = led(c, "CON-1", 1500)
    with pytest.raises(svc.OrderError) as e:
        svc._run(lambda: c.table("order_lines").update({"supplier_status": "received"}).eq("id", line["id"]).execute())
    assert "תאריך קבלה" in str(e.value) and "violates" not in str(e.value)


def test_anon_client_is_rejected_with_a_safe_message():
    anon = make_client("anon")
    with pytest.raises(svc.OrderError) as e:
        svc.search_lines(anon)
    assert "התחברו מחדש" in str(e.value) or "לא בוצע שינוי" in str(e.value)
    with pytest.raises(svc.OrderError):
        svc.pick_line(anon, "00000000-0000-0000-0000-000000000000", 1, "2026-10-25", "B-1")


def test_expired_token_maps_to_relogin_message():
    from postgrest import SyncPostgrestClient
    b = lambda x: base64.urlsafe_b64encode(x).rstrip(b"=").decode()
    h = b(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b(json.dumps({"role": "authenticated", "exp": int(time.time()) - 60}).encode())
    sig = b(hmac.new(SECRET.encode(), f"{h}.{body}".encode(), hashlib.sha256).digest())
    c = SyncPostgrestClient(base_url=URL, headers={})
    c.auth(f"{h}.{body}.{sig}")
    with pytest.raises(svc.OrderError) as e:
        svc.pick_line(c, "00000000-0000-0000-0000-000000000000", 1, "2026-10-25", "B-1")
    assert "התחברו מחדש" in str(e.value)
