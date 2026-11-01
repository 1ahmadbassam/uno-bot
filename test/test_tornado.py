import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Installs the module stubs needed to import the game modules
import test.test_rate_limit_mitigations as stubs  # noqa: F401

import actions
import bot
import card as c
import modes
import rivalry_log
import rivalry_stats as rs
from game import Game
from player import Player
from test.test_fairness_proof import check, play_a_game
from test.test_rivalry_log import A, I, LoggedGame, log_of


def tornado_game(mode='tornado'):
    game = Game(SimpleNamespace(id=-1, title="Test", type="group"))
    game.mode = mode
    game.rng.use_system()
    game.deck.fill('classic')
    p1, p2 = [Player(game, SimpleNamespace(id=i, first_name='P%d' % i, username=None)) for i in (1, 2)]
    game.started = True
    game.current_player = p1
    game.last_card = c.Card(c.RED, '5')
    p1.cards = [c.Card(c.BLUE, '1'), c.Card(c.BLUE, '2')]
    p2.cards = [c.Card(c.GREEN, '1'), c.Card(c.GREEN, '2')]
    return game, p1, p2


def play(player, card):
    """Plays `card` (added to the hand first) the way the inline menu does"""
    player.cards.append(card)
    with patch.object(actions, 'gm', MagicMock()), patch.object(actions, 'send_async'):
        actions.do_play_card(MagicMock(), player, str(card))


def pending_four(mode='tornado', color=c.RED):
    """A game where P1 played a +4 and chose `color`: P2 has to answer it"""
    game, p1, p2 = tornado_game(mode)
    play(p1, c.Card(None, None, c.DRAW_FOUR))
    game.choose_color(color)
    return game, p1, p2


class TestTornadoMode(unittest.TestCase):

    def test_mode(self):
        mode = modes.get('tornado')
        self.assertEqual(mode.message, 'Wind of Revolution 🌪️')
        self.assertEqual(mode.deck, 'classic')
        self.assertFalse(mode.bluff)
        self.assertTrue(mode.tornado)
        self.assertTrue(modes.CLASSIC.bluff)
        self.assertFalse(modes.CLASSIC.tornado)


class TestTornadoStacking(unittest.TestCase):

    def test_a_four_goes_on_a_four(self):
        game, p1, p2 = pending_four()
        four = c.Card(None, None, c.DRAW_FOUR)
        p2.cards.append(four)
        self.assertIn(four, p2.playable_cards())
        play(p2, four)
        self.assertEqual(game.draw_counter, 8)
        game.choose_color(c.BLUE)
        self.assertIs(game.current_player, p1)
        before = len(p1.cards)
        with patch.object(actions, 'gm', MagicMock()), patch.object(actions, 'send_async'):
            actions.do_draw(MagicMock(), p1)
        self.assertEqual(len(p1.cards), before + 8)
        self.assertIs(game.current_player, p2)

    def test_a_two_of_the_chosen_color_goes_on_a_four(self):
        game, p1, p2 = pending_four(color=c.GREEN)
        green, red = c.Card(c.GREEN, c.DRAW_TWO), c.Card(c.RED, c.DRAW_TWO)
        p2.cards += [green, red]
        playable = p2.playable_cards()
        self.assertIn(green, playable)
        self.assertNotIn(red, playable)
        play(p2, green)
        self.assertEqual(game.draw_counter, 6)
        # A +2 of any color goes on that +2, as in every mode, but no +4
        p1.cards += [c.Card(c.YELLOW, c.DRAW_TWO), c.Card(None, None, c.DRAW_FOUR)]
        self.assertEqual(p1.playable_cards(), [c.Card(c.YELLOW, c.DRAW_TWO)])

    def test_nothing_else_goes_on_a_four(self):
        game, p1, p2 = pending_four()
        p2.cards += [c.Card(c.RED, '7'), c.Card(None, None, c.CHOOSE), c.Card(c.RED, c.SKIP)]
        self.assertEqual(p2.playable_cards(), [])

    def test_classic_doesnt_stack_on_a_four(self):
        game, p1, p2 = pending_four('classic')
        p2.cards += [c.Card(None, None, c.DRAW_FOUR), c.Card(c.RED, c.DRAW_TWO)]
        self.assertEqual(p2.playable_cards(), [])

    def test_a_chooser_goes_on_a_chooser(self):
        game, p1, p2 = tornado_game()
        play(p1, c.Card(None, None, c.CHOOSE))
        game.choose_color(c.YELLOW)
        chooser = c.Card(None, None, c.CHOOSE)
        p2.cards.append(chooser)
        self.assertIn(chooser, p2.playable_cards())

    def test_a_chooser_only_goes_on_a_chooser_in_tornado(self):
        for mode in modes.MODES:
            game, p1, p2 = tornado_game(mode)
            game.last_card = c.Card(c.YELLOW, None, c.CHOOSE)
            game.draw_counter = 0
            self.assertEqual(p2._card_playable(c.Card(None, None, c.CHOOSE)), mode == 'tornado', mode)

    def test_a_chooser_still_goes_on_everything_else(self):
        for under in (c.Card(c.YELLOW, '7'), c.Card(c.YELLOW, c.SKIP), c.Card(c.YELLOW, None, c.DRAW_FOUR)):
            game, p1, p2 = tornado_game('classic')
            game.last_card = under
            game.draw_counter = 0
            self.assertTrue(p2._card_playable(c.Card(None, None, c.CHOOSE)), under)


