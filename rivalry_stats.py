#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Head-to-head metrics and texts for the rivalry score.
# Everything here works on plain match records (see Result) and never touches
# the database or Telegram, so it can be tested and reused by the exporter.

import csv
import io
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import List, Optional

WEEKDAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
# (label, first hour, last hour + 1) in local time; Night wraps past midnight
DAYPARTS = (('Morning', 5, 12), ('Afternoon', 12, 17), ('Evening', 17, 22), ('Night', 22, 29))
OUTCOME_NOTES = {'forfeit': 'by forfeit', 'manual': 'added by hand'}


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
    best_days = most_wins_in_a_day(results, tz)
    forfeits = Counter(r.winner_id for r in results if r.outcome == 'forfeit')
    day_values = {pid: ("%d (%s)" % (best_days[pid][0], best_days[pid][1].strftime('%d %b %Y'))
                        if pid in best_days else "0") for pid in (first, second)}
    lines += ["", "<b>Records</b>",
              "Biggest lead: " + rivals.pair(leads, first, second, "+{}"),
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


def scores_json(rivals, seasons: List[Season], results, tz):
    """Every result with its details, for a richer website. Telegram IDs are left out."""
    keys = {rivals.a_id: 'a', rivals.b_id: 'b'}
    names = {s.id: s.name for s in seasons}

    def stamp(dt):
        return to_local(dt, tz).isoformat(timespec='seconds') if dt else None

    data = {
        'players': {'a': rivals.a_name, 'b': rivals.b_name},
        'seasons': [{'name': s.name, 'started_at': stamp(s.started_at)} for s in seasons],
        'matches': [{
            'season': names.get(r.season_id),
            'ended_at': stamp(r.ended_at),
            'started_at': stamp(r.started_at),
            'winner': keys[r.winner_id],
            'outcome': r.outcome,
            'mode': r.mode,
            'players': r.players,
            'turns': r.turns,
            'loser_cards_left': r.loser_cards_left,
        } for r in results],
    }
    return json.dumps(data, ensure_ascii=False, indent=1)

