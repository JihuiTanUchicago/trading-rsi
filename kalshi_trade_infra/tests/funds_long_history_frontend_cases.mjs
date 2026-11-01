// Shared cases, run against each bundle's offline DOM/transport harness (Tau and Meridian): Funds on an account with a
// long history. Walked 2026-11-01 on a seeded account with ~1,500 closed trades over 60 days (2,190 paper-ledger
// entries, Bitcoin, weather and sports, some markets Kalshi cancelled):
// - the paper history ran two months of entries together under bare clock times, the date on some rows only: it now
//   reads by the reader's own day, a day line over each day's first entry, as Billing's statement does;
// - a cancelled market's settlement (the engine's memo "settled VOID at $0.50 a YES contract", +$4.50) read "won, paid
//   $1 a contract": it says Kalshi cancelled the market. The memo and the amount stay the engine's.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 8; i++) await flush(); };
const plain = (html) => html.replace(/<[^>]*>/g, ' ').replace(/&amp;/g, '&').replace(/⁠/g, '').replace(/ /g, ' ').replace(/\s+/g, ' ').trim();
const day = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const back = async (f) => {
  f.document.hidden = true; for (const [n, fn] of f.documentListeners) if (n === 'visibilitychange') fn({});
  f.document.hidden = false; for (const [n, fn] of f.documentListeners) if (n === 'visibilitychange') fn({});
  await settle();
};

