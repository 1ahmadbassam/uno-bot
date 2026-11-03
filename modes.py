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

    def __init__(self, id, title, message, deck='classic', hand_size=7, auto_skip=False, volcano=False, storm=False,
                 bluff=True, tornado=False, hide_counts=False, diablo=False, xeno=False, first_win_ends=False, nitro=False):
        self.id = id
        # Title of the inline result that picks this mode, and the message it sends
        self.title = title
        self.message = message
        self.deck = deck
        self.hand_size = hand_size
        # Whether the bot skips a player who takes too long (see actions.start_player_countdown)
        self.auto_skip = auto_skip
        # Whether draws can make the volcano erupt (volcano.py)
        self.volcano = volcano
        # Whether Swap, Drop and +1 cards do something (storm.py)
        self.storm = storm
        # Whether a +4 can be challenged as a bluff (actions.do_call_bluff)
        self.bluff = bluff
        # Whether a pending +4 can be answered with another +4, or a +2 of its color, and
        # a Color Chooser can go on a Color Chooser (Player._card_playable)
        self.tornado = tornado
        # Whether the game info leaves out how many cards each player holds (results.game_info)
        self.hide_counts = hide_counts
        # Whether the deck has Diablos, whose color the bot picks at random (actions.do_play_card)
        self.diablo = diablo
        # Whether the deck has Xenos and Alphas, and number cards adding up to 5 go on each
        # other (Player._card_playable); a Xeno's color has to be played, or drawn until found
        # (Player.draw)
        self.xeno = xeno
        self.nitro = nitro
        # Whether the first player to win ends the game, however many are left (actions.player_won)
        self.first_win_ends = first_win_ends


CLASSIC = Mode('classic', N_("🎻 Classic mode"), N_('Classic 🎻'))
FAST = Mode('fast', N_("🚀 Sonic mode"), N_('Gotta go fast! 🚀'), auto_skip=True, first_win_ends=True)
WILD = Mode('wild', N_("🐉 Wild mode"), N_('Into the Wild~ 🐉'), deck='wild')
VOLCANO = Mode('volcano', N_("🌋 Volcano mode"), N_('Ka-Boom! 🌋'), volcano=True)
STORM = Mode('storm', N_("🌧️ Storm mode"), N_('Dance in the Rain 🌧️'), deck='storm', storm=True)
TORNADO = Mode('tornado', N_("🌪️ Tornado mode"), N_('Wind of Revolution 🌪️'), bluff=False, tornado=True)
CRAZY = Mode('crazy', N_("👻 Crazy mode"), N_('Why so Serious? 👻️'), deck='crazy', hand_size=8, hide_counts=True)
DIABLO = Mode('diablo', N_("💀 Diablo mode"), N_('Welcome to Hell 💀️'), deck='diablo', bluff=False, diablo=True)
XENO = Mode('xeno', N_("👽 Xeno mode"), N_('Project Xeno 👽️'), deck='xeno', xeno=True)
RAINBOW = Mode('rainbow', N_("🌈 Rainbow mode"), N_('Over the Rainbow 🌈'), deck='rainbow')
NITRO = Mode('nitro', N_("⚡ Nitro mode"), N_('Unleash the Nitro ⚡'), deck='nitro', bluff=False, nitro=True)
R8P = Mode('r8p', N_("🍆 R8P mode"), N_('Prepare your holes 🍆'), deck='storm',
           volcano=True, storm=True, tornado=True, bluff=False)

# In the order the inline menu offers them
MODES = {mode.id: mode for mode in (CLASSIC, FAST, WILD, VOLCANO, STORM, TORNADO, CRAZY, DIABLO, XENO, RAINBOW, NITRO, R8P)}

# Not a mode of its own: a lobby set to it plays one of MODES, picked at /start
# with the game's random source (Game.start), so results only ever name a real mode
RANDOM = Mode('random', N_("🎲 Random mode"), N_('Leave it to chance 🎲'))

# What the creator can pick in the inline menu, and the config's default_gamemode
CHOICES = {**MODES, RANDOM.id: RANDOM}


def get(mode_id):
    """The mode called `mode_id`; classic for a game without a known mode
    (or a random one that hasn't been picked yet)"""
    return MODES.get(mode_id, CLASSIC)
