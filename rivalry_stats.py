#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Head-to-head metrics and texts for the rivalry score.
# Everything here works on plain match records (see Result) and never touches
# the database or Telegram, so it can be tested and reused by the exporter.

import csv
import io
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import List, Optional

import modes
import volcano

WEEKDAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
# (label, first hour, last hour + 1) in local time; Night wraps past midnight
DAYPARTS = (('Morning', 5, 12), ('Afternoon', 12, 17), ('Evening', 17, 22), ('Night', 22, 29))
OUTCOME_NOTES = {'forfeit': 'by forfeit', 'manual': 'added by hand'}
JSON_VERSION = 3
# Best win share and ratio count from this many games, so an early 1–0 is not 100%.
# web/index.html uses the same number (MIN_GAMES).
RECORD_MIN_GAMES = 20
COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')
# The element of web/index.html that the export fills with the data
DATA_PLACEHOLDER = '<script id="rivalry-data" type="application/json"></script>'
# The same element with whatever it holds: when the export folder is web/ itself, the
# template the export reads is the page it wrote last time, with older data inlined
DATA_ELEMENT_RE = re.compile(r'(<script id="rivalry-data" type="application/json">).*?(</script>)', re.S)


@dataclass
class Result:
    id: int
    season_id: int
    ended_at: datetime  # naive UTC
    winner_id: int
    loser_id: int
    outcome: str
    started_at: Optional[datetime] = None  # naive UTC
    mode: Optional[str] = None
    players: Optional[int] = None
    turns: Optional[int] = None
    loser_cards_left: Optional[int] = None


@dataclass
class Season:
    id: int
    name: str
    started_at: datetime


def to_local(dt, tz):
    return dt.replace(tzinfo=timezone.utc).astimezone(tz)


def between(results, a_id, b_id):
    """The results of games between the two rivals, oldest first"""
    pair = {a_id, b_id}
    return sorted((r for r in results if {r.winner_id, r.loser_id} == pair),
                  key=lambda r: (r.ended_at, r.id))


def tally(results, a_id, b_id):
    wins = Counter(r.winner_id for r in results)
    return wins[a_id], wins[b_id]


def running_scores(results, a_id, b_id):
    """[(result, score_a, score_b)] after each result"""
    a = b = 0
    rows = []
    for r in results:
        if r.winner_id == a_id:
            a += 1
        else:
            b += 1
        rows.append((r, a, b))
    return rows


def streaks(results):
    """(current streak as (player id, length) or None, {player id: longest streak})"""
    longest = Counter()
    current_id, length = None, 0
    for r in results:
        if r.winner_id == current_id:
            length += 1
        else:
            current_id, length = r.winner_id, 1
        longest[current_id] = max(longest[current_id], length)
    return ((current_id, length) if current_id is not None else None), longest


def biggest_leads(results, a_id, b_id):
    leads = Counter()
    for _, a, b in running_scores(results, a_id, b_id):
        leads[a_id] = max(leads[a_id], a - b)
        leads[b_id] = max(leads[b_id], b - a)
    return leads


def best_win_shares(results, a_id, b_id, min_games=RECORD_MIN_GAMES):
    """{player id: (best_wins, total_games)} counting from `min_games` games, or None before that"""
    best = {a_id: None, b_id: None}
    for _, a, b in running_scores(results, a_id, b_id):
        total = a + b
        if total < min_games:
            continue
        for pid, wins in ((a_id, a), (b_id, b)):
            if best[pid] is None:
                best[pid] = (wins, total)
                continue
            best_w, best_n = best[pid]
            if wins * best_n > best_w * total or (wins > 0 and wins * best_n == best_w * total):
                best[pid] = (wins, total)
    return best


def most_wins_in_a_day(results, tz):
    """{player id: (wins, local date)}"""
    per_day = Counter((r.winner_id, to_local(r.ended_at, tz).date()) for r in results)
    best = {}
    for (pid, day), n in sorted(per_day.items(), key=lambda item: item[0][1]):
        if pid not in best or n > best[pid][0]:
            best[pid] = (n, day)
    return best


def daypart(local_dt):
    hour = local_dt.hour if local_dt.hour >= 5 else local_dt.hour + 24
    return next(label for label, start, end in DAYPARTS if start <= hour < end)


