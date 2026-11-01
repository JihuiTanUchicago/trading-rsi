// Shared cases, run against each bundle's offline harness (Tau and Meridian), from the audit of money worked out in the
// browser (2026-11-01): every figure is the server's, or arithmetic on the server's that can't miss a cent or mix books.
// Each case is the input that showed the miss:
// - A Deploy row's backtest estimate divided the backtest's P&L by its trades as a float: $0.21 over 6 trades is $0.035,
//   0.034999… as a float, and read "+$0.03 a trade"; $0.03 over 2 trades times 15 closed read +$0.22 for $0.225. The
//   strategy page's "would have made" line did the same. Both now round the exact figure once (core usdShare).
// - The Research card's credits per idea divided the same way: $0.21 over 6 ideas read $0.03 for $0.035.
// - Billing's top-up price rounded the amount typed to whole cents first: $5.006 read "You'll pay $5.29" (as if $5.01) while
//   the server charges Decimal("5.006") x 1.055, $5.28.
// - A market page's "N open at cost + fees" added its rows' rounded costs: two positions of $0.335 read $0.68 beside the
//   dashboard's, Funds' and the strategy page's $0.67. It says the server's book figure (mine.books).
// - A price stored on four places, 0.5005, read 50¢ (500.4999… tenths as a float); it reads 50.1¢, half up.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const text = (html) => String(html).replace(/<button[\s\S]*?<\/button>/g, '').replace(/<[^>]*>/g, ' ').replace(/⁠/g, '').replace(/\s+/g, ' ').trim();
const dep = (id, extra = {}) => ({id, strategy_id: 40 + id, title: 'Dep ' + id, market: 'btc15m', mode: 'paper', status: 'running', status_reason: null,
  realized_pnl: 0, closed_trades: 0, open_positions: 0, unrealized_pnl: null, created_at: '2026-10-24T08:00:00Z', curve: [], stake: 5,
  max_positions: 8, backtest: {out_of_sample: null}, ...extra});

