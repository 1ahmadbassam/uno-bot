import json
import runpy
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import test.test_rate_limit_mitigations as stubs
import test.test_fairness_proof as proofs
import bot
import chat_setting
import modes
from game import Game
from game_manager import GameManager
from player import Player
from test.test_lobby_commands import LobbyTest, CREATOR, ADMIN, STRANGER


class TestSelectiveRandom(unittest.TestCase):
    def test_new_games_use_group_override_including_explicit_all(self):
        manager = GameManager()
        with patch.object(manager, '_persist_chat'), patch.object(manager, 'ensure_chat_loaded'), \
                patch('game.DEFAULT_GAMEMODE', 'classic'), patch('game.SELECTRANDOM_GAMEMODES', ('storm',)), \
                patch('game_manager.chat_setting.selective_random_modes') as saved:
            for pool in (['r8p'], []):
                saved.return_value = pool
                game = manager.new_game(stubs.MockChat(-1))
                self.assertEqual((game.mode, game.selectrandom_modes), ('selectrandom', pool))
            saved.return_value = None
            other = manager.new_game(stubs.MockChat(-2))
            self.assertEqual((other.mode, other.selectrandom_modes), ('classic', ['storm']))

    def test_group_override_survives_database_reopen(self):
        setup = textwrap.dedent('''
            import sys
            import chat_setting
            from database import bind_database
            bind_database(sys.argv[1])
        ''')
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / 'settings.sqlite3')
            for script in (
                "assert chat_setting.selective_random_modes(-1) is None\n"
                "chat_setting.save_selective_random_modes(-1, ['r8p', 'storm'])\n"
                "chat_setting.save_selective_random_modes(-2, [])\n",
                "assert chat_setting.selective_random_modes(-1) == ['r8p', 'storm']\n"
                "assert chat_setting.selective_random_modes(-2) == []\n"
                "assert chat_setting.selective_random_modes(-3) is None\n"
                "chat_setting.save_selective_random_modes(-1, ['classic'])\n",
                "assert chat_setting.selective_random_modes(-1) == ['classic']\n"
                "assert chat_setting.selective_random_modes(-2) == []\n"
            ):
                result = subprocess.run([sys.executable, '-c', setup + script, db_path],
                                        cwd=Path(__file__).resolve().parent.parent,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def config(self, value):
        with patch('builtins.open', mock_open(read_data=json.dumps(value))):
            return runpy.run_path(str(Path(__file__).resolve().parent.parent / 'config.py'))['SELECTRANDOM_GAMEMODES']

    def test_config_defaults_and_validation(self):
        self.assertEqual(self.config({}), ())
        self.assertEqual(self.config({'selectrandom_gamemodes': ['r8p', 'storm', 'r8p']}), ('storm', 'r8p'))
        for invalid in (None, 'storm', {}, [1], ['random'], ['selectrandom'], ['typo']):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'selectrandom_gamemodes'):
                self.config({'selectrandom_gamemodes': invalid})

    def test_default_mode_and_pool(self):
        with patch('game.DEFAULT_GAMEMODE', 'selectrandom'), patch('game.SELECTRANDOM_GAMEMODES', ('storm', 'r8p')):
            game = Game(stubs.MockChat(-1))
        self.assertEqual((game.mode, game.selectrandom_modes), ('selectrandom', ['storm', 'r8p']))
        choices = []
        bot._add_mode_results(choices)
        self.assertIn('mode_selectrandom', [choice.id for choice in choices])

    def test_pool_has_one_position_per_mode_and_overrides_random_exclusions(self):
        for i, expected in enumerate(('storm', 'r8p')):
            game = Game(stubs.MockChat(-1))
            game.mode = 'selectrandom'
            game.selectrandom_modes = ['r8p', 'storm', 'r8p']
            game.rng.use_system()
            with patch('game.RANDOM_GAMEMODE_EXCLUSIONS', {'storm', 'r8p'}), patch.object(game.rng, 'randbelow', return_value=i) as pick:
                game.pick_random_mode()
            pick.assert_called_once_with(2)
            self.assertEqual(game.mode, expected)
            self.assertEqual(game.rng.ops[-1], ['m', ['storm', 'r8p'], expected])
            game.abort_start()
            self.assertEqual(game.mode, 'selectrandom')

    def test_default_pool_matches_random(self):
        game = Game(stubs.MockChat(-1))
        game.mode = 'selectrandom'
        game.selectrandom_modes = []
        game.rng.use_system()
        for user_id in (1, 2):
            Player(game, stubs.MockUser(user_id, str(user_id)))
        with patch('game.RANDOM_GAMEMODE_EXCLUSIONS', set(modes.MODES) - {'wild'}):
            game.start()
        self.assertEqual(game.mode, 'wild')
        self.assertEqual(next(op for op in game.rng.ops if op[0] == 'm'), ['m', ['wild'], 'wild'])

    def test_pool_survives_restart_and_failed_start(self):
        game = Game(stubs.MockChat(-1))
        game.mode, game.selectrandom_modes = 'selectrandom', ['r8p']
        manager = GameManager()
        game = manager._deserialize_game(manager._serialize_game(game))
        self.assertEqual((game.mode, game.selectrandom_modes), ('selectrandom', ['r8p']))
        game.rng.use_system()
        game.pick_random_mode()
        game = manager._deserialize_game(manager._serialize_game(game))
        game.abort_start()
        self.assertEqual((game.mode, game.selectrandom_modes), ('selectrandom', ['r8p']))

    @unittest.skipUnless(shutil.which('node'), 'Node.js is not installed')
    def test_filtered_proof_checks_in_python_and_browser(self):
        with patch('game.SELECTRANDOM_GAMEMODES', ('storm', 'r8p')):
            game = proofs.play_a_game('selectrandom', players=2, max_turns=30, rng=proofs.seeds_rng(['a', 'b']))
        proof = game.rng.proof()
        self.assertEqual(next(op[1] for op in proof['ops'] if op[0] == 'm'), ['storm', 'r8p'])
        self.assertIsNone(proofs.check(proof)[0])
        self.assertEqual(proofs.TestReplayPageChecker.run_page(self, {
            'selective': {'proof': proof, 'whole': True}})['selective'], {'ok': True})


