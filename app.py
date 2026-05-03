import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, date
from reportlab.pdfgen import canvas
import io
import urllib.parse

DB = "inventory_reports_v1.db"

SANTAF_TYPE = "סנטף BH שקוף"
SANTAF_WIDTH_M = 1.0
SANTAF_LENGTHS = [
    "1500", "2000", "2500", "3000",
    "3500", "4000", "4500", "5000",
    "5500", "6000", "6500", "7000",
    "7500", "8000"
]


def conn():
    return sqlite3.connect(DB, check_same_thread=False)


def run(query, params=()):
    c = conn()
    cur = c.cursor()
    cur.execute(query, params)
    c.commit()
    c.close()


def get(query, params=()):
    c = conn()
    df = pd.read_sql_query(query, c, params=params)
    c.close()
    return df


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def init_db():
    run("""
    CREATE TABLE IF NOT EXISTS led_inventory (
        supplier_order_number TEXT NOT NULL,
        led_type TEXT NOT NULL,
        stock INTEGER DEFAULT 0,
        PRIMARY KEY (supplier_order_number, led_type)
    )
    """)

    run("""
    CREATE TABLE IF NOT EXISTS santaf_inventory (
        length TEXT PRIMARY KEY,
        stock INTEGER DEFAULT 0,
        min_stock INTEGER DEFAULT 20,
        price_per_sqm REAL DEFAULT 0
    )
    """)

    run("""
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT,
        action TEXT,
        product TEXT,
        order_number TEXT,
        supplier_order_number TEXT,
        item_details TEXT,
        length TEXT,
        quantity INTEGER,
        sqm REAL DEFAULT 0,
        cost REAL DEFAULT 0,
        notes TEXT
    )
    """)

    for length in SANTAF_LENGTHS:
        run("""
        INSERT OR IGNORE INTO santaf_inventory
        (length, stock, min_stock, price_per_sqm)
        VALUES (?, 0, 20, 0)
        """, (length,))


