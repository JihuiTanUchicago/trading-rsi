// Shared cases, run against each bundle's offline harness (Tau and Meridian): a clock time without its date ("23:50
// CDT") is only ever one from today, and stays so past the reader's midnight. From the midnight walk of 2026-11-01: a
// page left open past midnight kept a bare clock that meant "today" until the item drew again, which on a page with
// no change to draw (or reads failing) was for good:
// - Funds' cash moves ("When"), a grouped community post's time, a research session's "ended", a market page's
//   "as of" and its Closes figure;
// - the other way, a close tomorrow kept its date after midnight, when it had become today's;
// - the bell's times kept their words;
// - hovers (title) on the autopilot feed's clocks said a bare "23:50 CDT" for a time from today.
// Each time now says its words again from its own datetime when the day turns (dayWatch: the page's own ticks and a
// return to the tab, no new timer and no read), and a hover always names the date and zone.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
// Five minutes past the reader's next midnight, from now.
const pastLocalMidnight = () => { const n = new Date(); return new Date(n.getFullYear(), n.getMonth(), n.getDate() + 1, 0, 5).getTime() - n.getTime(); };
const nextMidnight = () => { const n = new Date(); return new Date(n.getFullYear(), n.getMonth(), n.getDate() + 1).getTime(); };
const fmt = (iso, o) => new Intl.DateTimeFormat(undefined, o).format(new Date(iso));
const CLOCK_ZONE = {hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZoneName: 'short'};
const STAMP = {month: 'short', day: 'numeric', ...CLOCK_ZONE};
const bare = (iso) => fmt(iso, CLOCK_ZONE), dated = (iso) => fmt(iso, STAMP);
const words = (el) => (el ? el.textContent.replace(/ /g, ' ') : null);
const ticks = async (f) => { for (const tick of [...f.intervals.values()]) { await tick(); await settle(); } };
const back = async (f) => {
  f.document.hidden = true; for (const [n, fn] of f.documentListeners) if (n === 'visibilitychange') fn({});
  f.document.hidden = false; for (const [n, fn] of f.documentListeners) if (n === 'visibilitychange') fn({});
  await settle();
};

