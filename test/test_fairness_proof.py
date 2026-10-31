import copy
import hashlib
import hmac
import inspect
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Installs the module stubs needed to import the game modules
import test.test_rate_limit_mitigations as stubs  # noqa: F401

import bot
import card as c
import fairness
import storm
import volcano
from errors import DeckEmptyError, GameStartingError
from fairness import FairRandom
from game import Game
from game_manager import GameManager
from player import Player

BEACON = "143ac4a3ef744631cc9815a2c3218bfa3179dc5c56bb999a9f1c24c1a5aa0c10"
FAIR_JS = os.path.join(os.path.dirname(__file__), os.pardir, 'web', 'fair.js')
DECKS_JS = os.path.join(os.path.dirname(__file__), os.pardir, 'web', 'decks.js')
INDEX_HTML = os.path.join(os.path.dirname(__file__), os.pardir, 'web', 'index.html')


# --- A reference checker: the same algorithm as web/fair.js -------------

def full_deck(mode):
    if mode == 'storm':
        # No 0s; two of each of the Storm cards per color
        values = ['1', '2', '3', '4', '5', '6', '7', '8', '9', 'draw', 'reverse', 'skip', 'swap', 'drop', 'plus1']
        cards = ['%s_%s' % (color, value) for color in 'rbgy' for value in values for _ in range(2)]
        return cards + ['colorchooser', 'draw_four'] * 4
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
    def __init__(self, seed_hex, entropy_hex):
        self.seed, self.entropy, self.counter = bytes.fromhex(seed_hex), bytes.fromhex(entropy_hex), 0

    def randbelow(self, n):
        if n <= 1:
            return 0
        mask = (1 << (n - 1).bit_length()) - 1
        while True:
            msg = b"uno-fair-v1" + self.entropy + self.counter.to_bytes(8, 'big')
            self.counter += 1
            value = int.from_bytes(hmac.new(self.seed, msg, hashlib.sha256).digest()[:8], 'big') & mask
            if value < n:
                return value


def check(proof):
    """(problem or None, {seat: cards drawn in order}) for a proof"""
    if hashlib.sha256(bytes.fromhex(proof['seed'])).hexdigest() != proof['commit']:
        return 'commitment', None
    if proof['source'] == 'players':
        entropy = fairness.players_entropy(proof['player_seeds'])
    else:
        entropy = proof['beacon']
    stream = Stream(proof['seed'], entropy)
    pile, grave, out, by_seat = [], [], {}, {}
    rolls = eruptions = 0
    for i, op in enumerate(proof['ops']):
        kind = op[0]
        if kind == 's':
            seats = list(op[1])
            for j in range(len(seats) - 1, 0, -1):
                k = stream.randbelow(j + 1)
                seats[j], seats[k] = seats[k], seats[j]
            if seats != op[2]:
                return 'seating at %d' % i, None
        elif kind == 'f':
            pile, grave, out = full_deck(op[1]), [], {}
        elif kind == 'd':
            if not pile:
                pile, grave = grave, []
            if not pile:
                return 'empty deck at %d' % i, None
            pile.sort()
            card = pile.pop(stream.randbelow(len(pile)))
            if card != op[1]:
                return 'card at %d' % i, None
            out[card] = out.get(card, 0) + 1
            by_seat.setdefault(op[2], []).append(card)
        elif kind in ('x', 'b'):
            for card in ([op[1]] if kind == 'x' else op[1]):
                if not out.get(card):
                    return 'invented card at %d' % i, None
                out[card] -= 1
                (grave if kind == 'x' else pile).append(card)
        elif kind == 'c':
            if c.COLORS[stream.randbelow(4)] != op[1]:
                return 'color at %d' % i, None
        elif kind == 'm':
            if op[1][stream.randbelow(len(op[1]))] != op[2]:
                return 'mode at %d' % i, None
        elif kind == 'v':
            if eruptions >= 5 or op[1] != 10 + rolls:
                return 'volcano odds at %d' % i, None
            rolls += 1
            if (stream.randbelow(op[1]) == 0) != op[2]:
                return 'volcano at %d' % i, None
            eruptions += op[2]
        else:
            return 'unknown op at %d' % i, None
    return None, by_seat


