#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Provably fair randomness for one game.
#
# 1. When a lobby opens, the game makes a secret 32-byte seed and the bot posts
#    its SHA-256 fingerprint. The seed can't change after that.
# 2. At /start the bot posts which drand round it will use (drand quicknet: a
#    public random value every 3 seconds, signed by the League of Entropy).
#    Telegram timestamps that message, and the round is the first one published
#    after it, so nobody can know the round's value while players join or while
#    deciding to start. Then the bot fetches that one round and deals.
# 3. If drand can't be reached, each player sends /seed <anything> instead and
#    the hash of those texts takes the drand value's place. The seed was
#    committed first, so the bot can't adapt it to the players' texts.
# 4. Every random choice (seating, a random game mode, deal, draws, colors the bot
#    picks) comes from
#
#      block i = HMAC-SHA256(key=seed, msg=b"uno-fair-v1" + entropy + i as 8 bytes big-endian)
#      value i = first 8 bytes of block i, big-endian
#
#    where entropy is the drand value (or the players' hash). randbelow(n) takes
#    value & (2^k - 1), k = bit length of n - 1, and retries with the next block
#    until it is below n. Draws pick an index into the draw pile sorted by card
#    name, so the result only depends on which cards are left.
# 5. When the game is over the seed is revealed. With the log of card movements
#    (`ops`) anyone can recompute every card and who got it (web/replay.html).
#
# If neither drand nor the players' seeds are available, the game uses the OS
# generator and the players are told before any card is dealt.

import hashlib
import hmac
import json
import logging
import secrets
import time
import urllib.error
import urllib.request

import card as c
import rivalry_log
from config import FAIR_PLAY

logger = logging.getLogger(__name__)

VERSION = 2
DOMAIN = b"uno-fair-v1"
PLAYERS_DOMAIN = b"uno-fair-players-v1"
DRAND_CHAIN = "52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971"  # quicknet
DRAND_GENESIS = 1692803367
DRAND_PERIOD = 3
DRAND_URLS = ("https://api.drand.sh", "https://api2.drand.sh", "https://api3.drand.sh",
              "https://drand.cloudflare.com")
FETCH_TIMEOUT = 3
# How long a start may wait for the announced round to come out
MAX_WAIT = 12
# Longest player seed kept
MAX_SEED_LENGTH = 200
# How long players have to send /seed when drand is down
SEED_TIMEOUT = 90

DRAND, PLAYERS, SYSTEM = 'drand', 'players', 'system'

_system = secrets.SystemRandom()


