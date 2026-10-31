#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Turn-by-turn log of games between the two rivals, for the website's
# statistics and replays.
#
# The log lives on the game (game.rivalry_log) and is saved with the rest of
# the game state, so it survives restarts without writing to the database on
# every turn. rivalry.record_first_place stores it next to the result.
# Only the rivals' own actions are logged; other players stay out of it.
#
# Event keys: k kind, t ms since the start, n turn number, p rival ("a"/"b"),
# c card, cs cards, h hand size after, th thinking time in ms. Kinds:
#   deal   hands (h: {"a": [...], "b": [...]}), first card (fc), first rival to act (first)
#   play   c, bl: 1 for a +4 played while holding the active color
#   draw   cs, r: "vol" chosen, "pen" a +2/+4, "bluff" caught bluffing, "chal" lost a
#          challenge, "time" ran out of time; src: rival whose card forced it; ok: 1 when
#          a chosen draw could be played
#   pass
#   color  co, au: 1 picked by the bot, sm: 1 the color held most
#   chal   challenger p, vs: the rival challenged, ok: 1 when the bluff was caught
#   time   p ran out of time
#   end    winner p, o: "win" or "forfeit"

import logging
import time
from datetime import datetime, timezone
from functools import wraps

import card as c
from config import RIVALRY_ENABLED, RIVALRY_PLAYERS, RIVALRY_CHAT_ID

logger = logging.getLogger(__name__)

VERSION = 1
KEYS = {pid: key for (pid, _), key in zip(RIVALRY_PLAYERS, ('a', 'b'))}
ENABLED = RIVALRY_ENABLED and len(KEYS) == 2 and 0 not in KEYS


def _never_breaks_the_game(function):
    """The game code calls these on every move: a failure drops the game's log instead of the move"""
    @wraps(function)
    def guarded(game, *args, **kwargs):
        try:
            return function(game, *args, **kwargs)
        except Exception:
            logger.exception("Stopped the rivalry log of chat %s", getattr(game.chat, 'id', None))
            game.rivalry_log = None
            return None
    return guarded


def _now_ms():
    return int(time.time() * 1000)


def key(player):
    return KEYS.get(player.user.id) if player is not None else None


def _log(game):
    return getattr(game, 'rivalry_log', None)


def _thinking(log, who, now):
    """ms since the turn started, or since this rival's previous move in the same turn"""
    return max(0, now - max(log.get('ta') or log['t0'], log['last'].get(who) or 0))


@_never_breaks_the_game
def think_time(game, player):
    """The thinking time of a move `player` makes now; take it before the move can end the turn"""
    log = _log(game)
    if log is None or key(player) is None:
        return None
    return _thinking(log, key(player), _now_ms())


def _add(game, player, kind, decision=False, think=None, **fields):
    """Append an event for `player` if it is a rival; a decision gets its thinking time"""
    log = _log(game)
    if log is None:
        return None
    who = key(player)
    if who is None:
        return None
    now = _now_ms()
    event = {'k': kind, 't': now - log['t0'], 'n': getattr(game, 'turns', 0), 'p': who}
    if decision:
        event['th'] = think if think is not None else _thinking(log, who, now)
        log['last'][who] = now
    event.update((k, v) for k, v in fields.items() if v is not None)
    log['ev'].append(event)
    return event


@_never_breaks_the_game
def start(game):
    """Begin the log once the hands are dealt, when both rivals play in a chat that counts"""
    game.rivalry_log = None
    if not ENABLED or (RIVALRY_CHAT_ID is not None and game.chat.id != RIVALRY_CHAT_ID):
        return
    rivals = {key(p): p for p in game.players if key(p)}
    if len(rivals) != 2:
        return
    now = _now_ms()
    game.rivalry_log = {
        'v': VERSION,
        'at': datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds'),
        't0': now, 'ta': now, 'last': {}, 'by': None,
        'ev': [{
            'k': 'deal', 't': 0, 'n': 0,
            'h': {rival: [str(card) for card in p.cards] for rival, p in rivals.items()},
            'fc': str(game.last_card) if game.last_card else None,
            'first': key(game.current_player),
        }],
    }


@_never_breaks_the_game
def turn(game):
    """The turn passed to game.current_player"""
    log = _log(game)
    if log is not None:
        log['ta'] = _now_ms()


@_never_breaks_the_game
def played(game, player, card, think=None):
    """`player` played `card`; `think`: think_time() taken before the card ended the turn"""
    log = _log(game)
    if log is None:
        return
    bluff = 1 if c.is_draw_wild(card) and getattr(player, 'bluffing', False) else None
    _add(game, player, 'play', True, think, c=str(card), h=len(player.cards), bl=bluff)
    if card.value == c.DRAW_TWO or c.is_draw_wild(card):
        log['by'] = key(player)


@_never_breaks_the_game
def drew(game, player, before, reason, src=None):
    """`player` drew the cards after the first `before` of their hand"""
    log = _log(game)
    if log is None:
        return
    new = player.cards[before:]
    if reason == 'pen' or (reason == 'time' and len(new) > 1):
        src = log.get('by')
    ok = None
    if reason == 'vol' and new and game.current_player is player and new[-1] in player.playable_cards():
        ok = 1
    _add(game, player, 'draw', reason in ('vol', 'pen'), cs=[str(card) for card in new],
         h=len(player.cards), r=reason, src=src if src != key(player) else None, ok=ok)


@_never_breaks_the_game
def passed(game, player):
    _add(game, player, 'pass', True, h=len(player.cards))


@_never_breaks_the_game
def color(game, player, chosen, auto=False):
    if _log(game) is None or player is None:
        return
    held = {col: sum(1 for card in player.cards if card.color == col) for col in game.colors}
    smart = 1 if max(held.values()) and held.get(chosen) == max(held.values()) else None
    _add(game, player, 'color', not auto, co=chosen, au=1 if auto else None, sm=smart)


@_never_breaks_the_game
def challenge(game, challenger, bluffer, caught):
    _add(game, challenger, 'chal', True, vs=key(bluffer), ok=1 if caught else None)


@_never_breaks_the_game
def timed_out(game, player):
    _add(game, player, 'time')


@_never_breaks_the_game
def finish(game, winner_id, outcome):
    """Close the log for the recorded result and take it off the game; None when there is none"""
    log = _log(game)
    game.rivalry_log = None
    if log is None:
        return None
    log['ev'].append({'k': 'end', 't': _now_ms() - log['t0'], 'n': getattr(game, 'turns', 0),
                      'p': KEYS.get(winner_id), 'o': outcome})
    return {'v': log['v'], 'at': log['at'], 'ev': log['ev']}
