import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# Provide compatibility stubs for isolated unit testing
try:
    import telegram
    if not hasattr(telegram, 'ParseMode'):
        try:
            import telegram.constants
            telegram.ParseMode = telegram.constants.ParseMode
        except Exception:
            telegram.ParseMode = object()
    import telegram.ext
    if not hasattr(telegram.ext, 'Filters'):
        telegram.ext.Filters = getattr(telegram.ext, 'filters', object())
    if hasattr(telegram.ext, 'filters'):
        if not hasattr(telegram.ext.filters, 'regex'):
            telegram.ext.filters.regex = getattr(telegram.ext.filters, 'Regex', lambda *a, **k: object())
        if not hasattr(telegram.ext.filters, 'status_update'):
            telegram.ext.filters.status_update = object()
except ImportError:
    pass

if 'apscheduler' not in sys.modules:
    ap = types.ModuleType('apscheduler')
    ap_j = types.ModuleType('apscheduler.jobstores')
    ap_jb = types.ModuleType('apscheduler.jobstores.base')
    class JobLookupError(Exception): pass
    ap_jb.JobLookupError = JobLookupError
    ap_j.base = ap_jb
    ap.jobstores = ap_j
    sys.modules['apscheduler'] = ap
    sys.modules['apscheduler.jobstores'] = ap_j
    sys.modules['apscheduler.jobstores.base'] = ap_jb

if 'pony' not in sys.modules:
    p = types.ModuleType('pony')
    po = types.ModuleType('pony.orm')
    class FakeDBSession:
        def __call__(self, f): return f
        def __enter__(self): return self
        def __exit__(self, exc_type, exc_val, exc_tb): pass
    po.db_session = FakeDBSession()
    class FakeEntity:
        def __init__(self, **kwargs):
            for k, v in kwargs.items(): setattr(self, k, v)
        @classmethod
        def get(cls, **kwargs): return None
        @classmethod
        def select(cls, *args, **kwargs): return []
        def delete(self): pass
    class FakeDB:
        Entity = FakeEntity
        def bind(*args, **kwargs): pass
        def generate_mapping(*args, **kwargs): pass
    po.Database = FakeDB
    po.PrimaryKey = lambda *args, **kwargs: None
    po.Required = lambda *args, **kwargs: None
    po.Optional = lambda *args, **kwargs: None
    p.orm = po
    sys.modules['pony'] = p
    sys.modules['pony.orm'] = po

if 'shared_vars' not in sys.modules:
    sv = types.ModuleType('shared_vars')
    sv.gm = types.SimpleNamespace(
        persist_game=lambda g: None,
        leave_game=lambda u, c: None,
        end_game=lambda c, u: None,
        rebuild_user_chat_index=lambda: None,
        load_persisted_games=lambda: None,
        ensure_chat_loaded=lambda c: None,
        chatid_games={}
    )
    sv.updater = types.SimpleNamespace(
        dispatcher=types.SimpleNamespace(
            add_handler=lambda *a, **k: None,
            add_error_handler=lambda *a, **k: None,
            run_async=lambda f, *a, **k: None
        )
    )
    sv.dispatcher = sv.updater.dispatcher
    sys.modules['shared_vars'] = sv
else:
    sv = sys.modules['shared_vars']
    if hasattr(sv, 'dispatcher'):
        if not hasattr(sv.dispatcher, 'add_handler'):
            sv.dispatcher.add_handler = lambda *a, **k: None
        if not hasattr(sv.dispatcher, 'add_error_handler'):
            sv.dispatcher.add_error_handler = lambda *a, **k: None
        if not hasattr(sv.dispatcher, 'run_async'):
            sv.dispatcher.run_async = lambda f, *a, **k: None

from telegram.error import RetryAfter
from utils import (
    make_color_keyboard,
    make_turn_keyboard,
    _send_message_with_migration_handling,
    _answer_inline_query_with_retry,
)
from bot import (
    _callback_game_key,
    callback_router,
    _handle_color_callback,
    _handle_action_callback,
)
from config import TURN_PACING_DELAY
from game import Game
from player import Player
import card as c


class MockChat:
    def __init__(self, chat_id=-1001234567, title="UNO Group"):
        self.id = chat_id
        self.type = "supergroup"
        self.title = title


