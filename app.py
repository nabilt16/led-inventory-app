import streamlit as st
import pandas as pd
import sqlite3

DB = "inventory.db"

def conn():
    return sqlite3.connect(DB, check_same_thread=False)

def run_query(query, params=()):
    c = conn()
    cur = c.cursor()
    cur.execute(query, params)
    c.commit()
    c.close()

def get_df(query):
    c = conn()
    data = pd.read_sql_query(query, c)
    c.close()
    return data

def init_db():
    run_query("CREATE TABLE IF NOT EXISTS inventory (led_type TEXT UNIQUE, main INT, pack INT)")
    run_query("CREATE TABLE IF NOT EXISTS pergola (order_no TEXT, led_type TEXT, qty INT, status TEXT)")

init_db()

st.title("💡 ניהול מלאי לדים")

menu = st.sidebar.radio("תפריט", ["דשבורד", "קליטה", "העברה", "אריזה"])

if menu == "דשבורד":
    data = get_df("SELECT * FROM inventory")
    st.dataframe(data)

elif menu == "קליטה":
    led = st.text_input("סוג לד")
    qty = st.number_input("כמות", 1)

    if st.button("הוסף"):
        run_query("INSERT OR IGNORE INTO inventory VALUES (?,0,0)", (led,))
        run_query("UPDATE inventory SET main = main + ? WHERE led_type = ?", (qty, led))
        st.success("נוסף למחסן ראשי")

elif menu == "העברה":
    data = get_df("SELECT led_type FROM inventory")

    if not data.empty:
        led = st.selectbox("בחר לד", data["led_type"])
        qty = st.number_input("כמות להעברה", 1)

        if st.button("העבר"):
            run_query("UPDATE inventory SET main = main - ?, pack = pack + ? WHERE led_type = ?", (qty, qty, led))
            st.success("עבר למחסן אריזה")

elif menu == "אריזה":
    order = st.text_input("מספר הזמנה")
    led = st.text_input("סוג לד")
    qty = st.number_input("כמות", 1)

    if st.button("אשר שימוש"):
        run_query("UPDATE inventory SET pack = pack - ? WHERE led_type = ?", (qty, led))
        run_query("INSERT INTO pergola VALUES (?,?,?,?)", (order, led, qty, "בוצע"))
        st.success("אושר שימוש")
