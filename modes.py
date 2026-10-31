#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# The game modes and the rules each one plays by.
#
# A mode's id is stored with saved games and with every rivalry result, so it
# must never change. Its deck is one of the decks in web/decks.js; the deck
# also decides which colors the game uses.


def N_(message):
    """Marks a message for translation (genpot.sh); it's translated where it's shown"""
    return message


class Mode(object):
    """The rules of one game mode"""

    def __init__(self, id, title, message, deck='classic', hand_size=7, auto_skip=False):
        self.id = id
        # Title of the inline result that picks this mode, and the message it sends
        self.title = title
        self.message = message
        self.deck = deck
        self.hand_size = hand_size
        # Whether the bot skips a player who takes too long (see actions.start_player_countdown)
        self.auto_skip = auto_skip


CLASSIC = Mode('classic', N_("🎻 Classic mode"), N_('Classic 🎻'))
FAST = Mode('fast', N_("🚀 Sanic mode"), N_('Gotta go fast! 🚀'), auto_skip=True)
WILD = Mode('wild', N_("🐉 Wild mode"), N_('Into the Wild~ 🐉'), deck='wild')

# In the order the inline menu offers them
MODES = {mode.id: mode for mode in (CLASSIC, FAST, WILD)}

# Not a mode of its own: a lobby set to it plays one of MODES, picked at /start
# with the game's random source (Game.start), so results only ever name a real mode
RANDOM = Mode('random', N_("🎲 Random mode"), N_('Leave it to chance 🎲'))

# What the creator can pick in the inline menu, and the config's default_gamemode
CHOICES = {**MODES, RANDOM.id: RANDOM}


def get(mode_id):
    """The mode called `mode_id`; classic for a game without a known mode
    (or a random one that hasn't been picked yet)"""
    return MODES.get(mode_id, CLASSIC)
