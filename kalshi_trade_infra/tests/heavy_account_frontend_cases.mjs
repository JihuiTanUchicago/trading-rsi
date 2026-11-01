// Shared cases, run against each bundle's offline DOM/transport harness (Tau and Meridian): a heavy, long-time customer.
// Walked 2026-11-01 on a seeded account (7,500 closed trades over 120 days, 40 deployments, 250 strategies, 300 research
// sessions, 2,000 credit rows, 400 bell items, a post with 121 replies):
// - the post's thread said "121 replies" and listed up to the 106th: the server sent the oldest 100 and the page had
//   no way to the 21 newest (a reply just sent among them). It now gets the newest page first and older ones on request;
// - Deploy (95KB) and the dashboard (79KB) downloaded their whole answer twice a minute on an unchanged board, for
//   the demo-funds check's pending flag alone (the demo cash memo ages out each minute): the flag rides the "same"
//   answer now, and a "same" answer carrying it still asks the demo cash and reads the page once more;
// - Meridian's "Recently closed, latest 12" said the list was cut but not where the rest are.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 8; i++) await flush(); };
const asked = (f, prefix) => f.requests.filter(({path}) => path.startsWith(prefix)).length;
const tick = async (f) => { for (const t of [...f.intervals.values()]) t(); await settle(); };
const reply = (id) => ({id, body: 'Reply ' + id, author: 'Ana', username: 'ana', created_at: '2026-10-17T10:00:00Z'});