def user(uid, name):
    return SimpleNamespace(id=uid, first_name=name, username=None)


def drand_rng():
    """A FairRandom that went through the /start flow with a fake drand round"""
    rng = FairRandom(enabled=True)
    rng.set_round(fairness.first_round_after(time.time()), int(time.time()), 1)
    with patch('fairness.fetch_round', return_value=BEACON), patch('fairness.time.sleep'):
        assert rng.fetch_planned_round()
    return rng


def seeds_rng(names):
    rng = FairRandom(enabled=True)
    rng.wait_for_seeds(60)
    for i, name in enumerate(names):
        rng.add_seed(i + 1, name, "seed of %s" % name)
    rng.use_player_seeds()
    return rng


def play_a_game(mode='classic', players=3, max_turns=600, rng=None):
    """Play a game through the real game code; returns the game"""
    game = Game(SimpleNamespace(id=-1, title="Test", type="group"))
    game.rng = rng or drand_rng()
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
            game.turn()
            p.leave()
            left_one = True
            continue
        playable = p.playable_cards()
        # Keep a few cards in hand so the game runs long enough to go through the deck
        if playable and len(p.cards) > 3:
            card = playable[0]
            p.play(card)
            # As actions.do_play_card does; nothing happens outside the Storm mode
            storm.apply(game, p, card)
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
        # As actions.do_draw does; nothing happens outside the Volcano mode
        volcano.roll(game, p)
        game.turn()
    return game


class TestFairRandom(unittest.TestCase):

    def test_commitment_is_the_seed_fingerprint(self):
        rng = FairRandom(enabled=True)
        self.assertEqual(rng.commitment, hashlib.sha256(rng.seed).hexdigest())
        self.assertEqual(len(rng.seed), 32)
        self.assertIsNone(rng.round)  # the round is only chosen at /start

    def test_round_comes_out_after_the_announcement(self):
        for stamp in (fairness.round_time(1000) + x for x in (0, 0.5, 1, 1.9, 2, 2.9)):
            stamp = int(stamp)
            self.assertGreater(fairness.round_time(fairness.first_round_after(stamp)), stamp + 1)

    def test_stream_matches_the_reference(self):
        rng = drand_rng()
        rng.seed = bytes(range(32))
        sizes = (2, 3, 7, 52, 108, 140, 1, 4)
        ours = [rng.randbelow(n) for n in sizes]
        reference = Stream(bytes(range(32)).hex(), BEACON)
        self.assertEqual(ours, [reference.randbelow(n) for n in sizes])

    def test_randbelow_is_uniform(self):
        rng = drand_rng()
        counts = [0] * 5
        for _ in range(5000):
            counts[rng.randbelow(5)] += 1
        for count in counts:
            self.assertTrue(800 <= count <= 1200, counts)

    def test_unreachable_drand_is_reported(self):
        rng = FairRandom(enabled=True)
        rng.set_round(fairness.first_round_after(time.time()))
        with patch('fairness.fetch_round', return_value=None), patch('fairness.time.sleep'):
            self.assertFalse(rng.fetch_planned_round())
        self.assertIsNone(rng.verifiable)

    def test_player_seeds_order_does_not_matter(self):
        a = fairness.players_entropy([['a', 'one'], ['b', 'two']])
        self.assertEqual(a, fairness.players_entropy([['b', 'two'], ['a', 'one']]))
        self.assertNotEqual(a, fairness.players_entropy([['a', 'one'], ['b', 'tw0']]))
        # Lengths are part of the hash, so texts can't be shifted between players
        self.assertNotEqual(fairness.players_entropy([['a', 'xy'], ['b', 'z']]),
                            fairness.players_entropy([['a', 'x'], ['b', 'yz']]))

    def test_each_player_sends_one_seed(self):
        rng = FairRandom(enabled=True)
        rng.wait_for_seeds(60)
        self.assertTrue(rng.awaiting_seeds)
        self.assertTrue(rng.add_seed(1, 'a', 'x'))
        self.assertFalse(rng.add_seed(1, 'a', 'y'))
        self.assertEqual(rng.player_seeds[1], ['a', 'x'])

    def test_saved_and_restored_stream_continues_identically(self):
        rng = drand_rng()
        [rng.randbelow(100) for _ in range(10)]
        restored = FairRandom.from_dict(json.loads(json.dumps(rng.to_dict())))
        self.assertEqual([rng.randbelow(100) for _ in range(20)], [restored.randbelow(100) for _ in range(20)])
        self.assertEqual(restored.proof(), rng.proof())

    def test_waiting_for_seeds_survives_a_restart(self):
        rng = FairRandom(enabled=True)
        rng.wait_for_seeds(60)
        rng.add_seed(7, 'a', 'hello')
        restored = FairRandom.from_dict(json.loads(json.dumps(rng.to_dict())))
        self.assertTrue(restored.awaiting_seeds)
        self.assertEqual(restored.player_seeds, {7: ['a', 'hello']})

    def test_first_version_saves_still_load(self):
        v1 = {'enabled': True, 'seed': '11' * 32, 'round': 5, 'beacon': BEACON, 'verifiable': True,
              'counter': 3, 'ops': [['f', 'classic']]}
        rng = FairRandom.from_dict(v1)
        self.assertEqual(rng.source, fairness.DRAND)
        self.assertEqual(rng.entropy, BEACON)
        lobby = FairRandom.from_dict({'enabled': True, 'seed': '22' * 32, 'round': 5, 'verifiable': None})
        self.assertIsNone(lobby.source)
        self.assertIsNone(lobby.round)


