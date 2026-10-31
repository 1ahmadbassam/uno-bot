import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Installs the module stubs needed to import the game modules
import test.test_rate_limit_mitigations as stubs  # noqa: F401

import actions
import card as c
import modes
import results
import rivalry_log
import rivalry_stats as rs
from deck import Deck
from game import Game
from player import Player
from test.test_fairness_proof import check, play_a_game
from test.test_rivalry_log import A, I, LoggedGame, log_of

EIGHT = c.Card(None, None, c.DRAW_EIGHT)


def game_of(mode, *names):
    game = Game(SimpleNamespace(id=-1, title="Test", type="group"))
    game.mode = mode
    game.rng.use_system()
    game.deck.fill(game.rules.deck)
    seated = [Player(game, SimpleNamespace(id=i + 1, first_name=name, username=name.lower()))
              for i, name in enumerate(names)]
    game.started = True
    game.last_card = c.Card(c.RED, '6')
    for i, p in enumerate(seated):
        p.cards = [c.Card(c.BLUE, str(n)) for n in range(1, 3 + i)]
    return game, seated


def info(game):
    return results.game_info(game).message_text


def send(player, card):
    """Plays `card` (added to the hand first) the way the inline menu does"""
    player.cards.append(card)
    with patch.object(actions, 'gm', MagicMock()), patch.object(actions, 'send_async') as sent:
        actions.do_play_card(MagicMock(), player, str(card))
    return sent


class TestCrazyMode(unittest.TestCase):

    def test_mode(self):
        mode = modes.get('crazy')
        self.assertEqual(mode.message, 'Why so Serious? 👻️')
        self.assertEqual((mode.deck, mode.hand_size, mode.hide_counts), ('crazy', 8, True))
        self.assertFalse(modes.CLASSIC.hide_counts)

    def test_deck_is_the_classic_one_and_two_eights(self):
        crazy, classic = Deck(), Deck()
        crazy.fill('crazy')
        classic.fill('classic')
        extra = Counter(map(str, crazy.cards)) - Counter(map(str, classic.cards))
        self.assertEqual(extra, Counter({'draw_eight': 2}))
        self.assertEqual(len(crazy.cards), 110)

    def test_eight_card(self):
        self.assertTrue(c.is_draw_wild(EIGHT))
        self.assertEqual(c.label(EIGHT), '⬛️+8')
        self.assertEqual(str(c.from_str('draw_eight')), 'draw_eight')
        self.assertIn('draw_eight', c.STICKERS)
        self.assertIn('draw_eight', c.STICKERS_GREY)

    def test_starting_hands_have_eight_cards(self):
        game = play_a_game('crazy', players=2, max_turns=1)
        deal = [op for op in game.rng.ops if op[0] == 'd' and op[2] is not None][:16]
        self.assertEqual(Counter(op[2] for op in deal), {'P1': 8, 'P2': 8})


