import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Installs the module stubs needed to import the game modules
import test.test_rate_limit_mitigations as stubs  # noqa: F401

import bot
import card as c
import deck
import modes
import rivalry_stats
from bot import _add_mode_results
from deck import Deck, DECKS
from game import Game
from player import Player
from test.test_fairness_proof import DECKS_JS, FAIR_JS, check, play_a_game
from utils import make_color_keyboard

# A deck with more colors, as a mode with extra colors would have
SIX = {"colors": ["r", "b", "g", "y", "p", "o"],
       "values": [{"values": ["1", "2", "3", "4", "5", "6", "draw", "reverse", "skip"], "copies": 2}],
       "specials": {"colorchooser": 4, "draw_four": 4}}


def six_colors():
    """Patches in a mode 'six' that plays with SIX"""
    mode = modes.Mode('six', 'Six', 'Six', deck='six', hand_size=8)
    return patch.dict(DECKS, {'six': SIX}), patch.dict(modes.MODES, {'six': mode})


class TestDecks(unittest.TestCase):

    def test_decks_are_the_ones_the_bot_always_dealt(self):
        classic, wild = Deck(), Deck()
        classic.fill('classic')
        wild.fill('wild')
        self.assertEqual(len(classic.cards), 108)
        self.assertEqual(Counter(map(str, classic.cards))['r_0'], 1)
        self.assertEqual(Counter(map(str, classic.cards))['g_skip'], 2)
        self.assertEqual(Counter(map(str, classic.cards))['draw_four'], 4)
        self.assertEqual(len(wild.cards), 4 * 8 * 4 + 12)
        self.assertEqual(Counter(map(str, wild.cards))['b_5'], 4)
        self.assertNotIn('b_6', map(str, wild.cards))
        self.assertEqual(Counter(map(str, wild.cards))['colorchooser'], 6)

    def test_every_mode_has_a_deck_with_stickers_for_each_card(self):
        for mode in modes.MODES.values():
            self.assertIn(mode.deck, DECKS, mode.id)
            d = Deck()
            d.fill(mode.deck)
            for card in d.cards:
                self.assertIn(str(card), c.STICKERS, (mode.id, str(card)))
                self.assertIn(str(card), c.STICKERS_GREY, (mode.id, str(card)))
            for color in DECKS[mode.deck]['colors']:
                self.assertIn(color, c.COLOR_ICONS)

    def test_fill_is_logged_with_the_deck_name(self):
        game = Game(SimpleNamespace(id=-1, title="Test", type="group"))
        game.rng.use_system()
        game.mode = 'wild'
        game.deck.fill(game.rules.deck)
        self.assertEqual(game.rng.ops[-1], ['f', 'wild'])

    def test_decks_js_is_plain_json(self):
        self.assertEqual(deck.load_decks(DECKS_JS), DECKS)


class TestModes(unittest.TestCase):

    def test_unknown_or_missing_mode_plays_classic(self):
        for mode_id in (None, 'text', 'nonsense'):
            self.assertIs(modes.get(mode_id), modes.CLASSIC)
        self.assertTrue(modes.get('fast').auto_skip)
        self.assertFalse(modes.get('classic').auto_skip)
        self.assertEqual(modes.get('wild').deck, 'wild')

    def test_inline_menu_offers_every_mode(self):
        results = []
        _add_mode_results(results)
        self.assertEqual([r.id for r in results], ['mode_' + m for m in modes.MODES] + ['mode_random'])

    def test_game_colors_come_from_the_deck(self):
        game = Game(SimpleNamespace(id=-1, title="Test", type="group"))
        self.assertEqual(game.colors, c.COLORS)
        decks, registry = six_colors()
        with decks, registry:
            game.mode = 'six'
            self.assertEqual(game.colors, tuple(SIX['colors']))

    def test_color_keyboard_has_a_button_per_color(self):
        game = SimpleNamespace(colors=('r', 'b', 'g', 'y', 'p', 'o'), translate=False)
        rows = make_color_keyboard(-1, game).inline_keyboard
        self.assertEqual([[b.callback_data for b in row] for row in rows[1:]],
                         [['color:-1:r', 'color:-1:b'], ['color:-1:g', 'color:-1:y'], ['color:-1:p', 'color:-1:o']])

    def test_scores_json_says_which_deck_each_mode_uses(self):
        rivals = SimpleNamespace(a_id=1, b_id=2, a_name='A', b_name='B')
        data = rivalry_stats.scores_data(rivals, [], [], rivalry_stats.timezone.utc)
        self.assertEqual(data['decks'], {'classic': 'classic', 'fast': 'classic', 'wild': 'wild'})


