"""Streamlit page "הזמנות": enter order lines, look them up, and run the daily preparation.

Mobile-first (single column), Hebrew. All data rules live in order_service.py and in the
database (migrations/001_order_lines.sql); this module only draws the screens.
"""
from collections import OrderedDict
from datetime import date

import streamlit as st

import order_service as svc

ICON = {"LED": "📦", "SANTAF": "🟫"}
STATE_ICON = {"issued": "🏁", "ready": "✅", "missing": "⛔"}


# ---------------------------------------------------------------- formatting
def _d(value):
    if not value:
        return "-"
    y, m, d = str(value)[:10].split("-")
    return f"{d}/{m}/{y}"


def _as_date(value, default=None):
    return date.fromisoformat(str(value)[:10]) if value else (default or date.today())


def _size(line):
    mm = line["length_mm"]
    return f"{mm / 1000:g} מ'" if line["category"] == "LED" else f"{mm} מ\"מ"


def _line_text(line):
    base = f"{line.get('item_spec') or ''} {_size(line)} × {line['quantity']}".strip()
    if line["category"] == "SANTAF" and line.get("color") and line["color"] != svc.STANDARD_COLOR:
        base += f" ({line['color']})"
    return base


def _safe(action, *args, **kwargs):
    """Run a service call; show the Hebrew error (never a traceback) on failure."""
    try:
        return True, action(*args, **kwargs)
    except svc.OrderError as exc:
        st.error(str(exc))
    except Exception as exc:                                   # defensive: never show a traceback
        st.error(svc.friendly_error(exc))
    return False, None


def _items(lines):
    """Turn lines into display items: one per LED package, one per Santaf line."""
    items, packages = [], {}
    for line in sorted(lines, key=lambda l: (l["category"], l.get("package_no") or 1, l["length_mm"])):
        if svc.is_package(line):
            key = (line["customer_order_number"], line["package_no"])
            if key not in packages:
                packages[key] = {"kind": "package", "lines": [], "head": line}
                items.append(packages[key])
            packages[key]["lines"].append(line)
        else:
            items.append({"kind": "line", "lines": [line], "head": line})
    return items


