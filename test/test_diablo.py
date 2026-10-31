import inspect
import unittest
from collections import Counter
from unittest.mock import MagicMock, patch

# Installs the module stubs needed to import the game modules
import test.test_rate_limit_mitigations as stubs

import actions
import bot
import card as c
import modes
import rivalry_log
import rivalry_stats as rs
from deck import Deck
from game import Game
from player import Player
from test.test_crazy import game_of, send
from test.test_fairness_proof import check, play_a_game
from test.test_inline_validation import chosen_result
from test.test_rivalry_log import A as A_ID, I as I_ID, LoggedGame

DIABLO = c.Card(None, None, c.DIABLO)


class TestDiabloMode(unittest.TestCase):

    def test_mode(self):
        mode = modes.get('diablo')
        self.assertEqual(mode.message, 'Welcome to Hell 💀️')
        self.assertEqual((mode.deck, mode.hand_size, mode.bluff, mode.diablo), ('diablo', 7, False, True))
        self.assertFalse(modes.CLASSIC.diablo)

    def test_deck_is_the_classic_one_and_eight_diablos(self):
        diablo, classic = Deck(), Deck()
        diablo.fill('diablo')
        classic.fill('classic')
        extra = Counter(map(str, diablo.cards)) - Counter(map(str, classic.cards))
        self.assertEqual(extra, Counter({'diablo': 8}))
        self.assertEqual(len(diablo.cards), 116)

    def test_diablo_card(self):
        self.assertFalse(c.is_draw_wild(DIABLO))
        self.assertEqual(c.label(DIABLO), '⬛️Diablo')
        self.assertEqual(c.spaced_label(c.Card(c.RED, None, c.DIABLO)), '❤️⬛️ Diablo')
        self.assertEqual(str(c.from_str('diablo')), 'diablo')
        self.assertEqual(c.from_str('diablo:g').color, c.GREEN)
        self.assertIn('diablo', c.STICKERS)
        self.assertIn('diablo', c.STICKERS_GREY)

    def test_games_play_through_and_pass_the_fairness_check(self):
        game = play_a_game('diablo', players=3)
        self.assertEqual(check(game.rng.proof())[0], None)
        # Every Diablo played got its color from the seed
        ops = game.rng.ops
        diablos = [i for i, op in enumerate(ops) if op[0] == 'x' and op[1] == 'diablo']
        self.assertTrue(diablos)
        self.assertGreaterEqual(sum(1 for op in ops if op[0] == 'c'), len(diablos))


class TestDiabloCard(unittest.TestCase):

    def setUp(self):
        self.game, (self.p1, self.p2) = game_of('diablo', 'Alice', 'Ivan')
        self.game.current_player = self.p1

    def test_the_bot_picks_the_color_and_the_turn_passes(self):
        with patch.object(self.game.rng, 'choose_color', return_value=c.YELLOW) as pick:
            sent = send(self.p1, DIABLO)
        pick.assert_called_once_with(self.game.colors)
        self.assertEqual(self.game.last_card.color, c.YELLOW)
        self.assertFalse(self.game.choosing_color)
        self.assertIs(self.game.current_player, self.p2)
        self.assertNotIn("Please choose a color", [call[1]['text'] for call in sent.call_args_list])

    def test_it_goes_on_any_color(self):
        self.game.last_card = c.Card(c.GREEN, c.SKIP)
        self.p1.cards = [DIABLO]
        self.assertEqual(self.p1.playable_cards(), [DIABLO])

    def test_not_on_a_pile_to_draw(self):
        for top in (c.Card(c.RED, c.DRAW_TWO), c.Card(c.RED, None, c.DRAW_FOUR)):
            self.game.last_card = top
            self.game.draw_counter = 2
            self.p1.cards = [DIABLO]
            self.assertEqual(self.p1.playable_cards(), [])

    def test_no_bluff_challenge(self):
        self.p1.cards.append(c.Card(c.RED, '9'))
        send(self.p1, c.Card(None, None, c.DRAW_FOUR))
        self.assertIsNone(self.game.last_bluffer)

    def test_winning_on_a_diablo(self):
        self.p1.cards = []
        with patch.object(actions, 'player_won') as won:
            sent = send(self.p1, DIABLO)
        won.assert_called_once_with(unittest.mock.ANY, self.p1)
        self.assertTrue(self.game.last_card.color)
        self.assertFalse([call for call in sent.call_args_list if 'Winning card' in call[1]['text']])

    def test_the_color_comes_from_the_game_seed(self):
        # Deal games until a player is dealt a Diablo
        for _ in range(50):
            game = play_a_game('diablo', players=2, max_turns=0)
            holder = next((p for p in game.players if DIABLO in p.cards), None)
            if holder:
                break
        game.current_player = holder
        game.choosing_color = False
        game.draw_counter = 0
        game.last_card.color = game.last_card.color or c.RED
        with patch.object(actions, 'gm', MagicMock()), patch.object(actions, 'send_async'):
            actions.do_play_card(MagicMock(), holder, 'diablo')
        self.assertEqual(game.rng.ops[-1], ['c', game.last_card.color])
        self.assertEqual(check(game.rng.proof())[0], None)

    def test_a_diablo_turned_up_first_gets_a_color(self):
        game = Game(stubs.MockChat(-1))
        game.mode = 'diablo'
        for i, name in enumerate(("Alice", "Ivan")):
            Player(game, stubs.MockUser(i + 1, name))
        game.deck.cards = [c.Card(c.BLUE, '1')] * 20 + [c.Card(None, None, c.DIABLO)]
        with patch.object(game.deck, 'draw', side_effect=lambda: game.deck.cards.pop()), \
                patch.object(game.rng, 'choose_color', return_value=c.GREEN):
            game._first_card_()
        self.assertEqual((game.last_card.special, game.last_card.color), (c.DIABLO, c.GREEN))
        self.assertFalse(game.choosing_color)


