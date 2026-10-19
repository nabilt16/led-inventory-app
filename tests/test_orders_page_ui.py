import os

from streamlit.testing.v1 import AppTest

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_harness.py")


def _app():
    at = AppTest.from_file(HARNESS, default_timeout=30)
    at.run()
    assert not at.exception
    return at


def _by_key(at, kind, key):
    return next(w for w in getattr(at, kind) if w.key == key)


def test_add_two_lines_to_same_order_keeps_header_and_does_not_touch_stock():
    at = _app()
    _by_key(at, "text_input", "ol_order_no").set_value("844122")
    _by_key(at, "text_input", "ol_ordered_by").set_value("דוד")
    # santaf line, 2000 mm x 4
    _by_key(at, "radio", "ol_cat_0").set_value("SANTAF")
    at.run()
    _by_key(at, "selectbox", "ol_len_saf_0").set_value(2000)
    _by_key(at, "number_input", "ol_qty_0").set_value(4)
    _by_key(at, "button", "ol_save_0").click()
    at.run()
    assert not at.exception and not at.error
    db = at.session_state["db"]
    assert len(db.tables["order_lines"]) == 1
    # header kept for the next line
    assert _by_key(at, "text_input", "ol_order_no").value == "844122"
    # second line: LED 15 m x 4 on the same order
    _by_key(at, "radio", "ol_cat_1").set_value("LED")
    at.run()
    _by_key(at, "text_input", "ol_spec_1").set_value("LED חם")
    _by_key(at, "number_input", "ol_len_led_1").set_value(15.0)
    _by_key(at, "number_input", "ol_qty_1").set_value(4)
    _by_key(at, "button", "ol_save_1").click()
    at.run()
    assert not at.exception and not at.error
    lines = db.tables["order_lines"]
    assert {l["customer_order_number"] for l in lines} == {"844122"}
    assert sorted((l["category"], l["length_mm"], l["quantity"]) for l in lines) == [
        ("LED", 15000, 4), ("SANTAF", 2000, 4)]
    assert all(l["ordered_by"] == "דוד" and l["status"] == "ordered" for l in lines)
    assert all(r["quantity"] == 0 for r in db.tables["santaf_inventory"])
    assert db.tables["led_inventory"] == []


def test_missing_order_number_shows_error_and_saves_nothing():
    at = _app()
    _by_key(at, "button", "ol_save_0").click()
    at.run()
    assert any("מספר הזמנת פרגולה" in e.value for e in at.error)
    assert at.session_state["db"].tables["order_lines"] == []


def test_receive_flow_adds_stock_once_and_clears_cache():
    at = _app()
    db = at.session_state["db"]
    _by_key(at, "text_input", "ol_order_no").set_value("844123")
    _by_key(at, "radio", "ol_cat_0").set_value("SANTAF")
    at.run()
    _by_key(at, "selectbox", "ol_len_saf_0").set_value(3000)
    _by_key(at, "number_input", "ol_qty_0").set_value(9)
    _by_key(at, "button", "ol_save_0").click()
    at.run()
    lid = db.tables["order_lines"][0]["id"]
    # button disabled until the physical-receipt checkbox is ticked
    assert _by_key(at, "button", f"or_btn_{lid}").disabled
    _by_key(at, "text_input", f"or_loc_{lid}").set_value("B-01")
    _by_key(at, "checkbox", f"or_ok_{lid}").check()
    at.run()
    _by_key(at, "button", f"or_btn_{lid}").click()
    at.run()
    assert not at.exception and not at.error
    saf = {r["length"]: r["quantity"] for r in db.tables["santaf_inventory"]}
    assert saf[3000] == 9 and saf[2000] == 0
    assert at.session_state["cache_cleared"] == 1
    assert db.tables["order_lines"][0]["storage_location"] == "B-01"
