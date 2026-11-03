import json
import runpy
import shutil
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

import test.test_rate_limit_mitigations as stubs  # noqa: F401
import test.test_fairness_proof as proofs
import modes
from game import Game


class TestRandomExclusions(unittest.TestCase):
    def config(self, data):
        path = Path(__file__).resolve().parent.parent / 'config.py'
        with patch('builtins.open', mock_open(read_data=json.dumps(data))):
            return runpy.run_path(str(path))['RANDOM_GAMEMODE_EXCLUSIONS']

    def test_config_defaults_to_all_modes_and_reads_json_array(self):
        self.assertEqual(self.config({}), frozenset())
        self.assertEqual(self.config({'random_gamemode_exclusions': ['r8p', 'r8p', 'thunderstorm']}),
                         {'r8p', 'thunderstorm'})

    def test_invalid_configuration_fails_with_clear_message(self):
        for invalid in ('wild', None, {}, [1]):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'JSON array'):
                self.config({'random_gamemode_exclusions': invalid})
        with self.assertRaisesRegex(ValueError, 'Unknown mode IDs.*typo'):
            self.config({'random_gamemode_exclusions': ['typo']})
        with self.assertRaisesRegex(ValueError, 'at least one'):
            self.config({'random_gamemode_exclusions': list(modes.MODES)})

    def test_each_eligible_mode_has_one_roll_position_and_excluded_modes_stay_selectable(self):
        exclusions = {'wild', 'r8p', 'thunderstorm'}
        candidates = [mode for mode in modes.MODES if mode not in exclusions]
        for i, expected in enumerate(candidates):
            game = Game(stubs.MockChat(-1))
            game.mode = 'random'
            game.rng.use_system()
            with patch('game.RANDOM_GAMEMODE_EXCLUSIONS', exclusions), patch.object(game.rng, 'randbelow', return_value=i) as pick:
                game.pick_random_mode()
            pick.assert_called_once_with(len(candidates))
            self.assertEqual(game.mode, expected)
            self.assertEqual(game.rng.ops[-1], ['m', candidates, expected])
        for mode in exclusions:
            self.assertIn(mode, modes.CHOICES)
            game.set_mode(mode)
            self.assertEqual(game.rules.id, mode)

    def test_single_remaining_mode_and_empty_pool_guard(self):
        game = Game(stubs.MockChat(-1))
        game.rng.use_system()
        with patch('game.RANDOM_GAMEMODE_EXCLUSIONS', set(modes.MODES) - {'storm'}):
            game.pick_random_mode()
        self.assertEqual(game.mode, 'storm')
        self.assertEqual(game.rng.ops[-1], ['m', ['storm'], 'storm'])
        with patch('game.RANDOM_GAMEMODE_EXCLUSIONS', set(modes.MODES)):
            with self.assertRaisesRegex(ValueError, 'at least one'):
                game.pick_random_mode()

    @unittest.skipUnless(shutil.which('node'), 'Node.js is not installed')
    def test_filtered_game_proof_replays_in_python_and_browser(self):
        with patch('game.RANDOM_GAMEMODE_EXCLUSIONS', set(modes.MODES) - {'wild', 'thunderstorm'}):
            game = proofs.play_a_game('random', players=2, max_turns=30, rng=proofs.seeds_rng(['a', 'b']))
        proof = game.rng.proof()
        choice = next(op for op in proof['ops'] if op[0] == 'm')
        self.assertEqual(choice[1], ['wild', 'thunderstorm'])
        self.assertIsNone(proofs.check(proof)[0])
        self.assertEqual(proofs.TestReplayPageChecker.run_page(self, {
            'filtered': {'proof': proof, 'whole': True}})['filtered'], {'ok': True})
