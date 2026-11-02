// Shared cases, run against each bundle's offline harness (Tau and Meridian): each strategy has one verdict, said in the
// same words and tone everywhere a customer sees it. Walked 2026-11-02 with a strategy in every verdict state, both
// themes: a strategy stored "passed" whose own validation was inconclusive read PASSED on its page's stamp right above a
// glance saying it didn't pass validation, Passed on its Strategies row, its Community card and its session row, and
// "Next rung, paper: nothing needed" on its ladder; a missed-final or held-back pass's ladder said "nothing needed" too
// beside a stamp saying it wasn't cleared; an unjudged idea read "Testing" on its session row and "Not judged" on its
// Strategies row; and a missed final test was grey on the session row beside a Void-toned stamp elsewhere.
//
// Table-driven: each state is the answer the server gives for it (the strategy page's, with `verdict` as
// services.verdicts sets it; tests/test_verdict_everywhere_db.py holds the server's answers to the same table), and
// every place that shows a verdict is held to the state's one stamp, tone and phrase.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
// A stamp's tone: its classes, without the spacing a bare class list carries ("mk ").
const tone = (cls) => String(cls || '').trim().split(/\s+/).join(' ');
const plain = (html) => html.replace(/<[^>]+>/g, '').replace(/&nbsp;| /g, ' ').replace(/\s+/g, ' ').trim();

const AT = '2026-10-25T10:00:00Z';
const paper = {id: 1, mode: 'paper', status: 'running', closed_trades: 8, realized_pnl: 3, open_positions: 0, stake: 5, created_at: '2026-10-20T08:00:00Z'};
const demo = {...paper, id: 2, mode: 'demo'};
const VAL = (classification) => ({classification, n_trades: 48, mean_net_return: 0.012, p_value: 0.18});
const HOLD_OK = {classification: 'forward_validated', n_trades: 44, mean_net_return: 0.03, p_value: 0.04, consumed: true};
const HOLD_BAD = {classification: 'rejected', n_trades: 44, mean_net_return: -0.003, p_value: 0.48, consumed: true};
const probation = {min_sample: 40, classification: 'inconclusive', validation: VAL('inconclusive'), holdout: null, paper: null, promoted: false};
const candidate = {min_sample: 40, classification: 'deployment_candidate', validation: VAL('statistically_supported'), holdout: HOLD_OK, paper: null, promoted: true};
const MISS = [{gate: 'significance', label: 'The edge could be luck', measured: 'p 0.18', needed: '0.0125', next: 'Try again.'}];
const HOLD_MISS = [{gate: 'significance', label: 'The edge could be luck on the locked holdout', measured: 'p 0.48', needed: '0.10', next: 'Try again.'}];
const DIVERS = [{gate: 'diversification', label: 'Too similar to a strategy you already run', measured: 'a 0.81 correlation', needed: '0.70 or less', next: ''}];

