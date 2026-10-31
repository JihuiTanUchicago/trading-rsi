// Shared cases, run against each bundle's offline harness (Tau and Meridian): a day the app shows says whose day it
// is, and a line lands on the day its own time fell on. From the time walk of 2026-10-31 (America/Chicago,
// Asia/Tokyo and Europe/Berlin, the day after Europe's clock change):
// - Billing's statement days were the server's, counted with today's offset from UTC alone, so after a clock change a
//   charge at 00:30 summer time sat under the day before, its own clock reading the other day. Each line now goes under
//   the reader's day its time fell on, and the statement says its days are the reader's, as the receipt's are UTC.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const flush = () => new Promise((resolve) => setImmediate(resolve));
// The reader's own day an instant fell on, as the harness's clock and zone have it.
const ownDay = (iso) => { const d = new Date(iso); return [d.getFullYear(), d.getMonth() + 1, d.getDate()].map((n) => String(n).padStart(2, '0')).join('-'); };
const dayBefore = (day) => new Date(Date.parse(day + 'T12:00:00Z') - 864e5).toISOString().slice(0, 10);
const heading = (day) => { const [y, m, d] = day.split('-').map(Number), at = new Date(y, m - 1, d);
  return at.toDateString() === new Date().toDateString() ? 'Today' : at.toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'}); };

export function timeTruthCases({name, billingPage, cardedBilling}) {
  test(`${name}: Billing's statement puts each line under the reader's day its time fell on, and says its days are the reader's`, async () => {
    // The server filed the newer lines a day early (as its one offset did across a clock change); each line's own time
    // is what the reader's day goes by. The older line it filed right.
    const late = '2026-01-15T12:30:00Z', later = '2026-01-15T12:40:00Z', early = '2026-01-12T12:00:00Z';
    const wrong = dayBefore(ownDay(late));
    const f = billingPage(cardedBilling({
      math: {days: 30, start: 10, bought: 0, purchases: 0, other: 0, usage: 0.5, end: 9.5},
      ledger: [
        {kind: 'usage', amount: -0.3, memo: 'Chat', item: 'Chat', calls: 1, created_at: later, first_at: later, day: wrong},
        {kind: 'usage', amount: -0.1, memo: 'Chat', item: 'Chat', calls: 1, created_at: late, first_at: late, day: wrong},
        {kind: 'usage', amount: -0.1, memo: 'Chat', item: 'Chat', calls: 1, created_at: early, first_at: early, day: ownDay(early)},
      ]}));
    await f.viewBilling(); await flush();
    const days = f.root.querySelectorAll('.t-ledger tr.ledger-day b').map((b) => b.textContent);
    assert.deepEqual(days, [heading(ownDay(late)), heading(ownDay(early))]);
    // Both of the day's lines sit under it, and its sum counts both.
    assert.match(f.root.querySelector('.t-ledger tr.ledger-day').innerHTML, /\$0\.40 used/);
    // The statement says whose days and clock these are, beside the receipt's UTC.
    assert.match(f.root.querySelector('.ledger-key').textContent,
      /^Last 30 days, newest first\. Its days and times are in your time zone; the receipt’s are in UTC\. /);
  });
}
