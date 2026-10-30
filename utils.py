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
import time
from telegram import Update
try:
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
except ImportError:
    class InlineKeyboardMarkup(object):
        def __init__(self, inline_keyboard=None):
            self.inline_keyboard = inline_keyboard or []
    class InlineKeyboardButton(object):
        def __init__(self, text="", callback_data=None, switch_inline_query_current_chat=None, **kwargs):
            self.text = text
            self.callback_data = callback_data
            self.switch_inline_query_current_chat = switch_inline_query_current_chat

try:
    from telegram.error import Unauthorized
except ImportError:
    from telegram.error import Forbidden as Unauthorized
from telegram.error import BadRequest, TimedOut, ChatMigrated, TelegramError
try:
    from telegram.error import RetryAfter
except ImportError:
    class RetryAfter(Exception):
        def __init__(self, retry_after=1):
            self.retry_after = retry_after
from telegram.ext import CallbackContext

from game_threads import game_thread_runner
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


def send_paced(bot, chat_id, delay=0, **kwargs):
    """
    Send a turn prompt after `delay` seconds without blocking the chat's game
    queue (inline menus and plays for the chat keep being served meanwhile).
    All paced prompts of a chat go through one FIFO queue, so they keep their order.
    """
    game_thread_runner.submit(f"pacing:{chat_id}", _send_after_delay, bot, chat_id, delay, kwargs)


def _send_after_delay(bot, chat_id, delay, kwargs):
    if delay > 0:
        time.sleep(delay)
    send_async(bot, chat_id, **kwargs)


def answer_async(bot, *args, **kwargs):
    """Answer an inline query asynchronously"""
    if 'timeout' not in kwargs:
        kwargs['timeout'] = TIMEOUT

    try:
        dispatcher.run_async(_answer_inline_query_with_retry, bot, *args, **kwargs)
    except Exception:
        logger.exception("Failed to schedule async answerInlineQuery")


def _retry_after_delay(err):
    """Safely extract delay in seconds from a RetryAfter exception as a float."""
    val = getattr(err, 'retry_after', 0)
    if hasattr(val, 'total_seconds'):
        return float(val.total_seconds())
    try:
        return float(val)
    except (TypeError, ValueError):
        return 1.0


def _answer_inline_query_with_retry(bot, *args, **kwargs):
    try:
        bot.answerInlineQuery(*args, **kwargs)
    except RetryAfter as err:
        logger.warning("Bot rate limited on answerInlineQuery (RetryAfter: %s). Sleeping...", err.retry_after)
        time.sleep(_retry_after_delay(err) + 0.5)
        try:
            bot.answerInlineQuery(*args, **kwargs)
        except Exception:
            logger.exception("Failed answerInlineQuery retry after RetryAfter")
    except Exception as err:
        logger.debug("Failed answerInlineQuery: %s", err)


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
    text = kwargs.get('text')
    if text is None and len(args) > 1:
        text = args[1]
    if text is not None and not str(text).strip():
        logger.warning("Dropping sendMessage: text is empty (chat_id=%s)", _extract_chat_id(args, kwargs))
        return

    try:
        bot.sendMessage(*args, **kwargs)
    except RetryAfter as err:
        logger.warning("Bot rate limited on sendMessage (RetryAfter: %s). Sleeping...", err.retry_after)
        time.sleep(_retry_after_delay(err) + 0.5)
        try:
            bot.sendMessage(*args, **kwargs)
        except Exception:
            logger.exception("Failed sendMessage retry after RetryAfter")
        return
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
        except RetryAfter as send_err:
            logger.warning("Bot rate limited on migrated sendMessage (RetryAfter: %s). Sleeping...", send_err.retry_after)
            time.sleep(_retry_after_delay(send_err) + 0.5)
            try:
                bot.sendMessage(*retry_args, **retry_kwargs)
            except Exception:
                logger.exception("Failed migrated sendMessage retry after RetryAfter")
            return
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
    owner = getattr(game, 'owner', None)
    if owner is None or not user:
        return False
    return user.id in owner


def user_can_change_mode(user, game, bot=None):
    """Check whether a user is allowed to change the game mode: the game's creator,
    the bot's admins and, when `bot` is given, the chat's admins, before the game starts"""
    if not user or not game or getattr(game, 'started', False):
        return False
    if user_is_creator(user, game):
        return True
    if bot is None:
        return False
    try:
        return user_is_admin(user, bot, game.chat)
    except TelegramError:
        return False


