// Shared cases, run against each bundle's offline DOM/transport harness (Tau and Meridian): what the browser does on a
// heavy account between reads. Walked 2026-11-01 in Chromium (Playwright, CDP metrics, a phone at 4x CPU) on a seeded
// account (80 strategies, 46 deployments, 115 open and ~1,400 closed positions, a session with ~5,000 log lines):
// - moving between pages kept every page visited in memory: each page's poll waker and day watch said it was done only
//   when called, on a wake or a day turn, so a round of the main pages added ~16,000 DOM nodes and 1MB of heap for good;
// - Deploy's 46 sparklines and Meridian's 115 position "Expires" lines built a date formatter each, on every poll;
// - an unchanged read still wrote the page: the Strategies chips, a live session's badge and wait line (every second),
//   the hot-markets rail, the bell, the autopilot pill and its dashboard card;
// - the chart's hidden text-size probe sat in the page's layout, so the next width read laid the whole page out again.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 8; i++) await flush(); };
const ago = (ms) => new Date(Date.now() - ms).toISOString();

// Counts what a script writes to one element's property, on that element only (its prototype is left as it is).
function spy(el, prop) {
  let n = 0, proto = Object.getPrototypeOf(el), d;
  while (proto && !(d = Object.getOwnPropertyDescriptor(proto, prop))) proto = Object.getPrototypeOf(proto);
  if (d && d.set) {
    Object.defineProperty(el, prop, {configurable: true, get() { return d.get.call(this); }, set(v) { n++; d.set.call(this, v); }});
  } else {
    let v = el[prop];
    Object.defineProperty(el, prop, {configurable: true, get() { return v; }, set(x) { n++; v = x; }});
  }
  return () => n;
}
function spyCalls(obj, name) {
  let n = 0;
  const was = obj[name];
  obj[name] = function (...a) { n++; return was.apply(this, a); };
  return () => n;
}