class TestFairnessProof(unittest.TestCase):

    def test_a_full_game_checks_out(self):
        for mode in ('classic', 'wild'):
            proof = play_a_game(mode).rng.proof()
            kinds = {op[0] for op in proof['ops']}
            self.assertTrue({'s', 'f', 'd', 'x', 'c'} <= kinds, kinds)
            problem, by_seat = check(proof)
            self.assertIsNone(problem)
            # Cards turned up for the first card belong to no seat: the first card itself
            # plus any Wild Draw Four put back before it
            put_back = sum(len(op[1]) for op in proof['ops'] if op[0] == 'b')
            self.assertEqual(len(by_seat[None]), 1 + put_back)

    def test_every_draw_names_who_got_it(self):
        game = play_a_game(max_turns=30)
        draws = [op for op in game.rng.proof()['ops'] if op[0] == 'd']
        hands = [op for op in draws if op[2] is not None]
        self.assertTrue(all(op[2] in ('P1', 'P2', 'P3') for op in hands))
        # Each player was dealt 7 cards first, in seat order
        seats = [op[2] for op in hands[:21]]
        self.assertEqual([seats[i] for i in (0, 7, 14)], [seats[i * 7 + 6] for i in range(3)])

    def test_a_game_with_player_seeds_checks_out(self):
        game = play_a_game(rng=seeds_rng(['P1', 'P2', 'P3']))
        proof = game.rng.proof()
        self.assertEqual(proof['source'], 'players')
        self.assertIsNone(proof['beacon'])
        self.assertEqual(len(proof['player_seeds']), 3)
        self.assertIsNone(check(proof)[0])

    def test_discard_pile_recycling_checks_out(self):
        game = play_a_game('classic', players=4, max_turns=1500)
        draws = sum(1 for op in game.rng.proof()['ops'] if op[0] == 'd')
        self.assertGreater(draws, 108, "the game never went through the whole deck")
        self.assertIsNone(check(game.rng.proof())[0])

    def test_tampering_is_detected(self):
        proof = play_a_game().rng.proof()
        draw = next(i for i, op in enumerate(proof['ops']) if op[0] == 'd' and op[1] != 'draw_four')

        swapped = copy.deepcopy(proof)
        swapped['ops'][draw][1] = 'draw_four'
        self.assertIsNotNone(check(swapped)[0])

        other_seed = copy.deepcopy(proof)
        other_seed['seed'] = '00' * 32
        self.assertEqual(check(other_seed)[0], 'commitment')

        other_beacon = copy.deepcopy(proof)
        other_beacon['beacon'] = '11' * 32
        self.assertIsNotNone(check(other_beacon)[0])

        invented = copy.deepcopy(proof)
        extra = 'r_0' if proof['ops'][draw][1] == 'draw_four' else 'draw_four'
        invented['ops'].insert(draw + 1, ['x', extra])
        self.assertIsNotNone(check(invented)[0])

        seeds = play_a_game(rng=seeds_rng(['P1', 'P2', 'P3'])).rng.proof()
        seeds['player_seeds'][0][1] = 'something else'
        self.assertIsNotNone(check(seeds)[0])

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
        """Without a score message to join (other chats, or the game went on), the reveal is its own message"""
        import actions
        game = play_a_game(max_turns=30)
        game.rng.match_ids = [494]
        with patch('actions.send_async') as send, patch('actions.rivalry.attach_proof') as attach, \
                patch('actions.rivalry.score_pending', return_value=False), \
                patch('actions.rivalry.match_url', return_value='https://example.org/uno-scores/replay.html#494'):
            actions.reveal_fairness(SimpleNamespace(), game)
            actions.reveal_fairness(SimpleNamespace(), game)
        send.assert_called_once()
        attach.assert_called_once_with([494], game.rng.proof())
        # The long values are behind copy buttons, not in the text
        text = send.call_args[1]['text']
        self.assertNotIn(game.rng.seed.hex(), text)
        buttons = [b.to_dict() for row in send.call_args[1]['reply_markup'].inline_keyboard for b in row]
        copied = [b['copy_text']['text'] for b in buttons if 'copy_text' in b]
        self.assertEqual(copied, [game.rng.seed.hex(), game.rng.commitment])
        self.assertIn('https://example.org/uno-scores/replay.html#494', [b.get('url') for b in buttons])

    def test_reveal_joins_the_pending_score_message(self):
        import actions
        game = play_a_game(max_turns=30)
        game.rng.match_ids = [495]
        with patch('actions.send_async') as send, patch('actions.rivalry.attach_proof') as attach, \
                patch('actions.rivalry.score_pending', return_value=True):
            actions.reveal_fairness(SimpleNamespace(), game)
        send.assert_not_called()
        attach.assert_called_once_with([495], game.rng.proof())

    def test_lobby_commitment_is_short_with_a_copy_button(self):
        import actions
        game = Game(SimpleNamespace(id=-1, title="Lobby", type="group"))
        game.rng = FairRandom(enabled=True)
        text, markup = actions.fairness_commitment(game)
        self.assertNotIn(game.rng.commitment, text)
        self.assertIn(game.rng.commitment[:8], text)
        button = markup.inline_keyboard[0][0].to_dict()
        self.assertEqual(button['copy_text'], {'text': game.rng.commitment})
        game.rng = FairRandom(enabled=False)
        self.assertIsNone(actions.fairness_commitment(game))

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


