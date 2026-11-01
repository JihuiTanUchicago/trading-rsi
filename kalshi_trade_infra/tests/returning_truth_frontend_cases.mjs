// Shared cases, run against each bundle's offline harness (Tau and Meridian). Walked 2026-11-01 as a customer back
// after 3 days of a weather, sports and crypto autopilot (seeded rows only, paper and demo), both themes, 390 and 1366
// wide, four places said something untrue or contradictory:
// - under the dashboard's latest closed trades, "Every closed trade, with net P&L, is in the track record ↓": the track
//   record lists no trade (a settled NBA trade was on Funds and in the CSV alone);
// - "Furthest on paper" and a Deploy row's "toward live ... enough in about 58 days" featured a strategy whose one paper
//   judgment had failed, which can no longer be cleared to trade;
// - Home listed a failed autopilot session under "Needs you", where its page says the autopilot starts the next itself;
// - the research log's "Times are CST." stood over every day, the days before the clock change in CDT.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const plain = (html) => html.replace(/<[^>]+>/g, '').replace(/ /g, ' ').replace(/\s+/g, ' ').trim();

const closed = (id, mode = 'paper') => ({id, deployment_id: 1, ticker: `KXNBAGAME-26OCT30GSWMIA-${id}`, title: `Strategy ${id}`, side: 'yes', qty: 4,
  entry_price: 0.35, mode, unrealized: null, pnl: 1.25, market_close_time: '2026-10-30T23:30:00Z', opened_at: '2026-10-30T20:00:00Z',
  closed_at: '2026-10-31T02:00:00Z'});
const books = (paper, demo) => ({
  paper: {equity: 1004, cash: 1004, open_at_cost: 0, total_pnl: 4, closed_trades: paper, open_positions: 0, realized_pnl: paper ? 4 : 0, today_pnl: 0, running: 1, daily: []},
  live: {connected: false, balance: null, closed_trades: 0, open_positions: 0, open_at_cost: 0, realized_pnl: 0, today_pnl: 0, running: 0},
  demo: {equity: 251, capital: 250, realized_pnl: demo ? 1 : 0, closed_trades: demo, open_positions: 0, health: null},
});

