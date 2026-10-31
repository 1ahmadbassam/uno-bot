import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Installs the module stubs needed to import the game modules
import test.test_rate_limit_mitigations as stubs  # noqa: F401

import actions
import card as c
import modes
import rivalry_log
import rivalry_stats as rs
import volcano
from game import Game
from game_manager import GameManager
from player import Player
from test.test_fairness_proof import DECKS_JS, FAIR_JS, check, play_a_game
from test.test_rivalry_log import A, I, LoggedGame, log_of


def volcano_game():
    game = Game(SimpleNamespace(id=-1, title="Test", type="group"))
    game.mode = 'volcano'
    game.rng.use_system()
    game.deck.fill('classic')
    p1 = Player(game, SimpleNamespace(id=1, first_name='A', username=None))
    Player(game, SimpleNamespace(id=2, first_name='B', username=None))
    game.started = True
    return game, p1


def rolls(ops):
    return [op for op in ops if op[0] == 'v']


class TestVolcanoMode(unittest.TestCase):

    def test_mode_plays_the_classic_deck_and_is_offered(self):
        mode = modes.get('volcano')
        self.assertTrue(mode.volcano)
        self.assertEqual((mode.deck, mode.hand_size, mode.auto_skip), ('classic', 7, False))
        self.assertEqual(mode.message, 'Ka-Boom! 🌋')
        self.assertFalse(any(m.volcano for m in modes.MODES.values() if m is not mode))

    def test_other_modes_never_roll(self):
        game, player = volcano_game()
        game.mode = 'classic'
        self.assertEqual(volcano.roll(game, player), [])
        self.assertEqual((game.volcano_rolls, rolls(game.rng.ops)), (0, []))

    def test_odds_fall_with_every_roll_and_stop_after_five_eruptions(self):
        game, player = volcano_game()
        game.rng.ops = []
        game.rng.source = 'system'
        hits = []
        # randbelow is 0 (an eruption) on the 1st, 3rd, ... calls of the volcano
        with patch.object(game.rng, 'randbelow', side_effect=lambda n: 0 if n % 2 == 0 else 1):
            for _ in range(30):
                hits.append(game.volcano_eruptions if volcano.roll(game, player) else 0)
        ops = rolls(game.rng.ops)
        self.assertEqual([op[1] for op in ops], list(range(10, 10 + len(ops))))
        self.assertEqual([h for h in hits if h], [1, 2, 3, 4, 5])
        self.assertEqual(game.volcano_eruptions, 5)
        # The fifth eruption is the last roll
        self.assertTrue(ops[-1][2])
        self.assertEqual(len(ops), game.volcano_rolls)

    def test_an_eruption_gives_three_cards_ahead_of_the_drawn_card(self):
        game, player = volcano_game()
        game.last_card = c.Card(c.RED, '5')
        player.cards = [c.Card(c.RED, '1')]
        player.draw()
        drawn = player.cards[-1]
        with patch.object(game.rng, 'randbelow', return_value=0):
            self.assertEqual(len(volcano.roll(game, player, chosen=True)), 3)
        self.assertEqual(len(player.cards), 5)
        self.assertIs(player.cards[-1], drawn)
        # Only the drawn card may be played, not the volcano's
        self.assertTrue(all(card is drawn for card in player.playable_cards()))

    def test_after_a_penalty_the_eruption_comes_after_every_drawn_card(self):
        game, player = volcano_game()
        game.last_card = c.Card(c.RED, c.DRAW_TWO)
        game.draw_counter = 4
        player.cards = [c.Card(c.RED, '1')]
        player.draw()
        penalty = list(player.cards)
        with patch.object(game.rng, 'randbelow', return_value=0):
            cards = volcano.roll(game, player)
        self.assertEqual(player.cards, penalty + cards)
        self.assertTrue(all(a is b for a, b in zip(player.cards, penalty)))

    def test_an_eruption_with_no_cards_left_counts_but_shows_nothing(self):
        game, player = volcano_game()
        game.deck.cards, game.deck.graveyard = [], []
        player.cards = [c.Card(c.RED, '1')]
        with patch.object(game.rng, 'randbelow', return_value=0),                 patch('volcano.send_sticker') as sticker, patch('rivalry_log.erupted') as logged:
            self.assertEqual(volcano.erupt(MagicMock(), game, player), [])
        self.assertEqual((game.volcano_eruptions, len(player.cards)), (1, 1))
        sticker.assert_not_called()
        logged.assert_not_called()

    def test_the_drawing_player_is_told_privately(self):
        import bot
        game, player = volcano_game()
        game.last_card = c.Card(c.RED, '5')
        player.cards = [c.Card(c.RED, '1'), c.Card(c.BLUE, '7')]
        notice = bot._drawn_card_notice(player, [c.Card(c.GREEN, '1')] * 3)
        self.assertIn("🌋 The volcano gave you 3 more cards.", notice)
        self.assertNotIn("volcano", bot._drawn_card_notice(player))

    def test_stickers_follow_the_eruption_number(self):
        game, player = volcano_game()
        bot = MagicMock()
        with patch.object(game.rng, 'randbelow', return_value=0), \
                patch('shared_vars.dispatcher') as dispatcher:
            for _ in range(6):
                volcano.erupt(bot, game, player)
        stickers = [call[1]['sticker'] for call in dispatcher.run_async.call_args_list]
        self.assertEqual(stickers, list(volcano.STICKERS))
        self.assertTrue(all(call[0][0] is bot.sendSticker for call in dispatcher.run_async.call_args_list))

    def test_eruptions_survive_a_restart(self):
        game, player = volcano_game()
        game.current_player = player
        game.volcano_rolls, game.volcano_eruptions = 7, 2
        restored = GameManager()._deserialize_game(GameManager()._serialize_game(game))
        self.assertEqual((restored.volcano_rolls, restored.volcano_eruptions), (7, 2))

    def test_abort_start_resets_the_volcano(self):
        game, player = volcano_game()
        game.volcano_rolls, game.volcano_eruptions = 3, 1
        game.abort_start()
        self.assertEqual((game.volcano_rolls, game.volcano_eruptions), (0, 0))


