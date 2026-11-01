// Shared cases, run against each bundle's offline harness (Tau and Meridian): every "today" on Home and the autopilot
// panel is the reader's own calendar day once the server counts it so, and says the UTC day plainly when it doesn't.
// From the sports walk of 2026-11-01: Home showed "Today (UTC)" realized P&L to a reader in New York, whose day it
// rolled over at 7-8pm local, the figure dropping to $0.00, while game times and "today/tomorrow" on the market page
// went by the reader's calendar. The server now counts each book's day from the reader's midnight (the zone boot.js
// keeps in its cookies, app/services/reader_day.py) and sends day_start; the page names the day by it. Each case runs
// in New York, Kolkata and Auckland (process TZ), where 00:00 UTC is never the reader's midnight.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const ticks = async (f) => { for (const tick of [...f.intervals.values()]) { await tick(); await settle(); } };
const ZONES = ['America/New_York', 'Asia/Kolkata', 'Pacific/Auckland'];
const inZone = async (zone, fn) => {
  const was = process.env.TZ;
  process.env.TZ = zone;
  try { return await fn(); } finally { if (was === undefined) delete process.env.TZ; else process.env.TZ = was; }
};
// The reader's last midnight, and 00:00 UTC today, as the server writes a naive UTC instant ("...+00:00").
const readerMidnight = (t = Date.now()) => { const d = new Date(t); d.setHours(0, 0, 0, 0); return d.getTime(); };
const utcMidnight = (t = Date.now()) => Math.floor(t / 864e5) * 864e5;
const wire = (t) => new Date(t).toISOString().replace('.000Z', '+00:00');
const pastLocalMidnight = () => { const n = new Date(); return new Date(n.getFullYear(), n.getMonth(), n.getDate() + 1, 0, 5).getTime() - n.getTime(); };
const trade = (id, mode, pnl, at) => ({id, deployment_id: 1, mode, side: 'yes', qty: 2, entry_price: 0.4, pnl, title: 'Late-game fade',
  ticker: 'KXNFLGAME-26NOV01-T' + id, closed_at: new Date(at).toISOString()});

