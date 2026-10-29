import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

# Installs the module stubs needed to import bot
import test.test_rate_limit_mitigations as stubs

import actions
import card as c
import rivalry
import rivalry_log
import rivalry_stats as rs
from game import Game
from game_manager import GameManager
from player import Player

A, I, X = 111, 222, 333


class Clock:
    """A clock the recorder reads instead of the real time"""

    def __init__(self):
        self.now = 1_000_000

    def __call__(self):
        return self.now

    def wait(self, seconds):
        self.now += int(seconds * 1000)


class LoggedGame(unittest.TestCase):
    """A started two-player game between the rivals, with the recorder on"""

    def setUp(self):
        self.clock = Clock()
        for patcher in (patch.multiple(rivalry_log, ENABLED=True, KEYS={A: 'a', I: 'b'}, RIVALRY_CHAT_ID=None),
                        patch.object(rivalry_log, '_now_ms', self.clock),
                        patch.object(actions, 'gm', MagicMock()),
                        patch.object(actions, 'send_async'),
                        patch.object(actions, 'send_paced')):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.game = Game(stubs.MockChat(-1))
        self.alice = Player(self.game, stubs.MockUser(A, "Alice"))
        self.ivan = Player(self.game, stubs.MockUser(I, "Ivan"))
        self.game.started = True
        self.game.deck._fill_classic_()
        self.game.current_player = self.alice
        self.game.last_card = c.Card(c.RED, '5')
        self.alice.cards = [c.Card(c.RED, '7'), c.Card(c.RED, c.DRAW_TWO), c.Card(c.BLUE, '3')]
        self.ivan.cards = [c.Card(c.GREEN, '1'), c.Card(c.GREEN, '2'), c.Card(c.YELLOW, '9')]
        rivalry_log.start(self.game)

    def events(self, kind=None):
        return [e for e in self.game.rivalry_log['ev'] if kind is None or e['k'] == kind]


class TestRecorder(LoggedGame):

    def test_deal_records_both_hands_and_the_opening(self):
        deal = self.events()[0]
        self.assertEqual(deal['k'], 'deal')
        self.assertEqual(deal['h'], {'a': ['r_7', 'r_draw', 'b_3'], 'b': ['g_1', 'g_2', 'y_9']})
        self.assertEqual((deal['fc'], deal['first']), ('r_5', 'a'))

    def test_only_rival_games_in_the_rivalry_chat_are_logged(self):
        other = Game(stubs.MockChat(-1))
        Player(other, stubs.MockUser(A, "Alice"))
        Player(other, stubs.MockUser(X, "Xena"))
        rivalry_log.start(other)
        self.assertIsNone(other.rivalry_log)
        with patch.object(rivalry_log, 'RIVALRY_CHAT_ID', -2):
            rivalry_log.start(self.game)
        self.assertIsNone(self.game.rivalry_log)

    def test_thinking_time_is_taken_before_the_card_ends_the_turn(self):
        self.clock.wait(4)
        actions.do_play_card(MagicMock(), self.alice, 'r_7')
        play = self.events('play')[0]
        self.assertEqual((play['p'], play['c'], play['h']), ('a', 'r_7', 2))
        self.assertEqual(play['th'], 4000)
        self.assertIs(self.game.current_player, self.ivan)

    def test_penalty_draw_names_the_rival_who_caused_it(self):
        actions.do_play_card(MagicMock(), self.alice, 'r_draw')
        self.clock.wait(2)
        actions.do_draw(MagicMock(), self.ivan)
        draw = self.events('draw')[0]
        self.assertEqual((draw['p'], draw['r'], draw['src'], len(draw['cs']), draw['h']), ('b', 'pen', 'a', 2, 5))
        self.assertEqual(draw['th'], 2000)
        self.assertEqual(self.ivan.cards[-2:], [c.from_str(card) for card in draw['cs']])

    def test_chosen_draw_and_pass(self):
        self.game.current_player = self.ivan
        self.game.last_card = c.Card(c.RED, '5')
        actions.do_draw(MagicMock(), self.ivan)
        rivalry_log.passed(self.game, self.ivan)
        draw, passed = self.events()[1:]
        self.assertEqual((draw['r'], len(draw['cs'])), ('vol', 1))
        self.assertEqual(draw.get('ok') == 1, self.ivan.cards[-1] in self.ivan.playable_cards())
        self.assertEqual((passed['k'], passed['h']), ('pass', 4))

    def test_caught_bluff(self):
        self.alice.cards.append(c.Card(None, None, c.DRAW_FOUR))
        actions.do_play_card(MagicMock(), self.alice, 'draw_four')
        self.assertEqual(self.events('play')[0]['bl'], 1)
        # Alice still holds two reds and one blue, so red is the smart pick
        self.game.choose_color(c.RED)
        color = self.events('color')[0]
        self.assertEqual((color['p'], color['co'], color.get('sm'), color.get('au')), ('a', 'r', 1, None))
        actions.do_call_bluff(MagicMock(), self.ivan)
        chal = self.events('chal')[0]
        self.assertEqual((chal['p'], chal['vs'], chal['ok']), ('b', 'a', 1))
        draw = self.events('draw')[0]
        self.assertEqual((draw['p'], draw['r'], draw['src'], len(draw['cs'])), ('a', 'bluff', 'b', 4))

    def test_other_players_are_left_out(self):
        xena = Player(self.game, stubs.MockUser(X, "Xena"))
        xena.cards = [c.Card(c.RED, '1'), c.Card(c.RED, '2')]
        self.game.current_player = xena
        actions.do_play_card(MagicMock(), xena, 'r_1')
        self.assertEqual(self.events('play'), [])

    def test_timeout_with_an_automatic_color(self):
        self.game.last_card = c.Card(None, None, c.CHOOSE)
        self.game.choosing_color = True
        actions.do_skip(MagicMock(), self.alice)
        kinds = [(e['k'], e.get('au'), e.get('r')) for e in self.events()[1:]]
        self.assertEqual(kinds, [('time', None, None), ('color', 1, None), ('draw', None, 'time')])

    def test_finish_closes_the_log_and_takes_it_off_the_game(self):
        self.clock.wait(30)
        log = rivalry_log.finish(self.game, A, 'win')
        self.assertIsNone(self.game.rivalry_log)
        self.assertEqual(log['ev'][-1], {'k': 'end', 't': 30000, 'n': 0, 'p': 'a', 'o': 'win'})
        self.assertIsNone(rivalry_log.finish(self.game, A, 'win'))

    def test_a_broken_log_never_breaks_the_move(self):
        self.game.rivalry_log = {'ev': 'not a list'}
        with self.assertLogs('rivalry_log', 'ERROR'):
            actions.do_play_card(MagicMock(), self.alice, 'r_7')
        self.assertIsNone(self.game.rivalry_log)
        self.assertIs(self.game.current_player, self.ivan)
        self.assertEqual(len(self.alice.cards), 2)

    def test_the_log_survives_a_restart(self):
        actions.do_play_card(MagicMock(), self.alice, 'r_7')
        gm = GameManager()
        restored = gm._deserialize_game(json.loads(json.dumps(gm._serialize_game(self.game))))
        self.assertEqual(restored.rivalry_log, self.game.rivalry_log)


