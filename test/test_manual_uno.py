import inspect
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Installs the module stubs needed to import the game modules
import test.test_rate_limit_mitigations as stubs

import actions
import bot
import card as c
import chat_setting
import results
import rivalry_log
import rivalry_stats as rs
import utils
from game import Game
from game_manager import GameManager
from player import Player
from test.test_crazy import game_of
from test.test_fairness_audit import leave_like_game_manager
from test.test_fairness_proof import check, drand_rng
from test.test_inline_validation import chosen_result
from test.test_rivalry_log import A as A_ID, I as I_ID, LoggedGame
from test.test_xeno import card, deck_of


def manual_game(*names, mode='classic'):
    game, seated = game_of(mode, *(names or ('Alice', 'Ivan', 'Bob')))
    game.manual_uno = True
    game.current_player = seated[0]
    game.last_card = card('r_5')
    return game, seated


class Table(unittest.TestCase):
    """Tapping through the inline menu, with what the group sees in self.said"""

    def setUp(self):
        self.job_queue = MagicMock()
        self.paced = self.start(patch('bot.send_paced'))
        self.sent = self.start(patch('bot.send_async'))
        self.start(patch('actions.send_async'))
        self.countdown = self.start(patch('bot.start_player_countdown'))
        self.gm = self.start(patch('bot.gm'))
        self.start(patch('actions.gm', self.gm))
        self.start(patch('bot.game_is_running', return_value=True))

    def start(self, patcher):
        self.addCleanup(patcher.stop)
        return patcher.start()

    @property
    def said(self):
        return [call[1]['text'] for call in self.paced.call_args_list]

    def tap(self, player, name):
        self.gm.userid_current = {player.user.id: player}
        result = chosen_result(player.user, "%s:%s" % (name, player.anti_cheat))
        context = MagicMock(job_queue=self.job_queue)
        inspect.unwrap(bot.process_result)(result, context)

    def time_up(self, game, player, since=None):
        bot._uno_time_up(MagicMock(), player, since or game.uno_since, self.job_queue)