export function readerDayCases({name, frontend, dashboard, storageFor, paperChart = false}) {
  const home = (answer) => frontend({fetchImpl: (path) => {
    if (path.startsWith('/api/dashboard')) return typeof answer === 'function' ? answer() : response(answer);
    if (path === '/api/autopilot/status') return response({autopilot: false, mode: 'off'});
    if (path.startsWith('/api/track-record')) return response({trades: [], curve: []});
    return response({messages: [], models: []});
  }});
  const books = (start, {paperToday = 1.25, demoToday = 0.75, recent} = {}) => {
    const base = dashboard();
    const at = Math.max(start + 60e3, Date.now() - 60e3);
    return {...base, day_start: wire(start),
      recent_closed: recent || [trade(1, 'paper', 1.25, at), trade(2, 'demo', 0.75, at)],
      paper: {...base.paper, closed_trades: 3, today_pnl: paperToday, realized_pnl: 12.5},
      demo: {...base.demo, equity: 1000.75, realized_pnl: 0.75, closed_trades: 1, today_pnl: demoToday, connected: true}};
  };
  const labels = (f) => f.root.querySelectorAll('.pnl-win').map((e) => e.textContent).filter((t) => /^(Today|Yesterday|Since|[A-Z][a-z]{2} \d)/.test(t) && !/^Since (you|your)/.test(t));

  for (const zone of ZONES) {
    test(`${name} in ${zone}: Home's paper and demo day figures say "Today", the reader's own day, never "(UTC)"`, () => inZone(zone, async () => {
      let fail = false;
      const f = home(() => { if (fail) throw new Error('offline'); return response(books(readerMidnight())); });
      await f.viewDashboard(); await settle();
      const before = labels(f);
      assert.ok(before.length >= 2 && before.every((t) => t === 'Today:'), JSON.stringify(before));
      assert.doesNotMatch(f.root.innerHTML, /\(UTC\)/);
      assert.match(f.root.querySelectorAll('.pnl-win').map((e) => e.parent.parent.innerHTML).join(' '), /\+\$1\.25/);
      // Past the reader's own midnight (not midnight UTC), with the read failing, the figure is the day before's.
      fail = true;
      f.shiftClock(pastLocalMidnight());
      await ticks(f);
      const after = labels(f);
      assert.equal(after.length, before.length, JSON.stringify(after));
      assert.ok(after.every((t) => t === 'Yesterday:'), JSON.stringify(after));
      f.clearTimers();
    }));

    test(`${name} in ${zone}: a day counted from 00:00 UTC (the server didn't know the reader's zone yet) says "(UTC)"`, () => inZone(zone, async () => {
      const f = home(books(utcMidnight()));
      await f.viewDashboard(); await settle();
      const said = labels(f);
      assert.ok(said.length >= 2 && said.every((t) => t === 'Today (UTC):'), JSON.stringify(said));
      f.clearTimers();
    }));

    test(`${name} in ${zone}: a paper trade closed before the reader's midnight isn't today's: "no trades closed yet", not $0.00`, () => inZone(zone, async () => {
      const start = readerMidnight();
      const f = home(books(start, {paperToday: 0, demoToday: 0, recent: [trade(1, 'paper', 1.25, start - 60e3)]}));
      await f.viewDashboard(); await settle();
      const paper = f.root.querySelectorAll('.pnl-win').filter((e) => e.textContent === 'Today:').map((e) => e.parent.parent.textContent);
      assert.ok(paper.length >= 1, JSON.stringify(labels(f)));
      assert.ok(paper.some((t) => /no trades closed yet|None yet/.test(t)), JSON.stringify(paper));
      assert.ok(paper.every((t) => !/\$0\.00/.test(t)), JSON.stringify(paper));
      f.clearTimers();
    }));

    test(`${name} in ${zone}: the autopilot panel's paper and live day say the reader's day, without "(UTC)"`, () => inZone(zone, async () => {
      const store = new Map([['tau_ap_open', '1']]);
      const f = frontend({storage: storageFor(store), fetchImpl: (path) => {
        if (path.startsWith('/api/chat?')) return response({messages: [], agent_busy: false});
        if (path === '/api/models') return response({models: [], default: ''});
        if (path === '/api/autopilot/status') return new Promise(() => {});
        throw new Error('Unexpected offline request: ' + path);
      }});
      f.S.me = {...f.S.me, user: {...f.S.me.user, id: 7, autopilot: true}};
      const paper = {autopilot: true, mode: 'paper', research: {active: [], tested_7d: 0, passed_7d: 0}, paper_running: 1, demo_running: 0,
        live_running: 0, today_paper_pnl: 2.5, today_live_pnl: 0, utc_day_live_pnl: 0, today_paper_slippage_estimate: 2.4, focus: [], idle: null,
        story: [], day_start: wire(readerMidnight())};
      f.S.ap = paper; f.S.apAt = Date.now();
      const box = f.document.createElement('div'); box.id = 'autopilot-float'; f.document.body.appendChild(box);
      f.renderAutopilot(box, paper); await settle();
      const dts = () => box.querySelector('#apf-stats').querySelectorAll('dt').map((e) => e.textContent);
      assert.ok(dts().some((t) => t.startsWith('Today: paper realized')), JSON.stringify(dts()));
      // A live trade lost today: the live book's day, the reader's, while the loss budget keeps 00:00 UTC.
      const live = {...paper, today_live_pnl: -3.07, utc_day_live_pnl: -1.5};
      f.S.ap = live;
      f.renderAutopilot(box, live); await settle();
      assert.ok(dts().includes('Live realized today'), JSON.stringify(dts()));
      assert.doesNotMatch(box.querySelector('#apf-stats').innerHTML, /\(UTC\)/);
      assert.match(box.querySelector('#apf-stats').innerHTML, /−\$3\.07|-\$3\.07/);
    }));

    if (paperChart) {
      test(`${name} in ${zone}: Home's closed paper P&L names its days as the reader's, or as UTC days when they are`, () => inZone(zone, async () => {
        const daily = [{d: '2026-10-30', pnl: 1.5}, {d: '2026-10-31', pnl: -0.25}];
        for (const [start, utc] of [[readerMidnight(), false], [utcMidnight(), true]]) {
          const answer = books(start);
          const f = home({...answer, paper: {...answer.paper, daily}});
          await f.viewDashboard(); await settle();
          const note = f.root.querySelector('.overview-performance .sect-note').textContent;
          assert.equal(/UTC days/.test(note), utc, note);
          f.clearTimers();
        }
      }));
    }
  }
}
