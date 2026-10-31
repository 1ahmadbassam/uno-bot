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
import utils
from deck import Deck
from errors import DeckEmptyError
from game import Game
from player import Player
from test.test_crazy import game_of, send
from test.test_fairness_proof import check, play_a_game
from test.test_inline_validation import chosen_result
from test.test_rivalry_log import A as A_ID, I as I_ID, LoggedGame

XENO = c.Card(None, None, c.XENO)


def card(name):
    return c.from_str(name)


def deck_of(game, *names):
    """Makes the deck give `names` in this order"""
    queue = [card(name) for name in names]
    return patch.object(game.deck, 'draw', side_effect=lambda seat=None: queue.pop(0))


class TestXenoMode(unittest.TestCase):

    def test_mode(self):
        mode = modes.get('xeno')
        self.assertEqual(mode.message, 'Project Xeno 👽️')
        self.assertEqual((mode.deck, mode.hand_size, mode.xeno), ('xeno', 7, True))
        self.assertFalse(modes.CLASSIC.xeno)

    def test_deck_has_numbers_draw_twos_alphas_and_xenos(self):
        deck = Deck()
        deck.fill('xeno')
        names = Counter(map(str, deck.cards))
        self.assertEqual(len(deck.cards), 100)
        self.assertEqual(names['xeno'], 8)
        for color in c.COLORS:
            self.assertEqual((names[color + '_alpha'], names[color + '_draw'], names[color + '_0'], names[color + '_9']),
                             (2, 2, 1, 2))
        self.assertFalse([name for name in names if name.split('_')[-1] in ('skip', 'reverse', 'colorchooser', 'four')])

    def test_cards(self):
        self.assertEqual(c.label(XENO), '⬛️Xeno')
        self.assertEqual(c.label(card('r_alpha')), '❤️Alpha')
        self.assertEqual(c.from_str('xeno:g').color, c.GREEN)
        for name in ('xeno', 'r_alpha', 'b_alpha', 'g_alpha', 'y_alpha'):
            self.assertIn(name, c.STICKERS)
            self.assertIn(name, c.STICKERS_GREY)

    def test_games_play_through_and_pass_the_fairness_check(self):
        game = play_a_game('xeno', players=3)
        self.assertEqual(check(game.rng.proof())[0], None)
        ops = game.rng.ops
        xenos = [op for op in ops if op[0] == 'x' and op[1] == 'xeno']
        self.assertTrue(xenos)
        self.assertGreaterEqual(sum(1 for op in ops if op[0] == 'c'), len(xenos))


