// Shared cases, run against each bundle's offline harness (Tau and Meridian). A relative time a poll compares ("2m ago",
// "as of 11 min ago", "read 1m ago") is an age element (core.js ageAt: <time data-age datetime>), left out of the
// poll's comparison. A poll whose only change is the minute says those words again in place (ageTick, on the poll's
// own tick) and writes nothing else: on Home, Funds and the strategy page each minute's new words made the whole page
// be written again (up to 600ms on a Meridian phone), losing scroll, selection and focus. What a reader sees is the
// same at every moment: the same words and rounding, an old mark dimmed past three minutes as before, and no age
// inside a live region (a tick is never announced).
import assert from './assert_text.mjs';
import {test} from 'node:test';
import {shown} from './age_text.mjs';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const MARKETS = [{key: 'btc15m', series: 'KXBTC15M', label: 'Bitcoin up or down in 15 minutes'}];
const ago = (s) => new Date(Date.now() - s * 1000).toISOString();
const polls = (f) => [...f.intervals.values()].filter((c) => String(c).includes('!document.hidden && gen === S.gen) tick()'));
const pollOnce = async (f) => { for (const t of polls(f)) t(); await settle(); };
const pos = (extra) => ({id: 1, title: 'Fade', ticker: 'KXBTC15M-26OCT301815-15', side: 'yes', qty: 12, entry_price: .41, entry_fee: .17, cost: 5.09,
  at_cost: 4.92, mark: .47, value: 5.64, unrealized: .55, mark_ts: ago(20), mode: 'paper', deployment_id: 1, strategy_id: 3, status: 'open',
  opened_at: ago(30), market_close_time: new Date(Date.now() + 9 * 60e3).toISOString(), ...extra});
const dep = (extra) => ({id: 1, strategy_id: 3, title: 'Fade', market: 'btc15m', mode: 'paper', status: 'running', status_reason: null,
  realized_pnl: 0, closed_trades: 0, open_positions: 1, unrealized_pnl: .55, created_at: ago(30), curve: [], stake: 5,
  max_positions: 20, backtest: {out_of_sample: null}, ...extra});
const words = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const ages = (el) => el.querySelectorAll('time[data-age]');
// No age sits inside a live region: saying its words again would be read out every minute.
const unannounced = (el) => {
  for (const t of ages(el)) {
    for (let p = t; p; p = p.parent) {
      assert.ok(!p.getAttribute('aria-live') && !['status', 'alert', 'log'].includes(p.getAttribute('role')),
        `an age (${t.getAttribute('data-age')}) sits inside a live region`);
    }
  }
};

// Each form's words at 0s, 59s, 61s, 59m, 61m and a day, as the helper each place said them with before (when,
// sinceMin, apAgo, bellShown). A day on: `when` gives the date, the bell its day and clock.
const STEPS = [0, 59, 61, 59 * 60, 61 * 60, 86400 + 1];
const SAID = {
  when: ['just now', 'just now', '1m ago', '59m ago', '1h ago', /^[A-Z][a-z]{2} \d{1,2}$/],
  since: ['just now', 'just now', 'just now', '59m ago', '61m ago', '24h ago'],
  ap: ['just now', 'just now', 'just now', '59 min ago', '61 min ago', '24 hours ago'],
  bell: ['just now', 'just now', '1m ago', '59m ago', '1h ago', /^[A-Z][a-z]{2} \d{1,2}, \d\d:\d\d \S+$/],
};
const says = (got, want, what) => (want instanceof RegExp ? assert.match(got, want, what) : assert.equal(got, want, what));

