#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Provably fair randomness for one game.
#
# When a lobby opens, the game makes a secret 32-byte seed and announces its
# SHA-256 fingerprint together with a drand round that hasn't been published
# yet (drand quicknet: a public random value every 3 seconds, signed by the
# League of Entropy). When the game starts, the bot fetches that one round.
# Every random choice in the game (seating, deal, draws, colors the bot picks)
# then comes from one stream:
#
#   block i = HMAC-SHA256(key=seed, msg=b"uno-fair-v1" + beacon + i as 8 bytes big-endian)
#   value i = first 8 bytes of block i, big-endian
#
# randbelow(n) takes value & (2^k - 1) with k = bit length of n - 1 and retries
# with the next block until it is below n (rejection sampling, no bias).
# Draws pick an index into the draw pile sorted by card name, so the result
# doesn't depend on how the pile happens to be laid out in memory.
#
# The seed was fixed before the beacon existed, so nobody could choose a seed
# that gives a particular game, and players can't compute cards without the
# seed. When the game ends the seed is revealed; with the log of card
# movements (`ops`) anyone can recompute every card (web/replay.html does).
#
# If drand can't be reached, the game falls back to the OS random generator
# and is marked as not verifiable instead of being held up.

import hashlib
import hmac
import json
import logging
import secrets
import time
import urllib.error
import urllib.request

import card as c
from config import FAIR_PLAY

logger = logging.getLogger(__name__)

VERSION = 1
DOMAIN = b"uno-fair-v1"
DRAND_CHAIN = "52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971"  # quicknet
DRAND_GENESIS = 1692803367
DRAND_PERIOD = 3
DRAND_URLS = ("https://api.drand.sh", "https://api2.drand.sh", "https://api3.drand.sh",
              "https://drand.cloudflare.com")
FETCH_TIMEOUT = 3
# Rounds ahead of the current one: the committed round is published 3-6 s after
# the lobby opens, so it can't be known when the commitment is posted
ROUNDS_AHEAD = 2
# How long /start may wait for the committed round if it isn't out yet
MAX_WAIT = 10

_system = secrets.SystemRandom()


def round_at(timestamp):
    """The latest drand round published at `timestamp` (Unix seconds)"""
    return int((timestamp - DRAND_GENESIS) // DRAND_PERIOD) + 1


def round_time(number):
    """When drand round `number` is published (Unix seconds)"""
    return DRAND_GENESIS + (number - 1) * DRAND_PERIOD


def fetch_round(number):
    """
    The randomness (hex) of drand round `number`, checked against its signature,
    or None if no mirror returned it.
    """
    for base in DRAND_URLS:
        url = "%s/%s/public/%d" % (base, DRAND_CHAIN, number)
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'uno-bot-fairness'})
            with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
                data = json.loads(response.read().decode('utf-8'))
            randomness = data['randomness']
            # quicknet randomness is the SHA-256 of the round's signature
            if (int(data['round']) == number and
                    hashlib.sha256(bytes.fromhex(data['signature'])).hexdigest() == randomness):
                return randomness
            logger.warning("drand mirror %s returned an inconsistent round %s", base, number)
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            logger.info("drand mirror %s could not serve round %s", base, number, exc_info=True)
    return None


