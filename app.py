import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, date
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import io
import urllib.parse

DB = "inventory_real.db"

SANTAF_LENGTHS = [
    "1500", "2000", "2500", "3000",
    "3500", "4000", "4500", "5000",
    "5500", "6000", "6500", "7000",
    "7500", "8000"
]

WIDTH = 1  # מטר


def conn():
    return sqlite3.connect(DB, check_same_thread=False)


def run(q, p=()):
    c = conn()
    cur = c.cursor()
    cur.execute(q, p)
    c.commit()
    c.close()


def get(q, p=()):
    c = conn()
    df = pd.read_sql_query(q, c, params=p)
    c.close()
    return df


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------- DB ----------
run("""
CREATE TABLE IF NOT EXISTS santaf_inventory (
    length TEXT PRIMARY KEY,
    stock INTEGER,
    min_stock INTEGER,
    price REAL
)
""")

run("""
CREATE TABLE IF NOT EXISTS santaf_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    order_number TEXT,
    length TEXT,
    qty INTEGER
)
""")

# init
for l in SANTAF_LENGTHS:
    run("""
    INSERT OR IGNORE INTO santaf_inventory
    VALUES (?, 0, 20, 0)
    """, (l,))


# ---------- UI ----------
st.set_page_config(layout="centered")

st.markdown("""
<style>
body {direction: rtl;}
.main {max-width:700px;}
.card {
border:1px solid #ddd;
padding:10px;
border-radius:10px;
margin-bottom:10px;
}
</style>
""", unsafe_allow_html=True)

st.title("🟫 ניהול סנטפים מתקדם")

page = st.selectbox("בחר מסך", [
    "📦 קליטה למלאי",
    "📤 ניפוק",
    "⚙️ הגדרות מינימום ומחיר",
    "📊 דוח צריכה"
])

# ---------- קליטה ----------
if page == "📦 קליטה למלאי":
    st.header("קליטת סנטפים")

    order = st.text_input("מספר הזמנה")
    d = st.date_input("תאריך")

    quantities = {}

    for l in SANTAF_LENGTHS:
        quantities[l] = st.number_input(f"{l} ממ", 0)

    if st.button("קלוט"):
        for l, q in quantities.items():
            if q > 0:
                run("UPDATE santaf_inventory SET stock = stock + ? WHERE length=?", (q, l))
        st.success("נקלט")

# ---------- ניפוק ----------
elif page == "📤 ניפוק":
    st.header("ניפוק סנטפים")

    order = st.text_input("מספר הזמנה")
    d = st.date_input("תאריך ניפוק")

    length = st.selectbox("מידה", SANTAF_LENGTHS)

    stock = get("SELECT stock FROM santaf_inventory WHERE length=?", (length,))
    stock = int(stock.iloc[0]["stock"])

    st.write("מלאי:", stock)

    qty = st.number_input("כמות", 1)

    if st.button("נפק"):
        if qty > stock:
            st.error("אין מספיק מלאי")
        else:
            run("UPDATE santaf_inventory SET stock = stock - ? WHERE length=?", (qty, length))
            run("INSERT INTO santaf_usage (date, order_number, length, qty) VALUES (?, ?, ?, ?)",
                (str(d), order, length, qty))
            st.success("נופק")

# ---------- מינימום ----------
elif page == "⚙️ הגדרות מינימום ומחיר":
    st.header("הגדרות")

    df = get("SELECT * FROM santaf_inventory")

    for _, r in df.iterrows():
        col1, col2 = st.columns(2)

        new_min = col1.number_input(f"מינימום {r['length']}", value=int(r["min_stock"]))
        new_price = col2.number_input(f"מחיר למ״ר {r['length']}", value=float(r["price"]))

        if st.button(f"שמור {r['length']}"):
            run("""
            UPDATE santaf_inventory
            SET min_stock=?, price=?
            WHERE length=?
            """, (new_min, new_price, r["length"]))

# ---------- דוח ----------
elif page == "📊 דוח צריכה":
    st.header("דוח צריכה")

    d1 = st.date_input("מתאריך")
    d2 = st.date_input("עד תאריך")

    df = get("""
    SELECT length, SUM(qty) as total
    FROM santaf_usage
    WHERE date BETWEEN ? AND ?
    GROUP BY length
    """, (str(d1), str(d2)))

    if df.empty:
        st.warning("אין נתונים")
    else:
        total_all = df["total"].sum()

        report_text = ""

        st.subheader("תוצאות")

        for _, r in df.iterrows():
            length = int(r["length"])
            qty = r["total"]

            meters = (length / 1000) * qty
            percent = (qty / total_all) * 100

            price = get("SELECT price FROM santaf_inventory WHERE length=?", (str(length),))
            price = float(price.iloc[0]["price"])

            cost = meters * price

            line = f"{length} ממ | כמות: {qty} | {percent:.1f}% | שטח: {meters:.2f} מ\"ר | עלות: {cost:.2f}"
            report_text += line + "\n"

            st.write(line)

        # ---------- PDF ----------
        if st.button("📄 צור PDF"):
            buffer = io.BytesIO()
            c = canvas.Canvas(buffer)

            y = 800
            for line in report_text.split("\n"):
                c.drawString(50, y, line)
                y -= 20

            c.save()
            buffer.seek(0)

            st.download_button(
                "📥 הורד PDF",
                buffer,
                file_name="report.pdf",
                mime="application/pdf"
            )

        # ---------- WhatsApp ----------
        msg = urllib.parse.quote(report_text)
        wa_link = f"https://wa.me/?text={msg}"
        st.markdown(f"[📲 שלח לוואטסאפ]({wa_link})")

        # ---------- Email ----------
        mail = urllib.parse.quote(report_text)
        mail_link = f"mailto:?subject=דוח סנטפים&body={mail}"
        st.markdown(f"[📧 שלח למייל]({mail_link})")
