// Shared cases, run against each bundle's offline harness (Tau and Meridian): every market reads by one long name
// and one short name, both from the shared core (core.js mktLong, mktShort). Walked 2026-10-31 with research
// sessions, feed lines, deployments, positions and bell notices on six markets: one market read "BTC 15-min
// up/down", "Bitcoin 15-minute" and "Bitcoin up or down in 15 minutes"; a perpetual strategy's page said "BTC
// perpetual, no expiry" over a Market line "Unlisted market"; the last move said "on BTC 15-min up/down" in a
// sentence the server's own lines say with the long name.
// - The long name is the catalog's label (me.markets) or, for a market the list leaves out, me.market_names. Every
//   sentence, heading, deck, meta line, button and contract says it.
// - The short name is core.js MKT_SHORT (a catalog city's "<City> high temp"), only where a row has no room: chips, the
//   market filter and picker, and a list's Market column.
// Bell walk 2026-11-01: the research_done notice said "Bitcoin above a set price at 5pm ET" while the session page it
// opens was headed "Session · BTC above set price at 5pm ET".
import assert from './assert_text.mjs';
import {test} from 'node:test';
import fs from 'node:fs';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const core = fs.readFileSync(new URL('../app/static/core.js', import.meta.url), 'utf8');
const block = core.slice(core.indexOf('const MKT_SHORT = {'));
const SHORT = Object.fromEntries([...block.slice(0, block.indexOf('};')).matchAll(/(\w+): '([^']*)'/g)].map((m) => [m[1], m[2]]));
// The long names as the server sends them (customer_label).
const LONG = {
  btc15m: 'Bitcoin up or down in 15 minutes', btc_daily: 'Bitcoin above a set price at 5pm ET',
  high_ny: 'NYC daily high temperature', nba_game: 'NBA game winner (moneyline)',
  btc_perp: 'Bitcoin perpetual future, no expiry (research only)',
};
// One market of each kind more, for the guard below: a catalog city's high (no MKT_SHORT entry: its short name is
// "<City> high temp"), and two more leagues.
const MORE = {kxhighchi: 'Chicago daily high temperature', nfl_game: 'NFL game winner (moneyline)', epl_game: 'English Premier League game result'};
SHORT.kxhighchi = 'Chicago high temp';
const ME = {
  markets: [
    {key: 'btc15m', label: LONG.btc15m, series: 'KXBTC15M', category: 'Crypto', has_data: true},
    {key: 'btc_daily', label: LONG.btc_daily, series: 'KXBTCD', category: 'Crypto', has_data: true, close_hour_et: 17},
    {key: 'high_ny', label: LONG.high_ny, series: 'KXHIGHNY', category: 'Weather', has_data: true},
    {key: 'nba_game', label: LONG.nba_game, series: 'KXNBAGAME', category: 'Sports', has_data: true},
    {key: 'kxhighchi', label: MORE.kxhighchi, series: 'KXHIGHCHI', category: 'Climate and Weather', has_data: true},
    {key: 'nfl_game', label: MORE.nfl_game, series: 'KXNFLGAME', category: 'Sports', has_data: true},
    {key: 'epl_game', label: MORE.epl_game, series: 'KXEPLGAME', category: 'Sports', has_data: true},
  ],
  // The perpetual while perps are closed: not in the list, still named.
  market_names: {btc_perp: LONG.btc_perp},
};

export function marketNameCases({name, frontend, detailStrategy, strategyFrontend}) {
  const withMe = (f) => { f.S.me = {...f.S.me, ...ME}; return f; };

  test(`${name}: a strategy's page says its market's long name in its heading and on its Market line, a perpetual's too`, async () => {
    for (const key of Object.keys(LONG)) {
      const perp = key === 'btc_perp';
      const f = withMe(strategyFrontend(detailStrategy({market: key, status: 'passed', dataset: {start: '2026-09-01', end: '2026-10-17', out_of_sample_from: '2026-10-10'},
        ...(perp ? {kind: 'perpetual', research_only: true} : {})}), () => response({})));
      await f.viewStrategy(42); await flush();
      const html = f.root.querySelector('#main').innerHTML;
      assert.ok(html.includes('<dt>Market</dt>'), key + ': the Market line');
      const dd = html.slice(html.indexOf('<dt>Market</dt>')).match(/<dd>([\s\S]*?)<\/dd>/)[1].replace(/<[^>]*>/g, '').replace(' ↗', '').trim();
      assert.equal(dd, LONG[key], key);
      const head = f.root.querySelector('.strategy-pagehead') || f.root.querySelector('.board-title');
      assert.ok(head && (head.outer || head.innerHTML).includes(LONG[key]), key + ': the long name in the heading');
      assert.ok(!(head.outer || head.innerHTML).includes(SHORT[key]), key + ': never the short name in the heading');
      assert.doesNotMatch(html, /Unlisted market/, key);
      f.clearTimers();
    }
  });

  test(`${name}: the autopilot's last move and research line name a market by its long name, a key the list leaves out too`, () => {
    const f = withMe(frontend());
    for (const key of Object.keys(LONG)) {
      assert.equal(f.apMoveText({text: `Started a fresh research session on ${key} — same gates as any run.`, at: new Date().toISOString()}),
        `Started a fresh research session on ${LONG[key]}`, key);
    }
    f.S.ap = {autopilot: true, mode: 'paper', research: {active: ['btc_perp', LONG.btc15m, 'KXHIGHNY', 'kxgone']}};
    assert.equal(f.apIdleLine().replace(/\s/g, ' '), `Researching ${[LONG.btc_perp, LONG.btc15m, LONG.high_ny, 'an unlisted market'].join(' · ')}`);
  });

  test(`${name}: the research list names each session's market by its long name (its short one only in a Market column), never the key`, async () => {
    const runs = Object.keys(LONG).map((market, i) => ({id: i + 1, kind: 'research', market, status: 'succeeded', prompt: '',
      created_at: '2026-10-31T10:00:00Z', started_at: '2026-10-31T10:00:00Z', finished_at: '2026-10-31T10:20:00Z',
      hypotheses_tried: 2, strategies_passed: 0}));
    const f = withMe(frontend({fetchImpl: (path) => {
      if (path.startsWith('/api/runs')) return response({runs, total: runs.length, counts: {}});
      if (path.startsWith('/api/research/context')) return response({at: null, markets: []});
      if (path.startsWith('/api/research/scoreboard')) return response({});
      if (path === '/api/models') return response({models: [], default: ''});
      return response({});
    }}));
    f.location.pathname = '/research';
    await f.viewResearch(); await flush(); await flush();
    const html = f.root.querySelector('#runs').innerHTML, text = html.replace(/<[^>]*>/g, ' ');
    // Tau's card titles an untitled session "Session · <long name>"; Meridian's row leaves it to its Market column.
    const titles = [...html.matchAll(/<span class="run-title"[^>]*>([\s\S]*?)<\/span>/g)].map((m) => m[1].replace(/<[^>]*>/g, ''));
    for (const key of Object.keys(LONG)) {
      assert.ok(text.includes(LONG[key]) || text.includes(SHORT[key]), key);
      assert.ok(!titles.some((s) => s.includes(SHORT[key])), key + ': never the short name in a title');
      assert.doesNotMatch(text, new RegExp(`\\b${key}\\b`), key);
    }
    f.clearTimers();
  });

  // The guard: one market of each kind (crypto 15-minute and daily, daily highs in two cities, three leagues), and every
  // surface that names one. A sentence, heading, deck, button or contract says the long name and never the short one.
  test(`${name}: every surface that names a market says its long name, the bell's name, never its short one or its key`, async () => {
    const ALL = {...LONG, ...MORE};
    const SEP = ' · ';
    // The bundles' separator holds a no-break space (SEP); the words are what this checks.
    const plain = (s) => String(s).replace(/\u00a0/g, ' ').replace(/\u2060/g, '');
    const league = (long) => long.replace(/ game (?:winner|result)(?: \(moneyline\))?$/, '');
    const CONTRACT = {
      btc15m: ['KXBTC15M-26OCT312315-15', LONG.btc15m], btc_daily: ['KXBTCD-26NOV0117-T113999.99', 'Bitcoin $114,000 or above at 5pm ET'],
      high_ny: ['KXHIGHNY-26NOV01-B58.5', `${LONG.high_ny} on Nov 1, 58° to 59°F`],
      kxhighchi: ['KXHIGHCHI-26NOV01-T52', `${MORE.kxhighchi} on Nov 1, 52°F threshold`],
      nba_game: ['KXNBAGAME-26OCT30GSWMIA-MIA', `${league(LONG.nba_game)}: Warriors vs Heat on Oct 30, Heat to win`],
      nfl_game: ['KXNFLGAME-26NOV01KCBUF-KC', `${league(MORE.nfl_game)}: Chiefs vs Bills on Nov 1, Chiefs to win`],
      epl_game: ['KXEPLGAME-26NOV01ARSCHE-TIE', `${league(MORE.epl_game)}: Arsenal vs Chelsea on Nov 1, a draw`],
    };
    const noShort = (s, key, where) => assert.ok(!String(s).includes(SHORT[key]), `${key}: the short name "${SHORT[key]}" in ${where}: ${s}`);
    for (const key of Object.keys(ALL)) {
      // The session page: an untitled session is headed by its market; a titled one's deck names it.
      for (const prompt of ['', 'Idea']) {
        const f = withMe(frontend({fetchImpl: (path) => (path.startsWith('/api/runs/9')
          ? response({run: {id: 9, kind: 'research', prompt, market: key, status: 'succeeded', created_at: '2026-10-31T10:00:00Z',
            started_at: '2026-10-31T10:00:00Z', finished_at: '2026-10-31T10:20:00Z', hypotheses_tried: 1, strategies_passed: 0}, events: [], strategies: []})
          : response({}))}));
        await f.viewRun(9); await flush();
        const board = f.root.querySelector('#runboard'), h1 = plain(board.querySelector('h1').textContent), deck = plain((board.querySelector('.deck') || {textContent: ''}).textContent);
        if (!prompt) assert.equal(h1, 'Session' + SEP + ALL[key], key + ': the session page heading');
        else assert.ok(deck.startsWith(ALL[key]), `${key}: the session page deck: ${deck}`);
        noShort(board.textContent, key, 'the session page header');
        f.clearTimers();
      }
      // A strategy's page: its heading's deck and its Market line.
      const g = withMe(strategyFrontend(detailStrategy({market: key, status: 'passed', dataset: {start: '2026-09-01', end: '2026-10-17', out_of_sample_from: '2026-10-10'}}), () => response({})));
      await g.viewStrategy(42); await flush();
      const head = g.root.querySelector('.strategy-pagehead') || g.root.querySelector('.board-title');
      const headText = (head.outer || head.innerHTML).replace(/<[^>]*>/g, '');
      assert.ok(headText.includes(ALL[key]), `${key}: the strategy page heading: ${headText}`);
      noShort(headText, key, 'the strategy page heading');
      g.clearTimers();
      // The autopilot's focus, said as a list of markets.
      const h = withMe(frontend());
      assert.equal(h.apFocusText([key], true), ALL[key], key + ': the autopilot focus');
      // An untitled session's title, as Billing's usage rows and the research list say it.
      assert.equal(plain(h.sessionTitle({market: key, prompt: ''})), 'Session' + SEP + ALL[key], key + ': the session title');
      // A position's contract begins with its market's long name (a game's, its league), as a Bitcoin one does.
      if (CONTRACT[key]) {
        const [ticker, said] = CONTRACT[key];
        assert.equal(h.contractName(ticker, key), said, key + ': the contract');
        noShort(h.contractName(ticker, key), key, 'the contract');
      }
    }
  });
}
