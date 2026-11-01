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

import card as c
from errors import DeckEmptyError
from fairness import seat_label
from config import WAITING_TIME


class Player(object):
    """
    This class represents a player.
    It is basically a doubly-linked ring list with the option to reverse the
    direction. On initialization, it will connect itself to a game and its
    other players by placing itself behind the current player.
    """

    def __init__(self, game, user):
        self.cards = list()
        self.game = game
        self.user = user
        self.logger = logging.getLogger(__name__)

        # Check if this player is the first player in this game.
        if game.current_player:
            self.next = game.current_player
            self.prev = game.current_player.prev
            game.current_player.prev.next = self
            game.current_player.prev = self
        else:
            self._next = self
            self._prev = self
            game.current_player = self

        self.bluffing = False
        self.drew = False
        # Whether the last draw actually produced a card (False if the deck was exhausted)
        self.drew_card = False
        self.anti_cheat = 0
        self.turn_started = datetime.now()
        self.waiting_time = WAITING_TIME
        self.pending_win = False

    def draw_first_hand(self):
        try:
            for _ in range(self.game.rules.hand_size):
                self.cards.append(self.game.deck.draw(seat_label(self)))
        except DeckEmptyError:
            for card in self.cards:
                self.game.deck.dismiss(card)
            # Empty the hand so a later leave() can't dismiss the same cards again
            self.cards = list()

            raise

    def leave(self):
        """Removes player from the game and closes the gap in the list"""
        if self._next is None or self._prev is None:
            return

        if self._next == self:
            if self.game.current_player == self:
                self.game.current_player = None
            self._next = None
            self._prev = None
        else:
            if self.game.current_player == self:
                self.game.current_player = self.next

            nxt = self.next
            prv = self.prev
            nxt.prev = prv
            prv.next = nxt
            self._next = None
            self._prev = None

        for card in self.cards:
            self.game.deck.dismiss(card)

        self.cards = list()

    def __repr__(self):
        return repr(self.user)

    def __str__(self):
        return str(self.user)

    @property
    def next(self):
        return self._next if not self.game.reversed else self._prev

    @next.setter
    def next(self, player):
        if not self.game.reversed:
            self._next = player
        else:
            self._prev = player

    @property
    def prev(self):
        return self._prev if not self.game.reversed else self._next

    @prev.setter
    def prev(self, player):
        if not self.game.reversed:
            self._prev = player
        else:
            self._next = player

    def draw(self):
        """Draws 1+ cards from the deck, depending on the draw counter"""
        _amount = self.game.draw_counter or 1
        drawn = 0
        # Xeno: on a Xeno, the player draws until they find its color
        last = self.game.last_card
        hunted = last.color if last is not None and last.special == c.XENO and not self.game.draw_counter else None

        try:
            for _ in range(_amount):
                self.cards.append(self.game.deck.draw(seat_label(self)))
                drawn += 1
            while hunted and self.cards[-1].color != hunted:
                self.cards.append(self.game.deck.draw(seat_label(self)))
                drawn += 1

        except DeckEmptyError:
            raise

        finally:
            self.game.draw_counter = 0
            # The turn still counts as drawn (so the player can pass), but only
            # a card that was actually drawn limits what may be played next
            self.drew = True
            self.drew_card = drawn > 0

    def play(self, card):
        """Plays a card and removes it from hand"""
        # Determine bluffing status at the exact moment a Wild Draw 4 is played
        # by checking the player's full hand against the active color.
        if c.is_draw_wild(card) and self.game.rules.bluff:
            active_color = self.game.last_card.color if self.game.last_card else None
            self.bluffing = any(hand_card.color == active_color for hand_card in self.cards if hand_card.color)
        else:
            self.bluffing = False

        self.cards.remove(card)
        if self.game.manual_uno and len(self.cards) == 1:
            self.game.hold_for_uno(self)
        self.game.play_card(card)

    def playable_cards(self):
        """Returns a list of the cards this player can play right now"""

        playable = list()

        cards = self.cards
        if self.drew and self.drew_card:
            # After drawing, only the drawn card (appended last) may be played
            cards = self.cards[-1:]

        for card in cards:
            if self._card_playable(card):
                self.logger.debug("Matching!")
                playable.append(card)

        # In standard UNO, players are permitted to play any card (including Wild cards)
        # as their final card to win.
        return playable

    def must_play(self):
        """Xeno: the card drawn to find a Xeno's color has to be played, so there's no passing"""
        last = self.game.last_card
        return bool(self.drew and self.drew_card and last is not None and last.special == c.XENO and
                    self.playable_cards())

    def can_pass(self):
        return self.drew and not self.must_play()

    def _card_playable(self, card):
        """Check a single card if it can be played"""

        is_playable = True
        last = self.game.last_card

        if not last.color:
            # Defensive: game.turn() always assigns a color, so this should not happen.
            # Accept any color rather than locking the game, but don't change game
            # state from what is only a playability check.
            self.logger.warning("Last card has no color")

        if last.special == c.XENO and (card.special or card.color != last.color):
            # Only its color goes on a Xeno: no other wild, nor the X-rule
            self.logger.debug("Card doesn't have the Xeno's color")
            is_playable = False
        elif last.special == c.CHOOSE and card.special == c.CHOOSE and not self.game.rules.tornado:
            self.logger.debug("A Color Chooser only goes on a Color Chooser in Tornado")
            is_playable = False
        elif (last.color and card.color != last.color and not card.special and
                # An Alpha has no value to match, so only goes on its color
                (card.value != last.value or card.value == c.ALPHA) and
                not (self.game.rules.xeno and c.sums_to_five(last, card))):
            self.logger.debug("Card's color or value doesn't match")
            is_playable = False
        elif last.value == c.DRAW_TWO and not \
                card.value == c.DRAW_TWO and self.game.draw_counter:
            self.logger.debug("Player has to draw and can't counter")
            is_playable = False
        elif c.is_draw_wild(last) and self.game.draw_counter and not (
                # Tornado: another +4, or a +2 of the chosen color (checked above)
                self.game.rules.tornado and (c.is_draw_wild(card) or card.value == c.DRAW_TWO)):
            self.logger.debug("Player has to draw and can't counter")
            is_playable = False

        return is_playable