def duration(r):
    if r.started_at is None or r.ended_at < r.started_at:
        return None
    return r.ended_at - r.started_at


def format_duration(delta):
    seconds = int(delta.total_seconds())
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return "%dh %02dm" % (hours, minutes)
    return "%dm %02ds" % (minutes, seconds)


def percent(part, whole):
    return (part / whole * 100) if whole else 0.0


def ratio(part, other):
    return "%.2f" % (part / other) if other else "∞" if part else "–"


class Rivals:
    """The two configured players. The order is the column order of the exports;
    texts put whoever leads first."""

    def __init__(self, players):
        (self.a_id, self.a_name), (self.b_id, self.b_name) = players
        self.names = {self.a_id: self.a_name, self.b_id: self.b_name}

    def name(self, pid):
        return escape(self.names.get(pid, str(pid)))

    def other(self, pid):
        return self.b_id if pid == self.a_id else self.a_id

    def initials(self):
        """One-letter marks for the form line, or the full names when they clash"""
        a, b = self.a_name[:1].upper(), self.b_name[:1].upper()
        if a and b and a != b:
            return {self.a_id: escape(a), self.b_id: escape(b)}
        return {self.a_id: self.name(self.a_id), self.b_id: self.name(self.b_id)}

    def ordered(self, results, tie_first=None):
        """(first, second) player ids: the leader first; on a tie `tie_first`, else config order"""
        a, b = tally(results, self.a_id, self.b_id)
        if a > b:
            return self.a_id, self.b_id
        if b > a:
            return self.b_id, self.a_id
        if tie_first == self.b_id:
            return self.b_id, self.a_id
        return self.a_id, self.b_id

    def scoreline(self, results, first, second):
        wins = Counter(r.winner_id for r in results)
        return "%s %d – %d %s" % (self.name(first), wins[first], wins[second], self.name(second))

    def lead_text(self, results):
        a, b = tally(results, self.a_id, self.b_id)
        if a == b:
            return "All square"
        leader = self.a_id if a > b else self.b_id
        return "%s leads by %d" % (self.name(leader), abs(a - b))

    def pair(self, values, first, second, fmt="{}"):
        return "%s %s · %s %s" % (self.name(first), fmt.format(values[first]),
                                  self.name(second), fmt.format(values[second]))


def standing_text(rivals, season, season_results, tie_first=None):
    """The season score with whoever leads first"""
    first, second = rivals.ordered(season_results, tie_first=tie_first)
    lines = ["<b>%s</b>" % rivals.scoreline(season_results, first, second),
             rivals.lead_text(season_results)]
    current, _ = streaks(season_results)
    if current and current[1] >= 2:
        lines.append("🔥 %s: %d in a row" % (rivals.name(current[0]), current[1]))
    lines.append("<i>Season %s · game %d</i>" % (escape(season.name), len(season_results)))
    return "\n".join(lines)


def pin_text(rivals, season, season_results, result):
    """The message posted (and pinned) after a recorded result"""
    note = OUTCOME_NOTES.get(result.outcome)
    return "🏆 <b>%s</b> wins%s!\n\n%s" % (rivals.name(result.winner_id), " (%s)" % note if note else "",
                                         standing_text(rivals, season, season_results, result.winner_id))