class TestWaitingForUno(Table):

    def test_the_second_to_last_card_holds_the_turn_and_the_bot_says_nothing(self):
        game, (alice, ivan, bob) = manual_game()
        alice.cards = [card('r_7'), card('b_1')]
        self.tap(alice, 'r_7')
        self.assertIs(game.uno_holder, alice)
        self.assertIs(game.current_player, alice)
        self.assertEqual(self.said, [])
        self.countdown.assert_not_called()
        self.job_queue.run_once.assert_called_once()
        self.assertEqual(self.job_queue.run_once.call_args[0][1], 8)

    def test_declaring_moves_the_game_on(self):
        game, (alice, ivan, bob) = manual_game()
        alice.cards = [card('r_7'), card('b_1')]
        self.tap(alice, 'r_7')
        self.tap(alice, 'uno')
        self.assertIsNone(game.uno_holder)
        self.assertIs(game.current_player, alice.next)
        self.assertEqual(self.said, ["Next player: " + bot.display_name(alice.next.user)])
        self.assertEqual(len(alice.cards), 1)
        self.countdown.assert_called_once()

    def test_not_declaring_costs_two_cards_announced_by_the_bot(self):
        game, (alice, ivan, bob) = manual_game()
        alice.cards = [card('r_7'), card('b_1')]
        self.tap(alice, 'r_7')
        with deck_of(game, 'g_1', 'g_2'):
            self.time_up(game, alice)
        self.assertEqual(alice.cards, [card('b_1'), card('g_1'), card('g_2')])
        self.assertIsNone(game.uno_holder)
        self.assertIs(game.current_player, alice.next)
        self.assertEqual(len(self.said), 1)
        self.assertIn("Alice (@alice) didn't declare UNO in time and takes a penalty of 2 cards!", self.said[0])
        self.assertIn("Next player:", self.said[0])

    def test_a_timer_of_an_earlier_wait_does_nothing(self):
        game, (alice, ivan, bob) = manual_game()
        alice.cards = [card('r_7'), card('b_1')]
        self.tap(alice, 'r_7')
        old = game.uno_since
        self.tap(alice, 'uno')
        self.time_up(game, alice, old)
        self.assertEqual(len(alice.cards), 1)
        game.hold_for_uno(alice)
        self.time_up(game, alice, old - timedelta(seconds=1))
        self.assertEqual(len(alice.cards), 1)

    def test_nothing_else_can_happen_meanwhile(self):
        game, (alice, ivan, bob) = manual_game()
        alice.cards = [card('r_7'), card('r_1')]
        self.tap(alice, 'r_7')
        for name in ('r_1', 'draw', 'pass', 'call_bluff', c.RED):
            self.assertFalse(bot._inline_action_allowed(alice, game, name), name)
        self.assertFalse(bot._inline_action_allowed(alice.next, game, 'uno'))
        self.assertTrue(bot._inline_action_allowed(alice, game, 'uno'))
        hand = len(alice.cards)
        with patch('actions.rivalry_log.timed_out') as timed_out:
            actions.do_skip(MagicMock(), alice, self.job_queue)
        timed_out.assert_not_called()
        self.assertEqual(len(alice.cards), hand)

    def test_uno_cant_be_declared_when_nobody_is_waited_for(self):
        game, (alice, ivan, bob) = manual_game()
        self.assertFalse(bot._inline_action_allowed(alice, game, 'uno'))

    def test_a_card_tapped_meanwhile_doesnt_make_the_uno_stale(self):
        game, (alice, ivan, bob) = manual_game()
        alice.cards = [card('r_7'), card('r_1')]
        self.tap(alice, 'r_7')
        token = alice.anti_cheat
        self.tap(alice, 'r_1')
        self.gm.userid_current = {alice.user.id: alice}
        inspect.unwrap(bot.process_result)(chosen_result(alice.user, "uno:%s" % token), MagicMock())
        self.assertIsNone(game.uno_holder)

    def test_only_the_second_to_last_card_waits(self):
        game, (alice, ivan, bob) = manual_game()
        alice.cards = [card('r_7'), card('b_1'), card('b_2')]
        self.tap(alice, 'r_7')
        self.assertIsNone(game.uno_holder)
        self.assertEqual(len(self.said), 1)

    def test_off_says_uno_like_before(self):
        game, (alice, ivan, bob) = manual_game()
        game.manual_uno = False
        alice.cards = [card('r_7'), card('b_1')]
        self.tap(alice, 'r_7')
        self.assertIsNone(game.uno_holder)
        self.assertIn("🔥 UNO!", self.said[0])


class TestCardEffects(Table):

    def declared(self, name, *seats, mode='classic'):
        game, seated = manual_game(*seats, mode=mode)
        seated[0].cards = [card(name), card('b_1')]
        self.tap(seated[0], name)
        self.assertIs(game.current_player, seated[0])
        return game, seated

    def test_a_skip_skips_after_the_uno(self):
        game, (alice, ivan, bob) = self.declared('r_skip')
        skipped_to = alice.next.next
        self.tap(alice, 'uno')
        self.assertIs(game.current_player, skipped_to)

    def test_a_reverse_reverses_after_the_uno(self):
        game, (alice, ivan, bob) = self.declared('r_reverse')
        self.assertTrue(game.reversed)
        self.tap(alice, 'uno')
        self.assertIs(game.current_player, alice.next)

    def test_two_players_a_skip_comes_back(self):
        game, (alice, ivan) = self.declared('r_skip', 'Alice', 'Ivan')
        self.tap(alice, 'uno')
        self.assertIs(game.current_player, alice)
        self.assertFalse(alice.drew)

    def test_a_draw_two_waits_for_the_next_player(self):
        game, (alice, ivan, bob) = self.declared('r_draw')
        self.assertEqual(game.draw_counter, 2)
        with deck_of(game, 'g_1', 'g_2'):
            self.time_up(game, alice)
        self.assertEqual(len(alice.cards), 3)
        self.assertEqual(game.draw_counter, 2)

    def test_a_wild_asks_for_the_color_after_the_uno(self):
        game, (alice, ivan, bob) = manual_game()
        alice.cards = [card('colorchooser'), card('b_1')]
        self.tap(alice, 'colorchooser')
        self.assertEqual(self.said, [])
        self.assertTrue(game.choosing_color)
        self.assertFalse(bot._inline_action_allowed(alice, game, c.RED))
        self.tap(alice, 'uno')
        self.assertIn("please choose a color", self.said[0])
        self.assertIs(game.current_player, alice)
        self.tap(alice, c.RED)
        self.assertIs(game.current_player, alice.next)

    def test_the_penalty_comes_before_the_color_of_a_wild(self):
        game, (alice, ivan, bob) = manual_game()
        alice.cards = [card('colorchooser'), card('b_1')]
        self.tap(alice, 'colorchooser')
        with deck_of(game, 'g_1', 'g_2'):
            self.time_up(game, alice)
        self.assertIn("takes a penalty", self.said[0])
        self.assertIn("please choose a color", self.said[0])

    def test_a_diablo_says_its_color_after_the_uno(self):
        game, (alice, ivan, bob) = manual_game(mode='diablo')
        alice.cards = [card('diablo'), card('b_1')]
        self.tap(alice, 'diablo')
        self.assertEqual(self.said, [])
        self.tap(alice, 'uno')
        self.assertIn("Diablo set the color to", self.said[0])
        self.assertIs(game.current_player, alice.next)

    def test_a_swap_doesnt_wait(self):
        game, (alice, ivan, bob) = manual_game(mode='storm')
        game.storm_cancel = False
        alice.cards = [card('r_swap'), card('b_1')]
        other = alice.next
        other.cards = [card('g_1'), card('g_2'), card('g_3')]
        self.tap(alice, 'r_swap')
        self.assertIsNone(game.uno_holder)
        self.assertEqual(len(other.cards), 1)
        self.assertEqual(len(self.said), 1)

    def test_a_cancelled_swap_waits(self):
        game, (alice, ivan, bob) = manual_game(mode='storm')
        game.storm_cancel = True
        alice.cards = [card('r_swap'), card('b_1')]
        self.tap(alice, 'r_swap')
        self.assertIs(game.uno_holder, alice)


