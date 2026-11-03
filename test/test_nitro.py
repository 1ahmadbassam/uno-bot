import hashlib
import inspect
import json
import shutil
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock, patch

import test.test_rate_limit_mitigations as stubs  # noqa: F401
import actions
import bot
import card as c
import modes
import nitro
import results
import rivalry_log
import rivalry_stats as rs
import utils
from config import TIME_REMOVAL_AFTER_SKIP
from deck import Deck
from game_manager import GameManager
from test.test_crazy import game_of
from test.test_fairness_proof import DECKS_JS, FAIR_JS, INDEX_HTML, check, seeds_rng
import test.test_rainbow as rainbow_tests
from test.test_rivalry_log import log_of
from test.test_inline_validation import chosen_result


def cards(*names):
    return [c.from_str(name) for name in names]


class TestNitro(unittest.TestCase):
    def setUp(self):
        self.game, (self.a, self.b) = game_of('nitro', 'Alice', 'Bob')
        self.bot = MagicMock()
        self.gm = MagicMock()
        self.gm.chatid_games = {-1: [self.game]}
        self.enterContext(patch('actions.gm', self.gm))
        self.enterContext(patch('bot.gm', self.gm))
        self.enterContext(patch('actions.send_async'))
        self.enterContext(patch('actions.send_paced'))
        self.enterContext(patch('actions.game_is_running', return_value=True))
        self.sequence = self.enterContext(patch('nitro.send_paced_sequence'))
        self.won = self.enterContext(patch('actions.player_won'))

    def play(self, player, name):
        actions.do_play_card(self.bot, player, name)

    def arm(self, amount=3):
        self.game.nitro_pending = {'source': self.a.user.id, 'amount': amount}
        self.game.current_player = self.b
        self.game.last_card = c.Card('g', None, c.NITRO)

    def roll(self, value):
        with patch.object(self.game.rng, 'randbelow', side_effect=[value - 1] + [0] * 5):
            return nitro.roll(self.bot, self.b)

    def test_mode_message_deck_and_stickers(self):
        choices = []
        results.add_mode(choices, modes.NITRO)
        self.assertEqual(choices[0].input_message_content.message_text, 'Unleash the Nitro ⚡')
        deck = Deck()
        deck.fill('nitro')
        count = Counter(map(str, deck.cards))
        self.assertEqual(len(deck.cards), 104)
        self.assertEqual(count['nitro'], 8)
        self.assertNotIn('colorchooser', count)
        self.assertNotIn('draw_four', count)
        for color in c.COLORS:
            self.assertNotIn(color + '_0', count)
            for value in ('1', '2', '3', '4', '5', '6', '7', '8', '9', 'draw', 'reverse', 'skip'):
                self.assertEqual(count[color + '_' + value], 2)
        for card in deck.cards:
            self.assertTrue(c.sticker_for(card, self.game))
            self.assertTrue(c.sticker_for(card, self.game, False))

    def test_nitro_randomizes_color_and_penalty_and_prompts(self):
        self.a.cards = cards('nitro', 'r_1')
        with patch.object(self.game.rng, 'choose_color', return_value='b'), \
                patch.object(self.game.rng, 'randbelow', return_value=4), patch('actions.send_async') as sent:
            self.play(self.a, 'nitro')
        self.assertEqual(self.game.last_card.color, 'b')
        self.assertEqual(self.game.nitro_pending, {'source': 1, 'amount': 5})
        self.assertIs(self.game.current_player, self.b)
        text = sent.call_args_list[0].kwargs['text']
        self.assertIn('Blue', text)
        self.assertIn('5-card', text)
        self.assertIn('/roll', text)

    def test_pending_roll_blocks_inline_actions_and_buttons(self):
        self.arm()
        self.b.cards = cards('nitro', 'g_1', 'g_1')
        self.assertEqual(self.b.playable_cards(), [])
        for name in ('draw', 'pass', 'double', 'nitro', 'g_1'):
            self.assertFalse(bot._inline_action_allowed(self.b, self.game, name))
        from utils import make_turn_keyboard
        self.assertEqual(len(make_turn_keyboard(self.game, -1).inline_keyboard), 1)

    def test_roll_one_reflects_and_roller_keeps_turn(self):
        self.arm()
        before = len(self.a.cards), len(self.b.cards)
        self.assertTrue(self.roll(1))
        self.assertEqual((len(self.a.cards), len(self.b.cards)), (before[0] + 3, before[1]))
        self.assertIs(self.game.current_player, self.b)
        self.assertIsNone(self.game.nitro_pending)
        self.assertFalse(self.b.drew)
        self.assertFalse(self.roll(1))

    def test_other_faces_draw_to_roller_with_unrestricted_turn(self):
        for face in range(2, 7):
            with self.subTest(face=face):
                self.arm()
                before = len(self.b.cards)
                self.roll(face)
                self.assertEqual(len(self.b.cards), before + 3)
                self.assertIs(self.game.current_player, self.b)
                self.assertFalse(self.b.drew)
                self.b.cards = cards('g_1', 'r_2')
                self.assertEqual(list(map(str, self.b.playable_cards())), ['g_1'])

    def test_die_message_is_sent_before_result(self):
        self.arm()
        self.roll(6)
        self.bot.sendMessage.assert_not_called()
        calls = [m['text'] for m in self.sequence.call_args.args[2]]
        self.assertEqual(calls[0], '🎲')
        self.assertIn('rolled 6', calls[1])
        self.assertIn('takes 3 cards', calls[1])

    def test_wrong_player_cannot_roll(self):
        self.arm()
        self.assertFalse(nitro.roll(self.bot, self.a))
        self.bot.sendMessage.assert_not_called()
        self.assertIsNotNone(self.game.nitro_pending)

    def test_winning_nitro_waits_and_roll_one_cancels_win(self):
        self.a.cards = cards('nitro')
        self.play(self.a, 'nitro')
        self.assertTrue(self.a.pending_win)
        self.won.assert_not_called()
        self.roll(1)
        self.assertTrue(self.a.cards)
        self.assertFalse(self.a.pending_win)
        self.won.assert_not_called()

    def test_winning_nitro_awarded_after_failed_roll(self):
        self.a.cards = cards('nitro')
        self.play(self.a, 'nitro')
        self.roll(2)
        self.won.assert_called_once_with(self.bot, self.a)

    def test_opening_nitro_roll_one_avoids_penalty(self):
        self.game.deck.cards = cards('nitro')
        self.game._first_card_()
        self.assertIsNotNone(self.game.last_card.color)
        self.assertIsNone(self.game.nitro_pending['source'])
        self.assertIn('/roll', nitro.prompt(self.game))
        self.game.current_player = self.b
        before = len(self.b.cards)
        self.roll(1)
        self.assertEqual(len(self.b.cards), before)

    def test_double_eligibility_toggle_and_exact_second_card(self):
        self.a.cards = cards('r_1', 'r_1', 'b_1', 'b_1', 'nitro', 'nitro')
        self.assertTrue(self.a.can_double())
        self.game.double_enabled = True
        self.assertEqual(list(map(str, self.a.playable_cards())), ['r_1', 'r_1'])
        self.assertTrue(bot._inline_action_allowed(self.a, self.game, 'double'))
        self.game.double_enabled = False
        self.assertIn('nitro', list(map(str, self.a.playable_cards())))
        self.game.double_enabled = True
        self.play(self.a, 'r_1')
        self.assertIs(self.game.current_player, self.a)
        self.assertEqual(self.game.double_card, 'r_1')
        self.assertEqual(list(map(str, self.a.playable_cards())), ['r_1'])
        for name in ('double', 'draw', 'pass', 'b_1', 'nitro'):
            self.assertFalse(bot._inline_action_allowed(self.a, self.game, name))
        self.play(self.a, 'r_1')
        self.assertIs(self.game.current_player, self.b)
        self.assertIsNone(self.game.double_card)

    def test_no_double_after_drawing_or_with_only_special_pairs(self):
        self.a.cards = cards('nitro', 'nitro')
        self.assertFalse(self.a.can_double())
        self.a.cards = cards('r_1', 'r_1')
        self.a.drew = True
        self.assertFalse(self.a.can_double())

    def test_double_draw_twos_stack_to_four(self):
        self.a.cards = cards('r_draw', 'r_draw', 'b_1')
        self.game.double_enabled = True
        self.play(self.a, 'r_draw')
        self.assertEqual(self.game.draw_counter, 0)
        self.play(self.a, 'r_draw')
        self.assertEqual(self.game.draw_counter, 4)
        self.assertIs(self.game.current_player, self.b)

    def test_double_action_effects_with_three_players(self):
        for value, expected in (('skip', 0), ('reverse', 1)):
            game, ps = game_of('nitro', 'A', 'B', 'C')
            ps[0].cards = cards('r_' + value, 'r_' + value, 'b_1')
            game.double_enabled = True
            self.play(ps[0], 'r_' + value)
            self.assertIs(game.current_player, ps[0])
            self.play(ps[0], 'r_' + value)
            self.assertIs(game.current_player, ps[expected])
            self.assertFalse(game.reversed)

    def test_single_and_double_action_turns_with_two_three_and_four_players(self):
        for size in (2, 3, 4):
            for value in ('skip', 'reverse'):
                for double in (False, True):
                    with self.subTest(size=size, value=value, double=double):
                        game, ps = game_of('nitro', *list('ABCD')[:size])
                        ps[0].cards = cards('r_' + value, 'r_' + value, 'b_1')
                        game.double_enabled = double
                        self.play(ps[0], 'r_' + value)
                        if double:
                            self.assertIs(game.current_player, ps[0])
                            self.play(ps[0], 'r_' + value)
                        if size == 2:
                            expected = 0
                        elif value == 'skip':
                            expected = (3 if double else 2) % size
                        else:
                            expected = 1 if double else size - 1
                        self.assertIs(game.current_player, ps[expected])

    def test_double_duel_action_retains_turn_after_manual_uno(self):
        for value in ('skip', 'reverse'):
            with self.subTest(value=value):
                game, (a, b) = game_of('nitro', 'A', 'B')
                game.manual_uno = True
                game.double_enabled = True
                a.cards = cards('r_' + value, 'r_' + value, 'b_1')
                self.play(a, 'r_' + value)
                self.play(a, 'r_' + value)
                self.assertIs(game.uno_holder, a)
                game.release_uno()
                self.assertIs(game.current_player, a)

    def test_double_final_pair_wins_without_intermediate_uno_wait(self):
        self.a.cards = cards('r_1', 'r_1')
        self.game.manual_uno = True
        self.game.double_enabled = True
        self.play(self.a, 'r_1')
        self.assertIsNone(self.game.uno_holder)
        self.won.assert_not_called()
        self.play(self.a, 'r_1')
        self.won.assert_called_once_with(self.bot, self.a)

    def test_restart_preserves_roll_and_pending_win(self):
        self.a.cards = cards('nitro')
        self.play(self.a, 'nitro')
        gm = GameManager()
        restored = gm._deserialize_game(json.loads(json.dumps(gm._serialize_game(self.game))))
        self.assertEqual(restored.nitro_pending, self.game.nitro_pending)
        self.assertTrue(next(p for p in restored.players if p.user.id == 1).pending_win)
        self.assertIsNone(restored.last_bluffer)

    def test_roll_command_validates_sender_and_keeps_turn(self):
        self.arm()
        update, context = MagicMock(), MagicMock()
        update.message.chat = self.game.chat
        update.message.from_user = self.a.user
        with patch('bot.send_async'), patch('bot._prompt_next_player') as prompt, patch('bot.game_is_running', return_value=True):
            inspect.unwrap(bot.roll_command)(update, context)
            self.assertIsNotNone(self.game.nitro_pending)
            prompt.assert_not_called()
            update.message.from_user = self.b.user
            inspect.unwrap(bot.roll_command)(update, context)
            self.assertIsNone(self.game.nitro_pending)
            self.assertIs(self.game.current_player, self.b)
            prompt.assert_called_once()

    def test_double_menu_order_and_toggle_sends_text_instead_of_sticker(self):
        self.a.cards = cards('r_1', 'r_1', 'b_2')
        self.gm.userid_players = {1: [self.a]}
        self.gm.userid_current = {1: self.a}
        update, context = MagicMock(), MagicMock()
        update.inline_query.from_user = self.a.user
        with patch('bot.answer_async') as answer:
            inspect.unwrap(bot.reply_to_query)(update, context)
        entries = answer.call_args.args[2]
        ids = [e.id.split(':')[0] for e in entries]
        self.assertEqual(ids[-2:], ['double', 'gameinfo'])
        self.assertLess(ids.index('r_1'), ids.index('double'))
        self.assertIn('Double play enabled', entries[-2].input_message_content.message_text)
        with patch('bot.send_async') as sent, patch('bot.send_paced') as paced:
            for expected in (True, False):
                token = 'double:%d' % self.a.anti_cheat
                inspect.unwrap(bot.process_result)(chosen_result(self.a.user, token), context)
                self.assertEqual(self.game.double_enabled, expected)
                options = []
                results.add_double(options, self.game)
                self.assertIn('disabled' if expected else 'enabled', options[0].input_message_content.message_text)
                if expected:
                    self.assertEqual(list(map(str, self.a.playable_cards())), ['r_1', 'r_1'])
                else:
                    self.assertTrue(self.a._card_playable(c.from_str('r_2')))
            sent.assert_not_called()
            paced.assert_not_called()

    def test_opening_announcement_has_color_amount_and_roll_prompt(self):
        def opening():
            self.game.last_card = c.Card('y', None, c.NITRO)
            self.game.prepare_nitro(None)
        with patch.object(self.game, '_first_card_', opening), patch('bot.start_player_countdown'), \
                patch('bot.dispatcher') as dispatcher:
            bot._deal_and_announce(self.bot, self.game, self.game.chat, None)
            dispatcher.run_async.call_args.args[0]()
        text = self.bot.sendMessage.call_args.kwargs['text']
        self.assertIn('Yellow', text)
        self.assertIn('-card penalty', text)
        self.assertIn('/roll', text)

    def test_pending_manual_uno_does_not_let_roller_act_early(self):
        self.game.manual_uno = True
        self.a.cards = cards('nitro', 'r_1')
        self.play(self.a, 'nitro')
        self.assertIs(self.game.uno_holder, self.a)
        self.assertIs(self.game.current_player, self.a)
        self.assertFalse(nitro.roll(self.bot, self.a))
        self.game.release_uno()
        self.assertIs(self.game.current_player, self.b)
        self.assertTrue(self.roll(6))

    def test_exhausted_deck_reports_actual_penalty(self):
        self.arm(5)
        self.game.deck.cards = cards('r_1')
        self.game.deck.graveyard = []
        before = len(self.b.cards)
        self.roll(2)
        self.assertEqual(len(self.b.cards), before + 1)
        self.assertIn('takes 1 card.', self.sequence.call_args.args[2][1]['text'])
        self.assertIsNone(self.game.nitro_pending)

    def test_leaving_roll_target_settles_a_deferred_winner(self):
        game, (a, b, other) = game_of('nitro', 'A', 'B', 'C')
        game.current_player = b
        game.nitro_pending = {'source': a.user.id, 'amount': 4}
        a.cards = []
        a.pending_win = True
        b.leave()
        actions.settle_draw_four_after_leave(self.bot, game, b, True)
        self.won.assert_called_once_with(self.bot, a)

    def test_restart_preserves_forced_double(self):
        self.a.cards = cards('r_1', 'r_1', 'b_2')
        self.game.double_enabled = True
        self.play(self.a, 'r_1')
        gm = GameManager()
        restored = gm._deserialize_game(json.loads(json.dumps(gm._serialize_game(self.game))))
        self.assertEqual(restored.double_card, 'r_1')
        self.assertEqual(list(map(str, restored.current_player.playable_cards())), ['r_1'])

    def test_timeout_completes_forced_pair_and_resolves_roll(self):
        self.a.cards = cards('r_1', 'r_1', 'b_2')
        self.game.double_enabled = True
        self.play(self.a, 'r_1')
        actions.do_skip(self.bot, self.a)
        self.assertIsNone(self.game.double_card)
        self.arm()
        actions.do_skip(self.bot, self.b)
        self.assertIsNone(self.game.nitro_pending)
        self.assertIs(self.game.current_player, self.a)

    def test_timeout_second_card_starts_manual_uno_wait(self):
        self.a.cards = cards('r_1', 'r_1', 'b_2')
        self.game.manual_uno = True
        self.game.double_enabled = True
        self.play(self.a, 'r_1')
        with patch('bot._start_uno_countdown') as countdown:
            actions.do_skip(self.bot, self.a)
        self.assertIs(self.game.uno_holder, self.a)
        countdown.assert_called_once_with(self.game, None)

    def test_leaving_cancels_pending_penalty(self):
        for leaving in (self.a, self.b):
            game, (a, b) = game_of('nitro', 'A', 'B')
            game.nitro_pending = {'source': a.user.id, 'amount': 4}
            game.current_player = b
            (a if leaving is self.a else b).leave()
            self.assertIsNone(game.nitro_pending)

    def registered_manager(self, game):
        manager = GameManager()
        manager.chatid_games = {game.chat.id: [game]}
        manager.userid_players = {p.user.id: [p] for p in game.players}
        manager.userid_current = {p.user.id: p for p in game.players}
        return manager

    def test_real_leave_announces_sender_and_target_cancellations_once(self):
        for leaving_source in (True, False):
            with self.subTest(source=leaving_source):
                game, (a, b, other) = game_of('nitro', 'A', 'B', 'C')
                game.current_player = b
                game.nitro_pending = {'source': a.user.id, 'amount': 4}
                gm = self.registered_manager(game)
                leaving = a if leaving_source else b
                with patch.object(gm, '_persist_chat'), patch('actions.gm', gm), \
                        patch('actions.send_async') as sent:
                    gm.leave_game(leaving.user, game.chat)
                    self.assertIsNone(game.nitro_pending)
                    self.assertIs(game.current_player, b if leaving_source else other)
                    actions.cancel_penalty_after_leave(self.bot, game, not leaving_source, False)
                    self.assertIn('played the Nitro' if leaving_source else 'facing the Nitro', sent.call_args.kwargs['text'])
                    self.assertIsNone(game.nitro_cancelled)
                    actions.cancel_penalty_after_leave(self.bot, game, not leaving_source, False)
                    sent.assert_called_once()

    def test_unrelated_player_leaving_keeps_pending_roll(self):
        game, (a, b, other) = game_of('nitro', 'A', 'B', 'C')
        game.current_player = b
        game.nitro_pending = {'source': a.user.id, 'amount': 4}
        gm = self.registered_manager(game)
        with patch.object(gm, '_persist_chat'), patch('actions.send_async') as sent:
            gm.leave_game(other.user, game.chat)
            actions.cancel_penalty_after_leave(self.bot, game, False, False)
        sent.assert_not_called()
        self.assertIsNotNone(game.nitro_pending)

    def test_pending_roll_timeout_counts_once_and_consumes_turn(self):
        self.arm(3)
        before = len(self.b.cards)
        waiting = self.b.waiting_time
        with patch.object(self.game.rng, 'randbelow', side_effect=[5, 0, 0, 0]), \
                patch('actions.rivalry_log.timed_out') as timed_out:
            actions.do_skip(self.bot, self.b)
        timed_out.assert_called_once_with(self.game, self.b)
        self.assertEqual(self.b.waiting_time, max(0, waiting - TIME_REMOVAL_AFTER_SKIP))
        self.assertEqual(len(self.b.cards), before + 3)
        self.assertIs(self.game.current_player, self.a)
        self.assertIsNone(self.game.nitro_pending)

    def test_pending_roll_timeout_at_zero_removes_player_without_rolling(self):
        game, (a, b, other) = game_of('nitro', 'A', 'B', 'C')
        game.current_player = b
        game.nitro_pending = {'source': a.user.id, 'amount': 4}
        b.waiting_time = 0
        gm = self.registered_manager(game)
        with patch.object(gm, '_persist_chat'), patch.object(gm, 'persist_game'), patch('actions.gm', gm), \
                patch('nitro.roll') as roll, patch('actions.rivalry_log.timed_out') as timed_out:
            actions.do_skip(self.bot, b)
        timed_out.assert_called_once_with(game, b)
        roll.assert_not_called()
        self.assertNotIn(b, game.players)
        self.assertIs(game.current_player, other)
        self.assertIsNone(game.nitro_pending)

    def test_die_and_result_use_chat_queue_and_retry_without_reroll(self):
        self.arm()
        with patch('utils.game_thread_runner.submit') as submit:
            self.sequence.side_effect = utils.send_paced_sequence
            self.roll(6)
        self.bot.sendMessage.assert_not_called()
        args = submit.call_args.args
        self.assertEqual(args[0], 'pacing:-1')
        self.assertIs(args[1], utils._send_message_sequence)
        self.bot.sendMessage.side_effect = [utils.RetryAfter(1), None, None]
        with patch('utils.time.sleep'):
            args[1](*args[2:])
        self.assertEqual([call.kwargs['text'] for call in self.bot.sendMessage.call_args_list][:2], ['🎲', '🎲'])
        self.assertIn('rolled 6', self.bot.sendMessage.call_args.kwargs['text'])
        self.assertEqual(sum(op[0] == 'nr' for op in self.game.rng.ops), 1)
        self.assertFalse(nitro.roll(self.bot, self.b))

    def test_die_delivery_failure_does_not_reopen_roll(self):
        self.arm()
        self.roll(6)
        messages = self.sequence.call_args.args[2]
        self.bot.sendMessage.side_effect = [TimeoutError('Telegram unavailable'), None]
        with self.assertLogs('utils', level='ERROR'):
            utils._send_message_sequence(self.bot, -1, messages)
        self.assertEqual(self.bot.sendMessage.call_count, 2)
        self.assertIn('rolled 6', self.bot.sendMessage.call_args.kwargs['text'])
        self.assertIsNone(self.game.nitro_pending)
        self.assertFalse(nitro.roll(self.bot, self.b))

    def test_roll_is_registered_in_command_menu(self):
        commandlist = Path(__file__).resolve().parents[1] / 'commandlist.txt'
        self.assertIn('roll', [line.partition(' - ')[0] for line in commandlist.read_text().splitlines()])

    def test_summary_counts_nitro_and_double_events(self):
        log = log_of(
            {'k': 'play', 'p': 'a', 'c': 'nitro', 'h': 1, 't': 1, 'n': 0},
            {'k': 'color', 'p': 'a', 'co': 'g', 'ni': 1, 'lk': 1, 't': 2, 'n': 0},
            {'k': 'roll', 'p': 'b', 'vs': 'a', 'roll': 1, 'amount': 3, 'to': 'a', 't': 3, 'n': 1},
            {'k': 'draw', 'p': 'a', 'r': 'nitro', 'src': 'b', 'cs': ['g_1'] * 3, 'h': 4, 't': 4, 'n': 1},
            {'k': 'play', 'p': 'b', 'c': 'g_draw', 'h': 2, 't': 5, 'n': 1},
            {'k': 'double', 'p': 'b', 'part': 2, 't': 6, 'n': 2},
            {'k': 'end', 'p': 'b', 'o': 'win', 'ni': 1, 't': 7, 'n': 3})
        d = rs.summarize_log(log)
        a, b = d['nitro']['a'], d['nitro']['b']
        self.assertEqual((a['p'], a['received'], a['lk']), (1, 3, 1))
        self.assertEqual((b['rolls'], b['ones'], b['returned'], b['sent'], b['double']), (1, 1, 3, 3, 1))
        self.assertEqual(b['faces'], [1, 0, 0, 0, 0, 0])
        self.assertEqual(b['pairs'], [0, 1, 0, 0])
        self.assertEqual(d['a']['ty'][-1], 1)
        rivals = rs.Rivals([(1, 'A'), (2, 'B')])
        result = rs.Result(1, 1, rs.datetime(2026, 11, 1), 2, 1, 'win', mode='nitro')
        text = rs.game_stats_text(rivals, 1, result, log, rs.timezone.utc, str)
        self.assertIn('Nitros played:', text)
        self.assertIn('Double plays:', text)
        self.assertIn('Die faces 1–6:', text)

    def proof_game(self):
        game, ps = game_of('nitro', 'A', 'B')
        game.rng = seeds_rng(['a', 'b'])
        game.rng.seed = bytes(range(32))
        game.rng.commitment = hashlib.sha256(game.rng.seed).hexdigest()
        game.deck.rng = game.rng
        with patch.dict(rivalry_log.KEYS, {1: 'a', 2: 'b'}), patch('rivalry_log.ENABLED', True), \
                patch('rivalry_log.RIVALRY_CHAT_ID', None), patch('game.SHUFFLE_PLAYERS', False):
            game.start()
            for p in ps:
                p.cards = []
                p.draw_first_hand()
            rivalry_log.start(game)
            for _ in range(150):
                p = game.current_player
                if game.nitro_pending:
                    nitro.roll(self.bot, p)
                elif game.double_card or (p.playable_cards() and len(p.cards) > 3):
                    if p.can_double():
                        game.double_enabled = True
                    self.play(p, str(p.playable_cards()[0]))
                else:
                    actions.do_draw(self.bot, p)
                    if game.current_player is p:
                        game.turn()
            events = game.rivalry_log['ev']
        return game.rng.proof(), events

    def test_all_randomness_replays_in_python(self):
        proof, _ = self.proof_game()
        self.assertTrue(any(op[0] == 'np' for op in proof['ops']))
        self.assertTrue(any(op[0] == 'nr' for op in proof['ops']))
        self.assertIsNone(check(proof)[0])

    @unittest.skipUnless(shutil.which('node'), 'Node.js is not installed')
    def test_browser_verifies_rolls_and_rejects_tampering(self):
        proof, events = self.proof_game()
        script = '\n'.join(Path(p).read_text(encoding='utf-8') for p in (DECKS_JS, FAIR_JS))
        script += '''
(async function () {
  var p = %s, events = %s;
  var valid = await verifyProof(p, events);
  var roll = events.find(function (e) { return e.k === 'roll'; });
  roll.roll = roll.roll === 6 ? 1 : roll.roll + 1;
  var invalid = await verifyProof(p, events);
  console.log(JSON.stringify([valid, invalid]));
})();
''' % (json.dumps(proof), json.dumps(events))
        valid, invalid = rainbow_tests.TestRainbow.run_browser_script(self, script)
        self.assertTrue(valid['ok'], valid)
        self.assertFalse(invalid['ok'], invalid)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is not installed')
    def test_website_nitro_statistics_and_fairness_rows(self):
        proof, events = self.proof_game()
        events = events + [{'k': 'end', 'p': 'a', 'o': 'win', 'ni': 1, 't': 100, 'n': 100}]
        summary = rs.summarize_log({'ev': events})
        page = Path(INDEX_HTML).read_text(encoding='utf-8')
        script = '\n'.join(Path(p).read_text(encoding='utf-8') for p in (DECKS_JS, FAIR_JS))
        script += '\nvar P = ["a", "b"]; var COL = { a: "red", b: "blue" };\n'
        script += page[page.index('var CARD_TYPES ='):page.index('var COL =')]
        script += page[page.index('function cardType(card)'):page.index('// Totals over the games')]
        script += page[page.index('var FAIR_MIN_GAMES ='):page.index('function fairBadge(m)')]
        script += page[page.index('function renderNitro(R)'):page.index('function renderManualUno(R)')]
        script += '''
var els = {}, charts = [];
function $(id) { return els[id] || (els[id] = { style: {} }); }
function sum(a) { return a.reduce(function (s, n) { return s + n; }, 0); }
function sw() { return ''; }
function nm(p) { return p; }
function f1(n) { return n.toFixed(1); }
function plural(n, w) { return n + ' ' + w + (n === 1 ? '' : 's'); }
function share(n, d) { return d ? n / d : 0; }
function pct(n) { return n; }
function pairTile(title, a, b) { return title + ':' + a + '/' + b; }
function addInto(a, b) { b.forEach(function (v, i) { a[i] = (a[i] || 0) + v; }); }
function barChart(el, spec) { charts.push(spec); }
var games = [{ mode: 'nitro', w: 'a', d: %s }];
var shown = renderNitro({ ms: games });
console.log(JSON.stringify({ shown: shown, tiles: els['ni-tiles'].innerHTML, charts: charts,
  tests: fairTests(games), mix: deckMix('nitro') }));
''' % json.dumps(summary)
        out = rainbow_tests.TestRainbow.run_browser_script(self, script)
        self.assertTrue(out['shown'])
        self.assertIn('Double plays completed', out['tiles'])
        self.assertEqual(len(out['charts']), 4)
        self.assertAlmostEqual(out['mix'][2], 8 / 104)
        self.assertIn('Nitro penalty sizes', [t['name'] for t in out['tests']])
