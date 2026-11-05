// Run with: node --test test/test_web_r8p.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const index = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
const replay = fs.readFileSync(path.join(root, 'web/replay.html'), 'utf8');

test('R8P statistics stay separate from the three constituent modes', () => {
  const nodes = {}, charts = [];
  const c = vm.createContext({
    P: ['a', 'b'],
    $: id => nodes[id] || (nodes[id] = { style: {} }),
    tile: (name, value) => `${name}: ${value};`,
    pairTile: (name, a, b) => `${name}: ${a}/${b};`,
    pct: value => value,
    share: (n, d) => d ? n / d : 0,
    nm: p => p,
    sw: () => '',
    stackBy: (...args) => charts.push(args)
  });
  vm.runInContext(index.slice(index.indexOf('function renderVolcano(R)'), index.indexOf('function renderCrazy(R)')), c);
  const player = { sw: 2, dr: 1, p1: 3, cx: 1, back: 8 };
  const stacks = { s4: 1, s2: 2, ww: 1, pen: 3, big: 12 };
  const d = { r8p: {
    vol: { r: 10, e: 7, x: 6.125, a: [4, 12, 1], b: [3, 9, 0] },
    storm: { a: player, b: player }, tornado: { a: stacks, b: stacks }
  } };
  const selection = { ms: [{ mode: 'r8p', w: 'a', d }, { mode: 'r8p', w: 'b', d }] };
  assert.equal(c.renderR8P(selection), true);
  assert.match(nodes['r8-tiles'].innerHTML, /Eruptions: 14/);
  assert.match(nodes['r8-tiles'].innerHTML, /Volcano rolls: 20/);
  assert.match(nodes['r8-tiles'].innerHTML, /recorded chances: 12.3/);
  assert.match(nodes['r8-stats'].innerHTML, /Biggest penalty drawn<\/td><td class="num">12/);
  assert.match(nodes['r8-stats'].innerHTML, /Swaps<\/td><td class="num">4/);
  assert.equal(charts[0][2][5].a, 1);
  assert.equal(charts[0][2][5].b, 1);
  for (const mode of ['Volcano', 'Storm', 'Tornado']) assert.equal(c['render' + mode](selection), false);
  assert.equal(c.renderR8P({ ms: [] }), false);
  assert.equal(nodes['sp-r8p'].style.display, 'none');
});

test('R8P replay math includes pressure increases and eruption rolls', () => {
  let toggle;
  const table = {};
  const details = {
    open: true,
    getAttribute: () => null,
    setAttribute: () => {},
    addEventListener: (_, callback) => { toggle = callback; },
    querySelector: () => table
  };
  const c = vm.createContext({ document: { createElement: () => details } });
  vm.runInContext(replay.slice(replay.indexOf('var COLOR_NAMES ='), replay.indexOf('function describe(e)')), c);
  vm.runInContext(replay.slice(replay.indexOf('function hex64(v)'), replay.indexOf('async function checkFairness(game)')), c);
  c.renderMath({ _steps: [
    { kind: 'rt', op: ['rt', 1, 350, 850] },
    { kind: 'rv', op: ['rv', 850, true] },
    { kind: 'm', op: ['m', ['classic', 'storm', 'tornado', 'r8p'], 'r8p'] }
  ] }, 'players');
  assert.match(details.innerHTML, /3 random picks/);
  toggle();
  assert.match(table.innerHTML, /Pressure 8.50%/);
  assert.match(table.innerHTML, /rise \+3.50 percentage points/);
  assert.match(table.innerHTML, /Eruption · reset to 5%/);
  assert.match(table.innerHTML, /eruption chance 8.50%/);
  assert.match(table.innerHTML, /random game mode/);
  assert.doesNotMatch(table.innerHTML, /classic, storm/);
});

test('replay rebuild preserves hands across Storm effects and eruptions', () => {
  const c = vm.createContext({});
  vm.runInContext(replay.slice(replay.indexOf('var COLOR_NAMES ='), replay.indexOf('function describe(e)')), c);
  const states = c.rebuild([
    { k: 'deal', h: { a: ['r_swap', 'b_1'], b: ['r_drop', 'g_2'] }, fc: 'r_1', first: 'a' },
    { k: 'play', p: 'a', c: 'r_swap' },
    { k: 'swap', p: 'a', vs: 'b', hs: { a: ['r_drop', 'g_2'], b: ['b_1'] } },
    { k: 'play', p: 'a', c: 'r_drop' },
    { k: 'drop', p: 'a', cs: ['r_9'] },
    { k: 'draw', p: 'b', cs: ['g_1'] },
    { k: 'erupt', p: 'b', cs: ['r_2', 'b_3', 'g_4'] }
  ]);
  assert.deepEqual(Array.from(states.at(-1).hands.a), ['r_9']);
  assert.deepEqual(Array.from(states.at(-1).hands.b), ['b_1', 'g_1', 'r_2', 'b_3', 'g_4']);
  assert.deepEqual(Array.from(states[0].hands.a), ['r_swap', 'b_1']);
});

test('replay JavaScript parses', () => {
  for (const match of replay.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new Function(match[1]);
});

test('R8P eruption labels have no cap while Volcano keeps its limit', () => {
  const c = vm.createContext({});
  vm.runInContext(replay.slice(replay.indexOf('var COLOR_NAMES ='), replay.indexOf('function show(step)')), c);
  c.G = { mode: 'r8p', players: { a: 'Alice' } };
  const event = { k: 'erupt', p: 'a', i: 15, cs: ['r_1', 'b_2', 'g_3'] };
  assert.match(c.describe(event), /eruption 15<\/span>/);
  assert.doesNotMatch(c.describe(event), /of 5/);
  c.G.mode = 'volcano';
  assert.match(c.describe({ ...event, i: 5 }), /eruption 5 of 5/);
});