class TestMenu(unittest.TestCase):

    def test_the_option_sends_text_with_the_new_sticker(self):
        game, (alice, ivan, bob) = manual_game()
        options = []
        results.add_uno(game, options)
        self.assertEqual(options[0].sticker_file_id,
                         "CAACAgQAAxUAAWrnGDHeiYfCfca_NUnUdd7e9XsJAALPHwAC-105UxdGk3TYKWX3PQQ")
        self.assertEqual(options[0].input_message_content.message_text, "🔥 UNO!")

    def test_the_buttons_go_while_waiting(self):
        game, (alice, ivan, bob) = manual_game()
        self.assertEqual(len(utils.make_turn_keyboard(game, 1).inline_keyboard), 2)
        game.hold_for_uno(alice)
        self.assertEqual(len(utils.make_turn_keyboard(game, 1).inline_keyboard), 1)

    def menu(self, game, player):
        update = MagicMock()
        update.inline_query.from_user = player.user
        update.inline_query.query = ''
        context = MagicMock()
        gm = MagicMock()
        gm.userid_players = {player.user.id: [player]}
        gm.userid_current = {player.user.id: player}
        with patch('bot.gm', gm), patch('bot.answer_async') as answer:
            inspect.unwrap(bot.reply_to_query)(update, context)
        return [option.id.rsplit(':', 1)[0] for option in answer.call_args[0][2]]

    def test_the_menu_offers_uno_and_a_grey_hand(self):
        game, (alice, ivan, bob) = manual_game()
        alice.cards = [card('r_1')]
        game.hold_for_uno(alice)
        options = self.menu(game, alice)
        self.assertEqual(options[0], 'uno')
        self.assertEqual(len(options), 2)
        self.assertNotIn('r_1', options)
        self.assertNotIn('uno', self.menu(game, alice.next))


class TestButtons(Table):

    def test_old_buttons_do_nothing_meanwhile(self):
        game, (alice, ivan, bob) = manual_game()
        game.hold_for_uno(alice)
        query = MagicMock()
        query.from_user = alice.user
        hand = list(alice.cards)
        with patch('bot._answer_callback') as answer:
            bot._action_callback_for_game(MagicMock(), query, game, 1, 'draw', MagicMock())
            bot._color_callback_for_game(MagicMock(), query, game, 1, c.RED, MagicMock())
        self.assertEqual(answer.call_count, 2)
        self.assertEqual(alice.cards, hand)
        self.assertIs(game.uno_holder, alice)


