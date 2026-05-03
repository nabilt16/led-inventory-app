import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, date

DB = "inventory_real.db"

SANTAF_TYPE = "סנטף BH שקוף"
SANTAF_LENGTHS = [
    "1500 ממ", "2000 ממ", "2500 ממ", "3000 ממ",
    "3500 ממ", "4000 ממ", "4500 ממ", "5000 ממ",
    "5500 ממ", "6000 ממ", "6500 ממ", "7000 ממ",
    "7500 ממ", "8000 ממ"
]
SANTAF_MIN_STOCK = 20


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
        supplier_order_number TEXT,
        led_type TEXT,
        stock INTEGER DEFAULT 0,
        PRIMARY KEY (supplier_order_number, led_type)
    )
    """)

    run("""
    CREATE TABLE IF NOT EXISTS santaf_inventory (
        length TEXT PRIMARY KEY,
        stock INTEGER DEFAULT 0,
        min_stock INTEGER DEFAULT 20
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
        quantity INTEGER,
        notes TEXT
    )
    """)

    for length in SANTAF_LENGTHS:
        run("""
        INSERT OR IGNORE INTO santaf_inventory (length, stock, min_stock)
        VALUES (?, 0, ?)
        """, (length, SANTAF_MIN_STOCK))


def add_tx(action, product, order_number, supplier_order_number, item_details, quantity, notes=""):
    run("""
    INSERT INTO transactions
    (created_at, action, product, order_number, supplier_order_number, item_details, quantity, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (now(), action, product, order_number, supplier_order_number, item_details, quantity, notes))


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
    "📜 היסטוריה",
    "⚠️ איפוס"
])


if page == "🏠 דשבורד":
    st.header("🏠 דשבורד")

    led_df = get("SELECT SUM(stock) AS total FROM led_inventory")
    santaf_df = get("SELECT SUM(stock) AS total FROM santaf_inventory")
    low_df = get("SELECT * FROM santaf_inventory WHERE stock < min_stock ORDER BY length")

    led_total = int(led_df.iloc[0]["total"]) if pd.notna(led_df.iloc[0]["total"]) else 0
    santaf_total = int(santaf_df.iloc[0]["total"]) if pd.notna(santaf_df.iloc[0]["total"]) else 0

    st.metric("💡 סה״כ לדים במלאי", led_total)
    st.metric("🟫 סה״כ סנטפים במלאי", santaf_total)
    st.metric("⚠️ מידות סנטף מתחת למינימום", len(low_df))

    st.subheader("⚠️ סנטפים מתחת למינימום")
    if low_df.empty:
        st.success("כל מידות הסנטף מעל מלאי מינימום.")
    else:
        for _, r in low_df.iterrows():
            card([
                f"**מידה:** {r['length']}",
                f"**מלאי נוכחי:** {r['stock']}",
                f"**מינימום:** {r['min_stock']}"
            ], warn=True)


elif page == "💡 קליטת לדים לפי הזמנה":
    st.header("💡 קליטת לדים לפי מספר הזמנה")

    supplier_order = st.text_input("מספר הזמנת לדים / ספק")
    receipt_date = st.date_input("תאריך קליטה", value=date.today())

    st.info("אם באותה הזמנה יש כמה סוגי לדים — קלוט כל סוג בנפרד עם אותו מספר הזמנה.")

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

            add_tx(
                "קליטת לדים",
                "לד",
                "",
                supplier_order.strip(),
                led_type.strip(),
                int(qty),
                f"תאריך קליטה: {receipt_date}. {notes}"
            )

            st.success("✅ הלדים נקלטו למלאי.")


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
        st.info("אין לדים זמינים במלאי.")
    else:
        inv["label"] = (
            "הזמנה: " + inv["supplier_order_number"] +
            " | סוג: " + inv["led_type"] +
            " | מלאי: " + inv["stock"].astype(str)
        )

        selected = st.selectbox("בחר לד לפי מספר הזמנה", inv["label"])
        row = inv[inv["label"] == selected].iloc[0]

        qty = st.number_input("כמות לניפוק", min_value=1, step=1)
        notes = st.text_area("הערות")

        if st.button("✅ נפק לד לפרגולה"):
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

                add_tx(
                    "ניפוק לדים לפרגולה",
                    "לד",
                    order_number.strip(),
                    row["supplier_order_number"],
                    row["led_type"],
                    int(qty),
                    f"תאריך ניפוק: {issue_date}. {notes}"
                )

                st.success("✅ הלד נופק לפרגולה.")


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
                f"**מספר הזמנת לדים:** {r['supplier_order_number']}",
                f"**סוג לד:** {r['led_type']}",
                f"**מלאי:** {r['stock']}"
            ])


