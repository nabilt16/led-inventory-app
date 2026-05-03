import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, date

DB = "inventory_mobile.db"


def conn():
    return sqlite3.connect(DB, check_same_thread=False)


def run(query, params=()):
    c = conn()
    cur = c.cursor()
    cur.execute(query, params)
    c.commit()
    c.close()


def get(query):
    c = conn()
    df = pd.read_sql_query(query, c)
    c.close()
    return df


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------- INIT DB ----------
run("""
CREATE TABLE IF NOT EXISTS led_inventory (
    led_type TEXT PRIMARY KEY,
    main_stock INTEGER DEFAULT 0,
    packing_stock INTEGER DEFAULT 0
)
""")

run("""
CREATE TABLE IF NOT EXISTS santaf_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    color TEXT,
    length TEXT,
    packing_stock INTEGER DEFAULT 0,
    min_stock INTEGER DEFAULT 20
)
""")

run("""
CREATE TABLE IF NOT EXISTS orders (
    order_number TEXT PRIMARY KEY,
    customer TEXT,
    packing_date TEXT,
    status TEXT,
    needs_led INTEGER,
    needs_santaf INTEGER
)
""")


# ---------- STYLE ----------
st.set_page_config(layout="centered")

st.markdown("""
<style>
.main .block-container {
    max-width: 700px;
    padding: 10px;
}

body {
    direction: rtl;
}

h1, h2, h3 {
    text-align: right;
}

.stButton > button {
    width: 100%;
    height: 55px;
    font-size: 18px;
    font-weight: bold;
    border-radius: 12px;
}

.card {
    border: 1px solid #ddd;
    padding: 12px;
    border-radius: 12px;
    margin-bottom: 10px;
}
</style>
""", unsafe_allow_html=True)


# ---------- MENU ----------
st.title("🏗️ ניהול לדים וסנטפים")

page = st.selectbox("בחר מסך", [
    "🏠 דשבורד",
    "➕ הזמנה חדשה",
    "💡 מלאי לדים",
    "🟫 מלאי סנטפים",
])


# ---------- DASHBOARD ----------
if page == "🏠 דשבורד":
    st.subheader("📊 מצב כללי")

    leds = get("SELECT * FROM led_inventory")
    santaf = get("SELECT * FROM santaf_inventory")

    led_pack = leds["packing_stock"].sum() if not leds.empty else 0
    santaf_pack = santaf["packing_stock"].sum() if not santaf.empty else 0

    st.metric("לדים באריזה", int(led_pack))
    st.metric("סנטפים באריזה", int(santaf_pack))

    st.subheader("⚠️ סנטפים מתחת למינימום")

    low = get("""
    SELECT * FROM santaf_inventory
    WHERE packing_stock < min_stock
    """)

    if low.empty:
        st.success("הכל תקין")
    else:
        for _, r in low.iterrows():
            st.markdown(f"""
            <div class="card">
            צבע: {r['color']}<br>
            אורך: {r['length']}<br>
            מלאי: {r['packing_stock']} / מינימום {r['min_stock']}
            </div>
            """, unsafe_allow_html=True)


# ---------- NEW ORDER ----------
elif page == "➕ הזמנה חדשה":
    st.subheader("➕ יצירת הזמנה")

    order = st.text_input("מספר הזמנה")
    customer = st.text_input("לקוח")
    date_input = st.date_input("תאריך אריזה", value=date.today())

    needs_led = st.checkbox("צריך לד")
    needs_santaf = st.checkbox("צריך סנטף")

    if st.button("שמור"):
        if not order:
            st.error("חסר מספר הזמנה")
        else:
            run("""
            INSERT OR REPLACE INTO orders
            VALUES (?, ?, ?, ?, ?, ?)
            """, (
                order,
                customer,
                str(date_input),
                "חדש",
                int(needs_led),
                int(needs_santaf)
            ))
            st.success("נשמר")


# ---------- LED ----------
elif page == "💡 מלאי לדים":
    st.subheader("💡 מלאי לדים")

    leds = get("SELECT * FROM led_inventory")

    for _, r in leds.iterrows():
        st.markdown(f"""
        <div class="card">
        סוג: {r['led_type']}<br>
        ראשי: {r['main_stock']}<br>
        אריזה: {r['packing_stock']}
        </div>
        """, unsafe_allow_html=True)

    st.divider()

    st.subheader("➕ הוספת לד")

    led_type = st.text_input("סוג לד")
    qty = st.number_input("כמות", min_value=1)

    if st.button("הוסף למלאי"):
        run("""
        INSERT INTO led_inventory (led_type, packing_stock)
        VALUES (?, ?)
        ON CONFLICT(led_type)
        DO UPDATE SET packing_stock = packing_stock + ?
        """, (led_type, qty, qty))

        st.success("נוסף")


# ---------- SANTAF ----------
elif page == "🟫 מלאי סנטפים":
    st.subheader("🟫 מלאי סנטפים")

    santaf = get("SELECT * FROM santaf_inventory")

    for _, r in santaf.iterrows():
        color = "red" if r["packing_stock"] < r["min_stock"] else "black"

        st.markdown(f"""
        <div class="card" style="color:{color}">
        צבע: {r['color']}<br>
        אורך: {r['length']}<br>
        מלאי: {r['packing_stock']}<br>
        מינימום: {r['min_stock']}
        </div>
        """, unsafe_allow_html=True)

    st.divider()

    st.subheader("➕ הוספת סנטף")

    color = st.text_input("צבע")
    length = st.text_input("אורך")
    qty = st.number_input("כמות", min_value=1)

    if st.button("הוסף סנטף"):
        run("""
        INSERT INTO santaf_inventory (color, length, packing_stock)
        VALUES (?, ?, ?)
        """, (color, length, qty))

        st.success("נוסף")
