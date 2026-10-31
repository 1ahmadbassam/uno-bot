import json
import os
import shutil
import subprocess
import tempfile
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Installs the module stubs needed to import the game modules
import test.test_rate_limit_mitigations as stubs  # noqa: F401

import actions
import bot
import card as c
import modes
import rivalry_log
import rivalry_stats as rs
import storm
from deck import Deck
from game import Game
from game_manager import GameManager
from player import Player
from test.test_fairness_proof import DECKS_JS, FAIR_JS, check, play_a_game
from test.test_rivalry_log import A, I, LoggedGame, log_of


def storm_game(players=2):
    game = Game(SimpleNamespace(id=-1, title="Test", type="group"))
    game.mode = 'storm'
    game.rng.use_system()
    game.deck.fill('storm')
    seated = [Player(game, SimpleNamespace(id=i + 1, first_name='P%d' % (i + 1), username=None))
              for i in range(players)]
    game.started = True
    game.last_card = c.Card(c.RED, '5')
    for p in seated:
        p.cards = [c.Card(c.BLUE, str(n)) for n in range(1, 4 + len(p.cards))]
    return game, seated


def hand(*cards):
    return [c.from_str(card) for card in cards]


def play(game, player, card):
    """Plays `card` (added to the hand first) the way the inline menu does"""
    player.cards.append(card)
    with patch.object(actions, 'gm', MagicMock()), patch.object(actions, 'send_async'):
        actions.do_play_card(MagicMock(), player, str(card))


class TestStormCards(unittest.TestCase):

    def test_deck_has_no_zeros_and_two_of_each_storm_card(self):
        deck = Deck()
        deck.fill('storm')
        names = Counter(map(str, deck.cards))
        self.assertEqual(len(deck.cards), 128)
        self.assertFalse(any(n.endswith('_0') for n in names))
        for color in c.COLORS:
            for value in c.STORM_VALUES:
                self.assertEqual(names['%s_%s' % (color, value)], 2)
        self.assertEqual((names['colorchooser'], names['draw_four']), (4, 4))

    def test_stickers_follow_the_pack_order(self):
        # Blue, green, red, yellow; swap, drop, +1 within each color
        self.assertEqual(list(c.STICKERS_STORM)[:3], ['b_swap', 'b_drop', 'b_plus1'])
        self.assertEqual(list(c.STICKERS_STORM)[3], 'g_swap')
        self.assertEqual(c.STICKERS['r_plus1'], 'CAACAgQAAxUAAWrlyoIRk17Jetf8DGQ_OoHN9SxwAAIWAwACRjR0DQYYbdQqcyB-PQQ')
        self.assertEqual(c.STICKERS_GREY['y_swap'], 'CAACAgQAAxUAAWrlyoIObsaysE-30o4YmsB_YeTqAAKDEAACEKnBU_VUEqxc-yVMPQQ')
        self.assertEqual(c.label(c.Card(c.BLUE, c.PLUS_ONE)), '💙+1')

    def test_mode(self):
        mode = modes.get('storm')
        self.assertTrue(mode.storm)
        self.assertEqual((mode.deck, mode.message), ('storm', 'Dance in the Rain 🌧️'))

    def test_plus_one_does_not_go_on_a_stack(self):
        game, (p1, p2) = storm_game()
        game.last_card = c.Card(c.RED, c.DRAW_TWO)
        game.draw_counter = 2
        self.assertFalse(p1._card_playable(c.Card(c.RED, c.PLUS_ONE)))


class TestStormEffects(unittest.TestCase):

    def test_plus_one_gives_each_opponent_a_card(self):
        game, players = storm_game(players=3)
        p1 = game.current_player
        sizes = [len(p.cards) for p in players]
        play(game, p1, c.Card(c.RED, c.PLUS_ONE))
        for p, size in zip(players, sizes):
            self.assertEqual(len(p.cards), size if p is p1 else size + 1)
        self.assertIs(game.current_player, p1.next)

    def test_swap_exchanges_hands_with_the_next_player(self):
        game, (p1, p2) = storm_game()
        p1.cards, p2.cards = hand('b_1', 'b_2'), hand('g_1', 'g_2', 'g_3', 'g_4')
        mine, theirs = list(p2.cards), list(p1.cards)
        play(game, p1, c.Card(c.RED, c.SWAP))
        self.assertEqual((p1.cards, p2.cards), (mine, theirs))
        self.assertIs(game.current_player, p2)

    def test_swap_follows_the_direction_of_play(self):
        game, (p1, p2, p3) = storm_game(players=3)
        game.reverse()
        before = list(p1.prev.cards)
        play(game, p1, c.Card(c.RED, c.SWAP))
        self.assertEqual(p1.cards, before)

    def test_drop_deals_a_new_hand_of_the_same_size(self):
        game, (p1, p2) = storm_game()
        game.rng.ops = []
        game.rng.source = 'system'
        p1.cards = hand('b_1', 'b_2', 'b_3')
        play(game, p1, c.Card(c.RED, c.DROP))
        self.assertEqual(len(p1.cards), 3)
        kinds = [op[0] for op in game.rng.ops]
        self.assertEqual(kinds[kinds.index('b'):], ['b', 'd', 'd', 'd'])
        self.assertEqual(game.rng.ops[kinds.index('b')][1], ['b_1', 'b_2', 'b_3'])

    def test_a_winning_card_does_nothing(self):
        game, (p1, p2) = storm_game()
        p1.cards = []
        theirs = list(p2.cards)
        with patch('actions.player_won'):
            play(game, p1, c.Card(c.RED, c.SWAP))
        self.assertEqual((p1.cards, p2.cards), ([], theirs))

    def test_other_modes_ignore_storm_cards(self):
        game, (p1, p2) = storm_game()
        game.mode = 'classic'
        mine, theirs = list(p1.cards), list(p2.cards)
        play(game, p1, c.Card(c.RED, c.SWAP))
        self.assertEqual((p1.cards, p2.cards), (mine, theirs))


