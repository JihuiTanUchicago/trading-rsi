// Shared cases, run against each bundle's offline harness (Tau and Meridian). From the trust walk of 2026-11-01:
// - every "Auto-reload couldn't add…" bell notice opened the newest failure's banner on Billing, whichever was pressed:
//   one opened from an older notice (?notice=<id>) shows that failure first, where the bell's link lands, and what came
//   after it, with the latest failure's banner under it; Billing already open lands on the next one pressed too;
// - the strategy page's machine spec showed the stored spec, so a market read "market": "kxhighchi": it shows the
//   server's spec_shown (markets by name, no internal id);
// - its later-period trades listed the backtest's latest 12 with no word of how many the period had.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };

export function listTotalsCases({name, frontend, detailStrategy, billingPage, cardedBilling}) {
  const latest = {at: '2026-10-31T20:08:00Z', notice: 304, text: 'It couldn’t add $1,000.00 in credits. Your bank wants to approve this charge itself.'};
  const older = (o = {}) => ({at: '2026-10-20T22:08:00Z', text: 'It couldn’t add $25.00 in credits. The charge couldn’t be completed just now.',
    ok_at: '2026-10-24T22:08:00Z', next_at: '2026-10-27T22:08:00Z', ...o});

  test(`${name}: Billing opened from an older failed auto-reload's notice shows that failure where the link lands, then the latest`, async () => {
    const posts = [], f = billingPage(cardedBilling({reload_failed: latest, reload_notice: older()}), posts);
    f.location.search = '?notice=263';
    await f.viewBilling(); await flush();
    assert.ok(f.requests.some(({path}) => /^\/api\/billing\?tz=-?\d+&notice=263$/.test(path)), 'the read names the notice');
    const spot = f.root.querySelector('#autorel-fail');
    assert.match(spot.getAttribute('class'), /\bautorel-was\b/);
    assert.match(spot.innerHTML, /<b>This auto-reload failed<\/b> <time datetime="2026-10-20T22:08:00Z" data-day="at">/);
    assert.match(spot.innerHTML, /It couldn’t add \$25\.00 in credits\. The charge couldn’t be completed just now\./);
    assert.doesNotMatch(spot.innerHTML, /1,000/);
    assert.match(spot.innerHTML, /An auto-reload went through after it, <time datetime="2026-10-24T22:08:00Z" data-day="at">/);
    const now = f.root.querySelector('#autorel-fail-now');
    assert.ok(now, 'the latest failure still has its banner');
    assert.match(now.innerHTML, /<b>Latest auto-reload failed<\/b>[\s\S]*\$1,000\.00/);
    assert.ok(now.querySelector('#autorel-fix'), 'Update card is on the latest one');
    // Auto-reload switched off hides the latest's banner (it is about auto-reload now), never the past failure.
    const sw = f.root.querySelector('#autoreload'); sw.checked = false; await sw.onchange(); await flush();
    assert.equal(now.hidden, true); assert.equal(spot.hidden, false);
    // Failed again before any went through: it says so, and that the latest is below.
    const g = billingPage(cardedBilling({reload_failed: latest, reload_notice: older({ok_at: null})}));
    g.location.search = '?notice=263';
    await g.viewBilling(); await flush();
    assert.match(g.root.querySelector('#autorel-fail').innerHTML, /Auto-reload failed again after it, <time datetime="2026-10-27T22:08:00Z" data-day="at">[^<]*<\/time>; the latest failure is below\./);
    // The latest notice itself (the server sends no reload_notice): the banner as ever, under the link's id.
    const h = billingPage(cardedBilling({reload_failed: latest}));
    h.location.search = '?notice=304';
    await h.viewBilling(); await flush();
    assert.match(h.root.querySelector('#autorel-fail').innerHTML, /<b>Auto-reload failed<\/b>[\s\S]*\$1,000\.00/);
    assert.equal(h.root.querySelector('#autorel-fail-now'), null);
    // No notice named, or one that isn't a number: the read is as before.
    const k = billingPage(cardedBilling({reload_failed: latest}));
    k.location.search = '?notice=abc';
    await k.viewBilling(); await flush();
    assert.ok(k.requests.some(({path}) => /^\/api\/billing\?tz=-?\d+$/.test(path)));
  });

  test(`${name}: with Billing open, the bell's next failed auto-reload (another ?notice=, the same #autorel-fail) lands on it`, async () => {
    const f = billingPage(cardedBilling({reload_failed: latest, reload_notice: older()}));
    f.location.pathname = '/billing'; f.location.hash = '#autorel-fail'; f.location.search = '?notice=263';
    await f.render(); await settle();
    const first = f.root.querySelector('#autorel-fail');
    assert.equal(first.focusOptions && first.focusOptions.preventScroll, true, 'the first notice lands on its failure');
    f.document.activeElement = null;
    f.location.search = '?notice=218';
    await f.render(); await settle();
    const next = f.root.querySelector('#autorel-fail');
    assert.notEqual(next, first);
    assert.equal(next.focusOptions && next.focusOptions.preventScroll, true, 'the second lands on its own, though only the query changed');
    if (f.clearTimers) f.clearTimers();
  });

  if (!detailStrategy) return;
  const strategyPage = async (detail) => {
    const f = frontend({fetchImpl: (path) => path === '/api/strategies/42' ? response(detail) : response({deployments: [], open_positions: []})});
    f.location.pathname = '/strategies/42';
    await f.render(); await settle();
    if (f.clearTimers) f.clearTimers();
    return f;
  };
  const kept = (n) => Array.from({length: n}, (_, i) => ({ticker: `KXBTC15M-26OCT${10 + i}1215-00`, side: 'yes', qty: 5, entry: 0.41,
    exit: i % 2 ? 1 : 0, pnl: i % 2 ? 2.9 : -2.1, reason: 'settled', opened: `2026-10-${10 + i}T12:00`}));

  test(`${name}: the strategy page's machine spec names markets by name and shows no internal id or raw key`, async () => {
    const f = await strategyPage(detailStrategy({spec: {market: 'kxhighchi', experiment_id: 4471, pool: ['eth15m'], side: 'yes'},
      spec_shown: {market: 'Chicago daily high temperature', pool: ['Ethereum up or down in 15 minutes'], side: 'yes'}}));
    const raw = f.root.querySelector('details.raw');
    assert.ok(raw, 'the spec block is on the page');
    assert.match(raw.querySelector('summary').textContent, /^Machine spec \(JSON\)$/);
    const pre = raw.querySelector('pre').textContent.replace(/&quot;/g, '"');  // the offline DOM keeps the escape
    assert.match(pre, /"market": "Chicago daily high temperature"/);
    assert.match(pre, /"Ethereum up or down in 15 minutes"/);
    assert.doesNotMatch(pre, /kxhighchi|eth15m|experiment_id|4471/);
    // A payload without it (an older server) shows an empty spec, never the stored one.
    const g = await strategyPage(detailStrategy({spec: {market: 'kxhighchi'}}));
    assert.doesNotMatch(g.root.querySelector('details.raw pre').textContent, /kxhighchi/);
  });

  test(`${name}: the later-period trades say how many of the period's trades they list`, async () => {
    const base = detailStrategy();
    const f = await strategyPage(detailStrategy({recent_trades: kept(12), out_of_sample: {...base.out_of_sample, trades: 64}}));
    assert.match(f.root.querySelector('#main').innerHTML, /<p class="hint">The latest 12 of its 64 later-period trades\.<\/p>/);
    const g = await strategyPage(detailStrategy({recent_trades: kept(6), out_of_sample: {...base.out_of_sample, trades: 6}}));
    assert.doesNotMatch(g.root.querySelector('#main').innerHTML, /later-period trades\.<\/p>/, 'every one listed: no count line');
  });
}