class TestXenoRules(unittest.TestCase):

    def setUp(self):
        self.game, (self.p1, self.p2) = game_of('xeno', 'Alice', 'Ivan')
        self.game.current_player = self.p1

    def playable(self, top, *names, counter=0):
        self.game.last_card = card(top) if isinstance(top, str) else top
        self.game.draw_counter = counter
        self.p1.cards = [card(name) for name in names]
        return [str(x) for x in self.p1.playable_cards()]

    def test_the_bot_picks_the_xenos_color_and_the_turn_passes(self):
        with patch.object(self.game.rng, 'choose_color', return_value=c.YELLOW) as pick:
            sent = send(self.p1, XENO)
        pick.assert_called_once_with(self.game.colors)
        self.assertEqual(self.game.last_card.color, c.YELLOW)
        self.assertFalse(self.game.choosing_color)
        self.assertIs(self.game.current_player, self.p2)
        self.assertNotIn("Please choose a color", [call[1]['text'] for call in sent.call_args_list])

    def test_a_xeno_goes_on_any_card_but_a_pile_or_another_xeno(self):
        self.assertEqual(self.playable('g_7', 'xeno'), ['xeno'])
        self.assertEqual(self.playable('b_alpha', 'xeno'), ['xeno'])
        self.assertEqual(self.playable('r_draw', 'xeno', counter=2), [])
        self.assertEqual(self.playable(c.Card(c.GREEN, None, c.XENO), 'xeno'), [])

    def test_only_its_color_goes_on_a_xeno(self):
        top = c.Card(c.GREEN, None, c.XENO)
        # Not even a number adding up to 5 with anything
        self.assertEqual(self.playable(top, 'g_5', 'g_alpha', 'g_draw', 'r_5', 'b_0', 'xeno'), ['g_5', 'g_alpha', 'g_draw'])

    def test_an_alpha_only_goes_on_its_color(self):
        self.assertEqual(self.playable('r_3', 'r_alpha', 'b_alpha'), ['r_alpha'])
        self.assertEqual(self.playable('b_alpha', 'r_alpha', 'b_alpha', 'b_7', 'r_7'), ['b_alpha', 'b_7'])

    def test_the_x_rule(self):
        self.assertEqual(self.playable('r_3', 'b_2', 'g_2', 'b_4', 'y_3'), ['b_2', 'g_2', 'y_3'])
        self.assertEqual(self.playable('y_5', 'b_0', 'g_5', 'b_1'), ['b_0', 'g_5'])
        # Numbers only: a +2 isn't a 2, an Alpha has no value
        self.assertEqual(self.playable('r_3', 'b_draw', 'b_alpha'), [])
        self.assertEqual(self.playable('r_alpha', 'b_5', 'b_0'), [])
        # Not on a pile to draw
        self.assertEqual(self.playable('r_draw', 'b_3', counter=2), [])

    def test_no_x_rule_in_other_modes(self):
        self.game.mode = 'classic'
        self.assertEqual(self.playable('r_3', 'b_2'), [])

    def test_drawing_on_a_xeno_goes_on_until_its_color(self):
        self.playable(c.Card(c.GREEN, None, c.XENO), 'r_1')
        with deck_of(self.game, 'b_1', 'r_alpha', 'g_7', 'g_8'):
            self.p1.draw()
        self.assertEqual([str(x) for x in self.p1.cards], ['r_1', 'b_1', 'r_alpha', 'g_7'])
        self.assertEqual(self.p1.playable_cards(), [card('g_7')])
        self.assertTrue(self.p1.must_play())
        self.assertFalse(self.p1.can_pass())

    def test_a_card_of_its_color_drawn_first_ends_it(self):
        self.playable(c.Card(c.GREEN, None, c.XENO), 'g_1')
        with deck_of(self.game, 'g_2', 'g_3'):
            self.p1.draw()
        self.assertEqual(len(self.p1.cards), 2)
        self.assertTrue(self.p1.must_play())

    def test_a_deck_running_out_ends_it_and_allows_passing(self):
        self.playable(c.Card(c.GREEN, None, c.XENO), 'r_1')
        self.game.deck.cards, self.game.deck.graveyard = [card('b_1')], []
        with self.assertRaises(DeckEmptyError):
            self.p1.draw()
        self.assertEqual(len(self.p1.cards), 2)
        self.assertTrue(self.p1.can_pass())

    def test_other_draws_are_one_card_and_can_be_passed(self):
        self.playable('g_7', 'r_1')
        with deck_of(self.game, 'b_1', 'g_2'):
            self.p1.draw()
        self.assertEqual(len(self.p1.cards), 2)
        self.assertTrue(self.p1.can_pass())

    def test_no_pass_button_nor_result_with_the_card_to_play(self):
        self.playable(c.Card(c.GREEN, None, c.XENO), 'r_1')
        with deck_of(self.game, 'g_2'):
            self.p1.draw()
        self.assertFalse(bot._inline_action_allowed(self.p1, self.game, 'pass'))
        self.assertTrue(bot._inline_action_allowed(self.p1, self.game, 'g_2'))
        buttons = [b.callback_data for row in utils.make_turn_keyboard(self.game, -1).inline_keyboard for b in row]
        self.assertFalse([data for data in buttons if data and data.endswith(':pass')])

    def test_a_timeout_on_a_xeno_draws_until_its_color(self):
        self.playable(c.Card(c.GREEN, None, c.XENO), 'r_1')
        with deck_of(self.game, 'b_1', 'g_2'), patch.object(actions, 'gm', MagicMock()), \
                patch.object(actions, 'send_paced'), patch.object(actions, 'send_async'):
            actions.do_skip(MagicMock(), self.p1)
        self.assertEqual(len(self.p1.cards), 3)
        self.assertIs(self.game.current_player, self.p2)

    def test_a_xeno_turned_up_first_gets_a_color(self):
        game = Game(stubs.MockChat(-1))
        game.mode = 'xeno'
        for i, name in enumerate(("Alice", "Ivan")):
            Player(game, stubs.MockUser(i + 1, name))
        game.deck.cards = [c.Card(c.BLUE, '1')] * 20 + [c.Card(None, None, c.XENO)]
        with patch.object(game.deck, 'draw', side_effect=lambda: game.deck.cards.pop()), \
                patch.object(game.rng, 'choose_color', return_value=c.GREEN):
            game._first_card_()
        self.assertEqual((game.last_card.special, game.last_card.color), (c.XENO, c.GREEN))
        self.assertFalse(game.choosing_color)


