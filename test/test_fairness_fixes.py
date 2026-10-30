import unittest
import sys
import types
import secrets
from types import SimpleNamespace
from unittest.mock import patch

# Provide lightweight stubs for optional runtime modules when running isolated unit tests
if 'telegram' not in sys.modules:
    tg = types.ModuleType('telegram')
    class FakeUser:
        def __init__(self, id, first_name=None, username=None):
            self.id = id
            self.first_name = first_name or str(id)
            self.username = username
    class FakeChat:
        def __init__(self, id, type='group', title=None):
            self.id = id
            self.type = type
            self.title = title or f"Group {id}"
    tg.Message = object
    tg.Chat = FakeChat
    tg.User = FakeUser
    tg.Update = object
    tg_err = types.ModuleType('telegram.error')
    class TelegramError(Exception): pass
    tg_err.BadRequest = TelegramError
    tg_err.TimedOut = TelegramError
    tg_err.ChatMigrated = TelegramError
    tg_err.Unauthorized = TelegramError
    tg.error = tg_err
    tg_ext = types.ModuleType('telegram.ext')
    tg_ext.CallbackContext = object
    class FakeUpdater:
        def __init__(self, *args, **kwargs):
            self.dispatcher = object()
    tg_ext.Updater = FakeUpdater
    sys.modules['telegram'] = tg
    sys.modules['telegram.error'] = tg_err
    sys.modules['telegram.ext'] = tg_ext

if 'apscheduler' not in sys.modules:
    ap = types.ModuleType('apscheduler')
    ap_j = types.ModuleType('apscheduler.jobstores')
    ap_jb = types.ModuleType('apscheduler.jobstores.base')
    class JobLookupError(Exception): pass
    ap_jb.JobLookupError = JobLookupError
    ap_j.base = ap_jb
    ap.jobstores = ap_j
    sys.modules['apscheduler'] = ap
    sys.modules['apscheduler.jobstores'] = ap_j
    sys.modules['apscheduler.jobstores.base'] = ap_jb

if 'pony' not in sys.modules:
    p = types.ModuleType('pony')
    po = types.ModuleType('pony.orm')
    class FakeDBSession:
        def __call__(self, f):
            return f
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    po.db_session = FakeDBSession()
    class FakeEntity:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
        @classmethod
        def get(cls, **kwargs):
            return None
        @classmethod
        def select(cls, *args, **kwargs):
            return []
        def delete(self):
            pass
    class FakeDB:
        Entity = FakeEntity
        def bind(*args, **kwargs): pass
        def generate_mapping(*args, **kwargs): pass
    po.Database = FakeDB
    po.PrimaryKey = lambda *args, **kwargs: None
    po.Required = lambda *args, **kwargs: None
    po.Optional = lambda *args, **kwargs: None
    po.LongStr = str
    p.orm = po
    sys.modules['pony'] = p
    sys.modules['pony.orm'] = po

if 'shared_vars' not in sys.modules:
    sv = types.ModuleType('shared_vars')
    sv.gm = types.SimpleNamespace(
        persist_game=lambda g: None,
        leave_game=lambda u, c: None,
        end_game=lambda c, u: None
    )
    sv.updater = None
    sv.dispatcher = types.SimpleNamespace(run_async=lambda f, *args, **kwargs: None)
    sys.modules['shared_vars'] = sv

if 'user_setting' not in sys.modules:
    us = types.ModuleType('user_setting')
    class FakeUserSetting:
        def __init__(self, *args, **kwargs):
            self.lang = ''
        @classmethod
        def get(cls, *args, **kwargs): return None
    us.UserSetting = FakeUserSetting
    sys.modules['user_setting'] = us

from game import Game
from player import Player
from deck import Deck
import card as c
from game_manager import GameManager
from actions import do_skip, do_call_bluff, do_play_card, do_draw, settle_draw_four_after_leave
from errors import DeckEmptyError
from internationalization import __