def score_text(rivals, title, results, tz, now, all_results=None, show_periods=False):
    """The /score message for one season (or all time) of results"""
    if not results:
        return "📊 <b>%s</b>\n\nNo games recorded yet." % escape(title)

    first, second = rivals.ordered(results)
    wins = Counter(r.winner_id for r in results)
    total = len(results)
    lines = ["📊 <b>%s</b> · %d game%s" % (escape(title), total, "" if total == 1 else "s"),
             "",
             "<b>%s</b>" % rivals.scoreline(results, first, second),
             rivals.lead_text(results),
             "",
             "<b>Win share</b>",
             rivals.pair({pid: percent(wins[pid], total) for pid in (first, second)}, first, second, "{:.1f}%"),
             "%s/%s %s · %s/%s %s" % (rivals.name(first), rivals.name(second), ratio(wins[first], wins[second]),
                                      rivals.name(second), rivals.name(first), ratio(wins[second], wins[first]))]

    if show_periods:
        local_now = now.astimezone(tz)
        today = local_now.date()
        week_start = today - timedelta(days=today.weekday())
        periods = (("Today", lambda d: d == today),
                   ("This week", lambda d: d >= week_start),
                   ("This month", lambda d: (d.year, d.month) == (today.year, today.month)))
        source = all_results if all_results is not None else results
        lines += ["", "<b>Periods</b> (%s – %s)" % (rivals.name(first), rivals.name(second))]
        for label, keep in periods:
            period = [r for r in source if keep(to_local(r.ended_at, tz).date())]
            counts = Counter(r.winner_id for r in period)
            lines.append("%s: %d – %d" % (label, counts[first], counts[second]))

    marks = rivals.initials()
    current, longest = streaks(results)
    lines += ["", "<b>Form</b> (last 10, newest last)",
              " ".join(marks[r.winner_id] for r in results[-10:]),
              "Current streak: %s %d" % (rivals.name(current[0]), current[1]),
              "Longest streak: " + rivals.pair(longest, first, second)]

    leads = biggest_leads(results, rivals.a_id, rivals.b_id)
    bests = best_win_shares(results, rivals.a_id, rivals.b_id)
    best_days = most_wins_in_a_day(results, tz)
    forfeits = Counter(r.winner_id for r in results if r.outcome == 'forfeit')
    if bests[first] is None:
        pending = "from game %d (%d so far)" % (RECORD_MIN_GAMES, total)
        share_line, ratio_line = "Best win share: " + pending, "Best ratio: " + pending
    else:
        share_values = {pid: "%.1f%% (%d games)" % (percent(bests[pid][0], bests[pid][1]), bests[pid][1])
                        for pid in (first, second)}
        share_line = "Best win share: " + rivals.pair(share_values, first, second)
        ratio_first = ratio(bests[first][0], bests[first][1] - bests[first][0])
        ratio_second = ratio(bests[second][0], bests[second][1] - bests[second][0])
        ratio_line = ("Best ratio: %s/%s %s (%d games) · %s/%s %s (%d games)" %
                      (rivals.name(first), rivals.name(second), ratio_first, bests[first][1],
                       rivals.name(second), rivals.name(first), ratio_second, bests[second][1]))
    day_values = {pid: ("%d (%s)" % (best_days[pid][0], best_days[pid][1].strftime('%d %b %Y'))
                        if pid in best_days else "0") for pid in (first, second)}
    lines += ["", "<b>Records</b>",
              "Biggest lead: " + rivals.pair(leads, first, second, "+{}"),
              share_line,
              ratio_line,
              "Most wins in a day: " + rivals.pair(day_values, first, second),
              "Forfeit wins: " + rivals.pair(forfeits, first, second)]

    tracked = [r for r in results if duration(r) is not None]
    if tracked:
        lengths = [duration(r) for r in tracked]
        average = sum(lengths, timedelta()) / len(lengths)
        fastest = min(tracked, key=duration)
        lines += ["", "<b>Games</b> (%d tracked by the bot)" % len(tracked),
                  "Average length: %s" % format_duration(average),
                  "Fastest win: %s by %s" % (format_duration(duration(fastest)), rivals.name(fastest.winner_id))]
        turns = [r.turns for r in tracked if r.turns]
        if turns:
            lines.append("Average turns: %.0f" % (sum(turns) / len(turns)))
        cards = [r.loser_cards_left for r in tracked if r.loser_cards_left is not None]
        if cards:
            lines.append("Cards left in the loser's hand: %.1f on average" % (sum(cards) / len(cards)))
        crowded = [r for r in tracked if (r.players or 2) > 2]
        if crowded:
            counts = Counter(r.winner_id for r in crowded)
            lines.append("Games with 3+ players: %d – %d" % (counts[first], counts[second]))
        modes = sorted({r.mode for r in tracked if r.mode})
        if modes:
            per_mode = []
            for mode in modes:
                counts = Counter(r.winner_id for r in tracked if r.mode == mode)
                per_mode.append("%s %d – %d" % (escape(mode), counts[first], counts[second]))
            lines.append("By mode: " + " · ".join(per_mode))

    lines += ["", "<b>When</b> (%s – %s)" % (rivals.name(first), rivals.name(second))]
    parts = []
    for label, _, _ in DAYPARTS:
        counts = Counter(r.winner_id for r in results if daypart(to_local(r.ended_at, tz)) == label)
        parts.append("%s %d – %d" % (label, counts[first], counts[second]))
    lines.append(" · ".join(parts))
    best_weekday = {}
    for pid in (first, second):
        days = Counter(to_local(r.ended_at, tz).weekday() for r in results if r.winner_id == pid)
        best_weekday[pid] = ("%s (%d)" % (WEEKDAYS[days.most_common(1)[0][0]], days.most_common(1)[0][1])
                             if days else "–")
    lines.append("Best day: " + rivals.pair(best_weekday, first, second))
    return "\n".join(lines)


