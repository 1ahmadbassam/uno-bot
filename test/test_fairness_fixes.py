import unittest
import sys
import types
import secrets
from types import SimpleNamespace

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
            self.stats = False
            self.cards_played = 0
            self.games_played = 0
            self.first_places = 0
        @classmethod
        def get(cls, *args, **kwargs): return None
    us.UserSetting = FakeUserSetting
    sys.modules['user_setting'] = us

from game import Game
from player import Player
from deck import Deck, secure_shuffle
import card as c
from game_manager import GameManager
from actions import do_skip, do_call_bluff, do_play_card, do_draw
from errors import DeckEmptyError


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

    def test_1_2_true_randomness_csprng(self):
        """1.2: Guarantee true randomness (CSPRNG, no seed predictability, uniform distribution)"""
        deck = Deck()
        deck._fill_classic_()
        original_cards = [str(x) for x in deck.cards]

        # Test secure shuffle
        deck.shuffle()
        shuffled_cards = [str(x) for x in deck.cards]
        self.assertEqual(len(original_cards), len(shuffled_cards))
        # It should not remain identical in order
        self.assertNotEqual(original_cards, shuffled_cards)

        # Statistical uniformity check: over 4000 shuffles, card at index 0 is well distributed
        sample_size = 4000
        first_card_colors = {c.RED: 0, c.BLUE: 0, c.GREEN: 0, c.YELLOW: 0, None: 0}
        test_deck = [c.Card(c.RED, '1'), c.Card(c.BLUE, '1'), c.Card(c.GREEN, '1'), c.Card(c.YELLOW, '1')]

        for _ in range(sample_size):
            secure_shuffle(test_deck)
            first_card_colors[test_deck[0].color] += 1

        # Each color should appear ~25% of the time (1000 times). Expect within [800, 1200]
        for color in [c.RED, c.BLUE, c.GREEN, c.YELLOW]:
            count = first_card_colors[color]
            self.assertTrue(800 <= count <= 1200, f"Color {color} count {count} deviates significantly from expected")

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


if __name__ == '__main__':
    unittest.main()
