// The shared core's sports words (core.js), run on their own with the reader's zone and clock fixed: a game's teams by
// name, "today" and "tomorrow" on the reader's calendar across midnight and the Nov 1 clock change, and a trade in a game
// Kalshi cancelled. Both themes name every position, trade and settle time through them. Sports walk 2026-11-01.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';
import {coreSource} from './bundle_source.mjs';

const core = (nowIso) => {
  const context = vm.createContext({window: {}});
  if (nowIso) vm.runInContext(`Date.now = () => ${Date.parse(nowIso)};`, context);
  vm.runInContext(coreSource(), context);
  return context.window.TauCore;
};
const withZone = (zone, fn) => {
  const was = process.env.TZ;
  process.env.TZ = zone;
  try { return fn(); } finally { if (was === undefined) delete process.env.TZ; else process.env.TZ = was; }
};

test('a game position names its teams and the side it pays on in words, by league; a code not known stays as Kalshi wrote it', () => {
  const {contractWords} = core();
  assert.equal(contractWords('KXNBAGAME-26NOV01LALBOS-LAL', 'NBA winner'), 'NBA: Lakers vs Celtics on Nov 1, Lakers to win');
  assert.equal(contractWords('KXNFLGAME-26NOV02SEADAL-DAL', 'NFL winner'), 'NFL: Seahawks vs Cowboys on Nov 2, Cowboys to win');
  // CHI is the Bulls in the NBA and the Bears in the NFL.
  assert.equal(contractWords('KXNBAGAME-26OCT31NYKCHI-CHI', 'NBA winner'), 'NBA: Knicks vs Bulls on Oct 31, Bulls to win');
  assert.equal(contractWords('KXNFLGAME-26NOV01CHIGB-CHI', 'NFL winner'), 'NFL: Bears vs Packers on Nov 1, Bears to win');
  assert.equal(contractWords('KXEPLGAME-26NOV01ARSCHE-TIE', 'Premier League result'), 'Premier League: Arsenal vs Chelsea on Nov 1, a draw');
  assert.equal(contractWords('KXNHLGAME-26NOV01XYZBOS-XYZ', 'NHL winner'), 'NHL: XYZ vs Bruins on Nov 1, XYZ to win');
});

test('a settle time says "today" or "tomorrow" on the reader\'s calendar, across midnight and the Nov 1 clock change', () => withZone('America/New_York', () => {
  // 01:30 EDT on Nov 1, half an hour before the clocks go back: the day is 25 hours long.
  let c = core('2026-11-01T05:30:00Z');
  assert.equal(c.closeWords('2026-11-02T04:30:00Z', 'settles'), 'settles today, 23:30 EST');
  assert.equal(c.closeWords('2026-11-02T05:30:00Z', 'settles'), 'settles tomorrow, 00:30 EST');
  assert.equal(c.closeWords('2026-11-03T04:59:00Z', 'settles'), 'settles tomorrow, 23:59 EST');
  assert.equal(c.closeWords('2026-11-03T05:30:00Z', 'settles'), 'settles Nov 3, 00:30 EST');
  assert.equal(c.closeWords('2026-11-02T05:30:00Z', 'settleTime', 0, true), 'Tomorrow, 00:30 EST');
  assert.equal(c.closeWords('2026-11-02T05:30:00Z', 'pos'), 'Open, settles tomorrow, 00:30 EST');
  // A minute before the reader's midnight, a game at 00:01 is tomorrow; two minutes later it is today.
  c = core('2026-11-02T04:59:00Z');
  assert.equal(c.closeWords('2026-11-02T05:01:00Z', 'settles'), 'settles tomorrow, 00:01 EST');
  c = core('2026-11-02T05:00:30Z');
  assert.equal(c.closeWords('2026-11-02T05:01:00Z', 'settles'), 'settles today, 00:01 EST');
}));

test('a game tonight in New York is tomorrow in UTC: each reader gets their own day', () => {
  const at = '2026-11-02T03:30:00Z';  // 22:30 EST Nov 1
  assert.equal(withZone('America/New_York', () => core('2026-11-01T20:00:00Z').closeWords(at, 'settles')), 'settles today, 22:30 EST');
  assert.equal(withZone('UTC', () => core('2026-11-01T20:00:00Z').closeWords(at, 'settles')), 'settles tomorrow, 03:30 UTC');
  assert.equal(withZone('America/Los_Angeles', () => core('2026-11-01T20:00:00Z').closeWords(at, 'settles')), 'settles today, 19:30 PST');
});

test('a trade held to settlement that paid between nothing and $1 reads as cancelled by Kalshi, never "settled"', () => {
  const {exitWord} = core();
  assert.equal(exitWord('settled', 0.5), 'cancelled by Kalshi');
  assert.equal(exitWord('settled', '0.3600'), 'cancelled by Kalshi');
  assert.equal(exitWord('settled', 1), 'settled');
  assert.equal(exitWord('settled', 0), 'settled');
  assert.equal(exitWord('settled'), 'settled');
  assert.equal(exitWord('take_profit', 0.5), 'take-profit');
});

test('Meridian on a phone puts an opened research form before the session at work, so Start is in view', () => {
  const css = fs.readFileSync(new URL('../app/static/app.meridian.css', import.meta.url), 'utf8');
  const block = css.slice(css.indexOf('@container research-work (max-width: 759px)'));
  const body = block.slice(0, block.indexOf('\n}\n'));
  assert.match(body, /\.research-page:has\(> \.research-create\[open\]\) > \.research-work \{ order: 1; \}/);
});