class TestCancel(unittest.TestCase):

    def setUp(self):
        self.game, (self.p1, self.p2) = storm_game()

    def run_cancel(self, user, game=None):
        update = MagicMock()
        update.message.chat.id = -1
        update.message.from_user = user
        handler = bot.cancel_effect.__wrapped__.__wrapped__
        with patch.object(bot, 'gm') as gm, patch.object(bot, 'send_async') as send:
            gm.chatid_games = {-1: [game or self.game]} if game is not False else {}
            handler(update, MagicMock())
        return send.call_args[1]['text']

    def test_messages(self):
        self.assertEqual(self.run_cancel(self.p2.user), "Not your turn!")
        self.assertEqual(self.run_cancel(self.p1.user), "Cards exchange disabled!")
        self.assertTrue(self.game.storm_cancel)
        self.assertEqual(self.run_cancel(self.p1.user), "Cards exchange enabled!")
        self.assertFalse(self.game.storm_cancel)
        self.assertEqual(self.run_cancel(self.p1.user, game=False), "Command exclusively available in Storm mode!")
        self.game.mode = 'classic'
        self.assertEqual(self.run_cancel(self.p1.user), "Command exclusively available in Storm mode!")
        self.game.mode, self.game.started = 'storm', False
        self.assertEqual(self.run_cancel(self.p1.user), "The game hasn't started yet.")

    def test_a_cancelled_swap_or_drop_does_nothing(self):
        for value in (c.SWAP, c.DROP):
            game, (p1, p2) = storm_game()
            mine, theirs = list(p1.cards), list(p2.cards)
            storm.toggle_cancel(game)
            play(game, p1, c.Card(c.RED, value))
            self.assertEqual((p1.cards, p2.cards), (mine, theirs), value)
            self.assertFalse(game.storm_cancel)

    def test_cancel_does_not_stop_a_plus_one(self):
        storm.toggle_cancel(self.game)
        before = len(self.p2.cards)
        play(self.game, self.p1, c.Card(c.RED, c.PLUS_ONE))
        self.assertEqual(len(self.p2.cards), before + 1)

    def test_any_card_uses_up_the_cancel(self):
        storm.toggle_cancel(self.game)
        play(self.game, self.p1, c.Card(c.RED, '7'))
        self.assertFalse(self.game.storm_cancel)
        # The next player's Swap works again
        mine, theirs = list(self.p2.cards), list(self.p1.cards)
        play(self.game, self.p2, c.Card(c.RED, c.SWAP))
        self.assertEqual((self.p1.cards, self.p2.cards), (mine, theirs))

    def test_drawing_ends_the_cancel(self):
        storm.toggle_cancel(self.game)
        with patch.object(actions, 'gm', MagicMock()), patch('volcano.erupt', return_value=[]):
            actions.do_draw(MagicMock(), self.p1)
        self.assertFalse(self.game.storm_cancel)

    def test_cancel_survives_a_restart(self):
        self.game.current_player = self.p1
        storm.toggle_cancel(self.game)
        restored = GameManager()._deserialize_game(GameManager()._serialize_game(self.game))
        self.assertTrue(restored.storm_cancel)


