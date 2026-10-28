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
from datetime import datetime
from functools import wraps

from telegram import InlineKeyboardMarkup, \
    InlineKeyboardButton, Update
try:
    from telegram import ParseMode
except ImportError:
    from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import InlineQueryHandler, ChosenInlineResultHandler, \
    CommandHandler, MessageHandler, Filters, CallbackQueryHandler, CallbackContext, TypeHandler

import card as c
import rivalry
import settings
import simple_commands
from access_control import access_control
from actions import do_skip, do_play_card, do_draw, do_call_bluff, start_player_countdown, \
    settle_draw_four_after_leave, cancel_penalty_after_leave, forfeit_win, end_game_without_winner
from config import WAITING_TIME, DEFAULT_GAMEMODE, MIN_PLAYERS, TURN_PACING_DELAY
from errors import (NoGameInChatError, LobbyClosedError, AlreadyJoinedError,
                    NotEnoughPlayersError, DeckEmptyError, LobbyFullError,
                    RejoinNotAllowedError)
from internationalization import _, __, user_locale, game_locales, locales_for_game, locale_for_user
from results import (add_call_bluff, add_draw, add_gameinfo,
                     add_no_game, add_not_started, add_pass,
                     add_card, add_mode_classic, add_mode_fast, add_mode_wild, add_mode_text)
from shared_vars import gm, updater, dispatcher
from simple_commands import help_handler
from start_bot import start_bot
from game_threads import game_thread_runner
from utils import display_name
from utils import send_async, send_paced, answer_async, error, TIMEOUT, user_is_creator_or_admin, user_is_creator, game_is_running, display_color_group, make_color_keyboard, make_turn_keyboard


logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logging.getLogger('apscheduler').setLevel(logging.WARNING)

# Game modes offered by the mode_* inline results
GAME_MODES = ('classic', 'fast', 'wild', 'text')


def _chat_key(update: Update):
    chat = update.effective_chat
    if chat is None:
        return "chat:unknown"
    return f"chat:{chat.id}"


def _callback_game_key(update: Update):
    query = update.callback_query
    if query and query.data:
        data = query.data
        if data.startswith("color:") or data.startswith("action:"):
            parts = data.split(":")
            if len(parts) >= 2:
                try:
                    return f"chat:{int(parts[1])}"
                except ValueError:
                    pass
        else:
            try:
                return f"chat:{int(data)}"
            except (TypeError, ValueError):
                pass
        return "callback:fallback"
    return "callback:unknown"


def _inline_result_key(update: Update):
    chosen = update.chosen_inline_result
    if chosen is None or chosen.from_user is None:
        return "inline:unknown"

    user_id = chosen.from_user.id
    player = gm.userid_current.get(user_id)
    if player:
        return f"chat:{player.game.chat.id}"
    return "inline-result:fallback"


def _inline_query_key(update: Update):
    inline_query = update.inline_query
    if inline_query is None or inline_query.from_user is None:
        return "inline-query:unknown"

    user_id = inline_query.from_user.id
    player = gm.userid_current.get(user_id)
    if player:
        return f"chat:{player.game.chat.id}"
    return "inline-query:fallback"


def _threaded_by(key_fn, require_message=False):
    def decorator(func):
        @wraps(func)
        def wrapped(update: Update, context: CallbackContext):
            if require_message and update.message is None:
                return
            key = key_fn(update)
            game_thread_runner.submit(key, func, update, context)
        return wrapped
    return decorator

@user_locale
def notify_me(update: Update, context: CallbackContext):
    """Handler for /notify_me command, pm people for next game"""
    chat_id = update.message.chat_id
    if update.message.chat.type == 'private':
        send_async(context.bot,
                   chat_id,
                   text=_("Send this command in a group to be notified "
                          "when a new game is started there."))
    else:
        try:
            gm.remind_dict[chat_id].add(update.message.from_user.id)
        except KeyError:
            gm.remind_dict[chat_id] = {update.message.from_user.id}


@user_locale
def new_game(update: Update, context: CallbackContext):
    """Handler for the /new command"""
    chat_id = update.message.chat_id

    if update.message.chat.type == 'private':
        help_handler(update, context)

    else:

        if update.message.chat_id in gm.remind_dict:
            for user in gm.remind_dict[update.message.chat_id]:
                send_async(context.bot,
                           user,
                           text=_("A new game has been started in {title}").format(
                                title=update.message.chat.title))

            del gm.remind_dict[update.message.chat_id]

        game = gm.new_game(update.message.chat)
        game.starter = update.message.from_user
        game.owner.add(update.message.from_user.id)
        game.mode = DEFAULT_GAMEMODE
        gm.persist_game(game)
        send_async(context.bot, chat_id,
                   text=_("Created a new game! Join the game with /join "
                          "and start the game with /start"))


