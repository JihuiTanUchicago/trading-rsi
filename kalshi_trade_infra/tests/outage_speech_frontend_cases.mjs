// Shared cases, run against each bundle's offline harness (Tau and Meridian): while reads fail, a screen reader hears
// the outage once and the recovery once. Walked 2026-11-01 at 390x844 and 1366x900 with every /api read aborted,
// answered 500 with a traceback, 502/503 with an edge page, or hung:
// - the "Last updated 45s ago, retrying" lines are role="status" and their age counts up on every tick, so the line was
//   spoken again on each failed poll: 39 times in 80s on a running session, 17 on the Research list, each line on Home
//   in turn; and nothing was said when the reads came back;
// - a strategy page's money, read once a minute, stood with no age through up to three minutes of failed reads.
// Reads only: nothing here sends, retries or times out a write.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const OUTAGE = 'Can’t reach Tau right now; retrying. The figures on screen are from its last good read.';
const BACK = 'Tau is back. The figures on screen are up to date.';
const run = (spent) => ({run: {id: 9, prompt: 'Idea', status: 'running', market: 'btc15m', created_at: '2026-10-17T10:00:00Z',
  started_at: '2026-10-17T10:00:00Z', spent_usd: spent, hypotheses_tried: 2}, events: []});

// Runs every timer a step left (a retry's pause, a read's abort, announce()'s 50ms), and keeps what the polite live
// region was given to say.
const speech = () => {
  const said = [];
  const drain = async (f) => {
    for (let i = 0; i < 10; i++) {
      await flush(); await flush();
      const due = [...f.timeouts];
      if (!due.length) break;
      for (const [id, cb] of due) {
        f.timeouts.delete(id); cb();
        if (String(cb).includes('textContent = msg')) said.push(f.document.getElementById('sr-status').textContent);
      }
    }
    await flush();
  };
  return {said, step: async (f, tick, ms) => { f.shiftClock(ms); tick(); await drain(f); }};
};

export function outageSpeechCases({name, frontend, detailStrategy, dashboard}) {
  test(`${name}: a running session whose reads fail says so once, keeps its figures and their age on screen in silence, and says once that it is back`, async () => {
    let down = false, spent = 1.25;
    const f = frontend({fetchImpl: () => (down ? Promise.reject(new TypeError('Failed to fetch')) : response(run(spent)))});
    await f.viewRun(9);
    const tick = f.intervals.get(f.S.timers.at(-1));
    const note = () => f.root.querySelector('#run-stale');
    const {said, step} = speech();
    down = true;
    for (let i = 0; i < 20; i++) await step(f, tick, 2000);  // 40s of failed 2s polls
    assert.match(note().textContent, /^Last updated \d+s ago, retrying$/);
    assert.equal(note().getAttribute('aria-live'), 'off', 'the counting line is not read out on each tick');
    assert.equal(note().getAttribute('role'), 'status', 'still a status line to read');
    assert.match(f.root.querySelector('#runfigs').innerHTML, /\$1\.25/);
    assert.deepEqual(said, [OUTAGE], 'the outage, once');
    down = false; spent = 1.5;
    for (let i = 0; i < 40 && note().textContent; i++) await step(f, tick, 2000);
    assert.equal(note().textContent, '');
    assert.match(f.root.querySelector('#runfigs').innerHTML, /\$1\.50/);
    for (let i = 0; i < 5; i++) await step(f, tick, 2000);
    assert.deepEqual(said, [OUTAGE, BACK], 'and the recovery, once');
    f.clearTimers();
  });

  test(`${name}: Home with two stale lines says the outage once, and that it is back only once both have cleared`, async () => {
    let downDash = false, downAp = false;
    const status = {autopilot: true, mode: 'paper', story: ['Researching BTC 15m.'], research: {tested_7d: 3, passed_7d: 0, active: []},
      live_running: 0, demo_running: 0, paper_running: 1, today_paper_pnl: 4.5};
    const f = frontend({fetchImpl: (path) => {
      if (path === '/api/dashboard') return downDash ? response({error: 'Tau is restarting.'}, 503) : response(dashboard());
      if (path === '/api/autopilot/status') return downAp ? response({error: 'Tau is restarting.'}, 503) : response(status);
      return response({});
    }});
    f.S.me.user.autopilot = true; f.S.ap = status; f.S.apAt = Date.now();
    await f.viewDashboard(); await settle();
    const dashTick = f.intervals.get(f.S.timers[0]);
    const apTick = [...f.intervals.values()].find((cb) => String(cb).includes("getElementById('autopilot-float') && !document.hidden && !apFresh()) tick()"));
    const line = (id) => f.root.querySelector('#' + id).textContent;
    const {said, step} = speech();
    downDash = downAp = true;
    for (let i = 0; i < 6; i++) { await step(f, dashTick, 10000); await step(f, apTick, 0); }
    assert.match(line('dash-stale'), /retrying$/);
    assert.match(line('dash-ap-stale'), /retrying$/);
    assert.deepEqual(said, [OUTAGE], 'one outage, however many lines say it');
    downDash = false;
    for (let i = 0; i < 20 && line('dash-stale'); i++) await step(f, dashTick, 15000);
    assert.equal(line('dash-stale'), '');
    assert.match(line('dash-ap-stale'), /retrying$/);
    assert.deepEqual(said, [OUTAGE], 'not back while a line still says it is retrying');
    downAp = false;
    for (let i = 0; i < 20 && line('dash-ap-stale'); i++) await step(f, apTick, 20000);
    assert.equal(line('dash-ap-stale'), '');
    assert.deepEqual(said, [OUTAGE, BACK]);
    f.clearTimers();
  });

  test(`${name}: a strategy page's money says its age after its first failed minute's read, not two minutes later`, async () => {
    let down = false;
    const detail = detailStrategy({deployments: [{id: 501, mode: 'paper', status: 'running', closed_trades: 3, realized_pnl: 1.25, open_positions: 0,
      stake: 5, created_at: '2026-10-20T10:00:00Z'}], book_totals: {paper: {pnl: 1.25, trades: 3, open: 0}}});
    const f = frontend({fetchImpl: (path) => {
      if (path === '/api/strategies/42') return down ? response({error: 'Tau is restarting.'}, 503) : response(detail);
      return response({error: 'Bad request.'}, 400);
    }});
    f.location.pathname = '/strategies/42';
    await f.viewStrategy(42); await settle();
    const tick = [...f.intervals.values()].find((c) => String(c).includes('!document.hidden && gen === S.gen) tick()'));
    const {said, step} = speech();
    down = true;
    await step(f, tick, 65e3);
    assert.equal(f.root.querySelector('#strat-stale').textContent, 'Last updated 65s ago, retrying');
    assert.match(f.root.querySelector('#since-deploying').textContent, /1\.25/);
    assert.deepEqual(said, [OUTAGE]);
    f.clearTimers();
  });
}
