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


import secrets
import logging

import card as c
from card import Card
from errors import DeckEmptyError

# Cryptographically secure PRNG singleton backed by OS-level entropy
csprng = secrets.SystemRandom()


def secure_shuffle(cards):
    """
    Cryptographically secure, unbiased Fisher-Yates shuffle.
    Uses OS-level CSPRNG entropy via secrets.SystemRandom() with rejection sampling
    (randbelow) to eliminate modulo bias.
    Executes multiple independent Fisher-Yates passes to guarantee uniform card
    distribution such that every permutation has equal probability (1 / N!).
    """
    if len(cards) <= 1:
        return
    for _ in range(3):
        csprng.shuffle(cards)


class Deck(object):
    """ This class represents a deck of cards """

    def __init__(self):
        self.cards = list()
        self.graveyard = list()
        self.logger = logging.getLogger(__name__)

        self.logger.debug("Initialized empty Deck")

    def shuffle(self):
        """Shuffles the deck using cryptographically secure randomness"""
        self.logger.debug("Shuffling Deck with cryptographic CSPRNG")
        secure_shuffle(self.cards)

    def draw(self):
        """Draws a card from this deck"""
        try:
            card = self.cards.pop()
            self.logger.debug("Drawing card %s", str(card))
            return card
        except IndexError:
            if len(self.graveyard):
                self.cards.extend(self.graveyard)
                self.graveyard.clear()
                self.shuffle()
                return self.draw()
            else:
                raise DeckEmptyError()

    def dismiss(self, card):
        """Returns a card to the deck"""
        if card.special:
            card.color = None
        self.graveyard.append(card)

    def _fill_classic_(self):
        # Fill deck with the classic card set
        self.cards.clear()
        for color in c.COLORS:
            for value in c.VALUES:
                self.cards.append(Card(color, value))
                if not value == c.ZERO:
                    self.cards.append(Card(color, value))
        for special in c.SPECIALS:
            for _ in range(4):
                self.cards.append(Card(None, None, special=special))
        self.shuffle()

    def _fill_wild_(self):
        # Fill deck with a wild card set
        self.cards.clear()
        for color in c.COLORS:
            for value in c.WILD_VALUES:
                for _ in range(4):
                    self.cards.append(Card(color, value))
        for special in c.SPECIALS:
            for _ in range(6):
                self.cards.append(Card(None, None, special=special))
        self.shuffle()
