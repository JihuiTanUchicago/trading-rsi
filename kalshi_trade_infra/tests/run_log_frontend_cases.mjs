// Shared cases, run against each bundle's research log (Tau and Meridian). From the session-log walk of 2026-11-01:
// - a log over two days read "23:53 … 00:05 … 01:02" with nothing to say which day each line was, and a failed
//   session from yesterday read "16:03" as if today: a log that isn't all from today starts each day with its day
//   ("Yesterday", "Sat, Oct 31"), in dayAt's words, said again when the day turns (dayWatch), and a log all from
//   today gains its "Yesterday" past the reader's midnight;
// - the log never said whether it showed every entry or only the latest: the line over it says which;
// - a copy of the log ran a kind's label into its words ("PAUSEDPaused by you.", "APPROVEDPlausible: ...").
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const pastLocalMidnight = () => { const n = new Date(); return new Date(n.getFullYear(), n.getMonth(), n.getDate() + 1, 0, 5).getTime() - n.getTime(); };
const back = async (f) => {
  f.document.hidden = true; for (const [n, fn] of f.documentListeners) if (n === 'visibilitychange') fn({});
  f.document.hidden = false; for (const [n, fn] of f.documentListeners) if (n === 'visibilitychange') fn({});
  await settle();
};
const dayNoon = (back) => { const n = new Date(); return new Date(n.getFullYear(), n.getMonth(), n.getDate() - back, 12).toISOString(); };
const weekday = (iso) => new Intl.DateTimeFormat(undefined, {weekday: 'short', month: 'short', day: 'numeric'}).format(new Date(iso));
// The day lines in the log, in order: [the row's own clock time (its datetime), the line's words].
const dayLines = (log) => log.children.flatMap((li) => (li.children || []).filter((c) => !c.removed && c.classList && c.classList.contains('log-day'))
  .map((d) => [(li.children.find((c) => c.tagName === 'TIME') || {getAttribute: () => null}).getAttribute('datetime'), d.querySelector('time').textContent]));

