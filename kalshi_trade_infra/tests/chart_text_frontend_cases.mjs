// Shared cases, run against each bundle's offline DOM/transport harness (Tau and Meridian): charts outside the track
// record at a reader's 200% text. Walked 2026-11-01 at 390x844 and 1366x900, 100% and 200% text, on a seeded account:
// - a strategy's equity chart ran its axis labels off its left edge ("−$0.59" read "0.59"; "$1,584.53" read "584.53")
//   and its "Later period" label off its right edge or over the top tick;
// - the market page's price chart lost the "1" of "100¢" and the top of the label;
// - a strategy chart between −$0.09 and +$0.04 put "−$0.02" on the break-even line and never said $0.
// The track record's charts (equityChart without `fit`, rpUnderwater without `words`) are drawn exactly as before.
// `cw`: the units a character of the bundle's cut label is measured at (Tau's capitals 7, Meridian's 6).
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 8; i++) await flush(); };
// Each y-axis tick: [its right edge, its words]; the zero label (class chart-zero) and the cut label (styled) are left out.
const ticks = (svg) => [...svg.matchAll(/<text x="([\d.]+)" y="[^"]*" text-anchor="end">([^<]*)<\/text>/g)].map((m) => [+m[1], m[2].replace(/⁠/g, '')])
  .filter((t) => /^[−$]/.test(t[1]));  // not the last date

export function chartTextCases({name, frontend, Element, cw}) {
  // A reader at 200% text: every chart label is drawn at twice the theme's size (`px`), as the page's CSS would set it.
  const atText = (px, opts = {}) => {
    const f = frontend({fetchImpl: () => response({}), ...opts, globals: {getComputedStyle: (el) => ({fontSize: el.tag === 'text' ? px + 'px' : '32px'})}});
    f.document.createElementNS = (_ns, tag) => new Element(tag);
    return f;
  };
  const curve = [[1761000000, 0], [1761400000, -420.15], [1762500000, 640.25], [1763600000, 1584.53]];
  const cut = new Date(1762500000e3).toISOString();
  const later = ['LATER PERIOD · NOT HELD OUT →', 'LATER · NOT HELD OUT →', 'LATER →'];

  test(`${name}: at 200% text a strategy's equity chart makes room for its axis labels and its cut label, compact over $100`, () => {
    const opts = {width: 328, fit: true, words: true, cutLabel: later};
    const at100 = frontend({fetchImpl: () => response({})}).equityChart(curve, cut, null, opts);
    const at200 = atText(22).equityChart(curve, cut, null, opts);
    // At the theme's size nothing moves: every figure to the cent, in a gutter fitted to them.
    assert.deepEqual(ticks(at100), [[63, '−$420.15'], [63, '$582.19'], [63, '$1,584.53']]);
    // At 200% the ticks read compact and the gutter fits each at 13 units a character (6.5 at 11px, twice).
    const big = ticks(at200);
    assert.deepEqual(big.map((t) => t[1]), ['−$420', '$582', '$1.6k']);
    for (const [x, words] of big) assert.ok(x >= 13 * words.length, `${words} needs ${13 * words.length} units left of ${x}`);
    // The cut label stays inside the plot: right of the y-axis labels and left of the chart's right edge.
    const L = big[0][0] + 8, W = 328, R = 10;
    const m = at200.match(/<text x="([\d.]+)" y="20"( text-anchor="end")? style="fill:var\(--ink-2\)">([^<]*)<\/text>/);
    assert.ok(m, at200);
    const x = +m[1], wide = 2 * cw * m[3].length;
    assert.ok(m[2] ? x - wide >= L : x + wide <= W - R, `"${m[3]}" at ${x} (${wide} wide) runs past ${L}..${W - R}`);
    // Its name still says every figure to the cent.
    assert.match((at200.match(/aria-label="([^"]*)"/) || [])[1], /ends at \+\$1,584\.53; low −\$420\.15 \([^)]+\), high \+\$1,584\.53/);
  });

  test(`${name}: the track record's equity chart and drawdown are drawn byte for byte as before at 200% text`, () => {
    const plain = frontend({fetchImpl: () => response({})}), big = atText(22);
    for (const width of [328, 760]) {
      assert.equal(big.equityChart(curve, null, null, {width}), plain.equityChart(curve, null, null, {width}));
      assert.equal(big.rpUnderwater(curve, null), plain.rpUnderwater(curve, null));
    }
  });

  test(`${name}: a strategy chart's middle tick that would sit on break-even reads $0, and zero is marked`, () => {
    const f = frontend({fetchImpl: () => response({})}), tiny = [[100, 0.01], [200, -0.09], [300, 0.04]];
    const fit = f.equityChart(tiny, null, null, {width: 760, fit: true, words: true});
    assert.deepEqual(ticks(fit).map((t) => t[1]), ['−$0.59', '$0.00', '$0.54']);
    // The track record's (no `fit`) keeps its own middle tick and its own zero rule.
    assert.equal(ticks(f.equityChart(tiny, null, null, {width: 760})).length, 3);
    assert.notEqual(ticks(f.equityChart(tiny, null, null, {width: 760}))[1][1], '$0.00');
  });

  test(`${name}: at 200% text the market's price chart fits "100¢" inside its gutter and its top`, async () => {
    const page = {ticker: 'KXBTCD-26OCT2617-T113499.99', series: 'KXBTCD', title: 'Bitcoin price at 5pm EDT?', sub: '$113,500 or above',
      status: 'active', yes_bid: '0.55', yes_ask: '0.57', last: '0.55', volume_24h: '15234', open_interest: '40211',
      close_time: '2026-10-26T21:00:00Z', as_of: '2026-10-26T11:00:00Z', rules: 'Resolves Yes above 113499.99.', period_min: 1,
      points: Array.from({length: 30}, (_, i) => ({t: 1793012400 + 60 * i, yes: .5 + i / 100, vol: 1})), mine: {open: [], closed: []}};
    const open = async (f) => {
      f.S.me.markets = [];
      f.location.pathname = '/explore'; f.location.search = '?m=' + encodeURIComponent(page.ticker);
      await f.viewExplore(); await settle();
      return f.root.querySelector('.pchart-wrap').innerHTML;
    };
    const fetchImpl = (path) => (path.startsWith('/api/explore/market?') ? response(page) : response({}));
    const top = (svg) => svg.match(/<text x="([\d.]+)" y="([\d.]+)" class="pc-ylab">100¢<\/text>/);
    const at100 = top(await open(frontend({fetchImpl}))), at200 = top(await open(atText(21, {fetchImpl})));
    assert.deepEqual([+at100[1], +at100[2]], [36, 17], 'at the theme’s size it is drawn as before');
    assert.ok(+at200[1] >= 4 * 7 * 2, `"100¢" needs about 56 units left of ${at200[1]}`);
    assert.ok(+at200[2] >= 21 + 3, `its top needs room above ${at200[2]}`);
  });
}