export function dayClockCases({name, frontend, fundsData, storageFor}) {
  for (const [way, move] of [['a return to the tab', back], ['the poll’s tick, its read still out', ticks]]) {
    test(`${name}: Funds' cash move from before the reader's midnight says its date past it, on ${way}`, async () => {
      const at = new Date().toISOString();
      const ledger = [{created_at: at, kind: 'trade', memo: 'Bought 5 YES KXBTC15M-26OCT241200-00 @ 0.30', amount: -1.58},
        {created_at: '2026-10-24T15:00:00Z', kind: 'deposit', memo: 'Starting paper balance', amount: 1000}];
      let reads = 0;
      const f = frontend({fetchImpl: (path) => {
        if (path === '/api/funds') return ++reads === 1 ? response({...fundsData(), paper: {cash: 998.42, ledger}}) : new Promise(() => {});
        return response({});
      }});
      f.location.pathname = '/funds';
      await f.viewFunds(); await settle();
      const when = () => words(f.root.querySelector('.t-ledger tbody tr td[data-lab="When"]').querySelector('time'));
      assert.equal(when(), bare(at));
      f.shiftClock(pastLocalMidnight());
      await move(f);
      assert.equal(when(), dated(at));
      f.clearTimers();
    });
  }

  test(`${name}: a grouped community post from before the reader's midnight says its date past it`, async () => {
    const now = Date.now(), iso = (ms) => new Date(ms).toISOString();
    const posts = [{id: 14, body: 'Post 14', author: 'Bob', username: 'bob', created_at: iso(now - 60000), mine: false, comments: 0, reactions: []},
      {id: 13, body: 'Post 13', author: 'Bob', username: 'bob', created_at: iso(now - 120000), mine: false, comments: 0, reactions: []}];
    const f = frontend({fetchImpl: (path) => path.startsWith('/api/community') ? response({posts}) : path === '/api/strategies' ? response({strategies: []}) : response({})});
    f.location.pathname = '/community';
    await f.viewCommunity(); await settle();
    const gutter = () => words(f.root.querySelector('#feed').querySelector('.post[data-id="13"]').querySelector('.gutter-time'));
    if (new Date(posts[1].created_at).toDateString() !== new Date().toDateString()) return;  // run in the two minutes after midnight
    assert.equal(gutter(), bare(posts[1].created_at));
    f.shiftClock(pastLocalMidnight());
    await back(f);
    assert.equal(gutter(), dated(posts[1].created_at));
    f.clearTimers();
  });

  test(`${name}: a session's "ended" from before the reader's midnight says its date past it`, async () => {
    const end = new Date(Date.now() - 60000).toISOString(), start = new Date(Date.now() - 70 * 60000).toISOString();
    if (new Date(end).toDateString() !== new Date().toDateString()) return;
    const f = frontend({fetchImpl: (path) => path.startsWith('/api/runs/9')
      ? response({run: {id: 9, prompt: 'Idea', market: 'btc15m', created_at: start, started_at: start, finished_at: end, status: 'succeeded'}, events: [], strategies: []})
      : response({})});
    await f.viewRun(9); f.clearTimers();
    const time = () => f.root.querySelectorAll('#runfigs .m').find((m) => m.querySelector('dt').textContent === 'Time').querySelector('time');
    assert.equal(words(time()), bare(end));
    f.shiftClock(pastLocalMidnight());
    await back(f);
    assert.equal(words(time()), dated(end));
  });

  test(`${name}: a market page's "as of" takes its date past the reader's midnight, and a close tomorrow drops it once that is today`, async () => {
    const asOf = new Date().toISOString(), close = new Date(nextMidnight() + 3 * 3600e3).toISOString();
    const payload = {ticker: 'KXBTCD-26OCT2617-T113499.99', series: 'KXBTCD', title: 'Bitcoin price at 5pm EDT?', sub: '$113,500 or above',
      status: 'active', yes_bid: '0.55', yes_ask: '0.57', last: '0.55', volume_24h: '15234', open_interest: '40211',
      close_time: close, as_of: asOf, rules: 'Resolves Yes above 113499.99.', period_min: 1,
      points: Array.from({length: 130}, (_, i) => ({t: 1793012400 + 60 * i, yes: .5, vol: 1})), mine: {open: [], closed: []}};
    const f = frontend({fetchImpl: (path) => (path.startsWith('/api/explore/market?') ? response(payload) : response({}))});
    f.S.me.markets = [{key: 'btc_daily', series: 'KXBTCD', label: 'Bitcoin above a set price at 5pm ET', category: 'Crypto', close_hour_et: 17}];
    f.location.pathname = '/explore'; f.location.search = '?m=' + encodeURIComponent(payload.ticker);
    await f.viewExplore(); await settle();
    const timeOf = (iso) => f.root.querySelectorAll('time').find((t) => t.getAttribute('datetime') === iso);
    assert.equal(words(timeOf(asOf)), bare(asOf));
    assert.equal(words(timeOf(close)), dated(close), 'a close tomorrow says its date');
    f.shiftClock(pastLocalMidnight());
    await back(f);
    assert.equal(words(timeOf(asOf)), dated(asOf));
    assert.equal(words(timeOf(close)), bare(close), 'the close is today now: no needless date');
    f.clearTimers();
  });

  test(`${name}: the autopilot feed's hover on a clock from today names its date and zone`, async () => {
    const store = new Map([['tau_ap_open', '1']]);
    const at = new Date().toISOString();
    const f = frontend({storage: storageFor(store), fetchImpl: (path) => {
      if (path.startsWith('/api/chat?')) return response({messages: [{id: 1, role: 'system', content: 'Reviewed: nothing to change.', created_at: at}], agent_busy: false});
      if (path === '/api/models') return response({models: [], default: ''});
      throw new Error('Unexpected offline request: ' + path);
    }});
    f.S.me = {...f.S.me, user: {...f.S.me.user, id: 7, autopilot: false}};
    const box = f.document.createElement('div'); box.id = 'autopilot-float'; f.document.body.appendChild(box);
    f.renderAutopilot(box, null); await settle();
    await f.apLoadChat(box, true); await settle();
    const t = box.querySelector('#apf-messages').querySelectorAll('time').find((e) => e.getAttribute('datetime') === at);
    assert.ok(t, 'the line has its clock');
    assert.equal(t.getAttribute('title'), dated(at));
  });

  test(`${name}: an open bell says its times again when the reader's day turns`, async () => {
    const at = new Date(Date.now() - 60e3).toISOString();
    const f = frontend({notifications: {unread: 0, items: [{id: 9, kind: 'share', body: 'A notice.', link: null, created_at: at, read: true, more: 0}]},
      fetchImpl: (path) => (path === '/api/autopilot/status' ? response({mode: 'off'}) : response({}))});
    f.location.pathname = '/';
    await f.render(); await settle();
    const wrap = f.root.querySelector('.bell-wrap');
    await wrap.querySelector('.bell-trig').onclick({stopPropagation() {}}); await settle();
    const time = () => wrap.querySelector('#bell-list').querySelectorAll('time').find((t) => t.getAttribute('datetime') === at);
    assert.equal(time().textContent, '1m ago');
    assert.equal(time().getAttribute('title'), dated(at));
    const shift = pastLocalMidnight();
    f.shiftClock(shift);
    await back(f);
    // Its age by the page's clock, moved on (at least six minutes): never the "1m ago" it said before midnight.
    const h = Math.floor((shift + 60e3) / 3600e3);
    assert.equal(time().textContent, h >= 24 ? dated(at) : h ? h + 'h ago' : Math.floor((shift + 60e3) / 60e3) + 'm ago');
    f.clearTimers();
  });
}