class FairRandom(object):
    """The random source of one game; see the module comment"""

    def __init__(self, enabled=None):
        self.enabled = FAIR_PLAY if enabled is None else enabled
        self.seed = secrets.token_bytes(32)
        self.commitment = hashlib.sha256(self.seed).hexdigest()
        self.committed_at = int(time.time())
        self.round = round_at(self.committed_at) + ROUNDS_AHEAD
        self.beacon = None       # hex randomness of self.round, once fetched
        self.verifiable = None   # None: not started; True: seeded stream; False: OS fallback
        self.counter = 0
        self.ops = []            # card movements and picks, in the order they used randomness
        self.revealed = False
        self.match_ids = []      # Rivalry results recorded in this game; their replays get the proof

    # --- Lifecycle ---------------------------------------------------------

    def activate(self):
        """Fetch the committed beacon when the game starts; fall back to the OS generator if it fails"""
        if self.verifiable is not None:
            return
        self.ops = []
        self.counter = 0
        if not self.enabled:
            self.verifiable = False
            return
        wait = round_time(self.round) - time.time()
        if 0 < wait <= MAX_WAIT:
            time.sleep(wait + 0.3)
        beacon = fetch_round(self.round)
        if beacon is None and round_time(self.round) > time.time() - DRAND_PERIOD:
            # Published moments ago; mirrors can lag slightly behind
            time.sleep(1.5)
            beacon = fetch_round(self.round)
        if beacon is None:
            logger.warning("No drand beacon for round %s; this game is not verifiable", self.round)
            self.verifiable = False
            return
        self.beacon = beacon
        self.verifiable = True

    @property
    def announce(self):
        """Whether the commitment should be posted (fairness is on for this game)"""
        return self.enabled

    # --- Randomness --------------------------------------------------------

    def _next64(self):
        message = DOMAIN + bytes.fromhex(self.beacon) + self.counter.to_bytes(8, 'big')
        self.counter += 1
        return int.from_bytes(hmac.new(self.seed, message, hashlib.sha256).digest()[:8], 'big')

    def randbelow(self, n):
        """A uniform integer in [0, n)"""
        if n <= 1:
            return 0
        if not self.verifiable:
            return _system.randrange(n)
        mask = (1 << (n - 1).bit_length()) - 1
        while True:
            value = self._next64() & mask
            if value < n:
                return value

    def shuffle(self, items):
        """Fisher-Yates in place: for i from the end down to 1, swap item i with item randbelow(i + 1)"""
        for i in range(len(items) - 1, 0, -1):
            j = self.randbelow(i + 1)
            items[i], items[j] = items[j], items[i]

    def choose_color(self):
        """A color the bot picks for a player (timeouts, a winning Wild)"""
        color = c.COLORS[self.randbelow(len(c.COLORS))]
        self.note('c', color)
        return color

    # --- Log of card movements --------------------------------------------
    # ops: ["s", names before, names after] seating; ["f", "classic"|"wild"] fresh deck;
    # ["d", card] drawn from the pile; ["x", card] to the discard pile;
    # ["b", [cards]] put back into the draw pile; ["c", color] color picked by the bot

    def note(self, kind, *values):
        if self.verifiable is not None:
            self.ops.append([kind] + list(values))

    # --- Publishing --------------------------------------------------------

    def proof(self):
        """Everything needed to check the game, the seed included; only publish it once the game is over"""
        return {
            'v': VERSION,
            'commit': self.commitment,
            'committed_at': self.committed_at,
            'chain': DRAND_CHAIN,
            'round': self.round,
            'beacon': self.beacon,
            'verifiable': bool(self.verifiable),
            'seed': self.seed.hex() if self.verifiable else None,
            'ops': self.ops,
        }

    def to_dict(self):
        return {
            'enabled': self.enabled,
            'seed': self.seed.hex(),
            'committed_at': self.committed_at,
            'round': self.round,
            'beacon': self.beacon,
            'verifiable': self.verifiable,
            'counter': self.counter,
            'ops': self.ops,
            'revealed': self.revealed,
            'match_ids': self.match_ids,
        }

    @classmethod
    def from_dict(cls, data):
        rng = cls(enabled=data.get('enabled', FAIR_PLAY))
        rng.seed = bytes.fromhex(data['seed'])
        rng.commitment = hashlib.sha256(rng.seed).hexdigest()
        rng.round = data['round']
        rng.committed_at = data.get('committed_at', round_time(rng.round) - DRAND_PERIOD * ROUNDS_AHEAD)
        rng.beacon = data.get('beacon')
        rng.verifiable = data.get('verifiable')
        rng.counter = data.get('counter', 0)
        rng.ops = data.get('ops') or []
        rng.revealed = data.get('revealed', False)
        rng.match_ids = data.get('match_ids') or []
        return rng

    @classmethod
    def unverifiable(cls):
        """For games saved before fairness proofs existed: OS randomness, nothing announced"""
        rng = cls(enabled=False)
        rng.verifiable = False
        return rng
