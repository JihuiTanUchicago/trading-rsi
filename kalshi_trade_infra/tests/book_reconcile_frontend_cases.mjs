// Shared cases from the book reconciliation walk (2026-10-31), run against each bundle's offline harness (Tau and
// Meridian). One account with paper and demo books (closed trades today and earlier, voided markets settled "scalar",
// open positions), one strategy on both, and a removed paper deployment with closed trades. Every money figure was read
// on Home, Strategies, the strategy page, Deploy, Funds, the track record and the trades CSVs, and compared:
// - Home's Deployed rows sat under the paper card's "+$18.26 realized" adding to $16.87, with nothing saying a removed
//   deployment's three trades (+$1.39) were in the book (Deploy and the strategy page say so; Home didn't);
// - after a demo reload the card's "+$3.49 realized since reload" sat beside the demo trades CSV, whose realized_pnl
//   column adds to every demo trade's −$11.66.
// Display only: every figure is the server's; no book is added to another and no money is worked out on the page.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const text = (html) => html.replace(/<button[\s\S]*?<\/button>/g, '').replace(/<[^>]*>/g, ' ').replace(/⁠/g, '').replace(/&amp;/g, '&').replace(/\s+/g, ' ').trim();
const iso = (ms) => new Date(ms).toISOString();

export function bookReconcileCases({name, frontend, dashboard}) {
  const dep = (id, mode, title, realized, trades, extra = {}) => ({id, strategy_id: id + 3, title, market: 'btc15m', mode, status: 'running',
    stake: 5, max_positions: 4, realized_pnl: realized, unrealized_pnl: null, closed_trades: trades, open_positions: 0, curve: [],
    created_at: iso(Date.now() - 9 * 864e5), ...extra});
  // The walk's account: paper rows +$12.98 (19) and +$3.89 (16), a removed paper deployment's +$1.39 (3) in the book's
  // +$18.26 (38); the demo copy −$15.15 (10), its book the same until a reload.
  const board = ({paper = {}, demo = {}, deployments} = {}) => {
    const base = dashboard();
    return {...base, recent_closed: [],
      deployments: deployments || [dep(3, 'paper', 'Settlement drift', 12.98, 19), dep(2, 'paper', 'Late-rush fade', 3.89, 16),
        dep(4, 'demo', 'Late-rush fade', -15.15, 10, {status: 'paused', status_reason: 'Lost $15.15, more than the $10.00 guard.'})],
      paper: {...base.paper, equity: 1018.11, cash: 1014.5, open_at_cost: 0, open_cost: 0, open_positions: 0, total_pnl: 18.26,
        realized_pnl: 18.26, closed_trades: 38, today_pnl: 2.21, ...paper},
      demo: {closed_trades: 10, open_at_cost: 0, open_cost: 0, open_positions: 0, running: 0, realized_pnl: -15.15, realized_all: -15.15,
        capital: 1000, equity: 984.85, total_pnl: -15.15, roi: -0.015, before_reload: 0, today_pnl: -5.25, health: null, ...demo}};
  };
  const home = async (data) => {
    const f = frontend({fetchImpl: (path) => {
      if (path.startsWith('/api/dashboard')) return response(data);
      if (path === '/api/autopilot/status') return response({autopilot: false, mode: 'off'});
      return response({messages: [], models: [], deployments: []});
    }});
    f.location.pathname = '/';
    await f.viewDashboard(); await settle();
    if (f.clearTimers) f.clearTimers();
    return f;
  };
  const deployed = (f) => text(f.root.querySelector('#dash-deps').innerHTML);
  const demoCard = (f) => {
    const cards = Array.from(f.root.querySelectorAll('.m')).map((m) => m.innerHTML).filter((h) => /starting capital|capital at last reload/.test(h));
    assert.equal(cards.length, 1, 'one demo card');
    return text(cards[0]);
  };

  test(`${name}: Home's Deployed rows say when the book above counts trades from a removed deployment`, async () => {
    const f = await home(board());
    const said = deployed(f);
    assert.match(said, /The paper book’s realized P&L above also counts 3 closed trades from removed deployments, not listed here\./);
    assert.match(said, /Deploy totals each book\./);
    assert.ok(f.root.querySelector('#dash-deps .deps-book a[href="/deploy"]'), 'Deploy is a link');
    // Counts only: no money is worked out here (the removed +$1.39 is Deploy's, from the server), and books are never added.
    assert.doesNotMatch(said, /\$1\.39|\$16\.87|\$3\.11/);
    // The demo rows add up to the demo book: nothing said for demo.
    assert.doesNotMatch(said, /demo book’s realized/);
    // One removed trade says "trade".
    assert.match(deployed(await home(board({paper: {closed_trades: 36}}))), /counts 1 closed trade from removed deployments/);
  });

  test(`${name}: Home's Deployed rows say nothing when they add up to their books`, async () => {
    const f = await home(board({paper: {closed_trades: 35, realized_pnl: 16.87}}));
    assert.equal(f.root.querySelector('#dash-deps .deps-book'), null);
    assert.doesNotMatch(deployed(f), /removed deployments|counts from its last reload/);
    // An answer without the book's count (an older server) says nothing either.
    const g = await home(board({paper: {closed_trades: undefined}}));
    assert.equal(g.root.querySelector('#dash-deps .deps-book'), null);
  });

  test(`${name}: after a demo reload, Home says the demo rows count every demo trade and the CSV link says its total`, async () => {
    // Reloaded to $500 with −$15.15 behind it, then one demo trade made +$3.49: the book reads +$3.49 since reload, the
    // demo row and the trades CSV −$11.66 (every demo trade).
    const reloaded = {demo: {closed_trades: 11, realized_pnl: 3.49, realized_all: -11.66, before_reload: -15.15, capital: 500, equity: 503.49, total_pnl: 3.49}};
    const rows = [dep(3, 'paper', 'Settlement drift', 12.98, 19), dep(2, 'paper', 'Late-rush fade', 3.89, 16),
      dep(4, 'demo', 'Late-rush fade', -11.66, 11, {status: 'paused', status_reason: 'Lost $15.15, more than the $10.00 guard.'})];
    const f = await home(board({...reloaded, deployments: rows}));
    const card = demoCard(f);
    assert.match(card, /\+\$3\.49 realized since reload, after Kalshi fees/);
    assert.match(card, /Download trades \(CSV\)( for the demo book)? · every demo trade, before the reload too: −\$11\.66 realized, after Kalshi fees/);
    assert.match(deployed(f), /Demo rows count every demo trade; the demo book above counts from its last reload\./);
    // Never reloaded: the card's figure is the file's, so the link says nothing more.
    const g = await home(board({deployments: rows.slice(0, 2).concat(dep(4, 'demo', 'Late-rush fade', -15.15, 10))}));
    assert.doesNotMatch(demoCard(g), /every demo trade/);
    assert.doesNotMatch(deployed(g), /last reload/);
    // The paper link never carries it.
    const paperHtml = Array.from(f.root.querySelectorAll('a.csv-link')).map((a) => a.getAttribute('href'));
    assert.ok(paperHtml.includes('/api/trades.csv?mode=paper') && paperHtml.includes('/api/trades.csv?mode=demo'));
    // A reloaded answer without realized_all (an older server) keeps the link as it was.
    const h = await home(board({demo: {...reloaded.demo, realized_all: undefined}, deployments: rows}));
    assert.doesNotMatch(demoCard(h), /every demo trade/);
  });
}
