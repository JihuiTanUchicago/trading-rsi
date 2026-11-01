// Shared cases, run against each bundle's offline harness (Tau and Meridian): three trust gaps from the walks of
// 2026-11-01 (w9777, w9781).
// - A theme switched in Settings left the account's other open tabs in the old theme until a reload. Now the switch names
//   the account and theme in localStorage (tau_theme_switch), and the other tabs' 'storage' event follows it: at once
//   when nothing would be lost, else at the next link pressed or Back. Signed-out tabs and other accounts stay put.
// - Sharing a strategy to your own sign-in email went to the server, which refused it after the round trip. It is said
//   before the press goes anywhere.
// - An amount typed on Funds was lost when the session had ended before the press; Research and Community kept their
//   text for this tab and put it back after sign-in. Funds does the same (sessionStorage), and never sends it by itself.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const posts = (f) => f.requests.filter(({options}) => options && options.method === 'POST');
const store = (init = {}) => {
  const m = new Map(Object.entries(init));
  return {m, getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k)};
};
const SHARE_SELF = 'That’s your own email. Enter the email your friend signs in with.';

export function tabTrustCases({name, frontend, theme, storageFor, detailStrategy, fundsData}) {
  const other = theme === 'tau' ? 'meridian' : 'tau';
  // A tab whose window listeners the check can call (the 'storage' event another tab's switch fires here).
  const tab = (opts = {}) => {
    const heard = {};
    const f = frontend({...opts, win: {...(opts.win || {}), addEventListener: (n, fn) => { (heard[n] = heard[n] || []).push(fn); }}});
    f.fire = (key, newValue) => (heard.storage || []).forEach((fn) => fn({key, newValue}));
    f.heard = heard;
    return f;
  };
  const switched = (id, to) => `${id}:${to}:${Date.now()}`;

  test(`${name}: another tab of this account switching theme reloads this one into it, with no read or timer`, async () => {
    // Before: no tab listened, so this one stayed in the old theme until it was reloaded by hand.
    const f = tab();
    f.location.pathname = '/deploy';
    const timers = f.intervals.size + f.timeouts.size;
    f.fire('tau_theme_switch', switched(7, other));
    assert.deepEqual(f.reloads, [true]);
    assert.equal(f.S.me.user.ui_theme, other, 'the account’s theme as this tab knows it');
    assert.deepEqual(f.requests, [], 'no read');
    assert.equal(f.intervals.size + f.timeouts.size, timers, 'no timer');
  });

  test(`${name}: a signed-out tab, another account's tab, its own theme and other keys never follow`, async () => {
    const f = tab();
    f.location.pathname = '/deploy';
    f.fire('tau_theme_switch', switched(9, other));  // another account's switch
    f.fire('tau_ui', other);  // the page shell writes this one for whoever signs in: it says nothing about whose
    f.fire('tau_theme_switch', 'junk');
    f.fire('tau_theme_switch', switched(7, 'dark'));
    f.fire('tau_theme_switch', null);
    assert.deepEqual(f.reloads, []);
    assert.ok(!f.S.themeMoved);
    f.fire('tau_theme_switch', switched(7, theme));  // switched back to this tab's own theme: nothing to load
    assert.deepEqual(f.reloads, []);
    assert.ok(!f.S.themeMoved);
    f.S.me = null;  // signed out
    f.fire('tau_theme_switch', switched(7, other));
    assert.deepEqual(f.reloads, []);
    assert.ok(!f.S.themeMoved);
  });

  test(`${name}: a tab with a write out or words typed follows at the next link pressed, never under them`, async () => {
    const f = tab();
    f.location.pathname = '/deploy';
    const onClick = f.documentListeners.filter(([n]) => n === 'click').map(([, fn]) => fn).find((fn) => String(fn).includes('a[href^="/"]'));
    const link = f.document.createElement('a'); link.setAttribute('href', '/strategies'); f.root.appendChild(link);
    const press = () => { const ev = {target: link, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }}; onClick(ev); return ev.defaultPrevented; };
    f.S.writing = 1;
    f.fire('tau_theme_switch', switched(7, other));
    assert.deepEqual(f.reloads, [], 'a write out is never loaded under');
    assert.equal(f.S.themeMoved, true);
    assert.equal(press(), true, 'still out: the app draws the next page itself');
    f.S.writing = 0;
    assert.equal(press(), false, 'the browser loads the next page, in the account’s theme');
    // Words typed on the page: the event doesn't reload; a link press leaves the page's own box behind anyway.
    const g = tab();
    g.location.pathname = '/research';
    const box = g.document.createElement('textarea'); box.value = 'half an idea'; g.root.appendChild(box);
    g.fire('tau_theme_switch', switched(7, other));
    assert.deepEqual(g.reloads, []);
    assert.equal(box.value, 'half an idea');
    assert.equal(g.S.themeMoved, true);
    // Back after it, calm: a full load in the new theme. Popstate is the one the bundle listens for.
    box.value = '';
    const pop = g.heard.popstate.at(-1);
    pop();
    assert.deepEqual(g.reloads, [true]);
  });

  test(`${name}: a saved theme switch names this account and the theme for the other tabs, after the save`, async () => {
    const local = new Map([['tau_ui', theme]]);
    const f = frontend({storage: storageFor(local), session: store(), fetchImpl: (path, options) => {
      assert.equal(path, '/api/ui-theme'); return response({ok: true, theme: JSON.parse(options.body).theme});
    }});
    await f.viewSettings();
    await f.root.querySelector(`[data-theme="${other}"]`).onclick(); await settle();
    assert.match(local.get('tau_theme_switch'), new RegExp(`^7:${other}:\\d+$`));
    assert.deepEqual(f.reloads, [true]);
    // A save Tau didn't confirm tells no other tab anything.
    const kept = new Map([['tau_ui', theme]]);
    const g = frontend({storage: storageFor(kept), session: store(), fetchImpl: () => response({error: 'Synthetic outage'}, 503)});
    await g.viewSettings();
    await g.root.querySelector(`[data-theme="${other}"]`).onclick(); await settle();
    assert.equal(kept.has('tau_theme_switch'), false);
  });

  test(`${name}: sharing a strategy to your own sign-in email is said before anything is sent`, async () => {
    const f = frontend({fetchImpl: (path, options) => {
      if (path === '/api/strategies/42') return response(detailStrategy({deployments: []}));
      if (path === '/api/strategies/42/share' && options && options.method === 'POST') return response({ok: true});
      return response({});
    }});
    f.S.me.user.email = 'Me@Example.com';
    f.location.pathname = '/strategies/42';
    await f.viewStrategy(42); await settle();
    // Before: sent, and the server answered "That’s your own account." after the round trip.
    f.root.querySelector('#share-email').value = '  me@example.COM ';
    f.root.querySelector('#share-form').onsubmit({preventDefault() {}}); await settle();
    assert.match(f.root.querySelector('#shareflash').innerHTML, new RegExp(SHARE_SELF));
    assert.match(f.root.querySelector('#shareflash').innerHTML, /class="notice bad"/);
    assert.deepEqual(posts(f), [], 'nothing sent');
    // A friend's email goes as before.
    f.root.querySelector('#share-email').value = 'friend@example.com';
    f.root.querySelector('#share-form').onsubmit({preventDefault() {}}); await settle();
    assert.deepEqual(posts(f).map((r) => r.path), ['/api/strategies/42/share']);
    assert.match(f.root.querySelector('#shareflash').innerHTML, /Shared/);
    f.clearTimers();
  });

  test(`${name}: an amount typed on Funds before the session ended is back in its box after sign-in, and never sent by itself`, async () => {
    const session = store();
    const data = () => ({...fundsData(), kalshi_dev: {connected: true, key_id: 'DEVK…5678', balance: 50, balance_at: new Date().toISOString()},
      demo: {capital: 1000, equity: 1001, realized: 1, closed_trades: 1}});
    const open = (uid, onWrite) => {
      const f = frontend({session, fetchImpl: (path, options) => {
        if (options && options.method === 'POST') { onWrite && onWrite(path, JSON.parse(options.body)); return response({ok: true}); }
        return path === '/api/funds' ? response(data()) : response({});
      }});
      f.S.me.user.id = uid;
      f.location.pathname = '/funds';
      return f;
    };
    const type = (f, form, value) => {
      const el = f.root.querySelector(`#${form} input[name=amount]`);
      el.value = value;
      f.root.querySelector('#' + form).dispatchEvent({type: 'input'});
    };
    const f = open(7);
    await f.viewFunds(); await settle();
    type(f, 'paper', '250');
    type(f, 'demo', '1500.5');
    assert.deepEqual(JSON.parse(session.m.get('tau_funds_draft')), {uid: 7, amounts: {paper: '250', demo: '1500.5'}});
    f.clearTimers();
    // Signed back in on this tab: the boxes have it, and nothing is sent until a press.
    const writes = [];
    const g = open(7, (path, body) => writes.push([path, body]));
    await g.viewFunds(); await settle();
    assert.equal(g.root.querySelector('#paper input[name=amount]').value, '250');
    assert.equal(g.root.querySelector('#demo input[name=amount]').value, '1500.5');
    assert.deepEqual(posts(g), [], 'restored, never re-sent');
    // Another account on this tab never sees it.
    const h = open(9);
    await h.viewFunds(); await settle();
    assert.equal(h.root.querySelector('#paper input[name=amount]').value, '');
    h.clearTimers();
    // Pressed and saved: the page draws its forms afresh, and the kept amount goes with them.
    const pf = g.root.querySelector('#paper');
    pf.amount = pf.querySelector('input[name=amount]');  // the form's named control, as the DOM gives it
    pf.querySelector('[data-kind="deposit"]').onclick();
    await pf.onsubmit({preventDefault() {}}); await settle();
    assert.deepEqual(writes, [['/api/funds/paper', {kind: 'deposit', amount: 250}]]);
    assert.equal(session.m.has('tau_funds_draft'), false);
    assert.equal(g.root.querySelector('#paper input[name=amount]').value, '');
    g.clearTimers();
    // A box cleared forgets it too; a key's fields are never kept.
    const k = open(7);
    await k.viewFunds(); await settle();
    type(k, 'paper', '40');
    type(k, 'paper', '');
    assert.equal(session.m.has('tau_funds_draft'), false);
    assert.ok(![...session.m.keys()].some((key) => /key/i.test(key)));
    k.clearTimers();
  });
}
