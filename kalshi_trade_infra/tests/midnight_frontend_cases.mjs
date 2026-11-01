// Shared cases, run against each bundle's offline harness (Tau and Meridian): a tab left open across midnight never
// shows yesterday as "Today". From the midnight walk of 2026-11-01 (America/Chicago and Asia/Tokyo, each page opened
// at 23:55 and moved past the reader's midnight and past midnight UTC, with no read, with the next poll's read and
// after a return to the tab):
// - Billing's "Today" heading changed only when the balance did: past midnight the morning's lines still sat under
//   "Today", and for good while the page's reads failed.
// - The autopilot feed's "Today" and "Yesterday" day lines never moved: the evening's check-ins stayed "Today" after
//   the reader's midnight, and a check-in after it got a second "Today" under them.
// - "Today (UTC)" on Home's paper and demo books and on the autopilot panel kept its label over the day before's
//   figure until a read landed, and for good while the reads failed or waited.
// Each label now draws again from what the page holds when the day turns (on the page's poll tick, its read
// answered or not, and on a return to the tab), and a day figure read before midnight UTC says "Yesterday (UTC)".
// Display only: every figure is the server's, unchanged, and nothing reads more often.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
// How far the page's clock moves: five minutes past the reader's next midnight, or past the next midnight UTC.
const pastLocalMidnight = () => { const n = new Date(); return new Date(n.getFullYear(), n.getMonth(), n.getDate() + 1, 0, 5).getTime() - n.getTime(); };
const pastUtcMidnight = () => { const n = Date.now(); return (Math.floor(n / 864e5) + 1) * 864e5 + 5 * 60e3 - n; };
const ownDay = (iso) => { const d = new Date(iso); return [d.getFullYear(), d.getMonth() + 1, d.getDate()].map((n) => String(n).padStart(2, '0')).join('-'); };
const weekday = (iso) => new Date(iso).toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'});
const ticks = async (f) => { for (const tick of [...f.intervals.values()]) { await tick(); await settle(); } };
const back = async (f) => {
  f.document.hidden = true; for (const [n, fn] of f.documentListeners) if (n === 'visibilitychange') fn({});
  f.document.hidden = false; for (const [n, fn] of f.documentListeners) if (n === 'visibilitychange') fn({});
  await settle();
};

