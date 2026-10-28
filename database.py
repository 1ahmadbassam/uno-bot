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


import logging
import os
import sqlite3

from pony.orm import Database

logger = logging.getLogger(__name__)

# Database singleton
db = Database()

# Columns of the removed opt-in /stats feature
LEGACY_COLUMNS = {'UserSetting': ('stats', 'first_places', 'games_played', 'cards_played')}


def drop_legacy_columns(path):
    """Drop columns of removed features from an existing database file"""
    if not os.path.exists(path):
        return
    conn = sqlite3.connect(path)
    try:
        for table, columns in LEGACY_COLUMNS.items():
            existing = {row[1] for row in conn.execute('PRAGMA table_info("%s")' % table)}
            for column in columns:
                if column in existing:
                    conn.execute('ALTER TABLE "%s" DROP COLUMN "%s"' % (table, column))
                    logger.info("Dropped legacy column %s.%s", table, column)
        conn.commit()
    except sqlite3.OperationalError:
        # Unused nullable columns are harmless; an old SQLite can't drop them
        logger.warning("Could not drop legacy columns", exc_info=True)
    finally:
        conn.close()


def bind_database(path):
    """Bind the database file at `path` and create missing tables.
    Import every entity module before calling it."""
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    drop_legacy_columns(path)
    db.bind('sqlite', path, create_db=True)
    db.generate_mapping(create_tables=True)