class TestSkipAndLeave(Table):

    def skip(self, game, user):
        update = MagicMock()
        update.message.chat = game.chat
        update.message.from_user = user
        self.gm.player_for_user_in_chat.return_value = next(p for p in game.players if p.user is user)
        inspect.unwrap(bot.skip_player)(update, MagicMock(job_queue=self.job_queue))

    def test_skip_waits_for_the_time_then_gives_the_penalty(self):
        # After a restart no timer is running, so this is the way out
        game, (alice, ivan, bob) = manual_game()
        alice.cards = [card('r_7'), card('b_1')]
        self.tap(alice, 'r_7')
        self.skip(game, alice.next.user)
        self.assertIs(game.uno_holder, alice)
        self.assertIn("Please wait", self.sent.call_args[1]['text'])
        game.uno_since = datetime.now() - timedelta(seconds=9)
        with deck_of(game, 'g_1', 'g_2'):
            self.skip(game, alice.next.user)
        self.assertIsNone(game.uno_holder)
        self.assertEqual(len(alice.cards), 3)

    def test_leaving_keeps_the_effect_of_the_card_for_the_next_player(self):
        game, (alice, ivan, bob) = manual_game()
        alice.cards = [card('r_draw'), card('b_1')]
        self.tap(alice, 'r_draw')
        nxt = alice.next
        self.assertTrue(bot._release_leaving_player(game, alice))
        self.assertIs(game.current_player, nxt)
        self.assertEqual(game.draw_counter, 2)
        self.assertFalse(bot._release_leaving_player(game, nxt))

    def test_a_wait_survives_a_restart(self):
        game, (alice, ivan, bob) = manual_game()
        alice.cards = [card('r_skip'), card('b_1')]
        alice.play(card('r_skip'))
        gm = GameManager()
        restored = gm._deserialize_game(gm._serialize_game(game))
        self.assertTrue(restored.manual_uno)
        self.assertEqual(restored.uno_holder.user.id, alice.user.id)
        self.assertEqual(restored.uno_since, game.uno_since)
        self.assertEqual(restored.uno_turns, 2)
        self.assertIs(restored.current_player, restored.uno_holder)

    def test_non_current_player_leave_does_not_crash(self):
        game, (alice, ivan, bob) = manual_game()
        self.gm.player_for_user_in_chat.return_value = ivan
        self.gm.leave_game.side_effect = leave_like_game_manager(game)
        update = MagicMock()
        update.message.chat = game.chat
        update.message.from_user = ivan.user
        inspect.unwrap(bot.leave_game)(update, MagicMock(job_queue=self.job_queue))

    def test_uno_holder_leave_releases_and_advances_turn(self):
        game, (alice, ivan, bob) = manual_game()
        alice.cards = [card('r_draw'), card('b_1')]
        self.tap(alice, 'r_draw')
        self.assertIs(game.uno_holder, alice)
        self.gm.player_for_user_in_chat.return_value = alice
        self.gm.leave_game.side_effect = leave_like_game_manager(game)
        update = MagicMock()
        update.message.chat = game.chat
        update.message.from_user = alice.user
        inspect.unwrap(bot.leave_game)(update, MagicMock(job_queue=self.job_queue))
        self.assertIsNone(game.uno_holder)
        self.assertIs(game.current_player, ivan)
        self.assertEqual(game.draw_counter, 2)

    def test_skip_timeout_exact_boundary(self):
        game, (alice, ivan, bob) = manual_game()
        alice.cards = [card('r_7'), card('b_1')]
        self.tap(alice, 'r_7')
        game.uno_since = datetime.now() - timedelta(seconds=7.9)
        self.skip(game, alice.next.user)
        self.assertIs(game.uno_holder, alice)
        self.assertIn("Please wait 1 second", self.sent.call_args[1]['text'])
        game.uno_since = datetime.now() - timedelta(seconds=8.01)
        with deck_of(game, 'g_1', 'g_2'):
            self.skip(game, alice.next.user)
        self.assertIsNone(game.uno_holder)
        self.assertEqual(len(alice.cards), 3)

    def test_held_diablo_color_announced_on_leave_or_kick(self):
        game, (alice, ivan, bob) = manual_game(mode='diablo')
        alice.cards = [card('diablo'), card('b_1')]
        self.tap(alice, 'diablo')
        self.assertIs(game.uno_holder, alice)
        self.gm.player_for_user_in_chat.return_value = alice
        self.gm.leave_game.side_effect = leave_like_game_manager(game)
        update = MagicMock()
        update.message.chat = game.chat
        update.message.from_user = alice.user
        inspect.unwrap(bot.leave_game)(update, MagicMock(job_queue=self.job_queue))
        self.assertIn("Diablo set the color to", self.sent.call_args[1]['text'])

    def test_restart_reschedules_uno_wait(self):
        game, (alice, ivan, bob) = manual_game()
        alice.cards = [card('r_skip'), card('b_1')]
        alice.play(card('r_skip'))
        gm = GameManager()
        restored = gm._deserialize_game(gm._serialize_game(game))
        job_queue = MagicMock()
        bot.reschedule_pending_uno(restored, job_queue)
        job_queue.run_once.assert_called_once()
        self.assertEqual(job_queue.run_once.call_args[0][1], 8)
        self.assertIsNotNone(restored.job)