class FakeBot(object):
    """Records messages; send_message answers like Telegram, with a timestamp"""

    def __init__(self, delays=()):
        self.sent = []
        self.delays = list(delays)  # seconds Telegram's timestamp lags behind, per message

    def send_message(self, chat_id, text, **kwargs):
        lag = self.delays.pop(0) if self.delays else 0
        self.sent.append(text)
        self.markups = getattr(self, 'markups', []) + [kwargs.get('reply_markup')]
        return SimpleNamespace(date=datetime.fromtimestamp(int(time.time() + lag), timezone.utc),
                               message_id=len(self.sent))


class TestStartFlow(unittest.TestCase):

    def setUp(self):
        self.chat = SimpleNamespace(id=-77, title="Start", type="group")
        self.game = Game(self.chat)
        self.game.rng = FairRandom(enabled=True)
        self.game.deck.rng = self.game.rng
        self.players = [Player(self.game, user(i, "P%d" % i)) for i in (1, 2)]
        self.patches = [patch('bot.gm'), patch('bot.send_async'), patch('bot._deal_and_announce'),
                        patch('fairness.time.sleep'), patch('bot.time.sleep')]
        self.gm, self.send, self.deal = [p.start() for p in self.patches[:3]]
        for p in self.patches[3:]:
            p.start()
        self.gm.chatid_games = {self.chat.id: [self.game]}

    def tearDown(self):
        # Reverse order: two of the patches replace the same time.sleep
        for p in reversed(self.patches):
            p.stop()

    def test_round_is_announced_then_fetched(self):
        fake = FakeBot()
        with patch('fairness.fetch_round', return_value=BEACON) as fetch:
            self.assertTrue(bot._prepare_fair_start(fake, self.game, MagicMock()))
        rng = self.game.rng
        self.assertEqual(rng.source, fairness.DRAND)
        self.assertIn(str(rng.round), fake.sent[0])
        copy = fake.markups[0].inline_keyboard[0][0].to_dict()['copy_text']['text']
        self.assertEqual(copy, rng.commitment)
        self.assertGreater(fairness.round_time(rng.round), rng.anchor['date'] + 1)
        fetch.assert_called_with(rng.round)

    def test_late_delivery_announces_a_later_round(self):
        fake = FakeBot(delays=[30, 0])  # Telegram stamped the first message long after the planned round
        with patch('fairness.fetch_round', return_value=BEACON):
            bot._prepare_fair_start(fake, self.game, MagicMock())
        self.assertEqual(len(fake.sent), 2)
        rng = self.game.rng
        self.assertIn(str(rng.round), fake.sent[1])
        self.assertGreater(fairness.round_time(rng.round), rng.anchor['date'] + 1)

    def test_drand_down_asks_for_seeds_and_locks_the_lobby(self):
        job_queue = MagicMock()
        with patch('fairness.fetch_round', return_value=None):
            self.assertFalse(bot._prepare_fair_start(FakeBot(), self.game, job_queue))
        self.assertTrue(self.game.rng.awaiting_seeds)
        self.assertIn('/seed', self.send.call_args[1]['text'])
        job_queue.run_once.assert_called_once()
        self.assertEqual(job_queue.run_once.call_args[0][1], fairness.SEED_TIMEOUT)

        gm = GameManager()
        gm.chatid_games[self.chat.id] = [self.game]
        for p in self.players:
            gm.userid_players[p.user.id] = [p]
            gm.userid_current[p.user.id] = p
        with self.assertRaises(GameStartingError):
            gm.join_game(user(3, "P3"), self.chat)
        with self.assertRaises(GameStartingError):
            gm.leave_game(self.players[0].user, self.chat)

    def _seed(self, player, text):
        self.gm.player_for_user_in_chat.return_value = player
        update = MagicMock()
        update.message.chat = self.chat
        update.message.from_user = player.user
        update.message.text = '/seed ' + text
        inspect.unwrap(bot.seed_command)(update, MagicMock())

    def test_game_deals_once_every_player_sent_a_seed(self):
        self.game.rng.wait_for_seeds(60)
        self._seed(self.players[0], 'purple banana')
        self.deal.assert_not_called()
        self._seed(self.players[0], 'again')  # a second seed from the same player is refused
        self.deal.assert_not_called()
        self._seed(self.players[1], '42')
        self.deal.assert_called_once()
        rng = self.game.rng
        self.assertEqual(rng.source, fairness.PLAYERS)
        self.assertEqual(sorted(t for _, t in rng.player_seeds.values()), ['42', 'purple banana'])

    def test_seed_without_text_is_refused(self):
        self.game.rng.wait_for_seeds(60)
        self._seed(self.players[0], '')
        self.assertEqual(self.game.rng.player_seeds, {})

    def test_timeout_starts_without_proof(self):
        self.game.rng.wait_for_seeds(60)
        with patch('bot.game_is_running', return_value=True), patch('bot.FAIR_PLAY_REQUIRED', False):
            bot._seeds_timed_out(MagicMock(), self.game, MagicMock())
        self.assertEqual(self.game.rng.source, fairness.SYSTEM)
        self.deal.assert_called_once()

    def test_timeout_cancels_the_start_when_proof_is_required(self):
        self.game.rng.wait_for_seeds(60)
        with patch('bot.game_is_running', return_value=True), patch('bot.FAIR_PLAY_REQUIRED', True):
            bot._seeds_timed_out(MagicMock(), self.game, MagicMock())
        self.assertFalse(self.game.rng.awaiting_seeds)
        self.assertIsNone(self.game.rng.source)
        self.deal.assert_not_called()

    def test_fairness_off_deals_straight_away(self):
        self.game.rng = FairRandom(enabled=False)
        fake = FakeBot()
        with patch('fairness.fetch_round') as fetch:
            self.assertTrue(bot._prepare_fair_start(fake, self.game, MagicMock()))
        fetch.assert_not_called()
        self.assertEqual(fake.sent, [])


