import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Installs the module stubs needed to import bot
import test.test_rate_limit_mitigations as stubs

import bot
from game_manager import GameManager
from utils import user_can_change_mode

CREATOR, PLAYER, ADMIN, STRANGER = (stubs.MockUser(i, name) for i, name in
                                    ((1, "Creator"), (2, "Player"), (3, "Admin"), (4, "Stranger")))


def admins_bot(*admins):
    """A bot whose chat administrators are `admins`"""
    fake = MagicMock()
    fake.get_chat_administrators.return_value = [SimpleNamespace(user=u) for u in admins]
    return fake


class LobbyTest(unittest.TestCase):

    def setUp(self):
        self.gm = GameManager()
        self.chat = stubs.MockChat(-4242)
        self.chat.type = 'group'
        for p in (patch.object(self.gm, '_persist_chat'), patch.object(self.gm, 'persist_game'),
                  patch('bot.gm', self.gm), patch('bot.send_async')):
            p.start()
            self.addCleanup(p.stop)
        self.send = bot.send_async
        # get_admin_ids caches per bot and chat for an hour
        self.bot = admins_bot(ADMIN)

    def command(self, handler, user):
        update = MagicMock()
        update.message.chat = self.chat
        update.message.chat_id = self.chat.id
        update.message.from_user = user
        context = MagicMock()
        context.bot = self.bot
        inspect.unwrap(handler)(update, context)

    def games(self):
        return self.gm.chatid_games.get(self.chat.id, [])

    def last_text(self):
        return self.send.call_args[1]['text']


class TestNewGame(LobbyTest):

    def test_new_refuses_while_a_game_has_players(self):
        self.command(bot.new_game, CREATOR)
        first = self.games()[0]
        self.gm.join_game(CREATOR, self.chat)
        for user in (CREATOR, STRANGER, ADMIN):
            self.command(bot.new_game, user)
            self.assertIn("/kill", self.last_text())
        self.assertEqual(self.games(), [first])

    def test_only_the_creator_or_an_admin_replaces_an_empty_lobby(self):
        self.command(bot.new_game, CREATOR)
        first = self.games()[0]
        self.command(bot.new_game, STRANGER)
        self.assertIn("/join", self.last_text())
        self.assertEqual(self.games(), [first])
        self.command(bot.new_game, ADMIN)
        self.assertEqual(len(self.games()), 1)
        self.assertIsNot(self.games()[0], first)
        self.assertIn(ADMIN.id, self.games()[0].owner)

    def test_lobby_message_opens_the_mode_menu(self):
        self.command(bot.new_game, CREATOR)
        markup = self.send.call_args[1]['reply_markup']
        self.assertIn('', [b.switch_inline_query_current_chat for row in markup.inline_keyboard for b in row])


class TestKill(LobbyTest):

    def lobby_with_players(self):
        self.command(bot.new_game, CREATOR)
        game = self.games()[0]
        self.gm.join_game(PLAYER, self.chat)
        self.gm.join_game(STRANGER, self.chat)
        return game

    def test_admin_who_is_not_playing_ends_the_game(self):
        game = self.lobby_with_players()
        ended = []
        self.gm.on_game_end = ended.append
        self.command(bot.kill_game, ADMIN)
        self.assertEqual(self.games(), [])
        self.assertEqual(ended, [game])
        self.assertNotIn(PLAYER.id, self.gm.userid_players)
        self.assertIn("Game ended", self.last_text())

    def test_creator_ends_an_empty_lobby_without_joining(self):
        self.command(bot.new_game, CREATOR)
        self.command(bot.kill_game, CREATOR)
        self.assertEqual(self.games(), [])

    def test_others_cannot_kill(self):
        self.lobby_with_players()
        self.command(bot.kill_game, PLAYER)
        self.assertEqual(len(self.games()), 1)
        self.assertIn("Only the game creator", self.last_text())


class TestModeMenu(LobbyTest):
    """The inline mode menu for the lobby's creator and the chat's admins, who needn't join"""

    def menu(self, user):
        update = MagicMock()
        update.inline_query.from_user = user
        context = MagicMock()
        context.bot = self.bot
        with patch('bot.answer_async') as answer:
            inspect.unwrap(bot.reply_to_query)(update, context)
        return [r.id for r in answer.call_args[0][2]]

    def choose(self, user, result_id):
        update = MagicMock()
        update.chosen_inline_result.from_user = user
        update.chosen_inline_result.result_id = result_id
        context = MagicMock()
        context.bot = self.bot
        inspect.unwrap(bot.process_result)(update, context)

    def test_creator_and_admin_change_the_mode_without_joining(self):
        self.command(bot.new_game, CREATOR)
        game = self.games()[0]
        for user, mode in ((CREATOR, 'wild'), (ADMIN, 'text')):
            ids = self.menu(user)
            self.assertIn('mode_%s@%d' % (mode, self.chat.id), ids)
            self.choose(user, 'mode_%s@%d' % (mode, self.chat.id))
            self.assertEqual(game.mode, mode)
        self.assertIn("Gamemode changed", self.last_text())

    def test_others_and_started_games_cannot_change_the_mode(self):
        self.command(bot.new_game, CREATOR)
        game = self.games()[0]
        before = game.mode
        self.assertEqual(self.menu(STRANGER), ['nogame'])
        self.choose(STRANGER, 'mode_wild@%d' % self.chat.id)
        self.choose(CREATOR, 'mode_nonsense@%d' % self.chat.id)
        game.started = True
        self.choose(CREATOR, 'mode_wild@%d' % self.chat.id)
        self.assertEqual(game.mode, before)
        self.assertEqual(self.menu(CREATOR), ['nogame'])

    def test_admin_check_needs_the_bot_and_fails_closed(self):
        self.command(bot.new_game, CREATOR)
        game = self.games()[0]
        self.assertTrue(user_can_change_mode(CREATOR, game))
        self.assertFalse(user_can_change_mode(ADMIN, game))
        self.assertTrue(user_can_change_mode(ADMIN, game, self.bot))
        self.assertFalse(user_can_change_mode(STRANGER, game, self.bot))


if __name__ == '__main__':
    unittest.main()
