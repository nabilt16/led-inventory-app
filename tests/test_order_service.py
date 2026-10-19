import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import order_service as svc  # noqa: E402
from fake_supabase import FakeClient  # noqa: E402

LENGTHS = [1500, 2000, 3000]


def santaf_db(qty=10):
    return FakeClient(
        santaf_inventory=[
            {"id": 1, "length": 2000, "quantity": qty, "min_quantity": 20},
            {"id": 2, "length": 3000, "quantity": 0, "min_quantity": 20},
        ],
        santaf_movements=[], led_inventory=[], order_lines=[],
    )


def new_line(client, **over):
    args = dict(customer_order_number="844122", category="SANTAF", quantity=4,
                length_mm=2000, santaf_lengths=LENGTHS)
    args.update(over)
    return svc.create_line(client, svc.build_line_payload(**args))


# ---------- validation ----------
@pytest.mark.parametrize("over", [
    dict(customer_order_number="  "), dict(category="X"), dict(quantity=0),
    dict(quantity=-3), dict(quantity="abc"), dict(length_mm=None),
    dict(length_mm=0), dict(length_mm=2222),            # not a known santaf length
])
def test_invalid_santaf_lines_rejected(over):
    args = dict(customer_order_number="844122", category="SANTAF", quantity=4,
                length_mm=2000, santaf_lengths=LENGTHS)
    args.update(over)
    with pytest.raises(svc.OrderError):
        svc.build_line_payload(**args)


def test_led_requires_type():
    with pytest.raises(svc.OrderError):
        svc.build_line_payload("844123", "LED", 4, length_mm=15000)


def test_payload_defaults_and_trimming():
    p = svc.build_line_payload(" 844123 ", "LED", 4, length_mm=15000, item_spec=" LED חם ",
                               ordered_by=" דוד ")
    assert p["customer_order_number"] == "844123"
    assert p["item_spec"] == "LED חם" and p["ordered_by"] == "דוד"
    assert p["status"] == "ordered" and p["stock_applied"] is False


# ---------- registering never touches stock ----------
def test_creating_lines_does_not_change_stock():
    db = santaf_db(10)
    new_line(db)
    new_line(db, length_mm=3000, quantity=2)
    new_line(db, category="LED", item_spec="LED", length_mm=15000, quantity=4)
    assert db.tables["santaf_inventory"][0]["quantity"] == 10
    assert db.tables["santaf_inventory"][1]["quantity"] == 0
    assert db.tables["led_inventory"] == [] and db.tables["santaf_movements"] == []


def test_record_as_received_is_record_only():
    db = santaf_db(10)
    line = new_line(db, record_as_received=True)
    assert line["status"] == "received" and line["stock_applied"] is False
    assert db.tables["santaf_inventory"][0]["quantity"] == 10
    with pytest.raises(svc.OrderError):               # cannot be received again
        svc.receive_line(db, line["id"], 4, "2026-10-20", "A-03", LENGTHS)
    assert db.tables["santaf_inventory"][0]["quantity"] == 10


def test_multiple_lines_same_order_stay_separate():
    db = santaf_db()
    new_line(db, length_mm=2000, quantity=4)
    new_line(db, length_mm=3000, quantity=2)
    new_line(db, category="LED", item_spec="LED", length_mm=3000, quantity=1)
    assert len(svc.search_lines(db, order_number="844122")) == 3
    assert svc.search_lines(db, order_number="999") == []


# ---------- receiving ----------
def test_receive_santaf_adds_stock_once():
    db = santaf_db(10)
    line = new_line(db)
    done = svc.receive_line(db, line["id"], 4, "2026-10-20", "B-01", LENGTHS)
    assert done["status"] == "received" and done["stock_applied"] is True
    assert done["storage_location"] == "B-01" and done["received_qty"] == 4
    assert db.tables["santaf_inventory"][0]["quantity"] == 14
    mv = db.tables["santaf_movements"]
    assert len(mv) == 1 and mv[0]["type"] == "IN" and mv[0]["quantity"] == 4


def test_double_receive_does_not_double_count():
    db = santaf_db(10)
    line = new_line(db)
    svc.receive_line(db, line["id"], 4, "2026-10-20", "B-01", LENGTHS)
    with pytest.raises(svc.OrderError):
        svc.receive_line(db, line["id"], 4, "2026-10-20", "B-01", LENGTHS)
    assert db.tables["santaf_inventory"][0]["quantity"] == 14
    assert len(db.tables["santaf_movements"]) == 1


