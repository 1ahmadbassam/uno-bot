#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# The Volcano mode: each time a player draws on their turn, the volcano may
# erupt and give them CARDS more. The k-th roll of the game (from 0) erupts
# with a chance of 1 in (BASE_ODDS + k), so the chance keeps decreasing, and
# the volcano goes quiet after MAX_ERUPTIONS eruptions.
#
# Rolls use the game's fair random source and are logged as ["v", odds, hit]
# ops (fairness.py), so web/fair.js can check every roll and the 1/(10+k) odds.

import logging

import rivalry_log
from errors import DeckEmptyError
from fairness import seat_label

BASE_ODDS = 10
CARDS = 3
MAX_ERUPTIONS = 5

# The sticker of the 1st to 5th eruption of a game (GamesArena pack, positions 26-30)
STICKERS = (
    'CAACAgQAAxUAAWrlyoNZo7Jj9BxAxaDMHNrD4gZ5AAKZEwACtlRpUUk-XqV3_LxSPQQ',
    'CAACAgQAAxUAAWrlyoPZA42e6MWhzo4_1TFkHfohAAIREwACsoxpUUnNPs9a49cuPQQ',
    'CAACAgQAAxUAAWrlyoM-0aJIRy9zbSnRiowD7zvxAAI9EgACiPloUVHNL0l8QjqlPQQ',
    'CAACAgQAAxUAAWrlyoNzmEbJIdQJ2JuyKcObA-dcAAIWEgAC2kNoUfBrxB59LLGvPQQ',
    'CAACAgQAAxUAAWrlyoOObNtu6qx2-49w87ydEi0bAAJWEwAC63doURDvegW6YZsBPQQ',
)

logger = logging.getLogger(__name__)


def odds(rolls):
    """The roll after `rolls` earlier ones erupts with a chance of 1 in odds(rolls)"""
    return BASE_ODDS + rolls


def roll(game, player):
    """
    After `player` drew on their turn: roll for an eruption in a volcano game.
    Returns the eruption's number (1 to MAX_ERUPTIONS), or 0 when the volcano stays quiet.
    """
    if not game.rules.volcano or game.volcano_eruptions >= MAX_ERUPTIONS:
        return 0
    n = odds(game.volcano_rolls)
    hit = game.rng.randbelow(n) == 0
    game.rng.note('v', n, hit)
    game.volcano_rolls += 1
    if not hit:
        return 0
    game.volcano_eruptions += 1

    # Ahead of a card the player just drew, which stays the one they may play
    at = len(player.cards) - 1 if player.drew_card and player.cards else len(player.cards)
    cards = []
    for _ in range(CARDS):
        try:
            cards.append(game.deck.draw(seat_label(player)))
        except DeckEmptyError:
            break
    player.cards[at:at] = cards
    rivalry_log.erupted(game, player, cards, game.volcano_eruptions)
    return game.volcano_eruptions


def send_sticker(bot, chat_id, eruption):
    """The sticker of the game's `eruption`-th eruption"""
    if not eruption:
        return
    # Here, so the statistics can use odds() without the bot's modules
    from shared_vars import dispatcher
    from utils import TIMEOUT
    try:
        dispatcher.run_async(bot.sendSticker, chat_id, sticker=STICKERS[eruption - 1],
                             timeout=TIMEOUT, disable_notification=True)
    except Exception:
        logger.exception("Failed to schedule the volcano sticker")


def erupt(bot, game, player):
    """roll() and, on an eruption, its sticker"""
    eruption = roll(game, player)
    send_sticker(bot, game.chat.id, eruption)
    return eruption
