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

from telegram import ParseMode, Update
from telegram.ext import CommandHandler, CallbackContext

import rivalry
from config import MIN_PLAYERS
from utils import send_async
from shared_vars import dispatcher
from internationalization import _, user_locale

@user_locale
def help_handler(update: Update, context: CallbackContext):
    """Handler for the /help command"""
    help_text = _(
        "<b>How to play</b>\n"
        "1. Add this bot to a group.\n"
        "2. Create a game with /new, or join the group's game with /join.\n"
        "3. When at least {min_players} players have joined, deal the cards with /start.\n"
        "4. Type the bot's username and a <b>space</b> in the chat box, or tap the "
        "<code>via @bot</code> text next to its messages. Your cards appear: the "
        "<b>greyed out</b> ones can't be played right now. Drawing, passing and a "
        "<b>?</b> for the game state are in the same list. Tap one to play it.\n\n"
        "<b>Playing</b>\n"
        "/new - Create a game in this group\n"
        "/join - Join this group's game, even after it started\n"
        "/start - Deal the cards and begin\n"
        "/seed - Only when the bot asks at the start: add any text to the game's "
        "randomness because drand can't be reached\n"
        "/leave - Leave the game\n"
        "/skip - Skip the player whose turn it is once their time is up "
        "(they draw a card); you can always skip yourself\n"
        "/notify_me - Get a private message when the next game starts here\n\n"
        "<b>Game creator</b>\n"
        "/close - Stop new players from joining\n"
        "/open - Let new players join again\n"
        "/enable_translations - Show game messages in every player's language\n"
        "/disable_translations - Show them in one language again\n"
        "Game mode: before the game starts, type the bot's username and a space "
        "and pick a mode (/modes explains them)\n\n"
        "<b>Game creator or group admins</b>\n"
        "/kick - Reply to a player's message with /kick to remove them\n"
        "/kill - End the game\n\n"
        "<b>Other</b>\n"
        "/settings - Language and other settings (in a private chat with the bot)\n"
        "/modes - The game modes\n"
        "/source - Source code and credits\n"
        "/help - This message").format(min_players=MIN_PLAYERS)
    # Its own message, so the translations of the text above still apply
    help_text += "\n\n" + _("<b>Storm mode</b>\n"
                            "/cancel - On your turn: play your next Swap or Drop without its effect "
                            "(/cancel again turns it back on)")
    rivalry_help = rivalry.help_text()
    if rivalry_help:
        help_text += "\n\n" + rivalry_help

    def _send():
      update.message.chat.send_message(
          help_text,
          parse_mode=ParseMode.HTML,
          disable_web_page_preview=True,
      )

    context.dispatcher.run_async(_send)

@user_locale
def modes(update: Update, context: CallbackContext):
    """Handler for the /help command"""
    modes_explanation = _("This UNO bot has three game modes: Classic, Sanic and Wild.\n\n"
      " 🎻 The Classic mode uses the conventional UNO deck and there is no auto skip.\n"
      " 🚀 The Sanic mode uses the conventional UNO deck and the bot automatically skips a player if he/she takes too long to play its turn\n"
      " 🐉 The Wild mode uses a deck with more special cards, less number variety and no auto skip.\n\n"
      "To change the game mode, the GAME CREATOR has to type the bot nickname and a space, "
      "just like when playing a card, and all gamemode options should appear.")
    # Their own messages, so the translations of the text above still apply
    modes_explanation += "\n\n" + _("🌋 The Volcano mode uses the conventional UNO deck, and each time you draw on your "
                                    "turn the volcano may erupt and give you 3 more cards: a 1 in 10 chance at first, "
                                    "lower with every draw of the game, and at most 5 eruptions per game.")
    modes_explanation += "\n\n" + _("🌧️ The Storm mode uses a deck without 0s and with three cards of its own: "
                                    "+1 gives each opponent a card at once (it doesn't stack), Swap exchanges your "
                                    "hand with the next player's and Drop puts your hand back into the deck and "
                                    "deals you as many new cards. Use /cancel before playing a Swap or Drop to "
                                    "play it without its effect.")
    modes_explanation += "\n\n" + _("🎲 The Random mode plays one of the modes, picked by the game's "
                                    "fair random source when it starts and revealed then.")
    send_async(context.bot, update.message.chat_id, text=modes_explanation,
               parse_mode=ParseMode.HTML, disable_web_page_preview=True)

@user_locale
def source(update: Update, context: CallbackContext):
    """Handler for the /help command"""
    source_text = (
        "Source code: https://github.com/1ahmadbassam/uno-bot\n\n"
        "Fork of "
        "https://github.com/jh0ker/mau_mau_bot\n\n"
        "Attributions:\n"
        "Draw icon by Faithtoken (http://www.faithtoken.com/)\n"
        "Pass icon by Delapouite (http://delapouite.com/)\n"
        "Originals available on http://game-icons.net\n"
        "Icons edited by ɳick"
    )

    send_async(context.bot, update.message.chat_id, text=source_text,
               disable_web_page_preview=True)


def register():
    dispatcher.add_handler(CommandHandler('help', help_handler))
    dispatcher.add_handler(CommandHandler('source', source))
    dispatcher.add_handler(CommandHandler('modes', modes))