@user_locale
def kill_game(update: Update, context: CallbackContext):
    """Handler for the /kill command"""
    chat = update.message.chat
    user = update.message.from_user
    gm.ensure_chat_loaded(chat)
    games = gm.chatid_games.get(chat.id)

    if update.message.chat.type == 'private':
        help_handler(update, context)
        return

    if not games:
            send_async(context.bot, chat.id,
                       text=_("There is no running game in this chat."))
            return

    game = games[-1]

    if user_is_creator_or_admin(user, game, context.bot, chat):

        try:
            gm.end_game(chat, user)
            send_async(context.bot, chat.id, text=__("Game ended!", multi=game.translate))

        except NoGameInChatError:
            send_async(context.bot, chat.id,
                       text=_("The game is not started yet. "
                              "Join the game with /join and start the game with /start"),
                       reply_to_message_id=update.message.message_id)

    else:
        send_async(context.bot, chat.id,
                  text=_("Only the game creator ({name}) and admin can do that.")
                  .format(name=game.starter.first_name),
                  reply_to_message_id=update.message.message_id)

@user_locale
def join_game(update: Update, context: CallbackContext):
    """Handler for the /join command"""
    chat = update.message.chat

    if update.message.chat.type == 'private':
        help_handler(update, context)
        return

    try:
        gm.join_game(update.message.from_user, chat)

    except LobbyClosedError:
            send_async(context.bot, chat.id, text=_("The lobby is closed"))

    except LobbyFullError:
        send_async(context.bot, chat.id,
                   text=_("The game is full."),
                   reply_to_message_id=update.message.message_id)

    except RejoinNotAllowedError:
        send_async(context.bot, chat.id,
                   text=_("You left this game, so you can't join it again."),
                   reply_to_message_id=update.message.message_id)

    except NoGameInChatError:
        send_async(context.bot, chat.id,
                   text=_("No game is running at the moment. "
                          "Create a new game with /new"),
                   reply_to_message_id=update.message.message_id)

    except AlreadyJoinedError:
        send_async(context.bot, chat.id,
                   text=_("You already joined the game. Start the game "
                          "with /start"),
                   reply_to_message_id=update.message.message_id)

    except DeckEmptyError:
        send_async(context.bot, chat.id,
                   text=_("There are not enough cards left in the deck for "
                          "new players to join."),
                   reply_to_message_id=update.message.message_id)

    else:
        send_async(context.bot, chat.id,
                   text=_("Joined the game"),
                   reply_to_message_id=update.message.message_id)


@user_locale
def leave_game(update: Update, context: CallbackContext):
    """Handler for the /leave command"""
    chat = update.message.chat
    user = update.message.from_user

    player = gm.player_for_user_in_chat(user, chat)

    if player is None:
        send_async(context.bot, chat.id, text=_("You are not playing in a game in "
                                        "this group."),
                   reply_to_message_id=update.message.message_id)
        return

    game = player.game
    user = update.message.from_user
    was_current = player is game.current_player
    was_choosing = was_current and (game.choosing_color or (game.last_card and not game.last_card.color))

    try:
        gm.leave_game(user, chat)

    except NoGameInChatError:
        send_async(context.bot, chat.id, text=_("You are not playing in a game in "
                                        "this group."),
                   reply_to_message_id=update.message.message_id)

    except NotEnoughPlayersError:
        forfeit_win(context.bot, game, user)
        gm.end_game(chat, user)
        send_async(context.bot, chat.id, text=__("Game ended!", multi=game.translate))

    else:
        if was_choosing and game.last_card and game.last_card.color:
            send_async(context.bot, chat.id,
                       text=__("Player left while choosing a color! Color automatically set to {color}.",
                               multi=game.translate).format(color=display_color_group(game.last_card.color, game)))

        if settle_draw_four_after_leave(context.bot, game, player, was_current):
            return
        cancel_penalty_after_leave(context.bot, game, was_current, was_choosing)

        if game.started:
            send_async(context.bot, chat.id,
                       text=__("Okay. Next Player: {name}",
                               multi=game.translate).format(
                           name=display_name(game.current_player.user)),
                       reply_markup=make_turn_keyboard(game, chat.id),
                       reply_to_message_id=update.message.message_id)
            if was_current:
                # The countdown was for the player who left
                start_player_countdown(context.bot, game, context.job_queue)
        else:
            send_async(context.bot, chat.id,
                       text=__("{name} left the game before it started.",
                               multi=game.translate).format(
                           name=display_name(user)),
                       reply_to_message_id=update.message.message_id)