# ---------------------------------------------------------------- new line
def _new_line_tab(client, santaf_lengths, on_stock_change):
    n = st.session_state.setdefault("ol_line_n", 0)
    flash = st.session_state.pop("ol_flash", None)
    if flash:
        st.success(flash)

    order_no = st.text_input("מספר הזמנת פרגולה *", key="ol_order_no")
    has_coord = st.checkbox("יש תאריך תיאום עם הלקוח", key="ol_has_coord")
    coord = st.date_input("תאריך תיאום", value=date.today(), key="ol_coord") if has_coord else None
    st.divider()

    category = st.radio("קטגוריה", list(svc.CATEGORIES), format_func=svc.CATEGORIES.get,
                        horizontal=True, key=f"ol_cat_{n}")
    supply, color, package_no = None, None, 1
    if category == "LED":
        spec = st.text_input("סוג לד *", key=f"ol_spec_{n}")
        meters = st.number_input("אורך (מטר)", min_value=0.1, value=1.5, step=0.5, key=f"ol_len_led_{n}")
        length_mm = int(round(meters * 1000))
        package_no = st.number_input("מספר חבילה", min_value=1, value=1, step=1, key=f"ol_pkg_{n}",
                                     help="שורות לד באותה חבילה של הספק מקבלות יחד סטטוס, קבלה ומיקום.")
    else:
        supply = st.radio("מקור הסנטף", list(svc.SUPPLY_LABELS), format_func=svc.SUPPLY_LABELS.get,
                          horizontal=True, key=f"ol_supply_{n}")
        spec = st.text_input("סוג", value=svc.DEFAULT_SANTAF_TYPE, key=f"ol_spec_s_{n}")
        if supply == "stock":
            color = svc.STANDARD_COLOR
            st.text_input("צבע", value=color, disabled=True, key=f"ol_color_fixed_{n}")
            length_mm = st.selectbox("אורך (מ\"מ)", santaf_lengths, key=f"ol_len_saf_{n}")
            ok, levels = _safe(svc.stock_levels, client)
            if ok:
                st.caption(f"זמין כעת במלאי הרגיל באורך {length_mm}: **{levels.get(length_mm, 0)}**")
        else:
            color = st.text_input("צבע", value=svc.STANDARD_COLOR, key=f"ol_color_{n}")
            length_mm = st.number_input("אורך (מ\"מ)", min_value=100, value=4500, step=100, key=f"ol_len_sup_{n}")
    quantity = st.number_input("כמות", min_value=1, value=1, step=1, key=f"ol_qty_{n}")

    status, ref, order_date, received_date, storage = None, None, None, None, None
    if category == "LED" or supply == "supplier":
        status = st.selectbox("סטטוס הזמנה מהספק", svc.STATUS_VALUES, format_func=svc.STATUS_LABELS.get,
                              key=f"ol_status_{n}")
        if status:
            ref = st.text_input("מספר הזמנת ספק", key=f"ol_ref_{n}")
            order_date = st.date_input("תאריך הזמנה מהספק", value=date.today(), key=f"ol_odate_{n}")
        if status == "received":
            received_date = st.date_input("תאריך קבלה במחסן *", value=date.today(), key=f"ol_rdate_{n}")
            storage = st.text_input("מיקום אחסון", key=f"ol_store_{n}", placeholder="למשל A-03")
        if category == "LED":
            st.caption("אם החבילה כבר קיימת בהזמנה, הסטטוס והקבלה שלה יועתקו לשורה החדשה.")
    notes = st.text_input("הערות", key=f"ol_notes_{n}")

    if st.button("✅ שמור שורה", key=f"ol_save_{n}", type="primary"):
        try:
            payload = svc.build_line_payload(
                customer_order_number=order_no, category=category, quantity=quantity,
                length_mm=length_mm, item_spec=spec, color=color, supply=supply,
                package_no=package_no, supplier_status=status, supplier_ref=ref,
                supplier_order_date=order_date, received_date=received_date,
                storage_location=storage, coordination_date=coord, notes=notes,
                santaf_lengths=santaf_lengths)
        except svc.OrderError as exc:
            st.error(str(exc))
            return
        ok, _ = _safe(svc.create_line, client, payload)
        if ok:
            st.session_state["ol_line_n"] = n + 1                # fresh line fields, order kept
            st.session_state["ol_flash"] = (f"✅ נשמרה שורה להזמנה {payload['customer_order_number']}. "
                                            "אפשר להוסיף שורה נוספת לאותה הזמנה.")
            st.rerun()

    if (order_no or "").strip():
        ok, lines = _safe(svc.search_lines, client, order_number=order_no, limit=200)
        mine = [l for l in (lines or []) if l["customer_order_number"] == order_no.strip()]
        if mine:
            st.markdown(f"**שורות בהזמנה {order_no.strip()}:**")
            for item in _items(mine):
                st.markdown("- " + _item_title(item))


# ---------------------------------------------------------------- display of orders
def _item_title(item):
    head = item["head"]
    if item["kind"] == "package":
        return f"{ICON['LED']} חבילה {head['package_no']}: " + ", ".join(_line_text(l) for l in item["lines"])
    return f"{ICON['SANTAF']} {_line_text(head)}"


