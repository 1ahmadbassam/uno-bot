import logging

import card as c
import rivalry
from datetime import datetime
from deck import csprng

from telegram import Message, Chat
from telegram.ext import CallbackContext
from apscheduler.jobstores.base import JobLookupError

from config import TIME_REMOVAL_AFTER_SKIP, MIN_FAST_TURN_TIME
from errors import DeckEmptyError, NotEnoughPlayersError
from game_threads import game_thread_runner
from internationalization import __, _, locales_for_game
from shared_vars import gm
from utils import send_async, send_paced, display_name, display_color_group, game_is_running, make_turn_keyboard

logger = logging.getLogger(__name__)

class Countdown(object):
    player = None
    job_queue = None

    def __init__(self, player, job_queue):
        self.player = player
        self.job_queue = job_queue


def do_skip(bot, player, job_queue=None):
    game = player.game
    chat = game.chat
    skipped_player = game.current_player

    # F-07: Guard against race condition: if an automated countdown timer fired for a
    # player who has already completed their turn, ignore the stale skip.
    if job_queue is not None and player != skipped_player:
        logger.info("Ignoring stale automated countdown skip for %s (current player is %s)",
                    display_name(player.user), display_name(skipped_player.user))
        return

    # A player who timed out while choosing a color already played their card;
    # any draw counter is the next player's penalty, not theirs.
    was_choosing = game.choosing_color or (game.last_card and not game.last_card.color)
    if was_choosing:
        auto_color = csprng.choice(c.COLORS)
        game.last_card.color = auto_color
        game.choosing_color = False
        game.opening_color_choice = False
        send_async(bot, chat.id,
                   text=__("Time ran out to choose a color! Color automatically set to {color}.",
                           multi=game.translate).format(color=display_color_group(auto_color, game)))

    # F-04: If there was a pending win on Wild Draw Four and challenger timed out, award win
    bluffer = getattr(game, 'last_bluffer', None)
    if bluffer and getattr(bluffer, 'pending_win', False):
        bluffer.pending_win = False
        game.last_bluffer = None
        player_won(bot, bluffer)
        if not game_is_running(game):
            return
        if skipped_player.waiting_time <= 0:
            # The challenger is removed below without drawing; the winner has left,
            # so nobody else can be challenged for that +4
            game.draw_counter = 0

    if skipped_player.waiting_time > 0:
        skipped_player.anti_cheat += 1
        skipped_player.waiting_time -= TIME_REMOVAL_AFTER_SKIP
        if (skipped_player.waiting_time < 0):
            skipped_player.waiting_time = 0

        draw_counter = game.draw_counter
        if was_choosing:
            game.draw_counter = 0
        try:
            skipped_player.draw()
        except DeckEmptyError:
            pass
        if was_choosing:
            game.draw_counter = draw_counter
        elif draw_counter and game.last_card.special == c.DRAW_FOUR:
            # The +4 penalty was taken, so it can't be challenged any more
            game.last_bluffer = None

        n = skipped_player.waiting_time
        game.turn()
        gm.persist_game(game)
        send_paced(bot, chat.id,
                   text=__("Waiting time to skip this player has "
                        "been reduced to {time} seconds.\n"
                        "Next player: {name}", multi=game.translate)
                   .format(time=n,
                           name=display_name(game.current_player.user)),
                   reply_markup=make_turn_keyboard(game, chat.id),
                   disable_notification=True
        )
        logger.info("{player} was skipped! "
                    .format(player=display_name(skipped_player.user)))
        if job_queue:
            start_player_countdown(bot, game, job_queue)

    else:
        try:
            gm.leave_game(skipped_player.user, chat)
            cancel_penalty_after_leave(bot, game, True, was_choosing)
            send_paced(bot, chat.id,
                       text=__("{name1} ran out of time "
                            "and has been removed from the game!\n"
                            "Next player: {name2}", multi=game.translate)
                       .format(name1=display_name(skipped_player.user),
                               name2=display_name(game.current_player.user)),
                       reply_markup=make_turn_keyboard(game, chat.id),
                       disable_notification=True)
            logger.info("{player} was skipped! "
                    .format(player=display_name(skipped_player.user)))
            gm.persist_game(game)
            if job_queue:
                start_player_countdown(bot, game, job_queue)

        except NotEnoughPlayersError:
            send_async(bot, chat.id,
                       text=__("{name} ran out of time "
                               "and has been removed from the game!\n"
                               "The game ended.", multi=game.translate)
                       .format(name=display_name(skipped_player.user)))

            forfeit_win(bot, game, skipped_player.user)
            gm.end_game(chat, skipped_player.user)


