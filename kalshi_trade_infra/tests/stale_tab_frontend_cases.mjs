// Shared cases, run against each bundle's offline harness (Tau and Meridian): a page left open while the app is updated
// under it. The app deploys every ~15 minutes, so an open tab often runs an older bundle against a newer server. Every
// JSON reply names each theme's build as its page shell carries it now (X-Tau-Build, app/web/page_modules.builds_header),
// and the page compares it with its own (window.__BUILD__, core.js buildMoved), with no request or timer of its own.
// Walk 2026-11-01 (w9777), before: Tau asked /api/version only on a page change, never on a page left open; Meridian
// never noticed; a route the new build moved (404) left a poll "retrying" for good or put "Not found" over the page; a
// page file from the new build said "Check your connection and try again", and Try again failed the same way for good.
// After: the page knows (Tau offers its one-press reload; Meridian shows no notice, d3b92d55); a link pressed loads the
// new build; a read or page the new build broke loads afresh by itself only when no write is out and nothing typed
// would be lost, once per build per tab; a write is never retried, aborted or reloaded under.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const settled = async (p) => { let done = false; p.then(() => { done = true; }, () => { done = true; }); await settle(); return done; };
const OLD = 'a1b2c3d4e5', NEW = 'f6e5d4c3b2', OTHER = '0a0b0c0d0e';
// A reply as fetch gives it, with the builds the server names (X-Tau-Build) and the account it read it as.
const reply = (data, {status = 200, build = null} = {}) => ({ok: status < 400, status, json: async () => data,
  headers: {get: (name) => { const n = String(name).toLowerCase(); return n === 'x-tau-build' ? build : n === 'x-tau-account' ? '7' : null; }}});
const sessionMap = (init = {}) => {
  const m = new Map(Object.entries(init));
  return {m, store: {getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k)}};
};
const VERBAR = 'A new version of Tau is ready.';
const PAGE_NEW = 'Tau was updated since this page opened. Reload the page to open this one.';

