"""Streamlit page: manual order-line entry, search, and warehouse receipt."""
from collections import OrderedDict
from datetime import date

import streamlit as st

import order_service as svc

CAT_ICON = {"LED": "💡", "SANTAF": "🟫"}
STATUS_ICON = {"ordered": "🕓", "received": "✅", "cancelled": "🚫"}


def _fmt_length(line):
    mm = line.get("length_mm") or 0
    if line.get("category") == "LED":
        return f"{mm / 1000:g} מ'"
    return f"{mm} מ\"מ"


def _fmt_date(value):
    if not value:
        return "-"
    y, m, d = str(value)[:10].split("-")
    return f"{d}/{m}/{y}"


def _line_summary(line):
    parts = [
        f"{CAT_ICON.get(line['category'], '')} **{svc.CATEGORIES.get(line['category'], '')}**",
        f"{line.get('item_spec') or '-'}",
        f"אורך: {_fmt_length(line)}",
        f"כמות מוזמנת: **{line['quantity']}**",
        f"{STATUS_ICON.get(line['status'], '')} {svc.STATUS_LABELS.get(line['status'], line['status'])}",
    ]
    return " | ".join(parts)


def _line_details(line):
    bits = []
    if line.get("supplier_order_ref"):
        bits.append(f"הזמנת ספק: {line['supplier_order_ref']}")
    if line.get("supplier_order_date"):
        bits.append(f"תאריך הזמנה: {_fmt_date(line['supplier_order_date'])}")
    if line.get("ordered_by"):
        bits.append(f"הוזמן ע\"י: {line['ordered_by']}")
    bits.append(f"תיאום ללקוח: {_fmt_date(line.get('coordination_date'))}")
    if line["status"] == "received":
        if line.get("stock_applied"):
            bits.append(
                f"נקלט למלאי: {line.get('received_qty')} ב-{_fmt_date(line.get('received_date'))}, "
                f"מיקום: {line.get('storage_location') or '-'}"
            )
        else:
            bits.append("סומן כהתקבל (רישום בלבד, לא נוסף למלאי)")
    if line.get("notes"):
        bits.append(f"הערות: {line['notes']}")
    return " · ".join(bits)


def _safe(action, *args, **kwargs):
    """Run a DB action; show user-facing errors without a traceback."""
    try:
        return True, action(*args, **kwargs)
    except svc.OrderError as exc:
        st.error(str(exc))
    except Exception:
        st.error("שגיאת תקשורת עם מסד הנתונים. לא נשמר שינוי. נסו שוב בעוד רגע.")
    return False, None


def _new_order_tab(client, santaf_lengths):
    n = st.session_state.setdefault("ol_line_n", 0)
    flash = st.session_state.pop("ol_flash", None)
    if flash:
        st.success(flash)

    st.markdown("##### פרטי ההזמנה (נשארים בין שורות)")
    c1, c2 = st.columns(2)
    order_no = c1.text_input("מספר הזמנת פרגולה *", key="ol_order_no")
    ordered_by = c2.text_input("הוזמן על ידי", key="ol_ordered_by")
    c3, c4 = st.columns(2)
    supplier_ref = c3.text_input("מספר הזמנת ספק", key="ol_supplier_ref")
    supplier_date = c4.date_input("תאריך הזמנה מהספק", value=date.today(), key="ol_supplier_date")
    has_coord = st.checkbox("ידוע תאריך תיאום ללקוח", key="ol_has_coord")
    coord_date = st.date_input("תאריך תיאום", value=date.today(), key="ol_coord_date") if has_coord else None

    st.markdown("##### שורה להוספה")
    category = st.radio("קטגוריה", list(svc.CATEGORIES), format_func=svc.CATEGORIES.get,
                        horizontal=True, key=f"ol_cat_{n}")
    if category == "LED":
        spec = st.text_input("סוג לד *", key=f"ol_spec_{n}")
        meters = st.number_input("אורך (מטר)", min_value=0.1, value=1.5, step=0.5, key=f"ol_len_led_{n}")
        length_mm = int(round(meters * 1000))
    else:
        spec = svc.DEFAULT_SANTAF_TYPE
        st.text_input("סוג", value=spec, disabled=True, key=f"ol_spec_s_{n}")
        length_mm = st.selectbox("אורך (מ\"מ)", santaf_lengths, key=f"ol_len_saf_{n}")
    quantity = st.number_input("כמות", min_value=1, value=1, step=1, key=f"ol_qty_{n}")
    status_choice = st.selectbox(
        "סטטוס",
        ["ordered", "received"],
        format_func=lambda s: "הוזמן מהספק, עדיין לא התקבל" if s == "ordered"
        else "כבר התקבל. רישום בלבד, לא מוסיף למלאי",
        key=f"ol_status_{n}",
    )
    notes = st.text_input("הערות", key=f"ol_notes_{n}")

    if st.button("✅ שמור שורה", key=f"ol_save_{n}", type="primary"):
        try:
            payload = svc.build_line_payload(
                customer_order_number=order_no, category=category, quantity=quantity,
                length_mm=length_mm, item_spec=spec, supplier_order_ref=supplier_ref,
                supplier_order_date=supplier_date, ordered_by=ordered_by,
                coordination_date=coord_date, notes=notes,
                record_as_received=(status_choice == "received"),
                santaf_lengths=santaf_lengths,
            )
        except svc.OrderError as exc:
            st.error(str(exc))
            return
        ok, _ = _safe(svc.create_line, client, payload)
        if ok:
            st.session_state["ol_line_n"] = n + 1          # fresh line fields, header kept
            st.session_state["ol_flash"] = (
                f"✅ נשמרה שורה להזמנה {payload['customer_order_number']}. "
                "אפשר להוסיף שורה נוספת לאותה הזמנה."
            )
            st.rerun()

    if _clean(order_no):
        ok, lines = _safe(svc.search_lines, client, order_number=order_no, limit=100)
        exact = [l for l in (lines or []) if l["customer_order_number"] == order_no.strip()]
        if exact:
            st.markdown(f"**שורות קיימות בהזמנה {order_no.strip()}:**")
            for l in exact:
                st.markdown(f"- {_line_summary(l)}")


