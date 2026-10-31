#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# The Storm mode: a deck without 0s and with three colored cards of its own.
#   +1    each opponent draws a card at once (it doesn't stack with +2 or +4)
#   Swap  you exchange your hand with the next player's
#   Drop  your hand goes back into the deck and you draw as many new cards
# Before playing, the player whose turn it is can /cancel the effect of their
# next Swap or Drop (/cancel again turns it back on). It applies to the next
# card they play, whatever it is, and ends with their turn.
#
# The effects show no message: the played card's sticker says what happened.
# Drop's cards go back as a "b" op and come out as "d" ops (fairness.py), so
# the checker replays them; a Swap moves no card through the deck.

import card as c
import rivalry_log
from errors import DeckEmptyError
from fairness import seat_label


def is_storm_card(card):
    return card is not None and card.value in c.STORM_VALUES


def can_cancel(card):
    """Whether /cancel stops this card's effect"""
    return card is not None and card.value in (c.SWAP, c.DROP)


def toggle_cancel(game):
    """/cancel by the current player: the new state, True when the next Swap or Drop does nothing"""
    game.storm_cancel = not game.storm_cancel
    return game.storm_cancel


def apply(game, player, card, cancelled=False):
    """
    The effect of `card`, which `player` just played (the turn has already passed).
    `cancelled`: the player used /cancel before playing it. A card that won the
    game does nothing. Returns the player a Swap exchanged hands with, if any.
    """
    if not game.rules.storm or not is_storm_card(card) or not player.cards:
        return None
    if card.value == c.PLUS_ONE:
        _plus_one(game, player)
    elif cancelled:
        return None
    elif card.value == c.SWAP:
        return _swap(game, player)
    elif card.value == c.DROP:
        _drop(game, player)
    return None


def _plus_one(game, player):
    for other in game.players:
        if other is player:
            continue
        before = len(other.cards)
        try:
            other.cards.append(game.deck.draw(seat_label(other)))
        except DeckEmptyError:
            return
        rivalry_log.drew(game, other, before, 'plus1', src=rivalry_log.key(player))


def _swap(game, player):
    other = player.next
    if other is None or other is player:
        return None
    player.cards, other.cards = other.cards, player.cards
    rivalry_log.swapped(game, player, other)
    return other


def _drop(game, player):
    returned = list(player.cards)
    player.cards = []
    game.deck.put_back(returned)
    for _ in returned:
        try:
            player.cards.append(game.deck.draw(seat_label(player)))
        except DeckEmptyError:
            break
    rivalry_log.dropped(game, player, returned)
