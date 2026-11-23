// Makes the store images from screenshots taken by tests/browser-test.js.
// Copy tests/out/peak-2-after-messages.png to store/images/raw-chat.png, 5-dark.png to raw-dark.png,
// 6-popup.png to raw-popup.png and 7-popup-settings.png to raw-settings.png, then: node store/make-images.js store/images
const { chromium } = require('playwright');
const fs = require('fs'); const path = require('path');
const dir = process.argv[2];
const img = (f) => 'data:image/png;base64,' + fs.readFileSync(path.join(dir, f)).toString('base64');
const icon = 'data:image/png;base64,' + fs.readFileSync(path.join(dir, '../../icons/icon128.png')).toString('base64');
const font = "font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;";
const shots = {
  // 1280x800 store screenshots
  'screenshot-1-chat.png': [1280, 800, `<img src="${img('raw-chat.png')}" style="width:1280px;height:800px;display:block">`],
  'screenshot-2-dark.png': [1280, 800, `<img src="${img('raw-dark.png')}" style="width:1280px;height:800px;display:block">`],
  'screenshot-3-popup.png': [1280, 800, `<div style="${font}width:1280px;height:800px;background:linear-gradient(135deg,#eef3fe,#f8f6f1);display:flex;align-items:center;justify-content:center;gap:70px">
     <div style="max-width:470px;color:#1f1f1f"><div style="font-size:42px;font-weight:700;line-height:1.15">Your Claude limits,<br>one click away</div>
     <p style="font-size:20px;line-height:1.5;color:#555;margin-top:18px">Session and weekly bars with reset times, the chat you're in, and how many messages you have left. The icon shows your session % at a glance.</p></div>
     <img src="${img('raw-popup.png')}" style="height:600px;border-radius:14px;box-shadow:0 20px 60px rgba(0,0,0,.18)"></div>`],
  'screenshot-4-settings.png': [1280, 800, `<div style="${font}width:1280px;height:800px;background:linear-gradient(135deg,#f8f6f1,#eef3fe);display:flex;align-items:center;justify-content:center;gap:70px">
     <img src="${img('raw-settings.png')}" style="height:720px;border-radius:14px;box-shadow:0 20px 60px rgba(0,0,0,.18)">
     <div style="max-width:470px;color:#1f1f1f"><div style="font-size:42px;font-weight:700;line-height:1.15">Make it yours</div>
     <p style="font-size:20px;line-height:1.5;color:#555;margin-top:18px">Pick which bars to show, get a notification when a limit resets, choose from 10 languages, and add an optional API key for exact token counts.</p></div></div>`],
  // 440x280 small promo tile
  'promo-small-440x280.png': [440, 280, `<div style="${font}width:440px;height:280px;background:linear-gradient(135deg,#2f6fe4,#3b82f6);color:#fff;display:flex;flex-direction:column;justify-content:center;padding:0 34px;box-sizing:border-box">
     <div style="display:flex;align-items:center;gap:12px"><img src="${icon}" style="width:44px;height:44px;border-radius:10px;background:#fff"><div style="font-size:24px;font-weight:700">AI Token Tracker</div></div>
     <div style="font-size:17px;margin-top:14px;opacity:.95">See your Claude usage limits right inside claude.ai</div>
     <div style="margin-top:20px;display:flex;flex-direction:column;gap:7px">
       ${[['Session (5h)',43],['Weekly',21]].map(([l,p])=>`<div style="font-size:12px;display:flex;justify-content:space-between"><span>${l}</span><b>${p}%</b></div><div style="height:6px;border-radius:9px;background:rgba(255,255,255,.3)"><div style="height:6px;width:${p}%;border-radius:9px;background:#fff"></div></div>`).join('')}
     </div></div>`],
  // 1400x560 marquee (optional, used if featured)
  'promo-marquee-1400x560.png': [1400, 560, `<div style="${font}width:1400px;height:560px;background:linear-gradient(135deg,#2f6fe4,#3b82f6);color:#fff;display:flex;align-items:center;justify-content:space-between;padding:0 90px;box-sizing:border-box">
     <div style="max-width:560px"><div style="display:flex;align-items:center;gap:16px"><img src="${icon}" style="width:64px;height:64px;border-radius:14px;background:#fff"><div style="font-size:40px;font-weight:700">AI Token Tracker</div></div>
     <div style="font-size:26px;margin-top:22px;line-height:1.4">Your Claude usage limits, chat length and messages left, right inside claude.ai.</div></div>
     <img src="${img('raw-popup.png')}" style="height:470px;border-radius:14px;box-shadow:0 20px 60px rgba(0,0,0,.25)"></div>`],
};
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME || '/usr/bin/google-chrome', args: ['--no-proxy-server'] });
  for (const [name, [w, h, html]] of Object.entries(shots)) {
    const p = await b.newPage({ viewport: { width: w, height: h } });
    await p.setContent(`<html><body style="margin:0">${html}</body></html>`);
    await p.waitForTimeout(200);
    await p.screenshot({ path: path.join(dir, name) });
    await p.close();
    console.log('made', name);
  }
  await b.close();
})();
