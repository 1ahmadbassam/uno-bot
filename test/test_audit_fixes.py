import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Installs the module stubs needed to import bot
import test.test_rate_limit_mitigations as stubs

import bot
import card as c
from actions import do_call_bluff, do_skip, forfeit_win, settle_draw_four_after_leave
from errors import DeckEmptyError, NotEnoughPlayersError
from game import Game
from game_manager import GameManager
from player import Player

BOT = SimpleNamespace(sendMessage=lambda *args, **kwargs: None)


def cards_in_play(game, extra_players=()):
    """Every card the game knows about: piles, hands, the top card, plus departed hands"""
    hands = sum(len(p.cards) for p in list(game.players) + list(extra_players))
    return len(game.deck.cards) + len(game.deck.graveyard) + hands + (1 if game.last_card else 0)


class TestAuditFixes(unittest.TestCase):

    def setUp(self):
        self.game = Game(stubs.MockChat(-1))
        self.alice = Player(self.game, stubs.MockUser(1, "Alice"))
        self.bob = Player(self.game, stubs.MockUser(2, "Bob"))
        self.charlie = Player(self.game, stubs.MockUser(3, "Charlie"))
        self.game.started = True
        self.game.deck.cards = [c.Card(c.RED, '1') for _ in range(20)]

    def unanswered_draw_four_by_alice(self):
        self.game.last_card = c.Card(c.BLUE, None, special=c.DRAW_FOUR)
        self.game.draw_counter = 4
        self.game.last_bluffer = self.alice
        self.alice.bluffing = True
        self.game.current_player = self.bob

    # F-V01
    def test_failed_mid_game_join_does_not_duplicate_cards(self):
        gm = GameManager()
        chat = stubs.MockChat(-5)
        game = gm.new_game(chat)
        gm.join_game(stubs.MockUser(1, "A"), chat)
        gm.join_game(stubs.MockUser(2, "B"), chat)
        game.started = True
        game.last_card = c.Card(c.RED, '5')
        game.deck.cards = [c.Card(c.BLUE, str(i)) for i in range(3)]
        before = cards_in_play(game)

        with patch.object(gm, '_persist_chat'), self.assertRaises(DeckEmptyError):
            gm.join_game(stubs.MockUser(3, "C"), chat)

        self.assertEqual(cards_in_play(game), before)

    # F-V02
    def test_challenge_never_judges_a_bystander(self):
        self.unanswered_draw_four_by_alice()
        self.alice.cards = []
        self.alice.pending_win = True
        self.charlie.bluffing = True  # must not matter: Charlie didn't play the +4
        self.alice.leave()

        with patch('actions.send_async'), patch('actions.gm'):
            settle_draw_four_after_leave(BOT, self.game, self.alice, was_current=False)
        self.assertEqual(self.game.draw_counter, 0)

        bob_hand, charlie_hand = len(self.bob.cards), len(self.charlie.cards)
        with patch('actions.send_async'), patch('actions.gm'):
            do_call_bluff(BOT, self.bob)
        self.assertEqual(len(self.bob.cards), bob_hand)
        self.assertEqual(len(self.charlie.cards), charlie_hand)

    def test_challenge_without_known_bluffer_cancels_penalty(self):
        self.unanswered_draw_four_by_alice()
        self.game.last_bluffer = None  # e.g. a save from before the bluffer was stored
        bob_hand, charlie_hand = len(self.bob.cards), len(self.charlie.cards)

        with patch('actions.send_async'), patch('actions.gm'):
            do_call_bluff(BOT, self.bob)

        self.assertEqual(self.game.draw_counter, 0)
        self.assertEqual(len(self.bob.cards), bob_hand)
        self.assertEqual(len(self.charlie.cards), charlie_hand)

    def test_bluffer_survives_save_and_restore(self):
        self.unanswered_draw_four_by_alice()
        gm = GameManager()
        restored = gm._deserialize_game(gm._serialize_game(self.game))
        self.assertIsNotNone(restored.last_bluffer)
        self.assertEqual(restored.last_bluffer.user.id, self.alice.user.id)

    # F-V04
    def test_no_cards_lost_when_bluffer_left(self):
        self.unanswered_draw_four_by_alice()
        self.alice.cards = [c.Card(c.BLUE, '3')]
        self.alice.leave()
        before = cards_in_play(self.game)

        with patch('actions.send_async'), patch('actions.gm'):
            settle_draw_four_after_leave(BOT, self.game, self.alice, was_current=False)
            do_call_bluff(BOT, self.bob)

        self.assertEqual(self.alice.cards, [])
        self.assertEqual(cards_in_play(self.game), before)

    def test_departed_bluffer_cannot_be_given_cards(self):
        """Even if the leave hook didn't run, a challenge never deals cards to a departed player"""
        self.unanswered_draw_four_by_alice()
        self.alice.cards = [c.Card(c.BLUE, '3')]
        self.alice.leave()
        before = cards_in_play(self.game)

        with patch('actions.send_async'), patch('actions.gm'):
            do_call_bluff(BOT, self.bob)

        self.assertEqual(self.alice.cards, [])
        self.assertEqual(cards_in_play(self.game), before)

    # F-V03
    def test_forfeit_credits_the_remaining_player(self):
        self.charlie.leave()
        with patch('actions.rivalry') as rivalry, patch('actions.send_async') as send:
            forfeit_win(BOT, self.game, self.alice.user)

        self.assertIn("Bob won!", send.call_args[1]['text'])
        rivalry.record_first_place.assert_called_once_with(BOT, self.game, self.bob.user, forfeit=True)
        self.assertEqual(self.game.players_won, 1)

    def test_no_forfeit_win_before_the_game_started(self):
        self.game.started = False
        with patch('actions.rivalry') as rivalry, patch('actions.send_async') as send:
            forfeit_win(BOT, self.game, self.alice.user)
        send.assert_not_called()
        rivalry.record_first_place.assert_not_called()

    def test_timeout_removal_in_two_player_game_credits_forfeit(self):
        self.charlie.leave()
        self.game.current_player = self.alice
        self.game.last_card = c.Card(c.RED, '5')
        self.alice.waiting_time = 0  # next timeout removes her
        gm = MagicMock()
        gm.leave_game.side_effect = NotEnoughPlayersError()
        with patch('actions.gm', gm), patch('actions.send_async'), patch('actions.forfeit_win') as forfeit:
            do_skip(BOT, self.alice)
        forfeit.assert_called_once_with(BOT, self.game, self.alice.user)
        gm.end_game.assert_called_once()

    # F-V05
    def test_draw_from_exhausted_deck_does_not_lock_hand(self):
        self.game.current_player = self.alice
        self.game.last_card = c.Card(c.RED, '5')
        self.alice.cards = [c.Card(c.RED, '7'), c.Card(c.GREEN, '1')]
        self.game.deck.cards, self.game.deck.graveyard = [], []

        with self.assertRaises(DeckEmptyError):
            self.alice.draw()

        self.assertTrue(self.alice.drew)  # can still pass
        self.assertFalse(self.alice.drew_card)
        self.assertEqual(self.alice.playable_cards(), [c.Card(c.RED, '7')])

    def test_after_real_draw_only_drawn_card_is_playable(self):
        self.game.current_player = self.alice
        self.game.last_card = c.Card(c.RED, '5')
        self.alice.cards = [c.Card(c.RED, '7')]
        self.game.deck.cards = [c.Card(c.RED, '2')]
        self.alice.draw()
        self.assertTrue(self.alice.drew_card)
        self.assertEqual(self.alice.playable_cards(), [c.Card(c.RED, '2')])

    # F-V06
    def test_stale_token_is_not_announced_as_cheating(self):
        self.game.current_player = self.alice
        self.game.last_card = c.Card(c.RED, '5')
        self.alice.cards = [c.Card(c.RED, '7')]
        stale_token = self.alice.anti_cheat
        self.alice.anti_cheat += 1  # e.g. bumped by a timeout skip

        gm = MagicMock()
        gm.userid_current = {self.alice.user.id: self.alice}
        update = MagicMock()
        update.chosen_inline_result.from_user = self.alice.user
        update.chosen_inline_result.result_id = f"r_7:{stale_token}"
        with patch('bot.gm', gm), patch('bot.send_async') as send, patch('bot.send_paced') as paced:
            inspect.unwrap(bot.process_result)(update, MagicMock())

        send.assert_not_called()
        paced.assert_not_called()
        self.assertIn(c.Card(c.RED, '7'), self.alice.cards)

    # F-V07
    def test_inline_answers_are_personal(self):
        gm = MagicMock()
        gm.userid_players = {}
        update = MagicMock()
        update.inline_query.from_user = self.alice.user
        with patch('bot.gm', gm), patch('bot.answer_async') as answer:
            inspect.unwrap(bot.reply_to_query)(update, MagicMock())
        self.assertTrue(answer.call_args[1].get('is_personal'))

    # F-V09
    def test_playable_cards_does_not_change_bluffing(self):
        self.game.current_player = self.alice
        self.game.last_card = c.Card(c.RED, '5')
        self.alice.cards = [c.Card(c.RED, '7')]
        self.alice.bluffing = False
        self.alice.playable_cards()
        self.assertFalse(self.alice.bluffing)
        self.alice.bluffing = True
        self.alice.playable_cards()
        self.assertTrue(self.alice.bluffing)


if __name__ == '__main__':
    unittest.main()
