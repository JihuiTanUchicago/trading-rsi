// Shared cases, run against each bundle's offline harness (Tau and Meridian), from a brand-new account's first
// fifteen minutes on a phone and a desktop (walked 2026-11-01): sign up, turn autopilot on, its first research
// session starts and runs.
// - Before the switch and until anything is spent, the autopilot card and panel say its research cap per 24 hours
//   beside what a session usually costs; it showed only once the first session had spent something.
// - Deploy with nothing passed and autopilot on sends the reader to the research it is running, not to start one.
// - The empty bell says research sessions don't ring it and links to Research, closing as the link is pressed.
// - Funds (Tau) says paper strategies trade at real market prices, not "the real market".
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const CAP = 'It starts no new research once research has cost $240.00 in 24 hours, your own sessions included; research you start yourself isn’t limited.';

export function firstQuarterHourCases({name, frontend, dashboard, apStatus, fundsData}) {
  const emptyWeek = {research_credits: 0, tested: 0, passed: 0, not_cleared: 0, paper_started: 0, paper_pnl: 0, paper_trades: 0,
    checkin_credits: 0, credits_left: 20, runway_days: null, reload_days: null, pace_days: 0, card_charged: false};
  const est = {yours: null, yours_by_model: {}, sessions: 0, typical: {'opus-4-6': 0.86}};
  const apPage = async (status, me = {}) => {
    const f = frontend({fetchImpl: (path) => {
      if (path === '/api/dashboard') return response(dashboard());
      if (path === '/api/autopilot/status') return response(status);
      if (path === '/api/research/estimate') return response(est);
      if (path === '/api/models') return response({models: [], default: ''});
      if (path.startsWith('/api/chat')) return response({messages: []});
      return response({});
    }});
    f.S.me = {...f.S.me, billing_enabled: true, credit_balance: 20, min_run_balance: 1, auto_reload: {amount: 20, charge: 21.1, threshold: 5},
      models: {default: 'opus-4-6', models: [{key: 'opus-4-6', label: 'Claude Opus 4.6'}]}, ...me};
    f.S.me.user.autopilot = !!status.autopilot; f.S.ap = status;
    await f.viewDashboard(); await settle();
    f.S.ap = status; f.paintDashAp(); await settle(); f.paintDashAp();
    return f;
  };

  test(`${name}: off, the autopilot card says its research cap per 24 hours beside what a session costs`, async () => {
    const off = {...apStatus, autopilot: false, mode: 'off', story: [], idle: null, research_cap: null, research_cap_usd: 240, focus: []};
    const f = await apPage(off);
    const card = f.root.querySelector('#dash-ap');
    assert.ok(card.textContent.includes('Turn on autopilot'), 'the off card');
    assert.ok(card.textContent.includes(CAP), 'the cap before the switch');
    assert.ok(card.textContent.includes('A research session usually costs about $0.86 in credits'));
    assert.doesNotMatch(card.innerHTML, /P&amp;L|paper P|list price|margin/);
    f.clearTimers();
    // No cap from the server (none set, or an older server): nothing said about one.
    const g = await apPage({...off, research_cap_usd: null});
    assert.doesNotMatch(g.root.querySelector('#dash-ap').textContent, /in 24 hours/);
    g.clearTimers();
    // Payments off on this server: no credits line, so no cap line either.
    const h = await apPage(off, {billing_enabled: false});
    assert.doesNotMatch(h.root.querySelector('#dash-ap').textContent, /in 24 hours/);
    h.clearTimers();
  });

  test(`${name}: on with nothing spent yet, the empty week says the cap; once something is spent the credits line says it with the spend`, async () => {
    const on = {...apStatus, autopilot: true, week: emptyWeek, research_cap: {usd: 240, spent_24h: 0, reached: false}, research_cap_usd: 240, billing_blocked: false};
    let f = await apPage(on);
    const left = f.root.querySelector('#dash-ap .ap-week-left');
    assert.ok(left, 'the empty week has its credits line');
    assert.ok(left.textContent.includes(CAP));
    f.clearTimers();
    // Once spent: the long line with the spend, said once (the short one is not added beside it).
    f = await apPage({...on, week: {...emptyWeek, research_credits: 0.31, tested: 1, credits_left: 19.65, pace_days: 0.01},
      research_cap: {usd: 240, spent_24h: 0.31, reached: false}});
    const text = f.root.querySelector('#dash-ap').textContent;
    assert.ok(text.includes('Its research is capped at $240.00 per 24 hours'));
    assert.ok(!text.includes(CAP), 'not said twice');
    f.clearTimers();
  });

  test(`${name}: Deploy with nothing passed and autopilot on links to its research, not to start a session`, async () => {
    for (const on of [true, false]) {
      const f = frontend({fetchImpl: (path) => path === '/api/deployments' ? response({deployments: [], open_positions: []})
        : path === '/api/strategies?status=passed' ? response({counts: {total: 0, passed: 0}, matched: 0, pages: 0, strategies: []}) : response({})});
      f.S.me.user.autopilot = on;
      await f.viewDeploy(); await settle(); await settle();
      assert.match(f.root.querySelector('#deps').innerHTML, /Nothing has passed yet/);
      const html = f.root.querySelector('#deps').innerHTML;
      if (on) {
        assert.match(html, /<a class="btn" href="\/research">See its research<\/a>/);
        assert.doesNotMatch(html, /research\?new=1/);
      } else assert.match(html, /<a class="btn" href="\/research\?new=1">(Start a research session|New session)<\/a>/);
      f.clearTimers && f.clearTimers();
    }
  });

  test(`${name}: the empty bell says research sessions don't ring it, links to Research, and closes as the link is pressed`, async () => {
    const f = frontend({notifications: {unread: 0, items: []}, fetchImpl: (path) => {
      if (path === '/api/notifications/read') return response({ok: true});
      if (path === '/api/autopilot/status') return response({mode: 'off'});
      return response({});
    }});
    f.location.pathname = '/';
    await f.render(); await settle();
    const wrap = f.root.querySelector('.bell-wrap'), trig = wrap.querySelector('.bell-trig'), menu = wrap.querySelector('.bell-menu');
    await trig.onclick({stopPropagation() {}}); await settle();
    const empty = menu.querySelector('.bell-empty');
    assert.match(empty.textContent, /^No notifications yet\. It rings when .+ Research sessions don’t ring it: Research shows each one as it runs\.$/);
    const go = empty.querySelector('a.bell-go');
    assert.equal(go.getAttribute('href'), '/research');
    assert.equal(menu.hidden, false);
    menu.querySelector('#bell-list').listeners.get('click')({target: go});
    assert.equal(menu.hidden, true, 'the menu closes as its link is pressed');
    f.clearTimers();
  });

  if (fundsData) {
    test(`${name}: Funds says paper strategies trade at real market prices, not "the real market"`, async () => {
      const f = frontend({fetchImpl: (path) => path === '/api/funds' ? response(fundsData()) : response({})});
      f.location.pathname = '/funds';
      await f.viewFunds(); await settle();
      const html = f.root.querySelector('#main').innerHTML;
      assert.match(html, /Paper strategies trade at real market prices with it, so results are realistic, but nothing real is at stake\./);
      assert.doesNotMatch(html, /trade the real market/);
      f.clearTimers && f.clearTimers();
    });
  }
}
