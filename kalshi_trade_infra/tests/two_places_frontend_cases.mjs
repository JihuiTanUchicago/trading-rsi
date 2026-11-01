// Shared cases, run against each bundle's offline harness (Tau and Meridian): the app open in two places at once (two
// windows side by side, or a desktop tab and a phone). Walked 2026-11-01 at 1366x900 and 390x844 with two pages on one
// account: something done in one place stood in the other until that page's next poll, up to a minute on a strategy
// page, and a press on what it still offered found the thing already gone:
// - focusing a window in view (never hidden, so no visibilitychange) read nothing but /api/me: its polls read at once
//   now, on the same 15s terms;
// - Remove on a deployment removed elsewhere said "This deployment is stopped." beside a row still offering Remove
//   until the next 10s read: the board reads again at once;
// - Share and Delete on a strategy deleted elsewhere said "No such strategy." under buttons still on offer, and its
//   poll said "retrying" for good: the page says the strategy is gone, once, and its Share and Delete stand down;
// - a reaction pressed on a post deleted elsewhere said "No such post." with the post still up; a reaction already
//   added elsewhere was taken straight back off by the stale pill.
// Reads only, besides the press itself: nothing here sends, retries or times out a write on its own.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const writes = (f) => f.requests.filter((r) => r.options && r.options.method && r.options.method !== 'GET');
const flip = async (f, hidden) => { f.document.hidden = hidden; for (const [type, on] of f.documentListeners) if (type === 'visibilitychange') on({}); await settle(); };
const polls = (f) => [...f.intervals.values()].filter((c) => String(c).includes('!document.hidden && gen === S.gen) tick()'));
const dep = (overrides = {}) => ({id: 1, strategy_id: 1, title: 'Dep one', market: 'btc15m', mode: 'paper', status: 'running',
  status_reason: null, realized_pnl: 1, closed_trades: 5, open_positions: 0, unrealized_pnl: null, created_at: '2026-10-24T08:00:00Z',
  curve: [], stake: 5, max_positions: 1, backtest: {out_of_sample: null}, ...overrides});
const post = (id, over = {}) => ({id, body: 'Post ' + id, author: 'Bob', username: 'bob', created_at: '2026-10-17T10:00:00Z',
  mine: false, comments: 0, reactions: [], ...over});
const GONE = 'This strategy is no longer in your list: it was deleted in another tab or on another device.';

