import os

from streamlit.testing.v1 import AppTest

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_harness.py")


def _app():
    at = AppTest.from_file(HARNESS, default_timeout=30)
    at.run()
    assert not at.exception
    return at


def w(at, kind, key):
    return next(x for x in getattr(at, kind) if x.key == key)


def stock(at, length):
    return next(r["quantity"] for r in at.session_state["db"].tables["santaf_inventory"] if r["length"] == length)


def add_led(at, n, length_m, qty, spec="LED חם", package=1):
    w(at, "radio", f"ol_cat_{n}").set_value("LED")
    at.run()
    w(at, "text_input", f"ol_spec_{n}").set_value(spec)
    w(at, "number_input", f"ol_len_led_{n}").set_value(length_m)
    w(at, "number_input", f"ol_qty_{n}").set_value(qty)
    w(at, "number_input", f"ol_pkg_{n}").set_value(package)
    w(at, "button", f"ol_save_{n}").click()
    at.run()
    assert not at.exception and not at.error


def test_four_supplier_statuses_in_hebrew_and_no_ordered_by_field():
    at = _app()
    sel = w(at, "selectbox", "ol_status_0")
    assert list(sel.options) == ["לא הוזמן", "הוזמן", "בהמתנה", "התקבל"]
    labels = [x.label for x in at.text_input] + [x.label for x in at.selectbox]
    assert not any("הוזמן על ידי" in l for l in labels)


def test_order_with_led_and_both_santaf_modes_keeps_header_and_never_touches_stock():
    at = _app()
    w(at, "text_input", "ol_order_no").set_value("844122")
    add_led(at, 0, 1.5, 2)                                                  # LED 1.5 m x 2, package 1
    assert w(at, "text_input", "ol_order_no").value == "844122"             # order kept for the next line
    add_led(at, 1, 3.0, 1)                                                  # LED 3 m x 1, same package
    # santaf from regular stock: 2000 mm x 4
    w(at, "radio", "ol_cat_2").set_value("SANTAF")
    at.run()
    w(at, "radio", "ol_supply_2").set_value("stock")
    at.run()
    w(at, "selectbox", "ol_len_saf_2").set_value(2000)
    w(at, "number_input", "ol_qty_2").set_value(4)
    w(at, "button", "ol_save_2").click()
    at.run()
    assert not at.exception and not at.error
    # santaf ordered specifically for the customer: red, 9000 mm x 25 (outside the 14 lengths)
    w(at, "radio", "ol_cat_3").set_value("SANTAF")
    at.run()
    w(at, "radio", "ol_supply_3").set_value("supplier")
    at.run()
    w(at, "text_input", "ol_color_3").set_value("אדום")
    w(at, "number_input", "ol_len_sup_3").set_value(9000)
    w(at, "number_input", "ol_qty_3").set_value(25)
    w(at, "selectbox", "ol_status_3").set_value("ordered")
    at.run()
    w(at, "button", "ol_save_3").click()
    at.run()
    assert not at.exception and not at.error
    db = at.session_state["db"]
    lines = db.tables["order_lines"]
    assert sorted((l["category"], l["supply"], l["length_mm"], l["quantity"]) for l in lines) == [
        ("LED", "supplier", 1500, 2), ("LED", "supplier", 3000, 1),
        ("SANTAF", "stock", 2000, 4), ("SANTAF", "supplier", 9000, 25)]
    assert {l["package_no"] for l in lines if l["category"] == "LED"} == {1}
    custom = next(l for l in lines if l["length_mm"] == 9000)
    assert (custom["color"], custom["supplier_status"]) == ("אדום", "ordered")
    assert all("ordered_by" not in l for l in lines)
    assert [r["quantity"] for r in db.tables["santaf_inventory"]] == [0, 100, 0, 20, 4]   # untouched
    assert db.tables["led_inventory"] == [] and db.tables["santaf_movements"] == []


def test_missing_order_number_and_invalid_stock_color_are_rejected():
    at = _app()
    w(at, "button", "ol_save_0").click()
    at.run()
    assert any("מספר הזמנת פרגולה" in e.value for e in at.error)
    assert at.session_state["db"].tables["order_lines"] == []


def test_pick_from_ui_deducts_once_and_issue_does_not_deduct_again():
    at = _app()
    w(at, "text_input", "ol_order_no").set_value("844123")
    w(at, "radio", "ol_cat_0").set_value("SANTAF")
    at.run()
    w(at, "radio", "ol_supply_0").set_value("stock")
    at.run()
    w(at, "selectbox", "ol_len_saf_0").set_value(2000)
    w(at, "number_input", "ol_qty_0").set_value(12)
    w(at, "button", "ol_save_0").click()
    at.run()
    db = at.session_state["db"]
    lid = db.tables["order_lines"][0]["id"]
    assert stock(at, 2000) == 100
    # pick (staging location required)
    w(at, "button", f"o_pick_{lid}").click()
    at.run()
    assert any("מיקום הכנה" in e.value for e in at.error) and stock(at, 2000) == 100
    w(at, "text_input", f"o_ps_{lid}").set_value("B-20")
    w(at, "button", f"o_pick_{lid}").click()
    at.run()
    assert not at.exception and stock(at, 2000) == 88
    assert db.tables["order_lines"][0]["picked_qty"] == 12
    assert at.session_state["cache_cleared"] >= 1
    # issue
    w(at, "text_input", f"o_who_{lid}").set_value("דוד")
    w(at, "button", f"o_issue_{lid}").click()
    at.run()
    assert not at.exception and not at.error
    assert stock(at, 2000) == 88                                         # not deducted twice
    assert db.tables["order_lines"][0]["issued_on"] and db.tables["order_lines"][0]["recipient"] == "דוד"
    assert [m["type"] for m in db.tables["santaf_movements"]] == ["PICK", "OUT"]


def test_supplier_status_change_from_ui_updates_package_and_not_stock():
    at = _app()
    w(at, "text_input", "ol_order_no").set_value("844124")
    add_led(at, 0, 1.5, 2)
    add_led(at, 1, 3.0, 1)
    db = at.session_state["db"]
    lid = db.tables["order_lines"][0]["id"]
    w(at, "selectbox", f"o_st_{lid}").set_value("received")
    at.run()
    w(at, "text_input", f"o_sl_{lid}").set_value("A-03")
    w(at, "button", f"o_stsave_{lid}").click()
    at.run()
    assert not at.exception and not at.error
    assert all(l["supplier_status"] == "received" and l["storage_location"] == "A-03"
               for l in db.tables["order_lines"])                         # both lines of the package
    assert [r["quantity"] for r in db.tables["santaf_inventory"]] == [0, 100, 0, 20, 4]
    assert db.tables["santaf_movements"] == [] and db.tables["led_inventory"] == []
