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

from utils import send_async
from shared_vars import dispatcher
from internationalization import _, user_locale

@user_locale
def help_handler(update: Update, context: CallbackContext):
    """Handler for the /help command"""
    help_text = _("Follow these steps:\n\n"
      "1. Add this bot to a group\n"
      "2. In the group, start a new game with /new or join an already"
      " running game with /join\n"
      "3. After at least two players have joined, start the game with"
      " /start\n"
      "4. Type the bot's username into your chat box and hit "
      "<b>space</b>, or click the <code>via @the bot's username</code> text "
      "next to messages. You will see your cards (some greyed out), "
      "any extra options like drawing, and a <b>?</b> to see the "
      "current game state. The <b>greyed out cards</b> are those you "
      "<b>can not play</b> at the moment. Tap an option to execute "
      "the selected action.\n"
      "Players can join the game at any time. To leave a game, "
      "use /leave. Use /notify_me to "
      "receive a private message when a new game is started.\n\n"
      "<b>Language</b> and other settings: /settings\n"
      "Other commands (only game creator):\n"
      "/close - Close lobby\n"
      "/open - Open lobby\n"
      "/kill - Terminate the game\n"
      "/kick - Select a player to kick "
      "by replying to him or her\n"
      "/enable_translations - Translate relevant texts into all "
      "languages spoken in a game\n"
      "/disable_translations - Use English for those texts\n\n")

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
    modes_explanation = _("This UNO bot has four game modes: Classic, Sanic, Wild and Text.\n\n"
      " 🎻 The Classic mode uses the conventional UNO deck and there is no auto skip.\n"
      " 🚀 The Sanic mode uses the conventional UNO deck and the bot automatically skips a player if he/she takes too long to play its turn\n"
      " 🐉 The Wild mode uses a deck with more special cards, less number variety and no auto skip.\n"
      " ✍️ The Text mode uses the conventional UNO deck but instead of stickers it uses the text.\n\n"
      "To change the game mode, the GAME CREATOR has to type the bot nickname and a space, "
      "just like when playing a card, and all gamemode options should appear.")
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
