import copy
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

# Installs the module stubs needed to import the game modules
import test.test_rate_limit_mitigations as stubs  # noqa: F401

import card as c
import fairness
from errors import DeckEmptyError
from fairness import FairRandom
from game import Game
from game_manager import GameManager
from player import Player

BEACON = "143ac4a3ef744631cc9815a2c3218bfa3179dc5c56bb999a9f1c24c1a5aa0c10"
REPLAY_HTML = os.path.join(os.path.dirname(__file__), os.pardir, 'web', 'replay.html')


# --- A reference checker: the same algorithm as web/replay.html -------------

def full_deck(mode):
    values = ['1', '2', '3', '4', '5', 'draw', 'reverse', 'skip'] if mode == 'wild' else \
        ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'draw', 'reverse', 'skip']
    cards = []
    for color in 'rbgy':
        for value in values:
            copies = 4 if mode == 'wild' else (1 if value == '0' else 2)
            cards += ['%s_%s' % (color, value)] * copies
    cards += ['colorchooser', 'draw_four'] * (6 if mode == 'wild' else 4)
    return cards


class Stream(object):
    def __init__(self, seed_hex, beacon_hex):
        self.seed, self.beacon, self.counter = bytes.fromhex(seed_hex), bytes.fromhex(beacon_hex), 0

    def randbelow(self, n):
        if n <= 1:
            return 0
        mask = (1 << (n - 1).bit_length()) - 1
        while True:
            msg = b"uno-fair-v1" + self.beacon + self.counter.to_bytes(8, 'big')
            self.counter += 1
            value = int.from_bytes(hmac.new(self.seed, msg, hashlib.sha256).digest()[:8], 'big') & mask
            if value < n:
                return value


def check(proof):
    """None when every card follows from the seed and beacon, else what went wrong"""
    if hashlib.sha256(bytes.fromhex(proof['seed'])).hexdigest() != proof['commit']:
        return 'commitment'
    stream = Stream(proof['seed'], proof['beacon'])
    pile, grave, out = [], [], {}
    for i, op in enumerate(proof['ops']):
        kind = op[0]
        if kind == 's':
            seats = list(op[1])
            for j in range(len(seats) - 1, 0, -1):
                k = stream.randbelow(j + 1)
                seats[j], seats[k] = seats[k], seats[j]
            if seats != op[2]:
                return 'seating at %d' % i
        elif kind == 'f':
            pile, grave, out = full_deck(op[1]), [], {}
        elif kind == 'd':
            if not pile:
                pile, grave = grave, []
            if not pile:
                return 'empty deck at %d' % i
            pile.sort()
            card = pile.pop(stream.randbelow(len(pile)))
            if card != op[1]:
                return 'card at %d' % i
            out[card] = out.get(card, 0) + 1
        elif kind in ('x', 'b'):
            for card in ([op[1]] if kind == 'x' else op[1]):
                if not out.get(card):
                    return 'invented card at %d' % i
                out[card] -= 1
                (grave if kind == 'x' else pile).append(card)
        elif kind == 'c':
            if c.COLORS[stream.randbelow(4)] != op[1]:
                return 'color at %d' % i
        else:
            return 'unknown op at %d' % i
    return None


def user(uid, name):
    return SimpleNamespace(id=uid, first_name=name, username=None)


def play_a_game(mode='classic', players=3, max_turns=600):
    """Play a game through the real game code with a fake drand beacon; returns the game"""
    with patch('fairness.fetch_round', return_value=BEACON), patch('fairness.time.sleep'):
        game = Game(SimpleNamespace(id=-1, title="Test", type="group"))
        game.rng = FairRandom(enabled=True)
        game.deck.rng = game.rng
        game.mode = mode
        seated = [Player(game, user(i + 1, "P%d" % (i + 1))) for i in range(players)]
        with patch('game.SHUFFLE_PLAYERS', True):
            game.start()
        game.started = True
        for p in seated:
            p.draw_first_hand()
        if game.choosing_color:
            game.choose_color(c.RED)

        left_one = auto_color = False
        for turn in range(max_turns):
            p = game.current_player
            if turn == 40 and not left_one and len(game.players) > 2:
                # A player leaves mid-game: their hand goes to the discard pile
                if p is game.current_player:
                    game.turn()
                p.leave()
                left_one = True
                continue
            playable = p.playable_cards()
            # Keep a few cards in hand so the game runs long enough to go through the deck
            if playable and len(p.cards) > 3:
                card = playable[0]
                p.play(card)
                if game.choosing_color:
                    if not auto_color:
                        game.turn()  # ran out of time: the bot picks the color
                        auto_color = True
                    else:
                        game.choose_color(c.COLORS[turn % 4])
                continue
            try:
                p.draw()
            except DeckEmptyError:
                break
            game.turn()
    return game


