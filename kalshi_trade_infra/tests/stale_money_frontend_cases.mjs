// Shared cases, run against each bundle's offline harness (Tau and Meridian): no money figure on the Strategies list or
// a strategy's own page goes stale in silence. Walked 2026-10-31 at 390x844 and 1366x900 after a two-hour laptop lid
// and with a tab left open:
// - the Strategies list and a strategy's page read once, at load: their paper and demo P&L, trade counts and paused
//   books stood as they were, with no age, where Home polls, reads again back on the tab and says how old its figures
//   are while a read fails;
// - back from the lid, Home's line said "Last updated 2h ago, retrying" while its first read was out, before anything
//   had failed;
// Reads only: nothing here sends, retries or times out a write (api() never does, test_api_retry.mjs).
import assert from './assert_text.mjs';
import {test} from 'node:test';
import {shown} from './age_text.mjs';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const flip = async (f, hidden) => { f.document.hidden = hidden; for (const [type, on] of f.documentListeners) if (type === 'visibilitychange') on({}); await settle(); };
// Away a while (over WAKE_AWAY_MS), then back on the tab: the page's polls read at once.
const away = async (f, ms) => { await flip(f, true); f.shiftClock(ms); await flip(f, false); };
// The page's poll timers (poll(): one interval per poll, ticking only while the tab shows).
const polls = (f) => [...f.intervals.values()].filter((c) => String(c).includes('!document.hidden && gen === S.gen) tick()'));
const writes = (f) => f.requests.filter((r) => r.options && r.options.method && r.options.method !== 'GET');
const BAD = () => response({error: 'Bad request.'}, 400);
// Each harness records focus its own way (Tau marks the element, Meridian moves document.activeElement).
const hasFocus = (f, el) => !!el && (el.focused === true || f.document.activeElement === el);

