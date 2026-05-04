import streamlit as st
from supabase import create_client, Client
import pandas as pd

# ==============================
# 🔑 הגדרות Supabase
# ==============================

SUPABASE_URL = "https://gwiieqmawtudoxafnjeg.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imd3aWllcW1hd3R1ZG94YWZuamVnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzc4NzkxMDAsImV4cCI6MjA5MzQ1NTEwMH0.v3fxlk30SHNPqyncYB557lLYsk99D50DGsPhJUNkeLk"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

st.set_page_config(page_title="ניהול לדים וסנטפים", layout="wide")

st.title("📦 מערכת ניהול לדים וסנטפים")

# ==============================
# 📦 לשוניות
# ==============================

tab1, tab2 = st.tabs(["💡 לדים", "🏗️ סנטפים"])

# ==============================
# 💡 לדים
# ==============================

with tab1:
    st.header("💡 ניהול לדים")

    st.subheader("➕ קליטת לדים")

    order_number = st.text_input("מספר הזמנה")
    led_type = st.text_input("סוג לד")
    quantity = st.number_input("כמות", min_value=1, value=1)

    if st.button("הוסף למלאי לדים"):
        data = {
            "order_number": order_number,
            "led_type": led_type,
            "quantity": quantity
        }

        supabase.table("led_inventory").insert(data).execute()
        st.success("נשמר בהצלחה")

    st.subheader("📊 מלאי לדים")

    res = supabase.table("led_inventory").select("*").execute()
    df = pd.DataFrame(res.data)

    if not df.empty:
        st.dataframe(df, use_container_width=True)
    else:
        st.info("אין נתונים")

# ==============================
# 🏗️ סנטפים
# ==============================

with tab2:
    st.header("🏗️ ניהול סנטפים")

    lengths = [
        1500,2000,2500,3000,3500,4000,
        4500,5000,5500,6000,6500,7000,7500,8000
    ]

    st.subheader("➕ קליטה למלאי")

    length = st.selectbox("אורך", lengths)
    quantity = st.number_input("כמות", min_value=1, value=1)

    if st.button("הוסף סנטף למלאי"):
        data = {
            "length": length,
            "quantity": quantity
        }

        supabase.table("santaf_inventory").insert(data).execute()
        st.success("נשמר בהצלחה")

    st.subheader("📊 מלאי סנטפים")

    res = supabase.table("santaf_inventory").select("*").execute()
    df = pd.DataFrame(res.data)

    if not df.empty:
        st.dataframe(df, use_container_width=True)
    else:
        st.info("אין נתונים")