class TestFairRandom(unittest.TestCase):

    def test_commitment_is_the_seed_fingerprint(self):
        rng = FairRandom(enabled=True)
        self.assertEqual(rng.commitment, hashlib.sha256(rng.seed).hexdigest())
        self.assertEqual(len(rng.seed), 32)

    def test_committed_round_is_not_published_yet(self):
        with patch('fairness.time.time', return_value=fairness.round_time(1000) + 1.0):
            rng = FairRandom(enabled=True)
        self.assertEqual(rng.round, 1002)
        self.assertGreater(fairness.round_time(rng.round), rng.committed_at)

    def test_stream_is_deterministic_and_matches_the_reference(self):
        rng = FairRandom(enabled=True)
        rng.seed = bytes(range(32))
        rng.beacon, rng.verifiable = BEACON, True
        ours = [rng.randbelow(n) for n in (2, 3, 7, 52, 108, 140, 1, 4)]
        reference = Stream(bytes(range(32)).hex(), BEACON)
        self.assertEqual(ours, [reference.randbelow(n) for n in (2, 3, 7, 52, 108, 140, 1, 4)])

    def test_randbelow_is_uniform(self):
        rng = FairRandom(enabled=True)
        rng.beacon, rng.verifiable = BEACON, True
        counts = [0] * 5
        for _ in range(5000):
            counts[rng.randbelow(5)] += 1
        for count in counts:
            self.assertTrue(800 <= count <= 1200, counts)

    def test_falls_back_when_drand_is_unreachable(self):
        with patch('fairness.fetch_round', return_value=None), patch('fairness.time.sleep'):
            rng = FairRandom(enabled=True)
            rng.activate()
        self.assertFalse(rng.verifiable)
        self.assertIsNone(rng.proof()['seed'])
        self.assertIn(rng.randbelow(10), range(10))

    def test_disabled_never_touches_the_network(self):
        with patch('fairness.fetch_round') as fetch:
            rng = FairRandom(enabled=False)
            rng.activate()
        fetch.assert_not_called()
        self.assertFalse(rng.verifiable)

    def test_saved_and_restored_stream_continues_identically(self):
        rng = FairRandom(enabled=True)
        rng.beacon, rng.verifiable = BEACON, True
        [rng.randbelow(100) for _ in range(10)]
        restored = FairRandom.from_dict(json.loads(json.dumps(rng.to_dict())))
        self.assertEqual([rng.randbelow(100) for _ in range(20)], [restored.randbelow(100) for _ in range(20)])
        self.assertEqual(restored.commitment, rng.commitment)


