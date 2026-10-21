"""The legacy pages (receive Santaf, issue Santaf, issue LED) run the real app.py but now change stock
only through the atomic database functions. The functions are EMULATED here (tests/fake_supabase.py);
their real SQL behaviour is proven in tests/integration against Postgres."""
import os
import sys
import time

import pytest
import streamlit as st
import supabase
from streamlit.testing.v1 import AppTest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from fake_supabase import FakeClient  # noqa: E402
import order_service as svc  # noqa: E402

APP = os.path.join(os.path.dirname(HERE), "app.py")
LENGTHS = [1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000, 5500, 6000, 6500, 7000, 7500, 8000]


@pytest.fixture(autouse=True)
def _fresh_caches():
    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


def make_db():
    return FakeClient(
        santaf_inventory=[{"id": i, "length": l, "quantity": 20 if l == 2000 else 0, "min_quantity": 5}
                          for i, l in enumerate(LENGTHS, 1)],
        santaf_movements=[],
        led_inventory=[{"id": 1, "order_number": "OLD-1", "led_type": "LED 3 מ'", "quantity": 5,
                        "notes": "", "created_at": "2026-05-01T10:00:00"}],
        order_lines=[],
    )


def app_on(monkeypatch, db, page):
    monkeypatch.setattr(supabase, "create_client", lambda *a, **k: db)
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["auth"] = {"access_token": "fake-access-worker@example.test",
                                "refresh_token": "fake-refresh-worker@example.test",
                                "expires_at": time.time() + 3000, "email": "worker@example.test"}
    at.run()
    at.session_state["current_page"] = page
    at.run()
    assert not at.exception
    return at


def w(at, kind, key):
    return next(x for x in getattr(at, kind) if x.key == key)


def stock(db, length):
    return next(r["quantity"] for r in db.tables["santaf_inventory"] if r["length"] == length)


# ---------------- legacy "קליטת סנטפים"
def test_legacy_receive_uses_atomic_function_and_refuses_double_submit(monkeypatch):
    db = make_db()
    at = app_on(monkeypatch, db, "🟫 קליטת סנטפים")
    w(at, "text_input", "santaf_receive_ref").set_value("S-1")
    w(at, "number_input", "santaf_receive_qty_2000").set_value(5)
    w(at, "button", "btn_santaf_receive").click()
    at.run()
    assert not at.exception and stock(db, 2000) == 25
    assert [m["type"] for m in db.tables["santaf_movements"]] == ["IN"]
    assert ("rpc", "santaf_receive_stock") in db.calls
    assert not any(c == ("santaf_inventory", "update") for c in db.calls)      # no read-modify-write
    # same click again (double click / retry) must not add stock twice
    w(at, "text_input", "santaf_receive_ref").set_value("S-1")
    w(at, "number_input", "santaf_receive_qty_2000").set_value(5)
    w(at, "button", "btn_santaf_receive").click()
    at.run()
    assert stock(db, 2000) == 25 and any("פעולה זהה" in e.value for e in at.error)


def test_legacy_receive_with_no_quantity_changes_nothing(monkeypatch):
    db = make_db()
    at = app_on(monkeypatch, db, "🟫 קליטת סנטפים")
    w(at, "text_input", "santaf_receive_ref").set_value("S-2")
    w(at, "button", "btn_santaf_receive").click()
    at.run()
    assert stock(db, 2000) == 20 and db.tables["santaf_movements"] == []


# ---------------- legacy "ניפוק סנטפים"
def test_legacy_issue_deducts_once_through_one_atomic_operation(monkeypatch):
    db = make_db()
    at = app_on(monkeypatch, db, "🟫 ניפוק סנטפים")
    w(at, "text_input", "santaf_issue_order").set_value("555")
    w(at, "number_input", "santaf_issue_qty").set_value(3)
    w(at, "selectbox", "santaf_issue_length").set_value(2000)
    w(at, "button", "btn_santaf_issue").click()
    at.run()
    assert not at.exception and stock(db, 2000) == 17
    line = db.tables["order_lines"][0]
    assert (line["customer_order_number"], line["quantity"], line["manual_issue"]) == ("555", 3, True)
    assert [m["type"] for m in db.tables["santaf_movements"]] == ["PICK", "OUT"]
    assert not any(c in (("santaf_inventory", "update"), ("santaf_movements", "insert")) for c in db.calls)
    # legacy consumption report keeps working: it reads OUT movements only
    assert [m["quantity"] for m in db.tables["santaf_movements"] if m["type"] == "OUT"] == [3]


def test_legacy_issue_cannot_double_deduct_an_order_planned_in_the_new_workflow(monkeypatch):
    db = make_db()
    planned = svc.create_line(db, svc.build_line_payload("777", "SANTAF", 4, 2000, supply="stock",
                                                         santaf_lengths=LENGTHS))
    at = app_on(monkeypatch, db, "🟫 ניפוק סנטפים")
    w(at, "text_input", "santaf_issue_order").set_value("777")
    w(at, "number_input", "santaf_issue_qty").set_value(4)
    w(at, "selectbox", "santaf_issue_length").set_value(2000)
    w(at, "button", "btn_santaf_issue").click()
    at.run()
    assert any("בדף ההזמנות" in e.value for e in at.error)
    assert stock(db, 2000) == 20 and planned["picked_qty"] == 0
    assert len(db.tables["order_lines"]) == 1


def test_legacy_issue_insufficient_stock_and_missing_order_number(monkeypatch):
    db = make_db()
    at = app_on(monkeypatch, db, "🟫 ניפוק סנטפים")
    w(at, "button", "btn_santaf_issue").click()
    at.run()
    assert any("מספר הזמנת פרגולה" in e.value for e in at.error)
    w(at, "text_input", "santaf_issue_order").set_value("888")
    w(at, "number_input", "santaf_issue_qty").set_value(21)
    w(at, "selectbox", "santaf_issue_length").set_value(2000)
    w(at, "button", "btn_santaf_issue").click()
    at.run()
    assert stock(db, 2000) == 20 and db.tables["order_lines"] == []


# ---------------- legacy "ניפוק לדים"
def test_legacy_led_issue_is_atomic_and_never_negative(monkeypatch):
    db = make_db()
    at = app_on(monkeypatch, db, "💡 ניפוק לדים")
    w(at, "text_input", "led_issue_pergola").set_value("999")
    w(at, "number_input", "led_issue_qty").set_value(2)
    w(at, "button", "btn_led_issue").click()
    at.run()
    assert not at.exception and db.tables["led_inventory"][0]["quantity"] == 3
    assert ("rpc", "led_issue_stock") in db.calls and ("led_inventory", "update") not in db.calls
    w(at, "number_input", "led_issue_qty").set_value(9)                       # more than the 3 left
    w(at, "button", "btn_led_issue").click()
    at.run()
    assert db.tables["led_inventory"][0]["quantity"] == 3


def test_dashboard_and_reports_still_render_after_the_changes(monkeypatch):
    db = make_db()
    for page in ("🏠 דשבורד", "💡 מלאי לדים", "🟫 מלאי סנטפים", "⚙️ מינימום סנטפים", "📋 דוחות"):
        at = app_on(monkeypatch, db, page)
        assert not at.exception, page
        assert not any("Traceback" in str(e.value) for e in at.error), page
