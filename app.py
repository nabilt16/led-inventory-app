import streamlit as st
import pandas as pd
from datetime import date, datetime
from supabase import create_client

SUPABASE_URL = "https://gwiieqmawtudoxafnjeg.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imd3aWllcW1hd3R1ZG94YWZuamVnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzc4NzkxMDAsImV4cCI6MjA5MzQ1NTEwMH0.v3fxlk30SHNPqyncYB557lLYsk99D50DGsPhJUNkeLk"

SANTAF_TYPE = "סנטף BH שקוף"
SANTAF_LENGTHS = [1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000, 5500, 6000, 6500, 7000, 7500, 8000]
DEFAULT_MIN_SANTAF = 20

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def today_str():
    return str(date.today())


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_data(response):
    return response.data if response and response.data else []


def get_led_rows():
    return safe_data(
        supabase.table("led_inventory")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )


def get_santaf_rows():
    return safe_data(
        supabase.table("santaf_inventory")
        .select("*")
        .order("length")
        .execute()
    )


def get_santaf_row(length):
    data = safe_data(
        supabase.table("santaf_inventory")
        .select("*")
        .eq("length", length)
        .limit(1)
        .execute()
    )
    return data[0] if data else None


def ensure_santaf_lengths():
    for length in SANTAF_LENGTHS:
        existing = get_santaf_row(length)
        if not existing:
            supabase.table("santaf_inventory").insert({
                "length": length,
                "quantity": 0,
                "min_quantity": DEFAULT_MIN_SANTAF
            }).execute()


def card(title, lines, warning=False):
    cls = "card warn" if warning else "card"
    html = f"<div class='{cls}'><h3>{title}</h3>"
    for line in lines:
        html += f"<div>{line}</div>"
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


st.set_page_config(
    page_title="ניהול לדים וסנטפים",
    page_icon="📦",
    layout="centered"
)

st.markdown("""
<style>
.main .block-container {
    max-width: 720px;
    padding: 12px;
}

body, div, p, span, label {
    direction: rtl;
}

h1, h2, h3 {
    text-align: right;
    line-height: 1.25;
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
    margin-bottom: 12px;
    background: #fafafa;
    text-align: right;
}

.warn {
    border: 2px solid #ff4b4b;
    background: #fff2f2;
}

.small-note {
    color: #666;
    font-size: 14px;
}
</style>
""", unsafe_allow_html=True)

ensure_santaf_lengths()

st.title("📦 מערכת ניהול לדים וסנטפים")

page = st.selectbox(
    "בחר מסך",
    [
        "🏠 דשבורד",
        "💡 קליטת לדים",
        "💡 ניפוק לדים",
        "💡 מלאי לדים",
        "🟫 קליטת סנטפים",
        "🟫 ניפוק סנטפים",
        "🟫 מלאי סנטפים",
        "⚙️ מינימום סנטפים",
        "📊 דוח צריכת סנטפים",
    ],
    key="main_menu"
)

if page == "🏠 דשבורד":
    st.header("🏠 דשבורד")

    led_rows = get_led_rows()
    santaf_rows = get_santaf_rows()

    total_leds = sum(int(r.get("quantity") or 0) for r in led_rows)
    total_santaf = sum(int(r.get("quantity") or 0) for r in santaf_rows)
    low_santaf = [
        r for r in santaf_rows
        if int(r.get("quantity") or 0) < int(r.get("min_quantity") or DEFAULT_MIN_SANTAF)
    ]

    st.metric("💡 סה״כ לדים במלאי", total_leds)
    st.metric("🟫 סה״כ סנטפים במלאי", total_santaf)
    st.metric("⚠️ מידות סנטף מתחת למינימום", len(low_santaf))

    st.subheader("⚠️ סנטפים מתחת למינימום")
    if not low_santaf:
        st.success("כל מידות הסנטף מעל המינימום.")
    else:
        for r in low_santaf:
            card(
                f"{r.get('length')} ממ",
                [
                    f"מלאי נוכחי: {r.get('quantity')}",
                    f"מינימום: {r.get('min_quantity')}",
                ],
                warning=True
            )

elif page == "💡 קליטת לדים":
    st.header("💡 קליטת לדים לפי הזמנה")

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
            supabase.table("led_inventory").insert({
                "order_number": order_number.strip(),
                "led_type": led_type.strip(),
                "quantity": int(quantity)
            }).execute()
            st.success("✅ הלדים נשמרו ב־Supabase.")

