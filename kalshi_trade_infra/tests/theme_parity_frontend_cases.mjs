// Shared cases from the Tau-vs-Meridian parity walk (2026-11-01), run against each bundle's offline harness. One seeded
// account (paper and demo books, research-session strategies, a legacy pass and a backtest-only strategy on paper) walked
// in both themes at 1366 and 390 px, every page's actions, money figures, notices and folds compared. What one theme
// said and the other didn't:
// - the dashboard's Kalshi cash with no key: Tau said where to connect it and that live trading isn't open, so Tau
//   places no live trades; Meridian's Accounts menu said only "Connect Kalshi";
// - a Deploy row (Deploy and the dashboard): Tau's Backtest estimate gave the backtest's average a trade before the first
//   close ("+$0.06 a trade, on average"), and for a research-session strategy where its unseen-data result is; Meridian's
//   Details fold said neither;
// - the Research headline's pass share ("50.0% of 14 ideas") showed under Tau's figure and only in a hover title on
//   Meridian's (a phone never showed it);
// - the strategy page's Share hint: Tau said the friend can deploy the copy; Meridian's left that out.
// Display only: every figure is the server's, unchanged, and no book is added to another.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const text = (html) => html.replace(/<button[\s\S]*?<\/button>/g, '').replace(/<[^>]*>/g, ' ').replace(/⁠/g, '').replace(/\s+/g, ' ').trim();
const dep = (id, extra = {}) => ({id, strategy_id: 40 + id, title: 'Dep ' + id, market: 'btc15m', mode: 'paper', status: 'running', status_reason: null,
  realized_pnl: 0, closed_trades: 0, open_positions: 0, unrealized_pnl: null, created_at: '2026-10-24T08:00:00Z', curve: [], stake: 5,
  max_positions: 8, backtest: {out_of_sample: null}, ...extra});

export function themeParityCases({name, frontend, dashboard, strategyFrontend, detailStrategy}) {
  test(`${name}: the dashboard's Kalshi cash with no key says where to connect one and that live trading isn't open`, async () => {
    const card = async (liveOpen) => {
      const f = frontend({fetchImpl: (path) => {
        if (path.startsWith('/api/dashboard')) return response({...dashboard(), live: {connected: false, balance: null, realized_pnl: 0, today_pnl: 0, open_positions: 0}});
        if (path === '/api/autopilot/status') return response({autopilot: false, mode: 'off'});
        return response({messages: [], models: []});
      }});
      f.S.me.live_trading_available = liveOpen;
      await f.viewDashboard(); await settle();
      if (f.clearTimers) f.clearTimers();
      // Tau's figures band or Meridian's Accounts menu: the figure headed Kalshi cash that reads Not connected.
      const cards = Array.from(f.root.querySelectorAll('.m')).map((m) => m.innerHTML).filter((h) => /Kalshi cash/.test(h) && /Not connected/.test(h));
      assert.equal(cards.length, 1, 'one Kalshi cash card');
      return cards[0];
    };
    const closed = await card(false);
    assert.match(closed, /Connect your Kalshi account on <a href="\/funds#connect-kalshi">Funds<\/a> to see your real cash at Kalshi\./);
    assert.match(text(closed), /Live trading isn’t open yet, so Tau places no live trades/);
    assert.match(text(await card(true)), /Connect your Kalshi account on Funds to trade real money/);
  });

  test(`${name}: a Deploy row gives the backtest's average a trade before its first close, and says where a research strategy's unseen-data result is`, async () => {
    const f = frontend({fetchImpl: (path) => {
      if (path === '/api/deployments') return response({deployments: [
        dep(1, {backtest_only: true, backtest: {out_of_sample: {pnl: 1.8, trades: 30}}}),
        dep(2, {closed_trades: 2, realized_pnl: 0.42, backtest: {out_of_sample: {pnl: 3.2, trades: 40}}}),
        dep(3),
        dep(4, {backtest_only: true}),
      ], open_positions: []});
      return response({});
    }});
    f.location.pathname = '/deploy';
    await f.viewDeploy(); await settle();
    if (f.clearTimers) f.clearTimers();
    const html = f.root.querySelector('#deps').innerHTML;
    const said = text(html);
    // $1.80 over 30 later-period trades is +$0.06 a trade; nothing closed yet, so no scaled figure.
    assert.match(said, /\+\$0\.06 a trade, on average/);
    // $3.20 over 40 trades, scaled to its 2 closed trades: +$0.16.
    assert.match(said, /\+\$0\.16/);
    // The research-session strategy with no later-period slice links to its page for the result.
    assert.match(html, /Its unseen-data result is on <a href="\/strategies\/43">its page<\/a>/);
    // A backtest-only one with no figure never claims an unseen-data result.
    assert.doesNotMatch(html, /\/strategies\/44">its page/);
    assert.equal((html.match(/Its unseen-data result is on/g) || []).length, 1);
  });

  test(`${name}: the strategy page's Share hint says the friend can deploy the copy, and a research-only copy says it can't`, async () => {
    const hint = async (over) => {
      const f = strategyFrontend(detailStrategy(over), () => response({}));
      await f.viewStrategy(42); await flush();
      if (f.clearTimers) f.clearTimers();
      return text(f.root.querySelector('#share-what').innerHTML);
    };
    const rule = await hint({status: 'passed'});
    assert.match(rule, /They can deploy it like their own/);
    assert.match(rule, /never your deployments, trades, balances or keys/);
    const code = await hint({kind: 'code', research_only: true, status: 'passed', rules: ['Agent-authored quant code.']});
    assert.match(code, /cannot be deployed/);
    assert.doesNotMatch(code, /They can deploy it/);
  });
}
