// Shared cases, run against each bundle's offline DOM/transport harness (Tau and Meridian):
// - the bell lists a grouped notice with how many more it stands for, a notice with no page as plain
//   text, and moves focus into the list when it opens;
// - the autopilot status read brings new notices to the bell within a minute, without a reload;
// - a list that didn't load still opens and says so, with Try again, and the next read fills it;
// - a refresh that failed with a list up keeps it and says quietly how old it is, with Try again (only then);
// - marking the list read that didn't save puts the dot back, and only the next open tries once more;
// - a bell link to a post opens the feed at that post with its replies, shows the feed from that post
//   when it is older than the newest page, and says so when the post is gone.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const ago = ms => new Date(Date.now() - ms).toISOString();
const notesRead = f => f.requests.filter(({path}) => path === '/api/notifications').length;
const post = (id, over = {}) => ({id, body: 'Post ' + id, author: 'Bob', username: 'bob', created_at: '2026-10-17T10:00:00Z',
  mine: true, comments: 1, reactions: [], ...over});

export function bellCases({name, frontend, detailStrategy}) {
  const dashboard = (notifications, extra = () => undefined) => frontend({notifications, fetchImpl: (path) => {
    const hit = extra(path);
    if (hit) return hit;
    if (path === '/api/notifications/read') return response({ok: true});
    if (path === '/api/autopilot/status') return response({mode: 'off'});
    return response({});
  }});

  test(`${name}: a grouped notice says how many more it stands for, one with no page is text, and opening moves focus in`, async () => {
    const f = dashboard({unread: 1, items: [
      {id: 9, kind: 'comment', body: 'Ana commented on your post.', link: '/community#post-4', created_at: ago(60e3), read: false, more: 2},
      {id: 5, kind: 'share', body: 'An old notice.', link: null, created_at: ago(2 * 86400e3), read: true, more: 0},
    ]});
    f.location.pathname = '/';
    await f.render(); await settle();
    const wrap = f.root.querySelector('.bell-wrap'), trig = wrap.querySelector('.bell-trig'), menu = wrap.querySelector('.bell-menu');
    const links = wrap.querySelectorAll('a.notif');
    assert.equal(links.length, 1);
    assert.equal(links[0].getAttribute('href'), '/community#post-4');
    assert.match(links[0].innerHTML, /<span class="notif-more">and 2 more like this<\/span>/);
    const plain = wrap.querySelectorAll('p.notif');
    assert.equal(plain.length, 1); assert.match(plain[0].textContent, /An old notice\./);
    assert.doesNotMatch(plain[0].innerHTML, /more like this/);
    // A disclosure, not a menu: the button names what it controls and the list is a labelled region.
    assert.equal(trig.getAttribute('aria-controls'), 'bell-menu'); assert.equal(trig.getAttribute('aria-haspopup'), null);
    assert.equal(menu.getAttribute('role'), 'region'); assert.equal(menu.getAttribute('aria-label'), 'Notifications');
    await trig.onclick({stopPropagation() {}});
    assert.equal(menu.hidden, false);
    assert.equal(links[0].focusOptions && links[0].focusOptions.preventScroll, true);  // focus went to the first notice
    await trig.onclick({stopPropagation() {}});

    // Nothing to open: focus goes to the list itself, which says it is empty.
    const g = dashboard({unread: 0, items: []});
    g.location.pathname = '/';
    await g.render(); await settle();
    const gm = g.root.querySelector('.bell-menu');
    await g.root.querySelector('.bell-trig').onclick({stopPropagation() {}});
    assert.equal(gm.focusOptions && gm.focusOptions.preventScroll, true);
    assert.match(gm.querySelector('.bell-empty').textContent, /No notifications yet/);
    // Empty, it says what will ring it, so a quiet first hour doesn't read as a broken bell.
    assert.match(gm.querySelector('.bell-empty').textContent, /It rings when a strategy goes on paper or is paused, passes its paper test or is ready for live, and if auto-reload can’t charge your card\./);
  });

  test(`${name}: with live trading closed the bell's summary and empty line name a live-ready notice as the notice does, never "ready for live"`, async () => {
    // The server's live switch closed (/api/me's live_trading_available): a strategy that clears every check waits on
    // paper, and its notice says "cleared every paper-to-live check". The summary over the list said "2 ready for live".
    const items = [1, 2, 3, 4].map((i) => ({id: i, kind: i < 3 ? 'live_ready' : 'paused', body: `Notice ${i}.`, link: null, created_at: ago(i * 60e3), read: false, more: 0}));
    const sum = async (open) => {
      const f = dashboard({unread: 4, items, unread_kinds: {live_ready: 2, paused: 2}});
      f.S.me.live_trading_available = open; f.location.pathname = '/';
      await f.render(); await settle();
      const el = f.root.querySelector('.bell-sum');
      f.clearTimers && f.clearTimers();
      return el ? el.textContent : '';
    };
    const shut = await sum(false);
    assert.match(shut, /2 cleared every paper-to-live check/);
    assert.doesNotMatch(shut, /ready for live/);
    assert.match(await sum(true), /2 ready for live/);  // open: as before
    const empty = async (open) => {
      const g = dashboard({unread: 0, items: []});
      g.S.me.live_trading_available = open; g.location.pathname = '/';
      await g.render(); await settle();
      const el = g.root.querySelector('.bell-empty');
      g.clearTimers && g.clearTimers();
      return el.textContent;
    };
    const closedEmpty = await empty(false);
    assert.match(closedEmpty, /It rings when a strategy goes on paper or is paused, passes its paper test or clears every paper-to-live check, and if auto-reload can’t charge your card\./);
    assert.doesNotMatch(closedEmpty, /ready for live/);
    assert.match(await empty(true), /passes its paper test or is ready for live, and/);
  });

  // Walk 2026-10-31: "131 new: 50 strategies put on paper · 50 comments": the lead counted every unread notice, the
  // parts only the newest 100 the server read (unread_kinds_of). Past that window the summary says which it covers.
  test(`${name}: the bell's summary says its parts cover the newest notices when more are unread than it read`, async () => {
    const items = [1, 2, 3, 4].map((i) => ({id: i, kind: i % 2 ? 'deployed' : 'comment', body: `Notice ${i}.`, link: null, created_at: ago(i * 60e3), read: false, more: 0}));
    const sum = async (d) => {
      const f = dashboard({items, ...d}); f.location.pathname = '/';
      await f.render(); await settle();
      const el = f.root.querySelector('.bell-sum');
      f.clearTimers && f.clearTimers();
      return el ? el.textContent : '';
    };
    assert.equal(await sum({unread: 131, unread_kinds: {deployed: 50, comment: 50}, unread_kinds_of: 100}),
      '131 new; the newest 100: 50 strategies put on paper\u00a0· 50 comments');
    // Every unread one counted in the parts: as before.
    assert.equal(await sum({unread: 6, unread_kinds: {deployed: 4, comment: 2}, unread_kinds_of: 100}),
      '6 new: 4 strategies put on paper\u00a0· 2 comments');
    // Finished research sessions are named, not counted among "others" (services.research_notice).
    assert.equal(await sum({unread: 5, unread_kinds: {deployed: 2, research_done: 2, comment: 1}, unread_kinds_of: 100}),
      '5 new: 2 strategies put on paper\u00a0· 2 research sessions finished\u00a0· 1 comment');
    assert.equal(await sum({unread: 4, unread_kinds: {research_done: 1, comment: 3}, unread_kinds_of: 100}),
      '4 new: 1 research session finished\u00a0· 3 comments');
  });

  test(`${name}: a notice about a strategy no longer in the list says so as text, and a big count is grouped`, async () => {
    // Walk 2026-10-31: a shared strategy the user later deleted, and a forward-test pass of one deleted since,
    // linked to "Not found". The server marks them gone; the line says so and opens nothing.
    const f = dashboard({unread: 1234, items: [
      {id: 9, kind: 'share', body: 'Sam shared the strategy “Drift” with you.', link: '/strategies/14', created_at: ago(60e3), read: false, more: 0, gone: true},
      {id: 8, kind: 'passed', body: '“Fade” passed the paper forward gate.', link: '/strategies/6', created_at: ago(120e3), read: false, more: 0, gone: false},
    ]});
    f.location.pathname = '/';
    await f.render(); await settle();
    const wrap = f.root.querySelector('.bell-wrap');
    const links = wrap.querySelectorAll('a.notif');
    assert.deepEqual(links.map((a) => a.getAttribute('href')), ['/strategies/6']);
    const plain = wrap.querySelectorAll('p.notif');
    assert.equal(plain.length, 1);
    assert.match(plain[0].innerHTML, /Sam shared the strategy “Drift” with you\.<span class="notif-more">This strategy is no longer in your list\.<\/span>/);
    assert.doesNotMatch(plain[0].innerHTML, /strategies\/14/);
    // Counts carry their thousands separator, on screen and to a screen reader.
    assert.equal(wrap.querySelector('.bell-trig').getAttribute('aria-label'), 'Notifications, 1,234 unread');
    assert.match(wrap.querySelector('.bell-head').innerHTML, /<span class="bell-new">1,234 new<\/span>/);
  });

  // Walk 2026-10-31: "Bob replied to your post. and 3 more like this" stood for replies from Dana, Carol and Bob, and
  // a live pause's line for an earlier paper pause with another reason. "like this" only when they say the same.
  test(`${name}: a grouped line says "more like this" only when they say the same, and whom replies and mentions are from`, async () => {
    const at = ago(60e3), row = (id, kind, more, over) => ({id, kind, body: `Notice ${id}.`, link: `/x/${id}`, created_at: at, read: true, more, ...over});
    const f = dashboard({unread: 0, items: [
      row(9, 'comment', 3, {alike: false, from: ['Dana <b>x</b>', 'Carol', 'Bob'], from_more: 2}),
      row(8, 'mention', 1, {alike: false, from: ['Bob'], from_more: 0}),
      row(7, 'comment', 1, {alike: true, from: ['Carol'], from_more: 0}),
      row(6, 'paused', 1, {alike: false, from: [], from_more: 0}),
      row(5, 'reload_failed', 2, {alike: false, from: [], from_more: 0}),
      row(4, 'share', 1, {alike: false, from: [], from_more: 0}),
      row(3, 'comment', 2, {alike: false, from: [], from_more: 0}),
      row(2, 'passed', 0, {alike: true, from: [], from_more: 0}),
    ]});
    f.location.pathname = '/';
    await f.render(); await settle();
    const more = f.root.querySelectorAll('#bell-list .notif').map((n) => (n.querySelector('.notif-more') || {innerHTML: ''}).innerHTML);
    assert.deepEqual(more, [
      'and 3 more replies to this post, from Dana &lt;b&gt;x&lt;/b&gt;, Carol, Bob and 2 others',
      'and 1 more mention on this post, from Bob',
      'and 1 more like this',
      'and 1 earlier pause of this strategy',
      'and 2 earlier failed auto-reloads',
      'and 1 earlier notice about this',
      'and 2 more replies to this post',
      '']);
  });

  test(`${name}: a list with older notices past its lines says so under them, and an old notice names its year`, async () => {
    const old = ago(400 * 86400e3), recent = ago(2 * 86400e3);
    const year = (iso) => String(new Date(iso).getFullYear()), thisYear = String(new Date().getFullYear());
    const list = async (d) => {
      const f = dashboard(d); f.location.pathname = '/';
      await f.render(); await settle();
      return f.root.querySelector('#bell-list');
    };
    let el = await list({unread: 0, older: true, items: [
      {id: 2, kind: 'deployed', body: 'Recent.', link: '/strategies/2', created_at: recent, read: true, more: 0},
      {id: 1, kind: 'deployed', body: 'Old.', link: '/strategies/1', created_at: old, read: true, more: 0}]});
    const older = el.querySelector('.bell-older');
    assert.ok(older);
    assert.equal(older.textContent, 'Older notices aren’t listed here.');
    assert.equal(older.tagName, 'P');
    const times = el.querySelectorAll('a.notif time');
    assert.match(times[1].textContent, new RegExp(`\\b${year(old)}\\b`));
    assert.match(times[1].getAttribute('title'), new RegExp(`\\b${year(old)}\\b`));
    if (year(recent) === thisYear) assert.doesNotMatch(times[0].textContent, new RegExp(`\\b${thisYear}\\b`));  // this year's: as before
    el = await list({unread: 0, older: false, items: [{id: 2, kind: 'deployed', body: 'Recent.', link: '/strategies/2', created_at: recent, read: true, more: 0}]});
    assert.equal(el.querySelector('.bell-older'), null);
    el = await list({unread: 0, older: true, items: []});  // empty is empty, whatever it says
    assert.equal(el.querySelector('.bell-older'), null);
  });

  test(`${name}: a pause notice opens its strategy at Since deploying, focused, and a page without it at its heading`, async () => {
    if (!detailStrategy) return;
    const dep = {id: 3, mode: 'live', status: 'paused', status_reason: 'Live is down $4.20 over its first 6 closed trades.', realized_pnl: -4.2,
      closed_trades: 6, open_positions: 0, created_at: '2026-10-18T08:00:00Z'};
    const open = async (deployments, hash) => {
      const f = frontend({fetchImpl: (path) => path === '/api/strategies/42' ? response(detailStrategy({deployments})) : response({deployments: [], open_positions: []})});
      f.location.pathname = '/deploy';
      await f.render(); await settle();
      f.location.pathname = '/strategies/42'; f.location.hash = hash;  // the bell's link, as go() opens it
      await f.render(); await settle();
      return f;
    };
    let f = await open([dep], '#since-deploying');
    const spot = f.root.querySelector('#since-deploying');
    assert.ok(spot, 'the strategy lists its deployments');
    assert.equal(spot.focusOptions && spot.focusOptions.preventScroll, true, 'focus lands on what the notice is about');
    assert.equal(spot.getAttribute('tabindex'), '-1');
    assert.equal(f.root.querySelector('main h1').focusOptions, undefined, 'not on the heading screens above it');
    if (f.clearTimers) f.clearTimers();
    // Deployed no more (removed since): the page starts at its heading, as any page change does.
    f = await open([], '#since-deploying');
    assert.equal(f.root.querySelector('#since-deploying'), null);
    assert.equal(f.root.querySelector('main h1').focusOptions.preventScroll, true);
    if (f.clearTimers) f.clearTimers();
    // Only the bell's parts are taken: another hash on the page leaves focus to the heading.
    f = await open([dep], '#main');
    assert.equal(f.root.querySelector('main h1').focusOptions.preventScroll, true);
    if (f.clearTimers) f.clearTimers();
  });

  test(`${name}: a notice that arrives while the page sits open reaches the bell within a minute, without a reload`, async () => {
    const notes = {unread: 0, items: []};
    const f = dashboard(notes);
    f.location.pathname = '/';
    await f.render(); await settle();
    const dot = f.root.querySelector('.bell-dot');
    assert.equal(dot.hidden, true); assert.equal(notesRead(f), 1);
    notes.unread = 1;
    notes.items.push({id: 12, kind: 'paused', body: '“Edge” was paused automatically.', link: '/strategies/7', created_at: ago(5e3), read: false, more: 0});
    const ticks = () => [...f.intervals.values()].forEach(tick => tick());
    ticks(); await settle();
    assert.equal(notesRead(f), 1);  // the autopilot's 20s read doesn't re-read the bell every time
    f.shiftClock(61e3); ticks(); await settle();
    assert.equal(notesRead(f), 2);
    assert.equal(dot.hidden, false);
    assert.equal(f.root.querySelector('.bell-trig').getAttribute('aria-label'), 'Notifications, 1 unread');
    assert.match(f.root.querySelector('a.notif.unread').textContent, /was paused automatically/);
  });

  test(`${name}: a bell whose list didn't load still opens, says so, and Try again or the next read fills it`, async () => {
    const unread = {unread: 1, items: [{id: 3, kind: 'deploy', body: 'Edge made a trade.', link: '/deploy', created_at: ago(60e3), read: false, more: 0}]};
    let failing = true;
    const f = dashboard(() => (failing ? response({error: 'Something went wrong on our side.'}, 500) : response(structuredClone(unread))));
    f.location.pathname = '/';
    await f.render(); await settle();
    const wrap = f.root.querySelector('.bell-wrap'), trig = wrap.querySelector('.bell-trig'), menu = wrap.querySelector('.bell-menu');
    const list = wrap.querySelector('#bell-list');
    await trig.onclick({stopPropagation() {}});
    assert.equal(menu.hidden, false);  // it used to do nothing at all
    assert.match(list.textContent, /didn.t load just now/);
    const again = list.querySelector('[data-bell-retry]');
    assert.ok(again);
    failing = false;
    list.listeners.get('click')({target: again});
    await settle();
    assert.match(list.querySelector('a.notif').textContent, /Edge made a trade/);
    assert.equal(menu.hidden, false);
    assert.equal(f.requests.filter(({path}) => path === '/api/notifications/read').length, 1);  // read as it shows

    // Left closed, the autopilot's read within the minute brings the dot back without a tap.
    failing = true;
    const g = dashboard(() => (failing ? response({error: 'Something went wrong on our side.'}, 500) : response(structuredClone(unread))));
    g.location.pathname = '/';
    await g.render(); await settle();
    assert.equal(g.root.querySelector('.bell-dot').hidden, true);
    failing = false;
    g.shiftClock(61e3); [...g.intervals.values()].forEach(tick => tick()); await settle();
    assert.equal(g.root.querySelector('.bell-dot').hidden, false);
    assert.equal(g.root.querySelector('.bell-trig').getAttribute('aria-label'), 'Notifications, 1 unread');
    // A list that never loaded has no "couldn't refresh" line: it says it didn't load.
    assert.equal(g.root.querySelector('.bell-stale').hidden, true);
  });

  // Walk 2026-10-28: a refresh that failed kept the last list and said nothing, so it read as current.
  test(`${name}: a refresh that failed keeps the list and says quietly how old it is, with Try again; only then`, async () => {
    const notes = {unread: 0, items: [{id: 3, kind: 'deploy', body: 'Edge made a trade.', link: '/deploy', created_at: ago(60e3), read: true, more: 0}]};
    let failing = false;
    const f = dashboard(() => (failing ? response({error: 'Something went wrong on our side.'}, 500) : response(structuredClone(notes))));
    f.location.pathname = '/';
    await f.render(); await settle();
    const wrap = f.root.querySelector('.bell-wrap'), trig = wrap.querySelector('.bell-trig'), menu = wrap.querySelector('.bell-menu');
    const line = wrap.querySelector('.bell-stale'), ticks = () => [...f.intervals.values()].forEach(tick => tick());
    assert.equal(line.hidden, true, 'nothing after a read that came back');
    f.shiftClock(61e3); ticks(); await settle();
    assert.equal(notesRead(f), 2); assert.equal(line.hidden, true, 'nor after a refresh that came back');
    failing = true;
    f.shiftClock(3 * 60e3); ticks(); await settle();
    assert.equal(notesRead(f), 3);
    assert.equal(line.hidden, false);
    assert.equal(line.textContent.trim(), 'Couldn’t refresh. Showing notifications from 3 min ago. Try again');
    assert.equal(line.getAttribute('role'), null, 'quiet: not an alert');
    assert.match(wrap.querySelector('a.notif').textContent, /Edge made a trade/, 'the list it had stays');
    assert.doesNotMatch(line.textContent, /went wrong|\(500\)|undefined|NaN/);
    // Opened later, it says how old the list is as of then. Opening reads it again (the list is minutes old,
    // first_session_frontend_cases.mjs); that read failing too keeps the list and the line.
    f.shiftClock(2 * 60e3);
    await trig.onclick({stopPropagation() {}}); await settle();
    assert.equal(menu.hidden, false);
    assert.equal(notesRead(f), 4);
    assert.match(line.textContent, /from 5 min ago\./);
    assert.match(wrap.querySelector('a.notif').textContent, /Edge made a trade/);
    // Try again that fails again keeps the line (and the list); one that comes back takes the line away.
    const press = () => line.listeners.get('click')({target: line.querySelector('[data-bell-retry]')});
    press(); await settle();
    assert.equal(notesRead(f), 5); assert.equal(line.hidden, false);
    assert.ok(wrap.querySelector('a.notif'));
    failing = false;
    notes.items.unshift({id: 4, kind: 'paused', body: '“Edge” was paused automatically.', link: '/strategies/7', created_at: ago(5e3), read: false, more: 0});
    notes.unread = 1;
    press(); await settle();
    assert.equal(notesRead(f), 6);
    assert.equal(line.hidden, true);
    assert.equal(line.textContent, '');
    assert.match(wrap.querySelector('#bell-list').textContent, /was paused automatically/);
    // Nothing reads on its own because of the failure: no extra reads beyond the ones above.
    assert.deepEqual(f.requests.filter(({path}) => path === '/api/notifications').length, 6);
  });

  // Walk 2026-10-28: with /api/notifications slow (8s), a tap on the bell did nothing until the read came back.
  test(`${name}: the bell opens while its list is still reading, says so, and fills in where it stands`, async () => {
    let answer;
    const pending = new Promise((resolve) => { answer = resolve; });
    const f = dashboard(() => pending);
    f.location.pathname = '/';
    await f.render(); await settle();
    const wrap = f.root.querySelector('.bell-wrap'), trig = wrap.querySelector('.bell-trig'), menu = wrap.querySelector('.bell-menu');
    const list = wrap.querySelector('#bell-list');
    assert.equal(typeof trig.onclick, 'function', 'the bell answers a tap before its list has come back');
    await trig.onclick({stopPropagation() {}});
    assert.equal(menu.hidden, false);
    assert.equal(list.textContent, 'Loading notifications…');
    assert.equal(f.requests.filter(({path}) => path === '/api/notifications/read').length, 0, 'nothing to mark read yet');
    answer(response({unread: 1, items: [{id: 3, kind: 'deploy', body: 'Edge made a trade.', link: '/deploy', created_at: ago(60e3), read: false, more: 0}]}));
    await settle();
    assert.match(list.querySelector('a.notif').textContent, /Edge made a trade/);
    assert.equal(menu.hidden, false);
    assert.equal(f.requests.filter(({path}) => path === '/api/notifications/read').length, 1, 'read as it shows, open');
  });

  test(`${name}: marking the list read that didn't save puts the dot back, and the next open tries once more`, async () => {
    // The dot cleared on screen and the items came back unread on the next page: the bell said nothing was new.
    const notes = () => response(structuredClone({unread: 2, items: [
      {id: 8, kind: 'paused', body: '“Edge” was paused automatically.', link: '/strategies/7', created_at: ago(30e3), read: false, more: 0},
      {id: 7, kind: 'deploy', body: 'Edge made a trade.', link: '/deploy', created_at: ago(90e3), read: false, more: 0},
      {id: 2, kind: 'share', body: 'An old notice.', link: null, created_at: ago(2 * 86400e3), read: true, more: 0}]}));
    let saves = true;
    const marks = (f) => f.requests.filter(({path}) => path === '/api/notifications/read').length;
    const f = dashboard(notes, (path) => path === '/api/notifications/read'
      ? (saves ? response({ok: true}) : response({error: 'Something went wrong on our side.'}, 500)) : undefined);
    f.location.pathname = '/';
    await f.render(); await settle();
    const wrap = f.root.querySelector('.bell-wrap'), trig = wrap.querySelector('.bell-trig'), dot = wrap.querySelector('.bell-dot');
    const open = async () => { await trig.onclick({stopPropagation() {}}); await settle(); };
    assert.equal(dot.hidden, false);
    saves = false;
    await open();
    assert.equal(marks(f), 1);
    assert.equal(dot.hidden, false);  // back, as the server still has them
    assert.equal(trig.getAttribute('aria-label'), 'Notifications, 2 unread');
    await open();  // closes; nothing tries again on its own
    assert.equal(dot.hidden, false);
    f.shiftClock(61e3); [...f.intervals.values()].forEach((tick) => tick()); await settle();  // the minute's read
    assert.equal(marks(f), 1);
    assert.equal(dot.hidden, false);

    // The next open shows them as new again and tries once more; this time it saves.
    saves = true;
    await open();
    assert.equal(wrap.querySelectorAll('.notif.unread').length, 2);
    assert.equal(marks(f), 2);
    assert.equal(dot.hidden, true); assert.equal(trig.getAttribute('aria-label'), 'Notifications');
    await open(); await open();  // nothing unread: no mark to send
    assert.equal(marks(f), 2);
    assert.equal(dot.hidden, true);
  });

  const community = (posts, older) => {
    const f = frontend({fetchImpl: (path) => {
      if (path === '/api/strategies') return response({strategies: []});
      if (path === '/api/community') return response({posts});
      if (path.startsWith('/api/community?before=')) return response({posts: older(+path.split('=')[1])});
      return response({});
    }});
    const proto = Object.getPrototypeOf(f.root);
    if (!('scrollIntoView' in proto)) proto.scrollIntoView = function (o) { this.scrolledTo = o; };
    if (!('click' in proto)) proto.click = function () { this.clicked = true; };
    return f;
  };

  test(`${name}: a bell link to a post opens the feed at it with its replies, from further back, or says it is gone`, async () => {
    // In the newest page: that post is marked, focused and its replies are opened.
    let f = community([post(12), post(11)], () => []);
    f.location.hash = '#post-11';
    await f.viewCommunity(); await settle();
    let art = f.root.querySelector('.post[data-id="11"]');
    assert.ok(art.classList.contains('post-target')); assert.equal(art.focusOptions && art.focusOptions.preventScroll, true);
    assert.equal(art.querySelector('[data-act="comments"]').clicked, true);
    assert.equal(f.root.querySelector('#feed-note').innerHTML, '');
    assert.equal(f.requests.filter(({path}) => path.startsWith('/api/community?before=')).length, 0);

    // Older than the newest page: the feed starts at it and offers the latest posts again.
    f = community([post(40), post(39)], (before) => [post(before - 1), post(before - 2)]);
    f.location.hash = '#post-5';
    await f.viewCommunity(); await settle();
    art = f.root.querySelector('.post[data-id="5"]');
    assert.ok(art && art.classList.contains('post-target'));
    const note = f.root.querySelector('#feed-note');
    assert.match(note.textContent, /Showing posts from the one you opened/);
    const latest = note.querySelector('[data-latest]');
    latest.onclick(); await settle();
    assert.equal(note.innerHTML, '');
    assert.ok(f.root.querySelector('.post[data-id="40"]'));
    assert.equal(f.requests.at(-1).path, '/api/community');

    // Gone: the feed stays, and says the post isn't there any more.
    f = community([post(40)], () => [post(3)]);
    f.location.hash = '#post-7';
    await f.viewCommunity(); await settle();
    assert.match(f.root.querySelector('#feed-note').textContent, /That post isn’t here any more/);
    assert.ok(f.root.querySelector('.post[data-id="40"]'));
  });

  test(`${name}: with more new notices than lines, the note under the list says how many older ones are new, until it closes`, async () => {
    // 47 new and 20 lines: the count said 47 while the list showed 20 new lines and "Older notices aren't listed here."
    const items = Array.from({length: 20}, (_, i) => ({id: 100 - i, kind: 'deployed', body: `Autopilot put “Idea ${i}” on paper: it passed every research gate.`,
      link: `/strategies/${100 - i}`, created_at: ago((i + 1) * 60e3), read: false, more: 0}));
    const f = dashboard({unread: 47, unread_listed: 20, older: true, items, unread_kinds: {deployed: 47}});
    f.location.pathname = '/';
    await f.render(); await settle();
    const wrap = f.root.querySelector('.bell-wrap'), trig = wrap.querySelector('.bell-trig');
    assert.equal(wrap.querySelector('.bell-new').textContent, '47 new');
    assert.equal(wrap.querySelectorAll('.notif.unread').length, 20);
    assert.equal(wrap.querySelector('.bell-older').textContent, 'Older notices aren’t listed here, including 27 new ones.');
    await trig.onclick({stopPropagation() {}}); await settle();
    await trig.onclick({stopPropagation() {}}); await settle();  // read and closed: no longer new
    assert.ok(wrap.querySelectorAll('.bell-older-new').every((e) => e.removed));
    // One new past the lines says "one"; none (or a server that doesn't say) reads as before.
    for (const [d, want] of [[{unread: 21, unread_listed: 20}, 'Older notices aren’t listed here, including 1 new one.'],
      [{unread: 20, unread_listed: 20}, 'Older notices aren’t listed here.'], [{unread: 30}, 'Older notices aren’t listed here.']]) {
      const g = dashboard({...d, older: true, items: structuredClone(items)});
      g.location.pathname = '/';
      await g.render(); await settle();
      assert.equal(g.root.querySelector('.bell-older').textContent.replace(/<[^>]*>/g, ''), want);
      g.clearTimers && g.clearTimers();
    }
  });

  // "Older notices aren't listed here" led nowhere: past the newest 20 lines an older notice couldn't be read at all.
  test(`${name}: Show older notices reads the next page by cursor, adds its lines grouped, focuses the first, says when none are left or a read failed, and marks nothing read`, async () => {
    const at = ago(3 * 86400e3);
    const newest = Array.from({length: 2}, (_, i) => ({id: 300 - i, kind: 'deployed', body: `New ${i}.`, link: `/strategies/${300 - i}`,
      created_at: ago((i + 1) * 60e3), read: false, more: 0}));
    const pages = {
      301: () => response({items: [
        {id: 150, kind: 'comment', body: 'Ana replied to your post.', link: '/community#post-9', created_at: at, read: true, more: 2, alike: true, from: [], from_more: 0},
        {id: 140, kind: 'passed', body: '“Fade” passed the paper forward gate.', link: '/strategies/6', created_at: at, read: true, more: 0}],
      older: true, next_before: 139}),
      139: () => (failing ? response({error: 'Traceback (most recent call last)'}, 500)
        : response({items: [{id: 20, kind: 'share', body: 'An old notice.', link: null, created_at: at, read: true, more: 0}], older: false, next_before: null})),
    };
    let failing = false;
    const olderReads = (f) => f.requests.filter(({path}) => path.startsWith('/api/notifications?')).map(({path}) => path);
    const marks = (f) => f.requests.filter(({path}) => path === '/api/notifications/read').length;
    const f = dashboard({unread: 3, unread_listed: 2, older: true, next_before: 301, items: newest}, (path) => {
      const m = /^\/api\/notifications\?before=(\d+)$/.exec(path);
      return m ? pages[m[1]]() : undefined;
    });
    f.location.pathname = '/';
    await f.render(); await settle();
    const wrap = f.root.querySelector('.bell-wrap'), list = wrap.querySelector('#bell-list');
    await wrap.querySelector('.bell-trig').onclick({stopPropagation() {}}); await settle();
    assert.equal(marks(f), 1, 'opening marks read, as before');
    assert.equal(list.querySelector('.bell-older').textContent, 'Older notices aren’t listed here, including 1 new one.');
    let more = list.querySelector('[data-bell-more]');
    assert.equal(more.tagName, 'BUTTON'); assert.equal(more.getAttribute('type'), 'button');
    assert.equal(more.textContent, 'Show older notices');
    assert.ok(!Object.values(more.attributes).some((v) => /301|139/.test(v)), 'the cursor is never on the page');
    assert.deepEqual(olderReads(f), [], 'nothing older is read until asked');

    const press = async () => { list.listeners.get('click')({target: list.querySelector('[data-bell-more]')}); await settle(); };
    await press();
    assert.deepEqual(olderReads(f), ['/api/notifications?before=301']);
    const lines = () => list.children.filter((n) => n.classes.has('notif'));
    assert.deepEqual(lines().map((n) => n.textContent.replace(/<[^>]*>/g, '').split('.')[0]), ['New: New 0', 'New: New 1', 'Ana replied to your post', '“Fade” passed the paper forward gate']);
    assert.match(lines()[2].innerHTML, /<span class="notif-more">and 2 more like this<\/span>/, 'grouped as the first page is');
    assert.equal(lines()[2].getAttribute('href'), '/community#post-9');
    assert.equal(list.children.at(-1).classes.has('bell-foot'), true, 'the new lines go above the control');
    assert.equal(lines()[2].focusOptions && lines()[2].focusOptions.preventScroll, true, 'focus goes to the first line added');
    more = list.querySelector('[data-bell-more]');
    assert.equal(more.textContent, 'Show older notices'); assert.equal(more.getAttribute('aria-disabled'), null);
    assert.ok(list.querySelector('.bell-older').removed, 'the note that they are not listed goes once they are');
    assert.equal(marks(f), 1, 'reading an older page marks nothing read');

    // A read that fails says so plainly, with Try again on the control; nothing reads again on its own.
    failing = true;
    await press();
    assert.equal(list.querySelector('.bell-more-note').textContent, 'Older notices didn’t load just now.');
    assert.equal(list.querySelector('.bell-more-note').getAttribute('role'), 'status');
    assert.equal(more.textContent, 'Try again');
    assert.doesNotMatch(list.querySelector('.bell-foot').textContent + more.textContent, /Traceback|500|undefined/);
    f.shiftClock(5 * 60e3); [...f.intervals.values()].forEach((tick) => tick()); f.elapse(60e3); await settle();
    assert.deepEqual(olderReads(f), ['/api/notifications?before=301', '/api/notifications?before=139']);
    failing = false;
    await press();
    assert.equal(olderReads(f).length, 3);
    const last = lines().at(-1);
    assert.match(last.textContent, /An old notice\./); assert.equal(last.tagName, 'P');
    assert.equal(last.getAttribute('tabindex'), '-1', 'a line with no page still takes the focus');
    assert.equal(last.focusOptions && last.focusOptions.preventScroll, true);
    assert.equal(list.querySelector('.bell-end').textContent, 'No older notices.');
    assert.equal(list.querySelector('[data-bell-more]'), null, 'none left: no control');
    assert.equal(marks(f), 1);
    f.clearTimers && f.clearTimers();
  });

  test(`${name}: a list painted afresh while an older page reads drops that page; a server that sends no cursor shows the note alone`, async () => {
    let answer;
    const f = dashboard({unread: 0, older: true, next_before: 77, items: [{id: 80, kind: 'deployed', body: 'Kept.', link: '/strategies/1', created_at: ago(60e3), read: true, more: 0}]},
      (path) => (path.startsWith('/api/notifications?before=') ? new Promise((resolve) => { answer = resolve; }) : undefined));
    f.location.pathname = '/';
    await f.render(); await settle();
    const wrap = f.root.querySelector('.bell-wrap'), list = wrap.querySelector('#bell-list'), trig = wrap.querySelector('.bell-trig');
    await trig.onclick({stopPropagation() {}}); await settle();
    const more = list.querySelector('[data-bell-more]');
    list.listeners.get('click')({target: more}); await settle();
    assert.equal(more.textContent, 'Loading older notices…'); assert.equal(more.getAttribute('aria-disabled'), 'true');
    list.listeners.get('click')({target: more}); await settle();  // a second press while it reads asks nothing more
    assert.equal(f.requests.filter(({path}) => path.startsWith('/api/notifications?before=')).length, 1);
    await trig.onclick({stopPropagation() {}}); f.shiftClock(20e3);
    await trig.onclick({stopPropagation() {}}); await settle();  // reopened later: the list is read and painted afresh
    answer(response({items: [{id: 70, kind: 'deployed', body: 'Stale page.', link: '/strategies/2', created_at: ago(9e6), read: true, more: 0}], older: false, next_before: null}));
    await settle();
    assert.doesNotMatch(list.children.map((n) => n.textContent).join(' '), /Stale page/);
    assert.ok(list.querySelector('[data-bell-more]'), 'the fresh list offers its own control');
    f.clearTimers && f.clearTimers();

    const g = dashboard({unread: 0, older: true, items: [{id: 80, kind: 'deployed', body: 'Kept.', link: '/strategies/1', created_at: ago(60e3), read: true, more: 0}]});
    g.location.pathname = '/';
    await g.render(); await settle();
    assert.equal(g.root.querySelector('#bell-list .bell-older').textContent, 'Older notices aren’t listed here.');
    assert.equal(g.root.querySelector('[data-bell-more]'), null);
    g.clearTimers && g.clearTimers();
  });

  test(`${name}: read in another tab, this tab's bell agrees when the tab comes back or its window is focused, by a read only`, async () => {
    // Opening the bell in one tab marks the list read on the server; the other tab said "2 unread" until its next
    // minute's tick, also right after the reader came back to it (walk 2026-10-31).
    const notes = {unread: 2, items: [
      {id: 8, kind: 'paused', body: '“Edge” was paused automatically.', link: '/strategies/7', created_at: ago(30e3), read: false, more: 0},
      {id: 7, kind: 'passed', body: '“Edge” passed the paper forward gate and is a deployment candidate.', link: '/strategies/7', created_at: ago(90e3), read: false, more: 0}]};
    const winOn = [];
    const f = frontend({notifications: () => response(structuredClone(notes)), win: {addEventListener: (n, fn) => winOn.push([n, fn])},
      fetchImpl: (path) => response(path === '/api/autopilot/status' ? {mode: 'off'} : {})});
    f.location.pathname = '/';
    await f.render(); await settle();
    const trig = f.root.querySelector('.bell-trig'), dot = f.root.querySelector('.bell-dot');
    const flip = async (hidden) => { f.document.hidden = hidden; for (const [n, fn] of f.documentListeners) if (n === 'visibilitychange') fn({}); await settle(); };
    const focus = async () => { for (const [n, fn] of winOn) if (n === 'focus') fn({}); await settle(); };
    const marks = () => f.requests.filter(({path}) => path === '/api/notifications/read').length;
    assert.equal(trig.getAttribute('aria-label'), 'Notifications, 2 unread'); assert.equal(notesRead(f), 1);
    notes.unread = 0; notes.items.forEach((n) => { n.read = true; });  // the other tab opened its bell
    await flip(true); await flip(false);
    assert.equal(notesRead(f), 1, 'not straight after the last read');
    await flip(true); f.shiftClock(2e3); await flip(false);  // a few seconds in the other tab
    assert.equal(notesRead(f), 2);
    assert.equal(dot.hidden, true); assert.equal(trig.getAttribute('aria-label'), 'Notifications');
    assert.equal(f.root.querySelectorAll('.notif.unread').length, 0);
    assert.equal(f.root.querySelector('.bell-new'), null);
    // Two windows side by side never go hidden: focusing this one reads it too, once.
    notes.unread = 1;
    notes.items.unshift({id: 9, kind: 'comment', body: 'Ana replied to your post.', link: '/community#post-4', created_at: ago(5e3), read: false, more: 0});
    f.shiftClock(5e3); await focus();
    assert.equal(notesRead(f), 2, 'a focus within 15 seconds of the last read adds none');
    f.shiftClock(11e3); await focus();
    assert.equal(notesRead(f), 3);
    assert.equal(dot.hidden, false); assert.equal(trig.getAttribute('aria-label'), 'Notifications, 1 unread');
    await focus();
    assert.equal(notesRead(f), 3, 'focus flapping adds no read');
    assert.equal(marks(), 0, 'nothing is marked read unless the reader opens the bell');
    f.clearTimers && f.clearTimers();
  });

  test(`${name}: closed by a tap outside on nothing that takes focus, the list gives focus back to the bell; Tab to a control keeps it there`, async () => {
    const f = dashboard({unread: 1, items: [
      {id: 8, kind: 'paused', body: '“Edge” was paused automatically.', link: '/strategies/7', created_at: ago(30e3), read: false, more: 0}]});
    f.location.pathname = '/';
    await f.render(); await settle();
    const wrap = f.root.querySelector('.bell-wrap'), trig = wrap.querySelector('.bell-trig'), menu = wrap.querySelector('.bell-menu');
    const open = async () => { await trig.onclick({stopPropagation() {}}); await settle(); trig.focusOptions = undefined; };  // each fake records the focus it was handed
    await open();
    wrap.dispatchEvent({type: 'focusout', relatedTarget: null});  // a tap on the page's text: focus goes to no control
    assert.equal(menu.hidden, true);
    f.elapse(0);  // once the browser has moved it
    assert.equal(trig.focusOptions && trig.focusOptions.preventScroll, true);
    // A container focused only by script (tabindex="-1", as a page's main is) is no control either.
    await open();
    const pane = f.document.createElement('div'); pane.setAttribute('tabindex', '-1');
    wrap.dispatchEvent({type: 'focusout', relatedTarget: pane});
    f.elapse(0);
    assert.equal(menu.hidden, true); assert.ok(trig.focusOptions);
    // Tab or a tap onto a control: focus stays where it went.
    await open();
    const control = f.document.createElement('button');
    wrap.dispatchEvent({type: 'focusout', relatedTarget: control});
    f.elapse(0);
    assert.equal(menu.hidden, true); assert.equal(trig.focusOptions, undefined);
    f.clearTimers && f.clearTimers();
  });

  // Walk 2026-11-01, an account with 300 notices: the bell never said how many there were, opening a list kept over 15
  // seconds marked it read twice, a research session deleted since opened "Not found", and the summary said "69 new;
  // the newest 100:" over parts adding up to 49.
  test(`${name}: with older notices past its lines the bell says how many there are and how many it shows, counting on to the last`, async () => {
    const at = ago(3 * 86400e3);
    const newest = [
      {id: 300, kind: 'comment', body: 'Ana replied to your post.', link: '/community#post-9', created_at: ago(60e3), read: false, more: 2, alike: true, from: [], from_more: 0},
      {id: 297, kind: 'deployed', body: 'New.', link: '/strategies/3', created_at: ago(120e3), read: false, more: 0}];
    const pages = {
      296: () => response({items: [{id: 296, kind: 'passed', body: 'Older.', link: '/strategies/6', created_at: at, read: true, more: 4}], older: true, next_before: 291}),
      291: () => response({items: [{id: 20, kind: 'share', body: 'Oldest.', link: null, created_at: at, read: true, more: 0}], older: false, next_before: null}),
    };
    const f = dashboard({unread: 3, unread_listed: 3, older: true, next_before: 296, total: 10, items: newest}, (path) => {
      const m = /^\/api\/notifications\?before=(\d+)$/.exec(path);
      return m ? pages[m[1]]() : undefined;
    });
    f.location.pathname = '/';
    await f.render(); await settle();
    const list = f.root.querySelector('#bell-list');
    await f.root.querySelector('.bell-trig').onclick({stopPropagation() {}}); await settle();
    // Two lines standing for four notices (a reply line with 2 more), of 10.
    assert.equal(list.querySelector('.bell-count').textContent, 'Showing the newest 4 of 10 notices.');
    assert.equal(list.querySelector('.bell-older').textContent, 'Older notices aren’t listed here.');
    const press = async () => { list.listeners.get('click')({target: list.querySelector('[data-bell-more]')}); await settle(); };
    await press();
    assert.equal(list.querySelector('.bell-count').textContent, 'Showing the newest 9 of 10 notices.');
    await press();
    assert.equal(list.querySelector('.bell-end').textContent, 'No older notices. All 10 are listed.');
    assert.equal(list.querySelector('.bell-count'), null);
    f.clearTimers && f.clearTimers();

    // Every notice listed: no count to give. A server that sends no total: the notes as before.
    for (const [d, count] of [[{total: 2, older: false}, null], [{older: true}, null], [{total: 2, older: true}, null]]) {
      const g = dashboard({unread: 0, items: structuredClone(newest).map((n) => ({...n, more: 0})), ...d});
      g.location.pathname = '/';
      await g.render(); await settle();
      assert.equal(g.root.querySelector('.bell-count'), count, JSON.stringify(d));
      g.clearTimers && g.clearTimers();
    }
  });

  test(`${name}: a notice about a research session deleted since says so as text, not a link to Not found`, async () => {
    const f = dashboard({unread: 0, items: [
      {id: 9, kind: 'research_done', body: 'Your research session on NYC daily high temperature finished. 2 of 5 ideas passed.', link: '/research/58', created_at: ago(60e3), read: true, more: 0, gone: true},
      {id: 8, kind: 'research_done', body: 'Your research session finished.', link: '/research/28', created_at: ago(120e3), read: true, more: 0, gone: false},
      {id: 7, kind: 'share', body: 'Sam shared the strategy “Drift” with you.', link: '/strategies/14', created_at: ago(180e3), read: true, more: 0, gone: true}]});
    f.location.pathname = '/';
    await f.render(); await settle();
    const wrap = f.root.querySelector('.bell-wrap');
    assert.deepEqual(wrap.querySelectorAll('a.notif').map((a) => a.getAttribute('href')), ['/research/28']);
    const plain = wrap.querySelectorAll('p.notif');
    assert.match(plain[0].innerHTML, /2 of 5 ideas passed\.<span class="notif-more">This research session has been deleted\.<\/span>/);
    assert.doesNotMatch(plain[0].innerHTML, /research\/58/);
    assert.match(plain[1].innerHTML, /<span class="notif-more">This strategy is no longer in your list\.<\/span>/);
    f.clearTimers && f.clearTimers();
  });

  test(`${name}: opening a list kept a while marks it read once, after the fresh read, and only up to the newest notice shown`, async () => {
    // The server still has them unread when the fresh read comes back: the write that went out as the list opened
    // hadn't landed. The old bell sent a second one then.
    const notes = () => response(structuredClone({unread: 2, items: [
      {id: 8, kind: 'paused', body: '“Edge” was paused automatically.', link: '/strategies/7', created_at: ago(30e3), read: false, more: 0},
      {id: 5, kind: 'comment', body: 'Ana replied to your post.', link: '/community#post-4', created_at: ago(90e3), read: false, more: 1}]}));
    const f = dashboard(notes);
    f.location.pathname = '/';
    await f.render(); await settle();
    const trig = f.root.querySelector('.bell-trig');
    const marks = () => f.requests.filter(({path}) => path === '/api/notifications/read');
    f.shiftClock(20e3);  // kept over BELL_OPEN_FRESH_MS: opening reads it again
    await trig.onclick({stopPropagation() {}}); await settle();
    assert.equal(notesRead(f), 2);
    assert.equal(marks().length, 1, 'one write per open');
    assert.deepEqual(JSON.parse(marks()[0].options.body), {upto: 8}, 'a notice that came in after this read stays new');
    assert.equal(f.root.querySelector('.bell-dot').hidden, true);
    await trig.onclick({stopPropagation() {}}); await settle();
    // Fresh: marked as it opens, once, as before.
    const g = dashboard(notes);
    g.location.pathname = '/';
    await g.render(); await settle();
    await g.root.querySelector('.bell-trig').onclick({stopPropagation() {}}); await settle();
    assert.equal(g.requests.filter(({path}) => path === '/api/notifications/read').length, 1);
    f.clearTimers && f.clearTimers(); g.clearTimers && g.clearTimers();
  });

  test(`${name}: the summary names the newest new notices its parts stand for`, async () => {
    const items = [1, 2, 3, 4].map((i) => ({id: i, kind: 'deployed', body: `Notice ${i}.`, link: null, created_at: ago(i * 60e3), read: false, more: 0}));
    const f = dashboard({items, unread: 69, unread_kinds: {deployed: 40, comment: 9}, unread_kinds_of: 100});
    f.location.pathname = '/';
    await f.render(); await settle();
    assert.equal(f.root.querySelector('.bell-sum').textContent, '69 new; the newest 49: 40 strategies put on paper · 9 comments');
    f.clearTimers && f.clearTimers();
  });
}
