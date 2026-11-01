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

# Modify this file if you want a different startup sequence, for example using
# a Webhook

import logging
from pathlib import Path

from telegram import BotCommand


logger = logging.getLogger(__name__)


def _load_bot_commands():
    commands_path = Path(__file__).with_name('commandlist.txt')
    commands = []

    for line in commands_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        command, separator, description = line.partition(' - ')
        if not separator:
            logger.warning("[WARN][commands] Skipping malformed command line: %s",
                           line)
            continue

        commands.append(BotCommand(command.lstrip('/').strip(),
                                   description.strip()))

    return commands


def set_bot_commands(updater):
    commands = _load_bot_commands()
    if not commands:
        logger.warning("[WARN][commands] No bot commands configured")
        return

    try:
        updater.bot.set_my_commands(commands)
    except Exception:
        logger.warning("[WARN][commands] Failed to set Telegram bot commands",
                       exc_info=True)
    else:
        logger.info("[INFO][commands] Set %d Telegram bot commands",
                    len(commands))


def start_bot(updater):
    set_bot_commands(updater)
    from bot import reschedule_pending_unos
    reschedule_pending_unos(getattr(updater, 'job_queue', None))
    updater.start_polling()