export function staleMoneyCases({name, frontend, detailStrategy, dashboard}) {
  test(`${name}: the Strategies list reads again back on the tab and on a minute's poll, draws what changed, keeps picks and focus, and says how old a list that won't refresh is`, async () => {
    let pnl = 1.25, trades = 3, hang = null, down = false;
    const row = () => detailStrategy({id: 64, title: 'On paper', status: 'passed', deployed: true, books: ['paper'], real: {pnl, trades, open: 1, mode: 'paper'}});
    const other = detailStrategy({id: 61, title: 'Cleared pass', status: 'passed', out_of_sample: {pnl: 3.1, trades: 40, win_rate: 0.6, days: 9}});
    const list = () => ({counts: {total: 2, passed: 2, deployed: 1}, matched: 2, pages: 1, strategies: [row(), other]});
    const f = frontend({fetchImpl: (path) => {
      if (path.startsWith('/api/strategies')) {
        if (down) return BAD();
        return hang ? new Promise((resolve) => { hang = () => resolve(response(list())); }) : response(list());
      }
      return response({});
    }});
    f.S.me.markets = [{key: 'btc15m', category: 'Crypto'}];
    f.location.pathname = '/strategies'; f.location.search = '?status=';
    await f.viewStrategies(); await settle();
    const reads = () => f.requests.filter((r) => r.path.startsWith('/api/strategies')).length;
    const slips = () => f.root.querySelector('#slips');
    const note = () => f.root.querySelector('#strats-stale').textContent;
    assert.match(slips().textContent, /1\.25/);
    assert.equal(polls(f).length, 1, 'the list polls');
    assert.equal(note(), '');
    // A strategy picked for a bulk deploy, and keyboard focus on a strategy's link.
    const pick = slips().querySelector('.pick[data-id="61"]');
    pick.checked = true; pick.onchange();
    const link = slips().querySelector('td.name a[href="/strategies/64"]');
    f.document.activeElement = link;
    const before = reads();
    // Two hours under a laptop lid; back on the tab, the read goes out at once and the old figures say so while it's out.
    pnl = 3.4; trades = 7; hang = true;
    await away(f, 2 * 3600e3);
    assert.equal(reads(), before + 1, 'read again back on the tab');
    assert.equal(note(), 'Last updated 2h ago, refreshing');
    assert.match(slips().textContent, /1\.25/, 'the list stays while the read is out');
    hang(); hang = null; await settle();
    assert.equal(note(), '');
    assert.match(slips().textContent, /3\.40/);
    assert.doesNotMatch(slips().textContent, /1\.25/);
    assert.notEqual(slips().querySelector('td.name a[href="/strategies/64"]'), link, 'drawn again');
    assert.ok(hasFocus(f, slips().querySelector('td.name a[href="/strategies/64"]')), 'focus on the same link in the new list');
    assert.equal(slips().querySelector('.pick[data-id="61"]').checked, true, 'the pick kept');
    // The minute's poll: nothing changed, nothing drawn.
    const drawn = slips().innerHTML;
    f.shiftClock(60e3); polls(f)[0](); await settle();
    assert.equal(reads(), before + 2);
    assert.equal(slips().innerHTML, drawn);
    // It fails: the list stays, and once past its age its line says how old it is and that it is retrying.
    down = true;
    f.shiftClock(65e3); polls(f)[0](); await settle();
    f.shiftClock(65e3); polls(f)[0](); await settle();
    assert.equal(note(), 'Last updated 2m ago, retrying');
    assert.match(slips().textContent, /3\.40/);
    assert.equal(slips().querySelector('.load-fail'), null, 'never "didn’t load" over the list it has');
    // A bulk deploy (a write) is out: the poll reads nothing.
    down = false;
    const busyNow = reads();
    const btn = f.root.querySelector('#bulk-dep');
    btn.classList.add('busy');
    for (let i = 0; i < 4; i++) { f.shiftClock(130e3); polls(f)[0](); await settle(); }
    assert.equal(reads(), busyNow, 'no read while a write is out');
    btn.classList.remove('busy');
    assert.equal(writes(f).length, 0, 'the poll never writes');
    f.clearTimers();
  });

  test(`${name}: a strategy's page reads its money again back on the tab and on a minute's poll, in place, focus kept, and says when its deployments changed`, async () => {
    let detail = null, hang = null, down = false;
    const dep = (o = {}) => ({id: 501, mode: 'paper', status: 'running', closed_trades: 3, realized_pnl: 1.25, open_positions: 0, stake: 5, created_at: '2026-10-20T10:00:00Z', ...o});
    const make = (pnl, trades, o = {}) => detailStrategy({deployments: [dep({closed_trades: trades, realized_pnl: pnl, ...o})], book_totals: {paper: {pnl, trades, open: 0}}});
    detail = make(1.25, 3);
    const f = frontend({fetchImpl: (path) => {
      if (path === '/api/strategies/42') {
        if (down) return BAD();
        return hang ? new Promise((resolve) => { hang = () => resolve(response(detail)); }) : response(detail);
      }
      if (path === '/api/strategies/42/live-readiness') return BAD();
      return response({});
    }});
    f.location.pathname = '/strategies/42';
    await f.viewStrategy(42); await settle();
    const reads = () => f.requests.filter((r) => r.path === '/api/strategies/42').length;
    const record = () => f.root.querySelector('#since-deploying').textContent;
    const top = () => f.root.querySelector('.paper-top');
    const note = () => f.root.querySelector('#strat-stale').textContent;
    const moved = () => f.root.querySelector('#strat-moved').textContent;
    assert.match(record(), /\+\$1\.25 realized over 3 closed trades/);
    assert.match(top().textContent, /1\.25/);
    assert.equal(polls(f).length, 1, 'the page polls');
    // Focus on the paper line's "Details below".
    const details = top().querySelector('a[href="#since-deploying"]');
    f.document.activeElement = details;
    const before = reads();
    detail = make(4.5, 7); hang = true;
    await away(f, 2 * 3600e3);
    assert.equal(reads(), before + 1, 'read again back on the tab');
    assert.equal(note(), 'Last updated 2h ago, refreshing');
    hang(); hang = null; await settle();
    assert.equal(note(), '');
    assert.match(record(), /\+\$4\.50 realized over 7 closed trades/);
    assert.doesNotMatch(record(), /1\.25/);
    assert.match(top().textContent, /4\.50/);
    const details2 = top().querySelector('a[href="#since-deploying"]');
    assert.notEqual(details2, details, 'drawn again');
    assert.ok(hasFocus(f, details2), 'focus on the same link in the new line');
    assert.equal(moved(), '', 'its deployments are as drawn');
    // Paused elsewhere (the engine's loss guard): Since deploying says so, and the Deploy panel is said to be behind.
    detail = make(4.5, 7, {status: 'paused', status_why: 'Lost $10.00, its loss limit'});
    f.shiftClock(60e3); polls(f)[0](); await settle();
    assert.match(record(), /Paper paused/);
    assert.match(moved(), /Its deployments changed since this page opened, so the Deploy panel may be behind\./);
    const reload = f.root.querySelector('#strat-moved [data-strat-reload]');
    assert.ok(reload, 'with Reload');
    // It fails: the figures stay, and past their age the line says how old they are.
    down = true;
    f.shiftClock(65e3); polls(f)[0](); await settle();
    f.shiftClock(65e3); polls(f)[0](); await settle();
    assert.equal(note(), 'Last updated 2m ago, retrying');
    assert.match(record(), /4\.50/);
    // A write on the page is out (a deploy button busy): the poll reads nothing.
    down = false;
    const busyNow = reads();
    const btn = f.root.querySelector('#dep-paper');
    btn.classList.add('busy');
    for (let i = 0; i < 4; i++) { f.shiftClock(130e3); polls(f)[0](); await settle(); }
    assert.equal(reads(), busyNow, 'no read while a write is out');
    btn.classList.remove('busy');
    assert.equal(writes(f).length, 0, 'the poll never writes');
    f.clearTimers();
  });

  // Walked 2026-11-01 (w9756) with the engine stopped: each read of the strategy page came back the same (no newer mark),
  // so the poll drew nothing, and a holding marked a minute before load still read "Unrealized +$0.32 as of 20:46 CDT",
  // undimmed, four hours on, where Home and Deploy said "as of 4 hours ago" over a dimmed figure for the same position.
  test(`${name}: a strategy's held position whose mark goes old with no newer price says how old, on the poll, with the same read`, async () => {
    const markTs = new Date(Date.now() - 60e3).toISOString();
    const held = {id: 7, deployment_id: 501, mode: 'paper', ticker: 'KXBTC15M-26NOV012338-00', side: 'yes', qty: 10, entry_price: 0.42,
      cost: 4.38, at_cost: 4.2, mark: 0.47, value: 4.7, unrealized: 0.32, mark_ts: markTs, market_close_time: new Date(Date.now() + 6 * 3600e3).toISOString(), status: 'open'};
    const detail = detailStrategy({deployments: [{id: 501, mode: 'paper', status: 'running', closed_trades: 0, realized_pnl: 0, open_positions: 1, stake: 5, created_at: '2026-10-20T10:00:00Z'}],
      book_totals: {paper: {pnl: 0, trades: 0, open: 1}}, holdings: [held], holdings_books: {paper: {open_cost: 4.38, open_positions: 1}}});
    const f = frontend({fetchImpl: (path) => path === '/api/strategies/42' ? response(detail) : path === '/api/strategies/42/live-readiness' ? BAD() : response({})});
    f.location.pathname = '/strategies/42';
    await f.viewStrategy(42); await settle();
    const held1 = () => f.root.querySelector('#holding-now');
    assert.ok(held1(), 'it shows what it holds');
    assert.doesNotMatch(held1().textContent, /min ago|hours? ago/, 'a minute-old mark reads as its time');
    assert.equal(held1().querySelectorAll('.mark-old').length, 0);
    // Four minutes on, the same answer (the engine stopped marking): the holding and its book's line say how old.
    f.shiftClock(4 * 60e3); polls(f)[0](); await settle();
    assert.match(held1().textContent, /Unrealized \+\$0\.32 as of 5 min ago/);
    assert.ok(held1().querySelectorAll('.mark-old').length >= 2, 'its figure and its book’s sum are dimmed');
    // An hour on, it says the hour.
    f.shiftClock(60 * 60e3); polls(f)[0](); await settle();
    // Only the age moved: it is said again in place, and the part isn't written.
    const drawn = held1();
    assert.match(shown(held1()), /as of 65 min ago/);
    assert.equal(held1(), drawn, 'the holding stays as drawn');
    assert.equal(writes(f).length, 0, 'the poll never writes');
    f.clearTimers();
  });

  test(`${name}: back from a sleep Home's line says it is refreshing while its read is out, and retrying once that read failed`, async () => {
    let hang = null;
    const f = frontend({fetchImpl: (path) => {
      if (path === '/api/dashboard') return hang ? new Promise((resolve) => { hang = (r) => resolve(r); }) : response(dashboard());
      if (path === '/api/autopilot/status') return response({autopilot: false, mode: 'off'});
      return response({});
    }});
    await f.viewDashboard(); await settle();
    const note = () => f.root.querySelector('#dash-stale').textContent;
    assert.equal(note(), '');
    hang = true;
    await away(f, 2 * 3600e3);
    assert.equal(typeof hang, 'function', 'the read is out');
    assert.equal(note(), 'Last updated 2h ago, refreshing');
    hang(BAD()); hang = true; await settle();
    assert.equal(note(), 'Last updated 2h ago, retrying');
    f.clearTimers();
  });
}