class TestFairnessProof(unittest.TestCase):

    def test_a_full_game_checks_out(self):
        for mode in ('classic', 'wild'):
            game = play_a_game(mode)
            proof = game.rng.proof()
            kinds = {op[0] for op in proof['ops']}
            # Seating, a fresh deck, draws, discards and a bot-picked color were all exercised
            self.assertTrue({'s', 'f', 'd', 'x', 'c'} <= kinds, kinds)
            self.assertIsNone(check(proof))

    def test_discard_pile_recycling_checks_out(self):
        game = play_a_game('classic', players=4, max_turns=1500)
        draws = sum(1 for op in game.rng.proof()['ops'] if op[0] == 'd')
        self.assertGreater(draws, 108, "the game never went through the whole deck")
        self.assertIsNone(check(game.rng.proof()))

    def test_tampering_is_detected(self):
        proof = play_a_game().rng.proof()
        draw = next(i for i, op in enumerate(proof['ops']) if op[0] == 'd' and op[1] != 'draw_four')

        swapped = copy.deepcopy(proof)
        swapped['ops'][draw][1] = 'draw_four'
        self.assertIsNotNone(check(swapped))

        other_seed = copy.deepcopy(proof)
        other_seed['seed'] = '00' * 32
        self.assertEqual(check(other_seed), 'commitment')

        other_beacon = copy.deepcopy(proof)
        other_beacon['beacon'] = '11' * 32
        self.assertIsNotNone(check(other_beacon))

        invented = copy.deepcopy(proof)
        invented['ops'].insert(draw + 1, ['x', 'draw_four'] if proof['ops'][draw][1] != 'draw_four' else ['x', 'r_0'])
        self.assertIsNotNone(check(invented))

    def test_proof_survives_a_restart(self):
        game = play_a_game(max_turns=30)
        gm = GameManager()
        restored = gm._deserialize_game(json.loads(json.dumps(gm._serialize_game(game))))
        self.assertEqual(restored.rng.proof(), game.rng.proof())
        self.assertIs(restored.deck.rng, restored.rng)

    def test_old_saves_get_no_proof(self):
        gm = GameManager()
        data = gm._serialize_game(Game(SimpleNamespace(id=-1, title="Old", type="group")))
        del data['fair']
        restored = gm._deserialize_game(data)
        self.assertFalse(restored.rng.announce)
        self.assertFalse(restored.rng.verifiable)

    def test_seed_is_revealed_once_at_game_end(self):
        import actions
        game = play_a_game(max_turns=30)
        game.rng.match_ids = [494]
        with patch('actions.send_async') as send, patch('actions.rivalry.attach_proof') as attach, \
                patch('actions.RIVALRY_WEB_URL', 'https://example.org/uno-scores/'):
            actions.reveal_fairness(SimpleNamespace(), game)
            actions.reveal_fairness(SimpleNamespace(), game)
        send.assert_called_once()
        text = send.call_args[1]['text']
        self.assertIn(game.rng.seed.hex(), text)
        self.assertIn('https://example.org/uno-scores/replay.html#494', text)
        attach.assert_called_once_with([494], game.rng.proof())

    def test_nothing_is_revealed_for_a_lobby_that_never_started(self):
        import actions
        game = Game(SimpleNamespace(id=-1, title="Lobby", type="group"))
        game.rng = FairRandom(enabled=True)
        with patch('actions.send_async') as send, patch('actions.rivalry.attach_proof') as attach:
            actions.reveal_fairness(SimpleNamespace(), game)
        send.assert_not_called()
        attach.assert_not_called()

    def test_end_game_runs_the_reveal_hook(self):
        gm = GameManager()
        chat = SimpleNamespace(id=-9, title="Hook", type="group")
        game = gm.new_game(chat)
        gm.join_game(user(1, "A"), chat)
        seen = []
        gm.on_game_end = seen.append
        with patch.object(gm, '_persist_chat'):
            gm.end_game(chat, user(1, "A"))
        self.assertEqual(seen, [game])

    @unittest.skipUnless(shutil.which('node'), "Node.js is not installed")
    def test_replay_page_checker_agrees(self):
        """Runs the checker from web/replay.html on a real proof, so the page and the bot agree"""
        with open(REPLAY_HTML, encoding='utf-8') as f:
            page = f.read()
        js = re.search(r"// --- Fairness check -+\n(.*?)// --- Loading", page, re.S).group(1)
        proofs = {'good': play_a_game('wild').rng.proof()}
        bad = copy.deepcopy(proofs['good'])
        i = next(i for i, op in enumerate(bad['ops']) if op[0] == 'd' and op[1] != 'r_1')
        bad['ops'][i][1] = 'r_1'
        proofs['bad'] = bad
        script = (
            "function cardName(c) { return c; }\nfunction plural(n, w) { return n + ' ' + w; }\n"
            "function esc(s) { return String(s); }\n" + js +
            "\n(async function () {\n  var proofs = " + json.dumps(proofs) + ";\n  var out = {};\n"
            "  for (var k in proofs) { var s = await fairStream(proofs[k].seed, proofs[k].beacon);"
            " out[k] = await replayOps(proofs[k], s); }\n  console.log(JSON.stringify(out));\n})();\n")
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
            f.write(script)
        try:
            result = subprocess.run(['node', f.name], capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(f.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertIsNone(out['good'])
        self.assertIsNotNone(out['bad'])


if __name__ == '__main__':
    unittest.main()
