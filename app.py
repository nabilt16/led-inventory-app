import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, date

DB = "inventory_v2.db"
SANTAF_MIN_STOCK = 20

PERGOLA_STATUSES = [
    "בייצור",
    "מוכן לאריזה",
    "באריזה",
    "ממתין ללד",
    "ממתין לסנטף",
    "ממתין ללד וסנטף",
    "מוכן למרלוג",
    "עלה למרלוג",
    "בוטל"
]

DEAD_REASONS = [
    "ביטול הזמנה",
    "דחייה ללא תיאום",
    "טעות ספק",
    "עודף",
    "אחר"
]


def conn():
    return sqlite3.connect(DB, check_same_thread=False)


def run_query(query, params=()):
    c = conn()
    cur = c.cursor()
    cur.execute(query, params)
    c.commit()
    c.close()


def get_df(query, params=()):
    c = conn()
    data = pd.read_sql_query(query, c, params=params)
    c.close()
    return data


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db():
    run_query("""
    CREATE TABLE IF NOT EXISTS led_inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        led_type TEXT UNIQUE NOT NULL,
        main_stock INTEGER DEFAULT 0,
        packing_stock INTEGER DEFAULT 0,
        dead_stock INTEGER DEFAULT 0
    )
    """)

    run_query("""
    CREATE TABLE IF NOT EXISTS santaf_inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        color TEXT NOT NULL,
        length TEXT NOT NULL,
        main_stock INTEGER DEFAULT 0,
        packing_stock INTEGER DEFAULT 0,
        dead_stock INTEGER DEFAULT 0,
        min_stock INTEGER DEFAULT 20,
        UNIQUE(color, length)
    )
    """)

    run_query("""
    CREATE TABLE IF NOT EXISTS pergola_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_number TEXT UNIQUE NOT NULL,
        customer_name TEXT,
        packing_date TEXT,
        status TEXT,
        needs_led INTEGER DEFAULT 0,
        led_type TEXT,
        led_qty INTEGER DEFAULT 0,
        led_issued INTEGER DEFAULT 0,
        needs_santaf INTEGER DEFAULT 0,
        santaf_color TEXT,
        santaf_length TEXT,
        santaf_qty INTEGER DEFAULT 0,
        santaf_issued INTEGER DEFAULT 0,
        notes TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """)

    run_query("""
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT,
        order_number TEXT,
        product TEXT,
        action TEXT,
        item_details TEXT,
        quantity INTEGER,
        from_location TEXT,
        to_location TEXT,
        reason TEXT,
        notes TEXT
    )
    """)


