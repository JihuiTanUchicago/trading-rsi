// Shared cases from the demo-reload walk (2026-10-31), run against each bundle's offline harness. One account whose demo
// book was reloaded to $500 an hour after a losing morning (before_reload −$15.15), then closed one more demo trade
// (+$3.49 since the reload), with its demo copy paused by its loss guard beside two running paper deployments, read in
// both themes at 390 and 1366 px:
// - the demo card read "+$3.49 realized since reload" over "Today (UTC): −$1.76 realized", the day counting the
//   morning's trades from before the reload, with nothing saying which figure counted from where;
// - its "Download trades (CSV)" sat beside the since-reload figure while the file holds every demo trade;
// - the dashboard's demo deployment row read "−$11.66" (every trade it made) under that "+$3.49 since reload" card;
// - Deploy's Money box read "Paper money only, nothing live" with a paused demo deployment listed on the same board.
// Display only: every figure is the server's, unchanged, and no book is added to another.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const text = (html) => html.replace(/<button[\s\S]*?<\/button>/g, '').replace(/<[^>]*>/g, ' ').replace(/⁠/g, '').replace(/\s+/g, ' ').trim();
const DAY = 864e5;
const dayStart = () => Math.floor(Date.now() / DAY) * DAY;
const iso = (ms) => new Date(ms).toISOString();
const closed = (mode, at, extra = {}) => ({id: Math.round(at / 1000) % 100000, ticker: 'KXBTC15M-26OCT290145-45', title: 'Late-rush fade',
  side: 'yes', qty: 6, entry_price: 0.42, mode, pnl: -2.66, market_close_time: iso(at), opened_at: iso(at - 9 * 60e3), closed_at: iso(at), ...extra});
const SINCE_FIRST = 'since its first demo trade: a reload doesn’t reset it';