class TestXenoMessages(unittest.TestCase):

    def test_the_next_player_message_says_the_color(self):
        game, (alice, ivan) = game_of('xeno', 'Alice', 'Ivan')
        game.current_player = alice
        alice.cards.append(XENO)
        gm = MagicMock()
        gm.userid_current = {alice.user.id: alice}
        with patch('bot.gm', gm), patch('bot.send_async'), patch('bot.send_paced') as paced, \
                patch('bot.start_player_countdown'), patch('bot.game_is_running', return_value=True), \
                patch('actions.gm', gm), patch('actions.send_async') as sent, \
                patch.object(game.rng, 'choose_color', return_value=c.BLUE):
            inspect.unwrap(bot.process_result)(chosen_result(alice.user, "xeno:%s" % alice.anti_cheat), MagicMock())
        sent.assert_not_called()
        text = paced.call_args[1]['text']
        self.assertTrue(text.startswith("👽 Xeno set the color to "), text)
        self.assertIn("Next player: Ivan", text)

    def test_the_first_message_says_the_color_of_an_opening_xeno(self):
        game = Game(stubs.MockChat(-1))
        game.mode = 'xeno'
        for i, name in enumerate(("Alice", "Ivan")):
            Player(game, stubs.MockUser(i + 1, name))

        def first_card():
            game.last_card = c.Card(c.YELLOW, None, c.XENO)

        sent = MagicMock()
        with patch.object(game, '_first_card_', first_card), patch('bot.gm'), \
                patch('bot.start_player_countdown'), patch('bot.dispatcher') as dispatcher:
            bot._deal_and_announce(sent, game, game.chat, None)
            dispatcher.run_async.call_args[0][0]()
        text = sent.sendMessage.call_args[1]['text']
        self.assertTrue(text.startswith("👽 Xeno set the color to "), text)

    def test_the_draw_button_counts_every_card_and_says_to_play_it(self):
        game, (alice, ivan) = game_of('xeno', 'Alice', 'Ivan')
        game.current_player = alice
        game.last_card = c.Card(c.GREEN, None, c.XENO)
        query = MagicMock()
        query.from_user.id = alice.user.id
        with deck_of(game, 'b_1', 'r_2', 'g_7'), patch('bot.gm'), patch('actions.gm'), \
                patch('bot.start_player_countdown'), patch('bot.game_is_running', return_value=True), \
                patch('bot._answer_callback') as answer, patch('bot._edit_message_text') as edit:
            bot._action_callback_for_game(MagicMock(), query, game, -1, 'draw', MagicMock())
        self.assertIn("drew 3 cards.", edit.call_args[1]['text'])
        self.assertIn("you have to play it", answer.call_args[0][2])
        self.assertIs(game.current_player, alice)