def add_tx(action, product, order_number, supplier_order_number, item_details, length, quantity, sqm=0, cost=0, notes=""):
    run("""
    INSERT INTO transactions
    (created_at, action, product, order_number, supplier_order_number,
     item_details, length, quantity, sqm, cost, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now(), action, product, order_number, supplier_order_number,
        item_details, length, int(quantity), float(sqm), float(cost), notes
    ))


def make_pdf(title, lines):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.setTitle(title)

    y = 800
    pdf.setFont("Helvetica", 12)
    pdf.drawString(40, y, title)
    y -= 30

    for line in lines:
        if y < 40:
            pdf.showPage()
            pdf.setFont("Helvetica", 12)
            y = 800
        pdf.drawString(40, y, line)
        y -= 22

    pdf.save()
    buffer.seek(0)
    return buffer


def setup_ui():
    st.set_page_config(page_title="ניהול לדים וסנטפים", page_icon="🏗️", layout="centered")

    st.markdown("""
    <style>
    .main .block-container {
        max-width: 720px;
        padding: 12px;
    }

    body, p, label, div, span {
        direction: rtl;
    }

    h1, h2, h3 {
        text-align: right;
        line-height: 1.3;
    }

    .stButton > button {
        width: 100%;
        min-height: 54px;
        font-size: 18px;
        font-weight: bold;
        border-radius: 14px;
    }

    .card {
        border: 1px solid #ddd;
        border-radius: 14px;
        padding: 14px;
        margin-bottom: 10px;
        background: #fafafa;
        text-align: right;
        direction: rtl;
    }

    .warn {
        border: 2px solid #ff4b4b;
        background: #fff2f2;
    }
    </style>
    """, unsafe_allow_html=True)


def card(lines, warn=False):
    cls = "card warn" if warn else "card"
    html = f"<div class='{cls}'>" + "<br>".join(lines) + "</div>"
    st.markdown(html, unsafe_allow_html=True)


init_db()
setup_ui()

st.title("🏗️ ניהול לדים וסנטפים")

page = st.selectbox("בחר מסך", [
    "🏠 דשבורד",
    "💡 קליטת לדים לפי הזמנה",
    "💡 ניפוק לדים לפרגולה",
    "💡 מלאי לדים",
    "🟫 קליטת סנטפים למלאי",
    "🟫 ניפוק סנטפים לפרגולה",
    "🟫 מלאי סנטפים",
    "⚙️ מינימום ומחיר סנטפים",
    "📊 דוח צריכת סנטפים",
    "📊 דוח צריכת לדים",
    "📜 היסטוריה",
    "⚠️ איפוס"
])


if page == "🏠 דשבורד":
    st.header("🏠 דשבורד")

    led_total_df = get("SELECT SUM(stock) AS total FROM led_inventory")
    santaf_total_df = get("SELECT SUM(stock) AS total FROM santaf_inventory")
    low_df = get("SELECT * FROM santaf_inventory WHERE stock < min_stock ORDER BY CAST(length AS INTEGER)")

    led_total = int(led_total_df.iloc[0]["total"]) if pd.notna(led_total_df.iloc[0]["total"]) else 0
    santaf_total = int(santaf_total_df.iloc[0]["total"]) if pd.notna(santaf_total_df.iloc[0]["total"]) else 0

    st.metric("💡 סה״כ לדים במלאי", led_total)
    st.metric("🟫 סה״כ סנטפים במלאי", santaf_total)
    st.metric("⚠️ מידות סנטף מתחת למינימום", len(low_df))

    st.subheader("⚠️ סנטפים מתחת למינימום")
    if low_df.empty:
        st.success("כל המידות מעל המינימום.")
    else:
        for _, r in low_df.iterrows():
            card([
                f"**מידה:** {r['length']} ממ",
                f"**מלאי:** {r['stock']}",
                f"**מינימום:** {r['min_stock']}"
            ], warn=True)


elif page == "💡 קליטת לדים לפי הזמנה":
    st.header("💡 קליטת לדים לפי מספר הזמנה")

    supplier_order = st.text_input("מספר הזמנת לדים / ספק")
    receipt_date = st.date_input("תאריך קליטה", value=date.today())
    led_type = st.text_input("סוג לד")
    qty = st.number_input("כמות לקליטה", min_value=1, step=1)
    notes = st.text_area("הערות")

    if st.button("✅ קלוט לדים"):
        if not supplier_order.strip():
            st.error("חובה להזין מספר הזמנה.")
        elif not led_type.strip():
            st.error("חובה להזין סוג לד.")
        else:
            run("""
            INSERT INTO led_inventory (supplier_order_number, led_type, stock)
            VALUES (?, ?, ?)
            ON CONFLICT(supplier_order_number, led_type)
            DO UPDATE SET stock = stock + ?
            """, (supplier_order.strip(), led_type.strip(), int(qty), int(qty)))

            add_tx("קליטת לדים", "לד", "", supplier_order.strip(), led_type.strip(), "", qty, 0, 0,
                   f"תאריך קליטה: {receipt_date}. {notes}")

            st.success("✅ הלדים נקלטו.")


elif page == "💡 ניפוק לדים לפרגולה":
    st.header("💡 ניפוק לדים לפרגולה")

    order_number = st.text_input("מספר הזמנת פרגולה")
    issue_date = st.date_input("תאריך ניפוק", value=date.today())

    inv = get("""
    SELECT supplier_order_number, led_type, stock
    FROM led_inventory
    WHERE stock > 0
    ORDER BY supplier_order_number, led_type
    """)

    if inv.empty:
        st.info("אין לדים זמינים.")
    else:
        inv["label"] = (
            "הזמנה: " + inv["supplier_order_number"] +
            " | סוג: " + inv["led_type"] +
            " | מלאי: " + inv["stock"].astype(str)
        )

        selected = st.selectbox("בחר לד לפי מספר הזמנת ספק", inv["label"])
        row = inv[inv["label"] == selected].iloc[0]

        qty = st.number_input("כמות לניפוק", min_value=1, step=1)
        notes = st.text_area("הערות")

        if st.button("✅ נפק לד"):
            if not order_number.strip():
                st.error("חובה להזין מספר הזמנת פרגולה.")
            elif int(qty) > int(row["stock"]):
                st.error(f"אין מספיק מלאי. קיים: {row['stock']}")
            else:
                run("""
                UPDATE led_inventory
                SET stock = stock - ?
                WHERE supplier_order_number = ? AND led_type = ?
                """, (int(qty), row["supplier_order_number"], row["led_type"]))

                add_tx("ניפוק לדים לפרגולה", "לד", order_number.strip(), row["supplier_order_number"],
                       row["led_type"], "", qty, 0, 0, f"תאריך ניפוק: {issue_date}. {notes}")

                st.success("✅ הלד נופק.")


elif page == "💡 מלאי לדים":
    st.header("💡 מלאי לדים לפי הזמנה")

    inv = get("""
    SELECT supplier_order_number, led_type, stock
    FROM led_inventory
    ORDER BY supplier_order_number, led_type
    """)

    if inv.empty:
        st.info("אין מלאי לדים.")
    else:
        for _, r in inv.iterrows():
            card([
                f"**מספר הזמנת ספק:** {r['supplier_order_number']}",
                f"**סוג לד:** {r['led_type']}",
                f"**מלאי:** {r['stock']}"
            ])


elif page == "🟫 קליטת סנטפים למלאי":
    st.header("🟫 קליטת סנטפים למלאי")

    supplier_order = st.text_input("מספר הזמנה / אסמכתא")
    receipt_date = st.date_input("תאריך קליטה", value=date.today())

    st.write(f"**סוג מוצר:** {SANTAF_TYPE}")
    st.info("הכנס כמות רק במידות שקיבלת.")

    quantities = {}
    for length in SANTAF_LENGTHS:
        quantities[length] = st.number_input(f"{length} ממ", min_value=0, step=1, key=f"receive_{length}")

    notes = st.text_area("הערות")

    if st.button("✅ קלוט סנטפים"):
        if not supplier_order.strip():
            st.error("חובה להזין מספר הזמנה / אסמכתא.")
        else:
            total = 0

            for length, qty in quantities.items():
                if int(qty) > 0:
                    run("""
                    UPDATE santaf_inventory
                    SET stock = stock + ?
                    WHERE length = ?
                    """, (int(qty), length))

                    add_tx("קליטת סנטפים", "סנטף", "", supplier_order.strip(),
                           SANTAF_TYPE, length, qty, 0, 0,
                           f"תאריך קליטה: {receipt_date}. {notes}")

                    total += int(qty)

            if total == 0:
                st.warning("לא הוזנה שום כמות.")
            else:
                st.success(f"✅ נקלטו {total} סנטפים.")


elif page == "🟫 ניפוק סנטפים לפרגולה":
    st.header("🟫 ניפוק סנטפים לפרגולה")

    order_number = st.text_input("מספר הזמנת פרגולה")
    issue_date = st.date_input("תאריך ניפוק", value=date.today())

    length = st.selectbox("בחר מידה", SANTAF_LENGTHS)

    stock_df = get("SELECT stock, min_stock, price_per_sqm FROM santaf_inventory WHERE length = ?", (length,))
    stock = int(stock_df.iloc[0]["stock"])
    min_stock = int(stock_df.iloc[0]["min_stock"])
    price = float(stock_df.iloc[0]["price_per_sqm"])

    st.write(f"**מלאי נוכחי:** {stock}")
    st.write(f"**מינימום:** {min_stock}")

    qty = st.number_input("כמות לניפוק", min_value=1, step=1)
    notes = st.text_area("הערות")

    if st.button("✅ נפק סנטף"):
        if not order_number.strip():
            st.error("חובה להזין מספר הזמנת פרגולה.")
        elif int(qty) > stock:
            st.error(f"אין מספיק מלאי. קיים: {stock}")
        else:
            new_stock = stock - int(qty)
            length_m = int(length) / 1000
            sqm = length_m * SANTAF_WIDTH_M * int(qty)
            cost = sqm * price

            run("UPDATE santaf_inventory SET stock = ? WHERE length = ?", (new_stock, length))

            add_tx("ניפוק סנטפים לפרגולה", "סנטף", order_number.strip(), "",
                   SANTAF_TYPE, length, qty, sqm, cost,
                   f"תאריך ניפוק: {issue_date}. {notes}")

            if new_stock < min_stock:
                st.warning(f"✅ נופק, אבל המלאי ירד מתחת למינימום. נשאר: {new_stock}")
            else:
                st.success("✅ הסנטף נופק.")


elif page == "🟫 מלאי סנטפים":
    st.header("🟫 מלאי סנטפים לפי מידה")

    inv = get("""
    SELECT length, stock, min_stock, price_per_sqm
    FROM santaf_inventory
    ORDER BY CAST(length AS INTEGER)
    """)

    for _, r in inv.iterrows():
        warn = int(r["stock"]) < int(r["min_stock"])
        card([
            f"**סוג:** {SANTAF_TYPE}",
            f"**מידה:** {r['length']} ממ",
            f"**מלאי:** {r['stock']}",
            f"**מינימום:** {r['min_stock']}",
            f"**מחיר למ״ר:** {r['price_per_sqm']}"
        ], warn=warn)


elif page == "⚙️ מינימום ומחיר סנטפים":
    st.header("⚙️ הגדרת מינימום ומחיר")

    st.info("כאן אתה מגדיר מינימום ומחיר למ״ר לכל מידה.")

    inv = get("""
    SELECT length, min_stock, price_per_sqm
    FROM santaf_inventory
    ORDER BY CAST(length AS INTEGER)
    """)

    for _, r in inv.iterrows():
        with st.container(border=True):
            st.subheader(f"{r['length']} ממ")
            new_min = st.number_input(
                f"מינימום {r['length']}",
                min_value=0,
                value=int(r["min_stock"]),
                step=1,
                key=f"min_{r['length']}"
            )
            new_price = st.number_input(
                f"מחיר למ״ר {r['length']}",
                min_value=0.0,
                value=float(r["price_per_sqm"]),
                step=1.0,
                key=f"price_{r['length']}"
            )
            if st.button(f"💾 שמור {r['length']}", key=f"save_{r['length']}"):
                run("""
                UPDATE santaf_inventory
                SET min_stock = ?, price_per_sqm = ?
                WHERE length = ?
                """, (int(new_min), float(new_price), r["length"]))
                st.success("✅ נשמר.")


elif page == "📊 דוח צריכת סנטפים":
    st.header("📊 דוח צריכת סנטפים")

    d1 = st.date_input("מתאריך", value=date.today())
    d2 = st.date_input("עד תאריך", value=date.today())

    report = get("""
    SELECT length,
           SUM(quantity) AS total_qty,
           SUM(sqm) AS total_sqm,
           SUM(cost) AS total_cost
    FROM transactions
    WHERE product = 'סנטף'
      AND action = 'ניפוק סנטפים לפרגולה'
      AND date(created_at) BETWEEN ? AND ?
    GROUP BY length
    ORDER BY CAST(length AS INTEGER)
    """, (str(d1), str(d2)))

    if report.empty:
        st.warning("אין נתונים לתקופה שנבחרה.")
    else:
        total_qty = report["total_qty"].sum()
        total_sqm = report["total_sqm"].sum()
        total_cost = report["total_cost"].sum()

        st.metric("סה״כ כמות", int(total_qty))
        st.metric("סה״כ מ״ר", round(float(total_sqm), 2))
        st.metric("סה״כ עלות", round(float(total_cost), 2))

        lines = [
            f"Santaf usage report",
            f"From: {d1} To: {d2}",
            f"Total qty: {int(total_qty)}",
            f"Total sqm: {round(float(total_sqm), 2)}",
            f"Total cost: {round(float(total_cost), 2)}",
            "-----------------------------"
        ]

        for _, r in report.iterrows():
            percent = (float(r["total_qty"]) / float(total_qty)) * 100 if total_qty else 0
            card([
                f"**מידה:** {r['length']} ממ",
                f"**כמות:** {int(r['total_qty'])}",
                f"**אחוז מכלל הצריכה:** {percent:.1f}%",
                f"**מ״ר:** {float(r['total_sqm']):.2f}",
                f"**עלות:** {float(r['total_cost']):.2f}"
            ])

            lines.append(
                f"{r['length']} mm | qty: {int(r['total_qty'])} | {percent:.1f}% | sqm: {float(r['total_sqm']):.2f} | cost: {float(r['total_cost']):.2f}"
            )

        pdf = make_pdf("Santaf usage report", lines)

        st.download_button(
            "📥 הורד PDF",
            data=pdf,
            file_name="santaf_report.pdf",
            mime="application/pdf"
        )

        text_msg = "\n".join(lines)
        wa_link = f"https://wa.me/?text={urllib.parse.quote(text_msg)}"
        mail_link = f"mailto:?subject=Santaf Report&body={urllib.parse.quote(text_msg)}"

        st.markdown(f"[📲 שלח סיכום לוואטסאפ]({wa_link})")
        st.markdown(f"[📧 שלח סיכום למייל]({mail_link})")


elif page == "📊 דוח צריכת לדים":
    st.header("📊 דוח צריכת לדים")

    d1 = st.date_input("מתאריך", value=date.today())
    d2 = st.date_input("עד תאריך", value=date.today())

    report = get("""
    SELECT item_details AS led_type,
           supplier_order_number,
           SUM(quantity) AS total_qty
    FROM transactions
    WHERE product = 'לד'
      AND action = 'ניפוק לדים לפרגולה'
      AND date(created_at) BETWEEN ? AND ?
    GROUP BY item_details, supplier_order_number
    ORDER BY item_details
    """, (str(d1), str(d2)))

    if report.empty:
        st.warning("אין נתוני צריכת לדים לתקופה.")
    else:
        total_qty = report["total_qty"].sum()
        st.metric("סה״כ לדים שנופקו", int(total_qty))

        for _, r in report.iterrows():
            percent = (float(r["total_qty"]) / float(total_qty)) * 100 if total_qty else 0
            card([
                f"**סוג לד:** {r['led_type']}",
                f"**מספר הזמנת ספק:** {r['supplier_order_number']}",
                f"**כמות:** {int(r['total_qty'])}",
                f"**אחוז:** {percent:.1f}%"
            ])


elif page == "📜 היסטוריה":
    st.header("📜 היסטוריית תנועות")

    search = st.text_input("חיפוש לפי מספר הזמנה")
    product = st.selectbox("מוצר", ["הכל", "לד", "סנטף"])

    query = """
    SELECT created_at, action, product, order_number, supplier_order_number,
           item_details, length, quantity, sqm, cost, notes
    FROM transactions
    WHERE 1=1
    """
    params = []

    if search.strip():
        query += " AND (order_number LIKE ? OR supplier_order_number LIKE ?)"
        params.extend([f"%{search.strip()}%", f"%{search.strip()}%"])

    if product != "הכל":
        query += " AND product = ?"
        params.append(product)

    query += " ORDER BY created_at DESC"

    hist = get(query, tuple(params))

    if hist.empty:
        st.info("אין תנועות.")
    else:
        for _, r in hist.iterrows():
            card([
                f"**תאריך:** {r['created_at']}",
                f"**פעולה:** {r['action']}",
                f"**מוצר:** {r['product']}",
                f"**הזמנת פרגולה:** {r['order_number'] or '-'}",
                f"**הזמנת ספק:** {r['supplier_order_number'] or '-'}",
                f"**פירוט:** {r['item_details']}",
                f"**מידה:** {r['length'] or '-'}",
                f"**כמות:** {r['quantity']}",
                f"**מ״ר:** {r['sqm']}",
                f"**עלות:** {r['cost']}",
                f"**הערות:** {r['notes'] or '-'}"
            ])


elif page == "⚠️ איפוס":
    st.header("⚠️ איפוס נתונים")

    st.error("פעולה זו מוחקת את כל המלאי וההיסטוריה.")

    confirm = st.checkbox("אני מבין ורוצה לאפס")

    if confirm:
        if st.button("🗑️ אפס הכל"):
            run("DROP TABLE IF EXISTS led_inventory")
            run("DROP TABLE IF EXISTS santaf_inventory")
            run("DROP TABLE IF EXISTS transactions")
            init_db()
            st.success("✅ אופס בהצלחה.")
            st.rerun()