@user_locale
def kick_player(update: Update, context: CallbackContext):
    """Handler for the /kick command"""

    if update.message.chat.type == 'private':
        help_handler(update, context)
        return

    chat = update.message.chat
    user = update.message.from_user
    gm.ensure_chat_loaded(chat)

    try:
        game = gm.chatid_games[chat.id][-1]

    except (KeyError, IndexError):
            send_async(context.bot, chat.id,
                   text=_("No game is running at the moment. "
                          "Create a new game with /new"),
                   reply_to_message_id=update.message.message_id)
            return

    if not game.started:
        send_async(context.bot, chat.id,
                   text=_("The game is not started yet. "
                          "Join the game with /join and start the game with /start"),
                   reply_to_message_id=update.message.message_id)
        return

    if user_is_creator_or_admin(user, game, context.bot, chat):

        if update.message.reply_to_message:
            kicked = update.message.reply_to_message.from_user
            kicked_player = gm.player_for_user_in_chat(kicked, chat)
            was_current = (kicked_player is not None) and (kicked_player is game.current_player)
            was_choosing = was_current and (game.choosing_color or (game.last_card and not game.last_card.color))

            try:
                gm.leave_game(kicked, chat)

            except NoGameInChatError:
                send_async(context.bot, chat.id, text=_("Player {name} is not found in the current game.").format(name=display_name(kicked)),
                                reply_to_message_id=update.message.message_id)
                return

            except NotEnoughPlayersError:
                send_async(context.bot, chat.id,
                                text=_("{0} was kicked by {1}").format(display_name(kicked), display_name(user)))
                if user.id != kicked.id and any(p.user.id == user.id for p in game.players):
                    # Kicking the only opponent must not hand the kicker a win
                    end_game_without_winner(context.bot, game)
                else:
                    forfeit_win(context.bot, game, kicked)
                # End it via the kicked player: the kicking admin may not be playing
                gm.end_game(chat, kicked)
                send_async(context.bot, chat.id, text=__("Game ended!", multi=game.translate))
                return

            send_async(context.bot, chat.id,
                            text=_("{0} was kicked by {1}").format(display_name(kicked), display_name(user)))

            if was_choosing and game.last_card and game.last_card.color:
                send_async(context.bot, chat.id,
                           text=__("Player left while choosing a color! Color automatically set to {color}.",
                                   multi=game.translate).format(color=display_color_group(game.last_card.color, game)))

            if settle_draw_four_after_leave(context.bot, game, kicked_player, was_current):
                return
            cancel_penalty_after_leave(context.bot, game, was_current, was_choosing)

        else:
            send_async(context.bot, chat.id,
                text=_("Please reply to the person you want to kick and type /kick again."),
                reply_to_message_id=update.message.message_id)
            return

        send_async(context.bot, chat.id,
                   text=__("Okay. Next Player: {name}",
                           multi=game.translate).format(
                       name=display_name(game.current_player.user)),
                   reply_markup=make_turn_keyboard(game, chat.id),
                   reply_to_message_id=update.message.message_id)
        if was_current:
            # The countdown was for the kicked player
            start_player_countdown(context.bot, game, context.job_queue)

    else:
        send_async(context.bot, chat.id,
                  text=_("Only the game creator ({name}) and admin can do that.")
                  .format(name=game.starter.first_name),
                  reply_to_message_id=update.message.message_id)


def select_game(update: Update, context: CallbackContext):
    """Handler for callback queries to select the current game"""
    query = update.callback_query
    if query is None:
        return

    try:
        chat_id = int(query.data)
    except (TypeError, ValueError):
        try:
            context.bot.answerCallbackQuery(query.id,
                                            text=_("This button is no longer valid."),
                                            show_alert=False,
                                            timeout=TIMEOUT)
        except BadRequest as err:
            if "Query is too old" not in str(err):
                raise
        return

    try:
        # Acknowledge quickly to avoid callback expiry under load.
        context.bot.answerCallbackQuery(query.id,
                                        text=_("Please switch to the group you selected!"),
                                        show_alert=False,
                                        timeout=TIMEOUT)
    except BadRequest as err:
        if "Query is too old" in str(err):
            return
        raise

    game_thread_runner.submit(
        _callback_game_key(update),
        _select_game_after_ack,
        context.bot,
        query,
        chat_id
    )


def _select_game_after_ack(bot, query, chat_id):
    user_id = query.from_user.id
    gm.ensure_user_loaded(user_id)
    players = gm.userid_players.get(user_id, [])
    for player in players:
        if player.game.chat.id == chat_id:
            gm.userid_current[user_id] = player
            break
    else:
        if query.message:
            send_async(bot, query.message.chat_id, text=_("Game not found."))
        return

    if query.message is None:
        return

    back = [[InlineKeyboardButton(text=_("Back to last group"),
                                  switch_inline_query='')]]
    bot.editMessageText(chat_id=query.message.chat_id,
                        message_id=query.message.message_id,
                        text=_("Selected group: {group}\n"
                               "<b>Make sure that you switch to the correct "
                               "group!</b>").format(
                            group=gm.userid_current[user_id].game.chat.title),
                        reply_markup=InlineKeyboardMarkup(back),
                        parse_mode=ParseMode.HTML,
                        timeout=TIMEOUT)


def callback_router(update: Update, context: CallbackContext):
    """Router for all callback queries (select_game, color selection, quick draw/pass)"""
    query = update.callback_query
    if query is None or not query.data:
        return

    data = query.data
    if data.startswith("color:"):
        parts = data.split(":", 2)
        if len(parts) == 3:
            try:
                chat_id = int(parts[1])
                color = parts[2]
            except ValueError:
                return
            game_thread_runner.submit(
                f"chat:{chat_id}",
                _handle_color_callback,
                context.bot,
                query,
                chat_id,
                color,
                context
            )
            return

    elif data.startswith("action:"):
        parts = data.split(":", 2)
        if len(parts) == 3:
            try:
                chat_id = int(parts[1])
                action = parts[2]
            except ValueError:
                return
            game_thread_runner.submit(
                f"chat:{chat_id}",
                _handle_action_callback,
                context.bot,
                query,
                chat_id,
                action,
                context
            )
            return

    select_game(update, context)


def _answer_callback(bot, query, text=None, show_alert=False):
    try:
        if text is None:
            bot.answerCallbackQuery(query.id)
        else:
            bot.answerCallbackQuery(query.id, text=text, show_alert=show_alert)
    except BadRequest:
        pass