class TestToggle(unittest.TestCase):

    def run_it(self, chat_type='group', user_id=5, admins=(5,)):
        update = MagicMock()
        update.message.chat = MagicMock(id=-7, type=chat_type)
        update.message.from_user = stubs.MockUser(user_id, "Alice")
        with patch('bot.send_async') as sent, patch('bot.chat_setting.toggle_manual_uno', return_value=True) as toggle, \
                patch('bot.user_is_admin', side_effect=lambda user, b, chat: user.id in admins):
            inspect.unwrap(bot.toggle_manual_uno)(update, MagicMock())
        return toggle, sent.call_args[1]['text']

    def test_group_admins_toggle_it(self):
        toggle, text = self.run_it()
        toggle.assert_called_once_with(-7)
        self.assertIn("Manual UNO is on", text)
        self.assertIn("8 seconds", text)

    def test_other_members_cant(self):
        toggle, text = self.run_it(user_id=6)
        toggle.assert_not_called()
        self.assertIn("Only the group admins", text)

    def test_not_in_a_private_chat(self):
        toggle, text = self.run_it(chat_type='private')
        toggle.assert_not_called()

    def test_off_by_default(self):
        self.assertFalse(chat_setting.manual_uno_enabled(-1))

    def test_a_game_takes_the_setting_of_its_group_when_it_starts(self):
        game, seated = game_of('classic', 'Alice', 'Ivan')
        game.started = False
        with patch('bot.chat_setting.manual_uno_enabled', return_value=True), patch('bot.dispatcher'), \
                patch('bot.start_player_countdown'), patch('bot.gm'), patch('bot.send_async'):
            bot._deal_and_announce(MagicMock(), game, game.chat, MagicMock())
        self.assertTrue(game.manual_uno)


class TestUnoFairness(unittest.TestCase):

    def test_the_penalty_cards_come_from_the_seed(self):
        # Not game_of: its hands don't come from the seed
        game = Game(SimpleNamespace(id=-1, title="Test", type="group"))
        game.rng = drand_rng()
        game.deck.rng = game.rng
        game.mode = 'classic'
        seated = [Player(game, stubs.MockUser(i, str(i))) for i in (1, 2)]
        game.manual_uno = True
        game.start()
        game.started = True
        for player in seated:
            player.draw_first_hand()
        if game.choosing_color:
            game.choose_color(c.RED)
        penalties = 0
        for _ in range(300):
            player = game.current_player
            playable = player.playable_cards()
            if not playable:
                player.draw()
                game.turn()
                continue
            player.play(playable[0])
            if game.uno_holder is player:
                penalties += 1
                actions.take_uno_penalty(player)
            if game.choosing_color:
                game.choose_color(c.RED)
            if not player.cards:
                break
        self.assertGreater(penalties, 0)
        self.assertEqual(check(game.rng.proof())[0], None)