export function browserCostCases({name, frontend, Element, dashboard}) {
  const counts = {research_running: 0, research_queued: 0, research_paused: 0};
  const research = (path) => {
    if (path.startsWith('/api/runs/9')) return response({run: {id: 9, prompt: 'Idea', market: 'btc15m', status: 'succeeded', created_at: ago(864e5), started_at: ago(864e5), finished_at: ago(80e6)}, events: [], strategies: []});
    if (path.startsWith('/api/runs')) return response({runs: [], counts, policy: null, totals: {tried: 0, passed: 0}, v: 'v1'});
    if (path === '/api/autopilot/status') return response({autopilot: false, mode: 'off'});
    return response({});
  };

  test(`${name}: a page left takes its poll wakers and day watches with it, so pages visited don't stay in memory`, async () => {
    const f = frontend({fetchImpl: research});
    const own = () => [...f.wakers, ...f.dayWatch].filter((w) => w.gen != null);
    f.location.pathname = '/research';
    await f.render(); await settle();
    const after1 = own().length;
    assert.ok(after1 >= 1, 'the session list has a waker for its poll');
    for (const path of ['/research/9', '/research', '/research/9', '/research', '/research/9', '/research']) {
      f.location.pathname = path;
      await f.render(); await settle();
    }
    assert.equal(own().length, after1, 'the same as after the first visit: none of the six pages left is kept');
    assert.ok(own().every((w) => w.gen === f.S.gen), 'only the page showing has any');
    // The ones every page shares (the day's own watches, the autopilot's and the rail's wakers) stay.
    assert.ok([...f.dayWatch].some((w) => w.gen == null));
    // The page showing still wakes: back online, its poll reads at once.
    const before = f.requests.filter((r) => r.path.startsWith('/api/runs')).length;
    [...f.wakers].forEach((w) => w());
    await settle();
    assert.ok(f.requests.filter((r) => r.path.startsWith('/api/runs')).length > before, 'the waker of the page showing still reads');
    f.clearTimers();
  });

  const dep = (id) => ({id, strategy_id: id, title: 'Dep ' + id, market: 'btc15m', mode: 'paper', status: 'running', status_reason: null,
    realized_pnl: 1.5, closed_trades: 3, open_positions: 0, unrealized_pnl: null, created_at: '2026-10-24T08:00:00Z', stake: 5, max_positions: 1, backtest: {},
    curve: [[1761300000, 0], [1761400000 + id, 0.5], [1761900000, 1.5]]});
  test(`${name}: Deploy's sparklines say their days through one kept formatter, however many rows it draws`, async () => {
    const built = async (n) => {
      let calls = 0;
      class CountingDate extends Date {
        toLocaleString(...a) { calls++; return super.toLocaleString(...a); }
        toLocaleDateString(...a) { calls++; return super.toLocaleDateString(...a); }
        toLocaleTimeString(...a) { calls++; return super.toLocaleTimeString(...a); }
      }
      const f = frontend({globals: {Date: CountingDate}, fetchImpl: (path) => {
        if (path.startsWith('/api/deployments')) return response({deployments: Array.from({length: n}, (_, i) => dep(i + 1)), open_positions: [], demo_health: null, v: 'b1'});
        if (path.startsWith('/api/strategies?status=passed')) return response({counts: {passed: 1}});
        return response({});
      }});
      f.location.pathname = '/deploy';
      await f.viewDeploy(); await settle();
      f.clearTimers();
      return {calls, labels: f.root.querySelectorAll('svg').map((e) => e.getAttribute('aria-label') || '').filter((l) => /P&(amp;)?L/.test(l))};
    };
    const few = await built(2), many = await built(24);
    assert.equal(many.labels.length, 24, 'a sparkline a row');
    assert.equal(many.calls, few.calls, 'no more toLocale*String calls for 22 more sparklines');
    // The words are toLocaleDateString's.
    const day = (t) => new Date(t * 1000).toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
    assert.ok(many.labels.every((l) => l.includes(`from ${day(1761300000)} to ${day(1761900000)}`)), 'each names its days as before');
  });

  test(`${name}: the dashboard's positions say their expiry through kept formatters, however many it lists`, async () => {
    const pos = (i) => ({id: i, deployment_id: 1, side: 'yes', qty: 2, entry_price: 0.4, cost: 0.8, title: 'Dep 1', ticker: 'KXBTC15M-26NOV010915-1' + i,
      mode: 'paper', opened_at: ago(3 * 3600e3), market_close_time: '2026-11-01T09:15:00Z', unrealized: null});
    const built = async (n) => {
      let calls = 0;
      class CountingDate extends Date {
        toLocaleString(...a) { calls++; return super.toLocaleString(...a); }
        toLocaleDateString(...a) { calls++; return super.toLocaleDateString(...a); }
        toLocaleTimeString(...a) { calls++; return super.toLocaleTimeString(...a); }
      }
      const f = frontend({globals: {Date: CountingDate}, fetchImpl: (path) => {
        if (path.startsWith('/api/dashboard')) return response({...dashboard(), deployments: [dep(1)], open_positions: Array.from({length: n}, (_, i) => pos(i + 1))});
        if (path === '/api/autopilot/status') return response({autopilot: false, mode: 'off'});
        return response({});
      }});
      f.location.pathname = '/';
      await f.viewDashboard(); await settle();
      f.clearTimers();
      return {calls, html: f.root.querySelector('#main').innerHTML};
    };
    const few = await built(2), many = await built(30);
    assert.equal(many.calls, few.calls, 'no more toLocale*String calls for 28 more positions');
    if (name === 'Meridian') {
      const close = new Date('2026-11-01T09:15:00Z');
      assert.ok(many.html.includes(close.toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'long', hourCycle: 'h23'})), 'the Expires line in the same words');
      assert.ok(many.html.includes(close.toLocaleString(undefined, {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZoneName: 'short'})), 'the Expiry cell in the same words');
    }
  });

  test(`${name}: an unchanged Strategies read leaves its chips as they are, and a changed count still rewrites them`, async () => {
    let passed = 3;
    const s = {id: 42, status: 'passed', side: 'yes', stake: 5, title: 'Edge', market: 'btc15m', direction: 'momentum', created_at: '2026-10-17T10:00:00Z', deployments: [], equity: [], out_of_sample: {pnl: 1, trades: 40}};
    const f = frontend({fetchImpl: (path) => {
      if (path.startsWith('/api/strategies')) return response({strategies: [s], counts: {total: 5, passed, failed: 1, rejected: 1, deployed: 0}, matched: 1, pages: 1});
      if (path === '/api/autopilot/status') return response({autopilot: false, mode: 'off'});
      return response({});
    }});
    f.location.pathname = '/strategies';
    await f.viewStrategies(); await settle();
    const chips = [...f.root.querySelectorAll('[data-f="status"]'), f.root.querySelector('[data-toggle="deployed"]')];
    assert.match(chips[1].textContent, /Passed \(3\)/);
    const writes = chips.map((c) => spy(c, 'textContent')), hides = chips.map((c) => spy(c, 'hidden'));
    const total = () => writes.reduce((a, w) => a + w(), 0) + hides.reduce((a, w) => a + w(), 0);
    for (const t of [...f.intervals.values()]) t();
    await settle();
    assert.ok(f.requests.filter((r) => r.path.startsWith('/api/strategies')).length >= 2, 'the poll read the list again');
    assert.equal(total(), 0, 'the same counts: no chip written');
    passed = 4;
    for (const t of [...f.intervals.values()]) t();
    await settle();
    assert.match(chips[1].textContent, /Passed \(4\)/);
    assert.ok(total() >= 1);
    f.clearTimers();
  });

  test(`${name}: a live session's badge and wait line are written only when their words change, not every second`, async () => {
    const at = ago(10 * 60e3);
    let first = true;  // the server sends a line once: later reads carry only newer ones (none here)
    const f = frontend({fetchImpl: (path) => {
      if (!path.startsWith('/api/runs/9')) return response({});
      const events = first ? [{id: 1, kind: 'step', message: 'Loaded 60 days of history.', data: {phase: 'data_loaded'}, created_at: at}] : [];
      first = false;
      return response({run: {id: 9, prompt: 'Idea', market: 'btc15m', created_at: at, started_at: at, status: 'running', run_forever: 1}, events, strategies: []});
    }});
    await f.viewRun(9); await settle();
    const host = f.root.querySelector('#rsiobs'), live = host.querySelector('[data-live]'), text = host.querySelector('[data-livetext]');
    const wait = host.querySelector('[data-wait]'), phase = host.querySelector('[data-phase]');
    const said = text.textContent;
    assert.match(said, /10 min|10m/);
    const n = [spy(live, 'className'), spy(text, 'textContent'), spy(wait, 'hidden'), spy(wait, 'textContent'), spy(phase, 'hidden'),
      spyCalls(host.classList, 'add'), spyCalls(host.classList, 'remove')];
    const each = [...f.intervals.values()].find((cb) => /^function paintLive/.test(String(cb)));
    assert.ok(each, 'the 1s ticker');
    for (let i = 0; i < 5; i++) each();
    // The 2s poll with nothing new: the same.
    const poll = f.intervals.get(f.S.timers.at(-1));
    poll(); await settle();
    assert.equal(text.textContent, said);
    assert.deepEqual(n.map((c) => c()), [0, 0, 0, 0, 0, 0, 0], 'nothing written while nothing it says changed');
    assert.equal(host.classList.contains('is-quiet'), true, 'ten quiet minutes: the panel stays quiet, never taken off and put back');
    // Over a minute later its words move on, and are written.
    f.shiftClock(61e3); each();
    assert.notEqual(text.textContent, said);
    f.clearTimers();
  });

  test(`${name}: the hot-markets rail is drawn again only when what it shows changed`, () => {
    const f = frontend({fetchImpl: () => response({})});
    const rail = f.document.createElement('aside'); rail.id = 'hotrail'; rail.attributes.id = 'hotrail'; f.document.body.appendChild(rail);
    f.S.hot = {markets: []}; f.S.hotAt = Date.now();
    f.renderHotRail();
    assert.equal(rail.writes, 1);
    assert.match(rail.innerHTML, /Warming up/);
    f.renderHotRail(); f.renderHotRail();
    assert.equal(rail.writes, 1, 'the same snapshot: not drawn again');
    f.S.hot = null; f.S.hotFail = true;
    f.renderHotRail();
    assert.equal(rail.writes, 2);
    assert.match(rail.innerHTML, /didn’t load/);
  });

  test(`${name}: the autopilot pill and its dashboard card write nothing for a status read that changed nothing`, async () => {
    const f = frontend({fetchImpl: (path) => {
      if (path.startsWith('/api/dashboard')) return response(dashboard());
      if (path === '/api/autopilot/status') return response({autopilot: true, mode: 'paper', research: {}, story: []});
      return response({});
    }});
    f.S.me.user.autopilot = true;
    f.location.pathname = '/';
    await f.viewDashboard(); await settle();
    const status = {autopilot: true, mode: 'paper', research: {}, story: []};
    f.S.ap = status;
    const box = f.document.createElement('div');
    f.renderAutopilot(box, status);
    const cls = spy(box, 'className'), shell = spy(box.dataset, 'shell'), html = box.writes;
    f.renderAutopilot(box, status); f.renderAutopilot(box, status);
    assert.equal(cls(), 0); assert.equal(shell(), 0); assert.equal(box.writes, html);
    if (name === 'Tau') {
      // Tau's pill steps aside (yield) over what it covers; a status read keeps that and leaves the class as it is.
      box.classList.add('yield');
      const before = cls();
      f.renderAutopilot(box, status);
      assert.equal(cls(), before, 'the stepped-aside pill keeps its class');
      assert.ok(box.classList.contains('yield'));
    }
    const card = f.root.querySelector('#dash-ap');
    if (card) {
      f.paintDashAp();
      const cardCls = spy(card, 'className');
      f.paintDashAp(); f.paintDashAp();
      assert.equal(cardCls(), 0, 'the card keeps its class');
    }
    f.clearTimers();
  });

  test(`${name}: the open autopilot's pulse keeps its class through a chat poll that changed nothing`, async () => {
    // Meridian's harness takes the Map, Tau's a storage: this one is both.
    const store = Object.assign(new Map([['tau_ap_open', '1']]), {getItem(k) { return this.get(k) ?? null; }, setItem(k, v) { this.set(k, String(v)); }});
    const f = frontend({storage: store, fetchImpl: (path) => {
      if (path.startsWith('/api/chat?')) return response({messages: [], agent_busy: false});
      if (path === '/api/models') return response({models: [], default: ''});
      return response({});
    }});
    f.S.me = {...f.S.me, user: {...(f.S.me && f.S.me.user), id: 7, autopilot: true}};
    f.S.ap = {...f.S.ap, autopilot: true, mode: 'paper', paper_running: 2};
    const box = f.document.createElement('div'); box.id = 'autopilot-float'; f.document.body.appendChild(box);
    f.renderAutopilot(box, null); await settle();
    const pulse = box.querySelector('#apf-pulse');
    assert.equal(pulse.className, 'runpulse apf-pulse-row');
    const cls = spy(pulse, 'className');
    for (let i = 0; i < 3; i++) { await f.apLoadChat(box, false); await flush(); }
    assert.equal(cls(), 0, 'the same class is not written again');
    assert.equal(pulse.className, 'runpulse apf-pulse-row');
    assert.equal(pulse.hidden, false);
    f.clearTimers();
  });

  test(`${name}: the bell's minute read with the same list leaves the list and its head as drawn`, async () => {
    const notes = {unread: 1, items: [{id: 4, kind: 'paused', body: 'Edge was paused.', link: '/deploy', created_at: ago(2 * 86400e3), read: false, more: 0}]};
    const f = frontend({notifications: () => response(structuredClone(notes)), fetchImpl: (path) => {
      if (path === '/api/notifications/read') return response({ok: true});
      if (path === '/api/autopilot/status') return response({mode: 'off'});
      return response({});
    }});
    f.location.pathname = '/';
    await f.render(); await settle();
    const list = f.root.querySelector('#bell-list'), head = f.root.querySelector('.bell-head'), trig = f.root.querySelector('.bell-trig');
    const reads = () => f.requests.filter(({path}) => path === '/api/notifications').length;
    const w = [list.writes, head.writes], label = spyCalls(trig, 'setAttribute');
    f.shiftClock(61e3); [...f.intervals.values()].forEach((t) => t()); await settle();
    assert.equal(reads(), 2, 'read again after a minute');
    assert.deepEqual([list.writes, head.writes], w, 'the same list: not drawn again');
    assert.equal(label(), 0);
    notes.items.unshift({id: 5, kind: 'pass', body: 'Edge passed.', link: '/deploy', created_at: ago(2 * 86400e3), read: false, more: 0}); notes.unread = 2;
    f.shiftClock(61e3); [...f.intervals.values()].forEach((t) => t()); await settle();
    assert.equal(list.writes, w[0] + 1);
    assert.match(list.innerHTML, /Edge passed/);
    assert.equal(trig.getAttribute('aria-label'), 'Notifications, 2 unread');
    f.clearTimers();
  });

  test(`${name}: a chart's text-size probe sits out of the page's layout and still reads the size`, () => {
    const made = [];
    const f = frontend({fetchImpl: () => response({}), globals: {getComputedStyle: (el) => ({fontSize: el.tag === 'text' ? '22px' : '32px'})}});
    f.document.createElementNS = (_ns, tag) => { const e = new Element(tag); made.push(e); return e; };
    const curve = [[1761000000, 0], [1761400000, -420.15], [1762500000, 640.25], [1763600000, 1584.53]];
    const svg = f.equityChart(curve, null, null, {width: 328, fit: true});
    const probe = made.find((e) => e.tag === 'svg');
    assert.ok(probe, 'measured');
    assert.equal(probe.style.cssText, 'display:none', 'no box in the layout: a width read after it lays nothing out');
    assert.equal(probe.removed, true);
    assert.match(svg, /\$1\.6k/, 'measured at 200% text, labels compact as before');
  });
}
