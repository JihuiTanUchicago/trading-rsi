// Shared cases, run against each bundle's offline harness (Tau and Meridian): weather and sports markets read as
// plainly as Bitcoin ones. Walked 2026-11-01 with research reopened on daily highs and sports, an account holding NYC,
// Chicago and Oklahoma City highs, an NFL game, an EPL draw and a postponed NBA game, both themes, phone and desktop:
// - two NYC positions both read "NYC daily high temperature", and a game "NFL game winner (moneyline)": no degrees,
//   no day, no teams, no side (a contract now reads its market's long name with them: "NYC daily high temperature on
//   Nov 1, 58° to 59°F", the name the strategy page's Market line says);
// - a city high the catalog adds had no short name, so chips clipped "HIGHEST TEMPERATURE IN CHICAG", and the research
//   picker and the Strategies filter listed two weather groups ("Climate and Weather" and "Weather");
// - Explore's game rows dropped the side a contract pays on ("Kansas City at Buffalo Winner?", sub "Kansas City");
// - a game Kalshi cancelled (settled "scalar" at 50¢) read "Kalshi lists it as settled but sent no Yes or No result".
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const written = (el) => (el.writes ? [el.html || ''] : []).concat(...el.children.map(written)).join('\n');
const MARKETS = [
  {key: 'btc15m', label: 'Bitcoin up or down in 15 minutes', series: 'KXBTC15M', category: 'Crypto', has_data: true},
  {key: 'high_ny', label: 'NYC daily high temperature', series: 'KXHIGHNY', category: 'Weather', has_data: true},
  {key: 'kxhighchi', label: 'Highest temperature in Chicago', series: 'KXHIGHCHI', category: 'Climate and Weather', has_data: true},
  {key: 'kxhightokc', label: 'Highest temperature in Oklahoma City', series: 'KXHIGHTOKC', category: 'Climate and Weather', has_data: true},
  {key: 'nfl_game', label: 'NFL game winner (moneyline)', series: 'KXNFLGAME', category: 'Sports', has_data: true},
  {key: 'epl_game', label: 'English Premier League game result', series: 'KXEPLGAME', category: 'Sports', has_data: true},
  {key: 'nba_game', label: 'NBA game winner (moneyline)', series: 'KXNBAGAME', category: 'Sports', has_data: true},
];
const text = (html) => html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
// The catalog's city highs as the server names them today (markets.Market.customer_label), the long name a position's
// contract begins with.
const NAMED = MARKETS.map((m) => (m.key === 'kxhighchi' ? {...m, label: 'Chicago daily high temperature'}
  : m.key === 'kxhightokc' ? {...m, label: 'Oklahoma City daily high temperature'} : m));