export function heavyAccountCases({name, frontend}) {
  test(`${name}: a thread past one page shows its newest replies first and reads every older one on request`, async () => {
    const post = {id: 11, body: 'Hi', author: 'Bob', username: 'bob', created_at: '2026-10-17T10:00:00Z', mine: false, comments: 121, reactions: []};
    const range = (a, b) => Array.from({length: b - a + 1}, (_, k) => reply(a + k));
    const f = frontend({fetchImpl: (path) => {
      if (path === '/api/strategies') return response({strategies: []});
      if (path === '/api/community') return response({posts: [post]});
      // The server: the newest 100 (22..121), then everything older than reply 22.
      if (path === '/api/community/11/comments?before=22') return response({comments: range(1, 21), older: false});
      if (path === '/api/community/11/comments') return response({comments: range(22, 121), older: true});
      return response({});
    }});
    f.location.pathname = '/community';
    await f.viewCommunity(); await settle();
    const feed = f.root.querySelector('#feed'), click = (target) => feed.listeners.get('click')({target});
    await click(feed.querySelector('[data-act="comments"]')); await settle();
    const box = feed.querySelector('.comments');
    const shown = () => box.querySelectorAll('.cmt').filter((c) => !c.hidden).map((c) => c.querySelector('p').textContent);
    const more = () => box.querySelector('.cmt-more');
    assert.deepEqual(shown(), range(112, 121).map((c) => c.body), 'the newest ten, the newest by the reply box');
    for (let i = 0; i < 9; i++) await more().onclick();
    assert.equal(shown().length, 100);
    assert.equal(more().hidden, false, 'older replies are left: the button stays');
    assert.equal(more().textContent, 'Load earlier replies');
    assert.equal(asked(f, '/api/community/11/comments?before='), 0);
    await more().onclick(); await settle();
    assert.equal(asked(f, '/api/community/11/comments?before=22'), 1, 'read the page older than the oldest one loaded');
    for (let i = 0; i < 3; i++) await more().onclick();
    assert.equal(shown().length, 121);
    assert.equal(shown()[0], 'Reply 1');
    assert.equal(shown()[120], 'Reply 121');
    assert.equal(more().hidden, true, 'nothing older is left');
  });

  test(`${name}: an older page of replies that fails to read says so on its button and reads again on a press`, async () => {
    const post = {id: 11, body: 'Hi', author: 'Bob', username: 'bob', created_at: '2026-10-17T10:00:00Z', mine: false, comments: 101, reactions: []};
    let failing = true;
    const f = frontend({fetchImpl: (path) => {
      if (path === '/api/strategies') return response({strategies: []});
      if (path === '/api/community') return response({posts: [post]});
      if (path.startsWith('/api/community/11/comments?before=')) return failing ? response({error: 'Server error.'}, 500) : response({comments: [reply(1)], older: false});
      if (path === '/api/community/11/comments') return response({comments: [reply(2)], older: true});
      return response({});
    }});
    f.location.pathname = '/community';
    await f.viewCommunity(); await settle();
    const feed = f.root.querySelector('#feed'), click = (target) => feed.listeners.get('click')({target});
    await click(feed.querySelector('[data-act="comments"]')); await settle();
    const box = feed.querySelector('.comments'), more = () => box.querySelector('.cmt-more');
    assert.equal(more().textContent, 'Load earlier replies', 'one reply loaded, older ones left');
    await more().onclick(); await settle();
    assert.match(more().textContent, /Earlier replies didn’t load\. Try again/);
    assert.equal(asked(f, '/api/community/11/comments?before='), 1, 'never read again on its own');
    failing = false;
    await more().onclick(); await settle();
    assert.equal(box.querySelectorAll('.cmt').length, 2);
    assert.equal(more().hidden, true);
  });

  // ---------- polls: the demo check's pending flag rides the "same" answer ----------
  const demoDep = {id: 2, strategy_id: 2, title: 'Dep 2', market: 'btc15m', mode: 'demo', status: 'running', status_reason: null,
    realized_pnl: 0, closed_trades: 0, open_positions: 0, unrealized_pnl: null, created_at: '2026-10-24T08:00:00Z', curve: [],
    stake: 5, max_positions: 1, backtest: {}};

  test(`${name}: an unchanged Deploy board whose demo check comes pending asks the demo cash and reads "same" again, never its whole answer`, async () => {
    let pending = false, full = 0;
    const f = frontend({fetchImpl: (path) => {
      if (path.startsWith('/api/deployments')) {
        if (path.includes('v=b1')) return response({v: 'b1', same: true, marks: {}, unrealized: {}, ...(pending ? {demo_health_pending: true} : {})});
        full++;
        return response({deployments: [demoDep], open_positions: [], demo_health: null, v: 'b1'});
      }
      if (path === '/api/funds/cash?book=demo') { pending = false; return response({book: 'demo', connected: true, balance: 100}); }
      if (path.startsWith('/api/strategies?status=passed')) return response({counts: {passed: 1}});
      return response({});
    }});
    f.location.pathname = '/deploy';
    await f.viewDeploy(); await settle();
    assert.equal(full, 1);
    await tick(f);
    assert.equal(asked(f, '/api/funds/cash?book=demo'), 0, 'nothing pending, nothing asked');
    pending = true;  // the demo cash memo ran out
    const reads = asked(f, '/api/deployments');
    await tick(f);
    assert.equal(asked(f, '/api/funds/cash?book=demo'), 1, 'the pending check asked for the demo cash');
    assert.equal(asked(f, '/api/deployments'), reads + 2, 'and read the board once more');
    assert.equal(full, 1, 'every read after the first was "same"');
    await tick(f);
    assert.equal(asked(f, '/api/funds/cash?book=demo'), 1, 'not pending any more: nothing more to ask');
    f.clearTimers();
  });

  test(`${name}: an unchanged dashboard whose demo check comes pending asks the demo cash and reads "same" again`, async () => {
    let pending = false, full = 0;
    const dash = {paper: {equity: 1001.6, cash: 1000, open_at_cost: 1.6, total_pnl: 1.6, closed_trades: 8, open_positions: 0,
      realized_pnl: 3.5, today_pnl: 0, running: 0, daily: []},
    live: {connected: false, balance: null, closed_trades: 0, open_positions: 0, open_at_cost: 0, realized_pnl: 0, today_pnl: 0, running: 0},
    demo: {equity: 1000, capital: 1000, realized_pnl: 0, closed_trades: 0, open_positions: 0, health: null},
    deployments: [], open_positions: [], recent_closed: [], reconcile: [], freshness: {btc: 30, engine: 4},
    agents: {research_running: 0, research_queued: 0}, v: 'h1'};
    const f = frontend({fetchImpl: (path) => {
      if (path.startsWith('/api/dashboard')) {
        if (path.includes('v=h1')) return response({v: 'h1', same: true, marks: {}, unrealized: {}, freshness: {btc: 30, engine: 4}, ...(pending ? {demo_health_pending: true} : {})});
        full++;
        return response(dash);
      }
      if (path === '/api/funds/cash?book=demo') { pending = false; return response({book: 'demo', connected: true, balance: 100}); }
      if (path.startsWith('/api/track-record')) return response({modes: {}});
      if (path === '/api/autopilot/status') return response({autopilot: false, mode: 'off'});
      return response({});
    }});
    f.S.me.user.autopilot = false; f.S.ap = null; f.S.apAt = 0;
    f.location.pathname = '/';
    await f.viewDashboard(); await settle();
    assert.equal(full, 1);
    pending = true;
    const reads = asked(f, '/api/dashboard');
    await tick(f);
    assert.equal(asked(f, '/api/funds/cash?book=demo'), 1, 'the pending check asked for the demo cash');
    assert.equal(asked(f, '/api/dashboard'), reads + 2, 'and read the dashboard once more');
    assert.equal(full, 1, 'every read after the first was "same"');
    f.clearTimers();
  });

  test(`${name}: the dashboard's closed list, cut to the latest trades, says where every closed trade is`, async () => {
    const closed = (id) => ({id, deployment_id: 1, ticker: `KXBTC15M-26OCT181230-${id}`, title: `Strategy ${id}`, side: 'yes', qty: 10,
      entry_price: 0.35, mode: 'paper', unrealized: null, pnl: 4.25, market_close_time: '2026-10-18T12:30:00Z',
      opened_at: '2026-10-18T12:00:00Z', closed_at: '2026-10-18T12:30:00Z'});
    for (const [total, says] of [[6038, true], [12, false]]) {
      const dash = {paper: {equity: 4670.51, cash: 4666.9, open_at_cost: 0, total_pnl: 3670.66, closed_trades: total, open_positions: 0,
        realized_pnl: 3670.66, today_pnl: 0, running: 1, daily: []},
      live: {connected: false, balance: null, closed_trades: 0, open_positions: 0, open_at_cost: 0, realized_pnl: 0, today_pnl: 0, running: 0},
      demo: {equity: 1000, capital: 1000, realized_pnl: 0, closed_trades: 0, open_positions: 0, health: null},
      deployments: [], open_positions: [], recent_closed: Array.from({length: 12}, (_, k) => closed(k + 1)), reconcile: [],
      freshness: {btc: 30, engine: 4}, agents: {research_running: 0, research_queued: 0}, v: 'c1'};
      const f = frontend({fetchImpl: (path) => path.startsWith('/api/dashboard') ? response(dash)
        : path.startsWith('/api/track-record') ? response({modes: {}}) : path === '/api/autopilot/status' ? response({autopilot: false, mode: 'off'}) : response({})});
      f.S.me.user.autopilot = false; f.S.ap = null; f.S.apAt = 0;
      f.location.pathname = '/';
      await f.viewDashboard(); await settle();
      const html = f.root.querySelector('#main').innerHTML + (f.root.querySelector('#boardbody') || {innerHTML: ''}).innerHTML;
      // Where they are: each book's trades CSV and, for paper, Funds' paper history. Never the track record, which lists
      // no trade (walk 2026-11-01).
      const link = /<p class="[^"]*">Every closed trade, with net P&amp;L, is in its book’s trades CSV \(Download trades, by the book’s figure above\); each paper one is also in your <a href="\/funds">Funds<\/a> paper history\.<\/p>/;
      if (says) assert.match(html, link, `${total} closed trades, 12 listed: it says where the rest are`);
      else if (name === 'Meridian') assert.doesNotMatch(html, link, 'every closed trade is listed: nothing to point to');
      assert.doesNotMatch(html, /is in the track record/);
      f.clearTimers();
    }
  });
}
