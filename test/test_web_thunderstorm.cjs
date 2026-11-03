const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');

test('Thunderstorm has separate statistics, charts and win rates from Storm and Wild', () => {
  const nodes = {}, charts = [];
  const c = vm.createContext({
    P: ['a', 'b'], COL: { a: 'red', b: 'blue' },
    $: id => nodes[id] || (nodes[id] = { style: {} }),
    pairTile: (title, a, b) => `${title}: ${a}/${b};`,
    share: (n, d) => d ? n / d : 0,
    pct: n => n * 100,
    plural: (n, w) => n + ' ' + w,
    sw: () => '', nm: p => p,
    barChart: (el, spec) => charts.push(spec)
  });
  vm.runInContext(html.slice(html.indexOf('function renderStorm(R, mode)'), html.indexOf('// The Tornado mode')), c);
  const stats = sw => ({ a: { sw, dr: 2, p1: 3, cx: 1 }, b: { sw: 0, dr: 1, p1: 2 } });
  const selection = { ms: [
    { mode: 'thunderstorm', w: 'a', d: { thunderstorm: stats(4) } },
    { mode: 'thunderstorm', w: 'b' },
    { mode: 'storm', w: 'b', d: { storm: stats(99) } },
    { mode: 'wild', w: 'a' }
  ] };
  assert.equal(c.renderStorm(selection, 'thunderstorm'), true);
  assert.match(nodes['thunderstorm-tiles'].innerHTML, /Win rate in Thunderstorm: 50\/50/);
  assert.match(nodes['thunderstorm-tiles'].innerHTML, /Swaps: 4\/0/);
  assert.doesNotMatch(nodes['thunderstorm-tiles'].innerHTML, /99/);
  assert.match(nodes['ts-lede'].innerHTML, /four copies/);
  assert.equal(charts[0].stacks[0].values[0], 4);
  assert.match(nodes['ts-win'].innerHTML, /Swapped more often/);
  assert.equal(c.renderStorm(selection), true);
  assert.match(nodes['storm-tiles'].innerHTML, /Swaps: 99\/0/);
  assert.equal(c.renderStorm({ ms: [{ mode: 'thunderstorm', w: 'a' }] }, 'thunderstorm'), true);
  assert.equal(nodes['ts-body'].style.display, 'none');
  assert.equal(c.renderStorm({ ms: [] }, 'thunderstorm'), false);
  assert.equal(nodes['sp-thunderstorm'].style.display, 'none');
});

test('Thunderstorm replays show Storm effect statistics', () => {
  const replay = fs.readFileSync(path.join(root, 'web/replay.html'), 'utf8');
  const nodes = {};
  const c = vm.createContext({ document: { getElementById: id => nodes[id] || (nodes[id] = {}) } });
  vm.runInContext(replay.slice(replay.indexOf('var COLOR_NAMES ='), replay.indexOf('// --- Cards')), c);
  vm.runInContext(replay.slice(replay.indexOf('function renderStats()'), replay.indexOf('function renderGame(')), c);
  c.G = { mode: 'thunderstorm', players: { a: 'Alice', b: 'Ivan' }, events: [
    { k: 'deal', h: { a: ['r_swap', 'r_1'], b: ['b_2', 'r_drop'] } },
    { k: 'play', p: 'a', c: 'r_swap', h: 1 },
    { k: 'swap', p: 'a', vs: 'b', h: 2, h2: 1 },
    { k: 'play', p: 'a', c: 'r_drop', h: 1 },
    { k: 'drop', p: 'a', cs: ['y_1'], h: 1 }
  ] };
  c.renderStats();
  assert.match(nodes.stats.innerHTML, /Swaps/);
  assert.match(nodes.stats.innerHTML, /Drops/);
  assert.match(nodes.stats.innerHTML, /Cancelled with \/cancel/);
});