class TestUnoLog(LoggedGame):

    def setUp(self):
        super().setUp()
        self.game.manual_uno = True
        self.alice.cards = [c.Card(c.RED, c.DRAW_TWO), c.Card(c.BLUE, '3')]

    def test_penalties_and_declarations_reach_the_stats(self):
        actions.do_play_card(MagicMock(), self.alice, 'r_draw', notify_color=False, notify_uno=False)
        rivalry_log.declared_uno(self.game, self.alice)
        self.game.release_uno()
        self.assertEqual(self.events('uno')[0]['p'], 'a')
        # Ivan draws the +2, then gets to his second to last card and misses his UNO
        self.ivan.draw()
        self.game.turn()
        self.game.draw_counter = 0
        self.ivan.cards = [c.Card(c.RED, '1'), c.Card(c.BLUE, '3')]
        self.game.hold_for_uno(self.ivan)
        self.ivan.cards = [c.Card(c.BLUE, '3')]
        actions.take_uno_penalty(self.ivan)
        drew = self.events('draw')[-1]
        self.assertEqual((drew['r'], len(drew['cs']), drew['h']), ('uno', 2, 3))
        self.assertNotIn('src', drew)
        log = rivalry_log.finish(self.game, A_ID, 'win')
        d = rs.summarize_log(log)
        self.assertEqual(d['manual_uno']['a'], {'dec': 1, 'pen': 0, 'pc': 0})
        self.assertEqual(d['manual_uno']['b'], {'dec': 0, 'pen': 1, 'pc': 2})
        self.assertEqual(d['b']['fd'], 2)

    def test_the_penalty_isnt_the_pile_waiting_for_the_next_player(self):
        log = {'ev': [
            {'k': 'deal', 't': 0, 'n': 0, 'h': {'a': ['r_1', 'r_2'], 'b': ['g_1']}, 'fc': 'r_5', 'first': 'a'},
            {'k': 'play', 'p': 'a', 'c': 'r_draw', 'h': 1, 't': 1, 'n': 0},
            {'k': 'draw', 'p': 'a', 'r': 'uno', 'cs': ['b_2', 'b_3'], 'h': 3, 't': 2, 'n': 0},
            {'k': 'draw', 'p': 'b', 'r': 'pen', 'cs': ['b_4', 'b_5'], 'src': 'a', 'h': 3, 't': 3, 'n': 1},
            {'k': 'end', 'p': 'a', 'o': 'win', 't': 4, 'n': 2, 'mu': 1},
        ]}
        d = rs.summarize_log(log)
        self.assertEqual(d['stack'], 2)
        self.assertEqual(d['a']['den'], 0)
        self.assertEqual(d['a']['dealt'], 2)
        self.assertEqual(d['manual_uno']['a']['pen'], 1)

    def test_a_game_without_it_has_no_numbers(self):
        self.game.manual_uno = False
        self.assertNotIn('manual_uno', rs.summarize_log(rivalry_log.finish(self.game, A_ID, 'win')))

    def test_the_stats_message_shows_it(self):
        self.play_and_miss()
        log = rivalry_log.finish(self.game, A_ID, 'win')
        rivals = rs.Rivals([(A_ID, "Alice"), (I_ID, "Ivan")])
        result = rs.Result(1, 1, rs.datetime(2026, 11, 1), A_ID, I_ID, 'win', None, 'classic', 2, 3, 2)
        lines = rs.game_stats_text(rivals, 1, result, log, rs.timezone.utc, str).split('\n')
        self.assertIn("1️⃣ UNOs declared · missed: Alice 0 · 1 · Ivan 0 · 0", lines)
        self.assertIn("Cards from missed UNOs: Alice +2 · Ivan +0", lines)

    def play_and_miss(self):
        self.game.hold_for_uno(self.alice)
        actions.take_uno_penalty(self.alice)

    def test_missed_uno_logged_when_deck_empty(self):
        self.alice.cards = [c.Card(c.BLUE, '3')]
        self.game.hold_for_uno(self.alice)
        self.game.deck.cards = []
        actions.take_uno_penalty(self.alice)
        drew = self.events('draw')[-1]
        self.assertEqual((drew['r'], len(drew['cs']), drew['h']), ('uno', 0, 1))
        log = rivalry_log.finish(self.game, A_ID, 'win')
        d = rs.summarize_log(log)
        self.assertEqual(d['manual_uno']['a'], {'dec': 0, 'pen': 1, 'pc': 0})
