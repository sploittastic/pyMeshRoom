// End to end: the real app in Chromium against a real pyMeshRoom (fake radio).
// node e2e.cjs PAGE_URL WORK_DIR, with devserver.py running on port 9101 (see run.sh)
const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const H = __dirname;
const SERVER = 'http://127.0.0.1:9101';
const [pageUrl, W] = process.argv.slice(2);
const shotPrefix = W + '/shot';
const token = fs.readFileSync(W + '/dev.log', 'utf8').match(/TOKEN (\S+)/)[1];
const ID = JSON.parse(fs.readFileSync(H + '/../vectors.json')).identities[0];
const PRIV = ID.private, PUB = ID.public;
const py = (...args) => execFileSync('python3', [H + '/mkpkt.py', ...args]).toString().trim();
const hear = (hex) => fs.appendFileSync(W + '/rx.txt', 'RX ' + hex + '\n');
const inject = (ch, sender, text) => hear(py(ch, sender, text));
const ok = (cond, what) => { if (!cond) { console.log('FAIL', what); process.exitCode = 1; } else console.log('ok  ', what); };
const GIF_ID = 'JIX9t2j0ZTN9S';
const TINY_GIF = Buffer.from('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7', 'base64');

let cookie = null;
async function admin(p, body) {
  if (!cookie) {
    const r = await fetch(SERVER + '/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"password":"adminpw"}' });
    cookie = r.headers.get('set-cookie').split(';')[0];
  }
  const r = await fetch(SERVER + p, { method: 'POST', headers: { 'Content-Type': 'application/json', Cookie: cookie }, body: JSON.stringify(body || {}) });
  return r.status;
}