class TestScoreMessage(unittest.TestCase):
    """The rivalry score message carries the match link and the seed buttons"""

    def test_score_message_links_the_match(self):
        import rivalry
        with patch('rivalry.RIVALRY_WEB_URL', 'https://example.org/uno-scores/'):
            text = rivalry._with_link("A 45 – 40 I", 495)
            plain = rivalry._with_link("A 45 – 40 I")
        self.assertIn('📈 Full stats', text)
        self.assertIn('href="https://example.org/uno-scores/replay.html#495">🔎 Match stats', text)
        self.assertNotIn('Match stats', plain)

    def test_announce_adds_the_reveal_to_the_score(self):
        import rivalry
        proof = play_a_game(max_turns=30).rng.proof()
        row = SimpleNamespace(events=json.dumps({'ev': [], 'proof': proof}))
        result = SimpleNamespace(id=495, season_id=1)
        with patch('rivalry.time.sleep'), patch('rivalry.export'), \
                patch('rivalry._load', return_value=([SimpleNamespace(id=1)], [result])), \
                patch('rivalry.rs.pin_text', return_value="A 45 – 40 I"), patch('rivalry._rivals'), \
                patch('rivalry.RivalryGameLog.get', return_value=row), \
                patch('rivalry.RIVALRY_WEB_URL', 'https://example.org/uno-scores/'), \
                patch('rivalry._post_score') as post:
            rivalry._pending_scores.add(495)
            rivalry._announce(SimpleNamespace(), -1, 495)
        text, markup = post.call_args[0][2], post.call_args[0][4]
        self.assertIn('🔓', text)
        self.assertIn('replay.html#495', text)
        self.assertNotIn(proof['seed'], text)
        copied = [b.to_dict()['copy_text']['text'] for b in markup.inline_keyboard[0]]
        self.assertEqual(copied, [proof['seed'], proof['commit']])
        self.assertFalse(rivalry.score_pending(495))


