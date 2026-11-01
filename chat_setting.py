#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pony.orm import Optional, PrimaryKey, db_session

from config import MANUAL_UNO
from database import db


class ChatSetting(db.Entity):

    id = PrimaryKey(int, auto=False, size=64)  # Telegram Chat ID
    manual_uno = Optional(bool, default=False)  # Players have to declare UNO themselves (/uno)


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