async function newPage(b, opts = {}) {
  const ctx = await b.newContext({ viewport: { width: 1200, height: 760 }, ...opts });
  await ctx.route(/^https:\/\/(media\d?|i)\.giphy\.com\//, (r) => r.fulfill({ status: 200, contentType: 'image/gif', body: TINY_GIF }));
  const p = await ctx.newPage();
  p.errors = [];
  p.on('pageerror', e => p.errors.push('pageerror: ' + e.message));
  p.on('console', m => { if (m.type() === 'error' && !/WebSocket|Failed to load resource/.test(m.text())) p.errors.push('console: ' + m.text()); });
  return p;
}

async function waitOpen(p) {
  try {
    await p.waitForSelector('#conn-dot.open', { timeout: 10000 });
  } catch (e) {
    const err = await p.textContent('#login-error').catch(() => '');
    throw new Error('not connected: ' + (err || e.message) + ' ' + p.errors.join(' | '));
  }
}

async function loginWith(p, { url = SERVER, key = null, priv = PRIV, name = 'WebTester' } = {}) {
  if (url !== null) await p.fill('#f-url', url);
  if (key) { await p.evaluate(() => { document.getElementById('key-box').open = true; }); await p.fill('#f-key', key); }
  await p.fill('#f-priv', priv);
  await p.fill('#f-name', name);
  await p.click('#login-go');
}

let b;
(async () => {
  b = await chromium.launch();
  const errors = [];
  const p = await newPage(b);
  await p.goto(pageUrl);
  ok(await p.isHidden('#f-key'), 'no API key field: logging in takes only the private key and a name');
  await p.fill('#f-url', SERVER); await p.fill('#f-priv', PRIV); await p.fill('#f-name', 'WebTester');
  await p.waitForFunction(() => document.getElementById('f-pub').textContent.startsWith('Public key:'));
  await p.screenshot({ path: shotPrefix + '-login.png' });
  await p.click('#login-go');
  await p.waitForSelector('#conn-dot.open', { timeout: 10000 });
  ok(true, 'logged in by signing with the private key');
  ok(/can read, but not send yet/.test(await p.textContent('#banner')), 'a key not heard on the air yet is pending: read only');
  ok(await p.isDisabled('#send') && await p.isDisabled('#advert'), 'pending: can\'t send or advertise');
  await p.screenshot({ path: shotPrefix + '-pending.png' });

  hear(py('--advert', PRIV, 'WebTester'));                   // their MeshCore app adverts within range of the radio
  await p.waitForSelector('#toast:text("you can send now")', { timeout: 10000 });
  await p.waitForSelector('#banner', { state: 'hidden', timeout: 5000 });
  ok(true, 'heard on the air: the page switches to "can send" by itself');
  ok(await p.textContent('#room-name') === 'Public', 'Public is the default channel');
  await p.fill('#text', 'hello from the browser');
  await p.press('#text', 'Enter');
  await p.waitForSelector('.msg.mine .status:text("Sent")', { timeout: 10000 });
  ok(true, 'own message sent');

  inject('Public', 'Bob', 'hi @[WebTester], welcome');
  await p.waitForSelector('.msg .sender:text("Bob")', { timeout: 10000 });
  ok(await p.textContent('.msg:not(.mine) .meta span:nth-child(3)') === '2 hops · SNR 6.0', 'hops and SNR shown');
  ok(await p.locator('.mention.me').count() === 1, 'own mention highlighted');
  const once = py('Public', 'Bob', 'only once');
  hear(once); hear(once);
  await p.waitForSelector('.msg .text:text("only once")');
  await p.waitForTimeout(800);
  ok(await p.locator('.msg .text:text("only once")').count() === 1, 'a packet heard twice shows once');

  inject('Public', 'Carol', 'look at this https://giphy.com/gifs/cat-funny-' + GIF_ID + ' haha');
  await p.waitForSelector('.msg .gif img', { timeout: 10000 });
  await p.waitForFunction(() => { const i = document.querySelector('.msg .gif img'); return i && i.complete && i.naturalWidth > 0; }, null, { timeout: 10000 });
  ok(await p.getAttribute('.msg .gif img', 'src') === 'https://media.giphy.com/media/' + GIF_ID + '/giphy.gif', 'a Giphy link shows as the GIF');
  ok(/look at this\s+haha/.test(await p.textContent('.msg:has(.gif) .text')), 'the rest of the message stays around the GIF');
  await p.fill('#text', 'https://giphy.com/gifs/' + GIF_ID);
  await p.waitForSelector('#gif-preview img', { timeout: 5000 });
  ok(true, 'a Giphy link being typed is previewed');
  await p.press('#text', 'Enter');
  await p.waitForSelector('.msg.mine .gif img', { timeout: 10000 });
  ok(await p.isHidden('#gif-preview'), 'sent as a GIF; preview cleared');
  await p.click('#show-gifs');
  ok(await p.locator('.msg .gif img').count() === 0 && await p.locator('.msg .gif-link').count() === 2, 'Show GIFs off: links instead, nothing loaded');
  await p.click('#show-gifs');
  ok(await p.locator('.msg .gif img').count() === 2, 'Show GIFs back on');

  await p.click('#add-channel');
  await p.fill('#a-tag', 'test');
  await p.click('#add-go');
  ok(await p.textContent('#room-name') === '#test', 'joined #test');
  inject('#test', 'Carol', 'on the hashtag');
  await p.waitForSelector('.msg .text:text("on the hashtag")', { timeout: 10000 });
  ok(true, '#test message decrypted');
  inject('Public', 'Bob', 'while you were away');
  await p.waitForSelector('.channels .badge', { timeout: 10000 });
  ok(await p.textContent('.channels button:first-child .badge') === '1', 'unread badge on Public');

  await p.click('#add-channel');
  await p.click('.tab[data-kind=private]');
  await p.fill('#a-name', 'Ops');
  await p.fill('#a-secret', '00112233445566778899aabbccddeeff');
  await p.click('#add-go');
  ok(await p.textContent('#room-name') === 'Ops', 'joined private channel Ops');
  await p.fill('#text', 'secret plans');
  await p.press('#text', 'Enter');
  for (let i = 1; i <= 4; i++) { await p.fill('#text', 'burst ' + i); await p.press('#text', 'Enter'); }
  await p.waitForFunction(() => [...document.querySelectorAll('.msg.mine .status')].every(s => s.textContent === 'Sent'), null, { timeout: 30000 });
  ok(true, '5 messages through the rate limit (3 per 2 s), all sent');
  inject('00112233445566778899aabbccddeeff', 'Dave', 'private reply');
  await p.waitForSelector('.msg .text:text("private reply")', { timeout: 10000 });
  ok(true, 'private channel message decrypted');

  await p.click('#advert');
  await p.click('[data-advert=flood]');
  await p.waitForSelector('#toast:text("Advert sent.")', { timeout: 15000 });
  ok(true, 'advert sent');
  await p.click('#channel-info');
  ok(await p.inputValue('#i-secret') === '00112233445566778899aabbccddeeff', 'private secret shown in details');
  await p.click('#dlg-info [data-close]');
  await p.screenshot({ path: shotPrefix + '-chat.png' });

  await p.reload();
  ok(await p.inputValue('#f-priv') === '', 'private key not remembered without the checkbox');
  ok(await p.inputValue('#f-url') === SERVER && await p.inputValue('#f-name') === 'WebTester', 'URL and name remembered');
  const stored = await p.evaluate(() => JSON.stringify(localStorage));
  ok(!stored.includes(PRIV) && !stored.includes(token), 'neither key in localStorage');
  await p.fill('#f-priv', PRIV);
  await p.click('#login-go');
  await p.waitForSelector('#conn-dot.open', { timeout: 10000 });
  ok(await p.locator('.channels button').count() === 3, 'channels kept across reload');
  await p.click('.channels button:first-child');
  ok(await p.locator('.msg .text:text("hello from the browser")').count() === 1, 'history kept across reload');
  ok(await p.isHidden('#banner'), 'logging in again: already allowed to send');

  ok(await admin('/api/v1/web/users/' + PUB + '/kick') === 200, 'admin kicks the key');
  await p.waitForSelector('#conn-dot.failed', { timeout: 10000 });
  ok(/admin disconnected/.test(await p.textContent('#banner')), 'kicked: ' + await p.textContent('#banner'));
  ok(await admin('/api/v1/web/users/' + PUB + '/ban', { name: 'WebTester' }) === 200, 'admin bans the key');
  const banned = await newPage(b);
  await banned.goto(pageUrl);
  await loginWith(banned);
  await banned.waitForSelector('#login-error:not([hidden])', { timeout: 10000 });
  ok(/banned/.test(await banned.textContent('#login-error')), 'banned key can\'t log in: ' + await banned.textContent('#login-error'));
  await admin('/api/v1/web/users/' + PUB + '/unban');

  const apiPage = await newPage(b);
  await apiPage.goto(pageUrl);
  await loginWith(apiPage, { key: token, name: 'Bot' });
  await apiPage.waitForSelector('#conn-dot.open', { timeout: 10000 });
  ok(await apiPage.isHidden('#banner') && !(await apiPage.isDisabled('#advert')), 'an API key still works, and can send at once');
  const bad = await newPage(b);
  await bad.goto(pageUrl);
  await loginWith(bad, { key: 'kaas_wrong' });
  await bad.waitForSelector('#login-error:not([hidden])', { timeout: 10000 });
  ok(/refused the login/.test(await bad.textContent('#login-error')), 'bad API key: ' + await bad.textContent('#login-error'));

  // served by pyMeshRoom itself at /meshcore-web/: the server comes from the page's own address
  const served = await newPage(b);
  const res = await served.goto(SERVER + '/meshcore-web');
  ok(res.ok() && served.url() === SERVER + '/meshcore-web/', 'pyMeshRoom serves it at /meshcore-web/');
  ok(await served.isHidden('#url-box') && await served.textContent('#url-host') === '127.0.0.1:9101', 'served by the server: no URL to type');
  await loginWith(served, { url: null, name: 'SameOrigin' });
  await waitOpen(served);
  ok(true, 'logged in from the server\'s own page');
  ok(await served.evaluate(() => getComputedStyle(document.body).backgroundColor) === 'rgb(17, 20, 24)', 'the dashboard\'s look');
  await served.screenshot({ path: shotPrefix + '-served.png' });

  const mp = await newPage(b, { viewport: { width: 390, height: 780 }, isMobile: true, hasTouch: true });
  await mp.goto(pageUrl);
  await loginWith(mp, { name: 'Phone' });
  await mp.waitForSelector('#conn-dot.open', { timeout: 10000 });
  inject('Public', 'Bob', 'a longer message to see how wrapping looks on a narrow phone screen, with @[Phone] mentioned https://giphy.com/gifs/' + GIF_ID);
  await mp.waitForSelector('.msg .sender:text("Bob")', { timeout: 10000 });
  ok(await mp.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'no horizontal scroll on a phone');
  await mp.screenshot({ path: shotPrefix + '-phone.png' });

  const tx = execFileSync('python3', [H + '/readtx.py', W + '/dev.log', '#test', '00112233445566778899aabbccddeeff']).toString();
  ok(tx.includes("GRP Public 'WebTester: hello from the browser'"), 'the radio sent our Public message, readable by the Python client');
  ok(tx.includes("GRP 00112233445566778899aabbccddeeff 'WebTester: burst 4'"), 'the radio sent our private message, readable with the secret');
  ok(/ADVERT flood \w+ valid-sig WebTester/.test(tx), 'the radio sent our advert, with a valid signature');
  for (const pg of [p, banned, apiPage, bad, served, mp]) errors.push(...pg.errors);
  ok(errors.length === 0, 'no page errors' + (errors.length ? ': ' + errors.join(' | ') : ''));
})().catch(e => { console.log('FAIL', e.message); process.exitCode = 1; }).finally(() => b && b.close());
