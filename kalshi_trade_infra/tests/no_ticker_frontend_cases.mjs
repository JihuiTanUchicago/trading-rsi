// Shared cases, run against each bundle's offline harness (Tau and Meridian): no customer ever reads a raw Kalshi ticker
// or series key. A keyboard walk on 2026-11-01 found Deploy's open positions reading "Kalshi contract KXSOL15M-…", once
// per row: the code sat on a small line under each market name, and on Funds' paper history, the strategy page's trades,
// Meridian's dashboard details and a market page's heading line too. Each page here is drawn from one heavy book (crypto
// 15-minute and daily, weather highs in three cities, sports with a draw and a game Kalshi cancelled, paper and demo, a
// deleted strategy's position, a series no market covers) and its text, attributes included, holds no ticker. Positions
// that share a name (five Solana 15-minute windows) each say their settle time inside the name (apartWords).
import assert from './assert_text.mjs';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const MARKETS = [
  {key: 'btc15m', series: 'KXBTC15M', label: 'Bitcoin up or down in 15 minutes', category: 'Crypto'},
  {key: 'sol15m', series: 'KXSOL15M', label: 'Solana up or down in 15 minutes', category: 'Crypto'},
  {key: 'btc_daily', series: 'KXBTCD', label: 'Bitcoin above a set price at 5pm ET', close_hour_et: 17, category: 'Crypto'},
  {key: 'sol_daily', series: 'KXSOLD', label: 'Solana above a set price at 5pm ET', close_hour_et: 17, category: 'Crypto'},
  {key: 'high_ny', series: 'KXHIGHNY', label: 'NYC daily high temperature', category: 'Weather'},
  {key: 'kxhighchi', series: 'KXHIGHCHI', label: 'Chicago daily high temperature', category: 'Weather'},
  {key: 'kxhightokc', series: 'KXHIGHTOKC', label: 'Oklahoma City daily high temperature', category: 'Weather'},
  {key: 'nfl_game', series: 'KXNFLGAME', label: 'NFL game winner (moneyline)', category: 'Sports'},
  {key: 'epl_game', series: 'KXEPLGAME', label: 'English Premier League game result', category: 'Sports'},
  {key: 'nba_game', series: 'KXNBAGAME', label: 'NBA game winner (moneyline)', category: 'Sports'},
];
const DEPS = [['sol15m', 'paper', 'Solana late fade'], ['sol15m', 'demo', 'Solana late fade'], ['btc_daily', 'paper', 'Bitcoin 5pm drift'],
  ['high_ny', 'paper', 'NYC forecast edge'], ['kxhighchi', 'demo', 'Chicago forecast gap'], ['kxhightokc', 'paper', 'Oklahoma City buckets'],
  ['nfl_game', 'paper', 'NFL favourites'], ['epl_game', 'paper', 'EPL draws'], ['btc15m', 'paper', 'Late fade']]
  .map(([market, mode, title], i) => ({id: i + 1, strategy_id: 100 + i, market, mode, title, status: 'running', stake: 5, max_positions: 8,
    realized_pnl: 0, closed_trades: 0, open_positions: 1, created_at: '2026-10-30T12:00:00Z'}));
const HOUR = 3600e3;
const at = (ms) => new Date(Date.now() + ms).toISOString();
const sol = (k, mode, dep, side) => ({ticker: `KXSOL15M-26NOV01${String(18 + Math.floor((k * 15) / 60)).padStart(2, '0')}${String((k * 15) % 60).padStart(2, '0')}-00`,
  deployment_id: dep, title: 'Solana late fade', mode, side, market_close_time: at(HOUR + k * 15 * 60e3)});