class TestXenoLog(LoggedGame):

    def setUp(self):
        super().setUp()
        self.game.mode = 'xeno'
        rivalry_log.start(self.game)

    def play(self, name, color=None):
        self.game.current_player = self.alice
        self.alice.cards.append(card(name))
        with patch.object(self.game.rng, 'choose_color', return_value=color):
            actions.do_play_card(MagicMock(), self.alice, name)

    def test_the_color_is_the_bots(self):
        # Alice holds two reds and a blue after it
        self.play('xeno', c.RED)
        play, = self.events('play')
        color, = self.events('color')
        self.assertEqual((play['c'], play['uc']), ('xeno', c.RED))
        self.assertEqual((color['co'], color['xe'], color['lk']), (c.RED, 1, 1))
        for key in ('au', 'th', 'dia'):
            self.assertNotIn(key, color)

    def test_x_rule_plays_are_flagged(self):
        # On the red 5: a blue 0 by the X-rule, then a green 0 by its value, and a red 2
        # on a red 3, by its color
        self.play('b_0')
        self.play('g_0')
        self.game.last_card = card('r_3')
        self.play('r_2')
        self.assertEqual([e.get('x5') for e in self.events('play')], [1, None, None])

    def test_drawing_for_a_xenos_color_is_flagged(self):
        self.game.last_card = c.Card(c.GREEN, None, c.XENO)
        with deck_of(self.game, 'b_1', 'g_2'):
            actions.do_draw(MagicMock(), self.alice)
        draw, = self.events('draw')
        self.assertEqual((draw['cs'], draw['hu'], draw['r'], draw['ok']), (['b_1', 'g_2'], 1, 'vol', 1))

    def test_a_game_marks_its_end_and_sums_up(self):
        self.play('xeno', c.BLUE)
        self.play('r_alpha')
        self.game.last_card = card('r_3')
        self.play('b_2')
        self.game.last_card = c.Card(c.GREEN, None, c.XENO)
        with deck_of(self.game, 'b_1', 'r_4', 'g_2'):
            actions.do_draw(MagicMock(), self.alice)
        end = rivalry_log.finish(self.game, A_ID, 'win')
        self.assertEqual(end['ev'][-1]['xe'], 1)
        d = rs.summarize_log(end)
        self.assertEqual(d['xeno']['a'], {'k': 0, 'p': 1, 'col': [0, 1, 0, 0], 'lk': 0, 'al': 1, 'x5': 1,
                                          'hu': 1, 'hc': 3, 'big': 3})
        self.assertEqual(d['a']['ty'][rs.CARD_TYPES.index('xen')], 1)
        self.assertEqual(d['a']['ty'][rs.CARD_TYPES.index('alp')], 1)
        self.assertEqual((d['a']['au'], d['a']['pk']), (0, [0, 0, 0, 0]))

    def test_a_xeno_turned_up_first_logs_its_color(self):
        self.game.last_card = c.Card(c.YELLOW, None, c.XENO)
        rivalry_log.start(self.game)
        self.assertEqual(self.events()[0]['fco'], c.YELLOW)

    def test_stats_lines(self):
        self.play('xeno', c.RED)
        self.game.last_card = c.Card(c.GREEN, None, c.XENO)
        with deck_of(self.game, 'b_1', 'g_2'):
            actions.do_draw(MagicMock(), self.alice)
        log = rivalry_log.finish(self.game, A_ID, 'win')
        rivals = rs.Rivals([(A_ID, "Alice"), (I_ID, "Ivan")])
        result = rs.Result(1, 1, rs.datetime(2026, 11, 1), A_ID, I_ID, 'win', None, 'xeno', 2, 3, 2)
        lines = rs.game_stats_text(rivals, 1, result, log, rs.timezone.utc, str).split('\n')
        self.assertIn("👽 Xenos · Alphas · X-rule plays: Alice 1 · 0 · 0 · Ivan 0 · 0 · 0", lines)
        self.assertIn("Xeno gave the color held most: Alice 1 of 1 · Ivan 0 of 0", lines)
        self.assertIn("Drawn to find a Xeno's color: Alice 1 time (+2 cards) · Ivan 0 times (+0 cards)", lines)
