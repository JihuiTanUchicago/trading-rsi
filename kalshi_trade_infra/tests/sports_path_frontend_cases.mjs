// Shared cases, run against each bundle's offline harness (Tau and Meridian): the sports path read true end to end.
// Walked 2026-11-01 (research reopened on sports) with an account holding NBA, NFL and Premier League games on paper and
// demo, a postponed NBA game Kalshi settled at 50¢, and Explore stubbed from the server's own payloads, both themes, 390
// and 1366, a reader in New York on the day of the clock change:
// - a game tonight's market page read "Closes Nov 14, 22:30 EST", Kalshi's backstop close, over a result due tonight;
// - its price said "≈ 41% chance of Yes" and never what Yes was for the game;
// - the postponed game's positions read "Closed (settled) @ 50¢", like a result;
// - off-hours, Explore ("Crypto and sports markets …") listed only Bitcoin and Ethereum and dropped Sports with no word;
// - Research from a game's page offered a Bitcoin example idea ("… before the 5pm close").
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const MARKETS = [
  {key: 'btc15m', label: 'Bitcoin up or down in 15 minutes', series: 'KXBTC15M', category: 'Crypto', has_data: true},
  {key: 'nba_game', label: 'NBA game winner (moneyline)', series: 'KXNBAGAME', category: 'Sports', has_data: true},
  {key: 'epl_game', label: 'English Premier League game result', series: 'KXEPLGAME', category: 'Sports', has_data: true},
];
const text = (html) => html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
// Noon tomorrow on the reader's calendar, whatever the hour the test runs at.
const tomorrowNoon = () => { const n = new Date(Date.now()); return new Date(n.getFullYear(), n.getMonth(), n.getDate() + 1, 12); };
const game = (over = {}) => ({ticker: 'KXNBAGAME-26NOV01LALBOS-LAL', series: 'KXNBAGAME', title: 'Los Angeles L at Boston Winner?', sub: 'Los Angeles L',
  status: 'active', yes_bid: '0.4000', yes_ask: '0.4200', last: '0.4100', volume_24h: '182345.00', open_interest: '96120.00',
  close_time: new Date(tomorrowNoon().getTime() + 13 * 86400e3).toISOString(), settles_at: tomorrowNoon().toISOString(),
  as_of: new Date(Date.now()).toISOString(), rules: 'If Los Angeles L wins, Yes.', rules_more: '', result: '', settle_value: null,
  period_min: 60, points: [], mine: {open: [], closed: [], books: {}}, my_markets: [], ...over});