def _game_for_callback(bot, query, chat_id):
    """Load the chat's game for a callback query, or answer the query and return None"""
    chat = query.message.chat if query.message else chat_id
    gm.ensure_chat_loaded(chat)
    games = gm.chatid_games.get(chat_id)
    if not games:
        _answer_callback(bot, query, _("There is no running game in this chat."), show_alert=True)
        return None
    return games[-1]


def _handle_color_callback(bot, query, chat_id, color, context):
    game = _game_for_callback(bot, query, chat_id)
    if game is None:
        return
    # Callbacks run on the game worker threads, outside the locale decorators
    with locales_for_game(game):
        _color_callback_for_game(bot, query, game, chat_id, color, context)


def _color_callback_for_game(bot, query, game, chat_id, color, context):
    if not game.started or not game.choosing_color or color not in c.COLORS:
        _answer_callback(bot, query, _("This button is no longer valid."))
        return

    chooser = game.current_player
    if query.from_user.id != chooser.user.id:
        _answer_callback(bot, query, _("It's not your turn!"), show_alert=True)
        return

    _answer_callback(bot, query)

    color_display = display_color_group(color, game)
    is_opening = getattr(game, 'opening_color_choice', False)

    if query.message:
        try:
            chosen_text = __("🎨 {name} chose {color}.", multi=game.translate).format(
                name=display_name(chooser.user),
                color=color_display
            )
            query.edit_message_text(text=chosen_text, reply_markup=None)
        except (BadRequest, Exception):
            pass

    reset_waiting_time(bot, chooser)
    game.choose_color(color)

    if game_is_running(game):
        gm.persist_game(game)

        if is_opening:
            announcement = __("Starting color set to {color}!\n", multi=game.translate).format(color=color_display)
        else:
            announcement = __("Color set to {color}!\n", multi=game.translate).format(color=color_display)

        nextplayer_message = announcement + __("Next player: {name}", multi=game.translate).format(
            name=display_name(game.current_player.user))

        reply_markup = make_turn_keyboard(game, chat_id)
        send_paced(bot, chat_id, TURN_PACING_DELAY,
                   text=nextplayer_message, reply_markup=reply_markup, disable_notification=True)
        start_player_countdown(bot, game, context.job_queue)


def _handle_action_callback(bot, query, chat_id, action, context):
    game = _game_for_callback(bot, query, chat_id)
    if game is None:
        return
    # Callbacks run on the game worker threads, outside the locale decorators
    with locales_for_game(game):
        _action_callback_for_game(bot, query, game, chat_id, action, context)


def _drawn_card_notice(player):
    """Private popup text naming the card the player just drew, in their own language"""
    drawn = player.cards[-1]
    with locale_for_user(player.user.id):
        notice = _("You drew {card}.").format(card=c.label(drawn))
        if drawn in player.playable_cards():
            notice += "\n" + _("You can play it or pass.")
        else:
            notice += "\n" + _("You can't play it, so pass.")
    return notice


def _action_callback_for_game(bot, query, game, chat_id, action, context):
    if not game.started or game.choosing_color:
        _answer_callback(bot, query, _("This button is no longer valid."))
        return

    player = game.current_player
    if query.from_user.id != player.user.id:
        _answer_callback(bot, query, _("It's not your turn!"), show_alert=True)
        return

    if action != "draw":
        _answer_callback(bot, query)
    player_name = display_name(player.user)

    if action == "draw":
        if player.drew:
            _answer_callback(bot, query)
            return
        draw_counter_before = getattr(game, 'draw_counter', 0)
        reset_waiting_time(bot, player)
        do_draw(bot, player)
        gm.persist_game(game)

        # Tell only the drawing player which card they got (the callback answer
        # is private), so they can decide to play or pass without the inline menu
        if game_is_running(game) and game.current_player is player and player.drew_card:
            _answer_callback(bot, query, _drawn_card_notice(player), show_alert=True)
        else:
            _answer_callback(bot, query)

        if game_is_running(game):
            cards_drawn = draw_counter_before or 1
            if cards_drawn > 1:
                draw_action_text = __("📥 {name} drew {number} cards.", multi=game.translate).format(
                    name=player_name, number=cards_drawn
                )
            else:
                draw_action_text = __("📥 {name} drew a card.", multi=game.translate).format(
                    name=player_name
                )

            if game.current_player == player:
                # Normal draw: still player's turn!
                prompt_text = draw_action_text + "\n" + __("Next player: {name}", multi=game.translate).format(
                    name=player_name)

                reply_markup = make_turn_keyboard(game, chat_id)
                if query.message:
                    try:
                        query.edit_message_text(text=prompt_text, reply_markup=reply_markup)
                    except (BadRequest, Exception):
                        send_async(bot, chat_id, text=prompt_text, reply_markup=reply_markup, disable_notification=True)
                else:
                    send_async(bot, chat_id, text=prompt_text, reply_markup=reply_markup, disable_notification=True)

                start_player_countdown(bot, game, context.job_queue)

            else:
                # Penalty draw (+2 or +4): turn advanced to next player
                if query.message:
                    try:
                        query.edit_message_text(text=draw_action_text, reply_markup=None)
                    except (BadRequest, Exception):
                        pass

                nextplayer_message = __("Next player: {name}", multi=game.translate).format(
                    name=display_name(game.current_player.user))

                reply_markup = make_turn_keyboard(game, chat_id)
                send_paced(bot, chat_id, TURN_PACING_DELAY,
                           text=nextplayer_message, reply_markup=reply_markup, disable_notification=True)
                start_player_countdown(bot, game, context.job_queue)

    elif action == "pass":
        if not player.drew:
            return
        reset_waiting_time(bot, player)
        game.turn()
        gm.persist_game(game)

        if query.message:
            try:
                pass_action_text = __("⏭️ {name} passed.", multi=game.translate).format(
                    name=player_name
                )
                query.edit_message_text(text=pass_action_text, reply_markup=None)
            except (BadRequest, Exception):
                pass

        if game_is_running(game):
            pass_msg = __("Next player: {name}", multi=game.translate).format(
                name=display_name(game.current_player.user))

            reply_markup = make_turn_keyboard(game, chat_id)
            send_paced(bot, chat_id, TURN_PACING_DELAY,
                       text=pass_msg, reply_markup=reply_markup, disable_notification=True)
            start_player_countdown(bot, game, context.job_queue)