class TestDiabloMessage(unittest.TestCase):

    def test_the_next_player_message_says_the_color(self):
        game, (alice, ivan) = game_of('diablo', 'Alice', 'Ivan')
        game.current_player = alice
        alice.cards.append(DIABLO)
        gm = MagicMock()
        gm.userid_current = {alice.user.id: alice}
        with patch('bot.gm', gm), patch('bot.send_async'), patch('bot.send_paced') as paced, \
                patch('bot.start_player_countdown'), patch('bot.game_is_running', return_value=True), \
                patch('actions.gm', gm), patch('actions.send_async') as sent, \
                patch.object(game.rng, 'choose_color', return_value=c.BLUE):
            inspect.unwrap(bot.process_result)(chosen_result(alice.user, "diablo:%s" % alice.anti_cheat), MagicMock())
        sent.assert_not_called()
        text = paced.call_args[1]['text']
        self.assertTrue(text.startswith("💀 Diablo set the color to "), text)
        self.assertIn("Next player: Ivan", text)

    def test_the_first_message_says_the_color_of_an_opening_diablo(self):
        game = Game(stubs.MockChat(-1))
        game.mode = 'diablo'
        for i, name in enumerate(("Alice", "Ivan")):
            Player(game, stubs.MockUser(i + 1, name))

        def first_card():
            game.last_card = c.Card(c.YELLOW, None, c.DIABLO)

        sent = MagicMock()
        with patch.object(game, '_first_card_', first_card), patch('bot.gm'), \
                patch('bot.start_player_countdown'), patch('bot.dispatcher') as dispatcher:
            bot._deal_and_announce(sent, game, game.chat, None)
            dispatcher.run_async.call_args[0][0]()
        text = sent.sendMessage.call_args[1]['text']
        self.assertTrue(text.startswith("💀 Diablo set the color to "), text)
        self.assertIn("First player: ", text)


class TestDiabloLog(LoggedGame):

    def setUp(self):
        super().setUp()
        self.game.mode = 'diablo'
        rivalry_log.start(self.game)

    def play_diablo(self, color):
        self.alice.cards.append(DIABLO)
        with patch.object(self.game.rng, 'choose_color', return_value=color):
            actions.do_play_card(MagicMock(), self.alice, 'diablo')

    def test_the_color_is_the_bots(self):
        # Alice holds two reds and a blue after it
        self.play_diablo(c.RED)
        play, = self.events('play')
        color, = self.events('color')
        self.assertEqual((play['c'], play['uc']), ('diablo', c.RED))
        self.assertEqual((color['co'], color['dia'], color['lk']), (c.RED, 1, 1))
        self.assertNotIn('au', color)
        self.assertNotIn('th', color)

    def test_a_game_marks_its_end_and_counts_the_diablos(self):
        self.play_diablo(c.BLUE)
        self.game.last_card = c.Card(c.GREEN, '4')
        self.game.current_player = self.alice
        self.play_diablo(c.GREEN)
        end = rivalry_log.finish(self.game, A_ID, 'win')
        self.assertEqual(end['ev'][-1]['db'], 1)
        d = rs.summarize_log(end)
        self.assertEqual(d['diablo']['a'], {'k': 0, 'p': 2, 'col': [0, 1, 1, 0], 'same': 1, 'lk': 0})
        self.assertEqual(d['a']['ty'][rs.CARD_TYPES.index('dia')], 2)
        # Not the timeout stat, nor the colors players chose
        self.assertEqual((d['a']['au'], d['a']['pk']), (0, [0, 0, 0, 0]))

    def test_the_color_under_it_survives_a_wild_being_dismissed(self):
        for under in (c.Card(c.GREEN, None, c.CHOOSE), c.Card(c.GREEN, None, c.DRAW_FOUR),
                      c.Card(c.GREEN, None, c.DIABLO)):
            self.game.last_card = under
            self.game.current_player = self.alice
            self.play_diablo(c.GREEN)
            self.assertEqual(self.events('play')[-1]['uc'], c.GREEN)
        end = rivalry_log.finish(self.game, A_ID, 'win')
        self.assertEqual(rs.summarize_log(end)['diablo']['a']['same'], 3)

    def test_a_diablo_turned_up_first_logs_its_color(self):
        self.game.last_card = c.Card(c.YELLOW, None, c.DIABLO)
        rivalry_log.start(self.game)
        self.assertEqual(self.events()[0]['fco'], c.YELLOW)

    def test_stats_lines(self):
        self.play_diablo(c.RED)
        log = rivalry_log.finish(self.game, A_ID, 'win')
        rivals = rs.Rivals([(A_ID, "Alice"), (I_ID, "Ivan")])
        result = rs.Result(1, 1, rs.datetime(2026, 11, 1), A_ID, I_ID, 'win', None, 'diablo', 2, 3, 2)
        lines = rs.game_stats_text(rivals, 1, result, log, rs.timezone.utc, str).split('\n')
        self.assertIn("💀 Diablos played: Alice 1 · Ivan 0", lines)
        self.assertIn("Diablo gave the color held most: Alice 1 of 1 · Ivan 0 of 0", lines)