elif page == "🟫 קליטת סנטפים למלאי":
    st.header("🟫 קליטת סנטפים למלאי")

    supplier_order = st.text_input("מספר הזמנה / אסמכתא")
    receipt_date = st.date_input("תאריך קליטה", value=date.today())
    st.write(f"**סוג מוצר:** {SANTAF_TYPE}")

    st.info("הכנס כמות רק במידות שקיבלת. שאר המידות תשאיר 0.")

    quantities = {}

    for length in SANTAF_LENGTHS:
        quantities[length] = st.number_input(length, min_value=0, step=1, key=f"receive_{length}")

    notes = st.text_area("הערות")

    if st.button("✅ קלוט סנטפים למלאי"):
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

                    add_tx(
                        "קליטת סנטפים",
                        "סנטף",
                        "",
                        supplier_order.strip(),
                        f"{SANTAF_TYPE} | {length}",
                        int(qty),
                        f"תאריך קליטה: {receipt_date}. {notes}"
                    )

                    total += int(qty)

            if total == 0:
                st.warning("לא הוזנה שום כמות.")
            else:
                st.success(f"✅ נקלטו {total} סנטפים למלאי.")


elif page == "🟫 ניפוק סנטפים לפרגולה":
    st.header("🟫 ניפוק סנטפים לפרגולה")

    order_number = st.text_input("מספר הזמנת פרגולה")
    issue_date = st.date_input("תאריך ניפוק", value=date.today())

    length = st.selectbox("בחר מידה", SANTAF_LENGTHS)

    stock_df = get("SELECT stock, min_stock FROM santaf_inventory WHERE length = ?", (length,))
    stock = int(stock_df.iloc[0]["stock"])
    min_stock = int(stock_df.iloc[0]["min_stock"])

    st.write(f"**מלאי נוכחי במידה {length}:** {stock}")
    st.write(f"**מינימום רצוי:** {min_stock}")

    qty = st.number_input("כמות לניפוק", min_value=1, step=1)
    notes = st.text_area("הערות")

    if st.button("✅ נפק סנטף לפרגולה"):
        if not order_number.strip():
            st.error("חובה להזין מספר הזמנת פרגולה.")
        elif int(qty) > stock:
            st.error(f"אין מספיק מלאי. קיים: {stock}")
        else:
            new_stock = stock - int(qty)

            run("""
            UPDATE santaf_inventory
            SET stock = ?
            WHERE length = ?
            """, (new_stock, length))

            add_tx(
                "ניפוק סנטפים לפרגולה",
                "סנטף",
                order_number.strip(),
                "",
                f"{SANTAF_TYPE} | {length}",
                int(qty),
                f"תאריך ניפוק: {issue_date}. {notes}"
            )

            if new_stock < min_stock:
                st.warning(f"⚠️ נופק בהצלחה, אבל המלאי ירד מתחת למינימום. נשאר: {new_stock}")
            else:
                st.success("✅ הסנטף נופק לפרגולה.")


elif page == "🟫 מלאי סנטפים":
    st.header("🟫 מלאי סנטפים לפי מידה")

    inv = get("""
    SELECT length, stock, min_stock
    FROM santaf_inventory
    ORDER BY CAST(REPLACE(length, ' ממ', '') AS INTEGER)
    """)

    for _, r in inv.iterrows():
        warn = int(r["stock"]) < int(r["min_stock"])
        card([
            f"**סוג:** {SANTAF_TYPE}",
            f"**מידה:** {r['length']}",
            f"**מלאי:** {r['stock']}",
            f"**מינימום:** {r['min_stock']}"
        ], warn=warn)


elif page == "📜 היסטוריה":
    st.header("📜 היסטוריית תנועות")

    search = st.text_input("חיפוש לפי מספר הזמנה")
    product = st.selectbox("מוצר", ["הכל", "לד", "סנטף"])

    query = """
    SELECT created_at, action, product, order_number, supplier_order_number,
           item_details, quantity, notes
    FROM transactions
    WHERE 1=1
    """
    params = []

    if search.strip():
        query += " AND (order_number LIKE ? OR supplier_order_number LIKE ?)"
        params.append(f"%{search.strip()}%")
        params.append(f"%{search.strip()}%")

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
                f"**מספר הזמנת פרגולה:** {r['order_number'] or '-'}",
                f"**מספר הזמנת ספק:** {r['supplier_order_number'] or '-'}",
                f"**פירוט:** {r['item_details']}",
                f"**כמות:** {r['quantity']}",
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
