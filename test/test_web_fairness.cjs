// Run with: node --test test/test_web_fairness.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');

function context() {
  const c = vm.createContext({});
  vm.runInContext(fs.readFileSync(path.join(root, 'web/decks.js'), 'utf8'), c);
  vm.runInContext(fs.readFileSync(path.join(root, 'web/fair.js'), 'utf8'), c);
  // Load the page's actual helpers and statistical code without starting the UI.
  vm.runInContext(html.slice(html.indexOf('var CARD_TYPES ='), html.indexOf('\nvar COL =')), c);
  vm.runInContext(`var P = ['a', 'b'];
    function sum(a) { return a.reduce(function (s, n) { return s + n; }, 0); }
    function f1(n) { return n.toFixed(1); }
    function sw(p) { return p; }
    function nm(p) { return p; }
    function plural(n, w) { return n + ' ' + w; }`, c);
  vm.runInContext(html.slice(html.indexOf('function cardType('), html.indexOf('// Totals over the games')), c);
  vm.runInContext(html.slice(html.indexOf('var FAIR_CHECKS ='), html.indexOf('\nfunction fairBadge(')), c);
  return c;
}

function game() {
  const stats = () => ({ hd: [4, 1, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0], hc: [2, 2, 1, 1, 0, 0], dt: [2, 1, 0, 0, 1], dc: [1, 1, 1, 0, 0, 0] });
  const nitro = () => ({ faces: [1, 1, 1, 1, 1, 1], col: [1, 1, 1, 1], penalties: [1, 1, 1, 1, 1] });
  return { mode: 'nitro', d: { first: 'a', a: stats(), b: stats(), deal: { a: [2, 1], b: [2, 1] }, nitro: { a: nitro(), b: nitro() } } };
}

test('page JavaScript parses', () => {
  for (const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
    if (!match[0].includes('application/json')) new Function(match[1]);
  }
});

test('three Nitro games cannot produce a p-value, even with many rolls', () => {
  const c = context();
  c.chiTest = () => { throw new Error('Sparse samples must not be tested'); };
  const rows = c.fairTests(Array.from({ length: 3 }, game));
  assert(rows.filter(x => x.name.startsWith('Nitro')).length === 9);
  assert(rows.every(x => x.p === null));
  assert(rows.filter(x => x.name.startsWith('Nitro')).every(x => x.pending.includes('20')));
});

test('20 contributing games enable tests; 19 do not', () => {
  const c = context();
  const find = n => c.fairTests(Array.from({ length: n }, game)).find(x => x.name === 'Wild cards in the opening hands');
  assert.equal(find(19).p, null);
  assert(Math.abs(find(20).p - 1) < 1e-8);
});

test('rare cards require informative games, not just logged games', () => {
  const c = context();
  const games = Array.from({ length: 30 }, game);
  games.slice(0, 11).forEach(m => { m.d.deal.a[1] = 0; m.d.deal.b[1] = 0; });
  const row = c.fairTests(games).find(x => x.name === 'Wild cards in the opening hands');
  assert.equal(row.games, 30);
  assert.equal(row.contributing, 19);
  assert.equal(row.p, null);
});

test('chi-square requires five expected observations in every active category', () => {
  const c = context();
  assert.equal(c.chiTest([10, 0], [5.1, 4.9]), null);
  assert.equal(c.chiTest([5, 5, 0], [5, 5, 0]), 1);
  assert(Math.abs(c.chiTest([10, 0], [5, 5]) - 0.001565402258) < 1e-9);
  const games = Array.from({ length: 20 }, game);
  games.forEach(m => { m.d.nitro.a.faces = [1, 0, 0, 0, 0, 0]; m.d.nitro.b.faces = [0, 0, 0, 0, 0, 0]; });
  const row = c.fairTests(games).find(x => x.name === 'Nitro die rolls');
  assert.equal(row.p, null);
  assert(row.pending.includes('5 expected'));
});

test('player comparisons detect a sustained opening-hand imbalance', () => {
  const c = context();
  const games = Array.from({ length: 25 }, game);
  games.forEach(m => { m.d.deal.a[1] = 3; m.d.deal.b[1] = 0; });
  const rows = c.fairTests(games);
  assert(rows.find(x => x.name === 'Wild cards in the opening hands').p < 0.001);
  assert(rows.find(x => x.name === 'Red cards in the opening hands').p > 0.99);
  assert(rows.filter(x => x.section.includes('observations')).every(x => x.p === null));
});

test('missing older Nitro penalty data does not count as observed zeroes', () => {
  const c = context();
  const games = Array.from({ length: 20 }, game);
  games.forEach(m => { delete m.d.nitro.a.penalties; delete m.d.nitro.b.penalties; });
  const row = c.fairTests(games).find(x => x.name === 'Nitro penalty sizes');
  assert.equal(row.games, 0);
  assert.equal(row.p, null);
  assert.equal(c.fairTests([]).filter(x => x.p !== null).length, 0);
});

test('Holm ignores pending rows and controls the eligible test family', () => {
  const c = context();
  const corrected = Array.from(c.holm([0.01, null, 0.04, 0.2]));
  assert.deepEqual(corrected, [0.03, null, 0.08, 0.2]);
});
