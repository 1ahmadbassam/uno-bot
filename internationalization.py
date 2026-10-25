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


import gettext
import threading
from contextlib import contextmanager
from functools import wraps

from locales import available_locales
from pony.orm import db_session
from user_setting import UserSetting
from shared_vars import gm

GETTEXT_DOMAIN = 'unobot'
GETTEXT_DIR = 'locales'


class _Underscore(object):
    """Class to emulate flufl.i18n behaviour, but with plural support"""
    def __init__(self):
        self.translators = dict()
        for locale in available_locales.keys():
            if locale == 'en_US':
                continue
            mo_path = gettext.find(GETTEXT_DOMAIN, GETTEXT_DIR, languages=[locale])
            if mo_path:
                self.translators[locale] = gettext.GNUTranslations(open(mo_path, 'rb'))
            else:
                self.translators[locale] = gettext.NullTranslations()
        self._local = threading.local()

    @property
    def locale_stack(self):
        if not hasattr(self._local, 'locale_stack'):
            self._local.locale_stack = list()
        return self._local.locale_stack

    def push(self, locale):
        self.locale_stack.append(locale)

    def pop(self):
        if self.locale_stack:
            return self.locale_stack.pop()
        else:
            return None

    @property
    def code(self):
        if self.locale_stack:
            return self.locale_stack[-1]
        else:
            return None

    def __call__(self, singular, plural=None, n=1, locale=None):
        if not locale:
            locale = self.locale_stack[-1] if self.locale_stack else 'en_US'

        if locale not in self.translators.keys():
            if n == 1:
                return singular
            else:
                return plural

        translator = self.translators[locale]

        if plural is None:
            return translator.gettext(singular)
        else:
            return translator.ngettext(singular, plural, n)

_ = _Underscore()


def __(singular, plural=None, n=1, multi=False):
    """Translates text into all locales on the stack"""
    translations = list()

    if not multi and len(set(_.locale_stack)) >= 1:
        translations.append(_(singular, plural, n, 'en_US'))

    elif _.locale_stack:
        for locale in _.locale_stack:
            translation = _(singular, plural, n, locale)

            if translation not in translations:
                translations.append(translation)
    else:
        translations.append(_(singular, plural, n, 'en_US'))

    res = '\n'.join(translations)
    # Only fall back when the result is blank; keep intentional leading/trailing newlines.
    return res if res.strip() else (singular if n == 1 else plural or singular)


def user_locale(func):
    @wraps(func)
    @db_session
    def wrapped(update, context, *pargs, **kwargs):
        user = _user_chat_from_update(update)[0]

        with db_session:
            us = UserSetting.get(id=user.id)

        if us and us.lang != 'en':
            _.push(us.lang)
        else:
            _.push('en_US')

        try:
            return func(update, context, *pargs, **kwargs)
        finally:
            _.pop()
    return wrapped


def _push_game_locales(game):
    """Push the locale of every player in the game onto the stack (once each)"""
    locales = list()
    for player in game.players:
        us = UserSetting.get(id=player.user.id)

        if us and us.lang != 'en':
            loc = us.lang
        else:
            loc = 'en_US'

        if loc in locales:
            continue

        _.push(loc)
        locales.append(loc)


@contextmanager
def locales_for_game(game):
    """Use the game's player locales (and a db_session) for code that runs outside
    a handler, e.g. callback handlers and timer jobs on the game worker threads"""
    initial_depth = len(_.locale_stack)
    # Keep a db_session open for the whole block: the wrapped code may read or
    # update user stats (e.g. a win awarded from a button or timer)
    with db_session:
        _push_game_locales(game)
        try:
            yield
        finally:
            while len(_.locale_stack) > initial_depth:
                _.pop()


def game_locales(func):
    @wraps(func)
    @db_session
    def wrapped(update, context, *pargs, **kwargs):
        user, chat = _user_chat_from_update(update)
        player = gm.player_for_user_in_chat(user, chat)
        initial_depth = len(_.locale_stack)

        if player:
            _push_game_locales(player.game)

        try:
            return func(update, context, *pargs, **kwargs)
        finally:
            while len(_.locale_stack) > initial_depth:
                _.pop()
    return wrapped


def _user_chat_from_update(update):
    user = update.effective_user
    chat = update.effective_chat

    if chat is None and user is not None:
        gm.ensure_user_loaded(user.id)
    if chat is None and user is not None and user.id in gm.userid_current:
        chat = gm.userid_current.get(user.id).game.chat

    return user, chat
