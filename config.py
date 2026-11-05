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


import os
import json
import modes

try:
    with open("config.json", "r") as f:
        config = json.load(f)
except FileNotFoundError:
    config = {}

TOKEN = os.getenv("TOKEN", config.get("token"))
WORKERS = int(os.getenv("WORKERS", config.get("workers", 32)))
ADMIN_LIST = os.getenv("ADMIN_LIST", config.get("admin_list", None))

if isinstance(ADMIN_LIST, str):
    ADMIN_LIST = set(int(x) for x in ADMIN_LIST.replace(',', ' ').split())
elif isinstance(ADMIN_LIST, (list, set, tuple)):
    ADMIN_LIST = set(int(x) for x in ADMIN_LIST)
else:
    ADMIN_LIST = None

ALLOWED_GROUP_ID = os.getenv("ALLOWED_GROUP_ID", config.get("allowed_group_id", None))
if ALLOWED_GROUP_ID is not None and str(ALLOWED_GROUP_ID).strip() != "":
    ALLOWED_GROUP_ID = int(ALLOWED_GROUP_ID)
else:
    ALLOWED_GROUP_ID = None

ALLOWED_USER_IDS = os.getenv("ALLOWED_USER_IDS", config.get("allowed_user_ids", None))
if isinstance(ALLOWED_USER_IDS, str):
    ALLOWED_USER_IDS = set(int(x) for x in ALLOWED_USER_IDS.replace(',', ' ').split())
elif isinstance(ALLOWED_USER_IDS, (list, set, tuple)):
    ALLOWED_USER_IDS = set(int(x) for x in ALLOWED_USER_IDS)
else:
    ALLOWED_USER_IDS = None

OPEN_LOBBY = os.getenv("OPEN_LOBBY", config.get("open_lobby", True))
ENABLE_TRANSLATIONS = os.getenv("ENABLE_TRANSLATIONS", config.get("enable_translations", False))

if isinstance(OPEN_LOBBY, str):
    OPEN_LOBBY = OPEN_LOBBY.lower() in ("yes", "true", "t", "1")

if isinstance(ENABLE_TRANSLATIONS, str):
    ENABLE_TRANSLATIONS = ENABLE_TRANSLATIONS.lower() in ("yes", "true", "t", "1")

DEFAULT_GAMEMODE = os.getenv("DEFAULT_GAMEMODE", config.get("default_gamemode", "fast"))
# Only affects the random roll; excluded modes remain available in the inline picker.
RANDOM_GAMEMODE_EXCLUSIONS = config.get("random_gamemode_exclusions", [])
if not isinstance(RANDOM_GAMEMODE_EXCLUSIONS, list) or any(
        not isinstance(mode, str) for mode in RANDOM_GAMEMODE_EXCLUSIONS):
    raise ValueError("random_gamemode_exclusions must be a JSON array of game mode IDs")
RANDOM_GAMEMODE_EXCLUSIONS = frozenset(RANDOM_GAMEMODE_EXCLUSIONS)
if RANDOM_GAMEMODE_EXCLUSIONS - modes.MODES.keys():
    raise ValueError("Unknown mode IDs in random_gamemode_exclusions: " +
                     ", ".join(sorted(RANDOM_GAMEMODE_EXCLUSIONS - modes.MODES.keys())))
if not modes.MODES.keys() - RANDOM_GAMEMODE_EXCLUSIONS:
    raise ValueError("random_gamemode_exclusions must leave at least one game mode available")
# An empty selective pool uses the same eligible modes as ordinary Random.
SELECTRANDOM_GAMEMODES = config.get("selectrandom_gamemodes", [])
if not isinstance(SELECTRANDOM_GAMEMODES, list) or any(
        not isinstance(mode, str) for mode in SELECTRANDOM_GAMEMODES):
    raise ValueError("selectrandom_gamemodes must be a JSON array of game mode IDs")
if set(SELECTRANDOM_GAMEMODES) - modes.MODES.keys():
    raise ValueError("Unknown mode IDs in selectrandom_gamemodes: " +
                     ", ".join(sorted(set(SELECTRANDOM_GAMEMODES) - modes.MODES.keys())))
