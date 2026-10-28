#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from datetime import datetime
from pony.orm import Optional, PrimaryKey, Required

from database import db


class RivalrySeason(db.Entity):
    id = PrimaryKey(int, auto=True)
    name = Required(str, unique=True)
    started_at = Required(datetime)  # UTC


class RivalryMatch(db.Entity):
    id = PrimaryKey(int, auto=True)
    season_id = Required(int)
    ended_at = Required(datetime)  # UTC
    winner_id = Required(int, size=64)
    loser_id = Required(int, size=64)
    outcome = Required(str)  # 'win', 'forfeit', 'manual' or 'imported'
    started_at = Optional(datetime)  # UTC; unknown for manual and imported results
    mode = Optional(str, nullable=True)
    players = Optional(int)  # Players in the game, both rivals included
    turns = Optional(int)
    loser_cards_left = Optional(int)
    chat_id = Optional(int, size=64)
    message_id = Optional(int, size=64)  # The pinned score message
