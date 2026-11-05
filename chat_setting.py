#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json

from pony.orm import Optional, PrimaryKey, Required, db_session

from config import MANUAL_UNO
from database import db


class ChatSetting(db.Entity):

    id = PrimaryKey(int, auto=False, size=64)  # Telegram Chat ID
    manual_uno = Optional(bool, default=False)  # Players have to declare UNO themselves (/uno)


class ChatRandomSetting(db.Entity):
    """Permanent /selectrandom overrides, independent of individual games."""

    id = PrimaryKey(int, auto=False, size=64)
    modes = Required(str)  # JSON array; [] is an explicit normal Random pool


@db_session
def selective_random_modes(chat_id):
    """None means no group override; even an empty saved list overrides the JSON default."""
    setting = ChatRandomSetting.get(id=chat_id)
    return json.loads(setting.modes) if setting else None


@db_session
def save_selective_random_modes(chat_id, modes):
    setting = ChatRandomSetting.get(id=chat_id)
    payload = json.dumps(modes)
    if setting:
        setting.modes = payload
    else:
        ChatRandomSetting(id=chat_id, modes=payload)


@db_session
def manual_uno_enabled(chat_id):
    """Whether games started in this chat need UNO declared; the config's choice until /uno is used"""
    setting = ChatSetting.get(id=chat_id)
    return setting.manual_uno if setting else MANUAL_UNO


@db_session
def toggle_manual_uno(chat_id):
    """/uno: switch manual UNO for this chat, and return the new state"""
    setting = ChatSetting.get(id=chat_id)
    enabled = not (setting.manual_uno if setting else MANUAL_UNO)
    if setting:
        setting.manual_uno = enabled
    else:
        ChatSetting(id=chat_id, manual_uno=enabled)
    return enabled
