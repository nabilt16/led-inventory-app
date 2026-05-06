import streamlit as st
import pandas as pd
import json
import os
from datetime import date, datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from supabase import create_client

SUPABASE_URL = st.secrets.get("SUPABASE_URL", "https://gwiieqmawtudoxafnjeg.supabase.co")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imd3aWllcW1hd3R1ZG94YWZuamVnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzc4NzkxMDAsImV4cCI6MjA5MzQ1NTEwMH0.v3fxlk30SHNPqyncYB557lLYsk99D50DGsPhJUNkeLk")

SANTAF_TYPE = "סנטף BH שקוף"
SANTAF_LENGTHS = [1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000, 5500, 6000, 6500, 7000, 7500, 8000]
DEFAULT_MIN_SANTAF = 20
MM = 'מ"מ'
EMAIL_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "email_history.json")
SENDER_EMAIL = "ariza1.nabilt@gmail.com"
SENDER_PASSWORD = st.secrets.get("SENDER_PASSWORD", "jsoevoatytetodaa")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def safe_data(response):
    return response.data if response and response.data else []


def short_date(value):
    if not value:
        return "-"
    return str(value).replace("T", " ")[:16]


def load_email_history():
    if os.path.exists(EMAIL_HISTORY_FILE):
        try:
            with open(EMAIL_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_email_to_history(email):
    history = load_email_history()
    if email in history:
        history.remove(email)
    history.insert(0, email)
    with open(EMAIL_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history[:20], f, ensure_ascii=False)


@st.cache_data(ttl=30)
def get_led_rows():
    return safe_data(
        supabase.table("led_inventory")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )


@st.cache_data(ttl=30)
def get_santaf_rows():
    return safe_data(
        supabase.table("santaf_inventory")
        .select("*")
        .order("length")
        .execute()
    )


@st.cache_data(ttl=30)
def get_santaf_row(length):
    data = safe_data(
        supabase.table("santaf_inventory")
        .select("*")
        .eq("length", length)
        .limit(1)
        .execute()
    )
    return data[0] if data else None


@st.cache_resource
def ensure_santaf_lengths():
    rows = get_santaf_rows()
    existing_lengths = {int(r["length"]) for r in rows}
    for length in SANTAF_LENGTHS:
        if length not in existing_lengths:
            supabase.table("santaf_inventory").insert({
                "length": length,
                "quantity": 0,
                "min_quantity": DEFAULT_MIN_SANTAF
            }).execute()


def reset_led_form():
    st.session_state["led_receive_order"] = ""
    st.session_state["led_receive_type"] = ""
    st.session_state["led_receive_qty"] = 1
    st.session_state["led_receive_notes"] = ""


def card(title, lines, warning=False):
    cls = "card warn" if warning else "card"
    html = f"<div class='{cls}'><div class='card-title'>{title}</div>"
    for line in lines:
        html += f"<div class='card-line'>{line}</div>"
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="ניהול לדים וסנטפים — Trellidor",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
/* ── Mobile sidebar ── */
@media (max-width: 768px) {
    /* Hide sidebar by default — initial_sidebar_state handles it */
    [data-testid="stSidebar"][aria-expanded="false"] {
        display: none !important;
    }
    /* Style the collapse/expand toggle button as hamburger */
    [data-testid="collapsedControl"] {
        display: flex !important;
        position: fixed !important;
        top: 10px !important;
        right: 10px !important;
        left: auto !important;
        z-index: 9999 !important;
        background: #1a2340 !important;
        border-radius: 8px !important;
        width: 42px !important;
        height: 42px !important;
        align-items: center !important;
        justify-content: center !important;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3) !important;
    }
    [data-testid="collapsedControl"] svg {
        fill: #ffffff !important;
        width: 20px !important;
        height: 20px !important;
    }
}

