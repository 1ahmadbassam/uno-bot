import json
import os
import shutil
import subprocess
import tempfile
import unittest
from collections import Counter
from unittest.mock import patch

import test.test_rate_limit_mitigations as stubs  # noqa: F401

import card as c
import modes
import results
import rivalry_stats as rs
from deck import DECKS, Deck
from game_manager import GameManager
from test.test_crazy import game_of
from test.test_fairness_proof import DECKS_JS, FAIR_JS, INDEX_HTML, check, play_a_game
from test.test_rivalry_log import log_of
from utils import display_color_group, make_color_keyboard


class TestRainbow(unittest.TestCase):
    def test_mode_selection_sends_requested_message(self):
        choices = []
        results.add_mode(choices, modes.RAINBOW)
        self.assertEqual(choices[0].id, 'mode_rainbow')
        self.assertEqual(choices[0].input_message_content.message_text, 'Over the Rainbow 🌈')
        self.assertEqual(modes.RAINBOW.hand_size, 7)
        self.assertTrue(modes.RAINBOW.bluff)
        self.assertFalse(modes.RAINBOW.auto_skip)

    def test_six_colors_numbers_one_through_six_and_classic_actions(self):
        deck = Deck()
        deck.fill('rainbow')
        cards = Counter(map(str, deck.cards))
        self.assertEqual(len(deck.cards), 116)
        for color in c.ALL_COLORS:
            for value in ('1', '2', '3', '4', '5', '6', 'draw', 'reverse', 'skip'):
                self.assertEqual(cards[color + '_' + value], 2)
            for value in ('0', '7', '8', '9'):
                self.assertNotIn(color + '_' + value, cards)
        self.assertEqual((cards[c.CHOOSE], cards[c.DRAW_FOUR]), (4, 4))

    def test_number_range_can_expand_in_shared_deck_spec(self):
        spec = dict(DECKS['rainbow'], values=[
            {'values': ['0'], 'copies': 1},
            {'values': [str(n) for n in range(1, 10)], 'copies': 2},
            {'values': ['draw', 'reverse', 'skip'], 'copies': 2}])
        game, _ = game_of('rainbow', 'A', 'B')
        with patch.dict(DECKS, rainbow=spec):
            deck = Deck()
            deck.fill('rainbow')
            self.assertEqual(len(deck.cards), 158)
            for card in deck.cards:
                self.assertTrue(c.sticker_for(card, game))
                self.assertTrue(c.sticker_for(card, game, playable=False))

    def test_extra_colors_follow_classic_matching_and_bluff_rules(self):
        game, (a, b) = game_of('rainbow', 'A', 'B')
        game.last_card = c.from_str('p_3')
        a.cards = [c.from_str(s) for s in ('p_6', 'o_3', 'o_4', 'draw_four')]
        self.assertTrue(a._card_playable(a.cards[0]))
        self.assertTrue(a._card_playable(a.cards[1]))
        self.assertFalse(a._card_playable(a.cards[2]))
        self.assertTrue(a._card_playable(a.cards[3]))
        a.play(a.cards[3])
        self.assertTrue(a.bluffing)

    def test_chooser_uses_mode_specific_artwork_in_both_states(self):
        chooser = c.Card(None, None, c.CHOOSE)
        for mode in ('rainbow', 'classic'):
            game, _ = game_of(mode, 'A', 'B')
            for playable in (True, False):
                options = []
                results.add_card(game, chooser, options, playable)
                source = ((c.STICKERS_RAINBOW if playable else c.STICKERS_RAINBOW_GREY)
                          if mode == 'rainbow' else (c.STICKERS if playable else c.STICKERS_GREY))
                self.assertEqual(options[0].sticker_file_id, source[c.CHOOSE])

    def test_keyboard_labels_and_saved_game_support_both_new_colors(self):
        game, _ = game_of('rainbow', 'A', 'B')
        rows = make_color_keyboard(game.chat.id, game).inline_keyboard
        self.assertEqual([button.text for row in rows[1:] for button in row],
                         ['❤️ Red', '💙 Blue', '💚 Green', '💛 Yellow', '💜 Purple', '🧡 Orange'])
        game.translate = True
        self.assertIn('Purple', display_color_group('p', game))
        self.assertIn('Orange', display_color_group('o', game))
        game.last_card = c.Card(c.ORANGE, None, c.CHOOSE)
        gm = GameManager()
        restored = gm._deserialize_game(json.loads(json.dumps(gm._serialize_game(game))))
        self.assertEqual(restored.mode, 'rainbow')
        self.assertEqual(restored.colors, c.ALL_COLORS)
        self.assertEqual(restored.last_card.color, c.ORANGE)
        self.assertEqual(c.label(c.from_str('p_draw')), '💜+2')

    def test_statistics_count_new_colors_in_hands_plays_draws_and_picks(self):
        log = log_of(
            {'k': 'play', 'p': 'a', 'c': 'p_1', 'h': 1, 't': 1, 'n': 0},
            {'k': 'draw', 'p': 'b', 'r': 'vol', 'cs': ['o_6', 'p_skip'], 'h': 3, 't': 2, 'n': 1},
            {'k': 'color', 'p': 'a', 'co': 'o', 't': 3, 'n': 2},
            {'k': 'end', 'p': 'a', 'o': 'win', 't': 4, 'n': 3})
        log['ev'][0]['h'] = {'a': ['p_1', 'o_2'], 'b': ['o_3']}
        summary = rs.summarize_log(log)
        self.assertEqual(summary['a']['hc'], [0, 0, 0, 0, 1, 1])
        self.assertEqual(summary['a']['co'], [0, 0, 0, 0, 1, 0])
        self.assertEqual(summary['a']['pk'], [0, 0, 0, 0, 0, 1])
        self.assertEqual(summary['b']['dc'], [0, 0, 0, 0, 1, 1])
        rivals = rs.Rivals([(1, 'A'), (2, 'B')])
        result = rs.Result(1, 1, rs.datetime(2026, 11, 1), 1, 2, 'win', mode='rainbow')
        data = rs.scores_data(rivals, [], [result], rs.timezone.utc)
        self.assertEqual(data['decks']['rainbow'], 'rainbow')
        self.assertIn('rainbow', rs.game_stats_text(rivals, 1, result, log, rs.timezone.utc,
                                                 lambda name: c.label(c.from_str(name))))

    def test_proof_replays_complete_rainbow_game(self):
        game = play_a_game('rainbow', players=2, max_turns=1500)
        self.assertIsNone(check(game.rng.proof())[0])

    @unittest.skipUnless(shutil.which('node'), 'Node.js is not installed')
    def test_browser_checker_replays_rainbow(self):
        proof = play_a_game('rainbow', players=2, max_turns=1500).rng.proof()
        script = ''
        for path in (DECKS_JS, FAIR_JS):
            with open(path, encoding='utf-8') as f:
                script += f.read() + '\n'
        script += ('(async function () { var p = %s; '
                   'console.log(JSON.stringify(await replayOps(p, await fairStream(p.seed, p.beacon)))); '
                   '})();') % json.dumps(proof)
        self.assertIsNone(self.run_browser_script(script))

    def run_browser_script(self, script):
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
            f.write(script)
        try:
            result = subprocess.run(['node', f.name], capture_output=True, text=True, encoding='utf-8', timeout=60)
        finally:
            os.unlink(f.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip())

    @unittest.skipUnless(shutil.which('node'), 'Node.js is not installed')
    def test_website_color_expectations_mix_four_and_six_color_decks(self):
        with open(INDEX_HTML, encoding='utf-8') as f:
            page = f.read()
        with open(DECKS_JS, encoding='utf-8') as f:
            script = f.read() + '\n'
        with open(FAIR_JS, encoding='utf-8') as f:
            script += f.read() + '\n'
        script += page[page.index('var CARD_TYPES ='):page.index('var COL =')]
        script += page[page.index('function cardType(card)'):page.index('// Totals over the games')]
        script += page[page.index('var FAIR_MIN_GAMES ='):page.index('function fairBadge(m)')]
        games = []
        for mode, colors in (('classic', c.COLORS), ('rainbow', c.ALL_COLORS)):
            log = log_of({'k': 'end', 'p': 'a', 'o': 'win', 't': 1, 'n': 1})
            log['ev'][0]['h'] = {'a': [color + '_1' for color in colors], 'b': []}
            summary = rs.summarize_log(log)
            if mode == 'classic':
                # Previously exported games only had four entries per color count.
                for player in ('a', 'b'):
                    for field in ('hc', 'dc', 'co', 'pk'):
                        summary[player][field] = summary[player][field][:4]
            games.append({'mode': mode, 'd': summary})
        script += '''
function sum(a) { return a.reduce(function (s, n) { return s + n; }, 0); }
function sw() { return ''; }
function f1(n) { return n.toFixed(1); }
console.log(JSON.stringify(fairTests(%s).find(function (t) { return t.name === 'Colors dealt'; })));
''' % json.dumps(games)
        out = self.run_browser_script(script)
        self.assertEqual(out['games'], 2)
        self.assertEqual(out['expect'], ' <span class="m">\u00b7</span> '.join(
            color + ' ' + count for color, count in zip(
                ('Red', 'Blue', 'Green', 'Yellow', 'Purple', 'Orange'),
                ('2.0', '2.0', '2.0', '2.0', '1.0', '1.0'))))
        self.assertIsNone(out['p'])
        self.assertIn('20 contributing games', out['pending'])