class TestVolcanoFairness(unittest.TestCase):

    def games(self, count=20):
        return [play_a_game('volcano', players=2) for _ in range(count)]

    def test_rolls_are_in_the_proof_and_check(self):
        erupted = 0
        for game in self.games():
            proof = game.rng.proof()
            ops = rolls(proof['ops'])
            self.assertTrue(ops)
            self.assertEqual([op[1] for op in ops], list(range(10, 10 + len(ops))))
            self.assertLessEqual(sum(op[2] for op in ops), 5)
            self.assertEqual(sum(op[2] for op in ops), game.volcano_eruptions)
            self.assertEqual(check(proof)[0], None)
            # Each eruption is followed by its 3 cards (fewer only when the deck ran dry)
            for i, op in enumerate(proof['ops']):
                if op[0] == 'v' and op[2]:
                    erupted += 1
                    self.assertEqual([o[0] for o in proof['ops'][i + 1:i + 4]], ['d', 'd', 'd'])
        self.assertGreater(erupted, 0)

    def tampered(self):
        for game in self.games(10):
            proof = game.rng.proof()
            if rolls(proof['ops']):
                break
        flipped, odds = copy.deepcopy(proof), copy.deepcopy(proof)
        next(op for op in flipped['ops'] if op[0] == 'v')[2] ^= True
        next(op for op in odds['ops'] if op[0] == 'v')[1] = 2
        return proof, flipped, odds

    def test_a_tampered_roll_fails_the_check(self):
        _, flipped, odds = self.tampered()
        self.assertEqual(check(flipped)[0][:7], 'volcano')
        self.assertEqual(check(odds)[0][:7], 'volcano')

    @unittest.skipUnless(shutil.which('node'), "Node.js is not installed")
    def test_page_checker_replays_the_rolls(self):
        cases = list(self.tampered())
        js = ''
        for path in (DECKS_JS, FAIR_JS):
            with open(path, encoding='utf-8') as f:
                js += f.read() + '\n'
        script = js + (
            "(async function () {\n  var out = [];\n  var cases = %s;\n"
            "  for (var i = 0; i < cases.length; i++)\n"
            "    out.push(await replayOps(cases[i], await fairStream(cases[i].seed, cases[i].beacon)));\n"
            "  console.log(JSON.stringify(out));\n})();\n") % json.dumps(cases)
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
            f.write(script)
        try:
            result = subprocess.run(['node', f.name], capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(f.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        good, flipped, odds = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertIsNone(good)
        self.assertIn('seed says', flipped)
        self.assertIn('odds of 1 in 2', odds)


class TestVolcanoLog(LoggedGame):

    def setUp(self):
        super().setUp()
        self.game.mode = 'volcano'
        rivalry_log.start(self.game)

    def test_a_draw_and_its_eruption_are_logged_apart(self):
        with patch.object(self.game.rng, 'randbelow', return_value=0), patch('volcano.send_sticker') as sticker:
            actions.do_draw(MagicMock(), self.alice)
        draw, = self.events('draw')
        erupt, = self.events('erupt')
        self.assertEqual(len(draw['cs']), 1)
        self.assertEqual((len(erupt['cs']), erupt['h'], erupt['i'], erupt['p']), (3, 7, 1, 'a'))
        self.assertEqual(sticker.call_args[0][2], 1)
        self.assertEqual(str(self.alice.cards[-1]), draw['cs'][0])

    def test_timeouts_and_lost_challenges_roll_too(self):
        with patch('volcano.erupt') as erupt:
            self.alice.waiting_time = 30
            actions.do_skip(MagicMock(), self.alice)
        erupt.assert_called_once()

    def test_the_end_records_the_rolls(self):
        self.game.volcano_rolls = 12
        log = rivalry_log.finish(self.game, A, 'win')
        self.assertEqual(log['ev'][-1]['vr'], 12)
        self.assertEqual(log['ev'][-1]['ve'], 0)
        self.game.mode = 'classic'
        rivalry_log.start(self.game)
        self.assertNotIn('vr', rivalry_log.finish(self.game, A, 'win')['ev'][-1])


class TestVolcanoSummary(unittest.TestCase):

    def test_summary_counts_eruptions_per_rival(self):
        log = log_of(
            {'k': 'draw', 'p': 'a', 'r': 'vol', 'cs': ['r_9'], 'h': 4, 't': 1, 'n': 0, 'th': 100},
            {'k': 'erupt', 'p': 'a', 'cs': ['b_1', 'b_2', 'b_3'], 'h': 7, 't': 1, 'n': 0, 'i': 1},
            {'k': 'play', 'p': 'b', 'c': 'g_1', 'h': 2, 't': 2, 'n': 1},
            {'k': 'play', 'p': 'b', 'c': 'g_skip', 'h': 1, 't': 3, 'n': 2},
            {'k': 'draw', 'p': 'b', 'r': 'vol', 'cs': ['y_1'], 'h': 2, 't': 4, 'n': 3},
            {'k': 'erupt', 'p': 'b', 'cs': ['b_4', 'b_5'], 'h': 4, 't': 4, 'n': 3, 'i': 2},
            {'k': 'end', 'p': 'a', 'o': 'win', 't': 8, 'n': 5, 'vr': 3},
        )
        d = rs.summarize_log(log)
        self.assertEqual(d['vol']['a'], [1, 3, 0])
        self.assertEqual(d['vol']['b'], [1, 2, 1])
        self.assertEqual((d['vol']['r'], d['vol']['n1'], d['vol']['e']), (3, 0, 2))
        self.assertAlmostEqual(d['vol']['x'], 1 / 10 + 1 / 11 + 1 / 12, places=4)
        self.assertEqual(d['a']['mx'], 7)

    def test_an_eruption_on_uno_is_counted(self):
        log = log_of(
            {'k': 'play', 'p': 'b', 'c': 'g_1', 'h': 2, 't': 1, 'n': 0},
            {'k': 'play', 'p': 'b', 'c': 'g_skip', 'h': 1, 't': 2, 'n': 1},
            {'k': 'draw', 'p': 'b', 'r': 'pen', 'cs': ['y_1', 'y_2'], 'h': 3, 't': 3, 'n': 2},
            {'k': 'erupt', 'p': 'b', 'cs': ['b_4', 'b_5', 'b_6'], 'h': 6, 't': 3, 'n': 2, 'i': 1},
            {'k': 'end', 'p': 'b', 'o': 'win', 't': 8, 'n': 5, 'vr': 1},
        )
        # On one card before the penalty draw that set it off
        self.assertEqual(rs.summarize_log(log)['vol']['b'], [1, 3, 1])

    def test_eruptions_of_other_players_count_toward_the_odds(self):
        log = log_of(
            {'k': 'erupt', 'p': 'a', 'cs': ['b_1', 'b_2', 'b_3'], 'h': 6, 't': 1, 'n': 2, 'i': 2},
            {'k': 'end', 'p': 'a', 'o': 'win', 't': 8, 'n': 5, 'vr': 9, 've': 2},
        )
        d = rs.summarize_log(log)['vol']
        self.assertEqual((d['e'], d['a'][0], d['b'][0]), (2, 1, 0))

    def test_other_modes_have_no_volcano_summary(self):
        log = log_of({'k': 'end', 'p': 'a', 'o': 'win', 't': 8, 'n': 5})
        self.assertNotIn('vol', rs.summarize_log(log))

    def test_scores_json_knows_the_volcano_deck(self):
        rivals = SimpleNamespace(a_id=1, b_id=2, a_name='A', b_name='B')
        self.assertEqual(rs.scores_data(rivals, [], [], rs.timezone.utc)['decks']['volcano'], 'classic')


if __name__ == '__main__':
    unittest.main()
