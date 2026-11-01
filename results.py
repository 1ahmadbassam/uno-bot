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


"""Defines helper functions to build the inline result list"""

from uuid import uuid4

from telegram import InlineQueryResultArticle, InputTextMessageContent, \
    InlineQueryResultCachedSticker as Sticker

import card as c
from internationalization import _, __


def seating(game):
    """The players in their seats, from the current player on, whichever way play goes"""
    players = game.players
    if not game.reversed or len(players) < 3:
        return players
    return players[:1] + players[:0:-1]


def player_list(game):
    """Generate list of player strings; the Crazy mode hides how many cards they hold"""
    if game.rules.hide_counts:
        return [_("{name} (● cards)").format(name=player.user.first_name) for player in seating(game)]
    return [_("{name} ({number} card)",
              "{name} ({number} cards)",
              len(player.cards))
            .format(name=player.user.first_name, number=len(player.cards))
            for player in seating(game)]


def add_no_game(results):
    """Add text result if user is not playing"""
    results.append(
        InlineQueryResultArticle(
            "nogame",
            title=_("You are not playing"),
            input_message_content=
            InputTextMessageContent(_('Not playing right now. Use /new to '
                                      'start a game or /join to join the '
                                      'current game in this group'))
        )
    )


def add_not_started(results):
    """Add text result if the game has not yet started"""
    results.append(
        InlineQueryResultArticle(
            "nogame",
            title=_("The game wasn't started yet"),
            input_message_content=
            InputTextMessageContent(_('Start the game with /start'))
        )
    )


def add_mode(results, mode):
    """Add the option to change to the modes.Mode `mode`"""
    results.append(
        InlineQueryResultArticle(
            "mode_" + mode.id,
            title=_(mode.title),
            input_message_content=
            InputTextMessageContent(_(mode.message))
        )
    )


def add_draw(player, results):
    """Add option to draw"""
    n = player.game.draw_counter or 1
    last = player.game.last_card
    if last is not None and last.special == c.XENO and not player.game.draw_counter:
        # As many as it takes (Player.draw)
        text = __("Drawing until the Xeno's color", multi=player.game.translate)
    else:
        text = __('Drawing {number} card', 'Drawing {number} cards', n, multi=player.game.translate).format(number=n)

    results.append(
        Sticker(
            "draw", sticker_file_id=c.STICKERS['option_draw'],
            input_message_content=InputTextMessageContent(text)
        )
    )


def add_uno(game, results):
    """Add option to declare UNO"""
    results.append(
        Sticker(
            "uno", sticker_file_id=c.STICKERS['option_uno'],
            input_message_content=InputTextMessageContent("🔥 UNO!")
        )
    )


def add_gameinfo(game, results):
    """Add option to show game info"""

    results.append(
        Sticker(
            "gameinfo",
            sticker_file_id=c.STICKERS['option_info'],
            input_message_content=game_info(game)
        )
    )


def add_pass(results, game):
    """Add option to pass"""
    results.append(
        Sticker(
            "pass", sticker_file_id=c.STICKERS['option_pass'],
            input_message_content=InputTextMessageContent(
                __('Pass', multi=game.translate)
            )
        )
    )


def add_call_bluff(results, game):
    """Add option to call a bluff"""
    results.append(
        Sticker(
            "call_bluff",
            sticker_file_id=c.STICKERS['option_bluff'],
            input_message_content=
            InputTextMessageContent(__("I'm calling your bluff!",
                                       multi=game.translate))
        )
    )


def add_card(game, card, results, can_play):
    """Add an option that represents a card"""

    if can_play:
        results.append(
            Sticker(str(card), sticker_file_id=c.STICKERS[str(card)])
        )
    else:
        results.append(
            Sticker(str(uuid4()), sticker_file_id=c.STICKERS_GREY[str(card)],
                    input_message_content=game_info(game))
        )


def game_info(game):
    """The game info message: first names only, so nobody gets pinged, and the
    players in their seats with the direction of play"""
    separator = "\n    — \n"
    return InputTextMessageContent(
        _("Your turn: {name}").format(name=game.current_player.user.first_name) +
        separator +
        _("Card: {card}").format(card=c.spaced_label(game.last_card)) +
        separator +
        # ⟳: play goes down the list (and from the last back to the first), ⟲: up
        (_("Players (⟲):") if game.reversed else _("Players (⟳):")) + "\n" +
        "\n".join("• " + player for player in player_list(game))
    )
