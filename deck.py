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


import json
import os
import secrets
import logging

import card as c
from card import Card
from errors import DeckEmptyError

# Cryptographically secure RNG backed by the OS entropy pool (getrandom /
# BCryptGenRandom). randrange() uses rejection sampling, so picks have no modulo bias.
csprng = secrets.SystemRandom()

DECKS_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'decks.js')


def load_decks(path=DECKS_JS):
    """The decks of web/decks.js, which the fairness checker reads too: {name: spec}"""
    with open(path, encoding='utf-8') as f:
        text = f.read()
    # The declaration starts a line; the comment above it mentions it too
    declaration = '\nvar DECKS ='
    start = text.index(declaration) + len(declaration)
    return json.loads(text[start:text.rindex(';')])


DECKS = load_decks()


class SystemSource(object):
    """Random source for a deck outside a game; games use fairness.FairRandom"""

    def randbelow(self, n):
        return csprng.randrange(n) if n > 1 else 0

    def note(self, kind, *values):
        pass


class Deck(object):
    """
    This class represents a deck of cards.

    The draw pile is an unordered pool: every draw picks a card uniformly at
    random from the cards that are left, at the moment of drawing. The odds are
    the same as for a shuffled deck, but no draw order is stored anywhere.
    The pick is an index into the pile sorted by card name, so it only depends
    on which cards are left (fairness.py replays it to check a game).
    """

    def __init__(self, rng=None):
        self.cards = list()
        self.graveyard = list()
        self.rng = rng or SystemSource()
        self.logger = logging.getLogger(__name__)

        self.logger.debug("Initialized empty Deck")

    def draw(self, seat=None):
        """Draws a uniformly random card from the remaining draw pile; `seat`: who gets it, for the fairness log"""
        if not self.cards:
            if not self.graveyard:
                raise DeckEmptyError()
            # Recycle the discard pile; no shuffle needed since draws are random picks
            self.cards.extend(self.graveyard)
            self.graveyard.clear()

        self.cards.sort(key=str)
        card = self.cards.pop(self.rng.randbelow(len(self.cards)))
        self.rng.note('d', str(card), seat)
        return card

    def dismiss(self, card):
        """Returns a card to the deck"""
        if card.special:
            card.color = None
        self.graveyard.append(card)
        self.rng.note('x', str(card))

    def put_back(self, cards):
        """Returns cards straight to the draw pile (e.g. a Wild Draw Four turned up as the first card)"""
        self.cards.extend(cards)
        if cards:
            self.rng.note('b', [str(card) for card in cards])

    def fill(self, name):
        """Fill the deck with the cards of deck `name` from web/decks.js"""
        self.rng.note('f', name)
        self.cards.clear()
        spec = DECKS[name]
        for color in spec['colors']:
            for group in spec['values']:
                for value in group['values']:
                    for _ in range(group['copies']):
                        self.cards.append(Card(color, value))
        for special, copies in spec['specials'].items():
            for _ in range(copies):
                self.cards.append(Card(None, None, special=special))
