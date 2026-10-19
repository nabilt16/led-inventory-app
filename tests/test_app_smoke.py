"""Runs the real app.py with supabase.create_client replaced by an in-memory fake.
Never contacts the network or the production database."""
import os
import sys
import time

import pytest
import streamlit as st
import supabase
from streamlit.testing.v1 import AppTest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fake_supabase import FakeClient  # noqa: E402

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
        santaf_inventory=[{"id": i, "length": l, "quantity": 0, "min_quantity": 20}
                          for i, l in enumerate(LENGTHS, 1)],
        santaf_movements=[], led_inventory=[], order_lines=[],
    )


def logged_in_app(monkeypatch, db):
    monkeypatch.setattr(supabase, "create_client", lambda *a, **k: db)
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["auth"] = {"access_token": "fake-access-worker@example.test",
                                "refresh_token": "fake-refresh-worker@example.test",
                                "expires_at": time.time() + 3000, "email": "worker@example.test"}
    return at


def test_not_logged_in_shows_login_and_makes_no_database_request(monkeypatch):
    db = make_db()
    monkeypatch.setattr(supabase, "create_client", lambda *a, **k: db)
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception
    assert [t.key for t in at.text_input] == ["login_email", "login_password"]
    assert db.calls == []                       # nothing touched the database
    assert not any(r.key == "desktop_nav" for r in at.radio)   # no app content


def test_wrong_password_rejected_correct_password_enters_app(monkeypatch):
    db = make_db()
    monkeypatch.setattr(supabase, "create_client", lambda *a, **k: db)
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.text_input(key="login_email").set_value("worker@example.test")
    at.text_input(key="login_password").set_value("wrong")
    at.button[0].click()
    at.run()
    assert any("ההתחברות נכשלה" in e.value for e in at.error)
    assert "auth" not in at.session_state and not any(c[0] == "santaf_inventory" for c in db.calls)
    at.text_input(key="login_password").set_value("correct-password")
    at.button[0].click()
    at.run()
    assert not at.exception
    assert any(r.key == "desktop_nav" for r in at.radio)
    assert db.token == "fake-access-worker@example.test"     # user token attached to requests


def test_expiring_session_is_refreshed(monkeypatch):
    db = make_db()
    at = logged_in_app(monkeypatch, db)
    at.session_state["auth"]["expires_at"] = time.time() + 30
    at.run()
    assert not at.exception
    assert ("auth", "refresh") in db.calls
    assert at.session_state["auth"]["expires_at"] > time.time() + 1000


def test_failed_refresh_sends_user_back_to_login(monkeypatch):
    db = make_db()
    at = logged_in_app(monkeypatch, db)
    at.session_state["auth"]["expires_at"] = time.time() + 30
    at.session_state["auth"]["refresh_token"] = "garbage"
    at.run()
    assert [t.key for t in at.text_input] == ["login_email", "login_password"]
    assert not any(c[0] == "santaf_inventory" for c in db.calls)


def test_orders_page_is_reachable_in_full_app(monkeypatch):
    db = make_db()
    at = logged_in_app(monkeypatch, db)
    at.run()
    assert not at.exception
    at.session_state["current_page"] = "📝 הזמנות"
    at.run()
    assert not at.exception
    assert any("הזמנות" in m.value for m in at.markdown)
    nav = next(r for r in at.radio if r.key == "desktop_nav")
    assert "📝 הזמנות" in nav.options


def test_connection_failure_shows_friendly_error_not_traceback(monkeypatch):
    db = make_db()
    db.fail_on[("santaf_inventory", "select")] = [1] * 5
    at = logged_in_app(monkeypatch, db)
    at.run()
    assert not at.exception                 # no raw traceback
    assert any("לא ניתן להתחבר" in e.value for e in at.error)