export function staleTabCases({name, frontend, theme, pagesKey}) {
  const said = (v) => (theme === 'tau' ? `tau:${v},meridian:${OTHER}` : `tau:${OTHER},meridian:${v}`);
  const tab = (fetchImpl, session) => frontend({fetchImpl, win: {__BUILD__: OLD}, build: OLD, ...(session ? {session} : {})});
  const typed = (f, inRoot) => {
    const box = f.document.createElement('textarea');
    box.value = 'half a message';
    (inRoot ? f.root : f.document.body).appendChild(box);
    return box;
  };

  test(`${name}: a page left open learns from a reply that the app was updated under it, with no request of its own`, async () => {
    // Before: nothing on a page left open (Tau asked /api/version on a page change only; Meridian never).
    let build = said(OLD);
    const f = tab(() => reply({ok: true}, {build}));
    await f.api('/api/deployments');
    assert.ok(!f.S.newBuild, 'its own build: nothing moved');
    build = theme === 'tau' ? `tau:${OLD},meridian:${NEW}` : `tau:${NEW},meridian:${OLD}`;
    await f.api('/api/deployments');
    assert.ok(!f.S.newBuild, 'the other theme’s build moving is not this page’s');
    build = said(NEW);
    await f.api('/api/deployments');
    assert.equal(f.S.newBuild, true);
    const bar = f.document.getElementById('verbar');
    if (theme === 'tau') assert.match(bar.innerHTML, new RegExp(VERBAR));
    else assert.equal(bar, null, 'Meridian shows no update notice (d3b92d55)');
    assert.deepEqual(f.requests.map((r) => r.path), ['/api/deployments', '/api/deployments', '/api/deployments'], 'no /api/version, no extra read');
    assert.deepEqual(f.reloads, [], 'knowing is not reloading');
    // A page with no build of its own (an older shell) or a reply naming none: nothing.
    const g = frontend({fetchImpl: () => reply({}, {build: said(NEW)})});
    await g.api('/api/deployments');
    assert.ok(!g.S.newBuild);
  });

  test(`${name}: a link pressed after the build moved loads the new build; a write out or words typed in the panel keep it in the app`, async () => {
    const f = tab(() => reply({}, {build: said(NEW)}));
    const onClick = f.documentListeners.filter(([n]) => n === 'click').map(([, fn]) => fn).find((fn) => String(fn).includes('a[href^="/"]'));
    const link = f.document.createElement('a'); link.setAttribute('href', '/deploy'); f.root.appendChild(link);
    const press = () => { const ev = {target: link, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }}; onClick(ev); return ev.defaultPrevented; };
    // The build hasn't moved: the app draws the page itself, as before.
    assert.equal(press(), true);
    assert.equal(f.navigations.at(-1), '/deploy');
    f.navigations.length = 0;
    await f.api('/api/me');
    assert.equal(f.S.newBuild, true);
    // Before: drawn by the old bundle against the new server. After: the browser follows the link, a full load.
    assert.equal(press(), false, 'the browser loads the page on the new build');
    assert.deepEqual(f.navigations, []);
    // Words typed on the page being left go with it either way, so they don't hold the load back.
    const own = typed(f, true);
    assert.equal(press(), false);
    own.remove(); f.root.children.splice(f.root.children.indexOf(own), 1);
    // A write out would be cut off by a load: the app draws the page itself.
    f.S.writing = 1;
    assert.equal(press(), true);
    f.S.writing = 0;
    // Words typed in what stays across pages (the autopilot panel's message) would be lost: the same.
    typed(f, false);
    assert.equal(press(), true);
  });

  test(`${name}: a read the new build moved (404) loads the page afresh once when calm; never a write, never over typed words`, async () => {
    const s = sessionMap();
    const f = tab(() => reply({error: 'Not found.'}, {status: 404, build: said(NEW)}), s.store);
    // Before: "Not found." over the page, or a poll "retrying" for good. After: the page loads afresh, nothing painted.
    assert.equal(await settled(f.api('/api/deployments')), false, 'the old code never paints the reply');
    assert.deepEqual(f.reloads, [true]);
    assert.equal(s.m.get('tau_build_reload'), OLD);
    // Once per build per tab: the same failure on the same old build stays a failure, so a fault can never loop.
    await assert.rejects(f.api('/api/deployments'), /Not found\./);
    assert.equal(f.reloads.length, 1);
    // Words typed anywhere on the page: no load; the read fails as before.
    const g = tab(() => reply({error: 'Not found.'}, {status: 404, build: said(NEW)}), sessionMap().store);
    typed(g, true);
    await assert.rejects(g.api('/api/deployments'), /Not found\./);
    assert.deepEqual(g.reloads, []);
    // A write is never loaded under, retried or turned into anything else: its refusal stands, sent once.
    const h = tab(() => reply({error: 'Not found.'}, {status: 404, build: said(NEW)}), sessionMap().store);
    await assert.rejects(h.api('/api/community', {body: 'hello'}), /Not found\./);
    assert.deepEqual(h.reloads, []);
    assert.equal(h.requests.filter((r) => r.options && r.options.method === 'POST').length, 1);
    assert.equal(h.S.writing, 0, 'the write is no longer out once it settles');
    // The build hasn't moved: a 404 is the thing being gone, said as before.
    const k = tab(() => reply({error: 'Not found.'}, {status: 404, build: said(OLD)}), sessionMap().store);
    await assert.rejects(k.api('/api/strategies/9'), /Not found\./);
    assert.deepEqual(k.reloads, []);
  });

  test(`${name}: a write is counted out while it runs, so nothing loads the page under it`, async () => {
    let answer;
    const f = tab((path) => (path === '/api/funds/paper' ? new Promise((r) => { answer = r; }) : reply({error: 'Not found.'}, {status: 404, build: said(NEW)})), sessionMap().store);
    const write = f.api('/api/funds/paper', {kind: 'deposit', amount: 5});
    await flush();
    assert.equal(f.S.writing, 1);
    await assert.rejects(f.api('/api/funds'), /Not found\./);
    assert.deepEqual(f.reloads, [], 'a moved read while a write is out fails as before');
    answer(reply({ok: true}, {build: said(NEW)}));
    assert.deepEqual({...(await write)}, {ok: true});
    assert.equal(f.S.writing, 0);
  });

  test(`${name}: a poll whose view breaks on a reply after the build moved loads afresh when calm`, async () => {
    const s = sessionMap();
    const f = tab(() => reply({}, {build: said(NEW)}), s.store);
    const broken = () => { throw new TypeError('Cannot read properties of undefined'); };
    await f.paced(broken, 10000)();
    assert.deepEqual(f.reloads, [], 'the build hasn’t moved: a view fault is a failed read, as before');
    await f.api('/api/me');
    await f.paced(broken, 10000)();
    assert.deepEqual(f.reloads, [true]);
    // A failed read (Tau unreachable, a deploy's gap) never loads the page: it stays on its last figures, retrying.
    const g = tab(() => reply({}, {build: said(NEW)}), sessionMap().store);
    await g.api('/api/me');
    await g.paced(() => Promise.reject(new g.ApiError(0, 'Can’t reach Tau. Check your connection.')), 10000)();
    assert.deepEqual(g.reloads, []);
  });

  test(`${name}: once Tau answers again after a deploy's gap, polls read at once instead of sitting out their back-off`, async () => {
    let down = false;
    const f = tab((path) => { if (down) return Promise.reject(new TypeError('Failed to fetch')); return reply({ok: path}, {build: said(OLD)}); });
    let reads = 0, fails = 0;
    f.poll(async (cfg) => { try { await f.api('/api/dashboard', undefined, cfg); reads++; } catch (e) { fails++; throw e; } }, 15000);
    const [tick] = [...f.intervals.values()].slice(-1);
    const run = async () => { tick(); for (let i = 0; i < 6; i++) { await settle(); f.elapse(2000); } await settle(); };
    await run();
    assert.equal(reads, 1);
    // The server goes away (a deploy's restart): the read retries twice, then fails, and the poll backs off.
    down = true;
    await run();
    assert.equal(fails, 1);
    down = false;
    await run();
    assert.equal(reads, 1, 'backing off: this tick sits out');
    // Before: the poll sat out its back-off, saying "retrying" over a server that was back. After: the first reply from
    // Tau (any read: the bell's, the autopilot's) wakes the page's polls, which read now.
    await f.api('/api/me');
    await settle();
    assert.equal(reads, 2, 'read at once, on Tau’s first answer');
    // A write that got no reply is never sent again by this: only reads go.
    down = true;
    await assert.rejects(f.api('/api/community', {body: 'hi'}), /Tau didn't confirm this/);
    down = false;
    await f.api('/api/me'); await settle();
    assert.equal(f.requests.filter((r) => r.options && r.options.method === 'POST').length, 1);
  });

  test(`${name}: a page file from the new build says Tau was updated and loads afresh when calm, instead of "check your connection"`, async () => {
    for (const calmNow of [true, false]) {
      const s = sessionMap();
      const f = tab(() => reply({}, {build: said(OLD)}), s.store);
      const boot = f.document.createElement('div');
      boot.id = 'tau-boot'; boot.dataset[pagesKey] = 'strategies:4d5e6f';
      f.document.body.appendChild(boot);
      const head = [];
      f.document.head = {appendChild: (el) => head.push(el)};
      delete f.PAGES.strategies;
      if (!calmNow) f.S.writing = 1;
      f.location.pathname = '/strategies';
      const done = f.render(); await flush();
      // The file the server sends now is the new build's: it asks for a helper this bundle doesn't have, and throws.
      assert.throws(() => f.window.TauPage('strategies', (T) => { const {fromTheNextBuild} = T; return {viewStrategies: fromTheNextBuild}; }), /asks for fromTheNextBuild/);
      head[0].onload(); await done; await settle();
      if (calmNow) {
        assert.deepEqual(f.reloads, [true], 'nothing to lose: the page loads on the new build');
      } else {
        assert.deepEqual(f.reloads, []);
        assert.match(f.root.innerHTML, new RegExp(PAGE_NEW));
        assert.doesNotMatch(f.root.innerHTML, /Check your connection/);
      }
    }
  });
}