class TestNoBluffs(unittest.TestCase):

    def test_a_four_cant_be_challenged(self):
        game, p1, p2 = pending_four()
        self.assertIsNone(game.last_bluffer)
        self.assertFalse(p1.bluffing)
        self.assertFalse(bot._inline_action_allowed(p2, game, 'call_bluff'))
        # Classic still offers the challenge
        game, p1, p2 = pending_four('classic')
        self.assertIs(game.last_bluffer, p1)
        self.assertTrue(bot._inline_action_allowed(p2, game, 'call_bluff'))

    def test_a_winning_four_wins_at_once(self):
        game, p1, p2 = tornado_game()
        p1.cards = []
        with patch.object(actions, 'player_won') as won:
            play(p1, c.Card(None, None, c.DRAW_FOUR))
        won.assert_called_once_with(unittest.mock.ANY, p1)
        self.assertFalse(getattr(p1, 'pending_win', False))


class TestTornadoFairness(unittest.TestCase):

    def test_games_check(self):
        for _ in range(10):
            proof = play_a_game('tornado', players=2).rng.proof()
            self.assertEqual(check(proof)[0], None)


class TestTornadoLog(LoggedGame):

    def setUp(self):
        super().setUp()
        self.game.mode = 'tornado'
        rivalry_log.start(self.game)

    def play(self, player, card):
        player.cards.append(card)
        actions.do_play_card(MagicMock(), player, str(card))

    def test_plays_log_the_pile_and_the_card_under_them(self):
        self.play(self.alice, c.Card(None, None, c.DRAW_FOUR))
        self.game.choose_color(c.RED)
        self.play(self.ivan, c.Card(c.RED, c.DRAW_TWO))
        self.play(self.alice, c.Card(c.BLUE, c.DRAW_TWO))
        first, second, third = self.events('play')
        self.assertNotIn('pi', first)
        self.assertEqual((second['pi'], second['on']), (4, 'draw_four'))
        self.assertEqual((third['pi'], third['on']), (6, 'r_draw'))
        self.game.draw_counter = 0
        self.play(self.ivan, c.Card(None, None, c.CHOOSE))
        self.game.choose_color(c.RED)
        self.play(self.alice, c.Card(None, None, c.CHOOSE))
        self.assertEqual(self.events('play')[-1]['on'], 'colorchooser')
        self.assertNotIn('pi', self.events('play')[-1])

    def test_classic_plays_dont_log_it(self):
        self.game.mode = 'classic'
        rivalry_log.start(self.game)
        self.play(self.alice, c.Card(c.RED, c.DRAW_TWO))
        self.play(self.ivan, c.Card(c.GREEN, c.DRAW_TWO))
        self.assertNotIn('pi', self.events('play')[-1])
        self.assertNotIn('on', self.events('play')[-1])

    def test_a_tornado_game_is_marked_at_the_end(self):
        log = rivalry_log.finish(self.game, A, 'win')
        self.assertEqual(log['ev'][-1]['tn'], 1)
        self.assertEqual(rs.summarize_log(log)['tornado']['a']['s4'], 0)
        self.game.mode = 'classic'
        rivalry_log.start(self.game)
        log = rivalry_log.finish(self.game, A, 'win')
        self.assertNotIn('tn', log['ev'][-1])
        self.assertNotIn('tornado', rs.summarize_log(log))