const POSITIONS = [
  ...[0, 1, 2, 3, 4].map((k) => sol(k, 'paper', 1, k % 2 ? 'no' : 'yes')),
  sol(0, 'demo', 2, 'yes'), sol(1, 'demo', 2, 'yes'),
  {ticker: 'KXBTCD-26NOV0117-T109999.99', deployment_id: 3, title: 'Bitcoin 5pm drift', mode: 'paper', side: 'yes', market_close_time: at(4 * HOUR)},
  {ticker: 'KXBTCD-26NOV0117-T110999.99', deployment_id: 3, title: 'Bitcoin 5pm drift', mode: 'paper', side: 'no', market_close_time: at(4 * HOUR)},
  {ticker: 'KXHIGHNY-26NOV02-B58.5', deployment_id: 4, title: 'NYC forecast edge', mode: 'paper', side: 'yes', market_close_time: at(20 * HOUR)},
  {ticker: 'KXHIGHNY-26NOV02-T64', deployment_id: 4, title: 'NYC forecast edge', mode: 'paper', side: 'no', market_close_time: at(20 * HOUR)},
  {ticker: 'KXHIGHCHI-26NOV02-B48.5', deployment_id: 5, title: 'Chicago forecast gap', mode: 'demo', side: 'yes', market_close_time: at(21 * HOUR)},
  {ticker: 'KXHIGHTOKC-26NOV02-T71', deployment_id: 6, title: 'Oklahoma City buckets', mode: 'paper', side: 'yes', market_close_time: at(21 * HOUR)},
  {ticker: 'KXNFLGAME-26NOV01KCBUF-KC', deployment_id: 7, title: 'NFL favourites', mode: 'paper', side: 'yes', market_close_time: at(6 * HOUR)},
  {ticker: 'KXEPLGAME-26NOV01ARSCHE-TIE', deployment_id: 8, title: 'EPL draws', mode: 'paper', side: 'yes', market_close_time: at(-HOUR)},
  // A deleted strategy's position (the server names it and says why), and a series no market here covers.
  {ticker: 'KXBTC15M-26NOV011816-00', deployment_id: 99, title: 'Deleted strategy', why: 'Its strategy was deleted, so Tau no longer sells or settles this position.',
    mode: 'paper', side: 'yes', market_close_time: at(HOUR / 2)},
  {ticker: 'KXFEDDECISION-26DEC-H0', deployment_id: 98, title: 'Late fade', mode: 'paper', side: 'yes', market_close_time: at(30 * 24 * HOUR)},
].map((p, i) => ({id: i + 1, qty: 4 + (i % 5), entry_price: 0.4, entry_fee: 0.02, cost: 1.62, at_cost: 1.6, unrealized: 0.05,
  mark_ts: new Date().toISOString(), opened_at: at(-HOUR), status: 'open', ...p}));
const CLOSED = [
  {ticker: 'KXNBAGAME-26OCT30GSWMIA-GSW', deployment_id: 99, title: 'NBA road dogs', mode: 'paper', side: 'yes', exit_price: 0.5, pnl: 1.2, close_reason: 'settled'},
  {ticker: 'KXSOL15M-26NOV011500-00', deployment_id: 1, title: 'Solana late fade', mode: 'paper', side: 'yes', exit_price: 1, pnl: 2.3, close_reason: 'settled'},
  {ticker: 'KXSOL15M-26NOV011515-00', deployment_id: 1, title: 'Solana late fade', mode: 'paper', side: 'yes', exit_price: 0, pnl: -1.6, close_reason: 'settled'},
].map((p, i) => ({id: 100 + i, qty: 4, entry_price: 0.4, entry_fee: 0.02, cost: 1.62, status: 'closed', opened_at: at(-5 * HOUR), closed_at: at(-3 * HOUR),
  market_close_time: at(-3 * HOUR - i * 15 * 60e3), ...p}));