def add_transaction(order_number, product, action, item_details, quantity,
                    from_location="", to_location="", reason="", notes=""):
    run_query("""
    INSERT INTO transactions
    (created_at, order_number, product, action, item_details, quantity,
     from_location, to_location, reason, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now(), order_number, product, action, item_details, quantity,
        from_location, to_location, reason, notes
    ))


def ensure_led(led_type):
    run_query("""
    INSERT OR IGNORE INTO led_inventory
    (led_type, main_stock, packing_stock, dead_stock)
    VALUES (?, 0, 0, 0)
    """, (led_type,))


def ensure_santaf(color, length):
    run_query("""
    INSERT OR IGNORE INTO santaf_inventory
    (color, length, main_stock, packing_stock, dead_stock, min_stock)
    VALUES (?, ?, 0, 0, 0, ?)
    """, (color, length, SANTAF_MIN_STOCK))


def calc_status(needs_led, led_issued, needs_santaf, santaf_issued):
    missing_led = needs_led and not led_issued
    missing_santaf = needs_santaf and not santaf_issued

    if missing_led and missing_santaf:
        return "ממתין ללד וסנטף"
    if missing_led:
        return "ממתין ללד"
    if missing_santaf:
        return "ממתין לסנטף"
    return "מוכן למרלוג"


def setup_page():
    st.set_page_config(
        page_title="ניהול לדים וסנטפים",
        page_icon="🏗️",
        layout="wide"
    )

    st.markdown("""
    <style>
    body {
        direction: rtl;
    }

    .main .block-container {
        max-width: 900px;
        padding-top: 1rem;
        padding-left: 1rem;
        padding-right: 1rem;
    }

    h1, h2, h3, p, label {
        text-align: right;
    }

    .stButton > button {
        width: 100%;
        height: 52px;
        font-size: 18px;
        font-weight: 700;
        border-radius: 14px;
    }

    div[data-testid="stMetric"] {
        background: #f8f8f8;
        padding: 14px;
        border-radius: 14px;
        border: 1px solid #ddd;
    }

    [data-testid="stDataFrame"] {
        direction: ltr;
    }

    @media (max-width: 768px) {
        .main .block-container {
            padding-left: 0.5rem;
            padding-right: 0.5rem;
        }

        h1 {
            font-size: 26px !important;
        }

        h2, h3 {
            font-size: 22px !important;
        }

        .stButton > button {
            height: 58px;
            font-size: 19px;
        }
    }
    </style>
    """, unsafe_allow_html=True)


def show_cards(df, fields):
    if df.empty:
        st.info("אין נתונים להצגה.")
        return

    for _, row in df.iterrows():
        with st.container(border=True):
            for title, col in fields:
                value = row[col] if col in row and pd.notna(row[col]) else "-"
                st.write(f"**{title}:** {value}")


init_db()
setup_page()

st.sidebar.title("🏗️ תפריט")
page = st.sidebar.radio(
    "בחר מסך",
    [
        "🏠 דשבורד",
        "➕ הזמנת פרגולה",
        "💡 מלאי לדים",
        "🟫 מלאי סנטפים",
        "📦 קליטה מהספק",
        "🚚 העברה לאריזה",
        "🔍 אריזה וניפוק",
        "☠️ מלאי מת",
        "📜 היסטוריה",
        "⚠️ איפוס"
    ]
)

if page == "🏠 דשבורד":
    st.title("🏠 דשבורד ראשי")

    leds = get_df("SELECT * FROM led_inventory")
    santaf = get_df("SELECT * FROM santaf_inventory")
    pergolas = get_df("SELECT * FROM pergola_orders")

    led_pack = int(leds["packing_stock"].sum()) if not leds.empty else 0
    santaf_pack = int(santaf["packing_stock"].sum()) if not santaf.empty else 0
    dead_total = 0
    if not leds.empty:
        dead_total += int(leds["dead_stock"].sum())
    if not santaf.empty:
        dead_total += int(santaf["dead_stock"].sum())

    waiting = 0
    if not pergolas.empty:
        waiting = len(pergolas[pergolas["status"].astype(str).str.contains("ממתין", na=False)])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("לדים באריזה", led_pack)
    c2.metric("סנטפים באריזה", santaf_pack)
    c3.metric("מלאי מת", dead_total)
    c4.metric("הזמנות ממתינות", waiting)

    st.subheader("🚨 הזמנות ממתינות")
    waiting_df = get_df("""
    SELECT order_number, customer_name, packing_date, status
    FROM pergola_orders
    WHERE status LIKE '%ממתין%'
    ORDER BY packing_date
    """)
    show_cards(waiting_df, [
        ("מספר הזמנה", "order_number"),
        ("לקוח", "customer_name"),
        ("תאריך אריזה", "packing_date"),
        ("סטטוס", "status")
    ])

    st.subheader("⚠️ סנטפים מתחת למלאי מינימום")
    low = get_df("""
    SELECT color, length, packing_stock, min_stock
    FROM santaf_inventory
    WHERE packing_stock < min_stock
    ORDER BY color, length
    """)
    show_cards(low, [
        ("צבע", "color"),
        ("אורך", "length"),
        ("מלאי אריזה", "packing_stock"),
        ("מינימום נדרש", "min_stock")
    ])


elif page == "➕ הזמנת פרגולה":
    st.title("➕ הזמנת פרגולה")

    with st.form("add_pergola"):
        order_number = st.text_input("מספר הזמנה")
        customer_name = st.text_input("שם לקוח / הערה")
        packing_date = st.date_input("תאריך אריזה מתוכנן", value=date.today())

        needs_led = st.checkbox("צריך לד?")
        led_type = ""
        led_qty = 0

        if needs_led:
            led_type = st.text_input("סוג לד")
            led_qty = st.number_input("כמות לדים", min_value=1, step=1)

        needs_santaf = st.checkbox("צריך סנטף?")
        santaf_color = ""
        santaf_length = ""
        santaf_qty = 0

        if needs_santaf:
            santaf_color = st.text_input("צבע סנטף")
            santaf_length = st.text_input("אורך סנטף")
            santaf_qty = st.number_input("כמות סנטפים", min_value=1, step=1)

        notes = st.text_area("הערות")
        submit = st.form_submit_button("✅ שמור הזמנה")

    if submit:
        if not order_number.strip():
            st.error("חובה להזין מספר הזמנה.")
        else:
            status = calc_status(int(needs_led), 0, int(needs_santaf), 0)

            try:
                run_query("""
                INSERT INTO pergola_orders
                (order_number, customer_name, packing_date, status,
                 needs_led, led_type, led_qty, led_issued,
                 needs_santaf, santaf_color, santaf_length, santaf_qty, santaf_issued,
                 notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 0, ?, ?, ?)
                """, (
                    order_number.strip(),
                    customer_name.strip(),
                    str(packing_date),
                    status,
                    int(needs_led),
                    led_type.strip(),
                    int(led_qty),
                    int(needs_santaf),
                    santaf_color.strip(),
                    santaf_length.strip(),
                    int(santaf_qty),
                    notes.strip(),
                    now(),
                    now()
                ))
                st.success("✅ ההזמנה נשמרה.")
            except sqlite3.IntegrityError:
                st.error("מספר הזמנה כבר קיים.")


elif page == "💡 מלאי לדים":
    st.title("💡 ניהול מלאי לדים")

    leds = get_df("""
    SELECT led_type, main_stock, packing_stock, dead_stock
    FROM led_inventory
    ORDER BY led_type
    """)

    show_cards(leds, [
        ("סוג לד", "led_type"),
        ("מחסן ראשי", "main_stock"),
        ("מחסן אריזה", "packing_stock"),
        ("מלאי מת", "dead_stock")
    ])


elif page == "🟫 מלאי סנטפים":
    st.title("🟫 ניהול מלאי סנטפים")

    santaf = get_df("""
    SELECT color, length, main_stock, packing_stock, dead_stock, min_stock
    FROM santaf_inventory
    ORDER BY color, length
    """)

    show_cards(santaf, [
        ("צבע", "color"),
        ("אורך", "length"),
        ("מחסן ראשי", "main_stock"),
        ("מחסן אריזה", "packing_stock"),
        ("מלאי מת", "dead_stock"),
        ("מינימום", "min_stock")
    ])


elif page == "📦 קליטה מהספק":
    st.title("📦 קליטה מהספק")

    product = st.radio("בחר מוצר", ["לד", "סנטף"], horizontal=True)

    with st.form("receive"):
        order_number = st.text_input("מספר הזמנה")
        qty = st.number_input("כמות", min_value=1, step=1)

        if product == "לד":
            led_type = st.text_input("סוג לד")
            color = ""
            length = ""
        else:
            led_type = ""
            color = st.text_input("צבע סנטף")
            length = st.text_input("אורך סנטף")

        notes = st.text_area("הערות")
        submit = st.form_submit_button("✅ קלוט למחסן ראשי")

    if submit:
        if not order_number.strip():
            st.error("חובה להזין מספר הזמנה.")
        elif product == "לד" and not led_type.strip():
            st.error("חובה להזין סוג לד.")
        elif product == "סנטף" and (not color.strip() or not length.strip()):
            st.error("חובה להזין צבע ואורך סנטף.")
        else:
            if product == "לד":
                ensure_led(led_type.strip())
                run_query("""
                UPDATE led_inventory
                SET main_stock = main_stock + ?
                WHERE led_type = ?
                """, (int(qty), led_type.strip()))

                add_transaction(order_number, "לד", "קליטה מהספק", led_type, int(qty), "ספק", "מחסן ראשי", "", notes)
            else:
                ensure_santaf(color.strip(), length.strip())
                run_query("""
                UPDATE santaf_inventory
                SET main_stock = main_stock + ?
                WHERE color = ? AND length = ?
                """, (int(qty), color.strip(), length.strip()))

                add_transaction(order_number, "סנטף", "קליטה מהספק", f"{color} | {length}", int(qty), "ספק", "מחסן ראשי", "", notes)

            st.success("✅ נקלט למחסן ראשי.")


elif page == "🚚 העברה לאריזה":
    st.title("🚚 העברה למחסן אריזה")

    product = st.radio("בחר מוצר", ["לד", "סנטף"], horizontal=True)

    if product == "לד":
        inv = get_df("SELECT led_type, main_stock FROM led_inventory WHERE main_stock > 0")
        if inv.empty:
            st.info("אין לדים במחסן ראשי.")
        else:
            selected = st.selectbox("בחר לד", inv["led_type"])
            qty = st.number_input("כמות", min_value=1, step=1)
            order_number = st.text_input("מספר הזמנה / אסמכתא")

            if st.button("🚚 העבר"):
                available = int(inv[inv["led_type"] == selected].iloc[0]["main_stock"])
                if not order_number.strip():
                    st.error("חובה להזין מספר הזמנה.")
                elif qty > available:
                    st.error(f"אין מספיק מלאי. קיים: {available}")
                else:
                    run_query("""
                    UPDATE led_inventory
                    SET main_stock = main_stock - ?, packing_stock = packing_stock + ?
                    WHERE led_type = ?
                    """, (int(qty), int(qty), selected))
                    add_transaction(order_number, "לד", "העברה לאריזה", selected, int(qty), "מחסן ראשי", "מחסן אריזה")
                    st.success("✅ הועבר לאריזה.")

    else:
        inv = get_df("SELECT color, length, main_stock FROM santaf_inventory WHERE main_stock > 0")
        if inv.empty:
            st.info("אין סנטפים במחסן ראשי.")
        else:
            inv["label"] = inv["color"] + " | " + inv["length"] + " | ראשי: " + inv["main_stock"].astype(str)
            selected = st.selectbox("בחר סנטף", inv["label"])
            row = inv[inv["label"] == selected].iloc[0]
            qty = st.number_input("כמות", min_value=1, step=1)
            order_number = st.text_input("מספר הזמנה / אסמכתא")

            if st.button("🚚 העבר"):
                available = int(row["main_stock"])
                if not order_number.strip():
                    st.error("חובה להזין מספר הזמנה.")
                elif qty > available:
                    st.error(f"אין מספיק מלאי. קיים: {available}")
                else:
                    run_query("""
                    UPDATE santaf_inventory
                    SET main_stock = main_stock - ?, packing_stock = packing_stock + ?
                    WHERE color = ? AND length = ?
                    """, (int(qty), int(qty), row["color"], row["length"]))
                    add_transaction(order_number, "סנטף", "העברה לאריזה", f"{row['color']} | {row['length']}", int(qty), "מחסן ראשי", "מחסן אריזה")
                    st.success("✅ הועבר לאריזה.")


elif page == "🔍 אריזה וניפוק":
    st.title("🔍 אריזה וניפוק")

    search = st.text_input("מספר הזמנת פרגולה")

    if search:
        orders = get_df("SELECT * FROM pergola_orders WHERE order_number = ?", (search.strip(),))

        if orders.empty:
            st.error("לא נמצאה הזמנה.")
        else:
            order = orders.iloc[0]

            st.write(f"**מספר הזמנה:** {order['order_number']}")
            st.write(f"**לקוח:** {order['customer_name'] or '-'}")
            st.write(f"**סטטוס:** {order['status']}")
            st.write(f"**תאריך אריזה:** {order['packing_date']}")

            st.divider()

            if int(order["needs_led"]) == 1:
                st.subheader("💡 לד")
                led_type = order["led_type"]
                led_qty = int(order["led_qty"])
                stock = get_df("SELECT packing_stock FROM led_inventory WHERE led_type = ?", (led_type,))
                pack = int(stock.iloc[0]["packing_stock"]) if not stock.empty else 0

                st.write(f"סוג לד: **{led_type}**")
                st.write(f"כמות נדרשת: **{led_qty}**")
                st.write(f"מלאי באריזה: **{pack}**")

                if int(order["led_issued"]) == 1:
                    st.success("לד כבר צורף.")
                elif st.button("✅ נפק לד"):
                    if pack < led_qty:
                        st.error("אין מספיק מלאי לד באריזה.")
                    else:
                        run_query("UPDATE led_inventory SET packing_stock = packing_stock - ? WHERE led_type = ?", (led_qty, led_type))
                        run_query("UPDATE pergola_orders SET led_issued = 1, updated_at = ? WHERE order_number = ?", (now(), order["order_number"]))
                        refreshed = get_df("SELECT * FROM pergola_orders WHERE order_number = ?", (order["order_number"],)).iloc[0]
                        new_status = calc_status(int(refreshed["needs_led"]), 1, int(refreshed["needs_santaf"]), int(refreshed["santaf_issued"]))
                        run_query("UPDATE pergola_orders SET status = ?, updated_at = ? WHERE order_number = ?", (new_status, now(), order["order_number"]))
                        add_transaction(order["order_number"], "לד", "ניפוק לפרגולה", led_type, led_qty, "מחסן אריזה", "פרגולה")
                        st.success("✅ לד נופק.")
                        st.rerun()
            else:
                st.info("להזמנה זו אין לד.")

            st.divider()

            if int(order["needs_santaf"]) == 1:
                st.subheader("🟫 סנטף")
                color = order["santaf_color"]
                length = order["santaf_length"]
                qty = int(order["santaf_qty"])

                stock = get_df("SELECT packing_stock FROM santaf_inventory WHERE color = ? AND length = ?", (color, length))
                pack = int(stock.iloc[0]["packing_stock"]) if not stock.empty else 0

                st.write(f"צבע: **{color}**")
                st.write(f"אורך: **{length}**")
                st.write(f"כמות נדרשת: **{qty}**")
                st.write(f"מלאי באריזה: **{pack}**")

                if int(order["santaf_issued"]) == 1:
                    st.success("סנטף כבר צורף.")
                elif st.button("✅ נפק סנטף"):
                    if pack < qty:
                        st.error("אין מספיק סנטף באריזה.")
                    else:
                        run_query("""
                        UPDATE santaf_inventory
                        SET packing_stock = packing_stock - ?
                        WHERE color = ? AND length = ?
                        """, (qty, color, length))
                        run_query("UPDATE pergola_orders SET santaf_issued = 1, updated_at = ? WHERE order_number = ?", (now(), order["order_number"]))
                        refreshed = get_df("SELECT * FROM pergola_orders WHERE order_number = ?", (order["order_number"],)).iloc[0]
                        new_status = calc_status(int(refreshed["needs_led"]), int(refreshed["led_issued"]), int(refreshed["needs_santaf"]), 1)
                        run_query("UPDATE pergola_orders SET status = ?, updated_at = ? WHERE order_number = ?", (new_status, now(), order["order_number"]))
                        add_transaction(order["order_number"], "סנטף", "ניפוק לפרגולה", f"{color} | {length}", qty, "מחסן אריזה", "פרגולה")
                        st.success("✅ סנטף נופק.")
                        st.rerun()
            else:
                st.info("להזמנה זו אין סנטף.")

            st.divider()

            if st.button("🚛 סמן עלה למרלוג"):
                run_query("UPDATE pergola_orders SET status = ?, updated_at = ? WHERE order_number = ?", ("עלה למרלוג", now(), order["order_number"]))
                add_transaction(order["order_number"], "כללי", "עלה למרלוג", "הזמנת פרגולה", 0, "אריזה", "מרלוג")
                st.success("✅ סומן עלה למרלוג.")
                st.rerun()


elif page == "☠️ מלאי מת":
    st.title("☠️ מלאי מת")

    product = st.radio("בחר מוצר", ["לד", "סנטף"], horizontal=True)
    order_number = st.text_input("מספר הזמנה / אסמכתא")
    qty = st.number_input("כמות", min_value=1, step=1)
    source = st.selectbox("מאיפה להעביר?", ["מחסן ראשי", "מחסן אריזה"])
    reason = st.selectbox("סיבה", DEAD_REASONS)
    notes = st.text_area("הערות")

    if product == "לד":
        inv = get_df("SELECT led_type, main_stock, packing_stock FROM led_inventory")
        if not inv.empty:
            selected = st.selectbox("בחר לד", inv["led_type"])

            if st.button("☠️ העבר למלאי מת"):
                field = "main_stock" if source == "מחסן ראשי" else "packing_stock"
                available = int(inv[inv["led_type"] == selected].iloc[0][field])

                if not order_number.strip():
                    st.error("חובה להזין מספר הזמנה.")
                elif qty > available:
                    st.error(f"אין מספיק מלאי. קיים: {available}")
                else:
                    run_query(f"""
                    UPDATE led_inventory
                    SET {field} = {field} - ?, dead_stock = dead_stock + ?
                    WHERE led_type = ?
                    """, (int(qty), int(qty), selected))
                    add_transaction(order_number, "לד", "העברה למלאי מת", selected, int(qty), source, "מלאי מת", reason, notes)
                    st.success("✅ הועבר למלאי מת.")

    else:
        inv = get_df("SELECT color, length, main_stock, packing_stock FROM santaf_inventory")
        if not inv.empty:
            inv["label"] = inv["color"] + " | " + inv["length"]
            selected = st.selectbox("בחר סנטף", inv["label"])
            row = inv[inv["label"] == selected].iloc[0]

            if st.button("☠️ העבר למלאי מת"):
                field = "main_stock" if source == "מחסן ראשי" else "packing_stock"
                available = int(row[field])

                if not order_number.strip():
                    st.error("חובה להזין מספר הזמנה.")
                elif qty > available:
                    st.error(f"אין מספיק מלאי. קיים: {available}")
                else:
                    run_query(f"""
                    UPDATE santaf_inventory
                    SET {field} = {field} - ?, dead_stock = dead_stock + ?
                    WHERE color = ? AND length = ?
                    """, (int(qty), int(qty), row["color"], row["length"]))
                    add_transaction(order_number, "סנטף", "העברה למלאי מת", f"{row['color']} | {row['length']}", int(qty), source, "מלאי מת", reason, notes)
                    st.success("✅ הועבר למלאי מת.")


elif page == "📜 היסטוריה":
    st.title("📜 היסטוריית תנועות")

    search = st.text_input("חיפוש לפי מספר הזמנה")
    product = st.selectbox("סוג מוצר", ["הכל", "לד", "סנטף", "כללי"])

    query = "SELECT * FROM transactions WHERE 1=1"
    params = []

    if search.strip():
        query += " AND order_number LIKE ?"
        params.append(f"%{search.strip()}%")

    if product != "הכל":
        query += " AND product = ?"
        params.append(product)

    query += " ORDER BY created_at DESC"

    data = get_df(query, tuple(params))

    show_cards(data, [
        ("תאריך", "created_at"),
        ("מספר הזמנה", "order_number"),
        ("מוצר", "product"),
        ("פעולה", "action"),
        ("פירוט", "item_details"),
        ("כמות", "quantity"),
        ("מ־", "from_location"),
        ("אל", "to_location"),
        ("סיבה", "reason"),
        ("הערות", "notes")
    ])


elif page == "⚠️ איפוס":
    st.title("⚠️ איפוס נתונים")

    st.error("זה מוחק את כל הנתונים בגרסה החדשה.")

    confirm = st.checkbox("אני מבין ורוצה למחוק הכל")

    if confirm:
        if st.button("🗑️ אפס נתונים"):
            run_query("DROP TABLE IF EXISTS led_inventory")
            run_query("DROP TABLE IF EXISTS santaf_inventory")
            run_query("DROP TABLE IF EXISTS pergola_orders")
            run_query("DROP TABLE IF EXISTS transactions")
            init_db()
            st.success("✅ אופס.")
            st.rerun()