class MockUser:
    def __init__(self, user_id=123, first_name="Alice", username="alice"):
        self.id = user_id
        self.first_name = first_name
        self.username = username


class TestRateLimitMitigations(unittest.TestCase):

    def test_pacing_delay_configured(self):
        self.assertIsInstance(TURN_PACING_DELAY, float)
        self.assertGreaterEqual(TURN_PACING_DELAY, 0.0)

    def test_make_color_keyboard(self):
        markup = make_color_keyboard(-100999)
        self.assertEqual(len(markup.inline_keyboard), 2)
        all_buttons = [btn for row in markup.inline_keyboard for btn in row]
        self.assertEqual(len(all_buttons), 4)
        callback_datas = [btn.callback_data for btn in all_buttons]
        self.assertIn("color:-100999:r", callback_datas)
        self.assertIn("color:-100999:b", callback_datas)
        self.assertIn("color:-100999:g", callback_datas)
        self.assertIn("color:-100999:y", callback_datas)

    def test_make_turn_keyboard_draw_and_pass(self):
        chat = MockChat()
        user1 = MockUser(1, "Alice")
        user2 = MockUser(2, "Bob")
        game = Game(chat)
        p1 = Player(game, user1)
        p2 = Player(game, user2)
        game.current_player = p1
        game.started = True

        # Before drawing, should show Draw button
        p1.drew = False
        game.draw_counter = 0
        markup = make_turn_keyboard(game, chat.id)
        all_buttons = [btn for row in markup.inline_keyboard for btn in row]
        self.assertTrue(any(btn.callback_data == f"action:{chat.id}:draw" for btn in all_buttons))
        self.assertFalse(any(btn.callback_data == f"action:{chat.id}:pass" for btn in all_buttons))

        # After drawing, should show Pass button
        p1.drew = True
        markup2 = make_turn_keyboard(game, chat.id)
        all_buttons2 = [btn for row in markup2.inline_keyboard for btn in row]
        self.assertFalse(any(btn.callback_data == f"action:{chat.id}:draw" for btn in all_buttons2))
        self.assertTrue(any(btn.callback_data == f"action:{chat.id}:pass" for btn in all_buttons2))

    def test_callback_game_key_extraction(self):
        query_color = MagicMock(data="color:-100123:r")
        update_color = MagicMock(callback_query=query_color)
        self.assertEqual(_callback_game_key(update_color), "chat:-100123")

        query_action = MagicMock(data="action:-100456:draw")
        update_action = MagicMock(callback_query=query_action)
        self.assertEqual(_callback_game_key(update_action), "chat:-100456")

        query_select = MagicMock(data="-100789")
        update_select = MagicMock(callback_query=query_select)
        self.assertEqual(_callback_game_key(update_select), "chat:-100789")

    @patch('bot.game_thread_runner.submit')
    def test_callback_router_dispatches_color_and_action(self, mock_submit):
        context = MagicMock()

        # Color query
        query_color = MagicMock(data="color:-100123:r")
        update_color = MagicMock(callback_query=query_color)
        callback_router(update_color, context)
        mock_submit.assert_called_once()
        self.assertEqual(mock_submit.call_args[0][0], "chat:-100123")
        mock_submit.reset_mock()

        # Action query
        query_action = MagicMock(data="action:-100123:draw")
        update_action = MagicMock(callback_query=query_action)
        callback_router(update_action, context)
        mock_submit.assert_called_once()
        self.assertEqual(mock_submit.call_args[0][0], "chat:-100123")

    @patch('bot.gm')
    def test_handle_color_callback_rejects_wrong_player(self, mock_gm):
        bot = MagicMock()
        query = MagicMock(from_user=MockUser(999))
        context = MagicMock()

        game = MagicMock()
        game.started = True
        game.choosing_color = True
        game.current_player = MagicMock(user=MockUser(111))
        mock_gm.chatid_games.get.return_value = [game]

        _handle_color_callback(bot, query, -100123, "r", context)
        bot.answerCallbackQuery.assert_called_once()
        self.assertTrue(bot.answerCallbackQuery.call_args[1].get('show_alert'))
        game.choose_color.assert_not_called()

    @patch('bot.gm')
    @patch('bot.send_async')
    @patch('bot.start_player_countdown')
    @patch('bot.game_is_running', return_value=True)
    def test_handle_color_callback_accepts_current_player(self, mock_is_running, mock_countdown, mock_send, mock_gm):
        bot = MagicMock()
        query = MagicMock(from_user=MockUser(111), message=MagicMock())
        context = MagicMock()

        chat = MockChat(-100123)
        user1 = MockUser(111, "Alice")
        user2 = MockUser(222, "Bob")
        game = Game(chat)
        p1 = Player(game, user1)
        p2 = Player(game, user2)
        game.current_player = p1
        game.started = True
        game.last_card = c.Card(None, None, special=c.CHOOSE)
        game.choosing_color = True
        mock_gm.chatid_games.get.return_value = [game]

        with patch('bot.TURN_PACING_DELAY', 0):
            _handle_color_callback(bot, query, chat.id, "r", context)

        self.assertEqual(game.last_card.color, "r")
        self.assertFalse(game.choosing_color)
        self.assertEqual(game.current_player, p2)
        mock_send.assert_called_once()
        query.edit_message_text.assert_called_once()
        self.assertIn("chose", query.edit_message_text.call_args[1]['text'])

    @patch('bot.gm')
    @patch('bot.send_async')
    @patch('bot.start_player_countdown')
    @patch('bot.game_is_running', return_value=True)
    def test_handle_action_callback_draw_and_pass(self, mock_is_running, mock_countdown, mock_send, mock_gm):
        bot = MagicMock()
        context = MagicMock()

        chat = MockChat(-100123)
        user1 = MockUser(111, "Alice")
        user2 = MockUser(222, "Bob")
        game = Game(chat)
        p1 = Player(game, user1)
        p2 = Player(game, user2)
        game.current_player = p1
        game.started = True
        game.last_card = c.Card(c.RED, '5')
        p1.cards = [c.Card(c.RED, '5')]
        game.deck.cards = [c.Card(c.BLUE, '3')]
        mock_gm.chatid_games.get.return_value = [game]

        # Draw action
        query_draw = MagicMock(from_user=user1, message=MagicMock())
        with patch('bot.TURN_PACING_DELAY', 0):
            _handle_action_callback(bot, query_draw, chat.id, "draw", context)

        self.assertTrue(p1.drew)
        self.assertEqual(game.current_player, p1)
        query_draw.edit_message_text.assert_called_once()
        self.assertIn("drew", query_draw.edit_message_text.call_args[1]['text'])

        # Pass action
        query_pass = MagicMock(from_user=user1, message=MagicMock())
        with patch('bot.TURN_PACING_DELAY', 0):
            _handle_action_callback(bot, query_pass, chat.id, "pass", context)

        self.assertEqual(game.current_player, p2)
        query_pass.edit_message_text.assert_called_once()
        self.assertIn("passed", query_pass.edit_message_text.call_args[1]['text'])

    @patch('time.sleep')
    def test_send_message_handles_retry_after(self, mock_sleep):
        bot = MagicMock()
        bot.sendMessage.side_effect = [
            RetryAfter(2),
            MagicMock(message_id=42)
        ]
        _send_message_with_migration_handling(bot, chat_id=-100123, text="Test")
        self.assertEqual(bot.sendMessage.call_count, 2)
        mock_sleep.assert_called_once_with(2.5)

    @patch('time.sleep')
    def test_answer_inline_query_handles_retry_after(self, mock_sleep):
        bot = MagicMock()
        bot.answerInlineQuery.side_effect = [
            RetryAfter(3),
            True
        ]
        _answer_inline_query_with_retry(bot, "query_id_123", [])
        self.assertEqual(bot.answerInlineQuery.call_count, 2)
        mock_sleep.assert_called_once_with(3.5)

    def test_ensure_chat_loaded_handles_int_and_chat_object(self):
        from game_manager import GameManager
        gm = GameManager()
        chat_id = -1003582555037
        chat_obj = MockChat(chat_id)

        # Should handle int chat ID without raising AttributeError
        with patch.object(gm, '_load_chat_from_storage', return_value=[]):
            gm.ensure_chat_loaded(chat_id)

        # Should handle Chat object without raising AttributeError
        with patch.object(gm, '_load_chat_from_storage', return_value=[]):
            gm.ensure_chat_loaded(chat_obj)


if __name__ == '__main__':
    unittest.main()