export function ageTickCases({name, frontend, fundsData, dashboard}) {
  test(`${name}: an age reads the same words at 0s, 59s, 61s, 59m, 61m and a day, drawn or said again in place`, () => {
    const f = frontend({fetchImpl: () => response({})});
    const core = f.win.TauCore;
    const old = {when: core.when, since: core.sinceMin, ap: core.apAgo};
    for (const [form, want] of Object.entries(SAID)) {
      STEPS.forEach((s, i) => {
        const iso = ago(s);
        const box = f.document.createElement('div');
        box.innerHTML = core.ageAt(iso, form);
        const t = box.querySelector('time');
        assert.equal(t.getAttribute('data-age'), form);
        assert.equal(t.getAttribute('datetime'), iso);
        says(t.textContent, want[i], `${form} drawn at ${s}s`);
        if (old[form]) assert.equal(t.textContent, old[form](iso), `${form} reads as its helper does`);
      });
    }
    // Drawn once at 0s, then said again in place as the clock moves: the same words at every step.
    const box = f.document.createElement('div');
    const iso = ago(0);
    box.innerHTML = Object.keys(SAID).map((form) => core.ageAt(iso, form)).join(' ');
    let moved = 0;
    STEPS.forEach((s, i) => {
      f.shiftClock(s * 1000 - moved); moved = s * 1000;
      core.ageTick(box);
      Object.keys(SAID).forEach((form) => says(box.querySelector(`time[data-age="${form}"]`).textContent, SAID[form][i], `${form} said again at ${s}s`));
    });
    // Its key leaves the words out and keeps the time and everything else.
    const a = `<p>read ${core.ageAt(iso)}</p>`;
    assert.equal(core.ageKey(a), core.ageKey(a.replace('just now', '5m ago')));
    assert.notEqual(core.ageKey(a), core.ageKey(core.ageAt(ago(5))));
    assert.notEqual(core.ageKey(a), core.ageKey(a.replace('read', 'Read')));
  });

  test(`${name}: a Home poll whose only change is the minute leaves the page as drawn`, async () => {
    const data = {...dashboard(), deployments: [dep()], open_positions: [pos({id: 1})], recent_closed: []};
    const f = frontend({fetchImpl: (path) => {
      if (path.startsWith('/api/dashboard')) return response(data);
      if (path === '/api/autopilot/status') return response({autopilot: false, mode: 'off'});
      return response({});
    }});
    f.S.me.markets = MARKETS;
    await f.viewDashboard(); await settle();
    const main = f.root.querySelector('#main');
    const drawn = [...ages(main)], writes = main.writes, html = main.innerHTML;
    assert.ok(drawn.length >= 1, 'an open position says when it opened');
    unannounced(main);
    assert.ok(drawn.some((t) => t.textContent === 'just now'));
    f.shiftClock(61000);
    await pollOnce(f);
    assert.equal(main.writes, writes, 'the page is not written');
    assert.equal(main.innerHTML, html);
    assert.ok(drawn.every((t) => t.isConnected), 'every age is the one drawn');
    assert.ok(drawn.some((t) => t.textContent === '1m ago'), 'it says "1m ago" in place');
    f.clearTimers();
  });

  test(`${name}: a Deploy poll whose only change is the minute writes no row, and its ages say the new words`, async () => {
    const answer = {open_positions: [pos({id: 1, mark_ts: ago(10 * 60)})], deployments: [dep()]};
    const f = frontend({fetchImpl: (path) => path.startsWith('/api/deployments') ? response(answer) : response({})});
    f.S.me.markets = MARKETS; f.location.pathname = '/deploy';
    await f.viewDeploy(); await settle();
    const main = f.root.querySelector('#main');
    const drawn = [...ages(main)], writes = main.writes;
    assert.ok(drawn.length >= 2, 'the old mark and the deployment each say their age');
    unannounced(main);
    const markAge = () => words(f.root.querySelector('#openpos .mark-age'));
    assert.match(markAge(), /as of 10 min ago/);
    f.shiftClock(61000);
    await pollOnce(f);
    assert.equal(main.writes, writes, 'the page is not written');
    assert.deepEqual([...ages(main)], drawn, 'every age is the one drawn');
    assert.ok(drawn.every((t) => t.isConnected), 'no row was written again');
    assert.equal(drawn.find((t) => t.closest('.mark-age')).textContent, '11 min ago');
    assert.ok(drawn.some((t) => t.getAttribute('data-age') === 'when' && t.textContent === '1m ago'), 'the deployment says "1m ago"');
    // A figure that moves is drawn as before.
    answer.open_positions = [pos({id: 1, mark_ts: ago(11 * 60 + 61), unrealized: .61})];
    f.shiftClock(30000);
    await pollOnce(f);
    assert.ok(!drawn.every((t) => t.isConnected), 'a moved figure is written again');
    f.clearTimers();
  });

  test(`${name}: an old mark is dimmed past three minutes as before, on the poll that crosses it`, async () => {
    const answer = {open_positions: [pos({id: 1, mark_ts: ago(170)})], deployments: [dep({created_at: '2026-10-01T05:00:00Z'})]};
    const f = frontend({fetchImpl: (path) => path.startsWith('/api/deployments') ? response(answer) : response({})});
    f.S.me.markets = MARKETS; f.location.pathname = '/deploy';
    await f.viewDeploy(); await settle();
    const cell = () => f.root.querySelector('#openpos .pos-unreal');
    const first = cell();
    assert.equal(first.querySelectorAll('.mark-old').length, 0, 'under three minutes: undimmed');
    assert.equal(first.querySelectorAll('.mark-age').length, 0);
    f.shiftClock(9000);  // 179s
    await pollOnce(f);
    assert.equal(cell(), first, '179s: drawn the same, left alone');
    assert.equal(cell().querySelectorAll('.mark-old').length, 0);
    f.shiftClock(2000);  // 181s
    await pollOnce(f);
    assert.notEqual(cell(), first, 'past three minutes the figure is drawn again, dimmed');
    assert.equal(cell().querySelectorAll('.mark-old').length, 1);
    assert.match(words(cell().querySelector('.mark-age')), /^as of 3 min ago/);
    assert.equal(cell().querySelector('.mark-age time[data-age="ap"]').textContent, '3 min ago');
    f.clearTimers();
  });

  test(`${name}: a Funds poll whose only change is the minute leaves the page as drawn, and says "read 1m ago"`, async () => {
    const at = ago(0);
    let balance = 0;
    const f = frontend({fetchImpl: (path) => {
      if (path.startsWith('/api/funds/cash')) return new Promise(() => {});
      if (path === '/api/funds') return response({...fundsData(), kalshi_dev: {connected: true, key_id: 'DEVK…5678', balance, balance_at: at, copies_from: 100}});
      return response({});
    }});
    f.location.pathname = '/funds';
    await f.viewFunds(); await settle();
    const main = f.root.querySelector('#main');
    const drawn = [...ages(main)], writes = main.writes;
    assert.ok(drawn.length >= 1);
    unannounced(main);
    const spot = () => shown(f.root.querySelector('[data-cash="demo-ok"]'));
    assert.match(spot(), /read just now\)/);
    f.shiftClock(61000);
    await pollOnce(f);
    assert.equal(main.writes, writes, 'the page is not written');
    assert.ok(drawn.every((t) => t.isConnected), 'every age is the one drawn');
    assert.match(spot(), /read 1m ago\)/);
    // A new figure is drawn as before.
    balance = 12;
    f.shiftClock(30000);
    await pollOnce(f);
    assert.ok(!drawn.every((t) => t.isConnected), 'a new balance is written');
    assert.match(spot(), /\$12\.00, read 1m ago\)/);
    f.clearTimers();
  });
}