def history_text(rivals, results, tz, count):
    if not results:
        return "No games recorded yet."
    lines = ["🗒 <b>Last %d game%s</b>" % (min(count, len(results)), "" if count == 1 else "s")]
    numbered = list(enumerate(results, 1))[-count:]
    for number, r in reversed(numbered):
        note = OUTCOME_NOTES.get(r.outcome)
        lines.append("#%d · %s · %s%s" % (number, to_local(r.ended_at, tz).strftime('%a %d %b %H:%M'),
                                          rivals.name(r.winner_id), " (%s)" % note if note else ""))
    return "\n".join(lines)


def scores_csv(rivals, results, tz):
    """The running score in the uno-scores format the website's index.html reads"""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(('date', 'time', 'score_a', 'score_i'))
    for r, a, b in running_scores(results, rivals.a_id, rivals.b_id):
        local = to_local(r.ended_at, tz)
        writer.writerow((local.strftime('%Y-%m-%d'), local.strftime('%H:%M'), a, b))
    return out.getvalue()


def valid_color(value):
    return value if isinstance(value, str) and COLOR_RE.match(value) else None


def scores_data(rivals, seasons: List[Season], results, tz, colors=None, logs=None, pacing=None):
    """
    Every result with its details, for a richer website. Telegram IDs are left out.
    `logs`: {match id: game log}; a result with a log carries its summary ('d') and the
    name of its replay file ('g'). `pacing`: the bot's delay in seconds before each turn.
    """
    logs = logs or {}
    keys = {rivals.a_id: 'a', rivals.b_id: 'b'}
    names = {s.id: s.name for s in seasons}
    colors = list(colors or ()) + [None, None]

    def stamp(dt):
        return to_local(dt, tz).isoformat(timespec='seconds') if dt else None

    return {
        'version': JSON_VERSION,
        'timezone': str(getattr(tz, 'key', tz)),
        'pacing': pacing,
        'players': {'a': rivals.a_name, 'b': rivals.b_name},
        'colors': {'a': valid_color(colors[0]), 'b': valid_color(colors[1])},
        # The deck of each mode (web/decks.js), for the website's fairness tests
        'decks': {mode.id: mode.deck for mode in modes.MODES.values()},
        'seasons': [{'name': s.name, 'started_at': stamp(s.started_at)} for s in seasons],
        'matches': [{
            'n': number,
            'season': names.get(r.season_id),
            'ended_at': stamp(r.ended_at),
            'started_at': stamp(r.started_at),
            'winner': keys[r.winner_id],
            'outcome': r.outcome,
            'mode': r.mode,
            'players': r.players,
            'turns': r.turns,
            'loser_cards_left': r.loser_cards_left,
            **({'g': r.id, 'd': summarize_log(logs[r.id])} if r.id in logs else {}),
            **({'f': fair_status(logs[r.id])} if r.id in logs and fair_status(logs[r.id]) else {}),
        } for number, r in enumerate(results, 1)],
    }


def fair_status(log):
    """How a logged game's cards can be checked: 'drand' or 'players' (provably fair), 'system'
    (the server's own generator, not checkable) or None (no proof: older games, or fair play off)"""
    proof = log.get('proof')
    if not proof:
        return None
    if not proof.get('verifiable'):
        return 'system'
    return proof.get('source') or 'drand'