def _details(item, levels):
    head = item["head"]
    bits = []
    if head["supply"] == "stock":
        need = head["quantity"] - head["picked_qty"]
        bits.append("מהמלאי הרגיל")
        bits.append(f"נאסף {head['picked_qty']} מתוך {head['quantity']}")
        if not head.get("issued_on") and need > 0:
            bits.append(f"זמין במלאי: {levels.get(head['length_mm'], 0)}")
    else:
        bits.append("הוזמן ללקוח" if head["category"] == "SANTAF" else "חבילת ספק")
        bits.append(f"סטטוס: {svc.STATUS_LABELS[head.get('supplier_status')]}")
        if head.get("supplier_ref"):
            bits.append(f"הזמנת ספק: {head['supplier_ref']}")
        if head.get("supplier_order_date"):
            bits.append(f"הוזמן ב-{_d(head['supplier_order_date'])}")
        if head.get("received_date"):
            bits.append(f"התקבל ב-{_d(head['received_date'])}")
        if head.get("storage_location"):
            bits.append(f"אחסון: {head['storage_location']}")
    if head.get("staging_location"):
        bits.append(f"הכנה: {head['staging_location']}")
    if head.get("issued_on"):
        bits.append(f"נופק ל{head['recipient']} ב-{_d(head['issued_on'])}")
    if head.get("notes"):
        bits.append(f"הערות: {head['notes']}")
    return " · ".join(bits)


def render_orders(client, lines, levels, santaf_lengths, on_stock_change, prefix, shortages=None):
    groups = OrderedDict()
    for l in lines:
        groups.setdefault(l["customer_order_number"], []).append(l)
    for number, group in groups.items():
        coord = next((l["coordination_date"] for l in group if l.get("coordination_date")), None)
        st.markdown(f"### הזמנת פרגולה {number}")
        st.caption(f"תיאום ללקוח: {_d(coord)}")
        with st.expander("📅 תאריך תיאום"):
            new_date = st.date_input("תאריך תיאום", value=_as_date(coord), key=f"{prefix}_coord_{number}")
            st.caption("שינוי התאריך לא מזיז שום דבר פיזית. מיקומים מעדכנים ידנית בכל פריט.")
            if st.button("שמור תאריך", key=f"{prefix}_coordsave_{number}"):
                ok, _ = _safe(svc.set_coordination_date, client, number, new_date)
                if ok:
                    st.rerun()
        for item in _items(group):
            state, label = _item_state(item, shortages)
            with st.container(border=True):
                st.markdown(f"{STATE_ICON[state]} **{label}**  \n{_item_title(item)}")
                st.caption(_details(item, levels))
                if state != "issued" or item["head"].get("issued_on") is None:
                    _actions(client, item, state, levels, on_stock_change, prefix)


def _item_state(item, shortages):
    states = [svc.line_state(l, shortages) for l in item["lines"]]
    for want in ("missing", "ready", "issued"):
        for s, label in states:
            if s == want:
                return s, label
    return states[0]


def _actions(client, item, state, levels, on_stock_change, prefix):
    head, lid = item["head"], item["head"]["id"]
    issued = bool(head.get("issued_on"))
    if issued:
        return
    with st.expander("⚙️ פעולות"):
        if head["supply"] == "supplier":
            _supplier_form(client, head, prefix)
        if head["supply"] == "stock":
            _pick_form(client, head, levels, on_stock_change, prefix)
        _location_form(client, head, prefix)
        if state == "ready":
            _issue_form(client, item, on_stock_change, prefix)
        _delete_form(client, item, prefix)


def _supplier_form(client, head, prefix):
    lid = head["id"]
    st.markdown("**סטטוס הזמנה מהספק**" + (" (לכל החבילה)" if svc.is_package(head) else ""))
    cur = head.get("supplier_status")
    status = st.selectbox("סטטוס", svc.STATUS_VALUES, index=svc.STATUS_VALUES.index(cur),
                          format_func=svc.STATUS_LABELS.get, key=f"{prefix}_st_{lid}")
    ref = odate = rdate = storage = None
    if status:
        ref = st.text_input("מספר הזמנת ספק", value=head.get("supplier_ref") or "", key=f"{prefix}_ref_{lid}")
        odate = st.date_input("תאריך הזמנה מהספק", value=_as_date(head.get("supplier_order_date")),
                              key=f"{prefix}_od_{lid}")
    if status == "received":
        rdate = st.date_input("תאריך קבלה *", value=_as_date(head.get("received_date")), key=f"{prefix}_rd_{lid}")
        storage = st.text_input("מיקום אחסון", value=head.get("storage_location") or "", key=f"{prefix}_sl_{lid}")
        st.caption("הסטטוס לא משנה את המלאי הרגיל.")
    if st.button("💾 שמור סטטוס", key=f"{prefix}_stsave_{lid}"):
        ok, _ = _safe(svc.update_supplier_info, client, head, status, ref, odate, rdate, storage)
        if ok:
            st.rerun()