elif page == "💡 ניפוק לדים":
    st.header("💡 ניפוק לדים לפרגולה")

    rows = [r for r in get_led_rows() if int(r.get("quantity") or 0) > 0]

    pergola_order = st.text_input("מספר הזמנת פרגולה", key="led_issue_pergola")
    issue_date = st.date_input("תאריך ניפוק", value=date.today(), key="led_issue_date")

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
                new_qty = current_qty - int(issue_qty)
                supabase.table("led_inventory").update({
                    "quantity": new_qty
                }).eq("id", selected["id"]).execute()

                st.success(f"✅ נופקו {issue_qty} לדים להזמנה {pergola_order}.")

elif page == "💡 מלאי לדים":
    st.header("💡 מלאי לדים")

    rows = get_led_rows()

    if not rows:
        st.info("אין נתונים.")
    else:
        for r in rows:
            card(
                f"{r.get('led_type')}",
                [
                    f"מספר הזמנת ספק: {r.get('order_number')}",
                    f"כמות במלאי: {r.get('quantity')}",
                    f"תאריך יצירה: {r.get('created_at')}",
                ]
            )

elif page == "🟫 קליטת סנטפים":
    st.header("🟫 קליטת סנטפים למלאי")

    st.write(f"סוג מוצר: **{SANTAF_TYPE}**")

    supplier_ref = st.text_input("מספר הזמנה / אסמכתא", key="santaf_receive_ref")
    receive_date = st.date_input("תאריך קליטה", value=date.today(), key="santaf_receive_date")
    notes = st.text_area("הערות", key="santaf_receive_notes")

    st.info("הכנס כמות רק במידות שקיבלת. שאר המידות תשאיר 0.")

    qty_by_length = {}
    for length in SANTAF_LENGTHS:
        qty_by_length[length] = st.number_input(
            f"{length} ממ",
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
                st.success(f"✅ נקלטו {total} סנטפים למלאי.")

elif page == "🟫 ניפוק סנטפים":
    st.header("🟫 ניפוק סנטפים לפרגולה")

    pergola_order = st.text_input("מספר הזמנת פרגולה", key="santaf_issue_order")
    issue_date = st.date_input("תאריך ניפוק", value=date.today(), key="santaf_issue_date")
    length = st.selectbox("בחר מידה", SANTAF_LENGTHS, key="santaf_issue_length")

    row = get_santaf_row(length)
    current_qty = int(row.get("quantity") or 0)
    min_qty = int(row.get("min_quantity") or DEFAULT_MIN_SANTAF)

    st.write(f"**מלאי נוכחי:** {current_qty}")
    st.write(f"**מינימום:** {min_qty}")

    issue_qty = st.number_input("כמות לניפוק", min_value=1, value=1, step=1, key="santaf_issue_qty")
    notes = st.text_area("הערות", key="santaf_issue_notes")

    if st.button("✅ נפק סנטף", key="btn_santaf_issue"):
        if not pergola_order.strip():
            st.error("חובה להזין מספר הזמנת פרגולה.")
        elif issue_qty > current_qty:
            st.error(f"אין מספיק מלאי. קיים: {current_qty}")
        else:
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

elif page == "🟫 מלאי סנטפים":
    st.header("🟫 מלאי סנטפים")

    rows = get_santaf_rows()

    for r in rows:
        quantity = int(r.get("quantity") or 0)
        min_quantity = int(r.get("min_quantity") or DEFAULT_MIN_SANTAF)

        card(
            f"{r.get('length')} ממ",
            [
                f"סוג: {SANTAF_TYPE}",
                f"מלאי נוכחי: {quantity}",
                f"מינימום: {min_quantity}",
            ],
            warning=quantity < min_quantity
        )

elif page == "⚙️ מינימום סנטפים":
    st.header("⚙️ הגדרת מינימום לפי מידה")

    rows = get_santaf_rows()

    for r in rows:
        length = int(r.get("length"))
        current_min = int(r.get("min_quantity") or DEFAULT_MIN_SANTAF)

        with st.container(border=True):
            st.subheader(f"{length} ממ")
            new_min = st.number_input(
                f"מינימום למידה {length}",
                min_value=0,
                value=current_min,
                step=1,
                key=f"santaf_min_{length}"
            )

            if st.button(f"💾 שמור מינימום {length}", key=f"btn_save_min_{length}"):
                supabase.table("santaf_inventory").update({
                    "min_quantity": int(new_min)
                }).eq("id", r["id"]).execute()
                st.success("✅ נשמר.")

elif page == "📊 דוח צריכת סנטפים":
    st.header("📊 דוח צריכת סנטפים")

    d1 = st.date_input("מתאריך", value=date.today(), key="report_from")
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
                f"{int(r['length'])} ממ",
                [
                    f"כמות: {int(r['quantity'])}",
                    f"אחוז מכלל הצריכה: {percent:.1f}%",
                ]
            )
