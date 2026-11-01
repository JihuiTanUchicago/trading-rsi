// Shared cases, run against each bundle's offline harness (Tau and Meridian): an autopilot whose focus spans
// several market families (walked 2026-11-01 on a phone and a desktop: the owner's focus widened from crypto to
// crypto, daily high temperatures and sports, with a research cap per 24 hours).
// - The "Focus:" line names the families as one list in words ("Crypto, daily high temperatures and sports"),
//   never "Crypto, Daily high temperatures, Sports" or a raw key, and its Change button says what it changes.
// - The credits line under the week says the research cap and the last 24 hours' spend in dollars and that at the
//   cap no new research starts; it never says "no daily spending cap" while the server sends one.
// - The Research page's "Is research getting better?" card shows each kind of market on its own line (spend,
//   sessions, ideas, gate reach, passes and dollars per idea that reached the gate), never only the one total.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const FOCUS = ['crypto', 'weather-high', 'sports'];

export function multiFamilyCases({name, frontend, dashboard, apStatus}) {
  const week = {research_credits: 268, tested: 62, passed: 3, paper_started: 0, paper_pnl: 0, paper_trades: 0,
    checkin_credits: 0.9, credits_left: 1276.1, runway_days: 33.2, reload_days: 33.1, pace_days: 7, card_charged: true};
  const apPage = async (status, me = {}) => {
    const f = frontend({fetchImpl: (path) => {
      if (path === '/api/dashboard') return response(dashboard());
      if (path === '/api/autopilot/status') return response(status);
      if (path === '/api/models') return response({models: [], default: ''});
      if (path.startsWith('/api/chat')) return response({messages: []});
      throw new Error('Unexpected offline request: ' + path);
    }});
    f.S.me = {...f.S.me, billing_enabled: true, credit_balance: 1276.1, min_run_balance: 1, auto_reload: {amount: 20, charge: 21.1, threshold: 5}, ...me};
    f.S.me.user.autopilot = true; f.S.ap = status;
    await f.viewDashboard(); await flush();
    f.S.ap = status; f.paintDashAp();
    return f;
  };

  test(`${name}: a focus across crypto, daily highs and sports reads as one list in words, and its Change says what it changes`, async () => {
    const f = await apPage({...apStatus, focus: FOCUS, week});
    const line = f.root.querySelector('#dash-ap .ap-focus');
    assert.equal(line.textContent.replace(/\s*Change$/, ''), 'Focus: Crypto, daily high temperatures and sports');
    assert.doesNotMatch(line.innerHTML, /weather-high|Daily high temperatures, Sports|, Sports/);
    assert.match(line.innerHTML, /<button type="button" data-apcard="focus" aria-label="Change focus">Change<\/button>$/);
    f.S.ap = {...apStatus, focus: ['btc', 'eth'], week}; f.paintDashAp();
    assert.equal(f.root.querySelector('#dash-ap .ap-focus').textContent.replace(/\s*Change$/, ''), 'Focus: Bitcoin and Ethereum');
    f.S.ap = {...apStatus, focus: ['weather-high'], week}; f.paintDashAp();
    assert.equal(f.root.querySelector('#dash-ap .ap-focus').textContent.replace(/\s*Change$/, ''), 'Focus: Daily high temperatures');
    f.clearTimers();
  });

  test(`${name}: the credits line says the research cap per 24 hours and today's spend in dollars, and when the cap stops new research`, async () => {
    let f = await apPage({...apStatus, focus: FOCUS, week, research_cap: {usd: 240, spent_24h: 217, reached: false}});
    let line = f.root.querySelector('#dash-ap .ap-week-left').innerHTML;
    assert.match(line, /Its research is capped at \$240\.00 per 24 hours, one cap for crypto, daily high temperatures and sports together: research sessions started in the last 24 hours, your own included, have cost <b>\$217\.00<\/b>\. At the cap it starts no new research until enough of them are over 24 hours old; research you start yourself isn’t limited\. Within the cap, each time your balance falls to \$5\.00, auto-reload charges your card and research carries on\./);
    assert.doesNotMatch(line, /no daily spending cap|P&amp;L|paper|spent in the last 24 hours/);
    f.clearTimers();
    f = await apPage({...apStatus, focus: FOCUS, week, research_cap: {usd: 240, spent_24h: 249.55, reached: true}}, {auto_reload: null});
    line = f.root.querySelector('#dash-ap .ap-week-left').innerHTML;
    assert.match(line, /Its research reached its cap of \$240\.00 per 24 hours, one cap for crypto, daily high temperatures and sports together: research sessions started in the last 24 hours, your own included, have cost <b>\$249\.55<\/b>, so it starts no new research until enough of them are over 24 hours old; research you start yourself isn’t limited\. Within the cap, it uses credits until your balance is under \$1\.00, then pauses\./);
    assert.doesNotMatch(line, /no daily spending cap/);
    f.clearTimers();
    // One cap for the whole focus: with three kinds of market "$240.00 per 24 hours" could read as $240 for each, three
    // times the money (walk 2026-11-01). A focus of one kind, or none, reads as before; the focus itself is never changed.
    f = await apPage({...apStatus, focus: ['weather-high'], week, research_cap: {usd: 240, spent_24h: 217, reached: false}});
    line = f.root.querySelector('#dash-ap .ap-week-left').innerHTML;
    assert.match(line, /Its research is capped at \$240\.00 per 24 hours: research sessions started/);
    assert.doesNotMatch(line, /one cap for/);
    f.clearTimers();
    f = await apPage({...apStatus, focus: [], week, research_cap: {usd: 240, spent_24h: 217, reached: false}});
    assert.doesNotMatch(f.root.querySelector('#dash-ap .ap-week-left').innerHTML, /one cap for/);
    f.clearTimers();
    f = await apPage({...apStatus, focus: ['btc', 'eth'], week, research_cap: {usd: 240, spent_24h: 3, reached: false}});
    assert.match(f.root.querySelector('#dash-ap .ap-week-left').innerHTML, /per 24 hours, one cap for Bitcoin and Ethereum together: research/);
    f.clearTimers();
    // No cap from the server (none set, or an older server): the line as before.
    f = await apPage({...apStatus, focus: FOCUS, week, research_cap: null});
    assert.match(f.root.querySelector('#dash-ap .ap-week-left').innerHTML, /It has no daily spending cap: each time your balance falls to \$5\.00/);
    f.clearTimers();
  });

  const fam = (runs, cost_usd, explored, gated, passed) => ({runs, cost_usd, explored, gated, passed});
  const board = (families) => ({total: {runs: 10, explored: 54, gated: 9, gate_reach_rate: 0.167, passed: 3, holdout_passed: 0, cost_usd: 244.5, pipeline_death_share: 0},
    families, days: [], trend: null, playbooks: [], scout: [], fix_list: [], data_requests: [], paper: null, tournament: null});
  const researchPage = async (b) => {
    const f = frontend({fetchImpl: (path) => {
      if (path.startsWith('/api/runs')) return response({runs: [], counts: {research_running: 0, research_queued: 0, research_paused: 0}, policy: null, totals: {tried: 54, passed: 3}, v: 'v1'});
      if (path.startsWith('/api/research/scoreboard')) return response(b);
      if (path === '/api/models') return response({models: [], default: ''});
      if (path === '/api/research/context') return response({markets: []});
      if (path === '/api/autopilot/status') return response({autopilot: false, mode: 'off'});
      return response({});
    }});
    f.S.me.user.autopilot = false;
    f.location.pathname = '/research';
    await f.viewResearch(); await settle();
    return f;
  };

  test(`${name}: the research card says where research spend went and what each kind of market produced, each on its own line`, async () => {
    const f = await researchPage(board({crypto: fam(4, 107.5, 23, 4, 1), sports: fam(3, 76, 17, 0, 0), 'weather-high': fam(3, 61, 14, 3, 1)}));
    const card = f.root.querySelector('#rsimo').innerHTML;
    const at = card.indexOf('By kind of market'), sect = card.slice(at, card.indexOf('</ul>', at));
    assert.ok(at > 0, 'the section is on the card');
    assert.match(card, /<h3>By kind of market<\/h3>/);
    const rows = [...sect.matchAll(/<li><span class="loop-k">([^<]*)<\/span><span class="loop-v">(.*?)<\/span><\/li>/g)].map((m) => [m[1], m[2].replace(/<\/?b>/g, '').replace(/\s+/g, ' ')]);
    assert.deepEqual(rows.map((r) => r[0]), ['Crypto', 'Sports', 'Daily high temps']);  // most spent first
    assert.match(rows[0][1], /^\$107\.50 spent · 4 sessions · 23 ideas tried · 4 reached the gate · 1 passed · \$26\.88 per idea that reached the gate$/);
    assert.match(rows[1][1], /^\$76\.00 spent · 3 sessions · 17 ideas tried · 0 reached the gate · 0 passed$/);  // nothing gated: no per-idea figure
    assert.match(rows[2][1], /\$20\.33 per idea that reached the gate$/);
    assert.doesNotMatch(sect, /weather-high|crypto<|usd_per_gated|gated idea/);
    f.clearTimers();
    // One kind of market only: the card's own figures already are that family's, so no section repeats them.
    const one = await researchPage(board({crypto: fam(4, 107.5, 23, 4, 1)}));
    assert.doesNotMatch(one.root.querySelector('#rsimo').innerHTML, /By kind of market/);
    one.clearTimers();
  });

  // Walked 2026-11-01: the card's Passed figure read "6 · 5 held out", as if five were kept back, beside sessions whose
  // passes were "held back" (not cleared). It counts the passes that then held up on the one-time final test.
  test(`${name}: the research card says how many passes also passed the final test, never "held out"`, async () => {
    const b = board({crypto: fam(1, 30, 1, 1, 1), 'weather-high': fam(2, 90, 6, 4, 4)});
    b.total = {...b.total, passed: 6, holdout_passed: 5};
    const f = await researchPage(b);
    const card = f.root.querySelector('#rsimo').innerHTML;
    assert.match(card, /5 also passed the final test/);
    assert.doesNotMatch(card, /held out/);
    f.clearTimers();
  });
}
