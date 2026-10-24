#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Telegram bot to play UNO in group chats
# Copyright (c) 2016 Jannes Höke <uno@jhoeke.de>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.


import unittest
import sys
import types
from types import SimpleNamespace

if 'pony' not in sys.modules:
    try:
        import pony
    except ImportError:
        p = types.ModuleType('pony')
        po = types.ModuleType('pony.orm')
        class FakeDBSession:
            def __call__(self, f):
                return f
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

        po.db_session = FakeDBSession()
        class FakeEntity:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)
            @classmethod
            def get(cls, **kwargs):
                return None
            @classmethod
            def select(cls, *args, **kwargs):
                return []
            def delete(self):
                pass

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

class FakeUser:
    def __init__(self, id, first_name=None, username=None):
        self.id = id
        self.first_name = first_name or str(id)
        self.username = username

class FakeChat:
    def __init__(self, id, type='group', title=None):
        self.id = id
        self.type = type
        self.title = title or f"Group {id}"

try:
    from telegram import User, Chat
    # If stubbed as object(), override with proper classes
    if User is object or Chat is object:
        User, Chat = FakeUser, FakeChat
except ImportError:
    User, Chat = FakeUser, FakeChat

from game_manager import GameManager
from errors import AlreadyJoinedError, LobbyClosedError, NoGameInChatError, \
    NotEnoughPlayersError


class Test(unittest.TestCase):

    game = None

    def setUp(self):
        self.gm = GameManager()

        self.chat0 = Chat(0, 'group')
        self.chat1 = Chat(1, 'group')
        self.chat2 = Chat(2, 'group')

        self.user0 = User(0, 'user0')
        self.user1 = User(1, 'user1')
        self.user2 = User(2, 'user2')

    def test_new_game(self):
        g0 = self.gm.new_game(self.chat0)
        g1 = self.gm.new_game(self.chat1)

        self.assertListEqual(self.gm.chatid_games[0], [g0])
        self.assertListEqual(self.gm.chatid_games[1], [g1])

    def test_join_game(self):

        self.assertRaises(NoGameInChatError,
                          self.gm.join_game,
                          *(self.user0, self.chat0))

        g0 = self.gm.new_game(self.chat0)

        self.gm.join_game(self.user0, self.chat0)
        self.assertEqual(len(g0.players), 1)

        self.gm.join_game(self.user1, self.chat0)
        self.assertEqual(len(g0.players), 2)

        g0.open = False
        self.assertRaises(LobbyClosedError,
                          self.gm.join_game,
                          *(self.user2, self.chat0))

        g0.open = True
        self.assertRaises(AlreadyJoinedError,
                          self.gm.join_game,
                          *(self.user1, self.chat0))

    def test_leave_game(self):
        self.gm.new_game(self.chat0)

        self.gm.join_game(self.user0, self.chat0)
        self.gm.join_game(self.user1, self.chat0)

        self.assertRaises(NotEnoughPlayersError,
                          self.gm.leave_game,
                          *(self.user1, self.chat0))

        self.gm.join_game(self.user2, self.chat0)
        self.gm.leave_game(self.user0, self.chat0)

        self.assertRaises(NoGameInChatError,
                          self.gm.leave_game,
                          *(self.user0, self.chat0))

    def test_end_game(self):
        self.gm.new_game(self.chat0)

        self.gm.join_game(self.user0, self.chat0)
        self.gm.join_game(self.user1, self.chat0)

        self.assertEqual(len(self.gm.userid_players[0]), 1)

        self.gm.new_game(self.chat0)
        self.gm.join_game(self.user2, self.chat0)

        self.gm.end_game(self.chat0, self.user0)
        self.assertEqual(len(self.gm.chatid_games[0]), 1)

        self.gm.end_game(self.chat0, self.user2)
        self.assertFalse(0 in self.gm.chatid_games)
        self.assertFalse(0 in self.gm.userid_players)
        self.assertFalse(1 in self.gm.userid_players)
        self.assertFalse(2 in self.gm.userid_players)
