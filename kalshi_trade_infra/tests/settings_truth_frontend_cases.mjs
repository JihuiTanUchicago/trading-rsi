// Shared cases, run against each bundle's offline harness (Tau and Meridian): every setting a customer changes says
// that it saved and still reads true later (settings walk, 2026-10-31, both themes at 390 and 1366 px).
// - Two tabs: email turned off in one tab left the other's Settings showing On after it came back to the front (its
//   /api/me read on return was thrown away), and with the theme saved as the other one elsewhere, pressing this tab's
//   own theme did nothing at all, so the reader couldn't keep it.
// - Billing in two tabs: auto-reload turned on in one tab, the other's 30s poll read it and kept showing Off, and a
//   change of amount there would have sent that stale Off back.
// - The autopilot's model and market selects saved with no word where they were changed, and dropped keyboard focus to
//   the page under the panel; the model select didn't say what it runs (its check-ins run on a set model).
// - "Turn off live" said nothing once it took, and a second press while it was out sent it again.
// Display only: no write is retried, and how each setting is saved is unchanged.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const plain = (html) => html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
const posts = (f) => f.requests.filter(({options}) => options && options.method === 'POST').map(({path, options}) => [path, JSON.parse(options.body)]);
// Each harness records focus its own way: Tau's marks the element, Meridian's moves activeElement.
const hasFocus = (f, el) => !!el && (el.focused === true || f.document.activeElement === el);
const shown = async (f) => {
  f.document.hidden = true; for (const [n, fn] of f.documentListeners) if (n === 'visibilitychange') fn({});
  f.document.hidden = false; for (const [n, fn] of f.documentListeners) if (n === 'visibilitychange') fn({});
  await settle();
};
// The interval whose tick asks for `prefix`: the page's own poll.
async function pollOf(f, prefix) {
  for (const tick of [...f.intervals.values()].reverse()) {
    const before = f.requests.length;
    await tick(); await settle();
    if (f.requests.slice(before).some(({path}) => path.startsWith(prefix))) return tick;
  }
  throw new Error('no poll asks for ' + prefix);
}