@game_locales
def status_update(update: Update, context: CallbackContext):
    """Remove player from game if user leaves the group"""
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    if message.left_chat_member:
        user = message.left_chat_member
        player = gm.player_for_user_in_chat(user, chat)
        if not player:
            return
        game = player.game
        was_current = player is game.current_player
        was_choosing = was_current and (game.choosing_color or (game.last_card and not game.last_card.color))

        try:
            gm.leave_game(user, chat)

        except NoGameInChatError:
            pass
        except NotEnoughPlayersError:
            forfeit_win(context.bot, game, user)
            gm.end_game(chat, user)
            send_async(context.bot, chat.id, text=__("Game ended!",
                                             multi=game.translate))
        else:
            send_async(context.bot, chat.id, text=__("Removing {name} from the game",
                                             multi=game.translate)
                       .format(name=display_name(user)))
            if was_choosing and game.last_card and game.last_card.color:
                send_async(context.bot, chat.id,
                           text=__("Player left while choosing a color! Color automatically set to {color}.",
                                   multi=game.translate).format(color=display_color_group(game.last_card.color, game)))
            if settle_draw_four_after_leave(context.bot, game, player, was_current):
                return
            cancel_penalty_after_leave(context.bot, game, was_current, was_choosing)
            if was_current and game.started:
                send_async(context.bot, chat.id,
                           text=__("Next player: {name}", multi=game.translate)
                           .format(name=display_name(game.current_player.user)),
                           reply_markup=make_turn_keyboard(game, chat.id),
                           disable_notification=True)
                # The countdown was for the player who left
                start_player_countdown(context.bot, game, context.job_queue)


@game_locales
@user_locale
def start_game(update: Update, context: CallbackContext):
    """Handler for the /start command"""

    if update.message.chat.type != 'private':
        chat = update.message.chat
        gm.ensure_chat_loaded(chat)

        try:
            game = gm.chatid_games[chat.id][-1]
        except (KeyError, IndexError):
            send_async(context.bot, chat.id,
                       text=_("There is no game running in this chat. Create "
                              "a new one with /new"))
            return

        if game.started:
            send_async(context.bot, chat.id, text=_("The game has already started"))

        elif len(game.players) < MIN_PLAYERS:
            send_async(context.bot, chat.id,
                       text=__("At least {minplayers} players must /join the game "
                              "before you can start it").format(minplayers=MIN_PLAYERS))

        else:
            # Starting a game
            game.start()

            try:
                for player in game.players:
                    player.draw_first_hand()
            except DeckEmptyError:
                game.abort_start()
                gm.persist_game(game)
                send_async(context.bot, chat.id,
                           text=_("There are not enough cards in the deck for "
                                  "this many players."))
                return
            gm.persist_game(game)
            if game.choosing_color:
                first_message = (
                    __("First card is Wild!\n"
                       "First player: {name}\n"
                       "Please choose the starting color.\n"
                       "Use /close to stop people from joining the game.\n"
                       "Enable multi-translations with /enable_translations",
                       multi=game.translate)
                    .format(name=display_name(game.current_player.user)))
                markup = make_color_keyboard(chat.id, game)
            else:
                first_message = (
                    __("First player: {name}\n"
                       "Use /close to stop people from joining the game.\n"
                       "Enable multi-translations with /enable_translations",
                       multi=game.translate)
                    .format(name=display_name(game.current_player.user)))
                markup = make_turn_keyboard(game, chat.id)

            def send_first():
                """Send the first card and player"""

                context.bot.sendSticker(chat.id,
                                sticker=c.STICKERS[str(game.last_card)],
                                timeout=TIMEOUT,
                                disable_notification=True)

                context.bot.sendMessage(chat.id,
                                text=first_message,
                                reply_markup=markup,
                                timeout=TIMEOUT,
                                disable_notification=True)

            dispatcher.run_async(send_first)
            start_player_countdown(context.bot, game, context.job_queue)

    elif len(context.args) and context.args[0] == 'select':
        gm.ensure_user_loaded(update.message.from_user.id)
        players = gm.userid_players[update.message.from_user.id]

        groups = list()
        for player in players:
            title = player.game.chat.title

            if player == gm.userid_current[update.message.from_user.id]:
                title = '- %s -' % player.game.chat.title

            groups.append(
                [InlineKeyboardButton(text=title,
                                      callback_data=str(player.game.chat.id))]
            )

        send_async(context.bot, update.message.chat_id,
                   text=_('Please select the group you want to play in.'),
                   reply_markup=InlineKeyboardMarkup(groups))

    else:
        help_handler(update, context)


