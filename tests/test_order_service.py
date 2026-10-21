import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import order_service as svc  # noqa: E402
from fake_supabase import FakeClient  # noqa: E402

LENGTHS = [1500, 2000, 3000, 4500]


def make_db(stock=None):
    stock = stock or {1500: 0, 2000: 100, 3000: 20, 4500: 4}
    return FakeClient(
        santaf_inventory=[{"id": i, "length": l, "quantity": q, "min_quantity": 20}
                          for i, (l, q) in enumerate(stock.items(), 1)],
        santaf_movements=[], led_inventory=[{"id": 1, "order_number": "OLD", "quantity": 5}], order_lines=[],
    )


def stock_of(db, length):
    return next(r["quantity"] for r in db.tables["santaf_inventory"] if r["length"] == length)


def led(db, order="844122", length=1500, qty=2, package_no=1, **extra):
    args = dict(customer_order_number=order, category="LED", quantity=qty, length_mm=length,
                item_spec="LED חם", package_no=package_no)
    args.update(extra)
    return svc.create_line(db, svc.build_line_payload(**args))


def santaf(db, order="844122", length=2000, qty=4, supply="stock", **extra):
    args = dict(customer_order_number=order, category="SANTAF", quantity=qty, length_mm=length,
                supply=supply, santaf_lengths=LENGTHS if supply == "stock" else ())
    args.update(extra)
    return svc.create_line(db, svc.build_line_payload(**args))


def rows(db):
    return db.tables["order_lines"]


# ======================= status vocabulary / form fields =======================
def test_exactly_four_supplier_statuses_with_required_hebrew_labels():
    assert svc.STATUS_VALUES == [None, "ordered", "pending", "received"]
    assert svc.STATUS_LABELS == {None: "לא הוזמן", "ordered": "הוזמן", "pending": "בהמתנה", "received": "התקבל"}


def test_ordered_by_is_gone_from_the_payload_and_the_form():
    p = svc.build_line_payload("844122", "LED", 1, 1500, item_spec="LED")
    assert "ordered_by" not in p
    with pytest.raises(TypeError):
        svc.build_line_payload("844122", "LED", 1, 1500, item_spec="LED", ordered_by="x")
    assert "ordered_by" not in open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "orders_page.py"),
                                    encoding="utf-8").read()


# ======================= validation =======================
@pytest.mark.parametrize("over", [
    dict(customer_order_number="  "), dict(category="X"), dict(quantity=0), dict(quantity=-1),
    dict(quantity="abc"), dict(length_mm=None), dict(length_mm=0), dict(supplier_status="done"),
    dict(supply="supplier", supplier_status="received", santaf_lengths=()),   # receipt date required
    dict(supply=None),                                         # santaf needs a chosen source
    dict(supply="stock", color="אדום"),                        # colored santaf is not standard stock
    dict(supply="stock", length_mm=9000),                      # not one of the fixed lengths
])
def test_invalid_santaf_input_rejected(over):
    args = dict(customer_order_number="844122", category="SANTAF", quantity=4, length_mm=2000,
                supply="stock", santaf_lengths=LENGTHS)
    args.update(over)
    with pytest.raises(svc.OrderError):
        svc.build_line_payload(**args)


def test_led_requires_type_and_is_always_supplier_ordered():
    with pytest.raises(svc.OrderError):
        svc.build_line_payload("1", "LED", 1, 1500)
    p = svc.build_line_payload("1", "LED", 1, 1500, item_spec="LED", supply="stock", color="אדום")
    assert p["supply"] == "supplier" and p["color"] is None


def test_stock_source_forces_standard_color_and_no_supplier_status():
    p = svc.build_line_payload("1", "SANTAF", 4, 2000, supply="stock", supplier_status="ordered",
                               santaf_lengths=LENGTHS)
    assert p["supplier_status"] is None and p["color"] == "שקוף" and p["item_spec"] == "סנטף BH"


def test_customer_specific_santaf_keeps_its_own_spec_outside_the_14_lengths():
    p = svc.build_line_payload("1", "SANTAF", 25, 9000, supply="supplier", color="אדום",
                               supplier_status="ordered", santaf_lengths=LENGTHS)
    assert (p["length_mm"], p["color"], p["supply"], p["quantity"]) == (9000, "אדום", "supplier", 25)


