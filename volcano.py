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
import r8p
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
R8P_STICKER = 'CAACAgQAAxUAAWrsbN1g_jpuqA2TZk-bW3ig8BHFAAJnIQACLBdgU9vwqLsF5TpdPQQ'


def odds(rolls):
    """The roll after `rolls` earlier ones erupts with a chance of 1 in odds(rolls)"""
    return BASE_ODDS + rolls


def roll(game, player, chosen=False):
    """
    After `player` drew on their turn: roll for an eruption in a volcano game.
    `chosen`: a draw by choice, whose one card stays the card they may play.
    Returns the cards the eruption gave (none when the volcano stays quiet).
    """
    combined = game.mode == 'r8p'
    if not game.rules.volcano or (not combined and game.volcano_eruptions >= MAX_ERUPTIONS):
        return []
    if combined:
        hit = r8p.roll(game)
    else:
        n = odds(game.volcano_rolls)
        hit = game.rng.randbelow(n) == 0
        game.rng.note('v', n, hit)
    game.volcano_rolls += 1
    if not hit:
        return []
    # Counts even when the deck is out of cards: the proof has the roll as an eruption,
    # and the checker counts those toward the limit
    game.volcano_eruptions += 1

    cards = []
    for _ in range(CARDS):
        try:
            cards.append(game.deck.draw(seat_label(player)))
        except DeckEmptyError:
            break
    if not cards:
        return []
    # playable_cards() lets a player who drew by choice play only the last card of their hand
    at = len(player.cards) - 1 if chosen and player.drew_card and player.cards else len(player.cards)
    player.cards[at:at] = cards
    rivalry_log.erupted(game, player, cards, game.volcano_eruptions)
    return cards


def send_sticker(bot, chat_id, eruption, mode='volcano'):
    """R8P's unnumbered sticker, or Volcano's numbered eruption sticker."""
    if not eruption:
        return
    # Here, so the statistics can use odds() without the bot's modules
    from shared_vars import dispatcher
    from utils import TIMEOUT
    try:
        sticker = R8P_STICKER if mode == 'r8p' else STICKERS[min(eruption, len(STICKERS)) - 1]
        dispatcher.run_async(bot.sendSticker, chat_id, sticker=sticker,
                             timeout=TIMEOUT, disable_notification=True)
    except Exception:
        logger.exception("Failed to schedule the volcano sticker")


def erupt(bot, game, player, chosen=False):
    """roll() and, on an eruption that gave cards, its sticker"""
    cards = roll(game, player, chosen)
    if cards:
        send_sticker(bot, game.chat.id, game.volcano_eruptions, mode=game.mode)
    return cards