def test_receive_led_creates_batch_row_with_context():
    db = santaf_db()
    line = new_line(db, category="LED", item_spec="LED חם", length_mm=1500, quantity=2,
                    supplier_order_ref="S-77")
    svc.receive_line(db, line["id"], 2, "2026-10-20", "A-05")
    row = db.tables["led_inventory"][0]
    assert row["order_number"] == "S-77" and row["quantity"] == 2
    assert "1.5" in row["led_type"] and "844122" in row["notes"] and "A-05" in row["notes"]


def test_receive_requires_location_and_positive_qty():
    db = santaf_db()
    line = new_line(db)
    with pytest.raises(svc.OrderError):
        svc.receive_line(db, line["id"], 4, "2026-10-20", "  ", LENGTHS)
    with pytest.raises(svc.OrderError):
        svc.receive_line(db, line["id"], 0, "2026-10-20", "A-03", LENGTHS)
    assert db.tables["order_lines"][0]["status"] == "ordered"


def test_led_stock_failure_releases_claim():
    db = santaf_db()
    line = new_line(db, category="LED", item_spec="LED", length_mm=3000, quantity=1)
    db.fail_on[("led_inventory", "insert")] = [1]
    with pytest.raises(svc.OrderError):
        svc.receive_line(db, line["id"], 1, "2026-10-20", "A-03")
    stored = db.tables["order_lines"][0]
    assert stored["status"] == "ordered" and stored["stock_applied"] is False
    assert db.tables["led_inventory"] == []
    svc.receive_line(db, line["id"], 1, "2026-10-20", "A-03")   # retry works
    assert len(db.tables["led_inventory"]) == 1


def test_santaf_movement_failure_rolls_back_stock_and_claim():
    db = santaf_db(10)
    line = new_line(db)
    db.fail_on[("santaf_movements", "insert")] = [1]
    with pytest.raises(svc.OrderError):
        svc.receive_line(db, line["id"], 4, "2026-10-20", "B-01", LENGTHS)
    assert db.tables["santaf_inventory"][0]["quantity"] == 10
    assert db.tables["order_lines"][0]["status"] == "ordered"


def test_cas_detects_concurrent_change():
    """Another user changes the quantity between our read and our write."""
    db = santaf_db(10)
    orig_table = db.table
    state = {"bumped": False}

    def table(name):
        q = orig_table(name)
        if name == "santaf_inventory":
            real = q.execute

            def execute():
                res = real()
                if q.op == "select" and not state["bumped"]:
                    state["bumped"] = True
                    db.tables["santaf_inventory"][0]["quantity"] = 25  # other user
                return res
            q.execute = execute
        return q
    db.table = table
    assert svc._santaf_adjust(db, 2000, 4) == 29        # retried on the fresh value
    assert db.tables["santaf_inventory"][0]["quantity"] == 29


def test_santaf_adjust_never_goes_negative():
    db = santaf_db(3)
    with pytest.raises(svc.OrderError):
        svc._santaf_adjust(db, 2000, -5)
    assert db.tables["santaf_inventory"][0]["quantity"] == 3


def test_missing_santaf_length_row():
    db = santaf_db()
    with pytest.raises(svc.OrderError):
        svc._santaf_adjust(db, 1500, 1)


# ---------- cancel / coordination ----------
def test_cancel_only_unreceived_and_never_deletes():
    db = santaf_db()
    a, b = new_line(db), new_line(db, length_mm=3000)
    svc.receive_line(db, b["id"], 4, "2026-10-20", "B-01", LENGTHS)
    svc.cancel_line(db, a["id"])
    with pytest.raises(svc.OrderError):
        svc.cancel_line(db, b["id"])
    with pytest.raises(svc.OrderError):
        svc.cancel_line(db, a["id"])
    assert [r["status"] for r in db.tables["order_lines"]] == ["cancelled", "received"]


def test_update_coordination_date_changes_only_that_line():
    db = santaf_db()
    a, b = new_line(db), new_line(db, length_mm=3000)
    svc.update_coordination_date(db, a["id"], "2026-10-25")
    rows = {r["id"]: r for r in db.tables["order_lines"]}
    assert rows[a["id"]]["coordination_date"] == "2026-10-25"
    assert rows[b["id"]].get("coordination_date") is None
    assert len(svc.search_lines(db, coordination_date="2026-10-25")) == 1