def test_received_needs_date_and_other_statuses_drop_receipt_data():
    p = svc.build_line_payload("1", "LED", 1, 1500, item_spec="L", supplier_status="received",
                               received_date="2026-10-20", storage_location=" A-03 ")
    assert p["received_date"] == "2026-10-20" and p["storage_location"] == "A-03"
    p = svc.build_line_payload("1", "LED", 1, 1500, item_spec="L", supplier_status="pending",
                               received_date="2026-10-20", storage_location="A-03")
    assert p["received_date"] is None and p["storage_location"] is None


# ======================= registering lines never changes stock =======================
def test_registering_and_status_changes_never_change_stock():
    db = make_db()
    led(db)
    s1 = santaf(db)
    custom = santaf(db, length=9000, qty=25, supply="supplier", color="אדום", supplier_status="ordered")
    svc.update_supplier_info(db, custom, "received", "S-1", "2026-10-19", "2026-10-20", "B-02")
    assert [r["quantity"] for r in db.tables["santaf_inventory"]] == [0, 100, 20, 4]
    assert db.tables["led_inventory"] == [{"id": 1, "order_number": "OLD", "quantity": 5}]
    assert db.tables["santaf_movements"] == []
    assert s1["picked_qty"] == 0


def test_multiple_lines_per_order_both_categories_are_kept_separate():
    db = make_db()
    led(db, length=1500, qty=2)
    led(db, length=3000, qty=1)
    santaf(db, length=2000, qty=4)
    santaf(db, length=3000, qty=2)
    found = svc.search_lines(db, order_number="844122")
    assert len(found) == 4
    assert sorted((r["category"], r["length_mm"], r["quantity"]) for r in found) == [
        ("LED", 1500, 2), ("LED", 3000, 1), ("SANTAF", 2000, 4), ("SANTAF", 3000, 2)]
    assert svc.search_lines(db, order_number="999") == []


def test_search_filters_status_category_and_coordination_date():
    db = make_db()
    led(db, order="A1", coordination_date="2026-10-25")
    santaf(db, order="A2", supply="supplier", color="אדום", length=9000, supplier_status="pending")
    santaf(db, order="A3")
    assert len(svc.search_lines(db, status="none")) == 2          # לא הוזמן (NULL): led + stock line
    assert len(svc.search_lines(db, status="pending")) == 1
    assert len(svc.search_lines(db, category="LED")) == 1
    assert [r["customer_order_number"] for r in svc.search_lines(db, coordination_date="2026-10-25")] == ["A1"]


# ======================= packages and order-level inheritance =======================
def test_new_led_line_joins_its_package_and_inherits_status():
    db = make_db()
    a = led(db, length=1500, supplier_status="received", received_date="2026-10-20",
            storage_location="A-03", supplier_ref="S-5")
    b = led(db, length=3000, qty=1, supplier_status=None)        # same package 1: form status ignored
    assert (b["supplier_status"], b["received_date"], b["storage_location"], b["supplier_ref"]) == \
        ("received", "2026-10-20", "A-03", "S-5")
    c = led(db, length=3000, qty=1, package_no=2)                 # a different package stays independent
    assert c["supplier_status"] is None and c["storage_location"] is None and a["package_no"] == 1


def test_cannot_add_a_line_to_an_issued_package():
    db = make_db()
    a = led(db, supplier_status="received", received_date="2026-10-20")
    svc.issue_lines(db, [a["id"]], "2026-10-25", "הלקוח")
    with pytest.raises(svc.OrderError):
        led(db, length=3000)


def test_line_without_coordination_date_inherits_the_orders_date():
    db = make_db()
    led(db, coordination_date="2026-10-25")
    s = santaf(db)
    assert s["coordination_date"] == "2026-10-25"
    other = santaf(db, order="OTHER")
    assert other["coordination_date"] is None