export function sportsPathCases({name, frontend}) {
  const marketPage = async (page) => {
    const f = frontend({fetchImpl: (path) => (path.startsWith('/api/explore/market?') ? response(page) : response({}))});
    f.S.me.markets = MARKETS;
    await f.viewMarketDetail(page.ticker); await flush();
    return f;
  };

  test(`${name}: a game's market page leads with when its result is due, not Kalshi's backstop close days out`, async () => {
    const f = await marketPage(game());
    const html = f.root.innerHTML;
    assert.match(html, /<dt class="lab">Result expected<\/dt><dd><span class="fig "><span data-close="[^"]+" data-form="settleTime"[^>]*>Tomorrow, 12:00 [A-Z]/);
    assert.match(text(html), /Kalshi stops trading once the result is in, [A-Z][a-z]{2} \d+, 12:00 [A-Z+\d:]+ at the latest/);
    assert.doesNotMatch(html, /<dt class="lab">Closes<\/dt>/, 'no "Closes" figure at the backstop');
    if (f.clearTimers) f.clearTimers();
    // A Bitcoin close, its result minutes after, keeps its Closes figure.
    const close = new Date(Date.now() + 5 * 3600e3).toISOString(), after = new Date(Date.now() + 5 * 3600e3 + 300e3).toISOString();
    const g = await marketPage(game({ticker: 'KXBTCD-26NOV0117-T112999.99', series: 'KXBTCD', title: 'Bitcoin price on Nov 1, 2026?', sub: '$113,000 or above',
      close_time: close, settles_at: after}));
    assert.match(g.root.innerHTML, /<dt class="lab"><span data-close="[^"]+" data-form="lab"[^>]*>Closes<\/span><\/dt>/);
    assert.doesNotMatch(g.root.innerHTML, /Result expected<\/dt>/);
    if (g.clearTimers) g.clearTimers();
  });

  test(`${name}: a game's price says what Yes is for it: that side winning, or a draw`, async () => {
    const f = await marketPage(game());
    assert.match(text(f.root.innerHTML), /≈ 41% chance of Yes \(Lakers win\) · bid/);
    if (f.clearTimers) f.clearTimers();
    const g = await marketPage(game({ticker: 'KXEPLGAME-26NOV01ARSCHE-TIE', series: 'KXEPLGAME', title: 'Arsenal vs Chelsea Winner?', sub: 'Tie'}));
    assert.match(text(g.root.innerHTML), /chance of Yes \(the game ends in a draw\)/);
    if (g.clearTimers) g.clearTimers();
    // A market that isn't a game's winner keeps "chance of Yes" alone.
    const h = await marketPage(game({ticker: 'KXNCAAFSPREAD-26NOV07OSUPSU-OSU14', series: 'KXNCAAFSPREAD', title: 'Ohio St. at Penn St.: Spread', sub: 'Ohio St. wins by over 13.5 Points'}));
    assert.match(text(h.root.innerHTML), /≈ 41% chance of Yes · bid/);
    if (h.clearTimers) h.clearTimers();
  });

  test(`${name}: a position in a game Kalshi cancelled reads as cancelled and paid at Kalshi's price, never "settled"`, async () => {
    const closed = (mode, entry, pnl) => ({id: 8, mode, side: 'yes', qty: 10, entry_price: entry, status: 'closed', title: 'NBA road dogs', strategy_id: 3,
      market_close_time: '2026-11-01T06:30:00Z', closed_at: '2026-11-01T06:30:00Z', exit_price: '0.5000', pnl, close_reason: 'settled', cost: 3.77});
    const f = await marketPage(game({ticker: 'KXNBAGAME-26OCT31NYKCHI-CHI', title: 'New York at Chicago Winner?', sub: 'Chicago', status: 'finalized',
      result: 'scalar', settle_value: 0.5, close_time: '2026-11-01T06:30:00Z', settles_at: '2026-11-01T03:00:00Z',
      mine: {open: [], closed: [closed('paper', '0.3600', '1.2300'), closed('demo', '0.3700', '1.1300')], books: {}}}));
    const html = f.root.innerHTML;
    assert.equal((html.match(/<td data-lab="Status">Cancelled by Kalshi, paid 50¢ a contract<\/td>/g) || []).length, 2);
    assert.doesNotMatch(html, /Closed \(settled\)/);
    // Each book on its own: paper's +$1.23 and demo's +$1.13, never added.
    assert.match(text(html), /1 closed · realized \+⁠?\$1\.13/);
    assert.match(text(html), /1 closed · realized \+⁠?\$1\.23/);
    assert.doesNotMatch(text(html), /\$2\.36/);
    if (f.clearTimers) f.clearTimers();
  });

  test(`${name}: off-hours, Explore says in words that no sports market is in the list, and says nothing once one is`, async () => {
    const row = {t: 'KXBTCD-26NOV0117-T112999.99', s: 'KXBTCD', ev: 'KXBTCD-26NOV0117', ti: 'Bitcoin price on Nov 1, 2026?', su: '$113,000 or above',
      last: '0.4', bid: '0.39', ask: '0.41', v24: '250000', oi: '120000', st: 'active', ct: null};
    const list = (groups) => frontend({fetchImpl: (path) => (path.startsWith('/api/explore?')
      ? response({at: new Date(Date.now()).toISOString(), total: 412, shown: 1, matched: 1, page: 0, per: 40, sort: 'v24', show: 'all', groups, markets: [row]}) : response({}))});
    const f = list([]);
    f.S.me.markets = MARKETS;
    await f.viewExplore(); await settle();
    assert.match(f.root.querySelector('#xnote').innerHTML, /<p class="hint xnone-sports">No sports markets are open in this list right now\. Games show here once Kalshi opens them/);
    if (f.clearTimers) f.clearTimers();
    const g = list(['sports']);
    g.S.me.markets = MARKETS;
    await g.viewExplore(); await settle();
    assert.doesNotMatch(g.root.querySelector('#xnote').innerHTML, /No sports markets/);
    if (g.clearTimers) g.clearTimers();
  });

  test(`${name}: Research from a game's page names its market and offers a sports example, not a Bitcoin one`, async () => {
    const f = frontend({fetchImpl: (path) => {
      if (path === '/api/models') return response({models: [{key: 'opus', label: 'Claude Opus 4.6'}], default: 'opus'});
      if (path.startsWith('/api/runs')) return response({runs: [], counts: {research_running: 0, research_queued: 0}, policy: null, v: 'v1'});
      if (path === '/api/autopilot/status') return response({autopilot: false, mode: 'off'});
      return response({markets: []});
    }});
    f.S.me = {...f.S.me, markets: MARKETS};
    f.S.me.user.autopilot = false;
    f.location.pathname = '/research'; f.location.search = '?new=1&market=nba_game';
    await f.viewResearch(); await settle();
    const ta = f.root.querySelector('textarea[name=prompt]');
    assert.match(ta.getAttribute('placeholder'), /home team .* before the game starts/);
    assert.doesNotMatch(ta.getAttribute('placeholder'), /5pm|tails/);
    // The harness has no radio groups: the picked market is the checked row that isn't Agent's choice.
    assert.equal((f.root.querySelectorAll('input[name=market]').find((r) => r.checked && r.value) || {}).value, 'nba_game');
    assert.equal(f.root.querySelector('#runforever').checked, true, 'the run-forever default stays');
    if (f.clearTimers) f.clearTimers();
  });
}