def log_of(*events):
    deal = {'k': 'deal', 't': 0, 'n': 0, 'h': {'a': ['r_1', 'r_draw', 'colorchooser'], 'b': ['g_1', 'g_skip', 'y_2']},
            'fc': 'r_5', 'first': 'a'}
    return {'v': 1, 'ev': [deal] + list(events)}


class TestSummary(unittest.TestCase):

    def test_summarize_log(self):
        log = log_of(
            {'k': 'play', 'p': 'a', 'c': 'r_draw', 'h': 2, 't': 1, 'n': 0, 'th': 3000},
            {'k': 'draw', 'p': 'b', 'r': 'pen', 'cs': ['b_1', 'b_2'], 'src': 'a', 'h': 5, 't': 2, 'n': 1, 'th': 1000},
            {'k': 'play', 'p': 'a', 'c': 'colorchooser', 'h': 1, 't': 3, 'n': 2, 'th': 5000},
            {'k': 'color', 'p': 'a', 'co': 'r', 'sm': 1, 't': 4, 'n': 2, 'th': 1000},
            {'k': 'draw', 'p': 'b', 'r': 'vol', 'cs': ['r_9'], 'ok': 1, 'h': 6, 't': 5, 'n': 3, 'th': 2000},
            {'k': 'play', 'p': 'b', 'c': 'r_9', 'h': 5, 't': 6, 'n': 3, 'th': 500},
            {'k': 'play', 'p': 'a', 'c': 'r_1', 'h': 0, 't': 7, 'n': 4, 'th': 2500},
            {'k': 'end', 'p': 'a', 'o': 'win', 't': 8, 'n': 5},
        )
        d = rs.summarize_log(log)
        self.assertEqual((d['first'], d['fc'], d['fin'], d['stack'], d['ms']), ('a', 'r_5', 'r_1', 2, 8))
        self.assertEqual(d['deal'], {'a': [1, 1], 'b': [1, 0]})
        a, b = d['a'], d['b']
        self.assertEqual((a['pl'], a['ty'], a['co']), (3, [1, 0, 0, 1, 1, 0], [2, 0, 0, 0]))
        self.assertEqual((a['dealt'], a['uno'], a['pk'], a['sm'], a['th']), (2, 1, [1, 0, 0, 0], 1, [11500, 4, 5000]))
        self.assertEqual((b['fd'], b['dv'], b['dk'], b['mx'], b['back']), (2, 1, 1, 6, 5))
        self.assertEqual((a['ahead'], b['ahead'], d['samples']), (6, 0, 6))

    def test_penalty_on_one_card_counts_as_denied(self):
        log = log_of(
            {'k': 'play', 'p': 'a', 'c': 'r_1', 'h': 2, 't': 1, 'n': 0},
            {'k': 'play', 'p': 'a', 'c': 'r_draw', 'h': 1, 't': 2, 'n': 1},
            {'k': 'draw', 'p': 'a', 'r': 'bluff', 'cs': ['b_1', 'b_2', 'b_3', 'b_4'], 'src': 'b', 'h': 5, 't': 3, 'n': 1},
        )
        d = rs.summarize_log(log)
        self.assertEqual((d['a']['den'], d['a']['fd'], d['b']['dealt'], d['fin']), (1, 4, 4, None))

    def test_card_types(self):
        self.assertEqual([rs.card_type(card) for card in ('r_5', 'g_skip', 'b_reverse', 'y_draw', 'colorchooser', 'draw_four')],
                         list(rs.CARD_TYPES))