def player_won(bot, player):
    """Handles player winning and leaves or ends the game cleanly"""
    game = player.game
    chat = game.chat
    user = player.user

    send_async(bot, chat.id,
               text=__("{name} won!", multi=game.translate)
               .format(name=user.first_name))

    if game.players_won == 0:
        rivalry.record_first_place(bot, game, user)

    game.players_won += 1

    try:
        gm.leave_game(user, chat)
    except NotEnoughPlayersError:
        send_async(bot, chat.id,
                   text=__("Game ended!", multi=game.translate))

        gm.end_game(chat, user)


def forfeit_win(bot, game, leaving_user):
    """
    Credit the win in a 2-player game when the other player leaves, is kicked
    or runs out of time, so quitting can't deny the opponent their win.
    Call it before the game is ended (the leaving player is still in the game).
    """
    if not game.started:
        return
    survivor = next((p for p in game.players if p.user.id != leaving_user.id), None)
    if survivor is None:
        return

    send_async(bot, game.chat.id,
               text=__("{name} won!", multi=game.translate)
               .format(name=survivor.user.first_name))

    if game.players_won == 0:
        rivalry.record_first_place(bot, game, survivor.user, forfeit=True)
    game.players_won += 1


def end_game_without_winner(bot, game):
    """
    End a 2-player game without crediting a win, e.g. when one player kicks the other.
    Call it before the game is ended (both players are still in the game).
    """
    if not game.started:
        return
    send_async(bot, game.chat.id,
               text=__("The game ended without a winner.", multi=game.translate))


def cancel_penalty_after_leave(bot, game, was_current, was_choosing):
    """
    Cancel a +2/+4 penalty when the player it targeted leaves or is removed
    before drawing, so it doesn't pass on to the next player.
    A player who left while choosing a color had just played the card
    themselves: the penalty is then the next player's and stays.
    Call it after settle_draw_four_after_leave.
    """
    if not was_current or was_choosing or game.draw_counter <= 0:
        return
    game.draw_counter = 0
    game.last_bluffer = None
    send_async(bot, game.chat.id,
               text=__("The player facing the draw penalty left the game, so it was cancelled.",
                       multi=game.translate))
    gm.persist_game(game)


def settle_draw_four_after_leave(bot, game, left_player, was_current):
    """
    Settle a Wild Draw Four that is still unanswered when a player leaves.
    - If the player who played it left, it can no longer be challenged, so its
      penalty is cancelled instead of judging (or penalizing) anyone else.
    - If it was their final card and the challenger (the current player) left,
      the win is awarded.
    Returns True if the game ended.
    """
    bluffer = getattr(game, 'last_bluffer', None)
    if not bluffer:
        return False

    if left_player is bluffer:
        unanswered = (game.last_card and game.last_card.special == c.DRAW_FOUR
                      and game.draw_counter > 0)
        bluffer.pending_win = False
        game.last_bluffer = None
        if unanswered:
            game.draw_counter = 0
            send_async(bot, game.chat.id,
                       text=__("The player who played the +4 left the game, so its penalty is cancelled.",
                               multi=game.translate))
        gm.persist_game(game)
        return False

    if not was_current or not getattr(bluffer, 'pending_win', False):
        return False

    bluffer.pending_win = False
    game.last_bluffer = None
    # The penalty belonged to the player who left; nobody else can challenge it
    game.draw_counter = 0
    player_won(bot, bluffer)
    if not game_is_running(game):
        return True
    gm.persist_game(game)
    return False


def do_play_card(bot, player, result_id, notify_color=True, notify_uno=True):
    """Plays the selected card and sends an update to the group if needed"""
    card = c.from_str(result_id)
    player.play(card)
    game = player.game
    chat = game.chat

    if card.special == c.DRAW_FOUR:
        game.last_bluffer = player

    if len(player.cards) == 1 and notify_uno:
        send_async(bot, chat.id, text="UNO!", disable_notification=True)

    if len(player.cards) == 0:
        # If the winning card was a Wild card, automatically assign a color
        # so remaining players can continue without deadlock
        if game.choosing_color:
            auto_color = csprng.choice(c.COLORS)
            game.choose_color(auto_color)
            send_async(bot, chat.id,
                       text=__("Winning card was Wild! Color set to {color}.",
                               multi=game.translate)
                       .format(color=display_color_group(auto_color, game)),
                       disable_notification=True)

        if card.special == c.DRAW_FOUR and game.draw_counter > 0:
            # F-04: Win is deferred until challenge resolution or draw/skip by next player
            player.pending_win = True
            game.last_bluffer = player
        else:
            player_won(bot, player)
    else:
        if game.choosing_color and notify_color:
            send_async(bot, chat.id, text=__("Please choose a color", multi=game.translate), disable_notification=True)

    if game in gm.chatid_games.get(chat.id, []):
        gm.persist_game(game)


