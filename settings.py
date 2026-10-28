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


from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import CommandHandler, Filters, MessageHandler, CallbackContext

from utils import send_async
from user_setting import UserSetting
from shared_vars import dispatcher
from locales import available_locales
from internationalization import _, user_locale


def _get_or_create_user_setting(user_id):
    us = UserSetting.get(id=user_id)
    if not us:
        us = UserSetting(id=user_id)
    return us


@user_locale
def show_settings(update: Update, context: CallbackContext):
    chat = update.message.chat

    if update.message.chat.type != 'private':
        send_async(context.bot, chat.id,
                   text=_("Please edit your settings in a private chat with "
                          "the bot."))
        return

    kb = [['🌍' + ' ' + _("Language")]]
    send_async(context.bot, chat.id, text='🔧' + ' ' + _("Settings"),
               reply_markup=ReplyKeyboardMarkup(keyboard=kb,
                                                one_time_keyboard=True))


@user_locale
def kb_select(update: Update, context: CallbackContext):
    chat = update.message.chat
    option = context.match[1]

    if option == '🌍':
        kb = [[locale + ' - ' + descr]
              for locale, descr
              in sorted(available_locales.items())]
        send_async(context.bot, chat.id, text=_("Select locale"),
                   reply_markup=ReplyKeyboardMarkup(keyboard=kb,
                                                    one_time_keyboard=True))


@user_locale
def locale_select(update: Update, context: CallbackContext):
    chat = update.message.chat
    user = update.message.from_user
    option = context.match[1]

    if option in available_locales:
        us = _get_or_create_user_setting(user.id)
        us.lang = option
        _.push(option)
        send_async(context.bot, chat.id, text=_("Set locale!"))
        _.pop()

def register():
    dispatcher.add_handler(CommandHandler('settings', show_settings))
    dispatcher.add_handler(MessageHandler(Filters.regex('^(🌍) .+$'),
                                        kb_select))
    dispatcher.add_handler(MessageHandler(Filters.regex(r'^(\w\w_\w\w) - .*'),
                                        locale_select))
