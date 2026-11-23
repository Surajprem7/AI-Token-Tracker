const path = require('path');
const fs = require('fs');
// Loads the extension in Chrome against the mock claude.ai and takes screenshots.
const { chromium } = require('playwright');

const EXT = process.argv[2];
const CHAT = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';
const out = (n) => path.join(__dirname, 'out', n);
fs.mkdirSync(path.join(__dirname, 'out'), { recursive: true });

(async () => {
  const profile = fs.mkdtempSync('/tmp/cut-profile-');
  const ctx = await chromium.launchPersistentContext(profile, {
    executablePath: process.env.CHROME || '/usr/bin/google-chrome',
    headless: false,
    viewport: { width: 1280, height: 800 },
    args: [
      '--headless=new',
      `--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`,
      '--host-resolver-rules=MAP claude.ai:443 127.0.0.1:8443',
      '--ignore-certificate-errors', '--no-first-run', '--no-proxy-server',
    ],
  });
  const errors = [];
  let sw = ctx.serviceWorkers()[0] || await ctx.waitForEvent('serviceworker', { timeout: 15000 });
  const extId = sw.url().split('/')[2];
  console.log('extension id', extId);

  const page = await ctx.newPage();
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`https://claude.ai/chat/${CHAT}`);
  await page.waitForSelector('.cut-side .cut-row', { timeout: 15000 });
  await page.waitForFunction(() => /tokens/.test(document.querySelector('.cut-strip')?.textContent || ''), null, { timeout: 15000 });
  await page.screenshot({ path: out('1-chat.png') });
  console.log('sidebar:', (await page.textContent('.cut-side')).replace(/\s+/g, ' '));
  console.log('strip:', (await page.textContent('.cut-strip')).replace(/\s+/g, ' '));

  // Send messages so it learns "messages left".
  for (let i = 0; i < 3; i++) {
    await page.click('#send');
    await page.waitForTimeout(4500);
  }
  await page.waitForTimeout(1000);
  console.log('strip after sends:', (await page.textContent('.cut-strip')).replace(/\s+/g, ' '));
  await page.screenshot({ path: out('2-after-messages.png') });
  await page.locator('main').screenshot({ path: out('3-composer.png') });
  await page.locator('nav').screenshot({ path: out('4-sidebar.png') });

  // Collapse the sidebar section.
  await page.click('.cut-side-head');
  await page.waitForTimeout(500);
  console.log('collapsed:', (await page.textContent('.cut-side')).replace(/\s+/g, ' '));
  await page.click('.cut-side-head');

  // Dark mode look.
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.addStyleTag({ content: 'body{background:#262624!important;color:#e8e6dc!important} nav{background:#1f1e1d!important;border-color:#333!important} fieldset{background:#30302e!important;border-color:#444!important} .bubble{background:#30302e!important}' });
  await page.waitForTimeout(300);
  await page.screenshot({ path: out('5-dark.png') });

  // Popup.
  const pop = await ctx.newPage();
  pop.on('pageerror', (e) => errors.push('popup: ' + e));
  await pop.setViewportSize({ width: 300, height: 600 });
  await pop.goto(`chrome-extension://${extId}/popup.html`);
  await pop.waitForSelector('.limit', { timeout: 10000 });
  await pop.waitForTimeout(1200);
  await pop.screenshot({ path: out('6-popup.png'), fullPage: true });
  await pop.click('summary');
  await pop.waitForTimeout(300);
  await pop.screenshot({ path: out('7-popup-settings.png'), fullPage: true });
  console.log('popup:', (await pop.textContent('body')).replace(/\s+/g, ' ').slice(0, 400));

  // Hindi.
  await pop.selectOption('#lang', 'hi');
  await page.bringToFront();
  await page.waitForTimeout(800);
  console.log('hindi sidebar:', (await page.textContent('.cut-side')).replace(/\s+/g, ' ').slice(0, 120));

  // Badge text.
  const badge = await sw.evaluate(() => chrome.action.getBadgeText({}));
  console.log('badge:', badge);
  const stored = await sw.evaluate(() => chrome.storage.local.get(['learn', 'chat']));
  console.log('learned:', JSON.stringify(stored.learn), 'chat:', JSON.stringify(stored.chat));

  console.log('errors:', errors.length ? errors : 'none');
  await ctx.close();
})().catch((e) => { console.error('FAIL', e); process.exit(1); });
