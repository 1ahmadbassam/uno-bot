import shutil
import unittest
from collections import Counter
from unittest.mock import MagicMock, patch

import test.test_rate_limit_mitigations as stubs  # noqa: F401
import test.test_fairness_proof as proofs
import actions
import bot
import card as c
import modes
import rivalry_log
import rivalry_stats as rs
import storm
from deck import Deck
from game_manager import GameManager
from test.test_rivalry_log import A, I, LoggedGame


class TestThunderstorm(unittest.TestCase):
    def test_deck_matches_wild_copies_with_all_storm_cards(self):
        deck = Deck()
        deck.fill('thunderstorm')
        cards = Counter(map(str, deck.cards))
        self.assertEqual(len(deck.cards), 188)
        for color in c.COLORS:
            for value in ['1', '2', '3', '4', '5', 'draw', 'reverse', 'skip', 'swap', 'drop', 'plus1']:
                self.assertEqual(cards[color + '_' + value], 4)
            for value in ['0', '6', '7', '8', '9']:
                self.assertNotIn(color + '_' + value, cards)
        self.assertEqual(cards['colorchooser'], 6)
        self.assertEqual(cards['draw_four'], 6)

    def test_picker_message_and_rules(self):
        results = []
        bot._add_mode_results(results)
        choice = next(r for r in results if r.id == 'mode_thunderstorm')
        self.assertEqual(choice.input_message_content.message_text, 'Riders on the storm ⛈')
        mode = modes.THUNDERSTORM
        self.assertEqual(mode.deck, 'thunderstorm')
        self.assertTrue(mode.storm and mode.bluff)
        self.assertFalse(mode.tornado or mode.volcano)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is not installed')
    def test_full_games_verify_with_the_new_deck_in_both_checkers(self):
        cases = {}
        for i in range(3):
            game = proofs.play_a_game('thunderstorm', players=2, max_turns=120,
                                      rng=proofs.seeds_rng(['a', str(i)]))
            proof = game.rng.proof()
            self.assertIn(['f', 'thunderstorm'], proof['ops'])
            self.assertIsNone(proofs.check(proof)[0])
            cases[str(i)] = {'proof': proof}
        for problem in proofs.TestReplayPageChecker.run_page(self, cases).values():
            self.assertIsNone(problem)


class TestThunderstormLog(LoggedGame):
    def setUp(self):
        super().setUp()
        self.game.mode = 'thunderstorm'
        self.game.deck.fill('thunderstorm')
        rivalry_log.start(self.game)

    def play(self, player, value):
        player.cards.append(c.Card(c.RED, value))
        actions.do_play_card(MagicMock(), player, 'r_' + value)

    def test_all_storm_effects_and_cancellation_have_own_stats(self):
        self.play(self.alice, c.SWAP)
        self.play(self.ivan, c.DROP)
        self.play(self.alice, c.PLUS_ONE)
        storm.toggle_cancel(self.game)
        self.play(self.ivan, c.SWAP)
        self.assertEqual(len(self.events('swap')), 1)
        self.assertEqual(len(self.events('drop')), 1)
        self.assertEqual(self.events('draw')[-1]['r'], 'plus1')
        log = rivalry_log.finish(self.game, A, 'forfeit')
        summary = rs.summarize_log(log)
        self.assertNotIn('storm', summary)
        self.assertNotIn('r8p', summary)
        ts = summary['thunderstorm']
        self.assertEqual(ts['a']['sw'], 1)
        self.assertEqual(ts['a']['p1'], 1)
        self.assertEqual(ts['b']['dr'], 1)
        self.assertEqual(ts['b']['cx'], 1)
        result = rs.Result(1, 1, rs.datetime.now(), A, I, 'forfeit', mode='thunderstorm')
        text = rs.game_stats_text(rs.Rivals([(A, 'Alice'), (I, 'Ivan')]), 1, result, log, rs.timezone.utc, str)
        self.assertIn('thunderstorm', text)
        self.assertIn('Swaps · Drops · +1s', text)
        self.assertIn('Cancelled with /cancel', text)

    def test_empty_mode_stats_and_restart_preserve_mode(self):
        self.game.storm_cancel = True
        manager = GameManager()
        restored = manager._deserialize_game(manager._serialize_game(self.game))
        self.assertEqual(restored.mode, 'thunderstorm')
        self.assertTrue(restored.rules.storm and restored.storm_cancel)
        summary = rs.summarize_log(rivalry_log.finish(restored, A, 'forfeit'))
        self.assertIn('thunderstorm', summary)
        self.assertNotIn('storm', summary)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is not installed')
    def test_logged_storm_effects_match_the_replay_proof(self):
        game = self.game
        game.rng = proofs.seeds_rng(['Alice', 'Ivan'])
        game.deck.rng = game.rng
        game.started = False
        game.start()
        for p in game.players:
            p.cards = []
            p.draw_first_hand()
        rivalry_log.start(game)
        if game.choosing_color:
            game.choose_color(c.RED)
        for _ in range(60):
            p = game.current_player
            playable = p.playable_cards()
            if playable and len(p.cards) > 3:
                actions.do_play_card(MagicMock(), p, str(playable[0]))
                if game.choosing_color:
                    game.choose_color(c.RED)
            else:
                actions.do_draw(MagicMock(), p)
                game.turn()
        log = rivalry_log.finish(game, A, 'forfeit')
        self.assertTrue(any(e['k'] in ('swap', 'drop') for e in log['ev']))
        out = proofs.TestReplayPageChecker.run_page(self, {
            'logged': {'proof': game.rng.proof(), 'events': log['ev'], 'whole': True}})
        self.assertEqual(out['logged'], {'ok': True})