class TestEightCard(unittest.TestCase):

    def setUp(self):
        self.game, (self.p1, self.p2) = game_of('crazy', 'Alice', 'Ivan')
        self.game.current_player = self.p1

    def test_the_next_player_draws_eight(self):
        send(self.p1, EIGHT)
        self.assertTrue(self.game.choosing_color)
        self.game.choose_color(c.GREEN)
        self.assertEqual(self.game.draw_counter, 8)
        before = len(self.p2.cards)
        with patch.object(actions, 'gm', MagicMock()), patch.object(actions, 'send_async'):
            actions.do_draw(MagicMock(), self.p2)
        self.assertEqual(len(self.p2.cards), before + 8)
        self.assertIs(self.game.current_player, self.p1)

    def test_nothing_goes_on_it(self):
        send(self.p1, EIGHT)
        self.game.choose_color(c.GREEN)
        self.p2.cards += [c.Card(c.GREEN, c.DRAW_TWO), c.Card(None, None, c.DRAW_FOUR), EIGHT]
        self.assertEqual(self.p2.playable_cards(), [])

    def challenge(self, bluffing):
        self.p1.cards.append(c.Card(c.RED if bluffing else c.YELLOW, '9'))
        send(self.p1, EIGHT)
        self.game.choose_color(c.GREEN)
        with patch.object(actions, 'gm', MagicMock()), patch.object(actions, 'send_async') as sent:
            actions.do_call_bluff(MagicMock(), self.p2)
        return sent.call_args[1]['text']

    def test_a_caught_bluff_gives_the_bluffer_eight(self):
        # The red 9 challenge() adds stays in hand
        before = len(self.p1.cards) + 1
        self.assertEqual(self.challenge(True), "Bluff called! Giving 8 cards to Alice")
        self.assertEqual(len(self.p1.cards), before + 8)

    def test_a_lost_challenge_gives_ten(self):
        before = len(self.p2.cards)
        self.assertEqual(self.challenge(False), "Alice didn't bluff! Giving 10 cards to Ivan")
        self.assertEqual(len(self.p2.cards), before + 10)

    def test_a_four_keeps_its_message(self):
        game, (p1, p2) = game_of('classic', 'Alice', 'Ivan')
        game.current_player = p1
        p1.cards.append(c.Card(c.RED, '9'))
        send(p1, c.Card(None, None, c.DRAW_FOUR))
        game.choose_color(c.GREEN)
        with patch.object(actions, 'gm', MagicMock()), patch.object(actions, 'send_async') as sent:
            actions.do_call_bluff(MagicMock(), p2)
        self.assertEqual(sent.call_args[1]['text'], "Bluff called! Giving 4 cards to Alice")


    def test_winning_on_an_eight_waits_for_the_answer(self):
        self.p1.cards = []
        with patch.object(actions, 'player_won') as won:
            send(self.p1, EIGHT)
            self.game.choose_color(c.GREEN)
            self.assertTrue(self.p1.pending_win)
            won.assert_not_called()
            with patch.object(actions, 'gm', MagicMock()), patch.object(actions, 'send_async'):
                actions.do_draw(MagicMock(), self.p2)
            won.assert_called_once_with(unittest.mock.ANY, self.p1)
        self.assertEqual(len(self.p2.cards), 3 + 8)

    def test_a_caught_bluff_takes_the_win_back(self):
        self.p1.cards = [c.Card(c.RED, '9')]
        send(self.p1, EIGHT)
        self.p1.cards.remove(c.Card(c.RED, '9'))
        self.p1.bluffing = True
        self.p1.pending_win = True
        self.game.choose_color(c.GREEN)
        with patch.object(actions, 'player_won') as won, patch.object(actions, 'gm', MagicMock()), \
                patch.object(actions, 'send_async'):
            actions.do_call_bluff(MagicMock(), self.p2)
        won.assert_not_called()
        self.assertFalse(self.p1.pending_win)
        self.assertEqual(len(self.p1.cards), 8)

    def test_its_player_leaving_cancels_it(self):
        send(self.p1, EIGHT)
        self.game.choose_color(c.GREEN)
        with patch.object(actions, 'gm', MagicMock()), patch.object(actions, 'send_async') as sent:
            actions.settle_draw_four_after_leave(MagicMock(), self.game, self.p1, False)
        self.assertEqual(self.game.draw_counter, 0)
        self.assertEqual(sent.call_args[1]['text'], "The player who played the +8 left the game, so its penalty is cancelled.")


class TestGameInfo(unittest.TestCase):

    def test_classic_shows_the_counts(self):
        game, (a, i) = game_of('classic', 'A', 'I')
        game.current_player = a
        a.cards = a.cards[:1]
        self.assertEqual(info(game), "Your turn: A\n    — \nCard: ❤️ 6\n    — \nPlayers (⟳):\n• A (1 card)\n• I (3 cards)")

    def test_crazy_hides_them(self):
        game, (a, i) = game_of('crazy', 'A', 'I')
        game.current_player = a
        self.assertEqual(info(game), "Your turn: A\n    — \nCard: ❤️ 6\n    — \nPlayers (⟳):\n• A (● cards)\n• I (● cards)")

    def test_nobody_is_pinged(self):
        game, players = game_of('classic', 'Alice', 'Ivan')
        self.assertNotIn('@', info(game))

    def test_players_are_listed_in_their_seats(self):
        game, (a, b, d) = game_of('classic', 'A', 'B', 'D')
        game.current_player = b
        self.assertEqual(info(game).split('Players (⟳):\n')[1].split('\n'), ['• B (3 cards)', '• D (4 cards)', '• A (2 cards)'])
        # The other way round, play goes from B to A: the seats stay the same, the arrow turns
        game.reverse()
        self.assertIs(b.next, a)
        self.assertEqual(info(game).split('Players (⟲):\n')[1].split('\n'), ['• B (3 cards)', '• D (4 cards)', '• A (2 cards)'])

    def test_no_card_yet(self):
        self.assertEqual(c.spaced_label(None), '–')
        game, players = game_of('classic', 'A', 'I')
        game.last_card = None
        self.assertIn("Card: –\n", info(game))

    def test_a_wild_shows_its_color(self):
        game, players = game_of('crazy', 'A', 'I')
        game.last_card = c.Card(c.BLUE, None, c.DRAW_EIGHT)
        self.assertIn("Card: 💙⬛️ +8\n", info(game))


class TestCrazyFairness(unittest.TestCase):

    def test_games_check(self):
        for _ in range(10):
            game = play_a_game('crazy', players=2)
            proof = game.rng.proof()
            self.assertIn(['f', 'crazy'], proof['ops'])
            self.assertEqual(check(proof)[0], None)

    def test_an_eight_is_never_the_first_card(self):
        for _ in range(30):
            game = play_a_game('crazy', players=2, max_turns=0)
            self.assertFalse(c.is_draw_wild(game.last_card))