export function settingsTruthCases({name, frontend, current, storageFor, billingPage, cardedBilling}) {
  const other = current === 'tau' ? 'meridian' : 'tau';
  const label = {tau: 'Tau', meridian: 'Meridian'};

  test(`${name}: Settings shows email and theme as saved after the tab's next read, when another tab changed them`, async () => {
    let me = {user: {id: 7, name: 'Offline fixture', ui_theme: current, email_opt_out: false}};
    const f = frontend({storage: storageFor(new Map([['tau_ui', current]])), fetchImpl: (path, options) => {
      if (path === '/api/me') return response(me);
      if (path === '/api/ui-theme') return response({ok: true, theme: JSON.parse(options.body).theme});
      throw new Error('Unexpected offline request: ' + path);
    }});
    f.S.me.user = {...f.S.me.user, ui_theme: current, email_opt_out: false};
    f.location.pathname = '/settings';
    await f.viewSettings();
    const sw = f.root.querySelector('#email-optional');
    assert.equal(sw.checked, true);
    // Another tab turns optional email off and saves the other theme; this tab comes back to the front.
    me = {user: {...me.user, email_opt_out: true, ui_theme: other}};
    await shown(f);
    assert.equal(sw.checked, false, 'the switch shows what is saved');
    assert.match(f.root.querySelector('#email-state').textContent, /^Off\./);
    assert.equal(plain(f.root.querySelector('#email-flash').innerHTML), 'Optional email is now off: it was changed in another tab or on another device.');
    assert.equal(f.root.querySelector(`[data-theme="${other}"]`).getAttribute('aria-checked'), 'true', 'the saved theme is the checked one');
    assert.equal(f.root.querySelector(`[data-theme="${current}"]`).getAttribute('aria-checked'), 'false');
    assert.equal(plain(f.root.querySelector('#theme-flash').innerHTML), `Your account’s theme is now ${label[other]}: it was changed in another tab or on another device. Reload to see it, or pick ${label[current]} to keep this one.`);
    assert.deepEqual(posts(f), [], 'reading it writes nothing');
    // Opened again in this tab, Settings draws from what was read.
    await f.viewSettings();
    assert.equal(f.root.querySelector('#email-optional').checked, false);
    // Pressing this tab's own theme now saves it (it did nothing, the account staying on the other one).
    await f.root.querySelector(`[data-theme="${current}"]`).onclick(); await settle();
    assert.deepEqual(posts(f), [['/api/ui-theme', {theme: current}]]);
    assert.deepEqual(f.reloads, [true]);
    // Pressing the theme the account has saved does nothing, as before.
    const g = frontend({fetchImpl: (path) => { throw new Error('Unexpected offline request: ' + path); }});
    g.S.me.user = {...g.S.me.user, ui_theme: current};
    await g.viewSettings();
    await g.root.querySelector(`[data-theme="${current}"]`).onclick(); await settle();
    assert.deepEqual(posts(g), []);
    assert.equal(g.root.querySelector('#theme-flash').innerHTML, '');
  });

  test(`${name}: a read from another account, or during a save, leaves Settings as it is`, async () => {
    let me = {user: {id: 8, email_opt_out: true, ui_theme: other}}, release;
    const f = frontend({fetchImpl: (path, options) => {
      if (path === '/api/me') return response(me);
      if (path === '/api/email-preferences') return new Promise((resolve) => { release = () => resolve(response({ok: true, email_opt_out: JSON.parse(options.body).email_opt_out})); });
      throw new Error('Unexpected offline request: ' + path);
    }});
    f.S.me.user = {...f.S.me.user, email_opt_out: false};
    f.location.pathname = '/settings';
    await f.viewSettings();
    const sw = f.root.querySelector('#email-optional');
    // Another account's reply (the tab reloads into that account instead): nothing of it is shown.
    await shown(f);
    assert.equal(f.S.me.user.email_opt_out, false);
    assert.equal(sw.checked, true);
    // A save still out keeps the switch where it was flipped.
    const g = frontend({fetchImpl: (path, options) => {
      if (path === '/api/me') return response(me);
      if (path === '/api/email-preferences') return new Promise((resolve) => { release = () => resolve(response({ok: true, email_opt_out: JSON.parse(options.body).email_opt_out})); });
      throw new Error('Unexpected offline request: ' + path);
    }});
    g.S.me.user = {...g.S.me.user, email_opt_out: false};
    g.location.pathname = '/settings';
    await g.viewSettings();
    const gs = g.root.querySelector('#email-optional');
    gs.checked = false; const saving = gs.onchange();
    me = {user: {id: 7, email_opt_out: false}};
    await shown(g);
    assert.equal(gs.checked, false, 'left where it was flipped');
    release(); await saving; await settle();
    assert.equal(gs.checked, false);
    assert.equal(posts(g).length, 1, 'one write');
  });

  test(`${name}: Billing's auto-reload follows a change saved elsewhere once its poll reads it, and says so`, async () => {
    let saved = {auto_reload: false, autoreload_amount: 25, autoreload_threshold: 8};
    const {cards, ledger, ...fig} = cardedBilling();
    const f = billingPage(cardedBilling(saved), [], {'/api/billing/figures': () => response({...fig, ...saved})});
    await f.viewBilling(); await settle();
    const box = f.root.querySelector('#autoreload');
    assert.equal(box.checked, false);
    const tick = await pollOf(f, '/api/billing/figures');
    assert.equal(f.root.querySelector('#autorel-flash').textContent.includes('another tab'), false, 'nothing changed, nothing said');
    // Turned on, at other amounts, in another tab.
    saved = {auto_reload: true, autoreload_amount: 40, autoreload_threshold: 10};
    await tick(); await settle();
    assert.equal(box.checked, true);
    assert.equal(f.root.querySelector('#reload-amt').value, 40);
    assert.match(f.root.querySelector('#autorel-sum').textContent, /^Reload \$40 at \$10$/);
    assert.match(f.root.querySelector('#autorel-when').innerHTML, /^<b>On\.<\/b>/);
    assert.equal(plain(f.root.querySelector('#autorel-flash').innerHTML), 'Auto-reload is now on: it reloads $40.00 at $10.00. It was changed in another tab or on another device.');
    // An amount being typed is never written over.
    const amt = f.root.querySelector('#reload-amt');
    amt.value = '55'; f.document.activeElement = amt;
    saved = {auto_reload: false, autoreload_amount: 25, autoreload_threshold: 8};
    await tick(); await settle();
    assert.equal(amt.value, '55');
    f.document.activeElement = f.document.body;
    await tick(); await settle();
    assert.equal(box.checked, false);
    assert.match(plain(f.root.querySelector('#autorel-flash').innerHTML), /^Auto-reload is now off\. It was changed in another tab or on another device\.$/);
    assert.deepEqual(posts(f).filter(([path]) => path !== '/api/billing/setup-intent'), [], 'the poll reads; it never writes');
    f.clearTimers();
  });

  const MODELS = {models: [{key: 'model-a', label: 'Model A', available: true}, {key: 'model-b', label: 'Model B', available: true}], default: 'model-a'};
  const panel = async (routes, status) => {
    const store = new Map([['tau_ap_open', '1']]);
    let f;
    f = frontend({storage: storageFor(store), fetchImpl: (path, options) => {
      if (path.startsWith('/api/chat?')) return response({messages: [], agent_busy: false});
      if (path === '/api/models') return response(MODELS);
      if (path === '/api/autopilot/status' && status) return response(status());
      if (path === '/api/notifications' && status) return response({items: [], unread: 0});
      if (routes[path]) { f.document.activeElement = f.document.body; return routes[path](JSON.parse(options.body)); }  // the save locks (blurs) it
      throw new Error('Unexpected offline request: ' + path);
    }});
    f.S.me = {...f.S.me, user: {...f.S.me.user, id: 7, autopilot: true}};
    f.S.ap = {...f.S.ap, autopilot: true, mode: 'paper', model: 'model-a', focus: []};
    const box = f.document.createElement('div'); box.id = 'autopilot-float'; f.document.body.appendChild(box);
    f.renderAutopilot(box, null);
    await settle();
    return {f, box};
  };

  test(`${name}: the autopilot's model says what it runs, and a change says it saved and keeps focus`, async () => {
    let fail = false;
    const {f, box} = await panel({'/api/autopilot/model': (b) => (fail ? response({error: 'Pick a model from the list, or leave it on the default.'}, 400) : response({ok: true, model: b.model}))});
    const sel = box.querySelector('#apf-model');
    assert.equal(sel.getAttribute('aria-describedby'), 'apf-model-hint');
    assert.equal(box.querySelector('#apf-model-hint').textContent, 'Runs the autopilot’s research and its chat replies. Its check-ins run on a set model.');
    f.document.activeElement = sel; sel.focused = false;
    sel.value = 'model-b'; await sel.onchange(); await settle();
    assert.deepEqual(posts(f), [['/api/autopilot/model', {model: 'model-b'}]]);
    assert.equal(plain(box.querySelector('#apf-flash').innerHTML), 'Saved: the autopilot’s next research sessions and chat replies use Model B.');
    assert.ok(hasFocus(f, sel), 'focus is back on the select');
    assert.equal(sel.disabled, false);
    fail = true;
    f.document.activeElement = sel; sel.focused = false;
    sel.value = 'model-a'; await sel.onchange(); await settle();
    assert.equal(posts(f).length, 2, 'one write per change, never again by itself');
    assert.equal(sel.value, 'model-b', 'a refused change puts it back');
    assert.match(box.querySelector('#apf-flash').innerHTML, /Pick a model from the list/);
    assert.ok(hasFocus(f, sel));
    f.clearTimers();
  });

  test(`${name}: the autopilot's markets select says it saved, and keeps focus`, async () => {
    const {f, box} = await panel({'/api/autopilot/focus': (b) => response({ok: true, focus: b.focus})});
    const sel = box.querySelector('#apf-focus');
    f.document.activeElement = sel; sel.focused = false;
    sel.value = 'btc'; await sel.onchange(); await settle();
    assert.deepEqual(posts(f), [['/api/autopilot/focus', {focus: ['btc']}]]);
    assert.equal(plain(box.querySelector('#apf-flash').innerHTML), 'Saved: the autopilot’s new research and strategies stay on Bitcoin.');
    assert.ok(hasFocus(f, sel), 'focus is back on the select');
    const sel2 = box.querySelector('#apf-focus');
    f.document.activeElement = sel2; sel2.focused = false;
    sel2.value = ''; await sel2.onchange(); await settle();
    assert.equal(plain(box.querySelector('#apf-flash').innerHTML), 'Saved: the autopilot may research and start strategies in any market.');
    f.clearTimers();
  });

  test(`${name}: Turn off live sends one write and says it took`, async () => {
    let release, armed = true;
    const st = () => ({mode: armed ? 'live' : 'paper', live_enabled: true, kalshi_connected: true, autopilot: true, autopilot_live: armed, live_running: 1, focus: []});
    const {f, box} = await panel({'/api/autopilot/live': () => new Promise((resolve) => { release = () => { armed = false; resolve(response({ok: true, autopilot_live: false})); }; })}, st);
    f.S.ap = st();
    f.apUpdate(box, f.S.ap);
    const off = box.querySelector('[data-ap="livetoggle"]');
    const first = off.onclick({currentTarget: off, target: off});
    off.onclick({currentTarget: off, target: off});
    await flush();
    assert.deepEqual(posts(f), [['/api/autopilot/live', {on: false}]], 'a second press while it is out sends nothing');
    release(); await first; await settle();
    assert.equal(plain(box.querySelector('#apf-flash').innerHTML), 'Live autopilot is off: the agent won’t move strategies to real money on its own. Strategies already trading live keep running; Kill pauses them.');
    assert.equal(box.querySelector('[data-ap="livetoggle"]'), null, 'the button goes once the status says so');
    f.clearTimers();
  });
}