def do_draw(bot, player):
    """Does the drawing"""
    game = player.game
    draw_counter_before = game.draw_counter

    try:
        player.draw()
    except DeckEmptyError:
        send_async(bot, player.game.chat.id,
                   text=__("There are no more cards in the deck.",
                           multi=game.translate))

    if (game.last_card.value == c.DRAW_TWO or
        game.last_card.special == c.DRAW_FOUR) and \
            draw_counter_before > 0:
        game.turn()

    # F-04: If there was a pending win on Wild Draw Four and challenger drew cards, award win
    bluffer = getattr(game, 'last_bluffer', None)
    if bluffer and getattr(bluffer, 'pending_win', False):
        bluffer.pending_win = False
        game.last_bluffer = None
        player_won(bot, bluffer)
    elif game.last_card.special == c.DRAW_FOUR and draw_counter_before > 0:
        # The +4 penalty was taken, so it can't be challenged any more
        game.last_bluffer = None

    gm.persist_game(game)


def do_call_bluff(bot, player):
    """Handles the bluff calling"""
    game = player.game
    chat = game.chat
    bluffer = getattr(game, 'last_bluffer', None)
    game.last_bluffer = None

    if bluffer is None or bluffer not in game.players:
        # Only the player who played the +4 can be judged; never fall back to
        # whoever sits before the challenger now
        game.draw_counter = 0
        send_async(bot, chat.id,
                   text=__("The player who played the +4 left the game, so its penalty is cancelled.",
                           multi=game.translate))
        gm.persist_game(game)
        return

    was_bluffing = bluffer.bluffing
    # Reset bluffing state so it never persists across subsequent turns
    bluffer.bluffing = False

    if was_bluffing:
        if getattr(bluffer, 'pending_win', False):
            bluffer.pending_win = False

        send_async(bot, chat.id,
                   text=__("Bluff called! Giving 4 cards to {name}",
                           multi=game.translate)
                   .format(name=bluffer.user.first_name))

        try:
            bluffer.draw()
        except DeckEmptyError:
            send_async(bot, player.game.chat.id,
                       text=__("There are no more cards in the deck.",
                               multi=game.translate))
        # F-03: Challenger retains their turn on successful challenge!
        game.draw_counter = 0

    else:
        game.draw_counter += 2
        send_async(bot, chat.id,
                   text=__("{name1} didn't bluff! Giving 6 cards to {name2}",
                           multi=game.translate)
                   .format(name1=bluffer.user.first_name,
                           name2=player.user.first_name))
        try:
            player.draw()
        except DeckEmptyError:
            send_async(bot, player.game.chat.id,
                       text=__("There are no more cards in the deck.",
                               multi=game.translate))

        game.turn()
        # F-04: If bluffer was innocent and had pending win, award the win now!
        if getattr(bluffer, 'pending_win', False):
            bluffer.pending_win = False
            player_won(bot, bluffer)

    gm.persist_game(game)


def start_player_countdown(bot, game, job_queue):
    player = game.current_player
    time = player.waiting_time

    if time < MIN_FAST_TURN_TIME:
        time = MIN_FAST_TURN_TIME

    if game.mode == 'fast':
        if game.job:
            try:
                game.job.schedule_removal()
            except JobLookupError:
                pass

        job = job_queue.run_once(
            #lambda x,y: do_skip(bot, player),
            skip_job,
            time,
            context=Countdown(player, job_queue)
        )

        logger.info("Started countdown for player: {player}. {time} seconds."
                    .format(player=display_name(player.user), time=time))
        player.game.job = job


def skip_job(context: CallbackContext):
    player = context.job.context.player
    game = player.game
    if game_is_running(game):
        if player != game.current_player:
            logger.info("Skipping stale timer job before submit: %s is no longer current player (%s)",
                        display_name(player.user), display_name(game.current_player.user))
            return
        job_queue = context.job.context.job_queue
        game_thread_runner.submit(
            f"chat:{game.chat.id}",
            _timed_skip,
            context.bot,
            player,
            job_queue
        )


def _timed_skip(bot, player, job_queue):
    """Run a countdown skip on the game worker with the game's locales active"""
    with locales_for_game(player.game):
        do_skip(bot, player, job_queue)
