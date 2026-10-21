"""Supabase Auth login gate.

The app used to talk to the database with the public "anon" key, which meant
anyone who could reach the app (or the key) could write. With this gate every
database request carries a logged-in user's token (role "authenticated"); the
RLS policies in migrations/ allow only that role. Tokens live in the server-side
Streamlit session only.
"""
import time

import streamlit as st

SESSION_KEY = "auth"
REFRESH_MARGIN_SECONDS = 120


def _store(session, email):
    st.session_state[SESSION_KEY] = {
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
        "expires_at": session.expires_at or (time.time() + 3000),
        "email": email,
    }


def _is_approved(client, access_token):
    """Ask the database (not the UI) whether this account is on the approved list.
    Fails closed: any error means "not approved"."""
    try:
        client.postgrest.auth(access_token)
        return client.rpc("is_approved_user", {}).execute().data is True
    except Exception:
        return False


def _login_form(client):
    st.markdown(
        """<div style="max-width:420px;margin:40px auto 0;text-align:center">
        <div style="font-size:42px">📦</div>
        <h2 style="margin:6px 0 2px">ניהול מלאי</h2>
        <p style="color:#667;margin:0 0 12px">יש להתחבר כדי להמשיך</p></div>""",
        unsafe_allow_html=True,
    )
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        with st.form("login_form"):
            email = st.text_input("אימייל", key="login_email")
            password = st.text_input("סיסמה", type="password", key="login_password")
            submitted = st.form_submit_button("כניסה", type="primary", use_container_width=True)
    if not submitted:
        return
    if not email.strip() or not password:
        mid.error("יש להזין אימייל וסיסמה.")
        return
    try:
        res = client.auth.sign_in_with_password({"email": email.strip(), "password": password})
        session = res.session
    except Exception:
        # Same message for wrong password / unknown user / network: do not leak which.
        mid.error("ההתחברות נכשלה: אימייל או סיסמה שגויים, או שאין חיבור לרשת.")
        return
    if session is None:
        mid.error("ההתחברות נכשלה.")
        return
    if not _is_approved(client, session.access_token):
        try:
            client.auth.sign_out()
        except Exception:
            pass
        mid.error("החשבון אינו מאושר לשימוש במערכת (או שאי אפשר לאמת הרשאה כרגע). פנו למנהל המערכת.")
        return
    _store(session, email.strip())
    st.rerun()


def require_login(client):
    """Block the page (st.stop) until logged in; attach the user's token to `client`.

    Returns the logged-in user's email.
    """
    auth = st.session_state.get(SESSION_KEY)
    if auth and auth["expires_at"] - time.time() < REFRESH_MARGIN_SECONDS:
        try:
            res = client.auth.refresh_session(auth["refresh_token"])
            _store(res.session, auth["email"])
            auth = st.session_state[SESSION_KEY]
        except Exception:
            st.session_state.pop(SESSION_KEY, None)
            auth = None
            st.warning("ההתחברות פגה. יש להתחבר מחדש.")
    if not auth:
        _login_form(client)
        st.stop()
    client.postgrest.auth(auth["access_token"])
    return auth["email"]


def logout_button(client):
    # The sidebar is dark and Streamlit's default secondary button is near-white,
    # which made the label unreadable; give this button explicit contrast.
    st.markdown(
        "<style>[data-testid='stSidebar'] .stButton > button {"
        "background:#2e4080 !important; border:1px solid #5a6aa8 !important;}"
        "[data-testid='stSidebar'] .stButton > button * {color:#ffffff !important;}</style>",
        unsafe_allow_html=True,
    )
    if st.button("🚪 התנתק", key="btn_logout"):
        try:
            client.auth.sign_out()
        except Exception:
            pass
        st.session_state.pop(SESSION_KEY, None)
        st.rerun()