export function runLogCases({name, frontend, longFeed, runEvent}) {
  const at = (id, kind, message, created_at, data = {}) => ({...runEvent(id, kind, message, data), created_at});
  const page = (events, status = 'succeeded') => frontend({fetchImpl: (path) => (path.startsWith('/api/runs/9')
    ? response({run: {id: 9, prompt: 'Idea', market: 'btc15m', created_at: events[0].created_at, started_at: events[0].created_at,
      finished_at: status === 'running' ? null : events.at(-1).created_at, status}, events: path.includes('after=0') ? events : [], strategies: []})
    : response({}))});

  test(`${name}: a session log over two days starts each day with its day, and says them again past the reader's midnight`, async () => {
    const now = new Date(Date.now() - 120000).toISOString();
    if (new Date(now).toDateString() !== new Date().toDateString()) return;  // run in the two minutes after midnight
    const y1 = dayNoon(1), y2 = new Date(Date.parse(y1) + 60000).toISOString();
    const f = page([at(1, 'step', 'Research started', y1), at(2, 'step', 'Loading data', y2), at(3, 'done', '0 of 1 ideas passed every check.', now)]);
    await f.viewRun(9); await settle(); f.clearTimers();
    const log = f.root.querySelector('#log');
    assert.deepEqual(dayLines(log), [[y1, 'Yesterday'], [now, 'Today']]);
    // Every entry is here, and the line over the log says so.
    const span = f.root.querySelector('.log-span');
    assert.equal(span.textContent, 'Showing every entry since the session started.'); assert.equal(span.hidden, false);
    f.shiftClock(pastLocalMidnight());
    await back(f);
    assert.deepEqual(dayLines(log), [[y1, weekday(y1)], [now, 'Yesterday']]);
  });

  test(`${name}: a session log all from today has no day line, and gains "Yesterday" past the reader's midnight`, async () => {
    const t1 = new Date(Date.now() - 120000).toISOString(), t2 = new Date(Date.now() - 60000).toISOString();
    if (new Date(t1).toDateString() !== new Date().toDateString()) return;
    const f = page([at(1, 'step', 'Research started', t1), at(2, 'step', 'Cancelled.', t2)], 'cancelled');
    await f.viewRun(9); await settle(); f.clearTimers();
    const log = f.root.querySelector('#log');
    assert.deepEqual(dayLines(log), []);
    f.shiftClock(pastLocalMidnight());
    await back(f);
    assert.deepEqual(dayLines(log), [[t1, 'Yesterday']]);
  });

  test(`${name}: the line over a long log says it shows only the latest entries until every one is shown`, async () => {
    const f = frontend(), {stream, log, span} = longFeed(f);
    stream.push(Array.from({length: 120}, (_, i) => runEvent(i + 101, 'step', `Step ${i + 101}`)));
    stream.older(101, async () => ({events: [runEvent(100, 'step', 'Step 100')], more: false}));
    assert.equal(span.textContent, 'Showing the latest 60 entries; earlier ones open at the top.'); assert.equal(span.hidden, false);
    const press = async () => { const b = log.children[0].querySelector('button'); await b.onclick({currentTarget: b}); };
    await press();
    assert.equal(span.textContent, 'Showing the latest 120 entries; earlier ones open at the top.');  // the server holds more
    await press();
    assert.equal(log.querySelector('.log-earlier'), null);
    assert.equal(span.textContent, 'Showing every entry since the session started.');
  });

  test(`${name}: the line over a cut log says how many entries or lines there are in all`, async () => {
    // "Showing the latest 60 entries" gave no total (walk 2026-11-01). Every line read, the first paint holding the oldest
    // rows: the entries in all. Earlier steps on the server: the lines the session has logged (the server's earlier.lines,
    // plus each poll's), since an entry groups lines the page hasn't read.
    let f = frontend(), {stream, span} = longFeed(f);
    stream.push(Array.from({length: 120}, (_, i) => runEvent(i + 1, 'step', `Step ${i + 1}`)));
    assert.equal(span.textContent, 'Showing the latest 60 of 120 entries; earlier ones open at the top.');
    ({stream, span} = longFeed(f = frontend()));
    stream.push(Array.from({length: 120}, (_, i) => runEvent(i + 2901, 'step', `Step ${i + 2901}`)));
    stream.older(2901, async () => ({events: [], more: false}), 3020);
    assert.equal(span.textContent, 'Showing the latest 60 entries; earlier ones open at the top. The session has logged 3,020 lines in all.');
    stream.push([runEvent(3021, 'step', 'Step 3021'), runEvent(3022, 'thinking', 'and so on', {block: 1})]);  // a poll's lines
    assert.match(span.textContent, /The session has logged 3,022 lines in all\.$/);
    ({stream, span} = longFeed(f = frontend()));
    stream.push(Array.from({length: 120}, (_, i) => runEvent(i + 101, 'step', `Step ${i + 101}`)));
    stream.older(101, async () => ({events: [], more: false}));  // no count from the server: the line as before
    assert.equal(span.textContent, 'Showing the latest 60 entries; earlier ones open at the top.');
  });

  test(`${name}: a page of earlier steps from another day takes its own day line, and one from the same day moves it up`, async () => {
    const f = frontend(), {stream, log} = longFeed(f);
    const d2 = '2026-10-17T12:00:00Z', d1 = '2026-10-16T12:00:00Z';
    stream.push([at(5, 'step', 'Step 5', d2), at(6, 'step', 'Step 6', d2)]);
    assert.equal(dayLines(log).length, 1); assert.equal(dayLines(log)[0][0], d2);
    const pages = {5: {events: [at(4, 'step', 'Step 4', d2)], more: true}, 4: {events: [at(3, 'step', 'Step 3', d1)], more: false}};
    stream.older(5, async (before) => pages[before]);
    const press = async () => { const b = log.querySelector('.log-earlier').querySelector('button'); await b.onclick({currentTarget: b}); };
    await press();  // same day: one line, now on Step 4
    assert.deepEqual(dayLines(log).map(([t]) => t), [d2]);
    assert.equal(dayLines(log).length, 1);
    assert.match(log.children.find((li) => (li.children || []).some((c) => !c.removed && c.classList && c.classList.contains('log-day'))).innerHTML, /Step 4/);
    await press();  // the day before: a line of its own above
    assert.deepEqual(dayLines(log).map(([t]) => t), [d1, d2]);
  });

  test(`${name}: a copy of the log keeps a space between a kind's label and its words`, () => {
    const f = frontend(), {stream, log} = longFeed(f);
    stream.push([runEvent(1, 'paused', 'Paused by you.'), runEvent(2, 'challenger', 'Challenger approved - Loose idea: plausible.', {title: 'Loose idea', decision: 'approve'}),
      runEvent(3, 'hypothesis', 'Card idea', {thesis: 'T'}), runEvent(4, 'challenger', 'Challenger approved - Card idea: plausible enough.', {title: 'Card idea', decision: 'approve'})]);
    assert.match(log.children[0].innerHTML, /<\/span> Paused by you\./);
    assert.match(log.children[1].innerHTML, /Skeptic’s review<\/span> approved/);
    assert.match(log.children[2].querySelector('[data-review]').innerHTML, /<\/span> Plausible enough\./);
  });
}
