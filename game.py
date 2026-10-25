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
import secrets
from config import ADMIN_LIST, OPEN_LOBBY, DEFAULT_GAMEMODE, ENABLE_TRANSLATIONS
from datetime import datetime

from deck import Deck
import card as c
from errors import DeckEmptyError

class Game(object):
    """ This class represents a game of UNO """

    def __init__(self, chat):
        self.chat = chat
        self.last_card = None
        self.deck = Deck()
        self.current_player = None
        self.reversed = False
        self.choosing_color = False
        self.started = False
        self.draw_counter = 0
        self.players_won = 0
        self.starter = None
        self.mode = DEFAULT_GAMEMODE
        self.job = None
        self.owner = set(ADMIN_LIST or [])
        self.open = OPEN_LOBBY
        self.translate = ENABLE_TRANSLATIONS
        self.last_activity = datetime.now()
        self.last_bluffer = None

        self.logger = logging.getLogger(__name__)

    def touch(self):
        self.last_activity = datetime.now()

    @property
    def players(self):
        """Returns a list of all players in this game"""
        players = list()
        if not self.current_player:
            return players

        current_player = self.current_player
        players.append(current_player)
        visited = {id(current_player)}
        itplayer = current_player.next
        while itplayer and itplayer != current_player:
            if id(itplayer) in visited:
                break
            visited.add(id(itplayer))
            players.append(itplayer)
            itplayer = itplayer.next
        return players

    def start(self):
        self.touch()
        if self.mode == None or self.mode != "wild":
            self.deck._fill_classic_()
        else:
            self.deck._fill_wild_()

        self._first_card_()
        self.started = True

    def set_mode(self, mode):
        self.touch()
        self.mode = mode

    def reverse(self):
        """Reverses the direction of game"""
        self.touch()
        self.reversed = not self.reversed

    def turn(self):
        """Marks the turn as over and change the current player"""
        self.touch()
        self.logger.debug("Next Player")
        # Ensure that if turn ends while choosing a color, a color is chosen to prevent soft-lock
        if self.last_card and not self.last_card.color:
            self.last_card.color = secrets.choice(c.COLORS)
        self.current_player = self.current_player.next
        self.current_player.drew = False
        self.current_player.turn_started = datetime.now()
        self.choosing_color = False

    def _first_card_(self):
        # In case that the player did not select a game mode
        if not self.deck.cards:
            self.set_mode(DEFAULT_GAMEMODE)

        # The first card should not be a special card (Wild or Wild Draw Four).
        # Any drawn specials are held temporarily, then returned to the deck and shuffled.
        temp_specials = []
        card = None
        while True:
            card = self.deck.draw()
            if card.special:
                temp_specials.append(card)
            else:
                break

        if temp_specials:
            self.deck.cards.extend(temp_specials)
            self.deck.shuffle()

        self.last_card = card

        # Resolve first-card action rules according to official UNO rules
        # without dismissing the starting card or skipping the first player on number cards
        if card.value == c.SKIP:
            self.turn()
        elif card.value == c.DRAW_TWO:
            # Set draw counter so the first player can counter by playing another +2,
            # or draw 2 cards and be skipped if they cannot counter
            self.draw_counter = 2
        elif card.value == c.REVERSE:
            if self.current_player == self.current_player.next.next:
                self.turn()
            else:
                self.reverse()

    def play_card(self, card):
        """
        Plays a card and triggers its effects.
        Should be called only from Player.play or on game start to play the
        first card
        """
        self.touch()
        self.deck.dismiss(self.last_card)
        self.last_card = card

        self.logger.info("Playing card " + repr(card))
        if card.value == c.SKIP:
            self.turn()
        elif card.special == c.DRAW_FOUR:
            self.draw_counter += 4
            self.logger.debug("Draw counter increased by 4")
        elif card.value == c.DRAW_TWO:
            self.draw_counter += 2
            self.logger.debug("Draw counter increased by 2")
        elif card.value == c.REVERSE:
            # Special rule for two players
            if self.current_player == self.current_player.next.next:
                self.turn()
            else:
                self.reverse()

        # Don't turn if the current player has to choose a color
        if card.special not in (c.CHOOSE, c.DRAW_FOUR):
            self.turn()
        else:
            self.logger.debug("Choosing Color...")
            self.choosing_color = True

    def choose_color(self, color):
        """Carries out the color choosing and turns the game"""
        self.touch()
        self.last_card.color = color
        self.turn()
