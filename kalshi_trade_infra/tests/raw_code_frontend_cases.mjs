// Shared cases, run against each bundle's offline harness (Tau and Meridian): a customer never reads a raw code where
// a plain name belongs. Walked 2026-10-31 with open and closed paper and demo positions on BTC 15-minute, BTC daily,
// NYC weather and NBA markets, both themes, phone and desktop:
// - the strategy page's "Holding right now" put the raw contract code under each position's name ("Kalshi contract
//   KXNBAGAME-26OCT31MILPHI-PHI"), where the dashboard's position rows say when it settles;
// - a strategy the research loop saved before its verdict (status "pending") was stamped with the raw key, "pending".
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const ME_MARKETS = [
  {key: 'btc15m', label: 'Bitcoin up or down in 15 minutes', series: 'KXBTC15M', category: 'Crypto', has_data: true},
  {key: 'btc_daily', label: 'Bitcoin above a set price at 5pm ET', series: 'KXBTCD', category: 'Crypto', has_data: true, close_hour_et: 17},
  {key: 'high_ny', label: 'NYC daily high temperature', series: 'KXHIGHNY', category: 'Weather', has_data: true},
  {key: 'nba_game', label: 'NBA game winner (moneyline)', series: 'KXNBAGAME', category: 'Sports', has_data: true},
];
// Text a reader sees: tags and attributes gone.
const words = (html) => html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');

export function rawCodeCases({name, detailStrategy, strategyFrontend}) {
  test(`${name}: a strategy page's open position reads by its market's plain name and when it settles, never its raw contract code`, async () => {
    const close = new Date(Date.now() + 3 * 3600e3).toISOString();
    const hold = (market, ticker, mode) => ({ticker, side: 'yes', qty: 4, entry_price: 0.57, cost: 2.35, unrealized: -0.15, mark_ts: new Date().toISOString(),
      mode, market_close_time: close});
    const cases = [
      ['nba_game', 'KXNBAGAME-26OCT31MILPHI-PHI', 'NBA game winner (moneyline)'],
      ['high_ny', 'KXHIGHNY-26OCT31-B58.5', 'NYC daily high temperature'],
      ['btc_daily', 'KXBTCD-26NOV0117-T113999.99', 'Bitcoin $114,000 or above at 5pm ET'],
      ['btc15m', 'KXBTC15M-26OCT312315-15', 'Bitcoin up or down in 15 minutes'],
    ];
    for (const [market, ticker, plain] of cases) {
      const f = strategyFrontend(detailStrategy({market, holdings: [hold(market, ticker, 'demo'), hold(market, ticker, 'paper')]}), () => response({}));
      f.S.me.markets = ME_MARKETS;
      await f.viewStrategy(42); await flush();
      const panel = f.root.querySelector('#holding-now');
      assert.ok(panel, market + ': the holdings panel');
      const html = panel.innerHTML;
      // The name opens its market page (where the code sits beside its Kalshi link), by its plain name.
      assert.ok(html.includes(`<a href="/explore?m=${encodeURIComponent(ticker)}">${plain}</a>`), market + ': the plain name, linked');
      // Under the name, when it settles, as the dashboard's rows say it; the code is nowhere a reader sees.
      for (const li of panel.querySelectorAll('.holdlist > li')) {
        const under = li.innerHTML.split('</div>')[1] || '';
        assert.match(under, /<div class="sub-fig"><span data-close="[^"]+" data-form="settles" data-cap="1">Settles /, market + ': settles under the name');
      }
      assert.doesNotMatch(words(html), /KX[A-Z0-9]{2,}/, market + ': no raw code in the text');
      assert.doesNotMatch(html, /kalshi-ref/, market);
      assert.equal(panel.querySelectorAll('[data-form="settles"]').length, 2, market + ': one settle time per holding');
      if (f.clearTimers) f.clearTimers();
    }
  });

  test(`${name}: a strategy saved before its verdict is stamped in words, never the raw status key "pending"`, async () => {
    const f = strategyFrontend(detailStrategy({status: 'pending'}), () => response({}));
    await f.viewStrategy(42); await flush();
    // Tau stamps it in the paper's byline, Meridian in the page head.
    const html = f.root.querySelector('.board-title').outerHTML + f.root.querySelector('#main').innerHTML;
    assert.match(html, /<span class="mk quiet">Not judged<\/span>/);
    assert.doesNotMatch(html, /<span class="mk[^"]*">pending<\/span>/);
    if (f.clearTimers) f.clearTimers();
  });
}