class TestStormFairness(unittest.TestCase):

    def games(self, count=15):
        return [play_a_game('storm', players=2) for _ in range(count)]

    def test_games_with_drops_check(self):
        drops = 0
        for game in self.games():
            proof = game.rng.proof()
            self.assertEqual(check(proof)[0], None)
            self.assertIn(['f', 'storm'], proof['ops'])
            drops += sum(1 for op in proof['ops'] if op[0] == 'b')
        self.assertGreater(drops, 0)

    @unittest.skipUnless(shutil.which('node'), "Node.js is not installed")
    def test_page_checker_replays_a_storm_game(self):
        proof = next(g for g in self.games() if any(op[0] == 'b' for op in g.rng.proof()['ops'])).rng.proof()
        js = ''
        for path in (DECKS_JS, FAIR_JS):
            with open(path, encoding='utf-8') as f:
                js += f.read() + '\n'
        script = js + ("(async function () {\n  var p = %s;\n"
                       "  console.log(JSON.stringify(await replayOps(p, await fairStream(p.seed, p.beacon))));\n"
                       "})();\n") % json.dumps(proof)
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
            f.write(script)
        try:
            result = subprocess.run(['node', f.name], capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(f.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(json.loads(result.stdout.strip().splitlines()[-1]))


class TestStormLog(LoggedGame):

    def setUp(self):
        super().setUp()
        self.game.mode = 'storm'
        rivalry_log.start(self.game)

    def play(self, player, card):
        player.cards.append(card)
        actions.do_play_card(MagicMock(), player, str(card))

    def test_swap_drop_and_plus_one_are_logged(self):
        self.play(self.alice, c.Card(c.RED, c.SWAP))
        swap, = self.events('swap')
        self.assertEqual((swap['p'], swap['vs'], swap['h'], swap['h2']), ('a', 'b', 3, 3))
        self.assertEqual(swap['hs']['a'], [str(card) for card in self.alice.cards])
        self.assertEqual(swap['hs']['b'], ['r_7', 'r_draw', 'b_3'])

        self.play(self.ivan, c.Card(c.RED, c.DROP))
        drop, = self.events('drop')
        self.assertEqual(drop['out'], ['r_7', 'r_draw', 'b_3'])
        self.assertEqual(drop['cs'], [str(card) for card in self.ivan.cards])

        self.play(self.alice, c.Card(c.RED, c.PLUS_ONE))
        draw, = self.events('draw')
        self.assertEqual((draw['p'], draw['r'], draw['src'], len(draw['cs'])), ('b', 'plus1', 'a', 1))

    def test_a_cancelled_card_is_marked(self):
        storm.toggle_cancel(self.game)
        self.play(self.alice, c.Card(c.RED, c.SWAP))
        self.assertEqual(self.events('play')[-1]['cx'], 1)
        self.assertEqual(self.events('swap'), [])


class TestStormSummary(unittest.TestCase):

    def test_only_cards_with_an_effect_count(self):
        log = log_of(
            {'k': 'play', 'p': 'a', 'c': 'r_swap', 'h': 2, 't': 1, 'n': 0},
            {'k': 'swap', 'p': 'a', 'vs': 'b', 'hs': {'a': ['g_1', 'g_skip', 'y_2'], 'b': ['r_1', 'r_draw']},
             'h': 3, 'h2': 2, 't': 1, 'n': 0},
            {'k': 'play', 'p': 'b', 'c': 'g_drop', 'h': 1, 't': 2, 'n': 1, 'cx': 1},
            {'k': 'play', 'p': 'a', 'c': 'g_plus1', 'h': 2, 't': 3, 'n': 2},
            {'k': 'draw', 'p': 'b', 'r': 'plus1', 'src': 'a', 'cs': ['b_9'], 'h': 2, 't': 3, 'n': 2},
            {'k': 'play', 'p': 'b', 'c': 'b_drop', 'h': 1, 't': 4, 'n': 3},
            {'k': 'drop', 'p': 'b', 'out': ['b_9'], 'cs': ['y_5'], 'h': 1, 't': 4, 'n': 3},
            {'k': 'end', 'p': 'b', 'o': 'win', 't': 8, 'n': 5},
        )
        d = rs.summarize_log(log)
        a, b = d['storm']['a'], d['storm']['b']
        self.assertEqual((a['sw'], a['gain'], a['up'], a['p1'], a['cx']), (1, 1, 0, 1, 0))
        self.assertEqual((b['sw'], b['dr'], b['back'], b['cx']), (0, 1, 1, 1))
        self.assertEqual(d['a']['dealt'], 1)
        self.assertEqual(d['b']['ty'][c.STORM_VALUES.index(c.DROP) + 6], 2)

    def test_a_swap_to_a_smaller_hand_onto_uno(self):
        log = log_of(
            {'k': 'play', 'p': 'b', 'c': 'g_1', 'h': 2, 't': 1, 'n': 0},
            {'k': 'play', 'p': 'b', 'c': 'g_skip', 'h': 1, 't': 2, 'n': 1},
            {'k': 'play', 'p': 'a', 'c': 'g_swap', 'h': 2, 't': 3, 'n': 2},
            {'k': 'swap', 'p': 'a', 'vs': 'b', 'hs': {'a': ['y_2'], 'b': ['r_1', 'r_draw']},
             'h': 1, 'h2': 2, 't': 3, 'n': 2},
        )
        a = rs.summarize_log(log)['storm']['a']
        self.assertEqual((a['sw'], a['gain'], a['up'], a['uno']), (1, -1, 1, 0))

    def test_other_games_have_no_storm_summary(self):
        self.assertNotIn('storm', rs.summarize_log(log_of({'k': 'end', 'p': 'a', 'o': 'win', 't': 8, 'n': 5})))


if __name__ == '__main__':
    unittest.main()