export function midnightCases({name, frontend, billingPage, cardedBilling, dashboard, storageFor}) {
  for (const [way, move] of [['its own poll tick, the read failing', ticks], ['a return to the tab', back]]) {
    test(`${name}: Billing's "Today" heading becomes the day's date past the reader's midnight, on ${way}`, async () => {
      const at = new Date().toISOString();
      let fail = false;
      const data = cardedBilling({math: {days: 30, start: 10, bought: 0, purchases: 0, other: 0, usage: 0.3, end: 9.7},
        ledger: [{kind: 'usage', amount: -0.3, memo: 'Chat', item: 'Chat', calls: 1, created_at: at, first_at: at, day: ownDay(at)}]});
      const f = billingPage(data, [], {'/api/billing/figures': () => { if (fail) throw new Error('offline'); return response({balance: data.balance}); }});
      await f.viewBilling(); await settle();
      const heads = () => f.root.querySelectorAll('.t-ledger tr.ledger-day b').map((b) => b.textContent);
      assert.deepEqual(heads(), ['Today']);
      fail = true;
      f.shiftClock(pastLocalMidnight());
      await move(f);
      assert.deepEqual(heads(), [weekday(at)]);
      f.clearTimers(); assert.equal(f.S.timers.length, 0);
    });
  }

  test(`${name}: Billing's "Today" heading follows the next poll past midnight though the balance didn't move`, async () => {
    const at = new Date().toISOString();
    const data = cardedBilling({math: {days: 30, start: 10, bought: 0, purchases: 0, other: 0, usage: 0.3, end: 9.7},
      ledger: [{kind: 'usage', amount: -0.3, memo: 'Chat', item: 'Chat', calls: 1, created_at: at, first_at: at, day: ownDay(at)}]});
    const f = billingPage(data, [], {'/api/billing/figures': () => response({balance: data.balance})});
    await f.viewBilling(); await settle();
    f.shiftClock(pastLocalMidnight());
    await ticks(f);
    assert.deepEqual(f.root.querySelectorAll('.t-ledger tr.ledger-day b').map((b) => b.textContent), [weekday(at)]);
    f.clearTimers();
  });

  test(`${name}: the autopilot feed's day lines turn past the reader's midnight, and a check-in after it gets its own "Today"`, async () => {
    const store = new Map([['tau_ap_open', '1']]);
    const at = new Date().toISOString();
    let msgs = [{id: 1, role: 'system', content: 'Reviewed: nothing to change.', created_at: at}], fail = false;
    const f = frontend({storage: storageFor(store), fetchImpl: (path) => {
      if (path.startsWith('/api/chat?')) { if (fail) throw new Error('offline'); return response({messages: msgs, agent_busy: false}); }
      if (path === '/api/models') return response({models: [], default: ''});
      throw new Error('Unexpected offline request: ' + path);
    }});
    f.S.me = {...f.S.me, user: {...f.S.me.user, id: 7, autopilot: false}};
    const box = f.document.createElement('div'); box.id = 'autopilot-float'; f.document.body.appendChild(box);
    f.renderAutopilot(box, null); await settle();
    await f.apLoadChat(box, true); await settle();
    const days = () => box.querySelectorAll('.ap-day').map((li) => li.textContent.split(' ·')[0].split(' ·')[0]);
    assert.deepEqual(days(), ['Today']);
    f.shiftClock(pastLocalMidnight());
    // A return to the tab, the chat's read failing: the line turns from what the feed holds.
    fail = true;
    await back(f);
    assert.deepEqual(days(), ['Yesterday']);
    // The next read brings a check-in after midnight: it starts its own day.
    fail = false;
    const later = new Date(Date.now() + pastLocalMidnight()).toISOString();
    msgs = [{id: 2, role: 'system', content: 'Reviewed: still nothing to change.', created_at: later}];
    await f.apLoadChat(box, false); await settle();
    assert.deepEqual(days(), ['Yesterday', 'Today']);
  });

  test(`${name}: the autopilot feed's "Today" turns on the chat's next read past midnight, with nothing new in it`, async () => {
    const store = new Map([['tau_ap_open', '1']]);
    const at = new Date().toISOString();
    let msgs = [{id: 1, role: 'user', content: 'Anything open?', created_at: at}];
    const f = frontend({storage: storageFor(store), fetchImpl: (path) => {
      if (path.startsWith('/api/chat?')) return response({messages: msgs, agent_busy: false});
      if (path === '/api/models') return response({models: [], default: ''});
      throw new Error('Unexpected offline request: ' + path);
    }});
    f.S.me = {...f.S.me, user: {...f.S.me.user, id: 7, autopilot: false}};
    const box = f.document.createElement('div'); box.id = 'autopilot-float'; f.document.body.appendChild(box);
    f.renderAutopilot(box, null); await settle();
    await f.apLoadChat(box, true); await settle();
    f.shiftClock(pastLocalMidnight());
    msgs = [];
    await f.apLoadChat(box, false); await settle();
    assert.equal(box.querySelectorAll('.ap-day').map((li) => li.textContent)[0].startsWith('Yesterday'), true);
  });

  // The paper book's day figure on Home, by the theme's own markup: Tau's line under the paper card, Meridian's cell.
  const dayLabels = (f) => f.root.querySelectorAll('.pnl-win').map((e) => e.textContent).filter((t) => /\(UTC\)/.test(t));
  for (const [way, move] of [['its own poll tick, the read failing', ticks], ['a return to the tab, the read still out', back]]) {
    test(`${name}: Home's "Today (UTC)" paper and demo figures say "Yesterday (UTC)" past midnight UTC on ${way}`, async () => {
      const closedAt = new Date().toISOString();
      let mode = 'ok';
      const answer = () => {
        const base = dashboard();
        return {...base, recent_closed: [
          {id: 1, deployment_id: 1, mode: 'paper', side: 'yes', qty: 2, entry_price: 0.4, pnl: 1.25, title: 'Fade', ticker: 'KXBTC15M-T1', closed_at: closedAt},
          {id: 2, deployment_id: 1, mode: 'demo', side: 'yes', qty: 2, entry_price: 0.4, pnl: 0.75, title: 'Fade', ticker: 'KXBTC15M-T2', closed_at: closedAt}],
        paper: {...base.paper, closed_trades: 3, today_pnl: 1.25, realized_pnl: 12.5},
        demo: {...base.demo, equity: 1000.75, realized_pnl: 0.75, closed_trades: 1, today_pnl: 0.75, connected: true}};
      };
      const f = frontend({fetchImpl: (path) => {
        if (path.startsWith('/api/dashboard')) {
          if (mode === 'fail') throw new Error('offline');
          if (mode === 'hang') return new Promise(() => {});
          return response(answer());
        }
        if (path === '/api/autopilot/status') return response({autopilot: false, mode: 'off'});
        if (path.startsWith('/api/track-record')) return response({trades: [], curve: []});
        return response({messages: [], models: []});
      }});
      await f.viewDashboard(); await settle();
      const before = dayLabels(f);
      assert.ok(before.length >= 1 && before.every((t) => t === 'Today (UTC):'), JSON.stringify(before));
      mode = way.includes('still out') ? 'hang' : 'fail';
      f.shiftClock(pastUtcMidnight());
      await move(f);
      const after = dayLabels(f);
      assert.equal(after.length, before.length, JSON.stringify(after));
      assert.ok(after.every((t) => t === 'Yesterday (UTC):'), JSON.stringify(after));
      // The figure is still the one the server counted for that day: relabelled, never re-dated as today's.
      assert.match(f.root.querySelectorAll('.pnl-win').map((e) => e.parent.parent.innerHTML).join(' '), /\+\$1\.25/);
      f.clearTimers();
    });
  }

  test(`${name}: Home's day figure reads "Today (UTC)" again once the poll brings the new day's figure`, async () => {
    const closedAt = new Date().toISOString();
    let today = 1.25;
    const f = frontend({fetchImpl: (path) => {
      if (path.startsWith('/api/dashboard')) {
        const base = dashboard();
        return response({...base, recent_closed: [{id: 1, deployment_id: 1, mode: 'paper', side: 'yes', qty: 2, entry_price: 0.4, pnl: 1.25, title: 'Fade', ticker: 'KXBTC15M-T1', closed_at: closedAt}],
          paper: {...base.paper, closed_trades: 3, today_pnl: today, realized_pnl: 12.5}});
      }
      if (path === '/api/autopilot/status') return response({autopilot: false, mode: 'off'});
      if (path.startsWith('/api/track-record')) return response({trades: [], curve: []});
      return response({messages: [], models: []});
    }});
    await f.viewDashboard(); await settle();
    f.shiftClock(pastUtcMidnight());
    today = 0;
    await ticks(f);
    assert.deepEqual([...new Set(dayLabels(f))], ['Today (UTC):']);
    assert.doesNotMatch(f.root.querySelectorAll('.pnl-win').map((e) => e.parent.parent.innerHTML).join(' '), /Yesterday \(UTC\)/);
    f.clearTimers();
  });

  test(`${name}: the autopilot panel's "Today (UTC)" figure says "Yesterday (UTC)" once its status read is from before midnight UTC`, async () => {
    const store = new Map([['tau_ap_open', '1']]);
    const status = {autopilot: true, mode: 'paper', research: {active: [], tested_7d: 0, passed_7d: 0}, paper_running: 1, demo_running: 0,
      live_running: 0, today_paper_pnl: 2.5, today_live_pnl: 0, today_paper_slippage_estimate: 2.4, focus: [], idle: null, story: []};
    const f = frontend({storage: storageFor(store), fetchImpl: (path) => {
      if (path.startsWith('/api/chat?')) return response({messages: [], agent_busy: false});
      if (path === '/api/models') return response({models: [], default: ''});
      if (path === '/api/autopilot/status') return new Promise(() => {});  // the next status read never answers
      throw new Error('Unexpected offline request: ' + path);
    }});
    f.S.me = {...f.S.me, user: {...f.S.me.user, id: 7, autopilot: true}};
    f.S.ap = status; f.S.apAt = Date.now();
    const box = f.document.createElement('div'); box.id = 'autopilot-float'; f.document.body.appendChild(box);
    f.renderAutopilot(box, status); await settle();
    const label = () => box.querySelector('#apf-stats').querySelectorAll('.pnl-win').map((e) => e.textContent);
    assert.deepEqual(label(), ['Today (UTC):']);
    f.shiftClock(pastUtcMidnight());
    await back(f);
    assert.deepEqual(label(), ['Yesterday (UTC):']);
    assert.match(box.querySelector('#apf-stats').innerHTML, /\+\$2\.50/);
  });
}