# ======================= updating =======================
def test_package_update_changes_all_lines_of_that_package_only():
    db = make_db()
    a, b = led(db, length=1500), led(db, length=3000)
    c = led(db, length=3000, package_no=2)
    svc.update_supplier_info(db, a, "received", "S-1", "2026-10-19", "2026-10-20", "A-03")
    by = {r["id"]: r for r in rows(db)}
    assert by[a["id"]]["supplier_status"] == by[b["id"]]["supplier_status"] == "received"
    assert by[b["id"]]["storage_location"] == "A-03"
    assert by[c["id"]]["supplier_status"] is None


def test_received_requires_date_and_going_back_clears_receipt():
    db = make_db()
    a = led(db)
    with pytest.raises(svc.OrderError):
        svc.update_supplier_info(db, a, "received", received_date=None)
    svc.update_supplier_info(db, a, "received", received_date="2026-10-20", storage_location="A-03")
    svc.update_supplier_info(db, rows(db)[0], "pending")
    r = rows(db)[0]
    assert r["supplier_status"] == "pending" and r["received_date"] is None and r["storage_location"] is None


def test_stock_lines_have_no_supplier_status():
    db = make_db()
    s = santaf(db)
    with pytest.raises(svc.OrderError):
        svc.update_supplier_info(db, s, "ordered")


def test_set_location_rules():
    db = make_db()
    a = led(db)
    with pytest.raises(svc.OrderError):                        # not received -> no storage location yet
        svc.set_location(db, a, "storage_location", "A-03")
    svc.set_location(db, a, "staging_location", "A-11")
    assert rows(db)[0]["staging_location"] == "A-11"
    with pytest.raises(svc.OrderError):
        svc.set_location(db, a, "staging_location", "  ")
    with pytest.raises(svc.OrderError):
        svc.set_location(db, a, "color", "x")


def test_changing_coordination_date_moves_nothing():
    db = make_db()
    a = led(db, supplier_status="received", received_date="2026-10-20", storage_location="A-03",
            coordination_date="2026-10-25")
    svc.set_location(db, a, "staging_location", "A-11")
    svc.set_coordination_date(db, "844122", "2026-11-02")
    r = rows(db)[0]
    assert r["coordination_date"] == "2026-11-02"
    assert (r["storage_location"], r["staging_location"]) == ("A-03", "A-11")   # location untouched


def test_delete_only_without_stock_effect():
    db = make_db()
    free = santaf(db)
    picked = santaf(db, length=3000, qty=2)
    svc.pick_line(db, picked["id"], 2, "2026-10-25", "B-20")
    svc.delete_line(db, free["id"])
    with pytest.raises(svc.OrderError):
        svc.delete_line(db, picked["id"])
    assert [r["id"] for r in rows(db)] == [picked["id"]]


# ======================= stock accounting: pick -> issue =======================
def test_pick_then_issue_100_88_88():
    db = make_db()
    line = santaf(db, length=2000, qty=12)
    svc.pick_line(db, line["id"], 12, "2026-10-25", "B-20")
    assert stock_of(db, 2000) == 88                                    # available
    assert rows(db)[0]["picked_qty"] == 12 and rows(db)[0]["staging_location"] == "B-20"   # staged
    assert stock_of(db, 2000) + rows(db)[0]["picked_qty"] == 100       # all still in the warehouse
    svc.issue_lines(db, [line["id"]], "2026-10-25", "דוד")
    assert stock_of(db, 2000) == 88                                    # not deducted twice
    assert [m["type"] for m in db.tables["santaf_movements"]] == ["PICK", "OUT"]
    assert rows(db)[0]["issued_on"] == "2026-10-25" and rows(db)[0]["recipient"] == "דוד"
    with pytest.raises(svc.OrderError):
        svc.issue_lines(db, [line["id"]], "2026-10-25", "דוד")         # no duplicate issuance
    with pytest.raises(svc.OrderError):
        svc.pick_line(db, line["id"], 1, "2026-10-25", "B-20")
    assert stock_of(db, 2000) == 88 and len(db.tables["santaf_movements"]) == 2