def _pick_form(client, head, levels, on_stock_change, prefix):
    lid = head["id"]
    remaining = head["quantity"] - head["picked_qty"]
    if remaining > 0:
        st.markdown("**איסוף מהמלאי הרגיל**")
        st.caption(f"זמין: {levels.get(head['length_mm'], 0)} · נותר לאסוף: {remaining}. "
                   "האיסוף מוריד מהמלאי הזמין מיד.")
        qty = st.number_input("כמות לאיסוף", min_value=1, max_value=remaining, value=remaining, step=1,
                              key=f"{prefix}_pq_{lid}")
        staging = st.text_input("מיקום הכנה *", value=head.get("staging_location") or "",
                                key=f"{prefix}_ps_{lid}", placeholder="למשל B-20")
        pdate = st.date_input("תאריך איסוף", value=date.today(), key=f"{prefix}_pd_{lid}")
        if st.button("📤 אסוף והורד מהמלאי", key=f"{prefix}_pick_{lid}"):
            ok, _ = _safe(svc.pick_line, client, lid, qty, pdate, staging)
            if ok:
                on_stock_change()
                st.rerun()
    if head["picked_qty"] > 0:
        if st.button("↩️ בטל איסוף (החזר למלאי)", key=f"{prefix}_unpick_{lid}"):
            ok, _ = _safe(svc.unpick_line, client, lid)
            if ok:
                on_stock_change()
                st.rerun()


def _location_form(client, head, prefix):
    lid = head["id"]
    st.markdown("**מיקום**")
    st.caption("עדכנו כאן רק כשהפריט הועבר בפועל.")
    can_store = head["supply"] == "supplier" and head.get("supplier_status") == "received"
    if can_store:
        store = st.text_input("מיקום אחסון", value=head.get("storage_location") or "", key=f"{prefix}_ls_{lid}")
        if st.button("💾 שמור מיקום אחסון", key=f"{prefix}_lssave_{lid}"):
            ok, _ = _safe(svc.set_location, client, head, "storage_location", store)
            if ok:
                st.rerun()
    if head["supply"] == "supplier" or head["picked_qty"] > 0:
        stage = st.text_input("מיקום הכנה (Staging)", value=head.get("staging_location") or "",
                              key=f"{prefix}_lg_{lid}", placeholder="למשל A-11")
        if st.button("💾 שמור מיקום הכנה", key=f"{prefix}_lgsave_{lid}"):
            ok, _ = _safe(svc.set_location, client, head, "staging_location", stage)
            if ok:
                st.rerun()


def _issue_form(client, item, on_stock_change, prefix):
    head = item["head"]
    lid = head["id"]
    st.markdown("**ניפוק**" + (" (כל החבילה)" if item["kind"] == "package" else ""))
    who = st.text_input("שם המקבל *", key=f"{prefix}_who_{lid}")
    idate = st.date_input("תאריך ניפוק", value=date.today(), key=f"{prefix}_id_{lid}")
    if st.button("🏁 נפק", key=f"{prefix}_issue_{lid}"):
        def go():
            ids = svc.issue_target_ids(client, head)
            return svc.issue_lines(client, ids, idate, who)
        ok, _ = _safe(go)
        if ok:
            on_stock_change()
            st.rerun()


def _delete_form(client, item, prefix):
    head = item["head"]
    if any(l["picked_qty"] > 0 for l in item["lines"]):
        return
    lid = head["id"]
    st.markdown("**מחיקת שורה שהוזנה בטעות**")
    first = item["lines"][0]
    confirm = st.checkbox("אני בטוח/ה שאני רוצה למחוק את השורה", key=f"{prefix}_delok_{lid}")
    if st.button("🗑️ מחק שורה", key=f"{prefix}_del_{lid}", disabled=not confirm):
        def go():
            for l in item["lines"]:
                svc.delete_line(client, l["id"])
        ok, _ = _safe(go)
        if ok:
            st.rerun()


