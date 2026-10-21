# מדריך שחרור: LED Inventory

פרויקט Supabase: `pergola-system` (`gwiieqmawtudoxafnjeg`). הרצת SQL: **SQL Editor** בלוח הבקרה.
כל הקבצים נבדקו מקומית מול סכמת הפרודקשן שנבדקה בביקורת (PostgreSQL 17.6).

## לפני שמתחילים (פעם אחת, בלוח הבקרה)
1. **Authentication ← Users ← Add user:** האימייל שלכם + סיסמה שתבחרו, עם Auto Confirm User.
2. **Authentication ← Sign In / Providers:** כבו "Allow new users to sign up" וכבו "Allow anonymous sign-ins".
3. **Streamlit Cloud ← App ← Settings ← Secrets:** ודאו שקיימים `SUPABASE_URL` ו-`SUPABASE_KEY`.
   (סיסמת Gmail ראו "סודות" בהמשך.)

## רצף השחרור (בסדר הזה)
| # | פעולה | קובץ | הערות |
|---|---|---|---|
| 1 | ביקורת (אופציונלי, קריאה בלבד) | `ops/audit_readonly.sql` | לשמור את הפלט. הוא מאפשר שחזור מדיניות ישנה |
| 2+3 | מיגרציה 001 + אישור הבעלים (קובץ אחד) | `ops/RELEASE_STEP_1.sql` | להחליף בו את האימייל לפני ההרצה. נעצרת לבד אם הסכמה לא תואמת, ומדווחת אם לא אושר משתמש |
| 4 | מיזוג ופריסה | GitHub → Streamlit Cloud | הגרסה החדשה עובדת גם לפני 002 (ל-authenticated יש הרשאות על הטבלאות הישנות) |
| 5 | בדיקת כניסה | האפליקציה | כניסה, הזנת הזמנה, חיפוש, איסוף וניפוק בדיקה אחד קטן |
| 6 | מיגרציה 002 | `migrations/002_lock_legacy_tables.sql` | **רק אחרי שלב 5.** סוגרת גישה אנונימית וכתיבה ישירה למלאי. נעצרת אם אין משתמש מאושר |
| 6א | אימות שהכתיבה האנונימית נחסמה | `python ops/verify_anon_blocked.py` | בטוח בכל מצב: לא כותב דבר. מציג BLOCKED/OPEN לכל בדיקה |
| 7 | איפוס מלאי (נפרד, באישור) | `ops/reset_stock_to_zero.sql` | נעצר אם יש סנטף שנאסף ולא נופק |

## אחרי 002: מה נאכף במסד הנתונים
- `anon`: אין גישה לשום טבלה או פונקציה.
- `authenticated`: רק משתמש שמופיע ופעיל ב-`app_users`.
- כמויות מלאי משתנות **רק** דרך הפונקציות האטומיות (`pick/unpick/issue_order_lines`, `santaf_receive_stock`,
  `santaf_issue_direct`, `led_issue_stock`). עדכון ישיר של `quantity` נדחה.
- `santaf_movements`: קריאה בלבד. אין מחיקה או TRUNCATE לאף אחד דרך ה-API.
- פעולה זהה שחוזרת תוך 15 שניות נדחית (לחיצה כפולה). אין מלאי שלילי. אורך סנטף ייחודי.

## שחזור
- **נעילה (משתמש לא מאושר):** להריץ שוב `ops/approve_user.sql`. לא פותח גישה אנונימית.
- **תקלה בגרסה אחרי 002:** מתקנים קדימה. הגרסה הישנה לא תעבוד אחרי 002.
- **ביטול 001:** `ops/rollback_001.sql` (מסרב אם יש הזמנות). אם 002 הורצה, קודם `ops/emergency_revert_002.sql`.
- **מוצא אחרון:** `ops/emergency_revert_002.sql` מחזיר את המצב המקורי **וגם פותח כתיבה ציבורית**. להריץ 002 שוב בהקדם.

## סודות
- ההגדרות הנוכחיות בקוד עדיין כוללות ערכי ברירת מחדל ישנים (מפתח anon וסיסמת Gmail).
  הענף `chore/remove-hardcoded-secrets` מסיר אותם ומחייב הגדרה ב-Secrets.
- **סדר בטוח:** (1) מכינים סיסמת אפליקציה חדשה ב-Gmail, (2) מזינים אותה ב-Secrets בשם `SENDER_PASSWORD`,
  (3) בודקים שליחת דוח במייל, (4) ממזגים את הענף, (5) מבטלים את הסיסמה הישנה ב-Gmail.

## בדיקות
`pip install -r requirements-dev.txt` ואז `pytest tests --ignore=tests/integration` (ללא רשת).
בדיקות האינטגרציה (`tests/integration`) דורשות Docker, Postgres ו-PostgREST מקומיים, ואינן נוגעות בפרודקשן.
בדיקת הדפדפן: `tests/e2e/e2e_flow.cjs` (Playwright) מול `tests/e2e/run_app_fake.py` עם נתוני דמה.
