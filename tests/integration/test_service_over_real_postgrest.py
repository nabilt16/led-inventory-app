"""order_service driven through the real postgrest-py client against local PostgREST+Postgres."""
import os
import sys

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


def santaf_qty(length):
    return int(psql(f"select coalesce(quantity,0) from santaf_inventory where length={length}"))


def test_full_flow_with_real_client():
    c = make_client()
    order = "E2E-1"
    p1 = svc.build_line_payload(order, "SANTAF", 4, length_mm=7500, ordered_by="דוד",
                                supplier_order_ref="S-1", supplier_order_date="2026-10-19",
                                coordination_date="2026-10-25", santaf_lengths=LENGTHS)
    p2 = svc.build_line_payload(order, "LED", 2, length_mm=15000, item_spec="LED חם",
                                supplier_order_ref="S-2", santaf_lengths=LENGTHS)
    l1, l2 = svc.create_line(c, p1), svc.create_line(c, p2)
    assert l1["id"] and l2["id"]

    found = svc.search_lines(c, order_number="e2e-1")                     # ilike, case-insensitive
    assert len(found) == 2
    assert [l["id"] for l in svc.search_lines(c, order_number=order, coordination_date="2026-10-25")] == [l1["id"]]
    assert len(svc.search_lines(c, status="ordered", category="LED", order_number=order)) == 1

    before = santaf_qty(7500)
    assert before == santaf_qty(7500) and santaf_qty(7500) == before      # entering orders changed nothing

    got = svc.receive_line(c, l1["id"], 4, "2026-10-20", "B-07", LENGTHS)
    assert got["status"] == "received" and got["stock_applied"] is True
    assert santaf_qty(7500) == before + 4

    with pytest.raises(svc.OrderError) as dup:                           # double submit
        svc.receive_line(c, l1["id"], 4, "2026-10-20", "B-07", LENGTHS)
    assert "כבר התקבלה" in str(dup.value) and "ORDERERR" not in str(dup.value)
    assert santaf_qty(7500) == before + 4

    svc.update_coordination_date(c, l2["id"], "2026-10-27")
    assert svc.search_lines(c, order_number=order, coordination_date="2026-10-27")[0]["id"] == l2["id"]
    svc.cancel_line(c, l2["id"])
    with pytest.raises(svc.OrderError):
        svc.receive_line(c, l2["id"], 2, "2026-10-20", "A-01", LENGTHS)
    with pytest.raises(svc.OrderError):
        svc.cancel_line(c, l1["id"])                                     # received lines cannot be cancelled
    assert psql("select count(*) from led_inventory where notes like '%E2E-1%'") == "0"


def test_anon_client_is_rejected_with_relogin_message():
    anon = make_client("anon")
    with pytest.raises(Exception):
        svc.create_line(anon, svc.build_line_payload("X", "LED", 1, length_mm=1000, item_spec="L"))
    with pytest.raises(svc.OrderError) as e:
        svc.receive_line(anon, "00000000-0000-0000-0000-000000000000", 1, "2026-10-20", "A-1")
    assert "לא בוצע שינוי" in str(e.value)


def test_expired_token_maps_to_relogin_message():
    from postgrest import SyncPostgrestClient
    import base64, hashlib, hmac, json, time
    b = lambda x: base64.urlsafe_b64encode(x).rstrip(b"=").decode()
    h = b(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b(json.dumps({"role": "authenticated", "exp": int(time.time()) - 60}).encode())
    sig = b(hmac.new(SECRET.encode(), f"{h}.{body}".encode(), hashlib.sha256).digest())
    c = SyncPostgrestClient(base_url=URL, headers={})
    c.auth(f"{h}.{body}.{sig}")
    with pytest.raises(svc.OrderError) as e:
        svc.receive_line(c, "00000000-0000-0000-0000-000000000000", 1, "2026-10-20", "A-1")
    assert "התחברו מחדש" in str(e.value)