const TICKER = /KX[A-Z0-9]{3,}/;
// What a reader can see or hear: the text, and every attribute a screen reader or a hover reads. A link's target and the
// page's own data attributes are not read.
const readable = (html) => [html.replace(/<[^>]*>/g, ' '),
  ...[...html.matchAll(/ (?:aria-label|title|alt|placeholder)="([^"]*)"/g)].map((m) => m[1])].join('\n');
const noTicker = (html, where) => {
  const hit = readable(html).match(new RegExp(`[^\\n]{0,60}${TICKER.source}[^\\n]{0,30}`));
  assert.equal(hit && hit[0], null, `${where}: a raw ticker a reader sees`);
  assert.doesNotMatch(html, /kalshi-ref/, where);
};

export function noTickerCases({name, frontend, strategyFrontend, detailStrategy, dashboard, fundsData, billingPage, cardedBilling}) {
  test(`${name}: Deploy's open positions never show a ticker, and five windows of one 15-minute market each say their settle time`, async () => {
    const f = frontend({fetchImpl: (path) => (path === '/api/deployments' || path.startsWith('/api/deployments?')
      ? response({deployments: DEPS, open_positions: POSITIONS}) : response({}))});
    f.S.me.markets = MARKETS;
    f.location.pathname = '/deploy';
    await f.viewDeploy(); await settle();
    const html = f.root.querySelector('#openpos').innerHTML;
    noTicker(html, 'Deploy');
    // Every Market link's name, in order: no two alike for the five paper Solana windows (each ends in its settle time).
    const links = [...html.matchAll(/data-lab="Market"><a href="[^"]*">([\s\S]*?)<\/a>/g)].map((m) => m[1].replace(/<[^>]*>/g, ''));
    assert.equal(links.length, POSITIONS.length);
    const paperSol = links.slice(0, 5);
    assert.equal(new Set(paperSol).size, 5, paperSol.join(' | '));
    for (const l of paperSol) assert.match(l, /^Solana up or down in 15 minutes \(\d\d:\d\d [^)]+\)$/, l);
    // A demo copy of a paper window holds the same contract: on the other side, its side tells it apart where its time
    // doesn't; on the same side nothing of the contract does (its book and strategy do), so it reads as the paper one.
    assert.equal(links[5], links[0]);
    assert.match(links[6], /^Solana up or down in 15 minutes \(\d\d:\d\d [^,)]+, YES\)$/, links[6]);
    assert.match(links[1], /^Solana up or down in 15 minutes \(\d\d:\d\d [^,)]+, NO\)$/, links[1]);
    assert.equal(links[7], 'Bitcoin $110,000 or above at 5pm ET');
    assert.ok(links.includes('Kalshi contract'), 'a series no market covers reads as a contract, never its code');
    // The board's deployments have a heading of their own between the page's h1 and "Open positions".
    const page = f.root.innerHTML;
    const heads = [...page.matchAll(/<(h[12])[^>]*>([\s\S]*?)<\/\1>/g)].map((m) => `${m[1]} ${m[2].replace(/<[^>]*>/g, '')}`);
    const deps = heads.indexOf('h2 Your deployments');
    assert.ok(deps > heads.findIndex((h) => /^h1 Deploy/.test(h)) && deps < heads.indexOf('h2 Open positions'), heads.join(' | '));
    assert.match(page, /<h2 class="vh" id="deps-head">Your deployments<\/h2><div id="deps">/);
    if (f.clearTimers) f.clearTimers();
  });

  test(`${name}: the dashboard's positions never show a ticker, and alike names say their settle time to a screen reader`, async () => {
    const data = {...dashboard(), deployments: DEPS, open_positions: POSITIONS, recent_closed: CLOSED};
    const f = frontend({fetchImpl: (path) => {
      if (path === '/api/dashboard' || path.startsWith('/api/dashboard?')) return response(data);
      if (path === '/api/autopilot/status') return response({autopilot: false, mode: 'off'});
      return response({});
    }});
    f.S.me.markets = MARKETS;
    await f.viewDashboard(); await settle();
    const html = f.root.querySelector('#main').innerHTML;
    noTicker(html, 'dashboard');
    // The Solana paper windows' names (Tau's links, Meridian's row names) differ by a hidden settle time.
    const names = [...html.matchAll(/(?:<a href="\/explore\?m=KXSOL15M[^"]*">|<span class="position-market">)([\s\S]*?)<\/(?:a|span)><\/(?:span|summary)?/g)]
      .map((m) => m[1].replace(/<[^>]*>/g, ''));
    const timed = names.filter((n) => /^Solana up or down in 15 minutes \(/.test(n));
    assert.ok(timed.length >= 2, names.join(' | '));
    assert.match(html, /Solana up or down in 15 minutes<span class="vh"> \(\d\d:\d\d [^)]+\)<\/span>/);
    if (f.clearTimers) f.clearTimers();
  });

  test(`${name}: the strategy page's holdings and trades never show a ticker`, async () => {
    const holdings = POSITIONS.slice(0, 5).map(({deployment_id, title, ...p}) => p);
    const recent_trades = CLOSED.map((t) => ({opened: '2026-11-01T15:00Z', ticker: t.ticker, side: t.side, qty: 4, entry: 0.4, exit: t.exit_price, pnl: t.pnl, reason: 'settled'}));
    const s = strategyFrontend(detailStrategy({market: 'sol15m', holdings, recent_trades}));
    s.S.me.markets = MARKETS;
    await s.viewStrategy(42); await settle();
    const html = s.root.querySelector('#main').innerHTML;
    noTicker(html, 'strategy page');
    const links = [...s.root.querySelector('#holding-now').innerHTML.matchAll(/<a href="\/explore[^"]*">([\s\S]*?)<\/a>/g)].map((m) => m[1].replace(/<[^>]*>/g, ''));
    assert.equal(new Set(links).size, 5, links.join(' | '));
    if (s.clearTimers) s.clearTimers();
  });

  test(`${name}: Funds' paper history never shows a ticker, a cancelled game's line included`, async () => {
    const ledger = [
      {created_at: at(-HOUR), kind: 'settlement', memo: 'KXNBAGAME-26OCT29NYKBKN-NYK settled VOID at $0.50 a YES contract', amount: 5},
      {created_at: at(-2 * HOUR), kind: 'trade', memo: 'Sold 6 NO KXSOL15M-26NOV011400-00 @ 0.62 (take_profit)', amount: 3.72},
      {created_at: at(-3 * HOUR), kind: 'trade', memo: 'Bought 9 NO KXHIGHCHI-26OCT29-T50 @ 0.58', amount: -5.3},
      {created_at: at(-4 * HOUR), kind: 'trade', memo: 'Bought 3 pair KXBTCD-26OCT3117-T112999.99/KXBTCD-26OCT3117-T113999.99 (pair)', amount: -1.8},
    ];
    const f = frontend({fetchImpl: (path) => path === '/api/funds' ? response({...fundsData(), paper: {cash: 996, ledger}}) : response({})});
    f.S.me.markets = MARKETS;
    f.location.pathname = '/funds';
    await f.viewFunds(); await settle();
    const rows = f.root.querySelector('.t-ledger').innerHTML;
    noTicker(rows, 'Funds');
    assert.match(rows, /NBA: NYK vs BKN on Oct 29, NYK to win settled VOID/);
    if (f.clearTimers) f.clearTimers();
  });

  test(`${name}: a market page's heading and the line under it never show its ticker, and its price file is named in words`, async () => {
    const page = {ticker: 'KXSOL15M-26NOV011815-00', series: 'KXSOL15M', title: '', sub: '', status: 'active', yes_bid: '0.41', yes_ask: '0.44', last: '0.42',
      volume_24h: '10', open_interest: '20', close_time: at(HOUR), settles_at: at(HOUR), as_of: new Date().toISOString(), rules: 'Rules.', rules_more: '', period_min: 1,
      points: [{t: 1793012400, yes: .4, vol: 3}, {t: 1793012460, yes: .42, vol: 2}], mine: {open: [], closed: []}, my_markets: []};
    const f = frontend({fetchImpl: (path) => (path.startsWith('/api/explore/market?') ? response(page) : response({}))});
    f.S.me.markets = MARKETS;
    await f.viewMarketDetail(page.ticker); await settle();
    noTicker(f.root.querySelector('.board-title').innerHTML, 'market page heading');
    const src = readFileSync(new URL('../app/static/page-explore.js', import.meta.url), 'utf8');
    assert.doesNotMatch(src, /download = `\$\{d\.ticker\}/, 'the price file was named by the ticker');
    if (f.clearTimers) f.clearTimers();
  });

  test(`${name}: Billing offers "Skip to Add credits" ahead of the statement, which takes focus to the panel`, async () => {
    const f = billingPage(cardedBilling());
    await f.viewBilling(); await flush();
    const main = f.root.querySelector('#main').innerHTML;
    const skip = main.indexOf('<a class="skip-to" href="#add-credits">Skip to Add credits</a>');
    assert.ok(skip >= 0, 'the skip link');
    // Before every other control of the page's body: the cards, the auto-reload switch and the statement's receipt link.
    for (const later of ['id="save-card"', 'id="autoreload"', 'href="/api/billing/receipt"', 'id="topup-amt"']) assert.ok(main.indexOf(later) > skip, later);
    assert.match(main, /<aside id="add-credits" aria-label="Add credits">/);
    // Off screen until a keyboard reaches it, in both themes.
    for (const file of ['app.css', 'app.meridian.css']) {
      const css = readFileSync(new URL(`../app/static/${file}`, import.meta.url), 'utf8');
      assert.match(css, /\.skip-to:not\(:focus\) \{ position: absolute !important; width: 1px; height: 1px; overflow: hidden; clip: rect\(0 0 0 0\);/, file);
      assert.match(css, /\.skip-to:focus \{ display: inline-block;/, file);
    }
    if (f.clearTimers) f.clearTimers();
  });
}