// Each verdict state: the page's answer, and its one stamp (word and the stamp's classes), the phrase a sentence says
// it in, and the glance's lead ('' where the page has no glance). Tone: a pass is "mk on"; a Void-toned verdict "mk";
// a quiet one "mk quiet".
export const VERDICT_STATES = [
  {key: 'draft', s: {status: 'pending', experiment: null}, word: 'Not judged', cls: 'mk quiet', phrase: 'not judged', lead: ''},
  {key: 'running research', s: {status: 'queued', experiment: null}, word: 'Testing', cls: 'mk quiet', phrase: 'testing', lead: ''},
  {key: 'rejected', s: {status: 'rejected', why_failed: MISS, experiment: {...probation, classification: 'rejected', validation: VAL('rejected')}},
    word: 'Rejected', cls: 'mk quiet', phrase: 'rejected', lead: 'Rejected.'},
  {key: 'inconclusive', s: {status: 'failed', why_failed: MISS, experiment: probation}, word: 'Void', cls: 'mk', phrase: 'void', lead: 'Void.'},
  {key: 'void, in its paper forward test', s: {status: 'failed', why_failed: MISS, experiment: probation, deployments: [paper]},
    word: 'Void', cls: 'mk', phrase: 'void', lead: 'Void.'},
  {key: 'passed, then its paper forward test', s: {status: 'passed', verdict: 'inconclusive', why_failed: [], experiment: probation, deployments: [paper]},
    word: 'Validation inconclusive', cls: 'mk', phrase: 'validation inconclusive', lead: 'Validation inconclusive.'},
  {key: 'passed, validation inconclusive', s: {status: 'passed', verdict: 'inconclusive', why_failed: [], experiment: probation},
    word: 'Validation inconclusive', cls: 'mk', phrase: 'validation inconclusive', lead: 'Validation inconclusive.'},
  {key: 'passed, validation rejected', s: {status: 'passed', verdict: 'missed_validation', why_failed: [], experiment: {...probation, classification: 'rejected', validation: VAL('rejected')}},
    word: 'Missed validation', cls: 'mk quiet', phrase: 'missed validation', lead: 'Missed validation.'},
  {key: 'passed validation', s: {status: 'passed', why_failed: [], experiment: {...probation, classification: 'statistically_supported', validation: VAL('statistically_supported')}},
    word: 'Passed', cls: 'mk on', phrase: 'passed', lead: 'Passed validation.'},
  {key: 'deployment candidate', s: {status: 'passed', why_failed: [], experiment: candidate}, word: 'Passed', cls: 'mk on', phrase: 'passed', lead: 'Passed.'},
  {key: 'missed final test', s: {status: 'passed', verdict: 'missed_final', why_failed: HOLD_MISS, experiment: {...candidate, classification: 'rejected', holdout: HOLD_BAD, promoted: false}},
    word: 'Missed final test', cls: 'mk', phrase: 'missed the final test', lead: 'Missed the final test.'},
  {key: 'held back by diversification', s: {status: 'passed', verdict: 'held_back', why_failed: DIVERS, experiment: {...candidate, classification: 'forward_validated', promoted: false}},
    word: 'Held back', cls: 'mk quiet', phrase: 'held back', lead: 'Held back.'},
  {key: 'held back after passing on paper', s: {status: 'failed', verdict: 'held_back', why_failed: DIVERS, deployments: [paper],
    experiment: {...probation, classification: 'forward_validated', paper: {classification: 'forward_validated', n_trades: 40}}},
  word: 'Held back', cls: 'mk quiet', phrase: 'held back', lead: 'Held back.'},
  {key: 'forward test passed', s: {status: 'passed', why_failed: [], deployments: [paper],
    experiment: {...probation, classification: 'deployment_candidate', paper: {classification: 'deployment_candidate', n_trades: 40}, promoted: true}},
  word: 'Passed', cls: 'mk on', phrase: 'passed', lead: 'Passed on paper.'},
  {key: 'forward test failed', s: {status: 'failed', why_failed: MISS, deployments: [paper],
    experiment: {...probation, paper: {classification: 'inconclusive', n_trades: 40, verdict_at: AT}}},
  word: 'Void', cls: 'mk', phrase: 'void', lead: 'Void.'},
  {key: 'live-ready', s: {status: 'passed', why_failed: [], experiment: candidate, deployments: [paper, demo]}, word: 'Passed', cls: 'mk on', phrase: 'passed', lead: 'Passed.'},
];
// The tone of a session row's stamp, on that list's palette.
const RUN_TONE = {'mk on': 'pass', mk: 'void', 'mk quiet': 'rej'};

