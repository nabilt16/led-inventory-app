// Browser end-to-end check against tests/e2e/run_app_fake.py (fake data, no production).
// Usage: node e2e_flow.cjs <desktop|mobile> <screenshotDir>
// Needs Playwright: set PLAYWRIGHT_PATH to the playwright package directory.
const path = require('path');
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');

const mode = process.argv[2] || 'desktop';
const shots = process.argv[3] || '.';
const BASE = 'http://127.0.0.1:8599';
const viewport = mode === 'mobile' ? { width: 390, height: 844 } : { width: 1280, height: 900 };

let failures = 0;
const log = (ok, name, extra = '') => { if (!ok) failures++; console.log(`${ok ? 'PASS' : 'FAIL'} [${mode}] ${name} ${extra}`); };

(async () => {
  const browser = await chromium.launch({ channel: 'chrome' });
  const ctx = await browser.newContext({ viewport, locale: 'he-IL', isMobile: mode === 'mobile', hasTouch: mode === 'mobile' });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', e => pageErrors.push(String(e)));
  const shot = async n => page.screenshot({ path: path.join(shots, `${mode}-${n}.png`), fullPage: true });
  const settle = (ms = 900) => page.waitForTimeout(ms);
  const bodyText = () => page.locator('body').innerText();

  // DOM-level click: on desktop the app's pre-existing sidebar CSS can overlay content
  async function jsClick(locator) { await locator.scrollIntoViewIfNeeded(); await locator.evaluate(e => e.click()); await settle(700); }
  async function commit(locator, value) { await locator.fill(value); await locator.press('Tab'); await settle(500); }
  async function pickRadio(text) {
    const l = page.locator('[data-testid="stRadio"] label').filter({ hasText: new RegExp('^' + text + '$') }).first();
    await l.scrollIntoViewIfNeeded(); await jsClick(l);
  }
  async function choose(labelText, optionText, scope = page) {
    const box = scope.getByRole('combobox', { name: labelText }).first();
    await box.scrollIntoViewIfNeeded(); await jsClick(box);
    await page.getByRole('option', { name: optionText, exact: true }).first().click(); await settle(700);
  }
  const dateInput = label => page.locator('[data-testid="stDateInput"]').filter({ hasText: label }).first().locator('input');
  async function setDate(locator, ymd) { await locator.fill(ymd); await locator.press('Enter'); await settle(600); await page.keyboard.press('Escape'); await page.mouse.click(5, 5); await settle(600); }

  async function gotoPage(label) {
    if (mode === 'mobile') {
      await page.getByRole('combobox', { name: /ניווט/ }).first().click();
      await page.getByRole('option', { name: label }).click();
    } else {
      const radio = page.getByText(label, { exact: true }).first();
      if (!(await radio.isVisible())) {
        await page.locator('[data-testid="stExpandSidebarButton"], [data-testid="collapsedControl"]').first().click();
        await settle(500);
      }
      await page.getByText(label, { exact: true }).first().click();
      await settle(700);
      const close = page.locator('[data-testid="stSidebarCollapseButton"] button').first();
      if (await close.count()) { await close.click({ force: true }).catch(() => {}); await settle(700); }
    }
    await settle(1400);
  }
  async function tab(nameRe) { const t = page.getByRole('tab', { name: nameRe }); await t.waitFor({ timeout: 25000 }); await jsClick(t); await settle(900); }
  const item = text => page.locator('[data-testid="stLayoutWrapper"], [data-testid="stVerticalBlockBorderWrapper"]').filter({ hasText: text }).last();
  async function openActions(scope) {
    const sm = scope.locator('summary', { hasText: 'פעולות' }).first();
    await sm.scrollIntoViewIfNeeded();
    await sm.evaluate(el => { const d = el.closest('details'); if (!d.open) el.click(); });   // open only if closed
    await settle(700);
  }

  async function addLine(fn) { await fn(); await jsClick(page.getByRole('button', { name: /שמור שורה/ })); await settle(1500); }

  try {
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.getByLabel('אימייל').waitFor({ timeout: 20000 });
    log(true, 'login screen shown first');
    log(!(await bodyText()).includes('דשבורד'), 'no app content before login');
    await page.getByLabel('אימייל').fill('stranger@example.test');
    await page.getByLabel('סיסמה').fill('stranger-password');
    await page.getByRole('button', { name: 'כניסה' }).click(); await settle(1800);
    const tStr = await bodyText();
    log(tStr.includes('אינו מאושר') && !tStr.includes('סקירה כללית'), 'valid but unapproved account is refused');
    await page.getByLabel('אימייל').fill('worker@example.test');
    await page.getByLabel('סיסמה').fill('correct-password');
    await page.getByRole('button', { name: 'כניסה' }).click(); await settle(2500);
    log((await bodyText()).includes('סקירה כללית'), 'owner login succeeds -> dashboard');
    const dir = await page.evaluate(() => ({ dir: getComputedStyle(document.body).direction, overflow: document.documentElement.scrollWidth - window.innerWidth }));
    log(dir.dir === 'rtl', 'RTL direction', JSON.stringify(dir));

    // ---- orders page
    await gotoPage('📝 הזמנות');
    let t = await bodyText();
    log(t.includes('רישום הזמנה ושינוי סטטוס ספק לא משנים את המלאי'), 'orders page: stock notice shown');
    log(!t.includes('הוזמן על ידי'), '"Ordered by" field is gone');
    await commit(page.getByLabel('מספר הזמנת פרגולה *'), '844122');
    await jsClick(page.getByText('יש תאריך תיאום עם הלקוח'));
    await setDate(dateInput('תאריך תיאום'), '2026/10/25');

    // LED 1.5 m x 2, package 1, status הוזמן
    await addLine(async () => {
      await pickRadio('לדים');
      await commit(page.getByLabel('סוג לד *'), 'LED חם');
      await commit(page.getByLabel('אורך (מטר)'), '1.5');
      await commit(page.getByLabel('כמות').first(), '2');
      await choose(/סטטוס הזמנה מהספק/, 'הוזמן');
    });
    log((await bodyText()).includes('נשמרה שורה להזמנה 844122'), 'LED line 1 saved');
    log((await page.getByLabel('מספר הזמנת פרגולה *').inputValue()) === '844122', 'order number kept for next line');
    // LED 3 m x 1, same package (inherits status)
    await addLine(async () => {
      await pickRadio('לדים');
      await commit(page.getByLabel('סוג לד *'), 'LED חם');
      await commit(page.getByLabel('אורך (מטר)'), '3');
      await commit(page.getByLabel('כמות').first(), '1');
    });
    // Santaf from regular stock: 2000 x 4
    await addLine(async () => {
      await pickRadio('סנטף');
      await pickRadio('מהמלאי הרגיל');
      await choose(/אורך \(מ"מ\)/, '2000');
      await commit(page.getByLabel('כמות').first(), '4');
    });
    // Santaf from regular stock: 4500 x 9 (only 4 available -> shortage)
    await addLine(async () => {
      await pickRadio('סנטף');
      await pickRadio('מהמלאי הרגיל');
      await choose(/אורך \(מ"מ\)/, '4500');
      await commit(page.getByLabel('כמות').first(), '9');
    });
    // Santaf ordered specifically: red 9000 x 25, status בהמתנה
    await addLine(async () => {
      await pickRadio('סנטף');
      await pickRadio('הוזמן במיוחד ללקוח');
      await commit(page.getByLabel('צבע'), 'אדום');
      await commit(page.getByLabel('אורך (מ"מ)'), '9000');
      await commit(page.getByLabel('כמות').first(), '25');
      await choose(/סטטוס הזמנה מהספק/, 'בהמתנה');
    });
    t = await bodyText();
    log(t.includes('שורות בהזמנה 844122') && t.includes('חבילה 1') && t.includes('אדום'), 'all 5 lines listed under order 844122');
    await shot('01-order-entered');

    // ---- stock must be unchanged by entering orders
    await gotoPage('🟫 מלאי סנטפים');
    t = await bodyText();
    log(/2000 מ"מ[\s\S]{0,80}מלאי נוכחי: 100/.test(t) && /4500 מ"מ[\s\S]{0,80}מלאי נוכחי: 4\b/.test(t), 'santaf stock unchanged after entering orders (100 / 4)');
    await gotoPage('💡 מלאי לדים');
    t = await bodyText();
    log(t.includes('OLD-1') && !t.includes('844122'), 'led stock unchanged (supplier packages never enter general stock)');

    // ---- search / lookup
    await gotoPage('📝 הזמנות');
    await tab(/חיפוש/);
    await commit(page.getByLabel('🔍 מספר הזמנת פרגולה'), '844122');
    t = await bodyText();
    log(t.includes('הזמנת פרגולה 844122') && t.includes('1 הזמנות'), 'lookup by order number');
    log(t.includes('סטטוס: הוזמן') && t.includes('סטטוס: בהמתנה') && t.includes('מהמלאי הרגיל'), 'statuses and source shown per item');
    await shot('02-search');

    // ---- daily preparation: shortage per length
    await tab(/הכנה יומית/);
    await setDate(dateInput('תאריך תיאום להכנה'), '2026/10/25');
    t = await bodyText();
    log(/4500 מ"מ: נדרש 9, זמין 4, חסר 5/.test(t), 'daily view: shortage for 4500 (need 9, have 4, missing 5)');
    log(!/2000 מ"מ: נדרש/.test(t), 'daily view: 2000 mm is sufficient (no shortage)');
    await shot('03-daily');

    // ---- pick standard santaf (2000 x 4): needs staging location, deducts immediately
    const saf = item('2000 מ"מ × 4');
    await openActions(saf);
    await jsClick(saf.getByRole('button', { name: /אסוף והורד מהמלאי/ }));
    log((await bodyText()).includes('מיקום הכנה'), 'pick without staging location is refused');
    await commit(saf.getByLabel('מיקום הכנה *'), 'B-20');
    await jsClick(saf.getByRole('button', { name: /אסוף והורד מהמלאי/ })); await settle(1500);
    await gotoPage('🟫 מלאי סנטפים');
    t = await bodyText();
    log(/2000 מ"מ[\s\S]{0,80}מלאי נוכחי: 96/.test(t), 'pick deducted available stock 100 -> 96 immediately');

    // ---- LED package: receive (whole package) with location, then issue as one unit
    await gotoPage('📝 הזמנות');
    await tab(/חיפוש/);
    await commit(page.getByLabel('🔍 מספר הזמנת פרגולה'), '844122');
    const pkg = item('חבילה 1');
    await openActions(pkg);
    await choose(/סטטוס$/, 'התקבל', pkg);
    await commit(pkg.getByLabel('מיקום אחסון').first(), 'A-03');
    await jsClick(pkg.getByRole('button', { name: /שמור סטטוס/ })); await settle(1500);
    t = await bodyText();
    log(t.includes('אחסון: A-03') && t.includes('התקבל במחסן'), 'LED package received with location A-03');
    const pkg2 = item('חבילה 1');
    await openActions(pkg2);
    await commit(pkg2.getByLabel('שם המקבל *'), 'הלקוח');
    await jsClick(pkg2.getByRole('button', { name: /^🏁 נפק/ })); await settle(1800);
    t = await bodyText();
    log(t.includes('נופק לה') && t.includes('הלקוח'), 'LED package issued with recipient');
    await shot('04-after-issue');

    // issue the picked santaf; stock must stay 96 (no second deduction)
    const saf2 = item('2000 מ"מ × 4');
    await openActions(saf2);
    await commit(saf2.getByLabel('שם המקבל *'), 'הלקוח');
    await jsClick(saf2.getByRole('button', { name: /^🏁 נפק/ })); await settle(1800);
    await gotoPage('🟫 מלאי סנטפים');
    t = await bodyText();
    log(/2000 מ"מ[\s\S]{0,80}מלאי נוכחי: 96/.test(t), 'issuing the picked santaf did not deduct a second time (still 96)');
    await gotoPage('💡 מלאי לדים');
    log((await bodyText()).includes('OLD-1') && !(await bodyText()).includes('844122'), 'led stock still untouched after package issued');

    // ---- other existing pages still render
    for (const p of ['🏠 דשבורד', '💡 קליטת לדים', '💡 ניפוק לדים', '🟫 קליטת סנטפים', '🟫 ניפוק סנטפים', '⚙️ מינימום סנטפים', '📋 דוחות']) {
      await gotoPage(p);
      log(!/Traceback|Error|שגיאה/.test(await bodyText()), `page renders: ${p}`);
    }
    await shot('05-reports');
    const ov = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    log(ov <= 1, 'no horizontal page overflow', `(overflow ${ov}px)`);
    log(pageErrors.length === 0, 'no uncaught browser errors', pageErrors.join(' | ').slice(0, 200));
  } catch (e) {
    failures++;
    console.log('FAIL [' + mode + '] exception:', String(e).split('\n')[0]);
    await shot('99-failure').catch(() => {});
  } finally {
    await browser.close();
    console.log(`RESULT [${mode}] failures=${failures}`);
    process.exit(failures ? 1 : 0);
  }
})();
