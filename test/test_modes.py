import json
import os
import shutil
import subprocess
import tempfile
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch

# Installs the module stubs needed to import the game modules
import test.test_rate_limit_mitigations as stubs  # noqa: F401

import card as c
import deck
import modes
import rivalry_stats
from bot import _add_mode_results
from deck import Deck, DECKS
from game import Game
from test.test_fairness_proof import DECKS_JS, FAIR_JS, play_a_game
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
        self.assertEqual([r.id for r in results], ['mode_' + m for m in modes.MODES])

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


if __name__ == '__main__':
    unittest.main()