class TestModeRules(unittest.TestCase):

    def test_hand_size_and_bot_colors_follow_the_mode(self):
        decks, registry = six_colors()
        with decks, registry:
            game = play_a_game('six', players=2)
        ops = game.rng.ops
        self.assertIn(['f', 'six'], ops)
        picked = [op[1] for op in ops if op[0] == 'c']
        self.assertTrue(picked)
        self.assertTrue(set(picked) <= set(SIX['colors']))
        # Both hands of 8 come right after the first card
        deal = [op for op in ops if op[0] == 'd'][1:17]
        self.assertEqual(Counter(op[2] for op in deal), {'P1': 8, 'P2': 8})

    @unittest.skipUnless(shutil.which('node'), "Node.js is not installed")
    def test_page_checker_replays_a_deck_with_more_colors(self):
        decks, registry = six_colors()
        with decks, registry:
            proof = play_a_game('six', players=2).rng.proof()
        js = ''
        for path in (DECKS_JS, FAIR_JS):
            with open(path, encoding='utf-8') as f:
                js += f.read() + '\n'
        script = js + (
            "DECKS.six = %s;\n(async function () {\n  var p = %s;\n"
            "  var wrong = JSON.parse(JSON.stringify(p));\n"
            "  var r = await replayOps(p, await fairStream(p.seed, p.beacon));\n"
            "  wrong.ops.forEach(function (op) { if (op[0] === 'f') op[1] = 'classic'; });\n"
            "  var w = await replayOps(wrong, await fairStream(p.seed, p.beacon));\n"
            "  console.log(JSON.stringify([r, w]));\n})();\n") % (json.dumps(SIX), json.dumps(proof))
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
            f.write(script)
        try:
            result = subprocess.run(['node', f.name], capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(f.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        right, wrong = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertIsNone(right)
        self.assertIsNotNone(wrong)


class TestRandomMode(unittest.TestCase):

    def test_menu_and_mode_change_offer_random(self):
        results = []
        _add_mode_results(results)
        self.assertEqual(results[-1].id, 'mode_random')
        game = Game(SimpleNamespace(id=-1, title="Test", type="group"))
        with patch('bot.user_can_change_mode', return_value=True), patch('bot.gm'), patch('bot.send_async') as send_async:
            bot._change_mode(MagicMock(), SimpleNamespace(id=1, first_name='A', username=None), game, 'random')
            self.assertEqual(game.mode, 'random')
            self.assertEqual(send_async.call_args[1]['text'], "Gamemode changed to: Random")

            bot._change_mode(MagicMock(), SimpleNamespace(id=1, first_name='A', username=None), game, 'wild')
            self.assertEqual(game.mode, 'wild')
            self.assertEqual(send_async.call_args[1]['text'], "Gamemode changed to: Wild")

    def test_default_mode_can_be_random(self):
        with patch('game.DEFAULT_GAMEMODE', 'random'):
            game = Game(SimpleNamespace(id=-1, title="Test", type="group"))
        self.assertEqual(game.mode, 'random')

    def test_start_picks_a_real_mode_with_the_game_seed(self):
        picked = set()
        for _ in range(30):
            game = play_a_game('random', players=2, max_turns=5)
            self.assertIn(game.mode, modes.MODES)
            self.assertTrue(game.random_mode)
            ops = game.rng.ops
            pick = next(op for op in ops if op[0] == 'm')
            self.assertEqual(pick, ['m', list(modes.MODES), game.mode])
            # Picked after the seats and before the deck, which is the picked mode's
            self.assertLess(ops.index(next(op for op in ops if op[0] == 's')), ops.index(pick))
            self.assertEqual(ops[ops.index(pick) + 1], ['f', game.rules.deck])
            self.assertEqual(check(game.rng.proof())[0], None)
            picked.add(game.mode)
        self.assertEqual(picked, set(modes.MODES))

    def test_a_tampered_pick_fails_the_check(self):
        proof = play_a_game('random', players=2, max_turns=5).rng.proof()
        pick = next(op for op in proof['ops'] if op[0] == 'm')
        pick[2] = next(m for m in pick[1] if m != pick[2])
        self.assertEqual(check(proof)[0][:4], 'mode')

    def test_aborted_start_goes_back_to_random(self):
        game = Game(SimpleNamespace(id=-1, title="Test", type="group"))
        game.mode = 'random'
        Player(game, SimpleNamespace(id=1, first_name='A', username=None))
        game.start()
        self.assertIn(game.mode, modes.MODES)
        game.abort_start()
        self.assertEqual((game.mode, game.random_mode), ('random', False))

    def test_first_message_reveals_the_mode(self):
        game = Game(SimpleNamespace(id=-1, title="Test", type="group"))
        game.mode = 'random'
        for i in (1, 2):
            Player(game, SimpleNamespace(id=i, first_name='P%d' % i, username=None))
        sent = MagicMock()
        with patch('bot.gm'), patch('bot.start_player_countdown'), \
                patch('bot.dispatcher') as dispatcher:
            bot._deal_and_announce(sent, game, game.chat, None)
            dispatcher.run_async.call_args[0][0]()
        text = sent.sendMessage.call_args[1]['text']
        self.assertTrue(text.startswith("🎲 The random pick is %s!\n" % game.rules.title), text)

    def test_results_name_the_picked_mode(self):
        game = play_a_game('random', players=2, max_turns=5)
        rivals = SimpleNamespace(a_id=1, b_id=2, a_name='A', b_name='B')
        self.assertIn(game.mode, rivalry_stats.scores_data(rivals, [], [], rivalry_stats.timezone.utc)['decks'])
        self.assertNotIn('random', modes.MODES)

    @unittest.skipUnless(shutil.which('node'), "Node.js is not installed")
    def test_page_checker_replays_the_pick(self):
        proof = play_a_game('random', players=2, max_turns=5).rng.proof()
        tampered = copy.deepcopy(proof)
        pick = next(op for op in tampered['ops'] if op[0] == 'm')
        pick[2] = next(m for m in pick[1] if m != pick[2])
        js = ''
        for path in (DECKS_JS, FAIR_JS):
            with open(path, encoding='utf-8') as f:
                js += f.read() + '\n'
        script = js + (
            "(async function () {\n  var out = [];\n  var cases = %s;\n"
            "  for (var i = 0; i < cases.length; i++)\n"
            "    out.push(await replayOps(cases[i], await fairStream(cases[i].seed, cases[i].beacon)));\n"
            "  console.log(JSON.stringify(out));\n})();\n") % json.dumps([proof, tampered])
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
            f.write(script)
        try:
            result = subprocess.run(['node', f.name], capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(f.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        good, bad = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertIsNone(good)
        self.assertIn('game mode', bad)


if __name__ == '__main__':
    unittest.main()