# ---------------------------------------------------------------- tabs
def _search_tab(client, santaf_lengths, on_stock_change):
    order_q = st.text_input("🔍 מספר הזמנת פרגולה", key="os_order")
    c1, c2 = st.columns(2)
    status_map = {"": "הכל", "none": "לא הוזמן", "ordered": "הוזמן", "pending": "בהמתנה", "received": "התקבל"}
    status = c1.selectbox("סטטוס", list(status_map), format_func=status_map.get, key="os_status")
    category = c2.selectbox("קטגוריה", ["", "LED", "SANTAF"],
                            format_func=lambda s: "הכל" if not s else svc.CATEGORIES[s], key="os_cat")
    by_date = st.checkbox("סינון לפי תאריך תיאום", key="os_by_date")
    coord = st.date_input("תאריך תיאום", value=date.today(), key="os_coord") if by_date else None
    ok, lines = _safe(svc.search_lines, client, order_number=order_q, status=status or None,
                      category=category or None, coordination_date=coord)
    if not ok:
        return
    if not lines:
        st.info("לא נמצאו שורות.")
        return
    ok, levels = _safe(svc.stock_levels, client)
    st.caption(f"{len({l['customer_order_number'] for l in lines})} הזמנות · {len(lines)} שורות")
    render_orders(client, lines, levels if ok else {}, santaf_lengths, on_stock_change, "o")


def _daily_tab(client, santaf_lengths, on_stock_change):
    day = st.date_input("תאריך תיאום להכנה", value=date.today(), key="dp_date")
    ok, lines = _safe(svc.search_lines, client, coordination_date=day)
    if not ok:
        return
    if not lines:
        st.info(f"אין הזמנות מתואמות ל-{_d(day)}.")
        return
    ok, levels = _safe(svc.stock_levels, client)
    summary = svc.daily_summary(lines, levels if ok else {})
    c = summary["counts"]
    a, b, d = st.columns(3)
    a.metric("חסר", c["missing"])
    b.metric("מוכנות", c["ready"])
    d.metric("נופקו", c["issued"])
    if summary["shortages"]:
        st.warning("חוסרים בסנטף מהמלאי הרגיל (לפי אורך, לכל הזמנות היום יחד):  \n" + "  \n".join(
            f"• {s['length']} מ\"מ: נדרש {s['required']}, זמין {s['available']}, חסר {s['shortfall']}"
            for s in summary["shortages"]))
    shortage_map = {s["length"]: s for s in summary["shortages"]}
    ordered = [l for o in summary["orders"] for l in o["lines"]]
    render_orders(client, ordered, levels if ok else {}, santaf_lengths, on_stock_change, "d", shortage_map)


def render(client, santaf_lengths, on_stock_change):
    st.markdown(
        """<div class="page-banner"><h2>📝 הזמנות</h2>
        <p>הזמנות פרגולה: לדים וסנטף, קבלה, מיקומים, הכנה וניפוק</p></div>""",
        unsafe_allow_html=True,
    )
    st.info("רישום הזמנה ושינוי סטטוס ספק לא משנים את המלאי. המלאי הרגיל יורד רק באיסוף. "
            "לסנטף מהמלאי הרגיל השתמשו באיסוף כאן ולא בדף 'ניפוק סנטפים' הישן.")
    tab_new, tab_find, tab_day = st.tabs(["➕ הוספה", "🔍 חיפוש", "📅 הכנה יומית"])
    with tab_new:
        _new_line_tab(client, santaf_lengths, on_stock_change)
    with tab_find:
        _search_tab(client, santaf_lengths, on_stock_change)
    with tab_day:
        _daily_tab(client, santaf_lengths, on_stock_change)
