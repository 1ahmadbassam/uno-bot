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
from telegram import Update
try:
    from telegram.error import Unauthorized
except ImportError:
    from telegram.error import Forbidden as Unauthorized
from telegram.error import BadRequest, TimedOut, ChatMigrated
from telegram.ext import CallbackContext

from internationalization import _, __
from mwt import MWT
from shared_vars import gm, dispatcher

logger = logging.getLogger(__name__)

TIMEOUT = 10


def list_subtract(list1, list2):
    """ Helper function to subtract two lists and return the sorted result """
    list1 = list1.copy()

    for x in list2:
        list1.remove(x)

    return list(sorted(list1))


def display_name(user):
    """ Get the current players name including their username, if possible """
    user_name = user.first_name
    if user.username:
        user_name += ' (@' + user.username + ')'
    return user_name


def display_color(color):
    """ Convert a color code to actual color name """
    if color == "r":
        return _("{emoji} Red").format(emoji='❤️')
    if color == "b":
        return _("{emoji} Blue").format(emoji='💙')
    if color == "g":
        return _("{emoji} Green").format(emoji='💚')
    if color == "y":
        return _("{emoji} Yellow").format(emoji='💛')


def display_color_group(color, game):
    """ Convert a color code to actual color name """
    if color == "r":
        return __("{emoji} Red", game.translate).format(
            emoji='❤️')
    if color == "b":
        return __("{emoji} Blue", game.translate).format(
            emoji='💙')
    if color == "g":
        return __("{emoji} Green", game.translate).format(
            emoji='💚')
    if color == "y":
        return __("{emoji} Yellow", game.translate).format(
            emoji='💛')


def error(update: Update, context: CallbackContext):
    """Simple error handler"""
    err = context.error if context else None
    if err is None:
        logger.error("Unknown error in update handler")
        return

    # Telegram API timeouts are transient and should not spam traceback logs.
    if isinstance(err, TimedOut):
        logger.warning("Telegram request timed out")
        return

    # Stale callback queries are expected when users tap old inline keyboards.
    if isinstance(err, BadRequest) and "Query is too old" in str(err):
        logger.info("Ignoring stale callback query")
        return

    logger.error("Unhandled exception in update handler",
                 exc_info=(type(err), err, err.__traceback__))


def send_async(bot, *args, **kwargs):
    """Send a message asynchronously"""
    if 'timeout' not in kwargs:
        kwargs['timeout'] = TIMEOUT

    try:
        dispatcher.run_async(_send_message_with_migration_handling, bot, *args, **kwargs)
    except Exception:
        logger.exception("Failed to schedule async sendMessage")


def answer_async(bot, *args, **kwargs):
    """Answer an inline query asynchronously"""
    if 'timeout' not in kwargs:
        kwargs['timeout'] = TIMEOUT

    try:
        dispatcher.run_async(bot.answerInlineQuery, *args, **kwargs)
    except Exception:
        logger.exception("Failed to schedule async answerInlineQuery")


def _extract_chat_id(args, kwargs):
    if 'chat_id' in kwargs:
        return kwargs['chat_id']
    if args:
        return args[0]
    return None


def _replace_chat_id(args, kwargs, new_chat_id):
    if 'chat_id' in kwargs:
        new_kwargs = dict(kwargs)
        new_kwargs['chat_id'] = new_chat_id
        return args, new_kwargs

    if args:
        return (new_chat_id,) + tuple(args[1:]), kwargs

    return args, kwargs


def _send_message_with_migration_handling(bot, *args, **kwargs):
    try:
        bot.sendMessage(*args, **kwargs)
    except Unauthorized as err:
        logger.info("Dropping sendMessage: unauthorized (%s) (chat_id=%s)",
                    err, _extract_chat_id(args, kwargs))
        return
    except ChatMigrated as err:
        old_chat_id = _extract_chat_id(args, kwargs)
        new_chat_id = err.new_chat_id

        if old_chat_id is not None and old_chat_id != new_chat_id:
            gm.migrate_chat_id(old_chat_id, new_chat_id)

        logger.info("Chat migrated from %s to %s; retrying sendMessage",
                    old_chat_id, new_chat_id)
        retry_args, retry_kwargs = _replace_chat_id(args, kwargs, new_chat_id)
        try:
            bot.sendMessage(*retry_args, **retry_kwargs)
        except Unauthorized as send_err:
            logger.info("Dropping sendMessage after migration: unauthorized (%s) (chat_id=%s)",
                        send_err, _extract_chat_id(retry_args, retry_kwargs))
            return
        except BadRequest as send_err:
            msg = str(send_err).lower()
            if "chat not found" in msg:
                logger.info("Dropping sendMessage after migration: chat not found (chat_id=%s)",
                            new_chat_id)
                return
            if "message to be replied not found" in msg:
                retry_kwargs = dict(retry_kwargs)
                retry_kwargs.pop("reply_to_message_id", None)
                bot.sendMessage(*retry_args, **retry_kwargs)
                return
            raise
    except BadRequest as err:
        msg = str(err).lower()
        if "chat not found" in msg:
            logger.info("Dropping sendMessage: chat not found (chat_id=%s)",
                        _extract_chat_id(args, kwargs))
            return
        if "message to be replied not found" in msg:
            retry_kwargs = dict(kwargs)
            retry_kwargs.pop("reply_to_message_id", None)
            bot.sendMessage(*args, **retry_kwargs)
            return
        raise


def game_is_running(game):
    return game in gm.chatid_games.get(game.chat.id, list())


def user_is_creator(user, game):
    return user.id in game.owner


def user_is_admin(user, bot, chat):
    return user.id in get_admin_ids(bot, chat.id)


def user_is_creator_or_admin(user, game, bot, chat):
    return user_is_creator(user, game) or user_is_admin(user, bot, chat)


@MWT(timeout=60*60)
def get_admin_ids(bot, chat_id):
    """Returns a list of admin IDs for a given chat. Results are cached for 1 hour."""
    return [admin.user.id for admin in bot.get_chat_administrators(chat_id)]