class TestCrazyLog(LoggedGame):

    def setUp(self):
        super().setUp()
        self.game.mode = 'crazy'
        rivalry_log.start(self.game)

    def test_eight_piles_are_marked(self):
        self.alice.cards.append(EIGHT)
        actions.do_play_card(MagicMock(), self.alice, 'draw_eight')
        self.game.choose_color(c.BLUE)
        actions.do_draw(MagicMock(), self.ivan)
        draw, = self.events('draw')
        self.assertEqual((draw['r'], draw['e8'], len(draw['cs'])), ('pen', 1, 8))
        # The next draw isn't a +8 pile any more, though the +8 is still on top
        self.ivan.cards.append(c.Card(c.BLUE, '1'))
        actions.do_play_card(MagicMock(), self.ivan, 'b_1')
        actions.do_draw(MagicMock(), self.alice)
        self.assertNotIn('e8', self.events('draw')[-1])

    def test_a_timeout_on_an_eight_draws_its_pile(self):
        self.alice.cards.append(EIGHT)
        actions.do_play_card(MagicMock(), self.alice, 'draw_eight')
        self.game.choose_color(c.BLUE)
        with patch.object(actions, 'start_player_countdown', create=True):
            actions.do_skip(MagicMock(), self.ivan)
        draw, = self.events('draw')
        self.assertEqual((draw['r'], draw['e8'], len(draw['cs'])), ('time', 1, 8))

    def test_a_challenge_logs_the_card(self):
        self.alice.cards += [c.Card(c.RED, '9'), EIGHT]
        actions.do_play_card(MagicMock(), self.alice, 'draw_eight')
        self.game.choose_color(c.BLUE)
        actions.do_call_bluff(MagicMock(), self.ivan)
        chal, = self.events('chal')
        self.assertEqual((chal['c'], chal['ok']), ('draw_eight', 1))

    def test_a_crazy_game_is_marked_at_the_end(self):
        log = rivalry_log.finish(self.game, A, 'win')
        self.assertEqual(log['ev'][-1]['cz'], 1)
        self.assertEqual(rs.summarize_log(log)['crazy']['a']['p8'], 0)
        self.game.mode = 'classic'
        rivalry_log.start(self.game)
        self.assertNotIn('crazy', rs.summarize_log(rivalry_log.finish(self.game, A, 'win')))


class TestCrazySummary(unittest.TestCase):

    def setUp(self):
        self.log = log_of(
            {'k': 'play', 'p': 'a', 'c': 'draw_eight', 'h': 2, 'bl': 1, 't': 1, 'n': 0},
            {'k': 'color', 'p': 'a', 'co': 'g', 't': 1, 'n': 0},
            {'k': 'draw', 'p': 'b', 'r': 'pen', 'src': 'a', 'e8': 1, 'cs': ['b_%d' % i for i in range(1, 9)],
             'h': 11, 't': 2, 'n': 1},
            {'k': 'play', 'p': 'a', 'c': 'g_1', 'h': 1, 't': 3, 'n': 2},
            {'k': 'play', 'p': 'b', 'c': 'g_2', 'h': 10, 't': 4, 'n': 3},
            {'k': 'play', 'p': 'a', 'c': 'g_3', 'h': 0, 't': 5, 'n': 4},
            {'k': 'end', 'p': 'a', 'o': 'win', 't': 8, 'n': 5, 'cz': 1},
        )
        self.log['ev'][0]['h']['b'].append('draw_eight')

    def test_eights_are_counted(self):
        d = rs.summarize_log(self.log)
        self.assertEqual(d['crazy']['a'], {'k8': 0, 'p8': 1, 'b8': 1, 'n8': 0, 't8': 0})
        self.assertEqual(d['crazy']['b'], {'k8': 1, 'p8': 0, 'b8': 0, 'n8': 1, 't8': 8})
        self.assertEqual(d['a']['ty'][rs.CARD_TYPES.index('d8')], 1)
        # A +8 isn't a +2/+4 stack
        self.assertEqual(d['stack'], 0)

    def test_stats_message(self):
        rivals = rs.Rivals([(A, "Alice"), (I, "Ivan")])
        result = rs.Result(7, 1, rs.datetime(2026, 10, 28, 12, 0), A, I, 'win', rs.datetime(2026, 10, 28, 11, 58),
                           'crazy', 2, 5, 10)
        lines = rs.game_stats_text(rivals, 3, result, self.log, rs.timezone.utc, str).split('\n')
        self.assertFalse([line for line in lines if line.startswith('Longest +2/+4 stack')])
        self.assertIn('👻 +8s played: Alice 1 (1 bluff) · Ivan 0 (0 bluffs)', lines)
        self.assertIn('Cards drawn from +8s: Alice 0 · Ivan 8', lines)


if __name__ == '__main__':
    unittest.main()
