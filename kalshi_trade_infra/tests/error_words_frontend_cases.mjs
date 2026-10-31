// Shared cases, run against each bundle's harness (Tau and Meridian): an error a customer reads says what to do, in
// plain words, the same in both themes. Walk 2026-10-31 of every form and detail URL at 390x844 and 1366x900:
// - a write the edge refused as too large (a 413 page with no words of Tau's) said "Tau couldn't do that (413).
//   Reload the page and try again.", and reloading sends the same thing again; Tau's own 413 says to make it smaller;
// - the strategy page's what-if redrew its chart inside the read's try, so a reply the page couldn't draw put the
//   browser's own error text ("Cannot read properties of …", "… is not a function") in its notice.
import assert from './assert_text.mjs';
import {test} from 'node:test';

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const TOO_LARGE = 'That’s too much to send at once. Make it smaller and try again.';

export function errorWordsCases({name, frontend, strategyFrontend, detailStrategy}) {
  test(`${name}: a write the edge refused as too large says to make it smaller, as Tau's own 413 does, with no code`, async () => {
    const edge = frontend({fetchImpl: () => ({ok: false, status: 413, json: async () => { throw new SyntaxError('<html>'); }})});
    await assert.rejects(edge.api('/api/community', {body: 'x'}), (e) => e.status === 413 && e.message === TOO_LARGE);
    // Tau's own reply carries the same words, and they win as any of Tau's words do.
    const tau = frontend({fetchImpl: () => response({error: TOO_LARGE}, 413)});
    await assert.rejects(tau.api('/api/community', {body: 'x'}), (e) => e.message === TOO_LARGE);
    // Another 4xx page in front of Tau keeps its line.
    const other = frontend({fetchImpl: () => ({ok: false, status: 403, json: async () => { throw new SyntaxError('<html>'); }})});
    await assert.rejects(other.api('/api/community', {body: 'x'}), /Tau couldn't do that \(403\)\. Reload the page and try again\./);
  });

  test(`${name}: a what-if the page couldn't draw says so in plain words, never the browser's own error text`, async () => {
    const f = strategyFrontend(detailStrategy({status: 'passed'}), () => response({in_sample: {}, out_of_sample: {}, equity: [], gates: {length: 1}, passed: true}));
    await f.viewStrategy(42); await flush();
    const wi = f.root.querySelector('#whatif');
    wi.querySelector('#wf').value = '2'; wi.querySelector('#ws').value = '1';
    wi.querySelector('#wf').oninput();
    await [...f.timeouts.values()].at(-1)(); await flush();
    const said = wi.querySelector('#wflash').textContent;
    assert.doesNotMatch(said, /TypeError|is not a function|Cannot read|undefined/);
    assert.match(said, /^Something went wrong on this page\. Try again\.$/);
  });
}