export function weatherSportsCases({name, frontend, detailStrategy, strategyFrontend}) {
  test(`${name}: a weather or game position says its city, day and degrees, or its teams, day and side, never only its market`, async () => {
    const close = new Date(Date.now() + 9 * 3600e3).toISOString();
    const hold = (ticker) => ({ticker, side: 'yes', qty: 4, entry_price: 0.31, cost: 1.3, unrealized: 0.1, mark_ts: new Date().toISOString(), mode: 'paper', market_close_time: close});
    const cases = [
      ['high_ny', 'KXHIGHNY-26NOV01-B58.5', 'NYC daily high temperature on Nov 1, 58° to 59°F'],
      ['high_ny', 'KXHIGHNY-26NOV02-T64', 'NYC daily high temperature on Nov 2, 64°F threshold'],
      ['kxhighchi', 'KXHIGHCHI-26NOV01-B48.5', 'Chicago daily high temperature on Nov 1, 48° to 49°F'],
      ['kxhightokc', 'KXHIGHTOKC-26NOV01-T71', 'Oklahoma City daily high temperature on Nov 1, 71°F threshold'],
      ['nfl_game', 'KXNFLGAME-26NOV01KCBUF-KC', 'NFL: Chiefs vs Bills on Nov 1, Chiefs to win'],
      ['nba_game', 'KXNBAGAME-26OCT30GSWMIA-MIA', 'NBA: Warriors vs Heat on Oct 30, Heat to win'],
      ['epl_game', 'KXEPLGAME-26NOV01ARSCHE-TIE', 'English Premier League: Arsenal vs Chelsea on Nov 1, a draw'],
      // A Bitcoin contract keeps its market's name.
      ['btc15m', 'KXBTC15M-26NOV012315-15', 'Bitcoin up or down in 15 minutes'],
    ];
    for (const [market, ticker, plain] of cases) {
      const f = strategyFrontend(detailStrategy({market, holdings: [hold(ticker)]}), () => response({}));
      f.S.me.markets = NAMED;
      await f.viewStrategy(42); await flush();
      const html = f.root.querySelector('#holding-now').innerHTML;
      assert.ok(html.includes(`<a href="/explore?m=${encodeURIComponent(ticker)}">${plain}</a>`), `${ticker}: ${text(html).slice(0, 160)}`);
      if (f.clearTimers) f.clearTimers();
    }
  });

  test(`${name}: the research picker lists every daily high under one Weather group, each by a short city name`, () => {
    const f = frontend();
    f.S.me.markets = MARKETS;
    const html = f.marketPicker(MARKETS);
    const groups = [...html.matchAll(/<\/span>([^<]+)<span class="mkt-count">(\d+)<\/span>/g)].map((m) => [m[1], +m[2]]);
    assert.deepEqual(groups, [['Crypto', 1], ['Sports', 3], ['Weather', 3]]);
    const names = [...html.matchAll(/<span class="mktrow-name">([^<]+)/g)].map((m) => m[1].trim());
    for (const n of ['NYC high temp', 'Chicago high temp', 'Oklahoma City high temp', 'NFL winner', 'Premier League result']) assert.ok(names.includes(n), `${n} in ${names}`);
    assert.doesNotMatch(html, /Climate and Weather|Highest temperature in/);
  });

  // The server names a catalog city's high as the NYC high's long name reads ("Chicago daily high temperature",
  // markets.Market.customer_label) since walk 2026-11-01; its short name is the same as from Kalshi's title.
  test(`${name}: a catalog city high sent by its long name keeps its short city name in chips and the picker`, () => {
    const f = frontend();
    const named = NAMED;
    f.S.me.markets = named;
    const names = [...f.marketPicker(named).matchAll(/<span class="mktrow-name">([^<]+)/g)].map((m) => m[1].trim());
    for (const n of ['NYC high temp', 'Chicago high temp', 'Oklahoma City high temp']) assert.ok(names.includes(n), `${n} in ${names}`);
    assert.ok(!names.some((n) => /daily high temperature/.test(n) && n !== 'NYC daily high temperature'), names.join(' | '));
  });

  test(`${name}: the Strategies market filter lists the daily highs in one Weather group by short names`, async () => {
    const f = frontend({fetchImpl: (path) => (path.startsWith('/api/strategies')
      ? response({counts: {total: 1, passed: 1}, matched: 1, pages: 1, strategies: [detailStrategy({id: 70, title: 'Chicago gap', status: 'passed', market: 'kxhighchi'})]}) : response({}))});
    f.S.me.markets = MARKETS;
    f.location.pathname = '/strategies'; f.location.search = '?status=';
    const drawing = f.viewStrategies();
    for (let i = 0; i < 4; i++) { await settle(); f.elapse(2000); }
    await drawing; await settle();
    const sel = (written(f.document.body).match(/<select class="input strat-mkt" id="strat-market"[\s\S]*?<\/select>/) || [''])[0];
    assert.match(sel, /<optgroup label="Weather"><option value="high_ny">NYC high temp<\/option><option value="kxhighchi">Chicago high temp<\/option><option value="kxhightokc">Oklahoma City high temp<\/option><\/optgroup>/);
    assert.doesNotMatch(sel, /Climate and Weather|Highest temperature in/);
    f.clearTimers();
  });

  test(`${name}: an Explore game row and its page keep the side its contract pays on`, async () => {
    const row = (t, ti, su) => ({t, s: t.split('-')[0], ev: t, ti, su, last: '0.5', bid: '0.49', ask: '0.51', v24: '10', oi: '5', st: 'active', ct: null});
    const f = frontend({fetchImpl: (path) => (path.startsWith('/api/explore?')
      ? response({at: '2026-11-01T02:00:00Z', total: 3, shown: 3, matched: 3, page: 0, per: 40, sort: 'v24', markets: [
        row('KXNFLGAME-26NOV01KCBUF-KC', 'Kansas City at Buffalo Winner?', 'Kansas City'),
        row('KXEPLGAME-26NOV01ARSCHE-ARS', 'Arsenal vs Chelsea Winner?', 'Arsenal'),
        row('KXOTHER-A', 'Minnesota wins', 'Minnesota')]}) : response({}))});
    f.S.me.markets = MARKETS;
    await f.viewExplore(); await settle();
    // Tau joins a row's title and side on one line; Meridian sets the side on its own line under the title.
    const rows = text(f.root.querySelector('#xrows').innerHTML);
    assert.match(rows, /Kansas City at Buffalo Winner\? (?:· )?Kansas City /);
    assert.match(rows, /Arsenal vs Chelsea Winner\? (?:· )?Arsenal /);
    assert.doesNotMatch(rows, /Minnesota wins · Minnesota/, 'a title that already says its side says it once');
    if (f.clearTimers) f.clearTimers();
    // The contract's own page is headed in Tau's words, the ones its positions use, and the line under it doesn't name it
    // again: it was headed "Kansas City at Buffalo Winner? · Kansas City" over "NFL: Chiefs vs Bills on Nov 1, Chiefs to
    // win" (regression walk 2026-11-01). Its price says Yes in the same team names.
    const page = {ticker: 'KXNFLGAME-26NOV01KCBUF-KC', series: 'KXNFLGAME', title: 'Kansas City at Buffalo Winner?', sub: 'Kansas City', status: 'active',
      yes_bid: '0.50', yes_ask: '0.52', last: '0.51', volume_24h: '10', open_interest: '5', close_time: '2026-11-15T21:25:00Z', settles_at: '2026-11-02T00:30:00Z',
      as_of: '2026-11-01T02:00:00Z', rules: 'If Kansas City wins, Yes.', rules_more: '', period_min: 60, points: [], mine: {open: [], closed: []}, my_markets: []};
    const head = async (over) => {
      const p = {...page, ...over};
      const g = frontend({fetchImpl: (path) => (path.startsWith('/api/explore/market?') ? response(p) : response({}))});
      g.S.me.markets = MARKETS.concat([{key: 'nhl_game', label: 'NHL game winner (moneyline)', series: 'KXNHLGAME', category: 'Sports', has_data: true}]);
      await g.viewMarketDetail(p.ticker); await flush();
      const out ={h1: g.root.querySelector('h1').innerHTML, deck: g.root.querySelector('.deck').innerHTML, body: text(g.root.innerHTML)};
      if (g.clearTimers) g.clearTimers();
      return out;
    };
    const nfl = await head({});
    assert.equal(nfl.h1, 'NFL: Chiefs vs Bills on Nov 1, Chiefs to win');
    assert.doesNotMatch(nfl.deck, /Chiefs|Kansas City|NFL/);
    assert.match(nfl.body, /≈ 51% chance of Yes \(Chiefs win\)/);
    assert.match(nfl.body, /If Kansas City wins, Yes\./, 'Kalshi’s own rules are quoted as Kalshi wrote them');
    // NBA, NHL and the Premier League alike, a draw included.
    assert.equal((await head({ticker: 'KXNBAGAME-26NOV01LALBOS-BOS', series: 'KXNBAGAME', title: 'Los Angeles L at Boston Winner?', sub: 'Boston'})).h1,
      'NBA: Lakers vs Celtics on Nov 1, Celtics to win');
    const nhl = await head({ticker: 'KXNHLGAME-26NOV01TORBOS-TOR', series: 'KXNHLGAME', title: 'Toronto at Boston Winner?', sub: 'Toronto'});
    assert.equal(nhl.h1, 'NHL: Maple Leafs vs Bruins on Nov 1, Maple Leafs to win');
    assert.match(nhl.body, /chance of Yes \(Maple Leafs win\)/);
    const epl = await head({ticker: 'KXEPLGAME-26NOV01ARSCHE-TIE', series: 'KXEPLGAME', title: 'Arsenal vs Chelsea Winner?', sub: 'Tie'});
    assert.equal(epl.h1, 'English Premier League: Arsenal vs Chelsea on Nov 1, a draw');
    assert.match(epl.body, /chance of Yes \(the game ends in a draw\)/);
    // A team Tau doesn't name keeps Kalshi's title and side, and says Yes in them.
    const odd = await head({ticker: 'KXNBAGAME-26NOV01LALXYZ-LAL', series: 'KXNBAGAME', title: 'Los Angeles L at Nowhere Winner?', sub: 'Los Angeles L'});
    assert.match(odd.h1, /^Los Angeles L at Nowhere Winner\? · Los Angeles L$/);
    assert.match(odd.body, /chance of Yes \(Los Angeles L wins\)/);
  });

  test(`${name}: a game Kalshi cancelled says so and what each contract paid, never "no result sent" or "scalar"`, async () => {
    const page = {ticker: 'KXNBAGAME-26OCT30GSWMIA-GSW', series: 'KXNBAGAME', title: 'Golden State at Miami Winner?', sub: 'Golden State', status: 'finalized',
      result: 'scalar', settle_value: 0.5, yes_bid: '0', yes_ask: '0', last: '0.36', volume_24h: '5234', open_interest: '12011',
      close_time: '2026-10-31T03:00:00Z', settles_at: '2026-10-31T03:00:00Z', as_of: '2026-11-01T02:00:00Z', rules: 'If Golden State wins, Yes.', rules_more: '',
      period_min: 60, points: [{t: 1793012400, yes: .4, vol: 3}, {t: 1793016000, yes: .36, vol: 2}], mine: {open: [], closed: []}, my_markets: []};
    const f = frontend({fetchImpl: (path) => (path.startsWith('/api/explore/market?') ? response(page) : response({}))});
    f.S.me.markets = MARKETS;
    await f.viewMarketDetail(page.ticker); await flush();
    const html = f.root.innerHTML;
    assert.match(html, /Result<\/dt><dd><span class="fig ">Cancelled<\/span><\/dd><dd class="sub-fig">Kalshi cancelled this market and paid 50¢ a contract: no Yes or No result/);
    assert.match(html, /<h2>How it settles<\/h2><p>Cancelled by Kalshi · paid 50¢ a contract<\/p>/);
    assert.equal(f.root.querySelector('h1').innerHTML, 'NBA: Warriors vs Heat on Oct 30, Warriors to win');
    assert.equal(f.root.querySelector('.deck').innerHTML, 'Cancelled by Kalshi');
    assert.doesNotMatch(text(html), /scalar|no result sent|sent no Yes or No|chance of Yes/i);
    if (f.clearTimers) f.clearTimers();
  });
}
