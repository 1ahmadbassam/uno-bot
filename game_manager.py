#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Telegram bot to play UNO in group chats
# Copyright (c) 2016 Jannes Höke <uno@jhoeke.de>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import logging
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import card as c
from pony.orm import db_session

from game import Game
from player import Player
from config import STALE_GAME_UNLOAD_SECONDS, STALE_GAME_SCAN_EVERY_SECONDS
from errors import (AlreadyJoinedError, LobbyClosedError, NoGameInChatError,
                    NotEnoughPlayersError, DeckEmptyError)
from persisted_state import PersistedChatState, PersistedUserChat

class GameManager(object):
    """ Manages all running games by using a confusing amount of dicts """

    def __init__(self):
        self.chatid_games = dict()
        self.userid_players = dict()
        self.userid_current = dict()
        self.remind_dict = dict()
        self._last_unload_scan = datetime.now()

        self.logger = logging.getLogger(__name__)

    def _chat_stub(self, chat_id, title=None, chat_type='group'):
        return SimpleNamespace(id=chat_id, title=title, type=chat_type)

    def _user_stub(self, user_id, first_name=None, username=None):
        return SimpleNamespace(id=user_id, first_name=first_name or str(user_id), username=username)

    def _serialize_card(self, card):
        if not card:
            return None
        if card.special and card.color:
            return f"{card.special}:{card.color}"
        return str(card)

    def _deserialize_card(self, value):
        return c.from_str(value) if value else None

    def _serialize_user(self, user):
        if user is None:
            return None
        return {
            'id': user.id,
            'first_name': getattr(user, 'first_name', str(user.id)),
            'username': getattr(user, 'username', None)
        }

    def _deserialize_user(self, data):
        if data is None:
            return None
        return self._user_stub(data['id'], data.get('first_name'), data.get('username'))

    def _serialize_game(self, game):
        players = game.players
        current_player_idx = players.index(game.current_player) if players and game.current_player in players else 0
        return {
            'chat': {
                'id': game.chat.id,
                'title': getattr(game.chat, 'title', None),
                'type': getattr(game.chat, 'type', 'group')
            },
            'started': game.started,
            'reversed': game.reversed,
            'choosing_color': game.choosing_color,
            'opening_color_choice': getattr(game, 'opening_color_choice', False),
            'draw_counter': game.draw_counter,
            'players_won': game.players_won,
            'mode': game.mode,
            'open': game.open,
            'translate': game.translate,
            'owner': list(game.owner or []),
            'starter': self._serialize_user(game.starter),
            'last_card': self._serialize_card(game.last_card),
            # Piles are stored sorted: draws are random picks, so pile order has no
            # meaning and storing it sorted keeps no trace of the in-memory layout.
            'deck_cards': sorted(self._serialize_card(card) for card in game.deck.cards),
            'deck_graveyard': sorted(self._serialize_card(card) for card in game.deck.graveyard),
            'players': [
                {
                    'user': self._serialize_user(player.user),
                    'cards': [self._serialize_card(card) for card in player.cards],
                    'bluffing': player.bluffing,
                    'drew': player.drew,
                    'anti_cheat': player.anti_cheat,
                    'turn_started': player.turn_started.isoformat(),
                    'waiting_time': player.waiting_time,
                    'pending_win': getattr(player, 'pending_win', False)
                }
                for player in players
            ],
            'current_player_idx': current_player_idx,
            'last_activity': game.last_activity.isoformat() if getattr(game, 'last_activity', None) else datetime.now().isoformat()
        }

    def _deserialize_game(self, data):
        chat_data = data['chat']
        game = Game(self._chat_stub(chat_data['id'], chat_data.get('title'), chat_data.get('type', 'group')))
        game.started = data.get('started', False)
        game.reversed = data.get('reversed', False)
        game.choosing_color = data.get('choosing_color', False)
        game.opening_color_choice = data.get('opening_color_choice', False)
        game.draw_counter = data.get('draw_counter', 0)
        game.players_won = data.get('players_won', 0)
        game.mode = data.get('mode')
        game.open = data.get('open', True)
        game.translate = data.get('translate', False)
        game.owner = set(data.get('owner') or [])
        game.starter = self._deserialize_user(data.get('starter'))
        game.last_card = self._deserialize_card(data.get('last_card'))
        game.deck.cards = [self._deserialize_card(card) for card in data.get('deck_cards', [])]
        game.deck.graveyard = [self._deserialize_card(card) for card in data.get('deck_graveyard', [])]
        game.last_activity = datetime.fromisoformat(data.get('last_activity', datetime.now().isoformat()))
        game.job = None

        for player_data in data.get('players', []):
            player = Player(game, self._deserialize_user(player_data['user']))
            player.cards = [self._deserialize_card(card) for card in player_data.get('cards', [])]
            player.bluffing = player_data.get('bluffing', False)
            player.drew = player_data.get('drew', False)
            player.anti_cheat = player_data.get('anti_cheat', 0)
            player.turn_started = datetime.fromisoformat(player_data.get('turn_started', datetime.now().isoformat()))
            player.waiting_time = player_data.get('waiting_time', player.waiting_time)
            player.pending_win = player_data.get('pending_win', False)
            if player.pending_win:
                game.last_bluffer = player

        if game.players:
            idx = data.get('current_player_idx', 0)
            if idx < 0 or idx >= len(game.players):
                idx = 0
            game.current_player = game.players[idx]
        return game

    @db_session
    def _persist_chat(self, chat_id):
        games = self.chatid_games.get(chat_id, [])
        row = PersistedChatState.get(chat_id=chat_id)
        if games:
            payload = json.dumps([self._serialize_game(game) for game in games])
            if row:
                row.payload = payload
                row.updated_at = datetime.now()
            else:
                PersistedChatState(chat_id=chat_id, payload=payload, updated_at=datetime.now())
        elif row:
            row.delete()

        # Keep per-user chat index in sync for fast ensure_user_loaded lookups.
        self._sync_user_chat_index_for_chat(chat_id, games=games)

    def _sync_user_chat_index_for_chat(self, chat_id, games=None):
        if games is None:
            row = PersistedChatState.get(chat_id=chat_id)
            if not row:
                for link in PersistedUserChat.select(chat_id=chat_id):
                    link.delete()
                return
            try:
                games_data = json.loads(row.payload)
            except json.JSONDecodeError:
                self.logger.warning("Skipping corrupted persisted payload for chat_id=%s", chat_id)
                for link in PersistedUserChat.select(chat_id=chat_id):
                    link.delete()
                return

            user_ids = set()
            for game_data in games_data:
                for player_data in game_data.get('players', []):
                    user_data = player_data.get('user') or {}
                    user_id = user_data.get('id')
                    if user_id is not None:
                        user_ids.add(user_id)
        else:
            user_ids = set()
            for game in games:
                for player in game.players:
                    user_ids.add(player.user.id)

        existing_links = list(PersistedUserChat.select(chat_id=chat_id))
        existing_user_ids = {link.user_id for link in existing_links}

        # Delete only stale links to avoid delete+recreate identity-map conflicts.
        for link in existing_links:
            if link.user_id not in user_ids:
                link.delete()

        # Insert only missing links.
        for user_id in user_ids - existing_user_ids:
            PersistedUserChat(user_id=user_id, chat_id=chat_id)

    def persist_game(self, game):
        game.touch()
        self._persist_chat(game.chat.id)

    @db_session
    def _load_chat_from_storage(self, chat_id):
        row = PersistedChatState.get(chat_id=chat_id)
        if not row:
            return []
        data = json.loads(row.payload)
        return [self._deserialize_game(game_data) for game_data in data]

    def _index_loaded_game(self, game):
        for player in game.players:
            self.userid_players.setdefault(player.user.id, []).append(player)
            if player.user.id not in self.userid_current:
                self.userid_current[player.user.id] = player
            elif game.current_player and self.userid_current[player.user.id].game.chat.id == game.chat.id:
                self.userid_current[player.user.id] = player

    def _deindex_game(self, game):
        for player in game.players:
            user_games = self.userid_players.get(player.user.id, [])
            if player in user_games:
                user_games.remove(player)
            if user_games:
                if self.userid_current.get(player.user.id) is player:
                    self.userid_current[player.user.id] = user_games[0]
            else:
                self.userid_players.pop(player.user.id, None)
                self.userid_current.pop(player.user.id, None)

    def load_persisted_games(self):
        self.chatid_games = {}
        self.userid_players = {}
        self.userid_current = {}

        with db_session:
            rows = PersistedChatState.select()[:]

        for row in rows:
            games = [self._deserialize_game(game_data) for game_data in json.loads(row.payload)]
            if games:
                self.chatid_games[row.chat_id] = games
                for game in games:
                    self._index_loaded_game(game)

    def ensure_chat_loaded(self, chat):
        chat_id = chat if isinstance(chat, int) else getattr(chat, 'id', chat)
        if chat_id in self.chatid_games:
            return
        games = self._load_chat_from_storage(chat_id)
        if not games:
            return
        for game in games:
            if hasattr(chat, 'id') and game.chat.id == chat_id:
                game.chat = chat
            self._index_loaded_game(game)
        self.chatid_games[chat_id] = games

    def ensure_user_loaded(self, user_id):
        if user_id in self.userid_players:
            return

        with db_session:
            chat_ids = [uc.chat_id for uc in PersistedUserChat.select(user_id=user_id)]

        for chat_id in chat_ids:
            if chat_id in self.chatid_games:
                continue
            games = self._load_chat_from_storage(chat_id)
            if not games:
                continue
            self.chatid_games[chat_id] = games
            for game in games:
                self._index_loaded_game(game)

    @db_session
    def rebuild_user_chat_index(self):
        """Rebuild persisted user->chat index from persisted chat payloads."""
        for link in PersistedUserChat.select():
            link.delete()

        rows = PersistedChatState.select()[:]
        links_created = 0
        for row in rows:
            try:
                games_data = json.loads(row.payload)
            except json.JSONDecodeError:
                self.logger.warning("Skipping corrupted persisted payload for chat_id=%s", row.chat_id)
                continue

            user_ids = set()
            for game_data in games_data:
                for player_data in game_data.get('players', []):
                    user_data = player_data.get('user') or {}
                    user_id = user_data.get('id')
                    if user_id is not None:
                        user_ids.add(user_id)

            for user_id in user_ids:
                PersistedUserChat(user_id=user_id, chat_id=row.chat_id)
                links_created += 1

        self.logger.info("Rebuilt user-chat index: %d links across %d chats",
                         links_created, len(rows))

    def _maybe_unload_stale_games(self, exclude_chat_id=None):
        if STALE_GAME_UNLOAD_SECONDS <= 0:
            return

        now = datetime.now()
        if now - self._last_unload_scan < timedelta(seconds=STALE_GAME_SCAN_EVERY_SECONDS):
            return

        self._last_unload_scan = now
        stale_chat_ids = []
        for chat_id, games in self.chatid_games.items():
            if chat_id == exclude_chat_id:
                continue
            if not games:
                continue
            newest = max(getattr(game, 'last_activity', now) for game in games)
            if now - newest > timedelta(seconds=STALE_GAME_UNLOAD_SECONDS):
                stale_chat_ids.append(chat_id)

        for chat_id in stale_chat_ids:
            for game in self.chatid_games.get(chat_id, []):
                self._deindex_game(game)
            del self.chatid_games[chat_id]

    @db_session
    def migrate_chat_id(self, old_chat_id, new_chat_id):
        """Move all in-memory and persisted state to a migrated supergroup chat id."""
        if old_chat_id == new_chat_id:
            return

        games = self.chatid_games.pop(old_chat_id, [])
        if games:
            for game in games:
                game.chat = self._chat_stub(
                    new_chat_id,
                    getattr(game.chat, 'title', None),
                    getattr(game.chat, 'type', 'supergroup')
                )
            self.chatid_games.setdefault(new_chat_id, []).extend(games)

        reminders = self.remind_dict.pop(old_chat_id, None)
        if reminders:
            self.remind_dict.setdefault(new_chat_id, set()).update(reminders)

        old_row = PersistedChatState.get(chat_id=old_chat_id)
        if old_row:
            new_row = PersistedChatState.get(chat_id=new_chat_id)
            if new_row:
                old_payload = json.loads(old_row.payload)
                new_payload = json.loads(new_row.payload)
                new_row.payload = json.dumps(new_payload + old_payload)
                new_row.updated_at = datetime.now()
                old_row.delete()
            else:
                PersistedChatState(
                    chat_id=new_chat_id,
                    payload=old_row.payload,
                    updated_at=datetime.now()
                )
                old_row.delete()
        elif games:
            self._persist_chat(new_chat_id)

        self._sync_user_chat_index_for_chat(old_chat_id, games=[])
        self._sync_user_chat_index_for_chat(new_chat_id)

    def new_game(self, chat):
        """
        Create a new game in this chat
        """
        chat_id = chat.id
        self.ensure_chat_loaded(chat)
        self._maybe_unload_stale_games(exclude_chat_id=chat.id)

        self.logger.debug("Creating new game in chat " + str(chat_id))
        game = Game(chat)

        if chat_id not in self.chatid_games:
            self.chatid_games[chat_id] = list()

        # remove old games
        for g in list(self.chatid_games[chat_id]):
            if not g.players:
                self.chatid_games[chat_id].remove(g)

        self.chatid_games[chat_id].append(game)
        self._persist_chat(chat_id)
        return game

    def join_game(self, user, chat):
        """ Create a player from the Telegram user and add it to the game """
        self.ensure_chat_loaded(chat)
        self._maybe_unload_stale_games(exclude_chat_id=chat.id)
        self.logger.info("Joining game with id " + str(chat.id))

        try:
            game = self.chatid_games[chat.id][-1]
        except (KeyError, IndexError):
            raise NoGameInChatError()

        if not game.open:
            raise LobbyClosedError()

        if user.id not in self.userid_players:
            self.userid_players[user.id] = list()

        players = self.userid_players[user.id]

        # Don not re-add a player and remove the player from previous games in
        # this chat, if he is in one of them
        for player in players:
            if player in game.players:
                raise AlreadyJoinedError()

        try:
            self.leave_game(user, chat)
        except NoGameInChatError:
            pass
        except NotEnoughPlayersError:
            self.end_game(chat, user)

            if user.id not in self.userid_players:
                self.userid_players[user.id] = list()

            players = self.userid_players[user.id]

        player = Player(game, user)
        if game.started:
            try:
                player.draw_first_hand()
            except DeckEmptyError:
                player.leave()
                if not players:
                    self.userid_players.pop(user.id, None)
                raise

        players.append(player)
        self.userid_current[user.id] = player
        game.touch()
        self._persist_chat(chat.id)

    def leave_game(self, user, chat):
        """ Remove a player from its current game """
        self.ensure_chat_loaded(chat)
        self._maybe_unload_stale_games(exclude_chat_id=chat.id)

        player = self.player_for_user_in_chat(user, chat)
        players = self.userid_players.get(user.id, list())

        if not player:
            games = self.chatid_games.get(chat.id, list())
            if not games:
                raise NoGameInChatError
            for g in games:
                for p in g.players:
                    if p.user.id == user.id:
                        if p == g.current_player:
                            g.turn()

                        p.leave()
                        g.touch()
                        self._persist_chat(chat.id)
                        return

            raise NoGameInChatError

        game = player.game

        if len(game.players) < 3:
            raise NotEnoughPlayersError()

        if player is game.current_player:
            game.turn()

        player.leave()
        players.remove(player)
        game.touch()

        # If this is the selected game, switch to another
        if self.userid_current.get(user.id, None) is player:
            if players:
                self.userid_current[user.id] = players[0]
            else:
                del self.userid_current[user.id]
                del self.userid_players[user.id]
        self._persist_chat(chat.id)

    def end_game(self, chat, user):
        """
        End a game
        """
        self.ensure_chat_loaded(chat)
        self._maybe_unload_stale_games(exclude_chat_id=chat.id)

        self.logger.info("Game in chat " + str(chat.id) + " ended")

        # Find the correct game instance to end
        player = self.player_for_user_in_chat(user, chat)

        if not player:
            raise NoGameInChatError

        game = player.game

        # Clear game
        for player_in_game in game.players:
            this_users_players = \
                self.userid_players.get(player_in_game.user.id, list())

            try:
                this_users_players.remove(player_in_game)
            except ValueError:
                pass

            if this_users_players:
                try:
                    self.userid_current[player_in_game.user.id] = this_users_players[0]
                except KeyError:
                    pass
            else:
                try:
                    del self.userid_players[player_in_game.user.id]
                except KeyError:
                    pass

                try:
                    del self.userid_current[player_in_game.user.id]
                except KeyError:
                    pass

        self.chatid_games[chat.id].remove(game)
        if not self.chatid_games[chat.id]:
            del self.chatid_games[chat.id]
        self._persist_chat(chat.id)

    def player_for_user_in_chat(self, user, chat):
        if user is None or chat is None:
            return None
        self.ensure_chat_loaded(chat)
        self.ensure_user_loaded(user.id)
        self._maybe_unload_stale_games(exclude_chat_id=chat.id)
        players = self.userid_players.get(user.id, list())
        for player in players:
            if player.game.chat.id == chat.id:
                player.game.touch()
                return player
        return None