def replay_data(rivals, result, number, log, tz, colors=None, pacing=None):
    """The replay file of one game: its result and every logged event"""
    colors = list(colors or ()) + [None, None]
    keys = {rivals.a_id: 'a', rivals.b_id: 'b'}
    return {
        'version': log.get('v', 1),
        'players': {'a': rivals.a_name, 'b': rivals.b_name},
        'colors': {'a': valid_color(colors[0]), 'b': valid_color(colors[1])},
        'n': number,
        'started_at': to_local(result.started_at, tz).isoformat(timespec='seconds') if result.started_at else None,
        'ended_at': to_local(result.ended_at, tz).isoformat(timespec='seconds'),
        'winner': keys.get(result.winner_id),
        'outcome': result.outcome,
        'mode': result.mode,
        'players_count': result.players,
        'pacing': pacing,
        'events': log['ev'],
        # fairness.py: seed, drand round and card movements, once the game is over
        'proof': log.get('proof'),
    }


def scores_json(rivals, seasons: List[Season], results, tz, colors=None):
    return json.dumps(scores_data(rivals, seasons, results, tz, colors), ensure_ascii=False, indent=1)


def inline_data(page, data):
    """The website page with `data` embedded in its data placeholder, so it renders without a request"""
    text = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    # Keep "</script>" and HTML comments in names from closing the script element
    text = text.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    # The inlined text never holds "<", so the first "</script>" closes the element.
    # A function, not a template string, so the backslashes of the JSON stay as they are.
    return DATA_ELEMENT_RE.sub(lambda m: m.group(1) + text + m.group(2), page, count=1)


# --- Game logs (rivalry_log.py) -------------------------------------------

# The Storm cards come last, so older summaries (6 types) keep their indexes
CARD_TYPES = ('num', 'skip', 'rev', 'd2', 'wild', 'd4', 'swap', 'drop', 'p1')
ACTION_TYPES = ('skip', 'rev', 'd2', 'swap', 'drop', 'p1')
LOG_COLORS = ('r', 'b', 'g', 'y')