class TestExpectedWait(unittest.TestCase):

    def test_expected_waits_do_not_count_as_slow(self):
        from game_threads import GameThreadRunner, expected_wait
        runner = GameThreadRunner(worker_count=1)
        runner._slow_run_warn_seconds = 0.2

        def waits():
            with expected_wait():
                time.sleep(0.3)

        def busy():
            time.sleep(0.3)

        with self.assertLogs('game_threads', level='WARNING') as logs:
            runner.submit('k', waits).result(5)
            runner.submit('k', busy).result(5)
            time.sleep(0.05)
        slow = [line for line in logs.output if 'slow task' in line]
        self.assertEqual(len(slow), 1, slow)


@unittest.skipUnless(shutil.which('node'), "Node.js is not installed")
class TestReplayPageChecker(unittest.TestCase):
    """Runs the website's checker (web/fair.js) on real proofs, so the page and the bot agree"""

    def run_page(self, cases):
        js = ''
        for path in (DECKS_JS, FAIR_JS):
            with open(path, encoding='utf-8') as f:
                js += f.read() + '\n'
        script = (
            js + "\n(async function () {\n  var cases = " + json.dumps(cases) + ";\n  var out = {};\n"
            "  for (var k in cases) {\n    var p = cases[k].proof;\n"
            "    if (cases[k].whole) { out[k] = await verifyProof(p, cases[k].events); continue; }\n"
            "    var entropy = p.source === 'players' ? await playersEntropy(p.player_seeds) : p.beacon;\n"
            "    var r = await replayOps(p, await fairStream(p.seed, entropy));\n"
            "    out[k] = r || replayMatchesProof(cases[k].events || [], p);\n  }\n"
            "  console.log(JSON.stringify(out));\n})();\n")
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
            f.write(script)
        try:
            result = subprocess.run(['node', f.name], capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(f.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_page_checker_agrees(self):
        with patch.dict(fairness.rivalry_log.KEYS, {1: 'a', 2: 'b'}):
            good = play_a_game('wild', players=2).rng.proof()
            seeded = play_a_game(players=2, rng=seeds_rng(['a', 'b'])).rng.proof()
        _, by_seat = check(good)
        events = [{'k': 'deal', 'h': {'a': by_seat['a'][:7], 'b': by_seat['b'][:7]}, 'fc': by_seat[None][0]},
                  {'k': 'draw', 'p': 'a', 'cs': by_seat['a'][7:9]}]
        swapped = copy.deepcopy(events)
        swapped[0]['h']['a'][0], swapped[0]['h']['b'][0] = events[0]['h']['b'][0], events[0]['h']['a'][0]
        bad_card = copy.deepcopy(good)
        i = next(i for i, op in enumerate(bad_card['ops']) if op[0] == 'd' and op[1] != 'r_1')
        bad_card['ops'][i][1] = 'r_1'
        out = self.run_page({
            'good': {'proof': good, 'events': events},
            'seeds': {'proof': seeded},
            'bad_card': {'proof': bad_card},
            'swapped_hands': {'proof': good, 'events': swapped},
            # The main page's one-call check; the players' seeds need no network
            'whole': {'proof': seeded, 'whole': True},
            'whole_bad_seed': {'proof': dict(seeded, seed='00' * 32), 'whole': True},
            'whole_server': {'proof': dict(seeded, verifiable=False, source='system'), 'whole': True},
        })
        self.assertIsNone(out['good'])
        self.assertIsNone(out['seeds'])
        self.assertIsNotNone(out['bad_card'])
        self.assertEqual(out['whole'], {'ok': True})
        self.assertFalse(out['whole_bad_seed']['ok'])
        self.assertIsNone(out['whole_server']['ok'])
        if events[0]['h']['a'][0] != events[0]['h']['b'][0]:
            self.assertIsNotNone(out['swapped_hands'])



class TestFairPlayExport(unittest.TestCase):
    """What the website's fair play section reads from scores.json"""

    def test_summary_counts_the_cards_each_rival_got(self):
        import rivalry_stats as rs
        log = {'ev': [
            {'k': 'deal', 't': 0, 'h': {'a': ['r_1', 'g_skip', 'draw_four'], 'b': ['y_2', 'b_draw', 'colorchooser']},
             'fc': 'r_5', 'first': 'a'},
            {'k': 'draw', 't': 5, 'p': 'b', 'cs': ['g_reverse', 'y_7'], 'h': 5, 'r': 'pen'},
            {'k': 'draw', 't': 9, 'p': 'a', 'cs': ['colorchooser'], 'h': 4, 'r': 'vol'},
            {'k': 'end', 't': 10, 'p': 'a', 'o': 'win'},
        ]}
        d = rs.summarize_log(log)
        self.assertEqual(d['a']['hd'], [1, 1, 0, 0, 0, 1, 0, 0, 0])
        self.assertEqual(d['a']['hc'], [1, 0, 1, 0])
        self.assertEqual(d['b']['hd'], [1, 0, 0, 1, 1, 0, 0, 0, 0])
        self.assertEqual(d['b']['dt'], [1, 0, 1, 0, 0, 0, 0, 0, 0])
        self.assertEqual(d['b']['dc'], [0, 0, 1, 1])
        self.assertEqual(d['a']['dt'], [0, 0, 0, 0, 1, 0, 0, 0, 0])

    def test_fair_status(self):
        import rivalry_stats as rs
        self.assertIsNone(rs.fair_status({'ev': []}))
        self.assertEqual(rs.fair_status({'proof': {'verifiable': True, 'source': 'drand'}}), 'drand')
        self.assertEqual(rs.fair_status({'proof': {'verifiable': True, 'source': 'players'}}), 'players')
        self.assertEqual(rs.fair_status({'proof': {'verifiable': False, 'source': 'system'}}), 'system')


@unittest.skipUnless(shutil.which('node'), "Node.js is not installed")
class TestFairPlayStatistics(unittest.TestCase):
    """The p-values on the website's fair play section, against values from scipy.stats"""

    def test_p_values(self):
        with open(INDEX_HTML, encoding='utf-8') as f:
            page = f.read()
        start, end = page.index('function lgamma(x)'), page.index('// Number, action (skip')
        script = page[start:end] + """
console.log(JSON.stringify({
  binom: [binomTest(7, 9), binomTest(28, 47), binomTest(0, 0)],
  // One game is Fisher's exact test: A holds all 3 of the cards among 7 + 7
  fisher: shareTest([{ nA: 7, nB: 7, K: 3 }], 3),
  // Two games add up: 2 of 2 and 1 of 1, among 7 + 7 cards each
  strat: shareTest([{ nA: 7, nB: 7, K: 2 }, { nA: 7, nB: 7, K: 1 }], 3),
  chi: [chiTest([26, 33, 35, 18], [28, 28, 28, 28]), gammaQ(2.5, 1.6)],
  holm: holm([0.01, null, 0.04, 0.03])
}));
"""
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
            f.write(script)
        try:
            result = subprocess.run(['node', f.name], capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(f.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        # scipy.stats.binomtest(7, 9).pvalue, binomtest(28, 47).pvalue
        self.assertAlmostEqual(out['binom'][0], 0.1796875, places=9)
        self.assertAlmostEqual(out['binom'][1], 0.24296021728332556, places=9)
        self.assertIsNone(out['binom'][2])
        # scipy.stats.fisher_exact([[3, 4], [0, 7]]).pvalue
        self.assertAlmostEqual(out['fisher'], 0.19230769230769232, places=9)
        # P(2 of 2) = 21/91 = 3/13, P(1 of 1) = 1/2: A got all 3 with 3/26, and none of them with 3/26 too
        self.assertAlmostEqual(out['strat'], 6 / 26, places=9)
        # scipy.stats.chisquare([26, 33, 35, 18]).pvalue, chi2.sf(3.2, 5)
        self.assertAlmostEqual(out['chi'][0], 0.09546993524522868, places=9)
        self.assertAlmostEqual(out['chi'][1], 0.6691829020332432, places=9)
        self.assertEqual(out['holm'], [0.03, None, 0.06, 0.06])


if __name__ == '__main__':
    unittest.main()