class TestFairnessFixes(unittest.TestCase):

    def setUp(self):
        self.game = Game(SimpleNamespace(id=1, title="Test Group", type="group"))
        self.p0 = Player(self.game, SimpleNamespace(id=100, first_name="Alice", username="alice"))
        self.p1 = Player(self.game, SimpleNamespace(id=101, first_name="Bob", username="bob"))
        self.p2 = Player(self.game, SimpleNamespace(id=102, first_name="Charlie", username="charlie"))

        import shared_vars
        def fake_leave(user, chat):
            for p in list(self.game.players):
                if p.user.id == user.id:
                    p.leave()
                    break
        shared_vars.gm.leave_game = fake_leave

    def test_1_2_draws_are_random_picks_from_csprng(self):
        """1.2: Each draw is a CSPRNG pick from the remaining pile, not the next card of a fixed order"""
        deck = Deck()
        deck.cards = [c.Card(c.RED, '1'), c.Card(c.BLUE, '1'), c.Card(c.GREEN, '1')]
        with patch('deck.csprng.randrange', return_value=1) as randrange:
            card = deck.draw()
        randrange.assert_called_once_with(3)
        # The pick indexes the pile sorted by card name: b_1, g_1, r_1
        self.assertEqual(card, c.Card(c.GREEN, '1'))
        self.assertEqual(sorted(deck.cards), sorted([c.Card(c.RED, '1'), c.Card(c.BLUE, '1')]))

    def test_1_2_draw_order_is_uniform(self):
        """1.2: Every card is equally likely at every draw position (no positional bias)"""
        sample_size = 4000
        colors = [c.RED, c.BLUE, c.GREEN, c.YELLOW]
        counts = [{color: 0 for color in colors} for _ in range(4)]

        for _ in range(sample_size):
            deck = Deck()
            # Always the same pile layout: any bias towards list order would show up
            deck.cards = [c.Card(color, '1') for color in colors]
            for position in range(4):
                counts[position][deck.draw().color] += 1

        # Each color should be drawn ~25% of the time (1000) at each position; ±200 is > 7 sigma
        for position in range(4):
            for color in colors:
                count = counts[position][color]
                self.assertTrue(800 <= count <= 1200,
                                f"Color {color} at draw {position} came up {count} times")

    def test_1_2_empty_pile_recycles_discards(self):
        """1.2: An empty draw pile is refilled from the discards"""
        deck = Deck()
        deck.graveyard = [c.Card(c.RED, '1'), c.Card(c.BLUE, '2')]
        drawn = deck.draw()
        self.assertIn(drawn, [c.Card(c.RED, '1'), c.Card(c.BLUE, '2')])
        self.assertEqual(len(deck.cards), 1)
        self.assertEqual(deck.graveyard, [])

        deck.cards.clear()
        with self.assertRaises(DeckEmptyError):
            deck.draw()

    def test_1_2_persisted_piles_carry_no_order(self):
        """1.2: Saved draw and discard piles are sorted, so they reveal nothing about future draws"""
        self.game.start()
        data = GameManager()._serialize_game(self.game)
        self.assertEqual(data['deck_cards'], sorted(data['deck_cards']))
        self.assertEqual(data['deck_graveyard'], sorted(data['deck_graveyard']))
        self.assertEqual(len(data['deck_cards']), len(self.game.deck.cards))

    def test_1_3_timeout_color_selection_softlock_prevented(self):
        """1.3: Game does not soft-lock when a player times out while choosing a color"""
        self.game.start()
        # Simulate player playing a Wild card and waiting to choose color
        wild_card = c.Card(None, None, special=c.CHOOSE)
        self.game.last_card = wild_card
        self.game.choosing_color = True

        # Player times out; do_skip is called
        dummy_bot = SimpleNamespace(sendMessage=lambda *args, **kwargs: None)
        do_skip(dummy_bot, self.p0)

        # last_card must have an auto-assigned valid color, and choosing_color must be False
        self.assertIn(self.game.last_card.color, c.COLORS)
        self.assertFalse(self.game.choosing_color)

        # Next player's matching card must be playable
        next_player = self.game.current_player
        matching_card = c.Card(self.game.last_card.color, '7')
        next_player.cards = [matching_card]
        self.assertIn(matching_card, next_player.playable_cards())

    def test_1_4_serialization_preserves_wild_color(self):
        """1.4: State serialization preserves chosen color on Wild cards"""
        gm = GameManager()
        # Card with special + chosen color
        wild_with_color = c.Card(c.RED, None, special=c.CHOOSE)
        serialized = gm._serialize_card(wild_with_color)
        self.assertEqual(serialized, "colorchooser:r")

        deserialized = gm._deserialize_card(serialized)
        self.assertEqual(deserialized.special, c.CHOOSE)
        self.assertEqual(deserialized.color, c.RED)

        # Also test draw_four
        df_with_color = c.Card(c.GREEN, None, special=c.DRAW_FOUR)
        serialized_df = gm._serialize_card(df_with_color)
        self.assertEqual(serialized_df, "draw_four:g")
        deserialized_df = gm._deserialize_card(serialized_df)
        self.assertEqual(deserialized_df.special, c.DRAW_FOUR)
        self.assertEqual(deserialized_df.color, c.GREEN)

    def test_1_5_first_card_no_duplication(self):
        """1.5: First card is not duplicated into the graveyard"""
        self.game.start()
        first_card_str = str(self.game.last_card)

        # On start, graveyard must NOT contain the first card
        self.assertEqual(len(self.game.deck.graveyard), 0)

        # First player plays a card
        first_player = self.game.current_player
        playable_card = c.Card(self.game.last_card.color or c.RED, '9')
        first_player.cards.append(playable_card)
        first_player.play(playable_card)

        # The first card should be in graveyard exactly ONCE
        graveyard_matches = [str(x) for x in self.game.deck.graveyard if str(x) == first_card_str]
        self.assertEqual(len(graveyard_matches), 1, "Starting card was duplicated into graveyard!")

    def test_2_1_bluffing_detection_and_reset(self):
        """2.1: Bluff detection accurately checks full hand and resets after challenge"""
        self.game.last_card = c.Card(c.RED, '3')

        # Alice has a Red card and a +4 card in hand. She draws a Blue card.
        self.p0.cards = [c.Card(c.RED, '5'), c.Card(None, None, special=c.DRAW_FOUR)]
        self.p0.drew = True
        self.p0.cards.append(c.Card(c.BLUE, '2'))

        # Playing +4 must recognize that she held Red 5 (bluffing = True)
        self.p0.play(c.Card(None, None, special=c.DRAW_FOUR))
        self.assertTrue(self.p0.bluffing)
        # do_play_card records who played the +4; the challenge judges that player
        self.game.last_bluffer = self.p0

        # Now Bob challenges Alice's bluff
        dummy_bot = SimpleNamespace(sendMessage=lambda *args, **kwargs: None)
        do_call_bluff(dummy_bot, self.p1)

        # After challenge resolution, Alice's bluffing state must reset to False
        self.assertFalse(self.p0.bluffing)

    def test_2_2_win_with_wild_card_allowed(self):
        """2.2: Playing a Wild or Draw Four as last card is allowed to win"""
        self.game.last_card = c.Card(c.BLUE, '4')
        wild = c.Card(None, None, special=c.CHOOSE)
        self.p0.cards = [wild]

        playable = self.p0.playable_cards()
        self.assertIn(wild, playable, "Wild card as last card should be playable!")

        df = c.Card(None, None, special=c.DRAW_FOUR)
        self.p0.cards = [df]
        playable_df = self.p0.playable_cards()
        self.assertIn(df, playable_df, "Draw Four as last card should be playable!")

    def test_2_3_wild_on_wild_allowed(self):
        """2.3: Playing a Wild card on another Wild card is allowed after color is chosen"""
        self.game.last_card = c.Card(c.RED, None, special=c.CHOOSE)
        self.game.draw_counter = 0

        # Player has Wild and Draw Four
        wild = c.Card(None, None, special=c.CHOOSE)
        df = c.Card(None, None, special=c.DRAW_FOUR)
        self.p0.cards = [wild, df]

        self.assertTrue(self.p0._card_playable(wild))
        self.assertTrue(self.p0._card_playable(df))

    def test_2_4_first_card_turn_order(self):
        """2.4: First card does not skip Player 1 on a standard number card"""
        # Test number card: Player 0 must be current_player
        self.game.deck.cards = [c.Card(c.GREEN, '5')]
        self.game._first_card_()
        self.assertEqual(self.game.current_player, self.p0)

        # Test Skip card: Player 1 should become current_player
        game2 = Game(SimpleNamespace(id=2, title="Test Group 2", type="group"))
        p0_2 = Player(game2, SimpleNamespace(id=200, first_name="A"))
        p1_2 = Player(game2, SimpleNamespace(id=201, first_name="B"))
        game2.deck.cards = [c.Card(c.GREEN, c.SKIP)]
        game2._first_card_()
        self.assertEqual(game2.current_player, p1_2)

    def test_starting_draw_two_allows_counter_and_turn_order(self):
        """Starting with Draw Two sets draw_counter=2 without skipping Player 1, allowing counter or draw"""
        game = Game(SimpleNamespace(id=3, title="Test Group 3", type="group"))
        p0 = Player(game, SimpleNamespace(id=300, first_name="A"))
        p1 = Player(game, SimpleNamespace(id=301, first_name="B"))
        game.deck.cards = [c.Card(c.BLUE, c.DRAW_TWO)]
        game._first_card_()

        # Player 0 remains current_player, draw_counter is 2
        self.assertEqual(game.current_player, p0)
        self.assertEqual(game.draw_counter, 2)

        # Player 0 has a +2 card and normal cards
        counter_card = c.Card(c.RED, c.DRAW_TWO)
        number_card = c.Card(c.BLUE, '7')
        p0.cards = [counter_card, number_card]

        # Only the counter card (+2) is playable
        playable = p0.playable_cards()
        self.assertIn(counter_card, playable)
        self.assertNotIn(number_card, playable)

        # Player 0 plays counter card: draw_counter stacks to 4 and turn moves to Player 1
        p0.play(counter_card)
        self.assertEqual(game.draw_counter, 4)
        self.assertEqual(game.current_player, p1)

    def test_2_5_linked_list_reversal_integrity(self):
        """2.5: Doubly-linked ring remains consistent when reversed and players leave"""
        self.assertEqual(self.game.players, [self.p0, self.p1, self.p2])

        # Reverse the game direction
        self.game.reverse()
        self.assertEqual(self.p0.next, self.p2)
        self.assertEqual(self.p2.next, self.p1)
        self.assertEqual(self.p1.next, self.p0)

        # Player leaves while reversed
        self.p1.leave()
        self.assertEqual(self.p0.next, self.p2)
        self.assertEqual(self.p2.next, self.p0)
        self.assertEqual(self.p0.prev, self.p2)
        self.assertEqual(self.p2.prev, self.p0)

        # Current player leaves
        self.p0.leave()
        self.assertEqual(self.game.current_player, self.p2)
        self.assertEqual(self.game.players, [self.p2])

    def test_f03_successful_bluff_challenge_retains_challenger_turn(self):
        """F-03: Challenger retains their turn on successful bluff challenge instead of being skipped"""
        self.game.start()
        self.game.last_card = c.Card(c.RED, '5')
        self.game.current_player = self.p1
        self.game.draw_counter = 4
        self.game.last_bluffer = self.p0
        self.p0.bluffing = True
        self.p0.cards = [c.Card(c.BLUE, '1')]

        dummy_bot = SimpleNamespace(sendMessage=lambda *args, **kwargs: None)
        do_call_bluff(dummy_bot, self.p1)

        # Alice bluffed: she draws 4 cards and her bluffing state resets
        self.assertFalse(self.p0.bluffing)
        self.assertEqual(len(self.p0.cards), 5)
        # Bob successfully challenged: draw_counter is reset to 0 and Bob retains his turn!
        self.assertEqual(self.game.draw_counter, 0)
        self.assertEqual(self.game.current_player, self.p1, "Successful challenger was unfairly skipped!")

    def test_f04_win_with_draw_four_bluff_resolution(self):
        """F-04: Playing Wild Draw 4 as last card defers win; bluffer penalized on challenge without punishing bystanders"""
        self.game.start()
        self.game.last_card = c.Card(c.BLUE, None, special=c.DRAW_FOUR)
        self.game.draw_counter = 4
        self.game.last_bluffer = self.p0
        self.game.current_player = self.p1

        # Case 1: Alice played +4 as last card while bluffing
        self.p0.cards = []
        self.p0.pending_win = True
        self.p0.bluffing = True

        dummy_bot = SimpleNamespace(sendMessage=lambda *args, **kwargs: None)
        do_call_bluff(dummy_bot, self.p1)

        # Bluff called: Alice draws 4 cards, pending_win is cancelled, Alice has NOT won
        self.assertFalse(self.p0.pending_win)
        self.assertEqual(len(self.p0.cards), 4)
        self.assertIn(self.p0, self.game.players)
        self.assertEqual(self.game.current_player, self.p1)

        # Case 2: Alice played +4 honestly with no matching colors
        self.p0.cards = []
        self.p0.pending_win = True
        self.p0.bluffing = False
        self.game.last_bluffer = self.p0
        self.game.draw_counter = 4
        self.game.current_player = self.p1

        do_call_bluff(dummy_bot, self.p1)

        # Bob falsely challenged: Bob draws 6 cards and is skipped
        self.assertEqual(self.game.current_player, self.p2)
        # Alice's win is confirmed and she leaves the game cleanly
        self.assertNotIn(self.p0, self.game.players)

    def test_f04_win_with_draw_four_when_challenger_draws(self):
        """F-04: Winner with Wild Draw 4 is awarded win when challenger draws cards"""
        self.game.start()
        self.game.last_card = c.Card(c.YELLOW, None, special=c.DRAW_FOUR)
        self.game.draw_counter = 4
        self.game.last_bluffer = self.p0
        self.p0.cards = []
        self.p0.pending_win = True
        self.game.current_player = self.p1

        dummy_bot = SimpleNamespace(sendMessage=lambda *args, **kwargs: None)
        # Bob accepts penalty and draws
        do_draw(dummy_bot, self.p1)

        # Alice's win is confirmed and she leaves cleanly
        self.assertNotIn(self.p0, self.game.players)
        self.assertEqual(self.game.current_player, self.p2)

    def test_skip_while_choosing_after_draw_four_keeps_penalty_for_next_player(self):
        """A +4 player who times out before choosing a color draws one card; the next player keeps the +4"""
        self.game.start()
        chooser = self.game.current_player
        self.game.last_card = c.Card(None, None, special=c.DRAW_FOUR)
        self.game.draw_counter = 4
        self.game.choosing_color = True
        hand_before = len(chooser.cards)

        dummy_bot = SimpleNamespace(sendMessage=lambda *args, **kwargs: None)
        do_skip(dummy_bot, chooser)

        self.assertEqual(len(chooser.cards), hand_before + 1)
        self.assertEqual(self.game.draw_counter, 4)
        self.assertIsNot(self.game.current_player, chooser)
        self.assertIn(self.game.last_card.color, c.COLORS)

    def test_skip_stops_when_pending_win_ends_the_game(self):
        """If awarding a pending +4 win ends the game, the skip does not keep playing the dead game"""
        self.game.start()
        self.game.last_card = c.Card(c.BLUE, None, special=c.DRAW_FOUR)
        self.game.draw_counter = 4
        self.game.last_bluffer = self.p0
        self.p0.cards = []
        self.p0.pending_win = True
        self.game.current_player = self.p1
        hand_before = len(self.p1.cards)

        dummy_bot = SimpleNamespace(sendMessage=lambda *args, **kwargs: None)
        with patch('actions.player_won') as won, patch('actions.game_is_running', return_value=False):
            do_skip(dummy_bot, self.p1)

        won.assert_called_once_with(dummy_bot, self.p0)
        self.assertEqual(len(self.p1.cards), hand_before)
        self.assertIs(self.game.current_player, self.p1)

    def test_pending_win_awarded_when_challenger_leaves(self):
        """If the challenger of a final +4 leaves, the winner gets the win and nobody inherits the +4"""
        self.game.start()
        self.game.last_card = c.Card(c.BLUE, None, special=c.DRAW_FOUR)
        self.game.draw_counter = 4
        self.game.last_bluffer = self.p0
        self.p0.cards = []
        self.p0.pending_win = True
        self.game.current_player = self.p1

        self.p1.leave()
        dummy_bot = SimpleNamespace(sendMessage=lambda *args, **kwargs: None)
        with patch('actions.player_won') as won, patch('actions.game_is_running', return_value=True):
            ended = settle_draw_four_after_leave(dummy_bot, self.game, self.p1, was_current=True)

        self.assertFalse(ended)
        won.assert_called_once_with(dummy_bot, self.p0)
        self.assertFalse(self.p0.pending_win)
        self.assertIsNone(self.game.last_bluffer)
        self.assertEqual(self.game.draw_counter, 0)

    def test_pending_win_cleared_when_winner_leaves(self):
        """If the pending winner leaves, the pending state is dropped without awarding a win"""
        self.game.start()
        self.game.last_bluffer = self.p0
        self.p0.pending_win = True
        self.game.current_player = self.p1

        dummy_bot = SimpleNamespace(sendMessage=lambda *args, **kwargs: None)
        with patch('actions.player_won') as won:
            settle_draw_four_after_leave(dummy_bot, self.game, self.p0, was_current=False)

        won.assert_not_called()
        self.assertFalse(self.p0.pending_win)
        self.assertIsNone(self.game.last_bluffer)

    def test_playable_check_does_not_change_missing_color(self):
        """Checking playable cards never changes game state, even if the last card has no color"""
        self.game.start()
        self.game.last_card = c.Card(None, None, special=c.CHOOSE)
        self.game.choosing_color = False
        player = self.game.current_player
        player.cards = [c.Card(c.RED, '3')]

        self.assertIn(player.cards[0], player.playable_cards())
        self.assertIsNone(self.game.last_card.color)

    def test_translation_keeps_trailing_newline(self):
        """__() must not strip intentional newlines (announcement + next player line)"""
        self.assertEqual(__("Color set to {color}!\n", multi=True), "Color set to {color}!\n")
        self.assertEqual(__("Color set to {color}!\n"), "Color set to {color}!\n")

    def test_f05_join_game_deck_exhaustion_leaves_no_phantom_player(self):
        """F-05: When DeckEmptyError occurs during mid-game join, no zombie player remains in the ring"""
        chat = SimpleNamespace(id=99, title="Exhaustion Chat", type="group")
        gm = GameManager()
        game = gm.new_game(chat)

        u1 = SimpleNamespace(id=901, first_name="U1", username="u1")
        u2 = SimpleNamespace(id=902, first_name="U2", username="u2")
        gm.join_game(u1, chat)
        gm.join_game(u2, chat)

        # Start game properly with full deck
        game.start()
        for p in game.players:
            p.draw_first_hand()

        initial_players = list(game.players)
        self.assertEqual(len(initial_players), 2)

        # Exhaust deck completely
        game.deck.cards.clear()
        game.deck.graveyard.clear()

        # New user attempts to join but deck is empty
        u3 = SimpleNamespace(id=903, first_name="U3", username="u3")
        with self.assertRaises(DeckEmptyError):
            gm.join_game(u3, chat)

        # Verify no phantom player remains in game.players
        remaining_players = list(game.players)
        self.assertEqual(len(remaining_players), 2)
        self.assertNotIn(u3.id, [p.user.id for p in remaining_players])
        self.assertNotIn(u3.id, gm.userid_players)

        # Circular integrity intact
        p_first = remaining_players[0]
        p_second = remaining_players[1]
        self.assertEqual(p_first.next, p_second)
        self.assertEqual(p_second.next, p_first)
        self.assertEqual(p_first.prev, p_second)
        self.assertEqual(p_second.prev, p_first)

    def test_f07_stale_countdown_timer_does_not_skip_innocent_player(self):
        """F-07: Stale automated countdown skip does not skip the next player when current player moved on"""
        self.game.start()
        # Suppose turn has advanced to Bob (p1)
        self.game.current_player = self.p1
        p1_waiting = self.p1.waiting_time

        # A stale countdown timer fires for Alice (p0)
        dummy_bot = SimpleNamespace(sendMessage=lambda *args, **kwargs: None)
        dummy_job_queue = object()

        do_skip(dummy_bot, self.p0, job_queue=dummy_job_queue)

        # Bob should NOT be skipped!
        self.assertEqual(self.game.current_player, self.p1)
        self.assertEqual(self.p1.waiting_time, p1_waiting)

    def test_f08_color_announcement_when_player_leaves_while_choosing(self):
        """F-08: Color is auto-selected and choosing state cleared when active player leaves"""
        self.game.start()
        self.game.last_card = c.Card(None, None, special=c.CHOOSE)
        self.game.choosing_color = True

        chat = self.game.chat
        gm = GameManager()
        gm.chatid_games[chat.id] = [self.game]
        for p in self.game.players:
            gm.userid_players[p.user.id] = [p]
            gm.userid_current[p.user.id] = p

        # Current player leaves while choosing color
        current_leaving = self.game.current_player
        self.assertTrue(self.game.choosing_color)

        gm.leave_game(current_leaving.user, chat)

        # Game must auto-select color and clear choosing_color
        self.assertIn(self.game.last_card.color, c.COLORS)
        self.assertFalse(self.game.choosing_color)

    def test_shuffle_players_ring_integrity(self):
        """Verify shuffle_players preserves doubly-linked circular ring integrity"""
        p3 = Player(self.game, SimpleNamespace(id=103, first_name="Dave", username="dave"))
        self.assertEqual(len(self.game.players), 4)

        for _ in range(50):
            self.game.shuffle_players()
            players = self.game.players
            self.assertEqual(len(players), 4)
            self.assertEqual(players[0], self.game.current_player)

            # Check every node's circular connections
            for i in range(4):
                curr = players[i]
                nxt = players[(i + 1) % 4]
                prv = players[(i - 1) % 4]
                self.assertEqual(curr.next, nxt)
                self.assertEqual(curr.prev, prv)
                self.assertEqual(curr.next.prev, curr)
                self.assertEqual(curr.prev.next, curr)

    def test_shuffle_players_statistical_uniformity(self):
        """Verify starting player selection is uniformly distributed across all players"""
        start_counts = {self.p0.user.id: 0, self.p1.user.id: 0, self.p2.user.id: 0}
        total_trials = 3000

        for _ in range(total_trials):
            self.game.shuffle_players()
            start_counts[self.game.current_player.user.id] += 1

        # Each of the 3 players should be starter ~1000 times (expect within [800, 1200])
        for uid, count in start_counts.items():
            self.assertTrue(800 <= count <= 1200, f"User {uid} starter count {count} deviates significantly from 1000")

    def test_shuffle_players_config_disabled(self):
        """When SHUFFLE_PLAYERS is disabled, join order and first player are preserved"""
        import game as game_module
        original_flag = game_module.SHUFFLE_PLAYERS
        try:
            game_module.SHUFFLE_PLAYERS = False
            test_game = Game(SimpleNamespace(id=55, title="No Shuffle", type="group"))
            pa = Player(test_game, SimpleNamespace(id=501, first_name="A"))
            pb = Player(test_game, SimpleNamespace(id=502, first_name="B"))
            pc = Player(test_game, SimpleNamespace(id=503, first_name="C"))

            # Start game with a number card. start() refills and shuffles the deck,
            # so keep the fixed deck; a random Skip/Reverse opener would change the order.
            test_game.deck.cards = [c.Card(c.RED, '3')]
            with patch.object(test_game.deck, '_fill_classic_', lambda: None):
                test_game.start()

            # Starting player must remain Player A (first joined)
            self.assertEqual(test_game.current_player, pa)
            self.assertEqual(test_game.players, [pa, pb, pc])
        finally:
            game_module.SHUFFLE_PLAYERS = original_flag

    def test_official_opening_wild_card_allows_starter_color_choice_and_turn(self):
        """Official UNO: Opening regular Wild card prompts first player for color, then lets them take their turn"""
        game = Game(SimpleNamespace(id=77, title="Wild Open", type="group"))
        p0 = Player(game, SimpleNamespace(id=701, first_name="A"))
        p1 = Player(game, SimpleNamespace(id=702, first_name="B"))

        # Setup deck where opening card is a regular Wild card
        wild = c.Card(None, None, special=c.CHOOSE)
        game.deck.cards = [wild]
        game._first_card_()

        # Regular Wild is accepted as opening card
        self.assertEqual(game.last_card.special, c.CHOOSE)
        self.assertTrue(game.choosing_color)
        self.assertTrue(game.opening_color_choice)
        self.assertEqual(game.current_player, p0)

        # Player 0 chooses Green
        game.choose_color(c.GREEN)

        # After choosing, last_card color is GREEN, choosing_color is cleared,
        # and Player 0 RETAINS their turn to play on Green!
        self.assertEqual(game.last_card.color, c.GREEN)
        self.assertFalse(game.choosing_color)
        self.assertFalse(game.opening_color_choice)
        self.assertEqual(game.current_player, p0, "Starting player must retain turn after choosing opening color")

        # Player 0 can now play a Green card
        green_card = c.Card(c.GREEN, '4')
        p0.cards = [green_card]
        self.assertIn(green_card, p0.playable_cards())
        p0.play(green_card)

        # After playing, turn advances to Player 1
        self.assertEqual(game.current_player, p1)

    def test_official_opening_wild_draw_four_is_reshuffled(self):
        """Official UNO: Opening Wild Draw 4 cannot start play; returned to deck and reshuffled"""
        game = Game(SimpleNamespace(id=78, title="DF Open", type="group"))
        Player(game, SimpleNamespace(id=711, first_name="A"))
        Player(game, SimpleNamespace(id=712, first_name="B"))

        # Deck with Draw 4 followed by a Red 5
        df = c.Card(None, None, special=c.DRAW_FOUR)
        num = c.Card(c.RED, '5')
        game.deck.cards = [df, num]
        game._first_card_()

        # Draw 4 was drawn first, put back in deck, and Red 5 became last card
        self.assertEqual(game.last_card, num)
        self.assertIn(df, game.deck.cards)
        self.assertFalse(game.choosing_color)

    def test_opening_color_choice_serialization(self):
        """Opening color choice state is preserved across serialization"""
        gm = GameManager()
        game = Game(SimpleNamespace(id=79, title="Persist Open", type="group"))
        p0 = Player(game, SimpleNamespace(id=721, first_name="A"))
        game.last_card = c.Card(None, None, special=c.CHOOSE)
        game.choosing_color = True
        game.opening_color_choice = True

        serialized = gm._serialize_game(game)
        self.assertTrue(serialized['opening_color_choice'])
        self.assertTrue(serialized['choosing_color'])

        deserialized = gm._deserialize_game(serialized)
        self.assertTrue(deserialized.opening_color_choice)
        self.assertTrue(deserialized.choosing_color)

    def test_no_deck_array_leaked_in_logs(self):
        """Verify Deck.__init__ does not log the entire cards array to logger"""
        import logging
        deck_logger = logging.getLogger('deck')
        with self.assertLogs(deck_logger, level='DEBUG') as log:
            d = Deck()
            d._fill_classic_()
            for msg in log.output:
                # Ensure the string representation of the full list of cards is never logged
                self.assertNotIn("Card(None, None", msg)
                self.assertNotIn("Card('r'", msg)


if __name__ == '__main__':
    unittest.main()