export function moneyDriftCases({name, frontend, strategyFrontend, detailStrategy, billingPage, cardedBilling}) {
  test(`${name}: a Deploy row's backtest estimate is the exact figure rounded once, never a float a hair under the half cent`, async () => {
    assert.ok(0.21 / 6 < 0.035 && (0.03 / 2) * 15 < 0.225, 'the float quotients sit under the half cent');
    const f = frontend({fetchImpl: (path) => (path === '/api/deployments' ? response({deployments: [
      dep(1, {backtest: {out_of_sample: {pnl: 0.21, trades: 6}}}),
      dep(2, {closed_trades: 15, realized_pnl: 0.4, backtest: {out_of_sample: {pnl: 0.03, trades: 2}}}),
    ], open_positions: []}) : response({}))});
    f.location.pathname = '/deploy';
    await f.viewDeploy(); await settle();
    if (f.clearTimers) f.clearTimers();
    const said = text(f.root.querySelector('#deps').innerHTML);
    assert.match(said, /\+\$0\.04 a trade, on average/);
    assert.doesNotMatch(said, /\+\$0\.03 a trade/);
    assert.match(said, /\+\$0\.23/);
    assert.doesNotMatch(said, /\+\$0\.22/);
  });

  test(`${name}: the strategy page's "would have made" line is the exact figure rounded once`, async () => {
    const f = strategyFrontend(detailStrategy({status: 'passed', stake: 5, out_of_sample: {...detailStrategy().out_of_sample, pnl: 0.03, trades: 2},
      deployments: [{id: 3, mode: 'paper', status: 'running', realized_pnl: 0.4, closed_trades: 15, open_positions: 0, stake: 5, created_at: '2026-10-27T08:00:00Z'}],
      book_totals: {paper: {pnl: 0.4, trades: 15, open: 0}}}), () => response({}));
    await f.viewStrategy(42); await settle();
    if (f.clearTimers) f.clearTimers();
    const line = f.root.querySelector('.real-pace').textContent;
    assert.match(line, /15 trades would have made \+\$0\.23/);
  });

  test(`${name}: the Research card's credits per idea are the exact quotient rounded once`, async () => {
    const fam = (runs, cost_usd, explored, gated, passed) => ({runs, cost_usd, explored, gated, passed});
    const board = {total: {runs: 2, explored: 6, gated: 6, gate_reach_rate: 1, passed: 0, holdout_passed: 0, cost_usd: 0.21, pipeline_death_share: 0},
      families: {crypto: fam(1, 0.21, 6, 6, 0), sports: fam(1, 0, 0, 0, 0)}, days: [], trend: null, playbooks: [], scout: [], fix_list: [], data_requests: [], paper: null, tournament: null};
    const f = frontend({fetchImpl: (path) => {
      if (path.startsWith('/api/runs')) return response({runs: [], counts: {research_running: 0, research_queued: 0, research_paused: 0}, policy: null, totals: {tried: 6, passed: 0}, v: 'v1'});
      if (path.startsWith('/api/research/scoreboard')) return response(board);
      if (path === '/api/models') return response({models: [], default: ''});
      if (path === '/api/research/context') return response({markets: []});
      if (path === '/api/autopilot/status') return response({autopilot: false, mode: 'off'});
      return response({});
    }});
    f.S.me.user.autopilot = false;
    f.location.pathname = '/research';
    await f.viewResearch(); await settle();
    if (f.clearTimers) f.clearTimers();
    const card = text(f.root.querySelector('#rsimo').innerHTML);
    assert.match(card, /\$0\.04 per idea /);
    assert.match(card, /\$0\.04 per idea that reached the gate/);
    assert.doesNotMatch(card, /\$0\.03 per idea/);
  });

  test(`${name}: the top-up price of an amount typed past the cent is what the server charges for it`, async () => {
    const f = billingPage(cardedBilling(), [], {});
    await f.viewBilling(); await flush();
    const amt = f.root.querySelector('#topup-amt'), note = () => f.root.querySelector('#topup-note').textContent;
    // Decimal("5.006") x 1.055 is 5.28133: the card pays $5.28 (billing.topup_charge_cents), not $5.01's $5.29.
    amt.value = '5.006'; amt.oninput();
    assert.match(note(), /^You’ll pay \$5\.28 \(/);
    // Whole amounts as before: $19 x 1.055 is $20.045, so $20.05; $37 is $39.035, so $39.04.
    amt.value = '19'; amt.oninput();
    assert.match(note(), /^You’ll pay \$20\.05 \(\$19\.00 credits/);
    amt.value = '37'; amt.oninput();
    assert.match(note(), /^You’ll pay \$39\.04 \(/);
    if (f.clearTimers) f.clearTimers();
  });

  test(`${name}: a market page's open cost per book is the server's exact sum, and a four-place price reads half up`, async () => {
    const ticker = 'KXBTCD-26OCT2617-T113499.99';
    const held = (id, over = {}) => ({id, title: 'Drift', strategy_id: 1, ticker, side: 'yes', qty: 1, entry_price: 0.33, cost: 0.34, unrealized: null,
      mode: 'paper', status: 'open', market_close_time: '2026-10-26T21:00:00Z', ...over});
    const live = held(3, {mode: 'live', entry_price: 0.5005, cost: 0.51});
    const body = {ticker, series: 'KXBTCD', title: 'Bitcoin price at 5pm EDT?', sub: '$113,500 or above', status: 'active', yes_bid: '0.55', yes_ask: '0.57',
      last: '0.55', volume_24h: '15234', open_interest: '40211', close_time: '2026-10-26T21:00:00Z', as_of: '2026-10-26T11:00:00Z', rules: 'Resolves Yes.',
      period_min: 1, points: Array.from({length: 130}, (_, i) => ({t: 1793012400 + 60 * i, yes: .5, vol: 1})),
      mine: {open: [held(1), held(2), live], closed: [],
        books: {paper: {open_positions: 2, open_at_cost: 0.66, open_cost: 0.67}, live: {open_positions: 1, open_at_cost: 0.5, open_cost: 0.51}}}};
    const f = frontend({fetchImpl: (path) => (path.startsWith('/api/explore/market?') ? response(body) : response({}))});
    f.S.me.markets = [{key: 'btc_daily', series: 'KXBTCD', label: 'Bitcoin above a set price at 5pm ET', category: 'Crypto', close_hour_et: 17}];
    f.location.pathname = '/explore'; f.location.search = '?m=' + encodeURIComponent(ticker);
    await f.viewExplore(); await settle();
    if (f.clearTimers) f.clearTimers();
    const sums = f.root.querySelectorAll('.mk-book .hint').map((el) => el.textContent);
    const paper = sums.find((s) => /^2 open/.test(s)), liveSum = sums.find((s) => /^1 open/.test(s));
    assert.match(paper, /^2 open at cost \+ fees \$0\.67/);
    assert.doesNotMatch(paper, /\$0\.68/);
    // Each book its own figure: live's never added to paper's.
    assert.match(liveSum, /^1 open at cost \+ fees \$0\.51/);
    assert.doesNotMatch(sums.join(' '), /\$1\.18|\$1\.19/);
    // The rows keep their own rounded cost.
    assert.equal(f.root.querySelectorAll('td').filter((td) => td.getAttribute('data-lab') === 'Cost + fee' && td.textContent === '$0.34').length, 2);
    assert.match(f.root.innerHTML, /YES ×1 @ 50\.1¢/);
    assert.doesNotMatch(f.root.innerHTML, /@ 50¢/);
  });
}
