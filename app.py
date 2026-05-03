import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, date

DB = "inventory.db"

PRODUCT_TYPES = ["לד", "סנטף"]

LOCATIONS = {
    "main": "מחסן ראשי",
    "packing": "מחסן אריזה",
    "dead": "מלאי מת"
}

PERGOLA_STATUSES = [
    "בייצור",
    "מוכן לאריזה",
    "באריזה",
    "ממתין ללד",
    "ממתין לסנטף",
    "ממתין ללד וסנטף",
    "לדים צורפו",
    "סנטף צורף",
    "מוכן למרלוג",
    "עלה למרלוג",
    "בוטל"
]

SUPPLIER_STATUSES = [
    "הוזמן מהספק",
    "התקבל במחסן ראשי",
    "עבר למחסן אריזה",
    "נופק",
    "נסגר"
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
    CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_type TEXT NOT NULL,
        item_name TEXT NOT NULL,
        color_or_type TEXT,
        length TEXT,
        main_stock INTEGER DEFAULT 0,
        packing_stock INTEGER DEFAULT 0,
        dead_stock INTEGER DEFAULT 0,
        UNIQUE(product_type, item_name, color_or_type, length)
    )
    """)

    run_query("""
    CREATE TABLE IF NOT EXISTS supplier_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        supplier_order_number TEXT NOT NULL,
        product_type TEXT NOT NULL,
        item_name TEXT NOT NULL,
        color_or_type TEXT,
        length TEXT,
        quantity INTEGER NOT NULL,
        order_date TEXT,
        received_date TEXT,
        status TEXT,
        notes TEXT,
        created_at TEXT
    )
    """)

    run_query("""
    CREATE TABLE IF NOT EXISTS pergola_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pergola_order_number TEXT UNIQUE NOT NULL,
        customer_name TEXT,
        packing_date TEXT,
        status TEXT,
        needs_led INTEGER DEFAULT 0,
        led_item TEXT,
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
        action_type TEXT NOT NULL,
        order_number TEXT,
        product_type TEXT NOT NULL,
        item_name TEXT NOT NULL,
        color_or_type TEXT,
        length TEXT,
        quantity INTEGER NOT NULL,
        from_location TEXT,
        to_location TEXT,
        status_after TEXT,
        reason TEXT,
        notes TEXT,
        created_at TEXT
    )
    """)


def ensure_inventory(product_type, item_name, color_or_type="", length=""):
    run_query("""
    INSERT OR IGNORE INTO inventory
    (product_type, item_name, color_or_type, length, main_stock, packing_stock, dead_stock)
    VALUES (?, ?, ?, ?, 0, 0, 0)
    """, (product_type, item_name, color_or_type, length))


def add_transaction(action_type, order_number, product_type, item_name, color_or_type, length,
                    quantity, from_location, to_location, status_after="", reason="", notes=""):
    run_query("""
    INSERT INTO transactions
    (action_type, order_number, product_type, item_name, color_or_type, length,
     quantity, from_location, to_location, status_after, reason, notes, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        action_type, order_number, product_type, item_name, color_or_type, length,
        quantity, from_location, to_location, status_after, reason, notes, now()
    ))


def get_stock(product_type, item_name, color_or_type="", length=""):
    df = get_df("""
    SELECT main_stock, packing_stock, dead_stock
    FROM inventory
    WHERE product_type = ? AND item_name = ? AND color_or_type = ? AND length = ?
    """, (product_type, item_name, color_or_type, length))

    if df.empty:
        return 0, 0, 0

    row = df.iloc[0]
    return int(row["main_stock"]), int(row["packing_stock"]), int(row["dead_stock"])


