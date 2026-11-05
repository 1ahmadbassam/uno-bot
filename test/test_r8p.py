import copy
import inspect
import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import test.test_rate_limit_mitigations as stubs  # noqa: F401
import test.test_fairness_proof as proof_tests
import actions
import bot
import card as c
import modes
import r8p
import rivalry_log
import rivalry_stats as rs
import simple_commands
import volcano
from game import Game
from game_manager import GameManager
from player import Player
from test.test_fairness_proof import check, play_a_game, seeds_rng, user
from test.test_rivalry_log import A, I, LoggedGame


def game_with_pressure():
    game = Game(stubs.MockChat(-1))
    game.mode = 'r8p'
    game.rng.use_system()
    game.deck.fill('storm')
    players = [Player(game, stubs.MockUser(i, str(i))) for i in (1, 2)]
    game.current_player = players[0]
    game.last_card = c.Card(c.RED, '5')
    for p in players:
        p.cards = [c.Card(c.BLUE, '7')]
    game.started = True
    return game, players


class TestR8P(unittest.TestCase):
    def test_inline_picker_and_combined_rules(self):
        choices = []
        bot._add_mode_results(choices)
        result = next(r for r in choices if r.id == 'mode_r8p')
        self.assertEqual(result.input_message_content.message_text, 'Prepare your holes 🍆')
        self.assertEqual(modes.R8P.deck, 'storm')
        self.assertTrue(modes.R8P.volcano and modes.R8P.storm and modes.R8P.tornado)
        self.assertFalse(modes.R8P.bluff)

    def test_pressure_increases_on_turns_not_draws_and_saturates(self):
        game, players = game_with_pressure()
        with patch.object(game.rng, 'randbelow', return_value=0):
            game.turn()
            self.assertEqual(game.r8p_pressure, 700)
            game.turn()
            self.assertEqual(game.r8p_pressure, 900)
            for _ in range(46):
                game.turn()
            self.assertEqual(game.r8p_pressure, 10000)
            game.turn()
            self.assertEqual(game.r8p_pressure, 10000)
        with patch.object(game.rng, 'randbelow', side_effect=[9999, 0, 0, 0]):
            self.assertEqual(len(volcano.roll(game, players[0])), 3)
        self.assertEqual(game.r8p_pressure, 500)
        self.assertEqual(game.r8p_expected, 1.0)

    def test_random_rises_and_no_rise_before_start_or_during_uno_wait(self):
        game, players = game_with_pressure()
        game.started = False
        game.turn()
        self.assertEqual(game.r8p_pressure, 500)
        game.started, game.turns = True, 0
        with patch.object(game.rng, 'randbelow', side_effect=[400, 123]):
            game.turn()
            self.assertEqual(game.r8p_pressure, 1100)
            game.turn()
            self.assertEqual(game.r8p_pressure, 1423)
        game.hold_for_uno(players[0])
        game.turn()
        self.assertEqual(game.r8p_pressure, 1423)
        with patch.object(game.rng, 'randbelow', return_value=0):
            game.release_uno()
        self.assertEqual(game.r8p_pressure, 1623)

    def test_roll_boundary_and_no_five_eruption_limit(self):
        game, players = game_with_pressure()
        with patch.object(game.rng, 'randbelow', return_value=500):
            self.assertEqual(volcano.roll(game, players[0]), [])
            self.assertEqual(game.r8p_pressure, 500)
        with patch.object(game.rng, 'randbelow', return_value=499):
            self.assertTrue(r8p.roll(game))
        with patch.object(game.rng, 'randbelow', return_value=0):
            for _ in range(7):
                self.assertEqual(len(volcano.roll(game, players[0])), 3)
        self.assertEqual(game.volcano_eruptions, 7)
        self.assertEqual(game.volcano_rolls, 8)

    def test_pressure_and_expected_eruptions_survive_restart_and_abort_resets(self):
        game, _ = game_with_pressure()
        game.r8p_pressure, game.r8p_expected = 7300, 2.25
        manager = GameManager()
        restored = manager._deserialize_game(manager._serialize_game(game))
        self.assertEqual((restored.r8p_pressure, restored.r8p_expected), (7300, 2.25))
        restored.abort_start()
        self.assertEqual((restored.r8p_pressure, restored.r8p_expected), (500, 0))

    def test_eruption_sticker_is_available_after_the_fifth(self):
        game, players = game_with_pressure()
        with patch('shared_vars.dispatcher.run_async') as send, patch('r8p.roll', return_value=True):
            for _ in range(12):
                volcano.erupt(MagicMock(), game, players[0])
        self.assertEqual(game.volcano_eruptions, 12)
        self.assertEqual([call.kwargs['sticker'] for call in send.call_args_list], [volcano.R8P_STICKER] * 12)

    def test_help_can_be_sent_within_telegram_message_limit(self):
        with patch('simple_commands.send_async') as send:
            inspect.unwrap(simple_commands.modes)(MagicMock(), MagicMock())
        texts = [call.kwargs['text'] for call in send.call_args_list]
        self.assertTrue(any('R8P' in text for text in texts))
        self.assertTrue(all(len(text.encode('utf-16-le')) // 2 <= 4096 for text in texts))

    @unittest.skipUnless(shutil.which('node'), 'Node.js is not installed')
    def test_real_games_and_tampered_pressure_checked_by_both_verifiers(self):
        cases = {}
        for i in range(3):
            game = play_a_game('r8p', players=2, max_turns=100, rng=seeds_rng(['a', str(i)]))
            proof = game.rng.proof()
            self.assertIsNone(check(proof)[0])
            self.assertTrue(any(op[0] == 'rt' for op in proof['ops']))
            self.assertTrue(any(op[0] == 'rv' for op in proof['ops']))
            cases[str(i)] = {'proof': proof}
        for kind in ('rt', 'rv'):
            bad = copy.deepcopy(proof)
            op = next(op for op in bad['ops'] if op[0] == kind)
            op[-1 if kind == 'rt' else 1] += 1
            self.assertIsNotNone(check(bad)[0])
            cases[kind] = {'proof': bad}
        out = proof_tests.TestReplayPageChecker.run_page(self, cases)
        for i in range(3):
            self.assertIsNone(out[str(i)])
        self.assertIn('pressure', out['rt'])
        self.assertIn('pressure', out['rv'])


class TestR8PLog(LoggedGame):
    def setUp(self):
        super().setUp()
        self.game.mode = 'r8p'
        rivalry_log.start(self.game)

    def test_effects_are_exported_only_inside_r8p_and_shown_in_telegram(self):
        self.alice.cards.append(c.Card(c.RED, c.SWAP))
        actions.do_play_card(MagicMock(), self.alice, 'r_swap')
        self.ivan.cards.append(c.Card(None, None, c.DRAW_FOUR))
        actions.do_play_card(MagicMock(), self.ivan, 'draw_four')
        self.game.choose_color(c.RED)
        self.alice.cards.append(c.Card(c.RED, c.DRAW_TWO))
        actions.do_play_card(MagicMock(), self.alice, 'r_draw')
        with patch.object(self.game.rng, 'randbelow', return_value=0), patch('volcano.send_sticker'):
            actions.do_draw(MagicMock(), self.ivan)
        log = rivalry_log.finish(self.game, A, 'forfeit')
        d = rs.summarize_log(log)
        self.assertTrue(all(key not in d for key in ('vol', 'storm', 'tornado')))
        self.assertEqual(d['r8p']['storm']['a']['sw'], 1)
        self.assertEqual(d['r8p']['tornado']['a']['s2'], 1)
        self.assertEqual(d['r8p']['vol']['b'][0], 1)
        self.assertEqual(d['r8p']['vol']['x'], round(self.game.r8p_expected, 4))
        rivals = rs.Rivals([(A, 'Alice'), (I, 'Ivan')])
        result = SimpleNamespace(winner_id=A, loser_id=I, ended_at=rs.datetime.now(), mode='r8p',
                                 outcome='forfeit', turns=self.game.turns, loser_cards_left=len(self.ivan.cards))
        text = rs.game_stats_text(rivals, 1, result, log, rs.timezone.utc, str)
        self.assertIn('r8p', text)
        for metric in ('Swaps', '+2 on +4', 'Eruptions'):
            self.assertIn(metric, text)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is not installed')
    def test_real_logged_draws_match_replay_proof_after_restart(self):
        game = Game(stubs.MockChat(-1))
        game.mode = 'r8p'
        game.rng = seeds_rng(['Alice', 'Ivan'])
        game.deck.rng = game.rng
        players = [Player(game, user(uid, name)) for uid, name in ((A, 'Alice'), (I, 'Ivan'))]
        game.start()
        for p in players:
            p.draw_first_hand()
        rivalry_log.start(game)
        if game.choosing_color:
            game.choose_color(c.RED)
        with patch('volcano.send_sticker'):
            for _ in range(25):
                actions.do_draw(MagicMock(), game.current_player)
                game.turn()
        manager = GameManager()
        game = manager._deserialize_game(manager._serialize_game(game))
        with patch('volcano.send_sticker'):
            actions.do_draw(MagicMock(), game.current_player)
        log = rivalry_log.finish(game, A, 'forfeit')
        proof = game.rng.proof()
        out = proof_tests.TestReplayPageChecker.run_page(self, {'real': {'proof': proof, 'events': log['ev'], 'whole': True}})
        self.assertEqual(out['real'], {'ok': True})


if __name__ == '__main__':
    unittest.main()
