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
  const consoleErrors = [];
  page.on('pageerror', e => consoleErrors.push(String(e)));
  const shot = async n => page.screenshot({ path: path.join(shots, `${mode}-${n}.png`), fullPage: true });
  const settle = (ms = 900) => page.waitForTimeout(ms);
  const bodyText = () => page.locator('body').innerText();

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
      // collapse the sidebar overlay again so it does not cover the page content
      const close = page.locator('[data-testid="stSidebarCollapseButton"] button').first();
      if (await close.count()) { await close.click({ force: true }).catch(() => {}); await settle(700); }
    }
    await settle(1400);
  }

  async function pickRadio(text) {
    const l = page.locator('[data-testid="stRadio"] label').filter({ hasText: new RegExp('^' + text + '$') }).first();
    await l.scrollIntoViewIfNeeded(); await l.click(); await settle(900);
  }

  // DOM-level click: on desktop the app's pre-existing sidebar CSS can overlay content
  async function jsClick(locator) { await locator.scrollIntoViewIfNeeded(); await locator.evaluate(e => e.click()); await settle(700); }

  async function commit(locator, value) { await locator.fill(value); await locator.press('Tab'); await settle(500); }

  try {
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.getByLabel('אימייל').waitFor({ timeout: 20000 });
    log(true, 'login screen shown first');
    const t0 = await bodyText();
    log(!t0.includes('דשבורד'), 'no app content before login');
    await shot('01-login');

    await page.getByLabel('אימייל').fill('worker@example.test');
    await page.getByLabel('סיסמה').fill('wrong');
    await page.getByRole('button', { name: 'כניסה' }).click(); await settle(1500);
    log((await bodyText()).includes('ההתחברות נכשלה'), 'wrong password rejected');

    await page.getByLabel('סיסמה').fill('correct-password');
    await page.getByRole('button', { name: 'כניסה' }).click(); await settle(2500);
    log((await bodyText()).includes('סקירה כללית'), 'login succeeds -> dashboard');
    await shot('02-dashboard');

    // RTL checks
    const dir = await page.evaluate(() => ({
      htmlDir: document.documentElement.dir || getComputedStyle(document.documentElement).direction,
      bodyDir: getComputedStyle(document.body).direction,
      appDir: getComputedStyle(document.querySelector('[data-testid="stAppViewContainer"]') || document.body).direction,
      overflow: document.documentElement.scrollWidth - window.innerWidth,
    }));
    console.log('INFO direction', JSON.stringify(dir));

    // --- Orders page: add two lines to one order
    await gotoPage('📝 הזמנות');
    log((await bodyText()).includes('רישום הזמנה לא משנה את המלאי'), 'orders page opens with stock notice');
    await commit(page.getByLabel('מספר הזמנת פרגולה *'), '844122');
    await commit(page.getByLabel('הוזמן על ידי'), 'דוד');

    // line 1: SANTAF 2000 x 4
    await pickRadio('סנטף');
    await page.getByRole('combobox', { name: /אורך/ }).first().click();
    await page.getByRole('option', { name: '2000', exact: true }).click(); await settle(500);
    await commit(page.getByLabel('כמות').first(), '4');
    await shot('03-orders-form-santaf');
    await page.getByRole('button', { name: /שמור שורה/ }).click(); await settle(1500);
    log((await bodyText()).includes('נשמרה שורה להזמנה 844122'), 'santaf line saved');

    // line 2: LED 15 m x 4 (header retained)
    const kept = await page.getByLabel('מספר הזמנת פרגולה *').inputValue();
    log(kept === '844122', 'order header kept after save');
    await pickRadio('לדים');
    await commit(page.getByLabel('סוג לד *'), 'LED חם');
    await commit(page.getByLabel('אורך (מטר)'), '15');
    await commit(page.getByLabel('כמות').first(), '4');
    await page.getByRole('button', { name: /שמור שורה/ }).click(); await settle(1500);
    const txt = await bodyText();
    log(txt.includes('שורות קיימות בהזמנה 844122'), 'both lines listed under order 844122');

    // validation: empty order number
    await commit(page.getByLabel('מספר הזמנת פרגולה *'), '');
    await page.getByRole('button', { name: /שמור שורה/ }).click(); await settle(1200);
    log((await bodyText()).includes('חובה להזין מספר הזמנת פרגולה'), 'missing order number is rejected');
    await commit(page.getByLabel('מספר הזמנת פרגולה *'), '844122');

    // --- Stock must be unchanged after entering orders
    await gotoPage('🟫 מלאי סנטפים');
    let st = await bodyText();
    log(/2000 מ"מ[\s\S]{0,80}מלאי נוכחי: 10\b/.test(st), 'santaf 2000 stock still 10 after entering orders');
    await gotoPage('💡 מלאי לדים');
    st = await bodyText();
    log(st.includes('OLD-1') && !st.includes('844122'), 'led stock unchanged (only legacy row)');
    await shot('04-led-stock');

    // --- Find + receive
    await gotoPage('📝 הזמנות');
    const findTab = page.getByRole('tab', { name: /הזמנות וקליטה/ });
    await findTab.waitFor({ timeout: 25000 });
    await jsClick(findTab);
    await commit(page.getByLabel('🔍 מספר הזמנת פרגולה'), '844122');
    st = await bodyText();
    log(st.includes('הזמנת פרגולה 844122') && st.includes('סנטף') && st.includes('לדים'), 'search finds both lines');
    await shot('05-search');

    // lines are sorted LED first, SANTAF second: receive each via its own expander
    async function receiveVia(idx, loc) {
      const ex = page.locator('[data-testid="stExpander"]').filter({ hasText: 'קליטה למחסן' }).nth(idx);
      await jsClick(ex.locator('summary'));
      const btn = ex.getByRole('button', { name: /קלוט למלאי/ });
      if (idx === 1) log(await btn.isDisabled(), 'receive button disabled until physical-receipt confirmation');
      await commit(ex.getByLabel('מיקום אחסון *'), loc);
      await jsClick(ex.getByText('אני מאשר/ת שהסחורה התקבלה פיזית במחסן'));
      if (idx === 1) await shot('06-receive-form');
      await jsClick(btn); await settle(1800);
    }
    await receiveVia(1, 'B-03');                       // SANTAF 2000 x 4
    st = await bodyText();
    log(st.includes('נקלט למלאי') || st.includes('B-03'), 'santaf receipt confirmed');
    await gotoPage('🟫 מלאי סנטפים');
    st = await bodyText();
    log(/2000 מ"מ[\s\S]{0,80}מלאי נוכחי: 14/.test(st), 'santaf stock increased 10 -> 14 only after receipt');

    await gotoPage('📝 הזמנות');
    await jsClick(page.getByRole('tab', { name: /הזמנות וקליטה/ }));
    await commit(page.getByLabel('🔍 מספר הזמנת פרגולה'), '844122');
    await receiveVia(0, 'A-05');                       // LED 15 m x 4
    await gotoPage('💡 מלאי לדים');
    st = await bodyText();
    log(st.includes('מיקום: A-05') && st.includes('844122') && st.includes('OLD-1'), 'led batch added after receipt (location + order kept, legacy row intact)');
    await shot('08-led-after-receipt');

    // a second receipt attempt for an already received line must not be possible from the UI
    await gotoPage('📝 הזמנות');
    await jsClick(page.getByRole('tab', { name: /הזמנות וקליטה/ }));
    await commit(page.getByLabel('🔍 מספר הזמנת פרגולה'), '844122');
    log((await page.getByRole('button', { name: /קלוט למלאי/ }).count()) === 0, 'received lines offer no receive button again');
    await shot('09-search-after-receipt');

    // other existing pages still render
    for (const p of ['🏠 דשבורד', '💡 קליטת לדים', '💡 ניפוק לדים', '🟫 קליטת סנטפים', '🟫 ניפוק סנטפים', '⚙️ מינימום סנטפים', '📋 דוחות']) {
      await gotoPage(p);
      const t = await bodyText();
      log(!/Traceback|Error|שגיאה/.test(t), `page renders: ${p}`);
    }
    await shot('07-reports');

    const ov = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    log(ov <= 1, 'no horizontal page overflow', `(overflow ${ov}px)`);
    log(consoleErrors.length === 0, 'no uncaught browser errors', consoleErrors.join(' | ').slice(0, 200));
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
