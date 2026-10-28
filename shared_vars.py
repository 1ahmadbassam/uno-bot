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


from config import TOKEN, WORKERS
import logging
import os
from telegram.ext import Updater

from game_manager import GameManager
from database import bind_database
import user_setting  # noqa: F401
import persisted_state  # noqa: F401
import rivalry_models  # noqa: F401

UNO_DB_PATH = os.getenv('UNO_DB', 'uno.sqlite3')

bind_database(UNO_DB_PATH)

gm = GameManager()
gm.rebuild_user_chat_index()
gm.load_persisted_games()
updater = Updater(token=TOKEN, workers=WORKERS, use_context=True)
dispatcher = updater.dispatcher