def test_insufficient_stock_is_refused_per_length_and_changes_nothing():
    db = make_db()
    line = santaf(db, length=4500, qty=9)
    with pytest.raises(svc.OrderError) as e:
        svc.pick_line(db, line["id"], 9, "2026-10-25", "B-20")
    assert "אין מספיק מלאי" in str(e.value)
    assert stock_of(db, 4500) == 4 and rows(db)[0]["picked_qty"] == 0 and db.tables["santaf_movements"] == []
    svc.pick_line(db, line["id"], 4, "2026-10-25", "B-20")             # partial pick of what exists
    assert stock_of(db, 4500) == 0
    with pytest.raises(svc.OrderError):
        svc.issue_lines(db, [line["id"]], "2026-10-25", "x")           # not fully picked


def test_unpick_returns_stock_once():
    db = make_db()
    line = santaf(db, length=3000, qty=5)
    svc.pick_line(db, line["id"], 5, "2026-10-25", "B-20")
    svc.unpick_line(db, line["id"], "2026-10-25")
    assert stock_of(db, 3000) == 20 and rows(db)[0]["picked_qty"] == 0
    with pytest.raises(svc.OrderError):
        svc.unpick_line(db, line["id"], "2026-10-25")
    assert stock_of(db, 3000) == 20


def test_pick_requires_staging_location_and_positive_qty():
    db = make_db()
    line = santaf(db)
    for qty, loc in [(0, "B-20"), (2, "  "), ("x", "B-20")]:
        with pytest.raises(svc.OrderError):
            svc.pick_line(db, line["id"], qty, "2026-10-25", loc)
    assert stock_of(db, 2000) == 100


def test_failure_mid_pick_rolls_everything_back():
    db = make_db()
    line = santaf(db, qty=6)
    db.fail_on[("santaf_movements", "insert")] = [1]
    with pytest.raises(svc.OrderError):
        svc.pick_line(db, line["id"], 6, "2026-10-25", "B-20")
    assert stock_of(db, 2000) == 100 and rows(db)[0]["picked_qty"] == 0 and db.tables["santaf_movements"] == []


def test_connection_error_is_friendly_and_leaks_nothing():
    db = make_db()
    line = santaf(db)
    db.fail_on[("rpc", "pick_stock_line")] = [1]
    with pytest.raises(svc.OrderError) as e:
        svc.pick_line(db, line["id"], 2, "2026-10-25", "B-20")
    assert "connection reset" not in str(e.value) and "לא בוצע שינוי" in str(e.value)
    assert stock_of(db, 2000) == 100


@pytest.mark.parametrize("raw", ["JWT expired", "{'code': 'PGRST301', 'message': 'JWT expired'}",
                                 "permission denied for table x"])
def test_expired_login_maps_to_relogin_message(raw):
    assert "התחברו מחדש" in svc.friendly_error(Exception(raw))


def test_constraint_violations_get_hebrew_messages():
    assert "תאריך קבלה" in svc.friendly_error(Exception('violates check constraint "ol_received_needs_date"'))


# ======================= LED packages and customer-specific Santaf: issue =======================
def test_led_package_is_issued_as_one_unit_and_needs_receipt():
    db = make_db()
    a, b = led(db, length=1500), led(db, length=3000)
    ids = svc.issue_target_ids(db, a)
    assert sorted(ids) == sorted([a["id"], b["id"]])
    with pytest.raises(svc.OrderError):                        # not received yet
        svc.issue_lines(db, ids, "2026-10-25", "הלקוח")
    svc.update_supplier_info(db, a, "received", received_date="2026-10-20", storage_location="A-03")
    svc.issue_lines(db, ids, "2026-10-25", "הלקוח")
    assert all(r["issued_on"] == "2026-10-25" and r["recipient"] == "הלקוח" for r in rows(db))
    assert stock_of(db, 2000) == 100 and db.tables["santaf_movements"] == []   # LED never touches santaf stock
    with pytest.raises(svc.OrderError):                        # issued lines are frozen
        svc.update_supplier_info(db, rows(db)[0], "pending")


def test_issue_requires_recipient():
    db = make_db()
    a = led(db, supplier_status="received", received_date="2026-10-20")
    with pytest.raises(svc.OrderError):
        svc.issue_lines(db, [a["id"]], "2026-10-25", "  ")
    assert rows(db)[0]["issued_on"] is None