export function verdictEverywhereCases({name, frontend, detailStrategy, strategyFrontend}) {
  const card = (st, i) => ({id: 70 + i, title: 'State ' + i, market: 'high_ny', direction: 'custom', status: st.s.status, ...(st.s.verdict ? {verdict: st.s.verdict} : {})});

  test(`${name}: every verdict state's Strategies row, page stamp, glance and ladder say one verdict`, async () => {
    const f0 = frontend({fetchImpl: () => response({})}), rungs = [];
    for (const [i, st] of VERDICT_STATES.entries()) {
      // Strategies row: the card the list sends (its status and verdict only, no experiment block).
      const row = f0.strategySlip(detailStrategy({...card(st, i), experiment: undefined, out_of_sample: {pnl: 3.1, trades: 40, win_rate: 0.6}}), null, {});
      const [, rowCls, rowWord] = row.match(/data-lab="Status"><span class="(mk[^"]*)">([^<]*)<\/span>/) || [];
      assert.deepEqual([rowWord, tone(rowCls)], [st.word, st.cls], `${st.key}: Strategies row`);
      // The strategy page.
      const f = strategyFrontend(detailStrategy({deployments: [], ...st.s}), () => response({}));
      await f.viewStrategy(42); await settle();
      const head = f.root.querySelector('.paper-byline') || f.root.querySelector('.strategy-pagehead');
      const [, pageCls, pageWord] = head.innerHTML.match(/<span class="(mk[^"]*)">([^<]*)<\/span>/) || [];
      assert.deepEqual([pageWord, tone(pageCls)], [st.word, st.cls], `${st.key}: page stamp`);
      const glance = f.root.querySelector('.verdict-glance');
      const lead = glance ? plain((glance.innerHTML.match(/<b>([^<]*)<\/b>/) || [])[1] || '') : '';
      assert.equal(lead, st.lead, `${st.key}: glance`);
      // The stamp never says Passed above a glance that says it didn't pass validation, and a stamp that isn't Passed
      // never sits over a glance that says it passed.
      if (glance) {
        if (st.word === 'Passed') assert.doesNotMatch(plain(glance.innerHTML), /didn’t pass|fell short on its first test on unseen data, so/, st.key);
        else assert.doesNotMatch(lead, /^Passed/, st.key);
      }
      // Its ladder's paper rung says "nothing needed" only for a pass the gatekeeper cleared.
      const ladder = f.root.querySelector('#ladder-body');
      const rung = ladder ? plain(ladder.innerHTML) : '';
      if (/Next rung, paper:/.test(rung)) {
        rungs.push(st.key);
        assert.equal(/nothing needed/.test(rung), st.word === 'Passed', `${st.key}: ladder ${rung}`);
      }
      f.clearTimers && f.clearTimers();
    }
    // Every pass not on paper yet showed its paper rung, cleared or not.
    assert.deepEqual(rungs.filter((k) => VERDICT_STATES.find((st) => st.key === k).s.status === 'passed'),
      ['passed, validation inconclusive', 'passed, validation rejected', 'passed validation', 'deployment candidate', 'missed final test', 'held back by diversification']);
  });

  test(`${name}: every verdict state's Community card, picker, session row and Home's not-cleared line say the same verdict`, async () => {
    // Community: the card the server froze (status and verdict), and the picker's phrase from the Strategies list.
    const mine = VERDICT_STATES.map((st, i) => card(st, i));
    const f = frontend({fetchImpl: (path) => path === '/api/strategies' ? response({strategies: mine})
      : path.startsWith('/api/community') ? response({posts: []}) : response({})});
    f.location.pathname = '/community';
    await f.viewCommunity(); await settle();
    const options = f.root.querySelector('#post-strat').querySelectorAll('option').slice(1).map((o) => o.textContent);
    for (const [i, st] of VERDICT_STATES.entries()) {
      const html = f.postSnapshot({kind: 'strategy', ...card(st, i), pnl: 3.1, trades: 40, win_rate: 0.6, days: 9}, '');
      const [, cls, word] = html.match(/<\/b><span class="(mk[^"]*)">([^<]*)<\/span>/) || [];
      assert.deepEqual([word, tone(cls)], [st.word, st.cls], `${st.key}: Community card`);
      assert.equal(options[i], `State ${i} (${st.phrase})`, `${st.key}: Community picker`);
    }
    // A shared card whose strategy was deleted since keeps the stamp it was shared with.
    const gone = f.postSnapshot({kind: 'strategy', id: 99, title: 'Gone', market: 'high_ny', status: 'passed', verdict: 'inconclusive', gone: true}, '');
    assert.match(gone, /<span class="mk">Validation inconclusive<\/span>/);
    f.clearTimers && f.clearTimers();

    // A session's rows: each idea's stamp in the same words, on the list's palette in the same tone.
    const strategies = VERDICT_STATES.map((st, i) => ({...card(st, i), ...(st.s.verdict === 'missed_final' ? {later_missed: HOLD_MISS} : {}),
      ...(st.s.verdict === 'held_back' ? {later_missed: DIVERS} : {})}));
    const g = frontend({fetchImpl: (path) => path.startsWith('/api/runs/9')
      ? response({run: {id: 9, prompt: 'Idea', market: 'high_ny', created_at: AT, started_at: AT, status: 'succeeded', hypotheses_tried: strategies.length, strategies_passed: 4},
        events: [], strategies})
      : response({})});
    await g.viewRun(9); await settle();
    // Meridian's table shows its first few until "Show all" (Tau lists every idea under a details of its own).
    const all = g.root.querySelector('[data-allstrats]');
    if (all) { g.root.querySelector('#run-artifacts').listeners.get('click')({target: all}); await settle(); }
    const list = (g.root.querySelector('#runideas') || g.root.querySelector('#run-artifacts')).innerHTML;
    for (const [i, st] of VERDICT_STATES.entries()) {
      const tau = list.match(new RegExp(`<span class="hx-stamp ([a-z]*)">([^<]*)</span><button type="button" class="ri-title" data-goto="${70 + i}">`));
      if (tau) assert.deepEqual([tau[2], tau[1]], [st.word, RUN_TONE[st.cls]], `${st.key}: session row`);
      else {
        const rowHtml = list.split('<tr').find((r) => r.includes(`href="/strategies/${70 + i}"`)) || '';
        const [, cls, word] = rowHtml.match(/data-lab="Status"><span class="(mk[^"]*)">([^<]*)<\/span>/) || [];
        assert.deepEqual([word, tone(cls)], [st.word, st.cls], `${st.key}: session row`);
      }
      // A pass its stamp says wasn't cleared is never offered as one to deploy.
      if (st.s.status === 'passed' && st.word !== 'Passed') {
        const at = list.indexOf(`/strategies/${70 + i}"`) >= 0 ? list.indexOf(`/strategies/${70 + i}"`) : list.indexOf(`data-goto="${70 + i}"`);
        assert.doesNotMatch(list.slice(at, at + 700).split(/<li>|<tr/)[0], /Open it to deploy on paper/, `${st.key}: session row`);
      }
    }
    g.clearTimers && g.clearTimers();

    // Home's new-results strip names each pass a later check or its validation left uncleared in its stamp's words.
    const h = frontend({fetchImpl: () => response({})});
    const fell = VERDICT_STATES.filter((st) => st.s.status === 'passed' && st.s.verdict);
    for (const st of fell) {
      const since = {at: '2026-10-19T08:00:00+00:00', first: false, books: {}, autopilot: {}, runs: {},
        passed: {n: 2, items: [{id: 9, title: 'Rain'}], not_cleared: [{id: 6, title: 'Gust', verdict: st.s.verdict}], not_cleared_n: 1}};
      assert.match(h.sinceStrip({since, deployments: []}, '#dash-deps'), new RegExp(`Not cleared to trade: <a href="/strategies/6">Gust</a> \\(${st.phrase}\\)\\.`), st.key);
    }
    h.clearTimers && h.clearTimers();
  });
}