@user_locale
def close_game(update: Update, context: CallbackContext):
    """Handler for the /close command"""
    chat = update.message.chat
    user = update.message.from_user
    gm.ensure_chat_loaded(chat)
    games = gm.chatid_games.get(chat.id)

    if not games:
        send_async(context.bot, chat.id,
                   text=_("There is no running game in this chat."))
        return

    game = games[-1]

    if user.id in game.owner:
        game.open = False
        gm.persist_game(game)
        send_async(context.bot, chat.id, text=_("Closed the lobby. "
                                        "No more players can join this game."))
        return

    else:
        send_async(context.bot, chat.id,
                   text=_("Only the game creator ({name}) and admin can do that.")
                   .format(name=game.starter.first_name),
                   reply_to_message_id=update.message.message_id)
        return


@user_locale
def open_game(update: Update, context: CallbackContext):
    """Handler for the /open command"""
    chat = update.message.chat
    user = update.message.from_user
    gm.ensure_chat_loaded(chat)
    games = gm.chatid_games.get(chat.id)

    if not games:
        send_async(context.bot, chat.id,
                   text=_("There is no running game in this chat."))
        return

    game = games[-1]

    if user.id in game.owner:
        game.open = True
        gm.persist_game(game)
        send_async(context.bot, chat.id, text=_("Opened the lobby. "
                                        "New players may /join the game."))
        return
    else:
        send_async(context.bot, chat.id,
                   text=_("Only the game creator ({name}) and admin can do that.")
                   .format(name=game.starter.first_name),
                   reply_to_message_id=update.message.message_id)
        return


@user_locale
def enable_translations(update: Update, context: CallbackContext):
    """Handler for the /enable_translations command"""
    chat = update.message.chat
    user = update.message.from_user
    gm.ensure_chat_loaded(chat)
    games = gm.chatid_games.get(chat.id)

    if not games:
        send_async(context.bot, chat.id,
                   text=_("There is no running game in this chat."))
        return

    game = games[-1]

    if user.id in game.owner:
        game.translate = True
        gm.persist_game(game)
        send_async(context.bot, chat.id, text=_("Enabled multi-translations. "
                                        "Disable with /disable_translations"))
        return

    else:
        send_async(context.bot, chat.id,
                   text=_("Only the game creator ({name}) and admin can do that.")
                   .format(name=game.starter.first_name),
                   reply_to_message_id=update.message.message_id)
        return


@user_locale
def disable_translations(update: Update, context: CallbackContext):
    """Handler for the /disable_translations command"""
    chat = update.message.chat
    user = update.message.from_user
    gm.ensure_chat_loaded(chat)
    games = gm.chatid_games.get(chat.id)

    if not games:
        send_async(context.bot, chat.id,
                   text=_("There is no running game in this chat."))
        return

    game = games[-1]

    if user.id in game.owner:
        game.translate = False
        gm.persist_game(game)
        send_async(context.bot, chat.id, text=_("Disabled multi-translations. "
                                        "Enable them again with "
                                        "/enable_translations"))
        return

    else:
        send_async(context.bot, chat.id,
                   text=_("Only the game creator ({name}) and admin can do that.")
                   .format(name=game.starter.first_name),
                   reply_to_message_id=update.message.message_id)
        return


@game_locales
@user_locale
def skip_player(update: Update, context: CallbackContext):
    """Handler for the /skip command"""
    chat = update.message.chat
    user = update.message.from_user

    player = gm.player_for_user_in_chat(user, chat)
    if not player:
        send_async(context.bot, chat.id,
                   text=_("You are not playing in a game in this chat."))
        return

    game = player.game
    skipped_player = game.current_player

    started = skipped_player.turn_started
    now = datetime.now()
    # total_seconds(): .seconds wraps a negative delta (clock set back) to ~86400
    delta = max(0.0, (now - started).total_seconds())

    # You can't skip if the current player still has time left
    # You can skip yourself even if you have time left (you'll still draw)
    if delta < skipped_player.waiting_time and player != skipped_player:
        n = max(1, int(skipped_player.waiting_time - delta))
        send_async(context.bot, chat.id,
                   text=_("Please wait {time} second",
                          "Please wait {time} seconds",
                          n)
                   .format(time=n),
                   reply_to_message_id=update.message.message_id)
    else:
        # Pass the job queue so fast mode starts the next player's countdown;
        # passing the skipped (current) player keeps do_skip's stale-timer check happy
        do_skip(context.bot, skipped_player, context.job_queue)