def test_customer_specific_santaf_never_touches_general_stock():
    db = make_db()
    c = santaf(db, length=9000, qty=25, supply="supplier", color="אדום", supplier_status="ordered")
    svc.update_supplier_info(db, c, "received", "S-2", "2026-10-18", "2026-10-20", "B-02")
    svc.set_location(db, rows(db)[0], "staging_location", "B-20")
    svc.issue_lines(db, [c["id"]], "2026-10-25", "הלקוח")
    assert [r["quantity"] for r in db.tables["santaf_inventory"]] == [0, 100, 20, 4]
    assert db.tables["santaf_movements"] == []


# ======================= daily preparation =======================
def test_daily_summary_checks_stock_per_length_across_orders_of_the_day():
    db = make_db({1500: 0, 2000: 10, 3000: 20, 4500: 4})
    santaf(db, order="A", length=2000, qty=6, coordination_date="2026-10-25")
    santaf(db, order="B", length=2000, qty=6, coordination_date="2026-10-25")     # together 12 > 10
    santaf(db, order="B", length=3000, qty=2, coordination_date="2026-10-25")
    led(db, order="A", coordination_date="2026-10-25")
    stock = svc.stock_levels(db)
    s = svc.daily_summary(svc.search_lines(db, coordination_date="2026-10-25"), stock)
    assert s["shortages"] == [{"length": 2000, "required": 12, "available": 10, "shortfall": 2}]
    by = {o["order"]: o for o in s["orders"]}
    assert by["A"]["status"] == "missing" and by["B"]["status"] == "missing"
    assert s["counts"] == {"missing": 2, "ready": 0, "issued": 0}


def test_daily_summary_order_ready_only_when_every_component_is_ready():
    db = make_db()
    l = led(db, order="A", coordination_date="2026-10-25", supplier_status="received",
            received_date="2026-10-20", storage_location="A-03")
    s1 = santaf(db, order="A", length=2000, qty=4)
    stock = svc.stock_levels(db)
    ready_led_only = svc.daily_summary(svc.search_lines(db, order_number="A"), stock)
    assert ready_led_only["orders"][0]["status"] == "missing"          # santaf not picked yet
    svc.pick_line(db, s1["id"], 4, "2026-10-25", "B-20")
    both = svc.daily_summary(svc.search_lines(db, order_number="A"), svc.stock_levels(db))
    assert both["orders"][0]["status"] == "ready" and both["shortages"] == []
    svc.issue_lines(db, [l["id"], s1["id"]], "2026-10-25", "הלקוח")
    done = svc.daily_summary(svc.search_lines(db, order_number="A"), svc.stock_levels(db))
    assert done["orders"][0]["status"] == "issued"


def test_line_state_labels():
    base = dict(supply="supplier", quantity=1, picked_qty=0, issued_on=None, supplier_status=None, length_mm=1)
    assert svc.line_state(base) == ("missing", "לא התקבל: לא הוזמן")
    assert svc.line_state({**base, "supplier_status": "pending"}) == ("missing", "לא התקבל: בהמתנה")
    assert svc.line_state({**base, "supplier_status": "received"})[0] == "ready"
    assert svc.line_state({**base, "issued_on": "2026-10-25"})[0] == "issued"
    stock = {**base, "supply": "stock", "quantity": 3}
    assert svc.line_state(stock)[1] == "ממתין לאיסוף"
    assert svc.line_state(stock, {1: {}})[1] == "חסר במלאי"
    assert svc.line_state({**stock, "picked_qty": 3})[0] == "ready"


# ======================= unified stock operations (legacy pages use the same functions) =======================
def test_standard_receive_adds_stock_and_refuses_an_identical_repeat():
    db = make_db()
    assert svc.receive_standard_santaf(db, {2000: 5, 3000: 2}, "2026-10-21") == 7
    assert stock_of(db, 2000) == 105 and stock_of(db, 3000) == 22
    with pytest.raises(svc.OrderError) as e:
        svc.receive_standard_santaf(db, {2000: 5, 3000: 2}, "2026-10-21")
    assert "פעולה זהה" in str(e.value) and stock_of(db, 2000) == 105
    assert [m["type"] for m in db.tables["santaf_movements"]] == ["IN", "IN"]