def round_at(timestamp):
    """The latest drand round published at `timestamp` (Unix seconds)"""
    return int((timestamp - DRAND_GENESIS) // DRAND_PERIOD) + 1


def round_time(number):
    """When drand round `number` is published (Unix seconds)"""
    return DRAND_GENESIS + (number - 1) * DRAND_PERIOD


def first_round_after(timestamp):
    """The first round published strictly after a message Telegram stamped `timestamp`.
    Telegram's dates are whole seconds, so the message went out before timestamp + 1."""
    return round_at(timestamp + 1) + 1


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


def players_entropy(seeds):
    """
    The hash that replaces the drand value when players send seeds:
    SHA-256(b"uno-fair-players-v1" + for each [label, text] sorted:
    4-byte big-endian length + label, 4-byte big-endian length + text), all UTF-8.
    """
    h = hashlib.sha256(PLAYERS_DOMAIN)
    for label, text in sorted(seeds):
        for part in (label.encode('utf-8'), text.encode('utf-8')):
            h.update(len(part).to_bytes(4, 'big'))
            h.update(part)
    return h.hexdigest()


def seat_label(player):
    """How a player appears in the fairness log: their rivalry key ("a"/"b") or their first name"""
    user = player.user
    return (rivalry_log.KEYS.get(getattr(user, 'id', None)) or getattr(user, 'first_name', None)
            or str(user))


class FairRandom(object):
    """The random source of one game; see the module comment"""

    def __init__(self, enabled=None):
        self.enabled = FAIR_PLAY if enabled is None else enabled
        self.seed = secrets.token_bytes(32)
        self.commitment = hashlib.sha256(self.seed).hexdigest()
        self.committed_at = int(time.time())
        self.round = None        # drand round announced at /start
        self.anchor = None       # {'date', 'message_id'} of that announcement, as Telegram stamped it
        self.source = None       # DRAND, PLAYERS or SYSTEM once the game starts
        self.entropy = None      # hex value mixed with the seed (drand value or players' hash)
        self.player_seeds = {}   # user id -> [label, text] while players send /seed
        self.seeds_deadline = None
        self.counter = 0
        self.ops = []            # card movements and picks, in the order they used randomness
        self.revealed = False
        self.match_ids = []      # Rivalry results recorded in this game; their replays get the proof

    @property
    def announce(self):
        """Whether fairness is on for this game (its commitment is posted)"""
        return self.enabled

    @property
    def verifiable(self):
        """None before the game starts, then whether its cards can be checked"""
        if self.source is None:
            return None
        return self.source in (DRAND, PLAYERS)

    @property
    def awaiting_seeds(self):
        return self.seeds_deadline is not None and self.source is None

    # --- Choosing the public randomness at /start ---------------------------

    def set_round(self, number, telegram_date=None, message_id=None):
        """Use drand round `number`, announced in a message Telegram stamped `telegram_date`
        (None when the announcement couldn't be sent)"""
        self.round = number
        self.anchor = None if telegram_date is None else {'date': int(telegram_date), 'message_id': message_id}

    def fetch_planned_round(self):
        """Wait for the announced round and fetch it; True when the game can use drand"""
        wait = round_time(self.round) - time.time()
        if wait > MAX_WAIT:
            return False
        if wait > 0:
            time.sleep(wait + 0.3)
        beacon = fetch_round(self.round)
        if beacon is None:
            # Mirrors can lag a moment behind the publication
            time.sleep(1.5)
            beacon = fetch_round(self.round)
        if beacon is None:
            logger.warning("No drand beacon for round %s", self.round)
            return False
        self._use(DRAND, beacon)
        return True

    def wait_for_seeds(self, seconds):
        self.player_seeds = {}
        self.seeds_deadline = time.time() + seconds

    def add_seed(self, user_id, label, text):
        """Store a player's /seed text; False if they already sent one"""
        if user_id in self.player_seeds:
            return False
        self.player_seeds[user_id] = [label, text[:MAX_SEED_LENGTH]]
        return True

    def use_player_seeds(self):
        self._use(PLAYERS, players_entropy(self.player_seeds.values()))

    def use_system(self):
        self._use(SYSTEM, None)

    def _use(self, source, entropy):
        self.source = source
        self.entropy = entropy
        self.seeds_deadline = None
        self.counter = 0
        self.ops = []

    # --- Randomness --------------------------------------------------------

    def _next64(self):
        message = DOMAIN + bytes.fromhex(self.entropy) + self.counter.to_bytes(8, 'big')
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

    def choose_color(self, colors=c.COLORS):
        """One of the game's `colors`, picked by the bot for a player (timeouts, a winning Wild)"""
        color = colors[self.randbelow(len(colors))]
        self.note('c', color)
        return color

    # --- Log of card movements --------------------------------------------
    # ops: ["s", seats before, seats after] seating; ["f", deck name in web/decks.js] fresh deck;
    # ["d", card, seat] drawn from the pile by seat (null: the first card turned up);
    # ["x", card] to the discard pile; ["b", [cards]] put back into the draw pile;
    # ["c", color] color picked by the bot, out of the colors of the last fresh deck;
    # ["m", [modes], mode] game mode picked out of [modes] for a random-mode lobby;
    # ["v", odds, erupted] a volcano roll (volcano.py): erupted when randbelow(odds) is 0.
    # Seats are seat_label()s.

    def note(self, kind, *values):
        if self.source is not None:
            self.ops.append([kind] + list(values))

    # --- Publishing --------------------------------------------------------

    def proof(self):
        """Everything needed to check the game, the seed included; only publish it once the game is over"""
        return {
            'v': VERSION,
            'commit': self.commitment,
            'committed_at': self.committed_at,
            'source': self.source,
            'chain': DRAND_CHAIN if self.source == DRAND else None,
            'round': self.round if self.source == DRAND else None,
            'anchor': self.anchor if self.source == DRAND else None,
            'beacon': self.entropy if self.source == DRAND else None,
            'player_seeds': sorted(self.player_seeds.values()) if self.source == PLAYERS else None,
            'verifiable': bool(self.verifiable),
            'seed': self.seed.hex() if self.verifiable else None,
            'ops': self.ops,
        }

    def to_dict(self):
        return {
            'v': VERSION,
            'enabled': self.enabled,
            'seed': self.seed.hex(),
            'committed_at': self.committed_at,
            'round': self.round,
            'anchor': self.anchor,
            'source': self.source,
            'entropy': self.entropy,
            'player_seeds': {str(uid): seed for uid, seed in self.player_seeds.items()},
            'seeds_deadline': self.seeds_deadline,
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
        rng.committed_at = data.get('committed_at', rng.committed_at)
        rng.round = data.get('round')
        rng.counter = data.get('counter', 0)
        rng.ops = data.get('ops') or []
        rng.revealed = data.get('revealed', False)
        rng.match_ids = data.get('match_ids') or []
        if data.get('v', 1) >= 2:
            rng.anchor = data.get('anchor')
            rng.source = data.get('source')
            rng.entropy = data.get('entropy')
            rng.player_seeds = {int(uid): seed for uid, seed in (data.get('player_seeds') or {}).items()}
            rng.seeds_deadline = data.get('seeds_deadline')
        elif data.get('beacon'):
            # Saved by the first version, which chose the round at /new
            rng.source, rng.entropy = DRAND, data['beacon']
        elif data.get('verifiable') is False:
            rng.source = SYSTEM
        else:
            rng.round = None
        return rng

    @classmethod
    def unverifiable(cls):
        """For games saved before fairness proofs existed: OS randomness, nothing announced"""
        rng = cls(enabled=False)
        rng.use_system()
        return rng