/* ── Global ── */
html, body, [class*="css"] {
    font-family: 'Segoe UI', Arial, sans-serif;
    direction: rtl;
}
.main .block-container {
    padding: 1.5rem 2rem 2rem 2rem;
    max-width: 860px;
}
h1, h2, h3, h4 { text-align: right; }

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: #1a2340;
    min-width: 230px;
}
[data-testid="stSidebar"] * { color: #e8eaf0 !important; }
[data-testid="stSidebar"] .stRadio label {
    font-size: 15px !important;
    padding: 6px 0 !important;
}
[data-testid="stSidebar"] hr { border-color: #2e3a5c !important; }

/* ── Page header banner ── */
.page-banner {
    background: linear-gradient(135deg, #1a2340 0%, #2e4080 100%);
    border-radius: 14px;
    padding: 20px 28px 16px 28px;
    margin-bottom: 24px;
    color: #ffffff;
    text-align: right;
}
.page-banner h2 { color: #ffffff; margin: 0; font-size: 22px; }
.page-banner p  { color: #b0bcdc; margin: 4px 0 0; font-size: 13px; }

/* ── Metric cards (dashboard) ── */
.metric-row { display: flex; gap: 14px; margin-bottom: 20px; flex-wrap: wrap; }
.metric-box {
    flex: 1; min-width: 160px;
    background: linear-gradient(145deg, #1e2d52 0%, #2a3f6f 100%);
    border: none;
    border-radius: 14px;
    padding: 20px 20px;
    text-align: center;
    box-shadow: 0 4px 16px rgba(26,35,64,0.35);
}
.metric-box .m-val { font-size: 38px; font-weight: 700; color: #ffffff; line-height: 1.1; }
.metric-box .m-lbl { font-size: 13px; color: #9fb3d8; margin-top: 5px; }
.metric-box.alert {
    background: linear-gradient(145deg, #7a1a1a 0%, #a83232 100%);
    box-shadow: 0 4px 16px rgba(160,40,40,0.4);
}
.metric-box.alert .m-val { color: #ffffff; }
.metric-box.alert .m-lbl { color: #f5b8b8; }

/* ── Inventory cards ── */
.card {
    border: 1px solid #e0e4f0;
    border-radius: 10px;
    padding: 9px 14px;
    margin-bottom: 8px;
    background: #ffffff;
    color: #1a1a1a;
    text-align: right;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
}
.card-title { font-weight: 700; font-size: 14px; color: #1a2340; margin-bottom: 3px; }
.card-line  { font-size: 13px; color: #4b5563; line-height: 1.5; }
.warn {
    border: 2px solid #e74c3c;
    background: #ffcccc;
    color: #1a1a1a;
    box-shadow: 0 2px 10px rgba(231,76,60,0.2);
}
.warn .card-title { color: #c0392b; }

/* ── Buttons ── */
.stButton > button {
    width: 100%;
    min-height: 50px;
    font-size: 16px;
    font-weight: 700;
    border-radius: 10px;
    background: #2e4080;
    color: #ffffff;
    border: none;
    transition: background 0.2s;
}
.stButton > button:hover { background: #1a2340; }

/* ── Low-stock grid (dashboard) ── */
.low-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
    margin-bottom: 16px;
}
.low-card {
    background: #ffcccc;
    border: 2px solid #e74c3c;
    border-radius: 10px;
    padding: 10px 13px;
    box-shadow: 0 2px 8px rgba(231,76,60,0.18);
    text-align: right;
}
.low-card-title {
    font-weight: 700;
    font-size: 15px;
    color: #c0392b;
    margin-bottom: 7px;
    border-bottom: 1px solid #f5a0a0;
    padding-bottom: 5px;
}
.low-card-row {
    display: flex;
    justify-content: space-between;
    font-size: 13px;
    line-height: 1.7;
}
.low-lbl { color: #7a3030; }
.low-val { font-weight: 600; color: #333; }
.low-val.red { color: #c0392b; }

/* ── Section divider ── */
.section-title {
    font-size: 17px;
    font-weight: 700;
    color: #1a2340;
    border-right: 4px solid #2e4080;
    padding-right: 10px;
    margin: 22px 0 12px;
    text-align: right;
}
</style>
""", unsafe_allow_html=True)

ensure_santaf_lengths()

# ── Sidebar navigation ────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style="text-align:center; padding: 18px 0 12px;">
        <div style="font-size:32px;">📦</div>
        <div style="font-size:17px; font-weight:700; color:#ffffff; margin-top:6px;">Trellidor</div>
        <div style="font-size:12px; color:#8899bb; margin-top:2px;">ניהול מלאי</div>
    </div>
    """, unsafe_allow_html=True)
    st.divider()

    st.markdown("<div style='font-size:11px; color:#8899bb; text-transform:uppercase; letter-spacing:1px; padding: 4px 0;'>ראשי</div>", unsafe_allow_html=True)
    page = st.radio("ניווט", [
        "🏠 דשבורד",
        "💡 קליטת לדים",
        "💡 ניפוק לדים",
        "💡 מלאי לדים",
        "🟫 קליטת סנטפים",
        "🟫 ניפוק סנטפים",
        "🟫 מלאי סנטפים",
        "⚙️ מינימום סנטפים",
        "📊 דוח צריכת סנטפים",
        "📅 דוח חודשי סנטפים",
    ], key="main_menu", label_visibility="collapsed")

    st.divider()
    st.markdown(f"<div style='font-size:11px; color:#8899bb; text-align:center;'>{datetime.now().strftime('%d/%m/%Y %H:%M')}</div>", unsafe_allow_html=True)


# ── Pages ─────────────────────────────────────────────────────────────────────
if page == "🏠 דשבורד":
    st.markdown("""<div class="page-banner"><h2>🏠 דשבורד</h2><p>סקירה כללית של מצב המלאי</p></div>""", unsafe_allow_html=True)

    led_rows = get_led_rows()
    santaf_rows = get_santaf_rows()

    total_leds = sum(int(r.get("quantity") or 0) for r in led_rows)
    total_santaf = sum(int(r.get("quantity") or 0) for r in santaf_rows)
    low_santaf = [
        r for r in santaf_rows
        if int(r.get("quantity") or 0) < int(r.get("min_quantity") or DEFAULT_MIN_SANTAF)
    ]

    alert_cls = "alert" if low_santaf else ""
    st.markdown(f"""
    <div class="metric-row">
        <div class="metric-box">
            <div class="m-val">{total_leds}</div>
            <div class="m-lbl">💡 לדים במלאי</div>
        </div>
        <div class="metric-box">
            <div class="m-val">{total_santaf}</div>
            <div class="m-lbl">🟫 סנטפים במלאי</div>
        </div>
        <div class="metric-box {alert_cls}">
            <div class="m-val">{len(low_santaf)}</div>
            <div class="m-lbl">⚠️ מידות מתחת למינימום</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if st.button("🔄 רענן מלאי", key="btn_refresh"):
        st.cache_data.clear()
        st.rerun()

    st.markdown('<div class="section-title">⚠️ סנטפים מתחת למינימום</div>', unsafe_allow_html=True)
    if not low_santaf:
        st.success("כל מידות הסנטף מעל המינימום.")
    else:
        grid_items = ""
        for r in low_santaf:
            qty = int(r.get("quantity") or 0)
            min_qty = int(r.get("min_quantity") or DEFAULT_MIN_SANTAF)
            shortage = min_qty - qty
            grid_items += f"""
            <div class="low-card">
                <div class="low-card-title">{r.get('length')} {MM}</div>
                <div class="low-card-row"><span class="low-lbl">מלאי</span><span class="low-val red">{qty}</span></div>
                <div class="low-card-row"><span class="low-lbl">מינימום</span><span class="low-val">{min_qty}</span></div>
                <div class="low-card-row"><span class="low-lbl">חוסר</span><span class="low-val red">−{shortage}</span></div>
            </div>"""
        st.markdown(f'<div class="low-grid">{grid_items}</div>', unsafe_allow_html=True)

        st.divider()
        st.markdown('<div class="section-title">📧 שלח דוח מלאי במייל</div>', unsafe_allow_html=True)

        email_history = load_email_history()
        if email_history:
            options = email_history + ["✏️ הזן כתובת חדשה"]
            selected_option = st.selectbox("כתובת מייל נמען", options, key="email_select")
            if selected_option == "✏️ הזן כתובת חדשה":
                recipient = st.text_input("הכנס כתובת מייל", key="email_recipient_new")
            else:
                recipient = selected_option
        else:
            recipient = st.text_input("כתובת מייל נמען", key="email_recipient")

        if st.button("📧 שלח דוח מלאי", key="btn_send_email"):
            if not recipient or not recipient.strip():
                st.error("יש להזין כתובת מייל נמען.")
            else:
                now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

                table_rows = ""
                for r in low_santaf:
                    qty = int(r.get("quantity") or 0)
                    min_qty = int(r.get("min_quantity") or DEFAULT_MIN_SANTAF)
                    shortage = min_qty - qty
                    row_bg = "#ffaaaa" if qty == 0 else "#fff5f5"
                    table_rows += f"""
                    <tr style="background:{row_bg};">
                        <td style="padding:10px 14px; text-align:center; font-weight:bold;">{r.get('length')} מ"מ</td>
                        <td style="padding:10px 14px; text-align:center; color:{'#cc0000' if qty == 0 else '#333'};">{qty}</td>
                        <td style="padding:10px 14px; text-align:center;">{min_qty}</td>
                        <td style="padding:10px 14px; text-align:center; color:#cc0000; font-weight:bold;">{shortage}</td>
                    </tr>"""

                html_body = f"""
                <html dir="rtl">
                <body style="margin:0; padding:20px; background:#f0f0f0; font-family:Arial,sans-serif; direction:rtl;">
                  <div style="max-width:620px; margin:0 auto; background:#ffffff; border-radius:10px; overflow:hidden; box-shadow:0 2px 10px rgba(0,0,0,0.12);">
                    <div style="background:#c0392b; padding:22px 28px;">
                      <h1 style="color:#ffffff; margin:0; font-size:22px;">&#9888; דוח מלאי סנטפים — התראה</h1>
                      <p style="color:#ffd5d5; margin:8px 0 0; font-size:13px;">תאריך ושעה: {now_str}</p>
                    </div>
                    <div style="padding:28px;">
                      <p style="font-size:15px; color:#333; margin-top:0;">להלן מידות הסנטף שהמלאי בהן נמוך מהמינימום הנדרש:</p>
                      <table style="width:100%; border-collapse:collapse; font-size:14px; border:1px solid #ddd;">
                        <thead>
                          <tr style="background:#333333; color:#ffffff;">
                            <th style="padding:11px 14px; text-align:center;">מידה</th>
                            <th style="padding:11px 14px; text-align:center;">מלאי נוכחי</th>
                            <th style="padding:11px 14px; text-align:center;">מינימום</th>
                            <th style="padding:11px 14px; text-align:center;">חוסר</th>
                          </tr>
                        </thead>
                        <tbody>{table_rows}</tbody>
                      </table>
                      <p style="margin-top:22px; font-size:13px; color:#666;">יש לדאוג לחידוש המלאי בהקדם האפשרי.</p>
                    </div>
                    <div style="background:#f5f5f5; padding:16px 28px; text-align:center; font-size:12px; color:#999; border-top:1px solid #e0e0e0;">
                      Trellidor Israel &nbsp;|&nbsp; מערכת ניהול מלאי לדים וסנטפים
                    </div>
                  </div>
                </body>
                </html>"""

                msg = MIMEMultipart("alternative")
                msg["Subject"] = f"דוח מלאי סנטפים — {now_str}"
                msg["From"] = SENDER_EMAIL
                msg["To"] = recipient.strip()
                msg.attach(MIMEText(html_body, "html", "utf-8"))

                try:
                    with st.spinner("שולח מייל..."):
                        with smtplib.SMTP("smtp.gmail.com", 587) as server:
                            server.starttls()
                            server.login(SENDER_EMAIL, SENDER_PASSWORD)
                            server.sendmail(SENDER_EMAIL, recipient.strip(), msg.as_string())
                    save_email_to_history(recipient.strip())
                    st.success(f"✅ המייל נשלח בהצלחה אל {recipient.strip()}")
                except Exception as e:
                    st.error(f"שגיאה בשליחת מייל: {e}")

elif page == "💡 קליטת לדים":
    st.markdown("""<div class="page-banner"><h2>💡 קליטת לדים</h2><p>הוספת לדים למלאי לפי הזמנת ספק</p></div>""", unsafe_allow_html=True)

    order_number = st.text_input("מספר הזמנת לדים / ספק", key="led_receive_order")
    led_type = st.text_input("סוג לד", key="led_receive_type")
    quantity = st.number_input("כמות", min_value=1, value=1, step=1, key="led_receive_qty")
    notes = st.text_area("הערות", key="led_receive_notes")

    if st.button("✅ הוסף למלאי לדים", key="btn_led_receive"):
        if not order_number.strip():
            st.error("חובה להזין מספר הזמנה.")
        elif not led_type.strip():
            st.error("חובה להזין סוג לד.")
        else:
            with st.spinner("שומר..."):
                supabase.table("led_inventory").insert({
                    "order_number": order_number.strip(),
                    "led_type": led_type.strip(),
                    "quantity": int(quantity),
                    "notes": notes.strip()
                }).execute()
            st.cache_data.clear()
            reset_led_form()
            st.success("✅ הלדים נשמרו במלאי.")
            st.rerun()

elif page == "💡 ניפוק לדים":
    st.markdown("""<div class="page-banner"><h2>💡 ניפוק לדים</h2><p>ניפוק לדים לפרגולה</p></div>""", unsafe_allow_html=True)

    rows = [r for r in get_led_rows() if int(r.get("quantity") or 0) > 0]

    pergola_order = st.text_input("מספר הזמנת פרגולה", key="led_issue_pergola")
    issue_date = st.date_input("תאריך ניפוק", value=date.today(), key="led_issue_date")

    search = st.text_input("🔍 חיפוש לפי מספר הזמנת ספק או סוג לד", key="led_issue_search")
    if search.strip():
        rows = [
            r for r in rows
            if search.strip().lower() in str(r.get("order_number", "")).lower()
            or search.strip().lower() in str(r.get("led_type", "")).lower()
        ]

    if not rows:
        st.info("אין לדים זמינים לניפוק.")
    else:
        options = []
        for r in rows:
            label = f"הזמנת ספק: {r.get('order_number')} | סוג: {r.get('led_type')} | כמות: {r.get('quantity')}"
            options.append((label, r))

        selected_label = st.selectbox("בחר לד לניפוק", [x[0] for x in options], key="led_issue_select")
        selected = dict(options)[selected_label]

        issue_qty = st.number_input("כמות לניפוק", min_value=1, value=1, step=1, key="led_issue_qty")
        notes = st.text_area("הערות", key="led_issue_notes")

        if st.button("✅ נפק לד", key="btn_led_issue"):
            current_qty = int(selected.get("quantity") or 0)

            if not pergola_order.strip():
                st.error("חובה להזין מספר הזמנת פרגולה.")
            elif issue_qty > current_qty:
                st.error(f"אין מספיק מלאי. קיים: {current_qty}")
            else:
                with st.spinner("מעדכן מלאי..."):
                    new_qty = current_qty - int(issue_qty)
                    supabase.table("led_inventory").update({
                        "quantity": new_qty
                    }).eq("id", selected["id"]).execute()

                st.cache_data.clear()
                st.success(f"✅ נופקו {issue_qty} לדים להזמנה {pergola_order}.")
                st.rerun()

elif page == "💡 מלאי לדים":
    st.markdown("""<div class="page-banner"><h2>💡 מלאי לדים</h2><p>כל הלדים הקיימים במלאי</p></div>""", unsafe_allow_html=True)

    if st.button("🔄 רענן מלאי", key="btn_refresh_led"):
        st.cache_data.clear()
        st.rerun()

    rows = get_led_rows()

    search = st.text_input("🔍 חיפוש לפי מספר הזמנת ספק או סוג לד", key="led_stock_search")
    if search.strip():
        rows = [
            r for r in rows
            if search.strip().lower() in str(r.get("order_number", "")).lower()
            or search.strip().lower() in str(r.get("led_type", "")).lower()
        ]

    if not rows:
        st.info("אין נתונים.")
    else:
        for r in rows:
            card(
                f"{r.get('led_type')}",
                [
                    f"מספר הזמנת ספק: {r.get('order_number')}",
                    f"כמות במלאי: {r.get('quantity')}",
                    f"הערות: {r.get('notes') or '-'}",
                    f"תאריך יצירה: {short_date(r.get('created_at'))}",
                ]
            )

elif page == "🟫 קליטת סנטפים":
    st.markdown("""<div class="page-banner"><h2>🟫 קליטת סנטפים</h2><p>הוספת סנטפים למלאי — סנטף BH שקוף</p></div>""", unsafe_allow_html=True)

    supplier_ref = st.text_input("מספר הזמנה / אסמכתא", key="santaf_receive_ref")
    receive_date = st.date_input("תאריך קליטה", value=date.today(), key="santaf_receive_date")
    notes = st.text_area("הערות", key="santaf_receive_notes")

    st.info("הכנס כמות רק במידות שקיבלת. שאר המידות תשאיר 0.")

    qty_by_length = {}
    cols = st.columns(2)
    for i, length in enumerate(SANTAF_LENGTHS):
        with cols[i % 2]:
            qty_by_length[length] = st.number_input(
                f"{length} {MM}",
                min_value=0,
                value=0,
                step=1,
                key=f"santaf_receive_qty_{length}"
            )

    if st.button("✅ קלוט סנטפים", key="btn_santaf_receive"):
        if not supplier_ref.strip():
            st.error("חובה להזין מספר הזמנה / אסמכתא.")
        else:
            total = 0

            with st.spinner("שומר סנטפים..."):
                for length, qty in qty_by_length.items():
                    if int(qty) > 0:
                        row = get_santaf_row(length)
                        current = int(row.get("quantity") or 0)
                        new_qty = current + int(qty)

                        supabase.table("santaf_inventory").update({
                            "quantity": new_qty
                        }).eq("id", row["id"]).execute()

                        supabase.table("santaf_movements").insert({
                            "length": length,
                            "quantity": int(qty),
                            "type": "IN",
                            "date": str(receive_date)
                        }).execute()

                        total += int(qty)

            if total == 0:
                st.warning("לא הוזנה שום כמות.")
            else:
                st.cache_data.clear()
                st.success(f"✅ נקלטו {total} סנטפים למלאי.")
                st.rerun()

elif page == "🟫 ניפוק סנטפים":
    st.markdown("""<div class="page-banner"><h2>🟫 ניפוק סנטפים</h2><p>ניפוק סנטפים לפרגולה</p></div>""", unsafe_allow_html=True)

    pergola_order = st.text_input("מספר הזמנת פרגולה", key="santaf_issue_order")
    issue_date = st.date_input("תאריך ניפוק", value=date.today(), key="santaf_issue_date")

    length_search = st.text_input("🔍 חיפוש לפי אורך", key="santaf_issue_length_search")
    available_lengths = SANTAF_LENGTHS
    if length_search.strip():
        available_lengths = [l for l in SANTAF_LENGTHS if length_search.strip() in str(l)]

    if not available_lengths:
        st.warning("לא נמצאה מידה מתאימה.")
    else:
        length = st.selectbox("בחר מידה", available_lengths, key="santaf_issue_length")

        row = get_santaf_row(length)
        current_qty = int(row.get("quantity") or 0)
        min_qty = int(row.get("min_quantity") or DEFAULT_MIN_SANTAF)

        col1, col2 = st.columns(2)
        col1.metric("מלאי נוכחי", current_qty)
        col2.metric("מינימום", min_qty)

        issue_qty = st.number_input("כמות לניפוק", min_value=1, value=1, step=1, key="santaf_issue_qty")
        notes = st.text_area("הערות", key="santaf_issue_notes")

        if st.button("✅ נפק סנטף", key="btn_santaf_issue"):
            if not pergola_order.strip():
                st.error("חובה להזין מספר הזמנת פרגולה.")
            elif issue_qty > current_qty:
                st.error(f"אין מספיק מלאי. קיים: {current_qty}")
            else:
                with st.spinner("מעדכן מלאי..."):
                    new_qty = current_qty - int(issue_qty)

                    supabase.table("santaf_inventory").update({
                        "quantity": new_qty
                    }).eq("id", row["id"]).execute()

                    supabase.table("santaf_movements").insert({
                        "length": length,
                        "quantity": int(issue_qty),
                        "type": "OUT",
                        "date": str(issue_date)
                    }).execute()

                if new_qty < min_qty:
                    st.warning(f"✅ נופק, אבל המלאי ירד מתחת למינימום. נשאר: {new_qty}")
                else:
                    st.success("✅ הסנטף נופק בהצלחה.")
                st.cache_data.clear()
                st.rerun()

elif page == "🟫 מלאי סנטפים":
    st.markdown("""<div class="page-banner"><h2>🟫 מלאי סנטפים</h2><p>כל מידות הסנטף וכמויותיהן</p></div>""", unsafe_allow_html=True)

    if st.button("🔄 רענן מלאי", key="btn_refresh_santaf"):
        st.cache_data.clear()
        st.rerun()

    rows = get_santaf_rows()

    length_search = st.text_input("🔍 חיפוש לפי אורך", key="santaf_stock_search")
    if length_search.strip():
        rows = [r for r in rows if length_search.strip() in str(r.get("length", ""))]

    for r in rows:
        quantity = int(r.get("quantity") or 0)
        min_quantity = int(r.get("min_quantity") or DEFAULT_MIN_SANTAF)

        card(
            f"{r.get('length')} {MM}",
            [
                f"סוג: סנטף BH שקוף",
                f"מלאי נוכחי: {quantity}",
                f"מינימום: {min_quantity}",
            ],
            warning=quantity < min_quantity
        )

elif page == "⚙️ מינימום סנטפים":
    st.markdown("""<div class="page-banner"><h2>⚙️ הגדרת מינימום</h2><p>קביעת כמות מינימום לכל מידת סנטף</p></div>""", unsafe_allow_html=True)

    rows = get_santaf_rows()

    cols = st.columns(2)
    for i, r in enumerate(rows):
        length = int(r.get("length"))
        current_min = int(r.get("min_quantity") or DEFAULT_MIN_SANTAF)

        with cols[i % 2]:
            with st.container(border=True):
                st.subheader(f"{length} {MM}")
                new_min = st.number_input(
                    f"מינימום",
                    min_value=0,
                    value=current_min,
                    step=1,
                    key=f"santaf_min_{length}"
                )

                if st.button(f"💾 שמור", key=f"btn_save_min_{length}"):
                    with st.spinner("שומר..."):
                        supabase.table("santaf_inventory").update({
                            "min_quantity": int(new_min)
                        }).eq("id", r["id"]).execute()
                    st.cache_data.clear()
                    st.success("✅ נשמר.")
                    st.rerun()

elif page == "📅 דוח חודשי סנטפים":
    st.markdown("""<div class="page-banner"><h2>📅 דוח חודשי סנטפים</h2><p>סיכום ניפוקים לפי תקופה עם עלויות</p></div>""", unsafe_allow_html=True)

    today = date.today()
    first_of_month = today.replace(day=1)

    col1, col2 = st.columns(2)
    with col1:
        d1 = st.date_input("מתאריך", value=first_of_month, key="monthly_from")
    with col2:
        d2 = st.date_input("עד תאריך", value=today, key="monthly_to")

    price_per_meter = st.number_input(
        "מחיר לכל מטר סנטף (₪)",
        min_value=0.0,
        value=0.0,
        step=0.5,
        format="%.2f",
        key="monthly_price"
    )

    movements = safe_data(
        supabase.table("santaf_movements")
        .select("*")
        .eq("type", "OUT")
        .gte("date", str(d1))
        .lte("date", str(d2))
        .execute()
    )

    if not movements:
        st.info("אין ניפוקי סנטפים בטווח התאריכים.")
    else:
        df = pd.DataFrame(movements)
        df["meters"] = df["length"].astype(float) / 1000 * df["quantity"].astype(float)
        report = df.groupby("length").agg(quantity=("quantity", "sum"), meters=("meters", "sum")).reset_index()

        total_qty = int(report["quantity"].sum())
        total_meters = report["meters"].sum()
        total_cost = total_meters * price_per_meter if price_per_meter > 0 else None

        col1, col2 = st.columns(2)
        col1.metric("סה״כ יחידות שנופקו", total_qty)
        col2.metric("סה״כ מטרים", f"{total_meters:.1f} מ׳")
        if total_cost is not None:
            st.metric("סה״כ עלות", f"₪{total_cost:,.2f}")

        st.divider()
        for _, r in report.iterrows():
            meters = float(r["meters"])
            lines = [
                f"כמות יחידות: {int(r['quantity'])}",
                f"סה״כ מטרים: {meters:.1f} מ׳",
            ]
            if price_per_meter > 0:
                lines.append(f"עלות: ₪{meters * price_per_meter:,.2f}")
            card(f"{int(r['length'])} {MM}", lines)

elif page == "📊 דוח צריכת סנטפים":
    st.markdown("""<div class="page-banner"><h2>📊 דוח צריכת סנטפים</h2><p>ניתוח צריכה לפי טווח תאריכים</p></div>""", unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        d1 = st.date_input("מתאריך", value=date.today(), key="report_from")
    with col2:
        d2 = st.date_input("עד תאריך", value=date.today(), key="report_to")

    movements = safe_data(
        supabase.table("santaf_movements")
        .select("*")
        .eq("type", "OUT")
        .gte("date", str(d1))
        .lte("date", str(d2))
        .execute()
    )

    if not movements:
        st.info("אין צריכת סנטפים בטווח התאריכים.")
    else:
        df = pd.DataFrame(movements)
        report = df.groupby("length")["quantity"].sum().reset_index()
        total = int(report["quantity"].sum())

        st.metric("סה״כ סנטפים שנופקו", total)

        for _, r in report.iterrows():
            percent = (int(r["quantity"]) / total) * 100 if total else 0
            card(
                f"{int(r['length'])} {MM}",
                [
                    f"כמות: {int(r['quantity'])}",
                    f"אחוז מכלל הצריכה: {percent:.1f}%",
                ]
            )