@pytest.mark.parametrize("qty", [0, -3, "x"])
def test_standard_receive_rejects_invalid_quantities(qty):
    db = make_db()
    with pytest.raises(svc.OrderError):
        svc.receive_standard_santaf(db, {2000: qty}, "2026-10-21")
    assert stock_of(db, 2000) == 100
    with pytest.raises(svc.OrderError):
        svc.receive_standard_santaf(db, {}, "2026-10-21")


def test_direct_issue_deducts_once_and_is_tied_to_an_order():
    db = make_db()
    line = svc.issue_standard_santaf(db, "LEG-1", 2000, 6, "2026-10-21")
    assert stock_of(db, 2000) == 94 and line["manual_issue"] and line["issued_on"] == "2026-10-21"
    assert [m["type"] for m in db.tables["santaf_movements"]] == ["PICK", "OUT"]
    with pytest.raises(svc.OrderError):
        svc.issue_standard_santaf(db, "LEG-1", 2000, 6, "2026-10-21")     # duplicate submission
    assert stock_of(db, 2000) == 94


def test_direct_issue_and_order_workflow_cannot_deduct_the_same_stock_twice():
    db = make_db()
    planned = santaf(db, order="DBL", length=2000, qty=4)
    with pytest.raises(svc.OrderError) as e:
        svc.issue_standard_santaf(db, "DBL", 2000, 4, "2026-10-21")
    assert "בדף ההזמנות" in str(e.value) and stock_of(db, 2000) == 100
    svc.pick_line(db, planned["id"], 4, "2026-10-21", "B-20")
    with pytest.raises(svc.OrderError):                                  # still planned (picked, not issued)
        svc.issue_standard_santaf(db, "DBL", 2000, 4, "2026-10-22")
    svc.issue_lines(db, [planned["id"]], "2026-10-22", "הלקוח")
    assert stock_of(db, 2000) == 96
    # reverse order: legacy issue first, then a plan for the same order+length cannot be picked
    svc.issue_standard_santaf(db, "DBL2", 3000, 2, "2026-10-21")
    later = santaf(db, order="DBL2", length=3000, qty=2)
    with pytest.raises(svc.OrderError) as e:
        svc.pick_line(db, later["id"], 2, "2026-10-21", "B-20")
    assert "נופק ידנית" in str(e.value) and stock_of(db, 3000) == 18


@pytest.mark.parametrize("order,length,qty", [("", 2000, 1), ("X", 2000, 0), ("X", 2000, -1), ("X", 4500, 5)])
def test_direct_issue_invalid_or_insufficient_changes_nothing(order, length, qty):
    db = make_db()
    with pytest.raises(svc.OrderError):
        svc.issue_standard_santaf(db, order, length, qty, "2026-10-21")
    assert [r["quantity"] for r in db.tables["santaf_inventory"]] == [0, 100, 20, 4]
    assert db.tables["order_lines"] == [] and db.tables["santaf_movements"] == []


def test_identical_repeat_pick_is_refused_but_unpick_allows_a_corrected_repick():
    db = make_db()
    line = santaf(db, length=2000, qty=10)
    svc.pick_line(db, line["id"], 4, "2026-10-21", "B-20")
    with pytest.raises(svc.OrderError) as e:
        svc.pick_line(db, line["id"], 4, "2026-10-21", "B-20")          # double click
    assert "פעולה זהה" in str(e.value) and stock_of(db, 2000) == 96
    svc.unpick_line(db, line["id"])
    svc.pick_line(db, line["id"], 4, "2026-10-21", "B-21")              # deliberate re-pick works
    assert stock_of(db, 2000) == 96


def test_led_stock_issue_atomic_and_never_negative():
    db = make_db()
    assert svc.issue_led_stock(db, 1, 2) == 3
    with pytest.raises(svc.OrderError):
        svc.issue_led_stock(db, 1, 2)                                     # identical repeat
    with pytest.raises(svc.OrderError):
        svc.issue_led_stock(db, 1, 4)                                     # more than the 3 left
    for bad in (0, -1):
        with pytest.raises(svc.OrderError):
            svc.issue_led_stock(db, 1, bad)
    assert db.tables["led_inventory"][0]["quantity"] == 3