def game_stats_text(rivals, number, result, log, tz, label, score_after=None, pacing=0, link=None):
    """
    The /stats message for one logged game: its result and how each rival played.
    `label` turns a card string into a readable card; `score_after`: the season's
    (score of the winner, score of the loser, season name) after this game.
    """
    d = summarize_log(log)
    keys = {rivals.a_id: 'a', rivals.b_id: 'b'}
    win, lose = result.winner_id, result.loser_id
    w, l = keys[win], keys[lose]
    order = ((win, w), (lose, l))

    def pair(values, fmt="{}"):
        return " · ".join("%s %s" % (rivals.name(pid), fmt.format(values[key])) for pid, key in order)

    def think(key):
        s = d[key]
        if not s['th'][1]:
            return "–"
        average = max(0.0, s['th'][0] / s['th'][1] / 1000 - (pacing or 0))
        return "%.1fs (slowest %ds)" % (average, round(max(0, s['th'][2] / 1000 - (pacing or 0))))

    when = to_local(result.ended_at, tz).strftime('%a %d %b %H:%M')
    lines = ["📋 <b>Game #%d</b> · %s%s" % (number, when, " · %s" % escape(result.mode) if result.mode else "")]
    how = " by forfeit" if result.outcome == 'forfeit' else ""
    facts = ["🏆 <b>%s</b> won%s in %s" % (rivals.name(win), how, format_duration(timedelta(milliseconds=d['ms'])))]
    if result.turns:
        facts.append("%d turns" % result.turns)
    lines.append(" · ".join(facts))
    if result.loser_cards_left is not None:
        lines.append("%s was left with %d card%s" % (rivals.name(lose), result.loser_cards_left,
                                                    "" if result.loser_cards_left == 1 else "s"))
    if score_after:
        lines.append("Season %s: %s %d – %d %s" % (escape(score_after[2]), rivals.name(win), score_after[0],
                                                   score_after[1], rivals.name(lose)))

    opening = []
    if d['first'] in (w, l):
        opening.append("%s went first" % rivals.name(win if d['first'] == w else lose))
    if d['fc']:
        opening.append("first card %s" % escape(label(d['fc'])))
    deal = d['deal']
    opening = ", ".join(opening)
    lines += ["", "<b>Start</b>", opening[:1].upper() + opening[1:] if opening else "–",
              "Action and wild cards dealt: " + pair({key: sum(deal.get(key, (0, 0))) for key in (w, l)})]

    s = {key: d[key] for key in (w, l)}
    lines += ["", "<b>Play</b>",
              "Cards played: " + pair({key: s[key]['pl'] for key in s}),
              "Action and wild cards: " + pair({key: sum(s[key]['ty'][1:]) for key in s}),
              "Chosen draws: " + pair({key: "%d (%d playable)" % (s[key]['dv'], s[key]['dk']) for key in s}),
              "Passes: " + pair({key: s[key]['ps'] for key in s}),
              "Penalty cards dealt: " + pair({key: s[key]['dealt'] for key in s}),
              "Penalty cards taken: " + pair({key: s[key]['fd'] for key in s})]
    if d['stack'] >= 4:
        lines.append("Longest +2/+4 stack: +%d" % d['stack'])
    if any(s[key]['ty'][5] for key in s):
        lines.append("+4s played: " + pair({key: "%d (%d bluff%s)" % (s[key]['ty'][5], s[key]['bl'], "" if s[key]['bl'] == 1 else "s")
                                            for key in s}))
    if d.get('storm'):
        st = d['storm']
        lines.append("🌧️ Swaps · Drops · +1s: " + pair({key: "%d · %d · %d" % (st[key]['sw'], st[key]['dr'], st[key]['p1'])
                                                       for key in s}))
        if any(st[key]['cx'] for key in s):
            lines.append("Cancelled with /cancel: " + pair({key: st[key]['cx'] for key in s}))
    if d.get('vol') and (d['vol'][w][0] or d['vol'][l][0]):
        lines.append("🌋 Eruptions: " + pair({key: "%d (+%d cards)" % tuple(d['vol'][key][:2]) for key in s}))
    if any(s[key]['ch'] for key in s):
        lines.append("Challenges won: " + pair({key: "%d of %d" % (s[key]['cw'], s[key]['ch']) for key in s}))

    samples = d['samples'] or 1
    lines += ["", "<b>Hands</b>",
              "Largest hand: " + pair({key: s[key]['mx'] for key in s}),
              "Fewer cards for: " + pair({key: "%.0f%%" % percent(s[key]['ahead'], samples) for key in s}) + " of the game",
              "Down to one card: " + pair({key: "%d time%s" % (s[key]['uno'], "" if s[key]['uno'] == 1 else "s") for key in s})]
    if s[w]['back'] > 0:
        lines.append("Comeback: %s was %d card%s behind at worst" % (rivals.name(win), s[w]['back'],
                                                                   "" if s[w]['back'] == 1 else "s"))
    if d['fin']:
        lines.append("Winning card: %s" % escape(label(d['fin'])))

    lines += ["", "<b>Time</b>", "Thinking per move: " + pair({key: think(key) for key in s})]
    if any(s[key]['to'] for key in s):
        lines.append("Timeouts: " + pair({key: s[key]['to'] for key in s}))
    if link:
        lines += ["", '<a href="%s">▶ Watch the replay</a>' % escape(link, quote=True)]
    return "\n".join(lines)


def card_type(card):
    """One of CARD_TYPES for a card string such as 'r_5', 'g_draw' or 'draw_four'"""
    if card == 'draw_four':
        return 'd4'
    if card == 'colorchooser':
        return 'wild'
    value = card.split('_', 1)[1] if '_' in card else card
    return {'skip': 'skip', 'reverse': 'rev', 'draw': 'd2', 'swap': 'swap', 'drop': 'drop',
            'plus1': 'p1'}.get(value, 'num')


