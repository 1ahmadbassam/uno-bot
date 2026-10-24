import unittest
from unittest.mock import MagicMock, patch
from access_control import access_control, DispatcherHandlerStop


class MockChat:
    def __init__(self, chat_id, chat_type='supergroup', title='Test Chat'):
        self.id = chat_id
        self.type = chat_type
        self.title = title


class MockUser:
    def __init__(self, user_id, username='testuser'):
        self.id = user_id
        self.username = username


class MockUpdate:
    def __init__(self, chat=None, user=None):
        self.effective_chat = chat
        self.effective_user = user


class TestAccessControl(unittest.TestCase):

    def setUp(self):
        self.context = MagicMock()

    @patch('access_control.ALLOWED_GROUP_ID', -1001234567890)
    @patch('access_control.ALLOWED_USER_IDS', {111, 222})
    def test_allowed_group_and_user_passes(self):
        update = MockUpdate(
            chat=MockChat(-1001234567890, 'supergroup'),
            user=MockUser(111)
        )
        # Should not raise any exception
        access_control(update, self.context)
        self.context.bot.leave_chat.assert_not_called()

    @patch('access_control.ALLOWED_GROUP_ID', -1001234567890)
    @patch('access_control.ALLOWED_USER_IDS', {111, 222})
    def test_unauthorized_group_is_blocked_and_left(self):
        update = MockUpdate(
            chat=MockChat(-1009999999999, 'supergroup'),
            user=MockUser(111)
        )
        with self.assertRaises(DispatcherHandlerStop):
            access_control(update, self.context)
        self.context.bot.leave_chat.assert_called_once_with(-1009999999999, timeout=10)

    @patch('access_control.ALLOWED_GROUP_ID', -1001234567890)
    @patch('access_control.ALLOWED_USER_IDS', {111, 222})
    def test_unauthorized_user_in_allowed_group_is_blocked(self):
        update = MockUpdate(
            chat=MockChat(-1001234567890, 'supergroup'),
            user=MockUser(999)
        )
        with self.assertRaises(DispatcherHandlerStop):
            access_control(update, self.context)
        self.context.bot.leave_chat.assert_not_called()

    @patch('access_control.ALLOWED_GROUP_ID', -1001234567890)
    @patch('access_control.ALLOWED_USER_IDS', {111, 222})
    def test_inline_query_allowed_user_passes(self):
        # In inline query, effective_chat is None
        update = MockUpdate(
            chat=None,
            user=MockUser(222)
        )
        access_control(update, self.context)

    @patch('access_control.ALLOWED_GROUP_ID', -1001234567890)
    @patch('access_control.ALLOWED_USER_IDS', {111, 222})
    def test_inline_query_unauthorized_user_is_blocked(self):
        update = MockUpdate(
            chat=None,
            user=MockUser(999)
        )
        with self.assertRaises(DispatcherHandlerStop):
            access_control(update, self.context)

    @patch('access_control.ALLOWED_GROUP_ID', -1001234567890)
    @patch('access_control.ALLOWED_USER_IDS', {111, 222})
    def test_private_chat_allowed_user_passes(self):
        update = MockUpdate(
            chat=MockChat(111, 'private'),
            user=MockUser(111)
        )
        access_control(update, self.context)

    @patch('access_control.ALLOWED_GROUP_ID', -1001234567890)
    @patch('access_control.ALLOWED_USER_IDS', {111, 222})
    def test_private_chat_unauthorized_user_is_blocked(self):
        update = MockUpdate(
            chat=MockChat(999, 'private'),
            user=MockUser(999)
        )
        with self.assertRaises(DispatcherHandlerStop):
            access_control(update, self.context)

    @patch('access_control.ALLOWED_GROUP_ID', None)
    @patch('access_control.ALLOWED_USER_IDS', None)
    def test_no_restrictions_allows_all(self):
        update = MockUpdate(
            chat=MockChat(-1009999999999, 'supergroup'),
            user=MockUser(999)
        )
        access_control(update, self.context)
        self.context.bot.leave_chat.assert_not_called()


class TestConfigParsing(unittest.TestCase):

    def test_config_parsing_env_vars(self):
        with patch.dict('os.environ', {
            'ALLOWED_GROUP_ID': '-100987654321',
            'ALLOWED_USER_IDS': '12345, 67890'
        }):
            import importlib
            import config
            importlib.reload(config)
            self.assertEqual(config.ALLOWED_GROUP_ID, -100987654321)
            self.assertEqual(config.ALLOWED_USER_IDS, {12345, 67890})

    def test_config_parsing_empty(self):
        with patch.dict('os.environ', {}, clear=True):
            import importlib
            import config
            importlib.reload(config)
            self.assertIsNone(config.ALLOWED_GROUP_ID)
            self.assertIsNone(config.ALLOWED_USER_IDS)


if __name__ == '__main__':
    unittest.main()