def receive_supplier_order(supplier_order_number, product_type, item_name, color_or_type,
                           length, quantity, order_date, received_date, notes):
    ensure_inventory(product_type, item_name, color_or_type, length)

    run_query("""
    INSERT INTO supplier_orders
    (supplier_order_number, product_type, item_name, color_or_type, length, quantity,
     order_date, received_date, status, notes, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        supplier_order_number, product_type, item_name, color_or_type, length, quantity,
        str(order_date), str(received_date), "התקבל במחסן ראשי", notes, now()
    ))

    run_query("""
    UPDATE inventory
    SET main_stock = main_stock + ?
    WHERE product_type = ? AND item_name = ? AND color_or_type = ? AND length = ?
    """, (quantity, product_type, item_name, color_or_type, length))

    add_transaction(
        "קליטה מהספק",
        supplier_order_number,
        product_type,
        item_name,
        color_or_type,
        length,
        quantity,
        "ספק",
        "מחסן ראשי",
        "התקבל במחסן ראשי",
        "",
        notes
    )


def transfer_to_packing(order_number, product_type, item_name, color_or_type, length, quantity, notes):
    ensure_inventory(product_type, item_name, color_or_type, length)
    main_stock, packing_stock, dead_stock = get_stock(product_type, item_name, color_or_type, length)

    if quantity <= 0:
        return False, "הכמות חייבת להיות גדולה מ־0."

    if main_stock < quantity:
        return False, f"אין מספיק מלאי במחסן ראשי. קיים: {main_stock}"

    run_query("""
    UPDATE inventory
    SET main_stock = main_stock - ?,
        packing_stock = packing_stock + ?
    WHERE product_type = ? AND item_name = ? AND color_or_type = ? AND length = ?
    """, (quantity, quantity, product_type, item_name, color_or_type, length))

    add_transaction(
        "העברה למחסן אריזה",
        order_number,
        product_type,
        item_name,
        color_or_type,
        length,
        quantity,
        "מחסן ראשי",
        "מחסן אריזה",
        "עבר למחסן אריזה",
        "",
        notes
    )

    return True, "הועבר למחסן אריזה בהצלחה."


def move_to_dead_stock(order_number, product_type, item_name, color_or_type, length,
                       quantity, source_location, reason, notes):
    ensure_inventory(product_type, item_name, color_or_type, length)
    main_stock, packing_stock, dead_stock = get_stock(product_type, item_name, color_or_type, length)

    if source_location == "מחסן ראשי":
        field = "main_stock"
        available = main_stock
    else:
        field = "packing_stock"
        available = packing_stock

    if quantity <= 0:
        return False, "הכמות חייבת להיות גדולה מ־0."

    if available < quantity:
        return False, f"אין מספיק מלאי במיקום שנבחר. קיים: {available}"

    run_query(f"""
    UPDATE inventory
    SET {field} = {field} - ?,
        dead_stock = dead_stock + ?
    WHERE product_type = ? AND item_name = ? AND color_or_type = ? AND length = ?
    """, (quantity, quantity, product_type, item_name, color_or_type, length))

    add_transaction(
        "העברה למלאי מת",
        order_number,
        product_type,
        item_name,
        color_or_type,
        length,
        quantity,
        source_location,
        "מלאי מת",
        "מלאי מת",
        reason,
        notes
    )

    return True, "הועבר למלאי מת בהצלחה."


def calculate_status(needs_led, led_issued, needs_santaf, santaf_issued):
    missing_led = bool(needs_led) and not bool(led_issued)
    missing_santaf = bool(needs_santaf) and not bool(santaf_issued)

    if missing_led and missing_santaf:
        return "ממתין ללד וסנטף"
    if missing_led:
        return "ממתין ללד"
    if missing_santaf:
        return "ממתין לסנטף"
    return "מוכן למרלוג"


def issue_for_pergola(pergola_order_number, product_type):
    order_df = get_df("""
    SELECT *
    FROM pergola_orders
    WHERE pergola_order_number = ?
    """, (pergola_order_number,))

    if order_df.empty:
        return False, "לא נמצאה הזמנת פרגולה."

    order = order_df.iloc[0]

    if product_type == "לד":
        if int(order["needs_led"]) == 0:
            return False, "להזמנה זו לא נדרש לד."

        if int(order["led_issued"]) == 1:
            return False, "לד כבר צורף להזמנה זו."

        item_name = order["led_item"] or "לד"
        color_or_type = order["led_type"] or ""
        length = ""
        qty = int(order["led_qty"])

        if qty <= 0:
            return False, "כמות הלדים בהזמנה לא תקינה."

        main_stock, packing_stock, dead_stock = get_stock("לד", item_name, color_or_type, length)

        if packing_stock < qty:
            new_status = calculate_status(1, 0, int(order["needs_santaf"]), int(order["santaf_issued"]))
            run_query("""
            UPDATE pergola_orders
            SET status = ?, updated_at = ?
            WHERE pergola_order_number = ?
            """, (new_status, now(), pergola_order_number))

            return False, f"אין מספיק לדים במחסן אריזה. נדרש: {qty}, קיים: {packing_stock}. ההזמנה סומנה כממתינה."

        run_query("""
        UPDATE inventory
        SET packing_stock = packing_stock - ?
        WHERE product_type = ? AND item_name = ? AND color_or_type = ? AND length = ?
        """, (qty, "לד", item_name, color_or_type, length))

        run_query("""
        UPDATE pergola_orders
        SET led_issued = 1,
            status = ?,
            updated_at = ?
        WHERE pergola_order_number = ?
        """, (
            calculate_status(1, 1, int(order["needs_santaf"]), int(order["santaf_issued"])),
            now(),
            pergola_order_number
        ))

        add_transaction(
            "ניפוק לד לפרגולה",
            pergola_order_number,
            "לד",
            item_name,
            color_or_type,
            length,
            qty,
            "מחסן אריזה",
            "פרגולה",
            "לד צורף",
            "",
            "ניפוק אוטומטי ממסך אריזה"
        )

        return True, "לד צורף לפרגולה והמלאי עודכן."

    if product_type == "סנטף":
        if int(order["needs_santaf"]) == 0:
            return False, "להזמנה זו לא נדרש סנטף."

        if int(order["santaf_issued"]) == 1:
            return False, "סנטף כבר צורף להזמנה זו."

        item_name = "סנטף"
        color_or_type = order["santaf_color"] or ""
        length = order["santaf_length"] or ""
        qty = int(order["santaf_qty"])

        if qty <= 0:
            return False, "כמות הסנטפים בהזמנה לא תקינה."

        main_stock, packing_stock, dead_stock = get_stock("סנטף", item_name, color_or_type, length)

        if packing_stock < qty:
            new_status = calculate_status(int(order["needs_led"]), int(order["led_issued"]), 1, 0)
            run_query("""
            UPDATE pergola_orders
            SET status = ?, updated_at = ?
            WHERE pergola_order_number = ?
            """, (new_status, now(), pergola_order_number))

            return False, f"אין מספיק סנטפים במחסן אריזה. נדרש: {qty}, קיים: {packing_stock}. ההזמנה סומנה כממתינה."

        run_query("""
        UPDATE inventory
        SET packing_stock = packing_stock - ?
        WHERE product_type = ? AND item_name = ? AND color_or_type = ? AND length = ?
        """, (qty, "סנטף", item_name, color_or_type, length))

        run_query("""
        UPDATE pergola_orders
        SET santaf_issued = 1,
            status = ?,
            updated_at = ?
        WHERE pergola_order_number = ?
        """, (
            calculate_status(int(order["needs_led"]), int(order["led_issued"]), 1, 1),
            now(),
            pergola_order_number
        ))

        add_transaction(
            "ניפוק סנטף לפרגולה",
            pergola_order_number,
            "סנטף",
            item_name,
            color_or_type,
            length,
            qty,
            "מחסן אריזה",
            "פרגולה",
            "סנטף צורף",
            "",
            "ניפוק אוטומטי ממסך אריזה"
        )

        return True, "סנטף צורף לפרגולה והמלאי עודכן."

    return False, "סוג מוצר לא תקין."


init_db()

st.set_page_config(
    page_title="ניהול לדים וסנטפים",
    page_icon="🏗️",
    layout="wide"
)

st.markdown("""
<style>
html, body, [class*="css"] {
    direction: rtl;
    text-align: right;
}
.stButton button {
    width: 100%;
    height: 56px;
    font-size: 20px;
    font-weight: bold;
    border-radius: 14px;
}
div[data-testid="stMetric"] {
    background-color: #f7f7f7;
    padding: 16px;
    border-radius: 14px;
    border: 1px solid #ddd;
}
</style>
""", unsafe_allow_html=True)

st.sidebar.title("🏗️ ניהול לדים וסנטפים")

page = st.sidebar.radio(
    "תפריט",
    [
        "🏠 דשבורד",
        "➕ הזמנת פרגולה",
        "📦 הזמנת/קליטת מוצר מהספק",
        "🚚 העברה למחסן אריזה",
        "🔍 מסך אריזה וניפוק",
        "☠️ מלאי מת",
        "📜 היסטוריית תנועות",
        "📊 דוחות מלאי",
        "⚠️ איפוס נתונים"
    ]
)

if page == "🏠 דשבורד":
    st.title("🏠 דשבורד")

    inv = get_df("SELECT * FROM inventory")
    pergolas = get_df("SELECT * FROM pergola_orders")

    total_main = int(inv["main_stock"].sum()) if not inv.empty else 0
    total_packing = int(inv["packing_stock"].sum()) if not inv.empty else 0
    total_dead = int(inv["dead_stock"].sum()) if not inv.empty else 0
    waiting = len(pergolas[pergolas["status"].str.contains("ממתין", na=False)]) if not pergolas.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("מחסן ראשי", total_main)
    c2.metric("מחסן אריזה", total_packing)
    c3.metric("מלאי מת", total_dead)
    c4.metric("הזמנות ממתינות", waiting)

    st.subheader("🚨 הזמנות שממתינות למוצר")
    waiting_df = get_df("""
    SELECT pergola_order_number, customer_name, packing_date, status,
           needs_led, led_type, led_qty, needs_santaf, santaf_color, santaf_length, santaf_qty
    FROM pergola_orders
    WHERE status LIKE '%ממתין%'
    ORDER BY packing_date
    """)
    st.dataframe(waiting_df, use_container_width=True, hide_index=True)

    st.subheader("📦 מלאי נוכחי")
    st.dataframe(inv, use_container_width=True, hide_index=True)


elif page == "➕ הזמנת פרגולה":
    st.title("➕ הוספת הזמנת פרגולה")

    with st.form("add_pergola"):
        order_number = st.text_input("מספר הזמנת פרגולה")
        customer_name = st.text_input("שם לקוח / הערה קצרה")
        packing_date = st.date_input("תאריך אריזה מתוכנן", value=date.today())

        st.divider()

        needs_led = st.checkbox("צריך לדים?")
        led_item = ""
        led_type = ""
        led_qty = 0

        if needs_led:
            led_item = st.text_input("שם מוצר לד", value="לד")
            led_type = st.text_input("סוג לד / צבע / דגם")
            led_qty = st.number_input("כמות לדים", min_value=1, step=1)

        st.divider()

        needs_santaf = st.checkbox("צריך סנטף?")
        santaf_color = ""
        santaf_length = ""
        santaf_qty = 0

        if needs_santaf:
            santaf_color = st.text_input("צבע סנטף")
            santaf_length = st.text_input("אורך סנטף")
            santaf_qty = st.number_input("כמות סנטפים לאורך הזה", min_value=1, step=1)

        notes = st.text_area("הערות")
        submit = st.form_submit_button("✅ שמור הזמנת פרגולה")

    if submit:
        if not order_number.strip():
            st.error("חובה להזין מספר הזמנת פרגולה.")
        else:
            initial_status = calculate_status(
                int(needs_led), 0,
                int(needs_santaf), 0
            )

            if not needs_led and not needs_santaf:
                initial_status = "מוכן למרלוג"

            try:
                run_query("""
                INSERT INTO pergola_orders
                (pergola_order_number, customer_name, packing_date, status,
                 needs_led, led_item, led_type, led_qty, led_issued,
                 needs_santaf, santaf_color, santaf_length, santaf_qty, santaf_issued,
                 notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 0, ?, ?, ?)
                """, (
                    order_number.strip(),
                    customer_name.strip(),
                    str(packing_date),
                    initial_status,
                    int(needs_led),
                    led_item.strip(),
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
                st.success("✅ הזמנת הפרגולה נשמרה.")
            except sqlite3.IntegrityError:
                st.error("❌ מספר הזמנה זה כבר קיים.")


elif page == "📦 הזמנת/קליטת מוצר מהספק":
    st.title("📦 הזמנת/קליטת מוצר מהספק")

    with st.form("supplier_receive"):
        supplier_order_number = st.text_input("מספר הזמנה")
        product_type = st.selectbox("סוג מוצר", PRODUCT_TYPES)

        if product_type == "לד":
            item_name = st.text_input("שם מוצר", value="לד")
            color_or_type = st.text_input("סוג לד / צבע / דגם")
            length = ""
        else:
            item_name = st.text_input("שם מוצר", value="סנטף")
            color_or_type = st.text_input("צבע סנטף")
            length = st.text_input("אורך סנטף")

        quantity = st.number_input("כמות", min_value=1, step=1)
        order_date = st.date_input("תאריך הזמנה מהספק", value=date.today())
        received_date = st.date_input("תאריך קבלה למחסן", value=date.today())
        notes = st.text_area("הערות")

        submit = st.form_submit_button("✅ קלוט למחסן ראשי")

    if submit:
        if not supplier_order_number.strip():
            st.error("חובה להזין מספר הזמנה.")
        elif not color_or_type.strip():
            st.error("חובה להזין סוג/צבע.")
        else:
            receive_supplier_order(
                supplier_order_number.strip(),
                product_type,
                item_name.strip(),
                color_or_type.strip(),
                length.strip(),
                int(quantity),
                order_date,
                received_date,
                notes.strip()
            )
            st.success("✅ המוצר נקלט למחסן ראשי ונרשם בהיסטוריה.")


elif page == "🚚 העברה למחסן אריזה":
    st.title("🚚 העברה למחסן אריזה")

    inv = get_df("""
    SELECT product_type, item_name, color_or_type, length, main_stock, packing_stock
    FROM inventory
    WHERE main_stock > 0
    ORDER BY product_type, item_name
    """)

    if inv.empty:
        st.warning("אין מלאי זמין במחסן ראשי.")
    else:
        inv["label"] = (
            inv["product_type"] + " | " +
            inv["item_name"] + " | " +
            inv["color_or_type"].fillna("") + " | " +
            inv["length"].fillna("") + " | ראשי: " +
            inv["main_stock"].astype(str)
        )

        selected = st.selectbox("בחר מוצר להעברה", inv["label"])
        row = inv[inv["label"] == selected].iloc[0]

        order_number = st.text_input("מספר הזמנה / אסמכתא")
        quantity = st.number_input("כמות להעברה", min_value=1, step=1)
        notes = st.text_area("הערות")

        if st.button("🚚 העבר למחסן אריזה"):
            if not order_number.strip():
                st.error("חובה להזין מספר הזמנה / אסמכתא.")
            else:
                ok, msg = transfer_to_packing(
                    order_number.strip(),
                    row["product_type"],
                    row["item_name"],
                    row["color_or_type"] or "",
                    row["length"] or "",
                    int(quantity),
                    notes.strip()
                )
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)


elif page == "🔍 מסך אריזה וניפוק":
    st.title("🔍 מסך אריזה וניפוק")

    search = st.text_input("חיפוש לפי מספר הזמנת פרגולה")

    if search:
        orders = get_df("""
        SELECT *
        FROM pergola_orders
        WHERE pergola_order_number LIKE ?
        ORDER BY created_at DESC
        """, (f"%{search.strip()}%",))

        if orders.empty:
            st.error("לא נמצאה הזמנה.")
        else:
            order = orders.iloc[0]

            st.subheader("📋 פרטי הזמנה")
            c1, c2, c3 = st.columns(3)
            c1.metric("מספר הזמנה", order["pergola_order_number"])
            c2.metric("לקוח", order["customer_name"] or "-")
            c3.metric("סטטוס", order["status"])

            st.write("תאריך אריזה:", order["packing_date"])

            st.divider()

            if int(order["needs_led"]) == 1:
                st.subheader("💡 לדים")
                led_main, led_pack, led_dead = get_stock(
                    "לד",
                    order["led_item"] or "לד",
                    order["led_type"] or "",
                    ""
                )

                c1, c2, c3 = st.columns(3)
                c1.metric("סוג לד", order["led_type"] or "-")
                c2.metric("כמות נדרשת", int(order["led_qty"]))
                c3.metric("מלאי באריזה", led_pack)

                if int(order["led_issued"]) == 1:
                    st.success("✅ לד כבר צורף.")
                else:
                    if st.button("✅ צרף לד לפרגולה"):
                        ok, msg = issue_for_pergola(order["pergola_order_number"], "לד")
                        if ok:
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)
            else:
                st.info("להזמנה זו לא נדרש לד.")

            st.divider()

            if int(order["needs_santaf"]) == 1:
                st.subheader("🟫 סנטף")
                santaf_main, santaf_pack, santaf_dead = get_stock(
                    "סנטף",
                    "סנטף",
                    order["santaf_color"] or "",
                    order["santaf_length"] or ""
                )

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("צבע", order["santaf_color"] or "-")
                c2.metric("אורך", order["santaf_length"] or "-")
                c3.metric("כמות", int(order["santaf_qty"]))
                c4.metric("מלאי באריזה", santaf_pack)

                if int(order["santaf_issued"]) == 1:
                    st.success("✅ סנטף כבר צורף.")
                else:
                    if st.button("✅ צרף סנטף לפרגולה"):
                        ok, msg = issue_for_pergola(order["pergola_order_number"], "סנטף")
                        if ok:
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)
            else:
                st.info("להזמנה זו לא נדרש סנטף.")

            st.divider()

            if st.button("🚛 סמן עלה למרלוג"):
                run_query("""
                UPDATE pergola_orders
                SET status = ?, updated_at = ?
                WHERE pergola_order_number = ?
                """, ("עלה למרלוג", now(), order["pergola_order_number"]))

                add_transaction(
                    "הזמנה עלתה למרלוג",
                    order["pergola_order_number"],
                    "לד",
                    "כללי",
                    "",
                    "",
                    0,
                    "אריזה",
                    "מרלוג",
                    "עלה למרלוג",
                    "",
                    "עדכון סטטוס הזמנת פרגולה"
                )

                st.success("✅ ההזמנה סומנה כעלתה למרלוג.")
                st.rerun()


elif page == "☠️ מלאי מת":
    st.title("☠️ העברה למלאי מת")

    inv = get_df("""
    SELECT product_type, item_name, color_or_type, length, main_stock, packing_stock, dead_stock
    FROM inventory
    WHERE main_stock > 0 OR packing_stock > 0
    ORDER BY product_type, item_name
    """)

    if inv.empty:
        st.info("אין מלאי שניתן להעביר למלאי מת.")
    else:
        inv["label"] = (
            inv["product_type"] + " | " +
            inv["item_name"] + " | " +
            inv["color_or_type"].fillna("") + " | " +
            inv["length"].fillna("") + " | ראשי: " +
            inv["main_stock"].astype(str) + " | אריזה: " +
            inv["packing_stock"].astype(str)
        )

        selected = st.selectbox("בחר מוצר", inv["label"])
        row = inv[inv["label"] == selected].iloc[0]

        order_number = st.text_input("מספר הזמנה / אסמכתא")
        source_location = st.selectbox("מאיפה להעביר?", ["מחסן ראשי", "מחסן אריזה"])
        quantity = st.number_input("כמות להעברה למלאי מת", min_value=1, step=1)
        reason = st.selectbox("סיבה", DEAD_REASONS)
        notes = st.text_area("הערות")

        if st.button("☠️ העבר למלאי מת"):
            if not order_number.strip():
                st.error("חובה להזין מספר הזמנה / אסמכתא.")
            else:
                ok, msg = move_to_dead_stock(
                    order_number.strip(),
                    row["product_type"],
                    row["item_name"],
                    row["color_or_type"] or "",
                    row["length"] or "",
                    int(quantity),
                    source_location,
                    reason,
                    notes.strip()
                )
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)


elif page == "📜 היסטוריית תנועות":
    st.title("📜 היסטוריית תנועות וניפוקים")

    search_order = st.text_input("חיפוש לפי מספר הזמנה")
    product_filter = st.selectbox("סינון לפי מוצר", ["הכל", "לד", "סנטף"])

    query = """
    SELECT created_at, order_number, action_type, product_type, item_name,
           color_or_type, length, quantity, from_location, to_location,
           status_after, reason, notes
    FROM transactions
    WHERE 1=1
    """
    params = []

    if search_order.strip():
        query += " AND order_number LIKE ?"
        params.append(f"%{search_order.strip()}%")

    if product_filter != "הכל":
        query += " AND product_type = ?"
        params.append(product_filter)

    query += " ORDER BY created_at DESC"

    history = get_df(query, tuple(params))
    st.dataframe(history, use_container_width=True, hide_index=True)


elif page == "📊 דוחות מלאי":
    st.title("📊 דוחות מלאי")

    tab1, tab2, tab3 = st.tabs(["מלאי נוכחי", "הזמנות פרגולה", "הזמנות ספק"])

    with tab1:
        inv = get_df("""
        SELECT product_type, item_name, color_or_type, length,
               main_stock, packing_stock, dead_stock
        FROM inventory
        ORDER BY product_type, item_name
        """)
        st.dataframe(inv, use_container_width=True, hide_index=True)

    with tab2:
        pergolas = get_df("""
        SELECT pergola_order_number, customer_name, packing_date, status,
               needs_led, led_type, led_qty, led_issued,
               needs_santaf, santaf_color, santaf_length, santaf_qty, santaf_issued,
               notes, created_at, updated_at
        FROM pergola_orders
        ORDER BY packing_date DESC
        """)
        st.dataframe(pergolas, use_container_width=True, hide_index=True)

    with tab3:
        supplier = get_df("""
        SELECT supplier_order_number, product_type, item_name, color_or_type, length,
               quantity, order_date, received_date, status, notes, created_at
        FROM supplier_orders
        ORDER BY created_at DESC
        """)
        st.dataframe(supplier, use_container_width=True, hide_index=True)


elif page == "⚠️ איפוס נתונים":
    st.title("⚠️ איפוס נתונים")

    st.error("פעולה זו מוחקת את כל הנתונים: מלאי, הזמנות, היסטוריה וניפוקים.")

    confirm = st.checkbox("אני מבין שכל הנתונים יימחקו")

    if confirm:
        if st.button("🗑️ אפס הכל"):
            run_query("DROP TABLE IF EXISTS transactions")
            run_query("DROP TABLE IF EXISTS pergola_orders")
            run_query("DROP TABLE IF EXISTS supplier_orders")
            run_query("DROP TABLE IF EXISTS inventory")
            init_db()
            st.success("✅ הנתונים אופסו.")
            st.rerun()
