# -*- coding: utf-8 -*-

import streamlit as st
import pandas as pd
import sqlite3

DB = "inventory.db"

def conn():
return sqlite3.connect(DB, check_same_thread=False)

def q(query, params=()):
c = conn()
cur = c.cursor()
cur.execute(query, params)
c.commit()
c.close()

def df(query):
c = conn()
data = pd.read_sql_query(query, c)
c.close()
return data

def init():
q("CREATE TABLE IF NOT EXISTS inventory (led_type TEXT UNIQUE, main INT, pack INT)")
q("CREATE TABLE IF NOT EXISTS pergola (order_no TEXT, led_type TEXT, qty INT, status TEXT)")

init()

st.title("💡 ניהול לדים")

menu = st.sidebar.radio("תפריט", ["דשבורד", "קליטה", "העברה", "אריזה"])

if menu == "דשבורד":
data = df("SELECT * FROM inventory")
st.dataframe(data)

elif menu == "קליטה":
led = st.text_input("סוג לד")
qty = st.number_input("כמות", 1)
if st.button("הוסף"):
q("INSERT OR IGNORE INTO inventory VALUES (?,0,0)", (led,))
q("UPDATE inventory SET main = main + ? WHERE led_type = ?", (qty, led))
st.success("נוסף למחסן ראשי")

elif menu == "העברה":
data = df("SELECT led_type FROM inventory")
if not data.empty:
led = st.selectbox("בחר לד", data["led_type"])
qty = st.number_input("כמות להעברה", 1)
if st.button("העבר"):
q("UPDATE inventory SET main = main - ?, pack = pack + ? WHERE led_type = ?", (qty, qty, led))
st.success("עבר לאריזה")

elif menu == "אריזה":
order = st.text_input("מספר הזמנה")
led = st.text_input("סוג לד")
qty = st.number_input("כמות", 1)
if st.button("אשר שימוש"):
q("UPDATE inventory SET pack = pack - ? WHERE led_type = ?", (qty, led))
q("INSERT INTO pergola VALUES (?,?,?,?)", (order, led, qty, "בוצע"))
st.success("אושר שימוש")