def _player_summary(hand):
    return {
        'pl': 0,                 # cards played
        'ty': [0] * len(CARD_TYPES),           # of each CARD_TYPES
        'co': [0] * 4,           # colored cards played, by LOG_COLORS
        'dv': 0, 'dk': 0,        # chosen draws, and how many of those could be played
        'ps': 0,                 # passes
        'fd': 0,                 # cards drawn as a penalty
        'dealt': 0,              # penalty cards the other rival drew because of this one
        'mx': hand,              # largest hand
        'uno': 0, 'den': 0,      # times down to one card; penalties taken on one card
        'pk': [0] * 4, 'sm': 0,  # colors picked, by LOG_COLORS; picks of the color held most
        'au': 0,                 # colors the bot picked after a timeout
        'bl': 0,                 # +4s played while holding the active color
        'ch': 0, 'cw': 0,        # challenges made, and won
        'to': 0,                 # timeouts
        'th': [0, 0, 0],         # thinking time: total ms, decisions, longest ms
        'ahead': 0,              # plays and draws after which this rival held fewer cards
        'back': 0,               # most cards behind the other rival at any point
        'hd': [0] * len(CARD_TYPES),           # opening hand, by CARD_TYPES
        'hc': [0] * 4,           # colored cards in the opening hand, by LOG_COLORS
        'dt': [0] * len(CARD_TYPES),           # cards drawn, by CARD_TYPES
        'dc': [0] * 4,           # colored cards drawn, by LOG_COLORS
    }


def _count_cards(cards, types, colors):
    """Add `cards` to the per-type and per-color counts (the fairness tests on the website)"""
    for card in cards:
        types[CARD_TYPES.index(card_type(card))] += 1
        if card[:1] in LOG_COLORS and '_' in card:
            colors[LOG_COLORS.index(card[0])] += 1


