import inspect
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Installs the module stubs needed to import bot
import test.test_rate_limit_mitigations as stubs

import bot
import card as c
from actions import do_draw, do_play_card, do_skip, cancel_penalty_after_leave
from errors import (DeckEmptyError, LobbyFullError, NotEnoughPlayersError,
                    RejoinNotAllowedError)
from game import Game
from game_manager import GameManager
from player import Player

BOT = SimpleNamespace(sendMessage=lambda *args, **kwargs: None)


def leave_like_game_manager(game):
    """A gm.leave_game stand-in with the real turn/leave behaviour"""
    def leave(user, chat):
        player = next(p for p in game.players if p.user.id == user.id)
        if len(game.players) < 3:
            raise NotEnoughPlayersError()
        if player is game.current_player:
            game.turn()
        player.leave()
    return leave


class TestFairnessAudit(unittest.TestCase):

    def setUp(self):
        self.game = Game(stubs.MockChat(-1))
        self.alice = Player(self.game, stubs.MockUser(1, "Alice"))
        self.bob = Player(self.game, stubs.MockUser(2, "Bob"))
        self.game.started = True
        self.game.deck.cards = [c.Card(c.GREEN, '1') for _ in range(30)]
        self.game.last_card = c.Card(c.RED, '5')
        self.game.current_player = self.alice

    def add_charlie(self):
        self.charlie = Player(self.game, stubs.MockUser(3, "Charlie"))

    def patched_actions(self):
        gm = MagicMock()
        gm.leave_game.side_effect = leave_like_game_manager(self.game)
        return gm, patch('actions.rivalry')

    def win_with(self, card):
        self.alice.cards = [card]
        gm, rivalry_patch = self.patched_actions()
        with patch('actions.gm', gm), rivalry_patch as rivalry, patch('actions.send_async'):
            do_play_card(BOT, self.alice, str(card))
        return gm, rivalry

    # VULN-01
    def test_two_player_win_with_skip_records_the_winner(self):
        gm, rivalry = self.win_with(c.Card(c.RED, c.SKIP))
        rivalry.record_first_place.assert_called_once_with(BOT, self.game, self.alice.user)
        gm.end_game.assert_called_once()

    def test_two_player_win_with_reverse_records_the_winner(self):
        _, rivalry = self.win_with(c.Card(c.RED, c.REVERSE))
        rivalry.record_first_place.assert_called_once_with(BOT, self.game, self.alice.user)

    def test_two_player_deferred_draw_four_win_records_the_winner(self):
        self.alice.cards = [c.Card(None, None, special=c.DRAW_FOUR)]
        gm, rivalry_patch = self.patched_actions()
        with patch('actions.gm', gm), rivalry_patch as rivalry, patch('actions.send_async'):
            do_play_card(BOT, self.alice, 'draw_four')
            self.assertTrue(self.alice.pending_win)
            self.assertIs(self.game.current_player, self.bob)
            rivalry.record_first_place.assert_not_called()
            do_draw(BOT, self.bob)
        rivalry.record_first_place.assert_called_once_with(BOT, self.game, self.alice.user)

    # VULN-02
    def test_penalty_is_cancelled_when_its_target_leaves(self):
        self.add_charlie()
        self.game.last_card = c.Card(c.RED, c.DRAW_TWO)
        self.game.draw_counter = 2
        self.game.current_player = self.bob
        leave_like_game_manager(self.game)(self.bob.user, None)

        with patch('actions.send_async'), patch('actions.gm'):
            cancel_penalty_after_leave(BOT, self.game, was_current=True, was_choosing=False)
        self.assertIs(self.game.current_player, self.charlie)
        self.assertEqual(self.game.draw_counter, 0)

    def test_penalty_stays_when_its_player_left_while_choosing_a_color(self):
        self.add_charlie()
        self.game.draw_counter = 4  # Bob played the +4 and is choosing; it's Charlie's penalty
        with patch('actions.send_async'), patch('actions.gm'):
            cancel_penalty_after_leave(BOT, self.game, was_current=True, was_choosing=True)
        self.assertEqual(self.game.draw_counter, 4)

    def test_timeout_removal_does_not_pass_penalty_on(self):
        self.add_charlie()
        self.game.last_card = c.Card(c.RED, c.DRAW_TWO)
        self.game.draw_counter = 2
        self.game.current_player = self.bob
        self.bob.waiting_time = 0
        charlie_hand = len(self.charlie.cards)
        gm, _ = self.patched_actions()
        with patch('actions.gm', gm), patch('actions.send_async'), patch('actions.send_paced'):
            do_skip(BOT, self.bob)
        self.assertIs(self.game.current_player, self.charlie)
        self.assertEqual(self.game.draw_counter, 0)
        self.assertEqual(len(self.charlie.cards), charlie_hand)

    # VULN-03
    def kick(self, kicker, kicked):
        gm = MagicMock()
        gm.chatid_games = {self.game.chat.id: [self.game]}
        gm.player_for_user_in_chat.side_effect = lambda user, chat: next(
            (p for p in self.game.players if p.user.id == user.id), None)
        gm.leave_game.side_effect = NotEnoughPlayersError()
        update = MagicMock()
        update.message.chat = self.game.chat
        update.message.from_user = kicker
        update.message.reply_to_message.from_user = kicked
        with patch('bot.gm', gm), patch('bot.send_async'), \
                patch('bot.user_is_creator_or_admin', return_value=True), \
                patch('bot.forfeit_win') as forfeit, \
                patch('bot.end_game_without_winner') as no_winner:
            inspect.unwrap(bot.kick_player)(update, MagicMock())
        return forfeit, no_winner

    def test_kicking_the_only_opponent_awards_no_win(self):
        forfeit, no_winner = self.kick(self.alice.user, self.bob.user)
        forfeit.assert_not_called()
        no_winner.assert_called_once_with(unittest.mock.ANY, self.game)

    def test_admin_outside_the_game_kicking_still_credits_forfeit(self):
        forfeit, no_winner = self.kick(stubs.MockUser(99, "Admin"), self.bob.user)
        forfeit.assert_called_once()
        no_winner.assert_not_called()

    # VULN-04 / VULN-08
    def lobby(self, players=3):
        gm = GameManager()
        chat = stubs.MockChat(-7)
        patcher = patch.object(gm, '_persist_chat')
        patcher.start()
        self.addCleanup(patcher.stop)
        game = gm.new_game(chat)
        users = [stubs.MockUser(10 + i, f"P{i}") for i in range(players)]
        for user in users:
            gm.join_game(user, chat)
        return gm, chat, game, users

    def test_player_who_left_a_started_game_cannot_rejoin(self):
        gm, chat, game, users = self.lobby()
        game.started = True
        game.last_card = c.Card(c.RED, '5')
        gm.leave_game(users[0], chat)
        with self.assertRaises(RejoinNotAllowedError):
            gm.join_game(users[0], chat)

        restored = gm._deserialize_game(gm._serialize_game(game))
        self.assertIn(users[0].id, restored.left_users)

    def test_leaving_before_start_allows_rejoin(self):
        gm, chat, game, users = self.lobby()
        gm.leave_game(users[0], chat)
        gm.join_game(users[0], chat)
        self.assertEqual(len(game.players), 3)

    def test_join_refused_when_game_is_full(self):
        with patch('game_manager.MAX_PLAYERS', 3):
            gm, chat, game, users = self.lobby()
            with self.assertRaises(LobbyFullError):
                gm.join_game(stubs.MockUser(50, "Late"), chat)

    def test_start_with_too_many_players_is_undone(self):
        game = Game(stubs.MockChat(-8))
        for i in range(16):
            Player(game, stubs.MockUser(100 + i, f"P{i}"))
        gm = MagicMock()
        gm.chatid_games = {game.chat.id: [game]}
        update = MagicMock()
        update.message.chat = game.chat
        with patch('bot.gm', gm), patch('bot.send_async') as send, \
                patch('bot.start_player_countdown') as countdown:
            inspect.unwrap(inspect.unwrap(bot.start_game))(update, MagicMock())
        self.assertFalse(game.started)
        self.assertIsNone(game.last_card)
        self.assertTrue(all(not p.cards for p in game.players))
        self.assertIn("not enough cards", send.call_args[1]['text'])
        countdown.assert_not_called()

    # VULN-05 / VULN-06
    def skip(self, issuer, turn_started):
        self.add_charlie()
        self.game.current_player = self.bob
        self.bob.turn_started = turn_started
        gm = MagicMock()
        gm.player_for_user_in_chat.return_value = issuer
        update = MagicMock()
        update.message.chat = self.game.chat
        context = MagicMock()
        with patch('bot.gm', gm), patch('bot.send_async'), patch('bot.do_skip') as skip:
            inspect.unwrap(inspect.unwrap(bot.skip_player))(update, context)
        return skip, context

    def test_skip_refused_when_clock_went_back(self):
        skip, _ = self.skip(self.alice, datetime.now() + timedelta(seconds=5))
        skip.assert_not_called()

    def test_skip_passes_current_player_and_job_queue(self):
        skip, context = self.skip(self.alice, datetime.now() - timedelta(seconds=1000))
        skip.assert_called_once_with(context.bot, self.bob, context.job_queue)

    def test_leave_by_current_player_restarts_countdown(self):
        self.add_charlie()
        self.game.current_player = self.bob
        gm = MagicMock()
        gm.player_for_user_in_chat.return_value = self.bob
        gm.leave_game.side_effect = leave_like_game_manager(self.game)
        update = MagicMock()
        update.message.chat = self.game.chat
        update.message.from_user = self.bob.user
        context = MagicMock()
        with patch('bot.gm', gm), patch('bot.send_async'), patch('actions.gm'), \
                patch('actions.send_async'), patch('bot.start_player_countdown') as countdown:
            inspect.unwrap(bot.leave_game)(update, context)
        countdown.assert_called_once_with(context.bot, self.game, context.job_queue)

    # VULN-07
    def test_call_bluff_not_allowed_while_choosing_color(self):
        self.game.last_card = c.Card(None, None, special=c.DRAW_FOUR)
        self.game.draw_counter = 4
        self.game.choosing_color = True
        self.assertFalse(bot._inline_action_allowed(self.alice, self.game, 'call_bluff'))

    # VULN-10
    def test_accepted_draw_four_clears_last_bluffer(self):
        self.game.last_card = c.Card(c.BLUE, None, special=c.DRAW_FOUR)
        self.game.draw_counter = 4
        self.game.last_bluffer = self.bob
        self.bob.cards = [c.Card(c.RED, '3')]
        with patch('actions.gm'), patch('actions.send_async'):
            do_draw(BOT, self.alice)
        self.assertIsNone(self.game.last_bluffer)
        self.assertEqual(len(self.alice.cards), 4)


if __name__ == '__main__':
    unittest.main()