export function twoPlacesCases({name, frontend, detailStrategy}) {
  // The Deploy board, its deployments as `list()` says; the window's listeners kept so a check can focus it.
  const board = ({list, act}) => {
    const windowListeners = [];
    const f = frontend({win: {addEventListener: (n, fn) => windowListeners.push([n, fn])}, fetchImpl: (path, options) => {
      if (path === '/api/deployments' || path.startsWith('/api/deployments?')) return response({deployments: list(), open_positions: []});
      if (act && /^\/api\/deployments\/\d+\/(pause|resume|stop)$/.test(path) && options && options.method === 'POST') return act(path);
      return response({});
    }});
    f.location.pathname = '/deploy';
    f.focusWindow = async () => { for (const [n, fn] of windowListeners) if (n === 'focus') fn({}); await settle(); };
    return f;
  };
  const boardReads = (f) => f.requests.filter((r) => r.path === '/api/deployments' || r.path.startsWith('/api/deployments?')).length;

  test(`${name}: focusing a window in view reads its page's polls at once, 15s apart at most, reads only`, async () => {
    let deps = [dep(), dep({id: 2, strategy_id: 2, title: 'Dep two'})];
    const f = board({list: () => deps});
    await f.viewDeploy(); await settle();
    f.document.hidden = false;
    assert.ok(f.root.querySelector('#deps [data-dep="1"][data-act="stop"]'));
    const start = boardReads(f);
    await f.focusWindow();
    assert.equal(boardReads(f), start, 'not straight after the page read it');
    // Removed in the other window; this one is focused a while later: the board reads at once, the row goes.
    deps = [dep({id: 2, strategy_id: 2, title: 'Dep two'})];
    f.shiftClock(16000);
    await f.focusWindow();
    assert.equal(boardReads(f), start + 1, 'one read on focus');
    assert.equal(f.root.querySelector('#deps [data-dep="1"]'), null, 'the row removed elsewhere is gone, with its Remove');
    // Focus flapping between the two windows: no more reads within 15 seconds of the last.
    for (let i = 0; i < 5; i++) { f.shiftClock(2000); await f.focusWindow(); }
    assert.equal(boardReads(f), start + 1);
    // A return to the tab reads (visibilitychange), and the focus that comes with it adds nothing.
    f.shiftClock(30000);
    await flip(f, true); f.shiftClock(6000); await flip(f, false); await f.focusWindow();
    assert.equal(boardReads(f), start + 2, 'the return read once');
    // A hidden tab's focus (never, in a browser, but) reads nothing.
    f.shiftClock(30000); f.document.hidden = true; await f.focusWindow(); f.document.hidden = false;
    assert.equal(boardReads(f), start + 2);
    assert.equal(writes(f).length, 0, 'focus never writes');
    f.clearTimers();
  });

  test(`${name}: Remove on a deployment already removed elsewhere says the server's words and the board drops the row at once`, async () => {
    let removed = false;
    const f = board({list: () => (removed ? [] : [dep()]), act: (path) => {
      assert.equal(path, '/api/deployments/1/stop');
      removed = true;  // the other window removed it first
      return response({error: 'This deployment is stopped.'}, 409);
    }});
    await f.viewDeploy(); await settle();
    const before = boardReads(f);
    const btn = f.root.querySelector('#deps [data-dep="1"][data-act="stop"]');
    btn.onclick(); f.elapse(500); btn.onclick();  // Remove's armed confirm, then the send
    await settle();
    assert.equal(writes(f).length, 1, 'sent once, never again on its own');
    assert.equal(boardReads(f), before + 1, 'the board reads again at once');
    assert.equal(f.root.querySelector('#deps [data-dep="1"]'), null, 'no stale Remove left to press');
    assert.match(f.root.querySelector('#flash').innerHTML, /This deployment is stopped\./);
    f.clearTimers();
  });

  test(`${name}: a strategy deleted elsewhere: Share says it is gone, Share and Delete stand down, and the poll says so instead of retrying`, async () => {
    let gone = false;
    const f = frontend({fetchImpl: (path, options) => {
      if (path === '/api/strategies/42') return gone ? response({error: 'No such strategy.'}, 404) : response(detailStrategy({deployments: []}));
      if (path === '/api/strategies/42/share' && options && options.method === 'POST') return response({error: 'No such strategy.'}, 404);
      if (path === '/api/strategies/42/delete') return response({error: 'No such strategy.'}, 404);
      return response({});
    }});
    f.location.pathname = '/strategies/42';
    await f.viewStrategy(42); await settle();
    const moved = () => f.root.querySelector('#strat-moved').textContent;
    assert.equal(moved(), '');
    gone = true;  // deleted in another tab
    f.root.querySelector('#share-email').value = 'friend@example.com';
    f.root.querySelector('#share-form').onsubmit({preventDefault() {}}); await settle();
    assert.match(f.root.querySelector('#shareflash').innerHTML, new RegExp(GONE));
    assert.doesNotMatch(f.root.querySelector('#shareflash').innerHTML, /No such strategy/);
    assert.match(moved(), new RegExp(GONE), 'the page says it once, near the top');
    assert.ok(f.root.querySelector('#strat-moved a[href="/strategies"]'), 'with the way to the list');
    assert.equal(f.root.querySelector('#del-strat').disabled, true, 'Delete stands down');
    assert.equal(f.root.querySelector('#share-form button').disabled, true, 'Share stands down');
    assert.equal(writes(f).length, 1, 'only the press itself was sent');
    // The minute's poll reads the 404: the gone line stays, and nothing says it is retrying.
    for (let i = 0; i < 3; i++) { f.shiftClock(65e3); polls(f)[0](); await settle(); }
    assert.equal(f.root.querySelector('#strat-stale').textContent, '', 'not "Last updated …, retrying"');
    assert.match(moved(), new RegExp(GONE));
    assert.equal(f.root.querySelectorAll('#strat-moved .strat-gone').length, 1, 'said once');
    assert.equal(writes(f).length, 1);
    f.clearTimers();
  });

  test(`${name}: a strategy page whose poll finds it deleted says so with no press at all`, async () => {
    let gone = false;
    const f = frontend({fetchImpl: (path) => {
      if (path === '/api/strategies/42') return gone ? response({error: 'No such strategy.'}, 404) : response(detailStrategy({deployments: []}));
      return response({});
    }});
    f.location.pathname = '/strategies/42';
    await f.viewStrategy(42); await settle();
    gone = true;
    f.shiftClock(65e3); polls(f)[0](); await settle();
    assert.match(f.root.querySelector('#strat-moved').textContent, new RegExp(GONE));
    assert.equal(f.root.querySelector('#del-strat').disabled, true);
    assert.equal(writes(f).length, 0, 'the poll never writes');
    f.clearTimers();
  });

  test(`${name}: a reaction on a post deleted elsewhere says the server's words and the post goes now; a stale pill adds, never takes off`, async () => {
    let posts = [post(5, {reactions: [{emoji: '🔥', count: 1, mine: false, names: ['Ann']}]}), post(6, {reactions: [{emoji: '👍', count: 1, mine: false, names: ['Ann']}]})];
    const bodies = [];
    const f = frontend({fetchImpl: (path, options) => {
      if (path === '/api/strategies') return response({strategies: []});
      if (path === '/api/community/6/react') return response({error: 'This post was deleted.'}, 404);
      if (path === '/api/community/5/react') {
        bodies.push(JSON.parse(options.body));
        // Already added in the other tab: the server keeps it on and says so.
        return response({reactions: [{emoji: '🔥', count: 2, mine: true, names: ['Ann', 'Offline fixture']}]});
      }
      if (path.startsWith('/api/community')) return response({posts});
      return response({});
    }});
    f.location.pathname = '/community';
    // alert0 puts its notice at the top of #main through firstChild and prepend, which the fake DOM lacks: added here
    // for this check, and taken off again whatever happens.
    const proto = Object.getPrototypeOf(f.root);
    const had = {first: Object.getOwnPropertyDescriptor(proto, 'firstChild'), prepend: Object.getOwnPropertyDescriptor(proto, 'prepend')};
    if (!had.first) Object.defineProperty(proto, 'firstChild', {configurable: true, get() { return this.children[0] || null; }});
    if (!had.prepend) Object.defineProperty(proto, 'prepend', {configurable: true, writable: true, value(child) { this.children.unshift(child); child.parent = this; }});
    try {
    await f.viewCommunity(); await settle();
    const feed = f.root.querySelector('#feed');
    feed.listeners.get('click')({target: feed.querySelector('.post[data-id="5"] .rx[data-emoji]')}); await settle();
    assert.deepEqual(bodies, [{emoji: '🔥', on: true}], 'the pill drawn as not mine asks to add');
    const pill = feed.querySelector('.post[data-id="5"] .rx[data-emoji]');
    assert.equal(pill.getAttribute('aria-pressed'), 'true', 'and shows it on, as the server has it');
    // Post 6 was deleted in the other tab: a reaction on it says so and the feed drops it at once.
    posts = [posts[0]];
    const reads = f.requests.filter((r) => r.path.startsWith('/api/community') && !(r.options && r.options.method === 'POST')).length;
    const art6 = feed.querySelector('.post[data-id="6"]');
    feed.listeners.get('click')({target: art6.querySelector('.rx[data-emoji]')}); await settle();
    assert.ok(art6.removed || !feed.querySelector('.post[data-id="6"]'), 'the deleted post goes now');
    assert.ok(f.requests.filter((r) => r.path.startsWith('/api/community') && !(r.options && r.options.method === 'POST')).length > reads, 'the feed reads again');
    assert.equal(feed.querySelector('.post[data-id="6"]'), null, 'and draws without it');
    assert.equal(writes(f).length, 2, 'each press sent once');
    const said = f.root.querySelector('#flash') || f.root.querySelector('[data-alert0]');
    assert.ok(said, 'a notice at the top of the page');
    assert.match(said.innerHTML, /This post was deleted\./, 'in the server’s words');
    f.clearTimers();
    } finally {
      if (!had.first) delete proto.firstChild;
      if (!had.prepend) delete proto.prepend;
    }
  });
}
