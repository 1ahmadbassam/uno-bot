#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# One-time import of the uno-scores CSV files into the bot's database.
#
# Every CSV row is the running score after one win (date,time,score_a,score_i),
# so each row becomes one result. score_a belongs to the first player under
# "rivalry" -> "players" in config.json, score_i to the second one.
# Stop the bot before running it; it rewrites the website files on its next start.
#
#   python scripts/import_uno_scores.py --season 2025=old/scores_2025.csv --season 2026=scores.csv
#
# Pass the seasons oldest first: the last one becomes the current season.

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ImportFailure(Exception):
    pass


def read_players(config_path):
    try:
        with open(config_path, encoding='utf-8') as f:
            rivalry = json.load(f).get('rivalry') or {}
    except FileNotFoundError:
        raise ImportFailure("Config file %s not found" % config_path)
    players = [(int(p['id']), str(p.get('name') or p['id'])) for p in rivalry.get('players') or []]
    ids = {pid for pid, _ in players}
    if len(players) != 2 or len(ids) != 2 or 0 in ids:
        raise ImportFailure('config.json needs "rivalry": {"players": [{"id": ..., "name": ...}, '
                           '{"id": ..., "name": ...}]} with two different Telegram ids')
    return players, rivalry.get('timezone') or 'UTC'


def parse_scores(path, tz):
    """[(UTC naive datetime, 0 for a score_a win / 1 for a score_i win)] of a uno-scores CSV"""
    wins = []
    previous = (0, 0)
    with open(path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != ['date', 'time', 'score_a', 'score_i']:
            raise ImportFailure("%s: expected the header date,time,score_a,score_i, got %s"
                               % (path, ','.join(reader.fieldnames or [])))
        for line, row in enumerate(reader, start=2):
            try:
                local = datetime.strptime('%s %s' % (row['date'], row['time']), '%Y-%m-%d %H:%M')
                score = (int(row['score_a']), int(row['score_i']))
            except (TypeError, ValueError):
                raise ImportFailure("%s:%d: can't read %s" % (path, line, ','.join(map(str, row.values()))))
            delta = (score[0] - previous[0], score[1] - previous[1])
            if delta not in ((1, 0), (0, 1)):
                raise ImportFailure("%s:%d: the score goes from %d-%d to %d-%d; every row must add exactly one win"
                                   % (path, line, previous[0], previous[1], score[0], score[1]))
            ended = local.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
            wins.append((ended, 0 if delta == (1, 0) else 1))
            previous = score
    return wins, previous


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import uno-scores CSV files into the bot's database.")
    parser.add_argument('--season', action='append', required=True, metavar='NAME=CSV',
                        help="a season name and its CSV file; repeat it, oldest season first")
    parser.add_argument('--db', default=os.getenv('UNO_DB', 'uno.sqlite3'),
                        help="the bot's database (default: $UNO_DB or uno.sqlite3)")
    parser.add_argument('--config', default='config.json', help="the bot's config.json (default: config.json)")
    parser.add_argument('--timezone', help="timezone of the CSV times (default: rivalry.timezone in config.json)")
    parser.add_argument('--replace', action='store_true',
                        help="delete every season and result already in the database first")
    parser.add_argument('--dry-run', action='store_true', help="check the files and show what would be imported")
    args = parser.parse_args(argv)

    try:
        players, config_tz = read_players(args.config)
        tz = ZoneInfo(args.timezone or config_tz)
        seasons = []
        for spec in args.season:
            name, sep, path = spec.partition('=')
            if not sep or not name.strip() or not path:
                raise ImportFailure("--season takes NAME=CSV, got %r" % spec)
            wins, final = parse_scores(path, tz)
            if not wins:
                raise ImportFailure("%s has no results" % path)
            seasons.append((name.strip(), path, wins, final))
        if len({name.lower() for name, _, _, _ in seasons}) != len(seasons):
            raise ImportFailure("Season names must be different")
    except ImportFailure as e:
        print("Error: %s" % e, file=sys.stderr)
        return 1

    (a_id, a_name), (b_id, b_name) = players
    print("score_a -> %s (%d), score_i -> %s (%d), times read as %s" % (a_name, a_id, b_name, b_id, tz.key))
    for name, path, wins, final in seasons:
        print("Season %s from %s: %d results, final score %s %d - %d %s"
              % (name, path, len(wins), a_name, final[0], final[1], b_name))
    if args.dry_run:
        print("Dry run: nothing written.")
        return 0

    from pony.orm import db_session, select
    from database import bind_database
    import user_setting  # noqa: F401
    import persisted_state  # noqa: F401
    from rivalry_models import RivalryMatch, RivalrySeason

    bind_database(args.db)
    with db_session:
        existing = RivalryMatch.select().count() + RivalrySeason.select().count()
        if existing and not args.replace:
            print("Error: %s already has rivalry seasons or results. Run with --replace to delete them "
                  "and import again." % os.path.abspath(args.db), file=sys.stderr)
            return 1
        if args.replace:
            RivalryMatch.select().delete(bulk=True)
            RivalrySeason.select().delete(bulk=True)

        for name, _, wins, _ in seasons:
            season = RivalrySeason(name=name, started_at=wins[0][0])
            season.flush()
            for ended, winner in wins:
                winner_id, loser_id = (a_id, b_id) if winner == 0 else (b_id, a_id)
                RivalryMatch(season_id=season.id, ended_at=ended, winner_id=winner_id, loser_id=loser_id,
                             outcome='imported')

    with db_session:
        for name, _, _, final in seasons:
            season = RivalrySeason.get(name=name)
            a = select(m for m in RivalryMatch if m.season_id == season.id and m.winner_id == a_id).count()
            b = select(m for m in RivalryMatch if m.season_id == season.id and m.winner_id == b_id).count()
            status = "OK" if (a, b) == final else "MISMATCH"
            print("Imported season %s: %s %d - %d %s [%s]" % (name, a_name, a, b, b_name, status))
            if status != "OK":
                return 1
    print("Done. Start the bot: it writes the website files on startup.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
