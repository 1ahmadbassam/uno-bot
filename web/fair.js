// The fairness checker shared by index.html and replay.html.
// Recomputes every random choice of a game from its revealed seed and its
// public randomness (see fairness.py in the bot). Nothing here trusts the bot:
// the drand round is fetched from drand itself and the deck is rebuilt from the rules
// in decks.js, which the page loads first.
'use strict';
// How cards and seats are named in messages; each page sets its own
var FairNames = {
  card: function (card) { return card; },
  seat: function (seat) { return seat; }
};
var DRAND_URLS = ['https://api.drand.sh', 'https://api2.drand.sh', 'https://api3.drand.sh', 'https://drand.cloudflare.com'];
var DRAND_GENESIS = 1692803367, DRAND_PERIOD = 3;
// The colors before the first fresh deck; each deck in decks.js has its own
var FAIR_COLORS = ['r', 'b', 'g', 'y'];

function hexBytes(hex) {
  var out = new Uint8Array(hex.length / 2);
  for (var i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}
function bytesHex(buf) {
  return Array.prototype.map.call(new Uint8Array(buf), function (b) { return (b < 16 ? '0' : '') + b.toString(16); }).join('');
}
async function sha256Hex(bytes) { return bytesHex(await crypto.subtle.digest('SHA-256', bytes)); }
function roundTime(round) { return DRAND_GENESIS + (round - 1) * DRAND_PERIOD; }

// Every card of the deck called `name` in decks.js (Deck.fill in the bot), or null for an unknown deck
function fullDeck(name) {
  var spec = DECKS[name], cards = [], i;
  if (!spec) return null;
  spec.colors.forEach(function (color) {
    spec.values.forEach(function (group) {
      group.values.forEach(function (value) { for (i = 0; i < group.copies; i++) cards.push(color + '_' + value); });
    });
  });
  Object.keys(spec.specials).forEach(function (s) { for (i = 0; i < spec.specials[s]; i++) cards.push(s); });
  return cards;
}

// The hash that stands in for drand when the players sent seeds (players_entropy in fairness.py)
async function playersEntropy(seeds) {
  var sorted = seeds.slice().sort(function (x, y) {
    return x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : x[1] < y[1] ? -1 : x[1] > y[1] ? 1 : 0;
  });
  var enc = new TextEncoder(), parts = [enc.encode('uno-fair-players-v1')];
  sorted.forEach(function (s) {
    [s[0], s[1]].forEach(function (text) {
      var bytes = enc.encode(text), len = new Uint8Array(4);
      new DataView(len.buffer).setUint32(0, bytes.length);
      parts.push(len, bytes);
    });
  });
  var total = parts.reduce(function (n, p) { return n + p.length; }, 0), all = new Uint8Array(total), at = 0;
  parts.forEach(function (p) { all.set(p, at); at += p.length; });
  return sha256Hex(all);
}

async function fairStream(seedHex, entropyHex) {
  var key = await crypto.subtle.importKey('raw', hexBytes(seedHex), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  var prefix = new Uint8Array([].concat(Array.from(new TextEncoder().encode('uno-fair-v1')), Array.from(hexBytes(entropyHex))));
  var counter = 0, trace = [];
  async function next64() {
    var msg = new Uint8Array(prefix.length + 8);
    msg.set(prefix);
    new DataView(msg.buffer).setBigUint64(prefix.length, BigInt(counter++));
    return new DataView(await crypto.subtle.sign('HMAC', key, msg)).getBigUint64(0);
  }
  return {
    // Every pick, for "Show the math": n, mask and each block tried (block number, 64-bit value, masked value)
    trace: trace,
    randbelow: async function (n) {
      var pick = { n: n, mask: 0n, tries: [], result: 0 };
      trace.push(pick);
      if (n <= 1) return 0;
      var mask = (1n << BigInt((n - 1).toString(2).length)) - 1n;
      pick.mask = mask;
      for (;;) {
        var block = counter, raw = await next64(), v = raw & mask;
        pick.tries.push({ block: block, raw: raw, v: v });
        if (v < BigInt(n)) { pick.result = Number(v); return pick.result; }
      }
    }
  };
}

async function fetchBeacon(chain, round) {
  for (var i = 0; i < DRAND_URLS.length; i++) {
    try {
      var r = await fetch(DRAND_URLS[i] + '/' + chain + '/public/' + round, { cache: 'force-cache' });
      if (!r.ok) continue;
      var d = await r.json();
      if (d.round === round && await sha256Hex(hexBytes(d.signature)) === d.randomness) return { randomness: d.randomness, from: DRAND_URLS[i] };
    } catch (e) { /* try the next mirror */ }
  }
  return null;
}

// Replays the card movements; returns null when everything matches, or what went wrong.
// Fills proof._draws (every card drawn, in order) and proof._bySeat (seat -> cards drawn, in order).
async function replayOps(proof, stream) {
  var pile = [], grave = [], out = {}, draws = [], bySeat = {}, steps = [], colors = FAIR_COLORS, i, j;
  proof._steps = steps;
  for (i = 0; i < proof.ops.length; i++) {
    var op = proof.ops[i], kind = op[0], where = 'step ' + (i + 1) + ': ', from = stream.trace ? stream.trace.length : 0;
    var step = { i: i, kind: kind, op: op };
    steps.push(step);
    if (kind === 's') {
      var seats = op[1].slice();
      for (j = seats.length - 1; j > 0; j--) {
        var k = await stream.randbelow(j + 1), t = seats[j]; seats[j] = seats[k]; seats[k] = t;
      }
      if (JSON.stringify(seats) !== JSON.stringify(op[2])) return where + 'the seating order is not the one the seed gives';
    } else if (kind === 'f') {
      pile = fullDeck(op[1]); grave = []; out = {};
      if (!pile) return where + 'unknown deck "' + op[1] + '"';
      colors = DECKS[op[1]].colors;
    } else if (kind === 'd') {
      if (!pile.length) { step.recycled = grave.length; pile = grave; grave = []; }
      if (!pile.length) return where + 'a card was drawn from an empty deck';
      pile.sort();
      step.pile = pile.length;
      var card = pile.splice(await stream.randbelow(pile.length), 1)[0];
      if (card !== op[1]) return where + 'the bot dealt ' + FairNames.card(op[1]) + ' but the seed gives ' + FairNames.card(card);
      out[card] = (out[card] || 0) + 1;
      draws.push(card);
      var seat = op.length > 2 && op[2] != null ? op[2] : '';
      (bySeat[seat] = bySeat[seat] || []).push(card);
    } else if (kind === 'x' || kind === 'b') {
      var cards = kind === 'x' ? [op[1]] : op[1];
      for (j = 0; j < cards.length; j++) {
        if (!(out[cards[j]] > 0)) return where + FairNames.card(cards[j]) + ' was returned to the deck without ever being dealt';
        out[cards[j]]--;
        (kind === 'x' ? grave : pile).push(cards[j]);
      }
    } else if (kind === 'c') {
      var color = colors[await stream.randbelow(colors.length)];
      if (color !== op[1]) return where + 'the bot picked a different color than the seed gives';
    } else {
      return where + 'unknown entry "' + kind + '"';
    }
    step.picks = stream.trace ? stream.trace.slice(from) : [];
  }
  proof._draws = draws;
  proof._bySeat = bySeat;
  return null;
}

// The cards the replay shows each rival receiving, in order: their hand, then each draw
function replayReceived(events) {
  var got = {}, first = null;
  events.forEach(function (e) {
    if (e.k === 'deal') { Object.keys(e.h).forEach(function (p) { got[p] = e.h[p].slice(); }); first = e.fc || null; }
    if (e.k === 'draw' && e.cs && e.p) got[e.p] = (got[e.p] || []).concat(e.cs);
  });
  return { bySeat: got, first: first };
}

// Each rival's cards in the replay must be exactly the cards the seed gave that seat, in the same order
function replayMatchesProof(events, proof) {
  var seen = replayReceived(events);
  if (proof.v < 2) {
    // First version: the log didn't say who got each card, so only check the cards exist
    var left = {};
    proof._draws.forEach(function (c) { left[c] = (left[c] || 0) + 1; });
    var all = [].concat.apply(seen.first ? [seen.first] : [], Object.keys(seen.bySeat).map(function (p) { return seen.bySeat[p]; }));
    return all.every(function (c) { if (!(left[c] > 0)) return false; left[c]--; return true; }) ? null : 'this replay shows cards the proof never dealt';
  }
  if (seen.first && (proof._bySeat[''] || [])[0] !== seen.first) return 'the first card turned up is not the one the seed gives';
  var seats = Object.keys(seen.bySeat);
  for (var i = 0; i < seats.length; i++) {
    var p = seats[i], shown = seen.bySeat[p], dealt = proof._bySeat[p] || [];
    for (var j = 0; j < shown.length; j++) {
      if (shown[j] !== dealt[j]) return FairNames.seat(p) + '\'s card ' + (j + 1) + ' (' + FairNames.card(shown[j]) + ') is not the one the seed gave them';
    }
  }
  return null;
}

// The whole check without the step-by-step display: { ok: true } when every card
// checks out, { ok: false, why } when something doesn't match, { ok: null, why } when
// the game can't be checked (no proof, server randomness, drand unreachable).
async function verifyProof(proof, events) {
  if (!proof) return { ok: null, why: 'no proof' };
  var source = proof.source || (proof.verifiable ? 'drand' : 'system');
  if (!proof.verifiable || source === 'system') return { ok: null, why: "the server's own random generator" };
  if (typeof crypto === 'undefined' || !crypto.subtle || typeof BigInt === 'undefined') return { ok: null, why: "this browser can't run the check" };
  if (await sha256Hex(hexBytes(proof.seed)) !== proof.commit) return { ok: false, why: "the seed doesn't match the fingerprint" };
  var entropy;
  if (source === 'drand') {
    var published = roundTime(proof.round);
    if (proof.anchor && proof.anchor.date && !(published > proof.anchor.date + 1)) return { ok: false, why: 'the drand round was out before the game was announced' };
    if (!proof.anchor && proof.v < 2 && proof.committed_at != null && !(proof.committed_at < published)) return { ok: false, why: 'the drand round was out before the seed was fixed' };
    var beacon = await fetchBeacon(proof.chain, proof.round);
    if (!beacon) return { ok: null, why: "drand couldn't be reached" };
    if (proof.beacon && proof.beacon !== beacon.randomness) return { ok: false, why: 'the bot used a different drand value' };
    entropy = beacon.randomness;
  } else {
    entropy = await playersEntropy(proof.player_seeds || []);
  }
  var problem = await replayOps(proof, await fairStream(proof.seed, entropy));
  if (problem) return { ok: false, why: problem };
  var mismatch = replayMatchesProof(events || [], proof);
  return mismatch ? { ok: false, why: mismatch } : { ok: true };
}