export function returningTruthCases({name, frontend}) {
  const dashboard = async (extra) => {
    const dash = {...books(30, 0), deployments: [], open_positions: [], recent_closed: Array.from({length: 12}, (_, k) => closed(k + 1)), reconcile: [],
      freshness: {btc: 30, engine: 4}, agents: {research_running: 0, research_queued: 0}, v: 'c1', ...extra};
    const f = frontend({fetchImpl: (path) => path.startsWith('/api/dashboard') ? response(dash)
      : path.startsWith('/api/track-record') ? response({modes: {}}) : path === '/api/autopilot/status' ? response({autopilot: false, mode: 'off'}) : response({})});
    f.S.me.user.autopilot = false; f.S.ap = null; f.S.apAt = 0;
    f.location.pathname = '/';
    await f.viewDashboard(); await settle();
    const html = f.root.querySelector('#main').innerHTML + (f.root.querySelector('#boardbody') || {innerHTML: ''}).innerHTML;
    f.clearTimers();
    return html;
  };

  test(`${name}: under the latest closed trades it says each book's trades CSV holds every one, never the track record`, async () => {
    const paper = await dashboard({});
    assert.match(paper, /Every closed trade, with net P&amp;L, is in its book’s trades CSV \(Download trades, by the book’s figure above\); each paper one is also in your <a href="\/funds">Funds<\/a> paper history\./);
    assert.match(paper, /class="csv-link" href="\/api\/trades\.csv\?mode=paper"/);  // the CSV it points to is there
    // Demo trades alone: no paper history to point to.
    const demo = await dashboard({...books(0, 30), recent_closed: Array.from({length: 12}, (_, k) => closed(k + 1, 'demo'))});
    assert.match(demo, /Every closed trade, with net P&amp;L, is in its book’s trades CSV \(Download trades, by the book’s figure above\)\.<\/p>/);
    assert.doesNotMatch(demo, /href="\/funds">Funds<\/a> paper history/);
    for (const html of [paper, demo]) assert.doesNotMatch(html, /is in the track record|href="#track-record">Every closed/);
  });

  test(`${name}: a Deploy row whose strategy can no longer be cleared says so, never how far it is toward live`, async () => {
    const r = {trades: 4, trades_needed: 30, events: 4, events_needed: 20, span_hours: 103.5, span_needed_hours: 48, running: true, eta_days: 58.5, trades_per_day: 0.4, rate_from: 'paper'};
    const dep = (id, road) => ({id, strategy_id: id, title: 'Dep ' + id, market: 'nba_game', mode: 'paper', status: 'running', status_reason: null, realized_pnl: 0,
      closed_trades: 4, open_positions: 0, unrealized_pnl: null, created_at: '2026-10-24T08:00:00Z', curve: [], stake: 5, max_positions: 1, backtest: {}, road});
    const f = frontend({fetchImpl: (path) => path === '/api/deployments' ? response({deployments: [dep(1, {...r, cleared_never: 'paper'}),
      dep(2, {...r, cleared_never: 'holdout'}), dep(3, r)], open_positions: []}) : response({})});
    await f.viewDeploy(); await settle();
    const lines = f.root.querySelectorAll('.road-line').map((el) => plain(el.innerHTML));
    f.clearTimers && f.clearTimers();
    assert.deepEqual(lines, [
      'It can’t be cleared to trade: its one judgment on its own paper trades didn’t clear it, so its paper record doesn’t lead to live.',
      'It can’t be cleared to trade: its one-time final test didn’t clear it, so its paper record doesn’t lead to live.',
      'Its paper record toward live: 4/30 trades · 4/20 separate events · enough in about 59 days at its pace',
    ]);
  });

  test(`${name}: "Furthest on paper" says plainly when its strategy can no longer be cleared, on the panel and the card`, async () => {
    const f = frontend({fetchImpl: (path) => path.startsWith('/api/dashboard') ? response({...books(4, 0), deployments: [], open_positions: [], recent_closed: [],
      reconcile: [], freshness: {btc: 30, engine: 4}, agents: {research_running: 0, research_queued: 0}, v: 'c1'})
      : path === '/api/autopilot/status' ? response({autopilot: true, mode: 'paper'}) : path.startsWith('/api/chat') ? response({messages: []})
      : path === '/api/models' ? response({models: [], default: ''}) : response({})});
    f.S.me.user.autopilot = true;
    await f.viewDashboard(); await settle();
    const prog = {strategy_id: 8, title: 'NBA road dogs', trades: 4, trades_needed: 30, net_pnl: 8.63, strategies: 1};
    const card = (p) => { f.S.ap = {autopilot: true, mode: 'paper', research: {active: []}, paper_progress: p}; f.paintDashAp(); return plain(f.root.querySelector('#dash-ap .apcard-figs').innerHTML); };
    assert.match(card({...prog, cleared_never: 'paper'}), /Furthest on paper\s*4 of 30 realistic paper trades It can’t be cleared to trade: its one judgment on its own paper trades didn’t clear it\. instant-fill/);
    assert.doesNotMatch(card(prog), /can’t be cleared/);
    f.clearTimers();
    // The panel, open (its open state kept as the page keeps it).
    const store = new Map([['tau_ap_open', '1']]);
    const storage = {get: (k) => store.get(k), set: (k, v) => store.set(k, String(v)), has: (k) => store.has(k), delete: (k) => store.delete(k),
      getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k)};
    const g = frontend({storage, fetchImpl: (path) => path.startsWith('/api/chat?') ? response({messages: [], agent_busy: false})
      : path === '/api/models' ? response({models: [], default: ''}) : response({})});
    g.S.me = {...g.S.me, user: {...(g.S.me && g.S.me.user), id: 7, autopilot: true}};
    g.S.ap = {...g.S.ap, autopilot: true, mode: 'paper'};
    const box = g.document.createElement('div'); box.id = 'autopilot-float'; g.document.body.appendChild(box);
    g.renderAutopilot(box, null); await flush(); await flush();
    const panel = (p) => {
      g.apUpdate(box, {autopilot: true, mode: 'paper', research: {active: []}, paper_progress: p});
      const dd = box.querySelector('#apf-stats').querySelectorAll('dd').find((el) => /NBA road dogs/.test(el.textContent));
      return plain(dd.innerHTML);
    };
    assert.match(panel({...prog, cleared_never: 'paper'}), /4 of 30 realistic paper trades · \+\$8\.63 net on them\. It can’t be cleared to trade: its one judgment on its own paper trades didn’t clear it\. Instant-fill/);
    assert.doesNotMatch(panel(prog), /can’t be cleared/);
    g.clearTimers();
  });

  test(`${name}: a failed session the autopilot started isn't under Needs you; the Autopilot row says it needs nothing`, async () => {
    const strip = async (runs, apFailed) => {
      const since = {at: new Date(Date.now() - 3 * 86400e3).toISOString(), first: false, books: {paper: {pnl: 0, trades: 0}}, passed: {n: 0, items: []},
        autopilot: {}, runs, runs_ap_failed: apFailed, own_sessions: true, spent: null, milestones: {passed: [], live_ready: []}, names: {}, auto_paused: [], by_deployment: {}};
      const f = frontend({fetchImpl: (path) => path.startsWith('/api/dashboard') ? response({...books(0, 0), deployments: [], open_positions: [], recent_closed: [],
        reconcile: [], freshness: {btc: 30, engine: 4}, agents: {research_running: 0, research_queued: 0}, since, v: 'c1'})
        : path === '/api/autopilot/status' ? response({autopilot: true, mode: 'paper'}) : response({})});
      f.S.me.user.autopilot = true;
      await f.viewDashboard(); await settle();
      const html = f.root.querySelector('#dash-since').innerHTML;
      f.clearTimers();
      return html;
    };
    const ap = await strip({failed: 1, succeeded: 2}, 1);
    assert.match(ap, /<dt>Needs you<\/dt><dd>Nothing\.<\/dd>/);
    assert.match(ap, /<dt>Autopilot<\/dt><dd>.*A research session it started stopped on an error; it starts its next one on its own, so there’s nothing for you to do\.<\/dd>/);
    assert.match(ap, /3 research sessions started, 2 finished, 1 failed/);  // still counted under New results
    // One of the reader's own failed too: that one stays under Needs you.
    const both = await strip({failed: 3}, 2);
    assert.match(both, /<dt>Needs you<\/dt><dd><a href="\/research">1 research session failed<\/a><\/dd>/);
    assert.match(both, /2 research sessions it started stopped on an error/);
    // None of the autopilot's (an older server sends no count): as before.
    assert.match(await strip({failed: 1}, undefined), /<dt>Needs you<\/dt><dd><a href="\/research">1 research session failed<\/a>/);
  });

  test(`${name}: the research log names the zone in force on each day across a clock change, never one zone for all`, async () => {
    const was = process.env.TZ;
    process.env.TZ = 'America/Chicago';
    try {
      const page = (times) => {
        const events = times.map((t, i) => ({id: i + 1, kind: 'step', message: `Step ${i + 1}`, data: {}, created_at: t}));
        return frontend({fetchImpl: (path) => (path.startsWith('/api/runs/9')
          ? response({run: {id: 9, prompt: 'Idea', market: 'nba_game', created_at: times[0], started_at: times[0], finished_at: times.at(-1), status: 'succeeded'},
            events: path.includes('after=0') ? events : [], strategies: []})
          : response({}))});
      };
      const read = async (times) => {
        const f = page(times);
        await f.viewRun(9); await settle(); f.clearTimers();
        const days = f.root.querySelector('#log').children.flatMap((li) => (li.children || []).filter((c) => !c.removed && c.classList && c.classList.contains('log-day')))
          .map((d) => plain(d.innerHTML));
        return {days, tz: f.root.querySelector('.log-tz').textContent};
      };
      // Oct 30 (CDT) and Nov 3 (CST), 2026: each day line says its own zone, and the line over the log no single one.
      const across = await read(['2026-10-30T15:00:00Z', '2026-11-03T15:00:00Z']);
      assert.deepEqual(across.days.map((d) => d.replace(/^.* · /, '')), ['times in CDT', 'times in CST']);
      assert.equal(across.tz, 'Each day says its time zone.');
      // Days all in one zone, before the change: that zone, not today's, over the log; the day lines say no zone.
      const before = await read(['2026-10-28T15:00:00Z', '2026-10-30T15:00:00Z']);
      assert.equal(before.tz, 'Times are CDT.');
      assert.ok(before.days.length === 2 && before.days.every((d) => !/times in/.test(d)), before.days.join(' | '));
    } finally {
      if (was === undefined) delete process.env.TZ; else process.env.TZ = was;
    }
  });
}
