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

    def test_old_menu_from_other_group_is_not_played_here(self):
        """A tap on one group's old menu never plays a card in another group's game"""
        other_game = Game(stubs.MockChat(-2, "Other group"))
        alice_there = Player(other_game, self.alice.user)
        carol = Player(other_game, stubs.MockUser(3, "Carol"))
        other_game.started = True
        other_game.last_card = c.Card(c.RED, '5')
        alice_there.cards = [c.Card(c.RED, '7')]
        # Alice opened the other group's menu on her turn there; that turn has passed.
        # It's now her turn here, and her token here happens to have the same value.
        other_game.current_player = carol
        self.assertEqual(self.alice.anti_cheat, alice_there.anti_cheat)

        gm = MagicMock()
        gm.userid_current = {self.alice.user.id: alice_there}
        gm.userid_players = {self.alice.user.id: [self.alice, alice_there]}
        with patch('bot.gm', gm), patch('bot.send_async'), patch('bot.send_paced'), \
                patch('bot.start_player_countdown'), patch('bot.game_is_running', return_value=True), \
                patch('actions.gm', gm), patch('actions.send_async'):
            raw_process_result(chosen_result(self.alice.user, f"r_7:{alice_there.anti_cheat}"), MagicMock())

        self.assertEqual(self.game.last_card, c.Card(c.RED, '5'))
        self.assertIn(c.Card(c.RED, '7'), self.alice.cards)
        self.assertEqual(other_game.last_card, c.Card(c.RED, '5'))
        self.assertIs(gm.userid_current[self.alice.user.id], alice_there)

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

    def test_user_can_change_mode(self):
        from utils import user_can_change_mode
        self.game.owner = {self.alice.user.id}
        self.game.started = False
        self.assertTrue(user_can_change_mode(self.alice.user, self.game))
        self.assertFalse(user_can_change_mode(self.bob.user, self.game))

        self.game.started = True
        self.assertFalse(user_can_change_mode(self.alice.user, self.game))

        self.assertFalse(user_can_change_mode(None, self.game))
        self.assertFalse(user_can_change_mode(self.alice.user, None))

    def test_make_mode_keyboard(self):
        from utils import make_mode_keyboard
        markup = make_mode_keyboard()
        self.assertEqual(len(markup.inline_keyboard), 1)
        row = markup.inline_keyboard[0]
        self.assertEqual(len(row), 1)
        btn = row[0]
        self.assertEqual(btn.text, "Change game mode")
        self.assertEqual(btn.switch_inline_query_current_chat, '')
        self.assertIsNone(btn.callback_data)

    def test_join_game_shows_mode_button_for_creator_before_start(self):
        self.game.started = False
        self.game.owner = {self.alice.user.id}

        update = MagicMock()
        update.message.chat = self.game.chat
        update.message.from_user = self.alice.user
        update.message.message_id = 42
        context = MagicMock()

        gm = MagicMock()
        gm.join_game.return_value = self.game

        raw_join_game = inspect.unwrap(bot.join_game)
        with patch('bot.gm', gm), patch('bot.send_async') as mock_send:
            raw_join_game(update, context)

        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        self.assertIn('reply_markup', kwargs)
        markup = kwargs['reply_markup']
        self.assertEqual(markup.inline_keyboard[0][0].text, "Change game mode")
        self.assertEqual(markup.inline_keyboard[0][0].switch_inline_query_current_chat, '')

    def test_join_game_no_button_for_non_creator(self):
        self.game.started = False
        self.game.owner = {self.alice.user.id}

        update = MagicMock()
        update.message.chat = self.game.chat
        update.message.from_user = self.bob.user
        update.message.message_id = 43
        context = MagicMock()

        gm = MagicMock()
        gm.join_game.return_value = self.game

        raw_join_game = inspect.unwrap(bot.join_game)
        with patch('bot.gm', gm), patch('bot.send_async') as mock_send:
            raw_join_game(update, context)

        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        self.assertNotIn('reply_markup', kwargs)

    def test_join_game_no_button_after_game_started(self):
        self.game.started = True
        self.game.owner = {self.alice.user.id}

        update = MagicMock()
        update.message.chat = self.game.chat
        update.message.from_user = self.alice.user
        update.message.message_id = 44
        context = MagicMock()

        gm = MagicMock()
        gm.join_game.return_value = self.game

        raw_join_game = inspect.unwrap(bot.join_game)
        with patch('bot.gm', gm), patch('bot.send_async') as mock_send:
            raw_join_game(update, context)

        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        self.assertNotIn('reply_markup', kwargs)

    def test_join_game_already_joined_shows_button_for_creator_before_start(self):
        from errors import AlreadyJoinedError
        self.game.started = False
        self.game.owner = {self.alice.user.id}

        update = MagicMock()
        update.message.chat = self.game.chat
        update.message.from_user = self.alice.user
        update.message.message_id = 45
        context = MagicMock()

        gm = MagicMock()
        gm.join_game.side_effect = AlreadyJoinedError()
        gm.player_for_user_in_chat.return_value = self.alice

        raw_join_game = inspect.unwrap(bot.join_game)
        with patch('bot.gm', gm), patch('bot.send_async') as mock_send:
            raw_join_game(update, context)

        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        self.assertIn('reply_markup', kwargs)
        markup = kwargs['reply_markup']
        self.assertEqual(markup.inline_keyboard[0][0].text, "Change game mode")



if __name__ == '__main__':
    unittest.main()