def user_is_admin(user, bot, chat):
    return user.id in get_admin_ids(bot, chat.id)


def user_is_creator_or_admin(user, game, bot, chat):
    return user_is_creator(user, game) or user_is_admin(user, bot, chat)


@MWT(timeout=60*60)
def get_admin_ids(bot, chat_id):
    """Returns a list of admin IDs for a given chat. Results are cached for 1 hour."""
    return [admin.user.id for admin in bot.get_chat_administrators(chat_id)]


def make_color_keyboard(chat_id, game=None):
    """Create inline keyboard for choosing a color"""
    if game and getattr(game, 'translate', False):
        r_text = display_color_group('r', game)
        b_text = display_color_group('b', game)
        g_text = display_color_group('g', game)
        y_text = display_color_group('y', game)
    else:
        r_text = _("{emoji} Red").format(emoji='❤️')
        b_text = _("{emoji} Blue").format(emoji='💙')
        g_text = _("{emoji} Green").format(emoji='💚')
        y_text = _("{emoji} Yellow").format(emoji='💛')

    return InlineKeyboardMarkup([
        # Opens the inline menu in this chat so the player can look at their hand
        [InlineKeyboardButton(text=_("🃏 Show my cards"), switch_inline_query_current_chat='')],
        [
            InlineKeyboardButton(text=r_text, callback_data=f"color:{chat_id}:r"),
            InlineKeyboardButton(text=b_text, callback_data=f"color:{chat_id}:b"),
        ],
        [
            InlineKeyboardButton(text=g_text, callback_data=f"color:{chat_id}:g"),
            InlineKeyboardButton(text=y_text, callback_data=f"color:{chat_id}:y"),
        ],
    ])


class _CopyTextButton(InlineKeyboardButton):
    """PTB 13 has no copy_text buttons (Bot API 7.11), so this adds the field when sent"""
    __slots__ = ('_copy_value',)

    def __init__(self, text, value):
        super().__init__(text=text)
        self._copy_value = value

    def to_dict(self):
        data = super().to_dict()
        data['copy_text'] = {'text': self._copy_value}
        return data


def copy_button(text, value):
    """A button that copies `value` (at most 256 characters) to the clipboard"""
    try:
        from telegram import CopyTextButton
    except ImportError:
        return _CopyTextButton(text, value)
    return InlineKeyboardButton(text=text, copy_text=CopyTextButton(value))


def fairness_keyboard(seed_hex, commitment, match_url=None):
    """Buttons to copy a finished game's seed and fingerprint, and to open its replay"""
    rows = [[copy_button(_("📋 Copy seed"), seed_hex), copy_button(_("📋 Copy fingerprint"), commitment)]]
    if match_url:
        rows.append([InlineKeyboardButton(text=_("🔎 Match stats"), url=match_url)])
    return InlineKeyboardMarkup(rows)


def make_turn_keyboard(game, chat_id):
    """Create inline keyboard for player turn, including play choice and quick draw/pass actions"""
    choice_btn = InlineKeyboardButton(text=_("Make your choice!"), switch_inline_query_current_chat='')
    keyboard = [[choice_btn]]

    current_player = getattr(game, 'current_player', None)
    if current_player is not None:
        action_row = []
        if not getattr(current_player, 'drew', False):
            draw_count = getattr(game, 'draw_counter', 0) or 1
            if draw_count > 1:
                draw_text = __("📥 Draw {number} cards", multi=game.translate).format(number=draw_count)
            else:
                draw_text = __("📥 Draw card", multi=game.translate)
            action_row.append(InlineKeyboardButton(text=draw_text, callback_data=f"action:{chat_id}:draw"))
        else:
            action_row.append(InlineKeyboardButton(text=__("⏭️ Pass", multi=game.translate), callback_data=f"action:{chat_id}:pass"))

        if action_row:
            keyboard.append(action_row)

    return InlineKeyboardMarkup(keyboard)


def make_mode_keyboard(game=None):
    """Create inline keyboard for opening the game mode inline menu"""
    mode_btn = InlineKeyboardButton(text=_("Change game mode"), switch_inline_query_current_chat='')
    return InlineKeyboardMarkup([[mode_btn]])

