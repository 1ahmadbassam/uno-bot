#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Head-to-head score between the two players configured under "rivalry":
# records who came first whenever they play each other, posts and pins the
# score, serves /score and writes static exports for a website.

import logging
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pony import orm
from telegram import ParseMode, Update
from telegram.ext import CommandHandler, CallbackContext

import rivalry_stats as rs
from config import (ADMIN_LIST, RIVALRY_ENABLED, RIVALRY_PLAYERS, RIVALRY_CHAT_ID, RIVALRY_TIMEZONE,
                    RIVALRY_EXPORT_DIR, RIVALRY_PIN)
from rivalry_models import RivalryMatch, RivalrySeason
from shared_vars import dispatcher
from utils import send_async

logger = logging.getLogger(__name__)

RIVAL_IDS = {pid for pid, _ in RIVALRY_PLAYERS}
ENABLED = RIVALRY_ENABLED and len(RIVALRY_PLAYERS) == 2 and len(RIVAL_IDS) == 2 and 0 not in RIVAL_IDS
# Let the game's own "won" and "Game ended" messages arrive before the score
ANNOUNCE_DELAY = 1.0
HISTORY_DEFAULT, HISTORY_MAX = 10, 50
INDEX_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'index.html')

_export_lock = threading.Lock()


def _timezone():
    try:
        return ZoneInfo(RIVALRY_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Unknown rivalry timezone %r, using UTC", RIVALRY_TIMEZONE)
        return timezone.utc


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _rivals():
    return rs.Rivals(RIVALRY_PLAYERS)


def match_for(game, winner_id, forfeit=False):
    """
    (loser id, outcome, cards left in the loser's hand) when the first place
    of `game` counts for the rivalry, else None.
    It counts when both rivals took part, whatever the number of players.
    A rival who left the game before the other won it lost by forfeit.
    """
    if not ENABLED or not game.started or winner_id not in RIVAL_IDS:
        return None
    if RIVALRY_CHAT_ID is not None and game.chat.id != RIVALRY_CHAT_ID:
        return None
    loser_id = next(pid for pid in RIVAL_IDS if pid != winner_id)
    loser = next((p for p in game.players if p.user.id == loser_id), None)
    if loser is not None:
        if forfeit:
            return loser_id, 'forfeit', None
        return loser_id, 'win', len(loser.cards)
    if loser_id in getattr(game, 'left_users', ()):
        return loser_id, 'forfeit', None
    return None


def record_first_place(bot, game, winner_user, forfeit=False):
    """Record the first place of `game` if it counts for the rivalry, then post the score.
    Call it while the winner (and a forfeiting loser) is still in the game."""
    found = match_for(game, winner_user.id, forfeit)
    if found is None:
        return
    loser_id, outcome, loser_cards = found
    try:
        with orm.db_session:
            match = RivalryMatch(
                season_id=_current_season().id,
                ended_at=_utcnow(),
                winner_id=winner_user.id,
                loser_id=loser_id,
                outcome=outcome,
                started_at=getattr(game, 'started_at', None),
                mode=game.mode,
                players=len(game.players) + len(getattr(game, 'left_users', ())),
                turns=getattr(game, 'turns', None),
                loser_cards_left=loser_cards,
                chat_id=game.chat.id,
            )
            # Commit now: the announcement and the export read it from other threads
            orm.commit()
            match_id = match.id
    except Exception:
        logger.exception("Could not record the rivalry result of chat %s", game.chat.id)
        return
    dispatcher.run_async(_announce, bot, game.chat.id, match_id)


def _current_season():
    """The newest season; the first one is created on demand and named after the year"""
    season = RivalrySeason.select().order_by(lambda s: orm.desc(s.id)).first()
    if season is None:
        now = _utcnow()
        season = RivalrySeason(name=str(rs.to_local(now, _timezone()).year), started_at=now)
        orm.flush()
    return season


def _season_record(season):
    return rs.Season(season.id, season.name, season.started_at)


def _result(m):
    return rs.Result(m.id, m.season_id, m.ended_at, m.winner_id, m.loser_id, m.outcome, m.started_at,
                     m.mode, m.players, m.turns, m.loser_cards_left)


def _load():
    """(seasons oldest first, results between the rivals oldest first). Call within a db_session."""
    seasons = sorted((_season_record(s) for s in RivalrySeason.select()), key=lambda s: s.id)
    rivals = _rivals()
    results = rs.between([_result(m) for m in RivalryMatch.select()], rivals.a_id, rivals.b_id)
    return seasons, results


def _announce(bot, chat_id, match_id):
    time.sleep(ANNOUNCE_DELAY)
    try:
        with orm.db_session:
            seasons, results = _load()
            result = next(r for r in results if r.id == match_id)
            season = next(s for s in seasons if s.id == result.season_id)
            season_results = [r for r in results if r.season_id == season.id]
            text = rs.pin_text(_rivals(), season, season_results, result)
        _post_score(bot, chat_id, text, match_id)
    except Exception:
        logger.exception("Could not announce rivalry result %s", match_id)
    export()


def _post_score(bot, chat_id, text, match_id=None):
    """Send a score message, pin it in place of the previous one and remember it on `match_id`"""
    message = bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    if not RIVALRY_PIN:
        return
    try:
        bot.pin_chat_message(chat_id, message.message_id, disable_notification=True)
    except Exception:
        logger.warning("Could not pin the score in chat %s (is the bot an admin?)", chat_id, exc_info=True)
        return
    new_id = message.message_id
    with orm.db_session:
        previous = RivalryMatch.select(
            lambda m: m.chat_id == chat_id and m.message_id is not None and m.message_id != new_id
        ).order_by(lambda m: orm.desc(m.id)).first()
        previous_message_id = None
        if previous is not None:
            previous_message_id, previous.message_id = previous.message_id, None
        if match_id is not None and RivalryMatch.exists(id=match_id):
            RivalryMatch[match_id].message_id = new_id
    if previous_message_id:
        _unpin(bot, chat_id, previous_message_id)


def _unpin(bot, chat_id, message_id):
    try:
        bot.unpin_chat_message(chat_id, message_id=message_id)
    except Exception:
        logger.info("Could not unpin score message %s in chat %s", message_id, chat_id, exc_info=True)


# --- Static exports -------------------------------------------------------

def _export_dir():
    if RIVALRY_EXPORT_DIR is False or RIVALRY_EXPORT_DIR == "":
        return None
    if RIVALRY_EXPORT_DIR:
        return RIVALRY_EXPORT_DIR
    return os.path.join(os.path.dirname(os.path.abspath(os.getenv('UNO_DB', 'uno.sqlite3'))), 'web')


def _safe_name(name):
    return re.sub(r'[^\w.-]+', '_', name).strip('._') or 'season'


def _write_atomic(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + '.tmp'
    with open(temp, 'w', encoding='utf-8', newline='') as f:
        f.write(content)
    os.replace(temp, path)


def write_exports(directory, rivals, seasons, results, tz):
    """
    Write the website files:
    index.html, scores.csv (the current season, in the uno-scores format),
    old/scores_<season>.csv for the earlier seasons and scores.json with every detail.
    """
    current = seasons[-1] if seasons else None
    for season in seasons:
        season_results = [r for r in results if r.season_id == season.id]
        name = 'scores.csv' if season is current else os.path.join('old', 'scores_%s.csv' % _safe_name(season.name))
        _write_atomic(os.path.join(directory, name), rs.scores_csv(rivals, season_results, tz))
    if current is None:
        _write_atomic(os.path.join(directory, 'scores.csv'), rs.scores_csv(rivals, [], tz))
    _write_atomic(os.path.join(directory, 'scores.json'), rs.scores_json(rivals, seasons, results, tz))
    if os.path.exists(INDEX_HTML):
        temp = os.path.join(directory, 'index.html.tmp')
        shutil.copyfile(INDEX_HTML, temp)
        os.replace(temp, os.path.join(directory, 'index.html'))


def export():
    directory = _export_dir()
    if not ENABLED or not directory:
        return
    try:
        with _export_lock:
            with orm.db_session:
                seasons, results = _load()
            write_exports(directory, _rivals(), seasons, results, _timezone())
    except Exception:
        logger.exception("Could not write the rivalry exports to %s", directory)


# --- Commands -------------------------------------------------------------

def _is_manager(user):
    return user.id in RIVAL_IDS or user.id in (ADMIN_LIST or ())


def _allowed(update):
    chat, user = update.effective_chat, update.effective_user
    if RIVALRY_CHAT_ID is None or chat.id == RIVALRY_CHAT_ID:
        return True
    return chat.type == 'private' and _is_manager(user)


def _reply(update, context, text):
    send_async(context.bot, update.effective_chat.id, text=text, parse_mode=ParseMode.HTML,
               disable_web_page_preview=True)


def score(update: Update, context: CallbackContext):
    """/score, /score all, /score <season> or /score history [n]"""
    if not _allowed(update):
        return
    args = context.args or []
    rivals, tz = _rivals(), _timezone()
    with orm.db_session:
        seasons, results = _load()

    if args and args[0].lower() == 'history':
        count = HISTORY_DEFAULT
        if len(args) > 1 and args[1].isdigit():
            count = max(1, min(HISTORY_MAX, int(args[1])))
        _reply(update, context, rs.history_text(rivals, results, tz, count))
        return

    now = datetime.now(timezone.utc)
    if args and args[0].lower() == 'all':
        _reply(update, context, rs.score_text(rivals, "All time", results, tz, now))
        return

    if args:
        wanted = ' '.join(args).lower()
        season = next((s for s in seasons if s.name.lower() == wanted), None)
        if season is None:
            names = ', '.join(escape(s.name) for s in seasons) or 'none yet'
            _reply(update, context, "No season called <b>%s</b>. Seasons: %s" % (escape(' '.join(args)), names))
            return
    else:
        season = seasons[-1] if seasons else None

    if season is None:
        _reply(update, context, "📊 No games recorded yet.")
        return
    current = season is seasons[-1]
    season_results = [r for r in results if r.season_id == season.id]
    text = rs.score_text(rivals, "Season %s" % season.name, season_results, tz, now,
                         all_results=results, show_periods=current)
    if current and len(seasons) > 1:
        first, second = rivals.ordered(results)
        text += "\n\nAll time: %s (/score all)" % rivals.scoreline(results, first, second)
    _reply(update, context, text)


def score_add(update: Update, context: CallbackContext):
    """/score_add <name>: record a win for a game played outside the bot"""
    if not _allowed(update) or not _is_manager(update.effective_user):
        return
    rivals = _rivals()
    wanted = ' '.join(context.args or []).strip().lower()
    winner_id = next((pid for pid, name in RIVALRY_PLAYERS if name.lower() == wanted), None)
    if winner_id is None:
        names = ' or '.join(escape(name) for _, name in RIVALRY_PLAYERS)
        _reply(update, context, "Usage: /score_add &lt;winner&gt;, the winner being %s" % names)
        return
    chat_id = update.effective_chat.id
    with orm.db_session:
        match = RivalryMatch(season_id=_current_season().id, ended_at=_utcnow(), winner_id=winner_id,
                             loser_id=rivals.other(winner_id), outcome='manual', chat_id=chat_id)
        orm.commit()
        match_id = match.id
    dispatcher.run_async(_announce, context.bot, chat_id, match_id)


def score_undo(update: Update, context: CallbackContext):
    """/score_undo: remove the newest result"""
    if not _allowed(update) or not _is_manager(update.effective_user):
        return
    rivals, tz = _rivals(), _timezone()
    chat_id = update.effective_chat.id
    with orm.db_session:
        seasons, results = _load()
        if not results:
            _reply(update, context, "Nothing to undo.")
            return
        last = results[-1]
        removed = RivalryMatch[last.id]
        pinned = (removed.chat_id, removed.message_id) if removed.message_id else None
        removed.delete()
    results = results[:-1]
    season = next(s for s in seasons if s.id == last.season_id)
    season_results = [r for r in results if r.season_id == season.id]
    text = "↩️ Removed %s's win from %s.\n\n%s" % (
        rivals.name(last.winner_id), rs.to_local(last.ended_at, tz).strftime('%a %d %b %H:%M'),
        rs.standing_text(rivals, season, season_results))
    dispatcher.run_async(_after_undo, context.bot, chat_id, text, pinned,
                         season_results[-1].id if season_results else None)


def _after_undo(bot, chat_id, text, pinned, match_id):
    if pinned:
        _unpin(bot, *pinned)
    try:
        _post_score(bot, chat_id, text, match_id)
    except Exception:
        logger.exception("Could not post the score after an undo")
    export()


def score_season(update: Update, context: CallbackContext):
    """/score_season <name>: start a new season from 0 – 0"""
    if not _allowed(update) or not _is_manager(update.effective_user):
        return
    name = ' '.join(context.args or []).strip()
    if not name:
        _reply(update, context, "Usage: /score_season &lt;name&gt;, e.g. /score_season 2027")
        return
    with orm.db_session:
        if RivalrySeason.exists(lambda s: s.name.lower() == name.lower()):
            _reply(update, context, "A season called <b>%s</b> already exists." % escape(name))
            return
        RivalrySeason(name=name, started_at=_utcnow())
    _reply(update, context, "🆕 Season <b>%s</b> started. The score is back to 0 – 0." % escape(name))
    dispatcher.run_async(export)


def register():
    if not ENABLED:
        if RIVALRY_ENABLED:
            logger.warning("Rivalry tracking is enabled but needs exactly two different player ids; it is off")
        return
    dispatcher.add_handler(CommandHandler('score', score))
    dispatcher.add_handler(CommandHandler('score_add', score_add))
    dispatcher.add_handler(CommandHandler('score_undo', score_undo))
    dispatcher.add_handler(CommandHandler('score_season', score_season))
    export()
