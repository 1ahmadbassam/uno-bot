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
from config import ADMIN_LIST, OPEN_LOBBY, DEFAULT_GAMEMODE, ENABLE_TRANSLATIONS, SHUFFLE_PLAYERS
from datetime import datetime, timezone

from deck import Deck
from fairness import FairRandom, seat_label
import card as c
import rivalry_log
from errors import DeckEmptyError

class Game(object):
    """ This class represents a game of UNO """

    def __init__(self, chat):
        self.chat = chat
        self.last_card = None
        # Every random choice of the game; its commitment is announced with the lobby
        self.rng = FairRandom()
        self.deck = Deck(self.rng)
        self.current_player = None
        self.reversed = False
        self.choosing_color = False
        self.opening_color_choice = False
        self.started = False
        self.started_at = None
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
        self.started_at = None  # UTC
        self.turns = 0
        self.rivalry_log = None  # See rivalry_log.py
        # Users who left (or were removed from) this game after it started;
        # they can't rejoin it for a fresh hand
        self.left_users = set()

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

    def shuffle_players(self):
        """Randomly shuffles the seating ring and starting player with the game's random source"""
        players = self.players
        if len(players) <= 1:
            return

        self.reversed = False
        before = [seat_label(p) for p in players]
        self.rng.shuffle(players)
        self.rng.note('s', before, [seat_label(p) for p in players])
        n = len(players)
        for i in range(n):
            players[i]._next = players[(i + 1) % n]
            players[i]._prev = players[(i - 1) % n]
        self.current_player = players[0]

    def start(self):
        self.touch()
        if self.rng.source is None:
            # Not prepared by the /start flow (fairness off, tests): OS randomness
            self.rng.use_system()
        if SHUFFLE_PLAYERS:
            self.shuffle_players()

        if self.mode == None or self.mode != "wild":
            self.deck._fill_classic_()
        else:
            self.deck._fill_wild_()

        self._first_card_()
        self.started = True
        self.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self.turns = 0

    def abort_start(self):
        """Undo start() when dealing failed, so the lobby can still be started"""
        self.touch()
        for player in self.players:
            player.cards = list()
        # The used seed can't be reused: a restarted lobby gets a new commitment
        self.rng = FairRandom()
        self.deck = Deck(self.rng)
        self.last_card = None
        self.draw_counter = 0
        self.reversed = False
        self.choosing_color = False
        self.opening_color_choice = False
        self.started = False
        self.started_at = None

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
        self.turns += 1
        # Ensure that if turn ends while choosing a color, a color is chosen to prevent soft-lock
        if self.last_card and not self.last_card.color:
            self.last_card.color = self.rng.choose_color()
            rivalry_log.color(self, self.current_player, self.last_card.color, auto=True)
        self.current_player = self.current_player.next
        self.current_player.drew = False
        self.current_player.turn_started = datetime.now()
        rivalry_log.turn(self)
        self.choosing_color = False
        self.opening_color_choice = False

    def _first_card_(self):
        # In case that the player did not select a game mode
        if not self.deck.cards:
            self.set_mode(DEFAULT_GAMEMODE)

        # Official Mattel UNO rules:
        # Wild Draw 4 (+4) cannot be the opening card. If drawn, it is held temporarily
        # and returned to the draw pile (draws are random picks, so no reshuffle is needed).
        # Regular Wild card (c.CHOOSE) is permitted: the first player chooses the starting
        # color and takes their opening turn on that color.
        temp_draw_fours = []
        card = None
        while True:
            card = self.deck.draw()
            if card.special == c.DRAW_FOUR:
                temp_draw_fours.append(card)
            else:
                break

        self.deck.put_back(temp_draw_fours)

        self.last_card = card

        # Resolve first-card action rules according to official UNO rules
        if card.special == c.CHOOSE:
            self.choosing_color = True
            self.opening_color_choice = True
        elif card.value == c.SKIP:
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

    def choose_color(self, color, auto=False):
        """Carries out the color choosing and turns the game; `auto`: the bot picked it"""
        self.touch()
        rivalry_log.color(self, self.current_player, color, auto)
        self.last_card.color = color
        self.choosing_color = False
        if getattr(self, 'opening_color_choice', False):
            self.opening_color_choice = False
        else:
            self.turn()
