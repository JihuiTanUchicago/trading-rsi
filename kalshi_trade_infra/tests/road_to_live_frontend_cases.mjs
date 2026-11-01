// Shared cases, run against each bundle's offline harness (Tau and Meridian). Walked 2026-11-01 along the road from a
// passed strategy to live (paper, the paper forward test, a demo copy, Road to live, live autopilot), both themes, 390
// and 1366 wide, with live trading closed. Where pages disagreed or said less than the truth:
// - a probation strategy in its paper forward test read "toward live: 22/30 trades · enough in about 2 days" on Deploy
//   and "Next rung, demo: nothing needed" on its page, while its one paper judgment (at its experiment's minimum sample,
//   40 here) comes first and only a pass can clear it to trade; its page never said it was being tested on paper;
// - a paper pass the diversification check held back read "its one judgment ... didn't clear it" on Deploy, beside its
//   page's "passed its one judgment there ... Held back";
// - one that can never be cleared to trade read "Next rung, demo: ... before live money" on its page.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const plain = (html) => html.replace(/<[^>]+>/g, '').replace(/ /g, ' ').replace(/\s+/g, ' ').trim();

export function roadToLiveCases({name, frontend, detailStrategy, strategyFrontend}) {
  test(`${name}: a Deploy row in its paper forward test counts toward its one judgment; a held-back pass says it passed`, async () => {
    const r = {trades: 22, trades_needed: 30, events: 18, events_needed: 20, span_hours: 127, span_needed_hours: 48, running: true, eta_days: 2.2,
      trades_per_day: 3.7, rate_from: 'paper'};
    const dep = (id, road) => ({id, strategy_id: id, title: 'Dep ' + id, market: 'nba_game', mode: 'paper', status: 'running', status_reason: null, realized_pnl: 0,
      closed_trades: 22, open_positions: 0, unrealized_pnl: null, created_at: '2026-10-24T08:00:00Z', curve: [], stake: 5, max_positions: 1, backtest: {}, road});
    const f = frontend({fetchImpl: (path) => path === '/api/deployments' ? response({deployments: [
      dep(1, {...r, forward_sample: 40, forward_events_needed: 20, forward_eta_days: 4.6}),
      dep(2, {...r, trades: 40, events: 24, forward_sample: 40, forward_events_needed: 20, forward_eta_days: 0}),
      dep(3, {...r, forward_sample: 40, forward_events_needed: 20, forward_eta_days: null, running: false}),
      dep(4, {...r, cleared_never: 'paper_held'}), dep(5, {...r, cleared_never: 'holdout_held'}), dep(6, r)], open_positions: []}) : response({})});
    await f.viewDeploy(); await settle();
    const lines = f.root.querySelectorAll('.road-line').map((el) => plain(el.innerHTML));
    f.clearTimers && f.clearTimers();
    assert.deepEqual(lines, [
      'In its paper forward test: 22/40 trades · 18/20 separate events · enough in about 5 days at its pace, then one judgment on them decides whether it can be cleared to trade.',
      'In its paper forward test: its 40 trades from 20+ separate events are in, so its one judgment on them is due. Only a pass clears it to trade.',
      'In its paper forward test: 22/40 trades · 18/20 separate events · paused, so not growing, then one judgment on them decides whether it can be cleared to trade.',
      'It can’t be cleared to trade: it passed its one judgment on its own paper trades but was held back, so its paper record doesn’t lead to live.',
      'It can’t be cleared to trade: it passed its one-time final test but was held back, so its paper record doesn’t lead to live.',
      'Its paper record toward live: 22/30 trades · 18/20 separate events · enough in about 2 days at its pace',
    ]);
    for (const line of lines.slice(0, 3)) assert.doesNotMatch(line, /toward live|\/30/);
  });

  test(`${name}: "Furthest on paper" in its paper forward test counts toward that judgment, not toward live`, async () => {
    const f = frontend({fetchImpl: (path) => path.startsWith('/api/dashboard') ? response({paper: {equity: 1004, cash: 1004, open_at_cost: 0, total_pnl: 4, closed_trades: 22,
      open_positions: 0, realized_pnl: 4, today_pnl: 0, running: 1, daily: []}, live: {connected: false, balance: null, closed_trades: 0, open_positions: 0, open_at_cost: 0,
      realized_pnl: 0, today_pnl: 0, running: 0}, demo: {equity: 250, capital: 250, realized_pnl: 0, closed_trades: 0, open_positions: 0, health: null},
      deployments: [], open_positions: [], recent_closed: [], reconcile: [], freshness: {btc: 30, engine: 4}, agents: {research_running: 0, research_queued: 0}, v: 'c1'})
      : path === '/api/autopilot/status' ? response({autopilot: true, mode: 'paper'}) : path.startsWith('/api/chat') ? response({messages: []})
      : path === '/api/models' ? response({models: [], default: ''}) : response({})});
    f.S.me.user.autopilot = true;
    await f.viewDashboard(); await settle();
    const prog = {strategy_id: 8, title: 'OKC probe up', trades: 22, trades_needed: 40, net_pnl: 48.92, strategies: 2, forward_sample: 40};
    const card = (p) => { f.S.ap = {autopilot: true, mode: 'paper', research: {active: []}, paper_progress: p}; f.paintDashAp(); return plain(f.root.querySelector('#dash-ap .apcard-figs').innerHTML); };
    assert.match(card(prog), /Furthest on paper\s*22 of the 40 realistic paper trades its paper forward test judges/);
    assert.match(card({...prog, trades: 41}), /41 realistic paper trades \(its paper forward test judges 40\)/);
    assert.match(card({...prog, trades_needed: 30, forward_sample: undefined}), /22 of 30 realistic paper trades/);
    f.clearTimers();
  });

  const d = (id, mode, status = 'running') => ({id, mode, status, closed_trades: 22, realized_pnl: 3, open_positions: 0, stake: 5, created_at: '2026-10-20T08:00:00Z'});
  const readiness = (trades, events) => ({ready: false, blocking: 'the gatekeeper hasn’t cleared it as a deployment candidate', deployment_candidate: false,
    live_autopilot: {ok: false, why: 'live autopilot is off'}, pace: {running: true, eta_days: 2.2, trades_per_day: 3.7, rate_from: 'paper'},
    paper: {ready: false, why: 'it needs 30+ closed paper trades on realistic fills first (has 22)', trades, trades_needed: 30, net_pnl: 48.9, events, events_needed: 20,
      span_hours: 127, span_needed_hours: 48, p_value: null, p_value_needed: 0.1, recent_trades: 11, recent_net_pnl: 20, realization: null,
      demo_orders: 0, demo_orders_needed: 20, demo_unresolved: 0, demo_mismatched_tickers: [],
      checks: {legacy_trades: true, legacy_pnl: true, realistic_trades: trades >= 30, events: events >= 20, net_pnl: true, span: true, edge: false, recent: true, demo: false}}});
  const missed = [{gate: 'min_sample', label: 'Too few trades to judge (26 of 40)', measured: '26', needed: '40', fix: ''}];
  const page = async (overrides, road) => {
    const reads = [];
    const f = strategyFrontend(detailStrategy({status: 'failed', why_failed: missed, ...overrides}), (path) => {
      reads.push(path);
      if (path === '/api/strategies/42/live-readiness') return road ? response(road) : response({error: 'down'}, 503);
      return response({});
    });
    await f.viewStrategy(42); await settle();
    const ladder = f.root.querySelector('#ladder-body'), glance = f.root.querySelector('.verdict-glance');
    const out = {ladder: ladder ? plain(ladder.innerHTML) : '', glance: glance ? plain(glance.innerHTML) : '', reads};
    f.clearTimers && f.clearTimers();
    return out;
  };
  const probation = {min_sample: 40, classification: 'inconclusive', validation: {classification: 'inconclusive', n_trades: 26}, holdout: null, paper: null, promoted: false};

  test(`${name}: a strategy in its paper forward test says its one judgment is next, with the gate's own counts`, async () => {
    const p = await page({experiment: probation, deployments: [d(1, 'paper')]}, readiness(22, 18));
    assert.match(p.ladder, /Next rung: its one judgment on its own paper trades, at 40 realistic paper trades from 20\+ separate events: 18 more trades \(22 of 40\) and 2 more separate events \(18 of 20\)\. Only a pass there can clear it to trade, so live can’t come before it\./);
    assert.doesNotMatch(p.ladder, /Next rung, demo|nothing needed|before live money/);
    assert.match(p.glance, /It’s on paper now as a forward test: one judgment on its own paper trades, made once its record holds 40 of them from 20\+ separate events, can still clear it to trade\./);
    assert.ok(p.reads.includes('/api/strategies/42/live-readiness'), 'its counts come from the same read as Road to live');
    const one = await page({experiment: probation, deployments: [d(1, 'paper'), d(2, 'demo')]}, readiness(39, 19));
    assert.match(one.ladder, /1 more trade \(39 of 40\) and 1 more separate event \(19 of 20\)/);
    assert.doesNotMatch(one.ladder, /Next rung, live/);
    const due = await page({experiment: probation, deployments: [d(1, 'paper')]}, readiness(40, 24));
    assert.match(due.ladder, /at 40 realistic paper trades from 20\+ separate events\. Both are in, so that judgment is due\./);
    // Before (or without) the read, the step still reads, without counts.
    const blind = await page({experiment: probation, deployments: [d(1, 'paper')]}, null);
    assert.match(blind.ladder, /Next rung: its one judgment on its own paper trades, at 40 realistic paper trades from 20\+ separate events\. Only a pass/);
    // Not on paper: no forward test is running, and the glance says nothing of one.
    const off = await page({experiment: probation, deployments: []}, null);
    assert.doesNotMatch(off.glance, /forward test/);
    assert.ok(!off.reads.includes('/api/strategies/42/live-readiness'));
  });

  test(`${name}: one that can never be cleared to trade has no next rung toward live money, and a held-back pass says it passed`, async () => {
    const held = await page({verdict: 'held_back', why_failed: [{gate: 'diversification', label: 'Too similar to a strategy you already run', measured: 'a 0.81 correlation', needed: '0.70 or less', fix: ''}],
      experiment: {...probation, classification: 'forward_validated', paper: {classification: 'forward_validated', n_trades: 40}}, deployments: [d(1, 'paper')]}, readiness(44, 26));
    assert.match(held.ladder, /It can’t be cleared to trade: it passed its one judgment on its own paper trades but was held back, so it has no next rung toward live money\./);
    assert.doesNotMatch(held.ladder, /Next rung, demo|before live money|didn’t clear/);
    const failed = await page({experiment: {...probation, paper: {classification: 'inconclusive', n_trades: 40, verdict_at: '2026-10-31T22:30:00Z'}}, deployments: [d(1, 'paper'), d(2, 'demo')]}, null);
    assert.match(failed.ladder, /It can’t be cleared to trade: its one judgment on its own paper trades didn’t clear it, so it has no next rung toward live money\./);
    assert.doesNotMatch(failed.ladder, /Next rung, live/);
    assert.doesNotMatch(failed.glance, /forward test: one judgment/);
    // A deployment candidate's ladder is as before.
    const cand = await page({status: 'passed', why_failed: [], experiment: {...probation, classification: 'deployment_candidate', promoted: true,
      holdout: {classification: 'forward_validated', n_trades: 44}}, deployments: [d(1, 'paper')]}, null);
    assert.match(cand.ladder, /Next rung, demo: nothing needed/);
  });
}
