import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

# Installs the module stubs needed to import bot
import test.test_rate_limit_mitigations as stubs

import card as c
import rivalry
import rivalry_stats as rs
from game import Game
from player import Player

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import import_uno_scores  # noqa: E402

A, I, X = 111, 222, 333
PLAYERS = [(A, "Alice"), (I, "Ivan")]
BEIRUT = ZoneInfo('Asia/Beirut')
T0 = datetime(2026, 10, 28, 12, 0)


def result(n, winner, outcome='win', **kwargs):
    return rs.Result(n, 1, T0 + timedelta(minutes=n), winner, A if winner == I else I, outcome, **kwargs)


def results(winners):
    return [result(n, w) for n, w in enumerate(winners, 1)]


class TestMatchFor(unittest.TestCase):

    def setUp(self):
        patcher = patch.multiple(rivalry, ENABLED=True, RIVAL_IDS={A, I}, RIVALRY_CHAT_ID=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.game = Game(stubs.MockChat(-1))
        self.alice = Player(self.game, stubs.MockUser(A, "Alice"))
        self.ivan = Player(self.game, stubs.MockUser(I, "Ivan"))
        self.game.started = True
        self.ivan.cards = [c.Card(c.RED, '1'), c.Card(c.RED, '2')]

    def test_win_between_the_rivals(self):
        self.assertEqual(rivalry.match_for(self.game, A), (I, 'win', 2))

    def test_counts_with_more_players(self):
        Player(self.game, stubs.MockUser(X, "Other"))
        self.assertEqual(rivalry.match_for(self.game, I), (A, 'win', 0))

    def test_another_winner_does_not_count(self):
        Player(self.game, stubs.MockUser(X, "Other"))
        self.assertIsNone(rivalry.match_for(self.game, X))

    def test_needs_both_rivals(self):
        game = Game(stubs.MockChat(-1))
        Player(game, stubs.MockUser(A, "Alice"))
        Player(game, stubs.MockUser(X, "Other"))
        game.started = True
        self.assertIsNone(rivalry.match_for(game, A))

    def test_rival_who_left_lost_by_forfeit(self):
        Player(self.game, stubs.MockUser(X, "Other"))
        self.ivan.leave()
        self.game.left_users.add(I)
        self.assertEqual(rivalry.match_for(self.game, A), (I, 'forfeit', None))

    def test_forfeit_flag(self):
        self.assertEqual(rivalry.match_for(self.game, A, forfeit=True), (I, 'forfeit', None))

    def test_disabled_other_chat_or_not_started(self):
        with patch.object(rivalry, 'ENABLED', False):
            self.assertIsNone(rivalry.match_for(self.game, A))
        with patch.object(rivalry, 'RIVALRY_CHAT_ID', -2):
            self.assertIsNone(rivalry.match_for(self.game, A))
        self.game.started = False
        self.assertIsNone(rivalry.match_for(self.game, A))

    def test_record_ignores_games_that_do_not_count(self):
        with patch.object(rivalry, 'RivalryMatch') as match, patch.object(rivalry, 'dispatcher') as dispatcher:
            rivalry.record_first_place(None, self.game, stubs.MockUser(X, "Other"))
        match.assert_not_called()
        dispatcher.run_async.assert_not_called()


class TestStats(unittest.TestCase):

    def setUp(self):
        self.rivals = rs.Rivals(PLAYERS)

    def test_streaks_and_leads(self):
        rows = results([A, A, I, I, I, A])
        current, longest = rs.streaks(rows)
        self.assertEqual(current, (A, 1))
        self.assertEqual((longest[A], longest[I]), (2, 3))
        leads = rs.biggest_leads(rows, A, I)
        self.assertEqual((leads[A], leads[I]), (2, 1))

    def test_pin_puts_the_leader_first(self):
        rows = results([I, I, A])
        text = rs.pin_text(self.rivals, rs.Season(1, "2026", T0), rows, rows[-1])
        self.assertIn("<b>Alice</b> wins!", text)
        self.assertIn("<b>Ivan 2 – 1 Alice</b>", text)
        self.assertIn("Ivan leads by 1", text)
        self.assertIn("Season 2026 · game 3", text)

    def test_pin_puts_the_winner_first_on_a_tie(self):
        rows = results([A, I])
        text = rs.pin_text(self.rivals, rs.Season(1, "2026", T0), rows, rows[-1])
        self.assertIn("<b>Ivan 1 – 1 Alice</b>", text)
        self.assertIn("All square", text)

    def test_pin_notes_forfeits(self):
        rows = [result(1, A, 'forfeit')]
        self.assertIn("wins (by forfeit)!", rs.pin_text(self.rivals, rs.Season(1, "s", T0), rows, rows[0]))

    def test_names_are_escaped(self):
        rivals = rs.Rivals([(A, "<A>"), (I, "I&I")])
        rows = results([A])
        text = rs.pin_text(rivals, rs.Season(1, "s", T0), rows, rows[0])
        self.assertIn("&lt;A&gt;", text)
        self.assertIn("I&amp;I", text)

    def test_score_text_shows_both_ratios(self):
        rows = results([A, A, A, I])
        rows[0].started_at = rows[0].ended_at - timedelta(minutes=5)
        rows[0].turns = 30
        text = rs.score_text(self.rivals, "Season 2026", rows, BEIRUT, datetime.now(timezone.utc),
                             show_periods=True)
        self.assertIn("<b>Alice 3 – 1 Ivan</b>", text)
        self.assertIn("Alice/Ivan 3.00 · Ivan/Alice 0.33", text)
        self.assertIn("Alice 75.0% · Ivan 25.0%", text)
        self.assertIn("A A A I", text)
        self.assertIn("Average length: 5m 00s", text)
        self.assertIn("Periods", text)

    def test_score_text_without_games(self):
        self.assertIn("No games recorded yet", rs.score_text(self.rivals, "S", [], BEIRUT, datetime.now(timezone.utc)))

    def test_history_is_newest_first(self):
        text = rs.history_text(self.rivals, results([A, I, A]), BEIRUT, 2)
        lines = text.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[1].startswith("#3 ") and lines[1].endswith("Alice"))
        self.assertTrue(lines[2].startswith("#2 ") and lines[2].endswith("Ivan"))

    def test_csv_is_the_uno_scores_format_in_local_time(self):
        rows = list(csv.reader(rs.scores_csv(self.rivals, results([I, A]), BEIRUT).splitlines()))
        self.assertEqual(rows, [['date', 'time', 'score_a', 'score_i'],
                                ['2026-10-28', '14:01', '0', '1'],
                                ['2026-10-28', '14:02', '1', '1']])

    def test_json_leaves_telegram_ids_out(self):
        text = rs.scores_json(self.rivals, [rs.Season(1, "2026", T0)], results([A, I]), BEIRUT)
        data = json.loads(text)
        self.assertNotIn(str(A), text)
        self.assertEqual(data['players'], {'a': 'Alice', 'b': 'Ivan'})
        self.assertEqual([m['winner'] for m in data['matches']], ['a', 'b'])
        self.assertEqual(data['matches'][0]['season'], '2026')

    def test_between_drops_results_of_other_pairs(self):
        other = rs.Result(9, 1, T0, A, X, 'manual')
        self.assertEqual([r.id for r in rs.between([other] + results([A]), A, I)], [1])