def _clean(v):
    return (v or "").strip()


def _search_tab(client, santaf_lengths, on_stock_change):
    c1, c2, c3 = st.columns(3)
    order_q = c1.text_input("🔍 מספר הזמנת פרגולה", key="os_order")
    status = c2.selectbox("סטטוס", ["", "ordered", "received", "cancelled"],
                          format_func=lambda s: "הכל" if not s else svc.STATUS_LABELS[s], key="os_status")
    category = c3.selectbox("קטגוריה", ["", "LED", "SANTAF"],
                            format_func=lambda s: "הכל" if not s else svc.CATEGORIES[s], key="os_cat")
    by_date = st.checkbox("סינון לפי תאריך תיאום", key="os_by_date")
    coord = st.date_input("תאריך תיאום", value=date.today(), key="os_coord") if by_date else None

    ok, lines = _safe(svc.search_lines, client, order_number=order_q, status=status or None,
                      category=category or None, coordination_date=coord)
    if not ok:
        return
    if not lines:
        st.info("לא נמצאו שורות הזמנה.")
        return

    groups = OrderedDict()
    for l in lines:
        groups.setdefault(l["customer_order_number"], []).append(l)
    st.caption(f"{len(groups)} הזמנות, {len(lines)} שורות")

    for order_no, group in groups.items():
        st.markdown(f"### הזמנת פרגולה {order_no}")
        for line in sorted(group, key=lambda r: (r["category"], r["length_mm"])):
            with st.container(border=True):
                st.markdown(_line_summary(line))
                st.caption(_line_details(line))
                if line["status"] == "cancelled":
                    continue
                _line_actions(client, line, santaf_lengths, on_stock_change)


def _line_actions(client, line, santaf_lengths, on_stock_change):
    lid = line["id"]
    with st.expander("עדכון תאריך תיאום"):
        new_date = st.date_input("תאריך תיאום חדש", key=f"oc_date_{lid}",
                                 value=date.fromisoformat(line["coordination_date"])
                                 if line.get("coordination_date") else date.today())
        if st.button("שמור תאריך", key=f"oc_save_{lid}"):
            ok, _ = _safe(svc.update_coordination_date, client, lid, new_date)
            if ok:
                st.rerun()
    if line["status"] != "ordered":
        return
    with st.expander("📥 קליטה למחסן"):
        st.warning("הקליטה מוסיפה את הכמות למלאי הפיזי. בצעו אותה רק אחרי שהסחורה הגיעה בפועל.")
        c1, c2 = st.columns(2)
        qty = c1.number_input("כמות שהתקבלה", min_value=1, value=int(line["quantity"]),
                              step=1, key=f"or_qty_{lid}")
        rdate = c2.date_input("תאריך קליטה", value=date.today(), key=f"or_date_{lid}")
        loc = st.text_input("מיקום אחסון *", key=f"or_loc_{lid}", placeholder="למשל A-03")
        confirm = st.checkbox("אני מאשר/ת שהסחורה התקבלה פיזית במחסן", key=f"or_ok_{lid}")
        if st.button("✅ קלוט למלאי", key=f"or_btn_{lid}", disabled=not confirm):
            ok, _ = _safe(svc.receive_line, client, lid, qty, rdate, loc, santaf_lengths)
            if ok:
                on_stock_change()
                st.success("✅ נקלט למלאי.")
                st.rerun()
        if st.button("🚫 בטל שורה (לא התקבלה, לא תיכנס למלאי)", key=f"oc_cancel_{lid}"):
            ok, _ = _safe(svc.cancel_line, client, lid)
            if ok:
                st.rerun()


def render(client, santaf_lengths, on_stock_change):
    st.markdown(
        """<div class="page-banner"><h2>📝 הזמנות</h2>
        <p>רישום הזמנות מהספק לפי הזמנת פרגולה, וקליטה למחסן</p></div>""",
        unsafe_allow_html=True,
    )
    st.info("רישום הזמנה לא משנה את המלאי. המלאי גדל רק בקליטה למחסן.")
    tab_new, tab_find = st.tabs(["➕ הזמנה חדשה", "🔍 הזמנות וקליטה"])
    with tab_new:
        _new_order_tab(client, santaf_lengths)
    with tab_find:
        _search_tab(client, santaf_lengths, on_stock_change)