class TestReplayExport(unittest.TestCase):

    def test_replays_are_written_once_and_removed_with_their_result(self):
        T0 = datetime(2026, 10, 28, 12, 0)
        rivals = rs.Rivals([(A, "Alice"), (I, "Ivan")])
        rows = [rs.Result(1, 1, T0, A, I, 'imported'),
                rs.Result(7, 1, T0 + timedelta(minutes=5), I, A, 'win', T0 + timedelta(minutes=1), 'wild', 2, 30, 4)]
        log = log_of({'k': 'end', 'p': 'b', 'o': 'win', 't': 240000, 'n': 30})
        seasons = [rs.Season(1, "2026", T0)]
        with tempfile.TemporaryDirectory() as directory:
            rivalry.write_exports(directory, rivals, seasons, rows, rs.timezone.utc, logs={7: log}, pacing=1.5)
            with open(os.path.join(directory, 'games', '7.json'), encoding='utf-8') as f:
                replay = json.load(f)
            self.assertEqual((replay['n'], replay['winner'], replay['pacing']), (2, 'b', 1.5))
            self.assertEqual(replay['events'], log['ev'])
            self.assertTrue(os.path.exists(os.path.join(directory, 'replay.html')))
            with open(os.path.join(directory, 'scores.json'), encoding='utf-8') as f:
                matches = json.load(f)['matches']
            self.assertNotIn('g', matches[0])
            self.assertEqual((matches[1]['g'], matches[1]['d']['ms']), (7, 240000))

            rivalry.write_exports(directory, rivals, seasons, rows[:1], rs.timezone.utc, logs={7: log})
            self.assertEqual(os.listdir(os.path.join(directory, 'games')), [])


# Runs in a fresh interpreter so the real Pony is used (the other tests may stub it)
DB_SCRIPT = r'''
import os, sys
from unittest.mock import MagicMock, patch
sys.path.insert(0, sys.argv[1])
import pony.orm
import test.test_rate_limit_mitigations as stubs
import card as c, rivalry, rivalry_log
from database import bind_database
from game import Game
from player import Player
from rivalry_models import RivalryGameLog, RivalryMatch

bind_database(os.path.join(sys.argv[2], 'uno.sqlite3'))
with patch.multiple(rivalry_log, ENABLED=True, KEYS={111: 'a', 222: 'b'}, RIVALRY_CHAT_ID=None), \
        patch.multiple(rivalry, ENABLED=True, RIVAL_IDS={111, 222}, RIVALRY_CHAT_ID=None,
                       RIVALRY_PLAYERS=[(111, 'Alice'), (222, 'Ivan')]), \
        patch.object(rivalry, 'dispatcher', MagicMock()):
    game = Game(stubs.MockChat(-1))
    alice, ivan = Player(game, stubs.MockUser(111, 'Alice')), Player(game, stubs.MockUser(222, 'Ivan'))
    game.started, game.current_player, game.last_card = True, alice, c.Card(c.RED, '5')
    ivan.cards = [c.Card(c.GREEN, '1')]
    rivalry_log.start(game)
    rivalry.record_first_place(MagicMock(), game, alice.user)
    with pony.orm.db_session:
        match = RivalryMatch.select().first()
        print(match.winner_id, RivalryGameLog.get(match_id=match.id).events.count('"k":"end"'))
'''


class TestStoredLog(unittest.TestCase):

    def test_a_recorded_result_keeps_its_log(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as directory:
            run = subprocess.run([sys.executable, '-c', DB_SCRIPT, root, directory], cwd=root,
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout.split(), [str(A), '1'])


if __name__ == '__main__':
    unittest.main()