export function fundsLongHistoryCases({name, frontend, fundsData}) {
  // Entry `id` (1..top), one every 5 hours back from an hour ago, in the reader's own clock.
  const now = new Date();
  const at = (id, top) => new Date(now.getTime() - 36e5 - (top - id) * 5 * 36e5);
  const entry = (id, top) => ({id, kind: id % 2 ? 'trade' : 'settlement', amount: id % 2 ? -1.25 : 5,
    created_at: at(id, top).toISOString(),
    memo: id % 2 ? `Bought 5 YES KXBTC15M-26OCT241200-00 @ 0.25 (#${id})` : `KXBTC15M-26OCT241200-00 settled YES (#${id})`});
  const server = (top, asked = []) => (path) => {
    if (path.startsWith('/api/funds/ledger?before=')) {
      asked.push(path);
      const before = +path.split('=')[1], ids = Array.from({length: top}, (_, k) => top - k).filter((id) => id < before);
      return response({ledger: ids.slice(0, 50).map((id) => entry(id, top)), more: ids.length > 50});
    }
    if (path === '/api/funds') {
      return response({...fundsData(), paper: {cash: 1234.56, equity: 1240.06, realized_pnl: 12.5, closed_trades: 40, open_positions: 0,
        ledger: Array.from({length: Math.min(50, top)}, (_, k) => entry(top - k, top))}});
    }
    return response({});
  };
  const body = (f) => [...f.root.querySelector('#ledger-rows').children];
  const isDay = (tr) => tr.classList.contains('ledger-day');
  const ids = (trs) => trs.filter((tr) => !isDay(tr)).map((tr) => +/#(\d+)\)/.exec(tr.innerHTML)[1]);

  test(`${name}: Funds' paper history reads by the reader's own day, a day line over each day's first entry`, async () => {
    const top = 120, f = frontend({fetchImpl: server(top)});
    f.location.pathname = '/funds';
    await f.viewFunds(); await settle();
    const rows = body(f);
    assert.deepEqual(ids(rows), Array.from({length: 50}, (_, k) => top - k), 'every entry, newest first, once');
    // Each entry sits under the day line of the reader's day it fell on, and each day has one line, over its first entry.
    let shown = null;
    const seen = [];
    for (const tr of rows) {
      if (isDay(tr)) {
        const t = tr.querySelector('time');
        assert.ok(t, 'the day line is a time, said again when the day turns');
        assert.equal(t.getAttribute('data-day'), 'day');
        assert.deepEqual(tr.children.map((td) => td.attributes['data-lab']), ['Day'], 'a phone label on the day line too');
        assert.equal(tr.children[0].attributes.colspan, '3');
        shown = day(new Date(t.getAttribute('datetime')));
        assert.ok(!seen.includes(shown), `one line for ${shown}`);
        seen.push(shown);
        continue;
      }
      const id = +/#(\d+)\)/.exec(tr.innerHTML)[1];
      assert.equal(shown, day(at(id, top)), `entry ${id} sits under its own day`);
      assert.deepEqual(tr.children.map((td) => td.attributes['data-lab']), ['When', 'What', 'Amount']);
    }
    assert.ok(isDay(rows[0]), 'the history starts with its day');
    assert.ok(seen.length >= 10, 'fifty entries 5 hours apart span ten days or more');
    const words = rows.filter(isDay).map((tr) => plain(tr.innerHTML));
    assert.equal(words[0], day(at(top, top)) === day(now) ? 'Today' : 'Yesterday');
    assert.ok(words.slice(2).every((w) => /^[A-Z][a-z]{2}, [A-Z][a-z]{2} \d{1,2}(, \d{4})?$/.test(w)), words.join(' | '));
    // No money on a day line: the history sums nothing.
    assert.ok(rows.filter(isDay).every((tr) => !/\$/.test(tr.innerHTML)));
    f.clearTimers();
  });

  test(`${name}: an older page of the paper history carries on the day it joins, and focus goes to its first entry`, async () => {
    const top = 120, asked = [], f = frontend({fetchImpl: server(top, asked)});
    f.location.pathname = '/funds';
    await f.viewFunds(); await settle();
    const lastShown = day(at(top - 49, top)), nextOlder = day(at(top - 50, top));
    await f.root.querySelector('#ledger-older').onclick({target: f.root.querySelector('#ledger-older-btn')}); await settle();
    assert.deepEqual(asked, [`/api/funds/ledger?before=${top - 49}`]);
    const rows = body(f), entries = rows.filter((tr) => !isDay(tr));
    assert.deepEqual(ids(rows), Array.from({length: 100}, (_, k) => top - k));
    // The first older entry: a day line over it only when it starts another day.
    const first = entries[50], i = rows.indexOf(first);
    assert.equal(isDay(rows[i - 1]), lastShown !== nextOlder);
    const days = rows.filter(isDay).map((tr) => day(new Date(tr.querySelector('time').getAttribute('datetime'))));
    assert.ok(days.length >= 20, 'a hundred entries 5 hours apart span twenty days or more');
    assert.equal(new Set(days).size, days.length, 'each day once, across the pages');
    // Focus on that entry, not a day line, as before the day lines.
    assert.ok(first.focused === true || f.document.activeElement === first, 'focus is on the first older entry');
    assert.equal(first.getAttribute('tabindex'), '-1');
    f.clearTimers();
  });

  test(`${name}: a day line says its words again when the reader's day turns, with no read`, async () => {
    const top = 6;
    let reads = 0;
    const inner = server(top);
    const f = frontend({fetchImpl: (path) => (path === '/api/funds' ? (++reads === 1 ? inner(path) : new Promise(() => {})) : inner(path))});
    f.location.pathname = '/funds';
    await f.viewFunds(); await settle();
    // The time's own words (the harness's markup of the row keeps what it was drawn with).
    const head = () => body(f).find(isDay).querySelector('time').textContent;
    const was = head();
    const n = new Date();
    f.shiftClock(new Date(n.getFullYear(), n.getMonth(), n.getDate() + 1, 0, 5).getTime() - n.getTime());
    await back(f);
    assert.equal(head(), was === 'Today' ? 'Yesterday' : was === 'Yesterday' ? new Intl.DateTimeFormat('en-US', {weekday: 'short', month: 'short', day: 'numeric'}).format(at(top, top)) : was);
    assert.equal(reads, 1);
    f.clearTimers();
  });

  test(`${name}: a market Kalshi cancelled reads as cancelled in the paper history, never "won, paid $1"`, async () => {
    const t = new Date(now.getTime() - 2 * 36e5).toISOString();
    const ledger = [
      {id: 9, kind: 'settlement', amount: 4.5, created_at: t, memo: 'KXNBAGAME-26OCT30GSWMIA-GSW settled VOID at $0.50 a YES contract'},
      {id: 8, kind: 'settlement', amount: 8.91, created_at: t, memo: 'KXEPLGAME-26OCT18LIVMCI-LIV settled VOID (refunded at cost)'},
      {id: 7, kind: 'settlement', amount: 10, created_at: t, memo: 'KXNBAGAME-26OCT30GSWMIA-GSW settled YES'},
      {id: 6, kind: 'settlement', amount: 0, created_at: t, memo: 'KXNBAGAME-26OCT30GSWMIA-GSW settled NO'},
    ];
    const f = frontend({fetchImpl: (path) => (path === '/api/funds' ? response({...fundsData(), paper: {cash: 1000, ledger}}) : response({}))});
    f.location.pathname = '/funds';
    await f.viewFunds(); await settle();
    const rows = body(f).filter((tr) => !isDay(tr)).map((tr) => plain(tr.innerHTML));
    assert.match(rows[0], /settled VOID at \$0\.50 a YES contract · Kalshi cancelled this market: no Yes or No result/);
    assert.match(rows[1], /settled VOID \(refunded at cost\) · Kalshi cancelled this market: no Yes or No result/);
    for (const r of rows.slice(0, 2)) assert.doesNotMatch(r, /won|lost|paid \$1|paid \$0/);
    // The engine's memo and the amount as they are.
    assert.match(rows[0], /\+\$4\.50$/);
    assert.match(rows[2], /settled YES · won, paid \$1 a contract/);
    assert.match(rows[3], /settled NO · lost, paid \$0/);
    f.clearTimers();
  });
}