class TestExports(unittest.TestCase):

    def test_writes_current_and_old_seasons(self):
        seasons = [rs.Season(1, "2025", T0), rs.Season(2, "2026", T0)]
        rows = results([A, I, A])
        rows[0].season_id = 1
        for row in rows[1:]:
            row.season_id = 2
        with tempfile.TemporaryDirectory() as directory:
            rivalry.write_exports(directory, rs.Rivals(PLAYERS), seasons, rows, BEIRUT)
            with open(os.path.join(directory, 'scores.csv')) as f:
                self.assertEqual(f.read().splitlines()[-1], '2026-10-28,14:03,1,1')
            with open(os.path.join(directory, 'old', 'scores_2025.csv')) as f:
                self.assertEqual(f.read().splitlines()[-1], '2026-10-28,14:01,1,0')
            self.assertTrue(os.path.exists(os.path.join(directory, 'index.html')))
            self.assertTrue(os.path.exists(os.path.join(directory, 'scores.json')))
            self.assertFalse([name for name in os.listdir(directory) if name.endswith('.tmp')])


class TestImport(unittest.TestCase):

    def write(self, directory, name, text):
        path = os.path.join(directory, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        return path

    def test_parse_turns_rows_into_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, 's.csv', "date,time,score_a,score_i\n"
                                                  "2026-10-17,15:04,0,1\n2026-10-18,20:40,1,1\n")
            wins, final = import_uno_scores.parse_scores(path, BEIRUT)
        self.assertEqual(wins, [(datetime(2026, 10, 17, 12, 4), 1), (datetime(2026, 10, 18, 17, 40), 0)])
        self.assertEqual(final, (1, 1))

    def test_parse_rejects_jumps_and_bad_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            jump = self.write(directory, 'j.csv', "date,time,score_a,score_i\n2026-10-17,15:04,2,0\n")
            header = self.write(directory, 'h.csv', "when,a,i\n")
            with self.assertRaises(import_uno_scores.ImportFailure):
                import_uno_scores.parse_scores(jump, BEIRUT)
            with self.assertRaises(import_uno_scores.ImportFailure):
                import_uno_scores.parse_scores(header, BEIRUT)

    def test_import_into_a_database(self):
        script = os.path.join(ROOT, 'scripts', 'import_uno_scores.py')
        with tempfile.TemporaryDirectory() as directory:
            self.write(directory, 'config.json', json.dumps(
                {'rivalry': {'players': [{'id': A, 'name': 'Alice'}, {'id': I, 'name': 'Ivan'}],
                             'timezone': 'Asia/Beirut'}}))
            old = self.write(directory, 'old.csv', "date,time,score_a,score_i\n2025-05-25,22:19,1,0\n")
            new = self.write(directory, 'new.csv', "date,time,score_a,score_i\n"
                                                   "2026-10-17,15:04,0,1\n2026-10-18,20:40,0,2\n")
            args = [sys.executable, script, '--db', 'uno.sqlite3', '--season', '2025=' + old, '--season', '2026=' + new]
            run = subprocess.run(args, cwd=directory, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("Imported season 2026: Alice 0 - 2 Ivan [OK]", run.stdout)

            again = subprocess.run(args, cwd=directory, capture_output=True, text=True)
            self.assertEqual(again.returncode, 1)
            self.assertIn("--replace", again.stderr)

            replaced = subprocess.run(args + ['--replace'], cwd=directory, capture_output=True, text=True)
            self.assertEqual(replaced.returncode, 0, replaced.stderr)


if __name__ == '__main__':
    unittest.main()