class TestSelectiveRandomCommand(LobbyTest):
    def setUp(self):
        super().setUp()
        self.settings = {}
        setting_model = MagicMock()
        setting_model.get.side_effect = lambda id: self.settings.get(id)
        def create_setting(**kwargs):
            from types import SimpleNamespace
            setting = SimpleNamespace(**kwargs)
            self.settings[setting.id] = setting
            return setting
        setting_model.side_effect = create_setting
        setting_patch = patch.object(chat_setting, 'ChatRandomSetting', setting_model)
        setting_patch.start()
        self.addCleanup(setting_patch.stop)
        self.command(bot.new_game, CREATOR)
        self.game = self.games()[0]

    def select(self, user=CREATOR, args=('r8p,', 'storm', 'r8p')):
        import inspect
        update, context = MagicMock(), MagicMock()
        update.message.chat, update.message.from_user = self.chat, user
        context.bot, context.args = self.bot, list(args)
        inspect.unwrap(bot.selectrandom_command)(update, context)

    def test_creator_or_admin_sets_pool_and_mode(self):
        for user in (CREATOR, ADMIN):
            self.select(user)
            self.assertEqual((self.game.mode, self.game.selectrandom_modes), ('selectrandom', ['storm', 'r8p']))
            self.assertIn('storm, r8p', self.last_text())
        self.select(args=('all',))
        self.assertEqual(self.game.selectrandom_modes, [])

    def test_command_persists_across_new_games_and_overrides_changed_json(self):
        self.select()
        with patch('game.SELECTRANDOM_GAMEMODES', ('wild',)), patch('game.DEFAULT_GAMEMODE', 'classic'):
            self.command(bot.new_game, CREATOR)
            self.game = self.games()[0]
            self.assertEqual((self.game.mode, self.game.selectrandom_modes), ('selectrandom', ['storm', 'r8p']))
            self.select(args=('all',))
            self.command(bot.new_game, CREATOR)
            game = self.games()[0]
            self.assertEqual((game.mode, game.selectrandom_modes), ('selectrandom', []))

    def test_rejects_unauthorized_started_or_committed_lobbies(self):
        before = self.game.mode
        self.select(STRANGER)
        self.assertEqual(self.game.mode, before)
        self.assertEqual(self.settings, {})
        self.game.started = True
        self.select()
        self.assertEqual(self.game.mode, before)
        self.game.started = False
        self.game.rng.seeds_deadline = 123
        self.select()
        self.assertEqual(self.game.mode, before)

    def test_invalid_input_and_usage_do_not_change_pool(self):
        self.select()
        for args in ((), ('typo',), ('random',), ('all', 'storm')):
            self.select(args=args)
            self.assertEqual(self.game.selectrandom_modes, ['storm', 'r8p'])
            self.assertEqual(chat_setting.selective_random_modes(self.chat.id), ['storm', 'r8p'])

    def test_private_chat_and_missing_lobby(self):
        self.chat.type = 'private'
        self.select()
        self.assertIn('group', self.last_text())
        self.chat.type = 'group'
        self.gm.chatid_games.clear()
        self.select()
        self.assertIn('no running game', self.last_text())