def summarize_log(log):
    """Per-game numbers of a game log for the website's statistics"""
    events = log['ev']
    deal = events[0]
    hands = {key: len(cards) for key, cards in deal['h'].items()}
    per = {key: _player_summary(hands[key]) for key in hands}
    for key, cards in deal['h'].items():
        _count_cards(cards, per[key]['hd'], per[key]['hc'])
    # The pending draw count, as game.draw_counter keeps it: a +2 turned up as the first
    # card already counts. A stack is recorded only when someone draws it, so a game
    # won on a +2 doesn't count.
    stack = 2 if card_type(deal.get('fc') or '') == 'd2' else 0
    max_stack = samples = 0
    finisher = {}
    # Volcano mode: per rival [eruptions, cards they gave, eruptions after a draw made on
    # one card]; an eruption follows the draw that caused it, so the hand before that draw
    eruptions = {key: [0, 0, 0] for key in hands}
    before_draw = dict(hands)
    first_eruption = None
    # Storm mode, per rival: Swap, Drop and +1 cards played with their effect, Swaps and Drops
    # played after /cancel, cards gained by swapping (less than 0: lost), cards a Drop put
    # back, swaps to a smaller hand, and swaps that left the other player on one card
    storm = {key: {'sw': 0, 'dr': 0, 'p1': 0, 'cx': 0, 'gain': 0, 'back': 0, 'up': 0, 'uno': 0}
             for key in hands}
    storm_game = False
    for e in events[1:]:
        kind, s = e['k'], per.get(e.get('p'))
        if kind == 'swap':
            # Before the rival check: a player the log leaves out may have swapped with a rival
            storm_game = True
            p, vs = e.get('p'), e.get('vs')
            mine, theirs = hands.get(p), hands.get(vs)
            if p in storm:
                st = storm[p]
                st['sw'] += 1
                st['gain'] += e['h'] - mine
                if theirs is not None and theirs < mine:
                    st['up'] += 1
                if e['h2'] == 1:
                    st['uno'] += 1
                hands[p] = e['h']
                s['mx'] = max(s['mx'], e['h'])
            if vs in per:
                hands[vs] = e['h2']
                per[vs]['mx'] = max(per[vs]['mx'], e['h2'])
            continue
        if s is None:
            continue
        p = e['p']
        if 'th' in e:
            s['th'][0] += e['th']
            s['th'][1] += 1
            s['th'][2] = max(s['th'][2], e['th'])
        if kind == 'play':
            kind_of_card = card_type(e['c'])
            if kind_of_card in ('swap', 'drop', 'p1'):
                storm_game = True
                if e.get('cx'):
                    storm[p]['cx'] += 1
                elif kind_of_card == 'p1' and e['h']:
                    storm[p]['p1'] += 1
            s['pl'] += 1
            s['ty'][CARD_TYPES.index(kind_of_card)] += 1
            if e['c'][:1] in LOG_COLORS and '_' in e['c']:
                s['co'][LOG_COLORS.index(e['c'][0])] += 1
            if kind_of_card == 'd4' and e.get('bl'):
                s['bl'] += 1
            if kind_of_card in ('d2', 'd4'):
                stack += 2 if kind_of_card == 'd2' else 4
            else:
                stack = 0
            if e['h'] == 1 and hands[p] > 1:
                s['uno'] += 1
            hands[p] = e['h']
            finisher[p] = e['c']
        elif kind == 'draw':
            count = len(e.get('cs') or ())
            before_draw[p] = hands[p]
            _count_cards(e.get('cs') or (), s['dt'], s['dc'])
            if e.get('r') == 'vol':
                s['dv'] += 1
                s['dk'] += e.get('ok', 0)
            else:
                s['fd'] += count
                if hands[p] == 1:
                    s['den'] += 1
                if e.get('src') in per:
                    per[e['src']]['dealt'] += count
                if stack:
                    # A failed challenge adds 2 to the pile, as do_call_bluff does
                    max_stack = max(max_stack, stack + (2 if e.get('r') == 'chal' else 0))
                stack = 0
            hands[p] = e['h']
            s['mx'] = max(s['mx'], e['h'])
        elif kind == 'drop':
            storm_game = True
            storm[p]['dr'] += 1
            storm[p]['back'] += len(e.get('out') or ())
            _count_cards(e.get('cs') or (), s['dt'], s['dc'])
            hands[p] = e['h']
        elif kind == 'erupt':
            count = len(e.get('cs') or ())
            eruptions[p][0] += 1
            eruptions[p][1] += count
            if before_draw[p] == 1:
                eruptions[p][2] += 1
            if first_eruption is None:
                first_eruption = e['n']
            hands[p] = e['h']
            s['mx'] = max(s['mx'], e['h'])
        elif kind == 'pass':
            s['ps'] += 1
        elif kind == 'color':
            if e.get('au'):
                s['au'] += 1
            elif e.get('co') in LOG_COLORS:
                s['pk'][LOG_COLORS.index(e['co'])] += 1
                s['sm'] += e.get('sm', 0)
        elif kind == 'chal':
            s['ch'] += 1
            s['cw'] += e.get('ok', 0)
        elif kind == 'time':
            s['to'] += 1
        if kind in ('play', 'draw') and len(hands) == 2:
            samples += 1
            if hands['a'] < hands['b']:
                per['a']['ahead'] += 1
            elif hands['b'] < hands['a']:
                per['b']['ahead'] += 1
            per['a']['back'] = max(per['a']['back'], hands['a'] - hands['b'])
            per['b']['back'] = max(per['b']['back'], hands['b'] - hands['a'])

    end = events[-1] if events[-1]['k'] == 'end' else {}
    winner = end.get('p')
    summary = {
        'first': deal.get('first'),
        'fc': deal.get('fc'),
        # (action cards, wild cards) in each starting hand
        'deal': {key: [sum(1 for card in cards if card_type(card) in ACTION_TYPES),
                       sum(1 for card in cards if card_type(card) in ('wild', 'd4'))]
                 for key, cards in deal['h'].items()},
        'fin': finisher.get(winner) if end.get('o') == 'win' else None,
        'stack': max_stack,
        'samples': samples,
        'ms': end.get('t', events[-1]['t']),
    }
    if storm_game:
        summary['storm'] = storm
    if 'vr' in end:
        rolls = end['vr']
        summary['vol'] = {
            'r': rolls,
            # Every eruption of the game; logs from before 've' only had the rivals' ones
            'e': end.get('ve', eruptions.get('a', [0])[0] + eruptions.get('b', [0])[0]),
            # The eruptions the rolls made were expected to give: the chance of each roll
            # added up (the cap ends the rolls, not the sum, so this stays a fair expectation)
            'x': round(sum(1 / volcano.odds(k) for k in range(rolls)), 4),
            'n1': first_eruption,
            **eruptions,
        }
    summary.update(per)
    return summary