@game_locales
@user_locale
def reply_to_query(update: Update, context: CallbackContext):
    """
    Handler for inline queries.
    Builds the result list for inline queries and answers to the client.
    """
    results = list()
    switch = None

    try:
        user = update.inline_query.from_user
        user_id = user.id
        gm.ensure_user_loaded(user_id)
        players = gm.userid_players[user_id]
        player = gm.userid_current[user_id]
        game = player.game
    except KeyError:
        add_no_game(results)
    else:

        # The game has not started.
        # The creator may change the game mode, other users just get a "game has not started" message.
        if not game.started:
            if user_is_creator(user, game):
                add_mode_classic(results)
                add_mode_fast(results)
                add_mode_wild(results)
                add_mode_text(results)
            else:
                add_not_started(results)


        elif user_id == game.current_player.user.id:
            if game.choosing_color:
                # The color is picked with the buttons under the color prompt, so the
                # menu only shows the hand, greyed out like when it's not your turn
                for card in sorted(player.cards):
                    add_card(game, card, results, can_play=False)
            else:
                if not player.drew:
                    add_draw(player, results)

                else:
                    add_pass(results, game)

                if game.last_card.special == c.DRAW_FOUR and game.draw_counter:
                    add_call_bluff(results, game)

                playable = player.playable_cards()
                added_ids = list()  # Duplicates are not allowed

                for card in sorted(player.cards):
                    add_card(game, card, results,
                             can_play=(card in playable and
                                            str(card) not in added_ids))
                    added_ids.append(str(card))

                add_gameinfo(game, results)

        elif user_id != game.current_player.user.id or not game.started:
            for card in sorted(player.cards):
                add_card(game, card, results, can_play=False)

        else:
            add_gameinfo(game, results)

        for result in results:
            result.id += ':%d' % player.anti_cheat

        if players and game and len(players) > 1:
            switch = _('Current game: {game}').format(game=game.chat.title)

    answer_async(context.bot, update.inline_query.id, results, cache_time=0,
                 is_personal=True, switch_pm_text=switch, switch_pm_parameter='select')


@game_locales
@user_locale
def process_result(update: Update, context: CallbackContext):
    """
    Handler for chosen inline results.
    Checks the players actions and acts accordingly.
    """
    try:
        user = update.chosen_inline_result.from_user
        gm.ensure_user_loaded(user.id)
        player = gm.userid_current[user.id]
        game = player.game
        result_id = update.chosen_inline_result.result_id
        chat = game.chat
    except (KeyError, AttributeError):
        return

    logger.debug("Selected result: " + result_id)

    # Inline result ids from stale/legacy clients may not carry anti-cheat data.
    # Ignore them instead of crashing the update handler.
    if ':' not in result_id:
        logger.info("Ignoring malformed chosen inline result id: %s", result_id)
        return

    result_id, anti_cheat = result_id.rsplit(':', 1)
    if not anti_cheat.isdigit():
        logger.info("Ignoring malformed anti-cheat token in result id: %s", anti_cheat)
        return

    last_anti_cheat = player.anti_cheat
    player.anti_cheat += 1
    played_card = False
    color_chosen_announcement = ""

    if result_id in ('hand', 'gameinfo', 'nogame'):
        return
    elif result_id.startswith('mode_'):
        # First 5 characters are 'mode_', the rest is the gamemode.
        mode = result_id[5:]
        if game.started or mode not in GAME_MODES or not user_is_creator(user, game):
            logger.info("Ignoring mode change to %s by %s", mode, display_name(user))
            return
        game.set_mode(mode)
        gm.persist_game(game)
        logger.info("Gamemode changed to {mode}".format(mode = mode))
        send_async(context.bot, chat.id, text=__("Gamemode changed to {mode}", multi=game.translate).format(mode=mode))
        return
    elif len(result_id) == 36:  # UUID result
        return
    elif int(anti_cheat) != last_anti_cheat:
        # A menu opened before the last action/timeout; usually lag, not cheating,
        # so don't accuse anyone in the group
        logger.info("Ignoring stale inline token %s (current %s) from %s",
                    anti_cheat, last_anti_cheat, display_name(player.user))
        return
    elif not _inline_action_allowed(player, game, result_id):
        # Results from a menu opened earlier stay tappable after the state changed
        # (button draw/pass, timeout, ...), so every action is checked against the game.
        logger.info("Ignoring stale or invalid inline result %s from %s",
                    result_id, display_name(player.user))
        return
    elif result_id == 'call_bluff':
        if (player == game.current_player and
                game.last_card and
                game.last_card.special == c.DRAW_FOUR and
                game.draw_counter > 0):
            reset_waiting_time(context.bot, player)
            do_call_bluff(context.bot, player)
        else:
            return
    elif result_id == 'draw':
        reset_waiting_time(context.bot, player)
        do_draw(context.bot, player)
    elif result_id == 'pass':
        reset_waiting_time(context.bot, player)
        game.turn()
    elif result_id in c.COLORS:
        if game.choosing_color and player == game.current_player:
            reset_waiting_time(context.bot, player)
            is_opening = getattr(game, 'opening_color_choice', False)
            game.choose_color(result_id)
            if is_opening:
                color_chosen_announcement = __("Starting color set to {color}!\n", multi=game.translate).format(
                    color=display_color_group(result_id, game)
                )
            else:
                color_chosen_announcement = __("Color set to {color}!\n", multi=game.translate).format(
                    color=display_color_group(result_id, game)
                )
        else:
            return
    else:
        reset_waiting_time(context.bot, player)
        do_play_card(context.bot, player, result_id, notify_color=False, notify_uno=False)
        played_card = True

    if game_is_running(game):
        gm.persist_game(game)
        # Only announce UNO right after the card that left the player with one card
        uno_prefix = "🔥 UNO!\n" if played_card and len(player.cards) == 1 else ""
        if game.choosing_color:
            color_prompt = uno_prefix + __("{name}, please choose a color:", multi=game.translate).format(
                name=display_name(game.current_player.user)
            )
            markup = make_color_keyboard(chat.id, game)
            # Unpaced, but queued with the paced prompts so it can't overtake them
            send_paced(context.bot, chat.id,
                       text=color_prompt,
                       reply_markup=markup,
                       disable_notification=True)
            start_player_countdown(context.bot, game, context.job_queue)
        else:
            nextplayer_message = color_chosen_announcement + uno_prefix + (
                __("Next player: {name}", multi=game.translate)
                .format(name=display_name(game.current_player.user)))
            markup = make_turn_keyboard(game, chat.id)
            send_paced(context.bot, chat.id, TURN_PACING_DELAY,
                       text=nextplayer_message,
                       reply_markup=markup,
                       disable_notification=True)
            start_player_countdown(context.bot, game, context.job_queue)