export function demoReloadTruthCases({name, frontend, dashboard}) {
  const dep = (mode, extra = {}) => ({id: mode === 'demo' ? 4 : 2, strategy_id: 5, title: 'Late-rush fade', market: 'btc15m', mode, status: 'running',
    stake: 5, max_positions: 4, realized_pnl: mode === 'demo' ? -11.66 : 3.89, unrealized_pnl: null, closed_trades: mode === 'demo' ? 11 : 16,
    open_positions: 0, curve: [], created_at: iso(dayStart() - 9 * DAY), backtest: {out_of_sample: null}, ...extra});
  const pausedDemo = () => dep('demo', {status: 'paused', status_reason: 'Lost $15.15, more than the $10.00 guard.'});
  const board = (demo) => {
    const base = dashboard();
    return {...base, deployments: [dep('paper'), pausedDemo()],
      recent_closed: [closed('demo', dayStart() + 60e3, {pnl: 3.49}), closed('demo', dayStart() + 30e3), closed('paper', dayStart() + 20e3, {pnl: 2.86})],
      paper: {...base.paper, equity: 1003.89, cash: 1003.89, open_at_cost: 0, open_positions: 0, total_pnl: 3.89, realized_pnl: 3.89,
        closed_trades: 16, today_pnl: 2.21},
      demo: {closed_trades: 11, open_at_cost: 0, open_cost: 0, open_positions: 0, running: 0, realized_pnl: 3.49, capital: 500,
        equity: 503.49, total_pnl: 3.49, roi: 0.007, before_reload: -15.15, realized_all: -11.66, today_pnl: -1.76, health: null, ...demo}};
  };
  const home = async (data) => {
    const f = frontend({fetchImpl: (path) => {
      if (path.startsWith('/api/dashboard')) return response(data);
      if (path === '/api/autopilot/status') return response({autopilot: false, mode: 'off'});
      return response({messages: [], models: []});
    }});
    await f.viewDashboard(); await settle();
    if (f.clearTimers) f.clearTimers();
    const cards = Array.from(f.root.querySelectorAll('.m')).map((m) => m.innerHTML).filter((h) => /starting capital|capital at last reload/.test(h));
    assert.equal(cards.length, 1, 'one demo card');
    const deps = f.root.querySelector('#deps') || f.root.querySelector('#main');
    return {card: text(cards[0]), page: text(deps.innerHTML)};
  };

  test(`${name}: after a demo reload the card's day says it counts from midnight UTC, not from the reload`, async () => {
    const {card} = await home(board({}));
    assert.match(card, /\$500\.00 capital at last reload/);
    assert.match(card, /\+\$3\.49 realized since reload, after Kalshi fees/);
    assert.match(card, /Today \(UTC\): −\$1\.76 realized, after Kalshi fees · from midnight UTC, not from the reload/);
    // Demo money only: never paper's day (+$2.21), nor the book's figures added to it.
    assert.doesNotMatch(card, /\$2\.21|\$1\.73|\$5\.25/);
    // A book never reloaded keeps its day as it was.
    const never = (await home(board({before_reload: 0, capital: 1000, equity: 988.34, realized_pnl: -11.66, total_pnl: -11.66}))).card;
    assert.match(never, /Today \(UTC\): −\$1\.76 realized, after Kalshi fees/);
    assert.doesNotMatch(never, /not from the reload/);
  });

  test(`${name}: a reloaded demo book's trades file says it holds the trades from before the reload too`, async () => {
    const {card} = await home(board({}));
    // The file's own total beside its link (#749's realized_all), so a reader sees why it adds to more than the card.
    assert.match(card, /Download trades \(CSV\) for the demo book.*every demo trade, before the reload too: .*−\$11\.66/);
    const never = (await home(board({before_reload: 0, capital: 1000, equity: 988.34, realized_pnl: -11.66, total_pnl: -11.66}))).card;
    assert.match(never, /Download trades \(CSV\)/);
    assert.doesNotMatch(never, /before the reload/);
  });

  test(`${name}: after a reload the dashboard's demo deployment says its figure counts from its first demo trade`, async () => {
    const {page} = await home(board({}));
    assert.match(page, /−\$11\.66/);
    // Once, for the demo row: the paper row never says it.
    assert.equal(page.split(SINCE_FIRST).length - 1, 1, page);
    const never = (await home(board({before_reload: 0, capital: 1000, equity: 988.34, realized_pnl: -11.66, total_pnl: -11.66}))).page;
    assert.doesNotMatch(never, /since its first demo trade/);
  });

  const money = async (deployments) => {
    const f = frontend({fetchImpl: (path) => (path === '/api/deployments' ? response({deployments, open_positions: []}) : response({}))});
    await f.viewDeploy(); await settle();
    if (f.clearTimers) f.clearTimers();
    return f.root.querySelector('#dfigs').innerHTML.match(/<dt class="lab">Money<\/dt>[\s\S]*$/)[0];
  };

  test(`${name}: Deploy's Money box says a paused demo book is paused, never "Paper money only"`, async () => {
    for (const [deps, line, marks] of [
      [[dep('paper'), pausedDemo()], 'Paper running, demo paused, nothing live', /mk">Paper<\/span> <span class="mk dep">Demo/],
      [[pausedDemo()], 'Demo paused, nothing live', /^(?![\s\S]*mk">Paper)[\s\S]*mk dep">Demo/],
      [[dep('paper', {status: 'paused'}), pausedDemo()], 'Paper and demo paused, nothing live', /mk">Paper<\/span> <span class="mk dep">Demo/]]) {
      const box = await money(deps);
      assert.match(box, new RegExp(`<dd class="sub-fig">${line}</dd>`));
      assert.match(box, marks);
      assert.doesNotMatch(box, /Paper money only/);
    }
    // Unchanged where nothing demo is listed, or demo runs.
    assert.match(await money([dep('paper')]), /<dd class="sub-fig">Paper money only, nothing live<\/dd>/);
    assert.match(await money([dep('paper'), dep('demo')]), /<dd class="sub-fig">Paper and demo only, nothing live<\/dd>/);
  });
}