class TestTornadoSummary(unittest.TestCase):

    def setUp(self):
        self.log = log_of(
            {'k': 'play', 'p': 'a', 'c': 'draw_four', 'h': 2, 't': 1, 'n': 0},
            {'k': 'color', 'p': 'a', 'co': 'g', 't': 1, 'n': 0},
            {'k': 'play', 'p': 'b', 'c': 'draw_four', 'h': 2, 't': 2, 'n': 1, 'pi': 4, 'on': 'draw_four'},
            {'k': 'color', 'p': 'b', 'co': 'r', 't': 2, 'n': 1},
            {'k': 'play', 'p': 'a', 'c': 'r_draw', 'h': 1, 't': 3, 'n': 2, 'pi': 8, 'on': 'draw_four'},
            {'k': 'draw', 'p': 'b', 'r': 'pen', 'src': 'a', 'cs': ['b_%d' % i for i in range(1, 10)] + ['y_1'],
             'h': 12, 't': 4, 'n': 3},
            # Ran out of time with nothing to draw but the usual card: not a pile
            {'k': 'draw', 'p': 'a', 'r': 'time', 'cs': ['g_3'], 'h': 2, 't': 4, 'n': 4},
            {'k': 'play', 'p': 'b', 'c': 'colorchooser', 'h': 11, 't': 5, 'n': 5},
            {'k': 'color', 'p': 'b', 'co': 'y', 't': 5, 'n': 5},
            {'k': 'play', 'p': 'a', 'c': 'colorchooser', 'h': 1, 't': 6, 'n': 6, 'on': 'colorchooser'},
            {'k': 'color', 'p': 'a', 'co': 'b', 't': 6, 'n': 6},
            # A third player took the +4 in between (the log leaves their turn out): no stack
            {'k': 'play', 'p': 'b', 'c': 'draw_four', 'h': 10, 't': 7, 'n': 7},
            {'k': 'color', 'p': 'b', 'co': 'g', 't': 7, 'n': 7},
            {'k': 'play', 'p': 'a', 'c': 'draw_four', 'h': 0, 't': 8, 'n': 9},
            {'k': 'end', 'p': 'a', 'o': 'win', 't': 9, 'n': 9, 'tn': 1},
        )

    def test_stacks_are_counted(self):
        d = rs.summarize_log(self.log)
        self.assertEqual(d['stack'], 10)
        self.assertEqual(d['tornado']['a'], {'s4': 0, 's2': 1, 'ww': 1, 'pen': 0, 'big': 0})
        # And a timeout with a pile is one
        next(e for e in self.log['ev'] if e.get('r') == 'time')['cs'] = ['g_3', 'g_4']
        self.assertEqual(rs.summarize_log(self.log)['tornado']['a'], {'s4': 0, 's2': 1, 'ww': 1, 'pen': 1, 'big': 2})
        self.assertEqual(d['tornado']['b'], {'s4': 1, 's2': 0, 'ww': 0, 'pen': 1, 'big': 10})

    def test_stats_message(self):
        rivals = rs.Rivals([(A, "Alice"), (I, "Ivan")])
        result = rs.Result(7, 1, rs.datetime(2026, 10, 28, 12, 0), A, I, 'win', rs.datetime(2026, 10, 28, 11, 58),
                           'tornado', 2, 6, 11)
        text = rs.game_stats_text(rivals, 3, result, self.log, rs.timezone.utc, str)
        for line in ('🌪️ +4 on +4 · +2 on +4: Alice 0 · 1 · Ivan 1 · 0',
                     'Color Chooser on Color Chooser: Alice 1 · Ivan 0',
                     'Biggest penalty drawn: Alice – · Ivan +10',
                     # No bluff count: Tornado has no challenges
                     '+4s played: Alice 2 · Ivan 2'):
            self.assertIn(line, text.split('\n'))


if __name__ == '__main__':
    unittest.main()
