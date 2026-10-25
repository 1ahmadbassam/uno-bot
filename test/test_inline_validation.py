import inspect
import unittest
from unittest.mock import MagicMock, patch

# Installs the module stubs needed to import bot
import test.test_rate_limit_mitigations as stubs

import bot
import card as c
from game import Game
from player import Player

# Skip the locale/db decorators; they are not what is under test here
raw_process_result = inspect.unwrap(bot.process_result)


def chosen_result(user, result_id):
    update = MagicMock()
    update.chosen_inline_result.from_user = user
    update.chosen_inline_result.result_id = result_id
    return update


class TestInlineResultValidation(unittest.TestCase):
    """Results from a menu opened earlier stay tappable, so each action is checked against the game"""

    def setUp(self):
        self.game = Game(stubs.MockChat(-1))
        self.alice = Player(self.game, stubs.MockUser(1, "Alice"))
        self.bob = Player(self.game, stubs.MockUser(2, "Bob"))
        self.game.started = True
        self.game.current_player = self.alice
        self.game.last_card = c.Card(c.RED, '5')
        self.game.deck.cards = [c.Card(c.BLUE, '9') for _ in range(10)]
        self.alice.cards = [c.Card(c.RED, '7'), c.Card(c.GREEN, '1')]
        self.bob.cards = [c.Card(c.RED, '8'), c.Card(c.GREEN, '2')]

    def choose(self, player, result_id):
        gm = MagicMock()
        gm.userid_current = {player.user.id: player}
        with patch('bot.gm', gm), patch('bot.send_async'), patch('bot.send_paced'), \
                patch('bot.start_player_countdown'), patch('bot.game_is_running', return_value=True), \
                patch('actions.gm', gm), patch('actions.send_async'):
            raw_process_result(chosen_result(player.user, f"{result_id}:{player.anti_cheat}"), MagicMock())

    def test_valid_card_is_played(self):
        self.choose(self.alice, 'r_7')
        self.assertEqual(self.game.last_card, c.Card(c.RED, '7'))
        self.assertIs(self.game.current_player, self.bob)

    def test_card_out_of_turn_is_rejected(self):
        self.game.turn()  # Alice's turn passed (timeout or button) while her menu was open
        self.choose(self.alice, 'r_7')
        self.assertEqual(self.game.last_card, c.Card(c.RED, '5'))
        self.assertIn(c.Card(c.RED, '7'), self.alice.cards)
        self.assertIs(self.game.current_player, self.bob)

    def test_card_not_allowed_after_draw_is_rejected(self):
        self.alice.draw()  # drew via the button; only the drawn card may be played now
        self.choose(self.alice, 'r_7')
        self.assertEqual(self.game.last_card, c.Card(c.RED, '5'))
        self.assertIs(self.game.current_player, self.alice)

    def test_unplayable_card_is_rejected(self):
        self.choose(self.alice, 'g_1')
        self.assertEqual(self.game.last_card, c.Card(c.RED, '5'))
        self.assertIn(c.Card(c.GREEN, '1'), self.alice.cards)

    def test_card_not_in_hand_is_rejected(self):
        self.choose(self.alice, 'r_9')
        self.assertEqual(self.game.last_card, c.Card(c.RED, '5'))
        self.assertIs(self.game.current_player, self.alice)

    def test_card_while_choosing_color_is_rejected(self):
        self.game.choosing_color = True
        self.choose(self.alice, 'r_7')
        self.assertIn(c.Card(c.RED, '7'), self.alice.cards)

    def test_pass_out_of_turn_is_rejected(self):
        self.choose(self.bob, 'pass')
        self.assertIs(self.game.current_player, self.alice)

    def test_pass_before_drawing_is_rejected(self):
        self.choose(self.alice, 'pass')
        self.assertIs(self.game.current_player, self.alice)

    def test_pass_after_drawing_is_allowed(self):
        self.alice.draw()
        self.choose(self.alice, 'pass')
        self.assertIs(self.game.current_player, self.bob)

    def test_second_draw_is_rejected(self):
        self.alice.draw()
        hand = len(self.alice.cards)
        self.choose(self.alice, 'draw')
        self.assertEqual(len(self.alice.cards), hand)

    def test_draw_out_of_turn_is_rejected(self):
        hand = len(self.bob.cards)
        self.choose(self.bob, 'draw')
        self.assertEqual(len(self.bob.cards), hand)

    def test_mode_change_after_start_is_rejected(self):
        self.game.owner = {self.alice.user.id}
        self.game.mode = 'classic'
        self.choose(self.alice, 'mode_wild')
        self.assertEqual(self.game.mode, 'classic')

    def test_mode_change_before_start_by_creator_is_allowed(self):
        self.game.started = False
        self.game.owner = {self.alice.user.id}
        self.choose(self.alice, 'mode_wild')
        self.assertEqual(self.game.mode, 'wild')

    def test_mode_change_by_non_creator_is_rejected(self):
        self.game.started = False
        self.game.owner = {self.alice.user.id}
        self.game.mode = 'classic'
        self.choose(self.bob, 'mode_wild')
        self.assertEqual(self.game.mode, 'classic')


if __name__ == '__main__':
    unittest.main()