def _inline_action_allowed(player, game, result_id):
    """Check a chosen inline result against the current game state"""
    if not game.started or player is not game.current_player:
        return False

    if result_id == 'call_bluff':
        return bool(not game.choosing_color and game.last_card and
                    game.last_card.special == c.DRAW_FOUR and game.draw_counter > 0)
    if result_id == 'draw':
        return not game.choosing_color and not player.drew
    if result_id == 'pass':
        return not game.choosing_color and player.drew
    if result_id in c.COLORS:
        return game.choosing_color

    if game.choosing_color:
        return False
    card = c.from_str(result_id)
    return card is not None and card in player.playable_cards()


def reset_waiting_time(bot, player):
    """Resets waiting time for a player and sends a notice to the group"""
    chat = player.game.chat

    if player.waiting_time < WAITING_TIME:
        player.waiting_time = WAITING_TIME
        gm.persist_game(player.game)
        send_async(bot, chat.id,
                   text=__("Waiting time for {name} has been reset to {time} "
                           "seconds", multi=player.game.translate)
                   .format(name=display_name(player.user), time=WAITING_TIME))


# Add all handlers to the dispatcher and run the bot
threaded_new_game = _threaded_by(_chat_key, require_message=True)(new_game)
threaded_kill_game = _threaded_by(_chat_key, require_message=True)(kill_game)
threaded_join_game = _threaded_by(_chat_key, require_message=True)(join_game)
threaded_leave_game = _threaded_by(_chat_key, require_message=True)(leave_game)
threaded_kick_player = _threaded_by(_chat_key, require_message=True)(kick_player)
threaded_start_game = _threaded_by(_chat_key, require_message=True)(start_game)
threaded_close_game = _threaded_by(_chat_key, require_message=True)(close_game)
threaded_open_game = _threaded_by(_chat_key, require_message=True)(open_game)
threaded_enable_translations = _threaded_by(_chat_key, require_message=True)(enable_translations)
threaded_disable_translations = _threaded_by(_chat_key, require_message=True)(disable_translations)
threaded_skip_player = _threaded_by(_chat_key, require_message=True)(skip_player)
threaded_status_update = _threaded_by(_chat_key, require_message=True)(status_update)
threaded_process_result = _threaded_by(_inline_result_key)(process_result)
threaded_reply_to_query = _threaded_by(_inline_query_key)(reply_to_query)
threaded_notify_me = _threaded_by(_chat_key, require_message=True)(notify_me)

# Enforce access restrictions before all other handlers
dispatcher.add_handler(TypeHandler(Update, access_control), group=-1)

dispatcher.add_handler(InlineQueryHandler(threaded_reply_to_query))
try:
    dispatcher.add_handler(ChosenInlineResultHandler(threaded_process_result, pass_job_queue=True))
except TypeError:
    dispatcher.add_handler(ChosenInlineResultHandler(threaded_process_result))

dispatcher.add_handler(CallbackQueryHandler(callback_router))

try:
    dispatcher.add_handler(CommandHandler('start', threaded_start_game, pass_args=True, pass_job_queue=True))
except TypeError:
    dispatcher.add_handler(CommandHandler('start', threaded_start_game))
dispatcher.add_handler(CommandHandler('new', threaded_new_game))
dispatcher.add_handler(CommandHandler('kill', threaded_kill_game))
dispatcher.add_handler(CommandHandler('join', threaded_join_game))
dispatcher.add_handler(CommandHandler('leave', threaded_leave_game))
dispatcher.add_handler(CommandHandler('kick', threaded_kick_player))
dispatcher.add_handler(CommandHandler('open', threaded_open_game))
dispatcher.add_handler(CommandHandler('close', threaded_close_game))
dispatcher.add_handler(CommandHandler('enable_translations',
                                      threaded_enable_translations))
dispatcher.add_handler(CommandHandler('disable_translations',
                                      threaded_disable_translations))
dispatcher.add_handler(CommandHandler('skip', threaded_skip_player))
dispatcher.add_handler(CommandHandler('notify_me', threaded_notify_me))
simple_commands.register()
settings.register()
rivalry.register()
dispatcher.add_handler(MessageHandler(Filters.status_update, threaded_status_update))
dispatcher.add_error_handler(error)

if __name__ == '__main__':
    start_bot(updater)
    updater.idle()