SELECTRANDOM_GAMEMODES = tuple(mode for mode in modes.MODES if mode in SELECTRANDOM_GAMEMODES)
WAITING_TIME = int(os.getenv("WAITING_TIME", config.get("waiting_time", 120)))
TIME_REMOVAL_AFTER_SKIP = int(os.getenv("TIME_REMOVAL_AFTER_SKIP", config.get("time_removal_after_skip", 20)))
MIN_FAST_TURN_TIME = int(os.getenv("MIN_FAST_TURN_TIME", config.get("min_fast_turn_time", 15)))
MIN_PLAYERS = int(os.getenv("MIN_PLAYERS", config.get("min_players", 2)))
# Official UNO allows up to 10 players; more can run the deck dry while dealing
MAX_PLAYERS = int(os.getenv("MAX_PLAYERS", config.get("max_players", 10)))
STALE_GAME_UNLOAD_SECONDS = int(os.getenv("STALE_GAME_UNLOAD_SECONDS", config.get("stale_game_unload_seconds", 1800)))
STALE_GAME_SCAN_EVERY_SECONDS = int(os.getenv("STALE_GAME_SCAN_EVERY_SECONDS", config.get("stale_game_scan_every_seconds", 60)))
DEFAULT_CARDSET = os.getenv("DEFAULT_CARDSET", config.get("default_cardset", "classic")).lower()
SHUFFLE_PLAYERS = os.getenv("SHUFFLE_PLAYERS", config.get("shuffle_players", True))
TURN_PACING_DELAY = float(os.getenv("TURN_PACING_DELAY", config.get("turn_pacing_delay", 1.5)))
# Whether players have to declare UNO themselves in a group that hasn't used /uno, and
# the seconds they have to do it
MANUAL_UNO = os.getenv("MANUAL_UNO", config.get("manual_uno", False))
if isinstance(MANUAL_UNO, str):
    MANUAL_UNO = MANUAL_UNO.lower() in ("yes", "true", "t", "1")
UNO_DECLARE_TIME = int(os.getenv("UNO_DECLARE_TIME", config.get("uno_declare_time", 8)))

if isinstance(SHUFFLE_PLAYERS, str):
    SHUFFLE_PLAYERS = SHUFFLE_PLAYERS.lower() in ("yes", "true", "t", "1")

# Provably fair games (fairness.py): commit to a seed, fetch one drand round per game
FAIR_PLAY = os.getenv("FAIR_PLAY", config.get("fair_play", True))
if isinstance(FAIR_PLAY, str):
    FAIR_PLAY = FAIR_PLAY.lower() in ("yes", "true", "t", "1")
# With fair_play_required, a game whose randomness can't be checked doesn't start
FAIR_PLAY_REQUIRED = os.getenv("FAIR_PLAY_REQUIRED", config.get("fair_play_required", False))
if isinstance(FAIR_PLAY_REQUIRED, str):
    FAIR_PLAY_REQUIRED = FAIR_PLAY_REQUIRED.lower() in ("yes", "true", "t", "1")

# Head-to-head score between two players, recorded automatically when one of them wins
RIVALRY = config.get("rivalry") or {}
RIVALRY_ENABLED = bool(RIVALRY.get("enabled", False))
# [(telegram_id, name), (telegram_id, name)]: the order is the column order of the exports
RIVALRY_PLAYERS = [(int(p["id"]), str(p.get("name") or p["id"])) for p in RIVALRY.get("players") or []]
RIVALRY_CHAT_ID = int(RIVALRY["chat_id"]) if RIVALRY.get("chat_id") not in (None, "") else None
RIVALRY_TIMEZONE = RIVALRY.get("timezone") or "UTC"
# None: a "web" folder next to the database; false: no export
RIVALRY_EXPORT_DIR = RIVALRY.get("export_dir")
RIVALRY_PIN = bool(RIVALRY.get("pin", True))
# Optional chart colors of the two players, in the order of "players" ("#rrggbb" or None)
RIVALRY_COLORS = [p.get("color") or None for p in RIVALRY.get("players") or []]
# Linked from /score and the score messages when set
RIVALRY_WEB_URL = RIVALRY.get("web_url") or None
