# UNO Bot

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](./LICENSE)

Private fork of [jh0ker/mau_mau_bot](https://github.com/jh0ker/mau_mau_bot).

## Head-to-head score

The bot can keep the score between two players. It replaces the old uno-scores bot: no manual reporting, no GitHub.

### What counts

- A game counts when both players took part, with any number of players. Whichever of the two finishes **first** wins it.
- If one of them leaves, runs out of time or is kicked by someone else, the other wins **by forfeit**. In a 2-player game, kicking your only opponent ends the game with no winner, so it doesn't count. Neither does a game ended with `/kill`.
- After each result the bot posts the score in the game's chat (leader first) and pins it in place of the previous score. To pin, the bot must be an admin of the group with the right to pin messages.

### Setup

Add a `rivalry` block to `config.json`:

```json
"rivalry": {
    "enabled": true,
    "players": [
        {"id": 111111111, "name": "A"},
        {"id": 222222222, "name": "I"}
    ],
    "chat_id": null,
    "timezone": "UTC",
    "export_dir": null,
    "pin": true
}
```

- `players`: the two Telegram user ids and display names. The first one is `score_a` in the CSV exports, the second one `score_i`.
- `chat_id`: only count games and answer `/score` in this group. `null` means any chat. The two players and the bot admins can also use the commands in a private chat with the bot.
- `timezone`: an IANA name such as `Europe/Berlin`, used for the times shown and written to the exports. Results are stored in UTC. The import also reads the old CSV times in this timezone, so set it to the timezone of the machine the old uno-scores bot ran on.
- `export_dir`: where the website files are written. `null` means a `web` folder next to the database (`./data/web` with docker-compose). `false` turns the exports off.
- `pin`: pin the score message after every result.

### Commands

| Command | Who | What |
| --- | --- | --- |
| `/score` | everyone | Current season: score, win share, both ratios, today/week/month, form, streaks, records, game length and turns, results by mode, time of day and weekday |
| `/score all` | everyone | The same for all seasons together |
| `/score <season>` | everyone | A past season, e.g. `/score 2025` |
| `/score history [n]` | everyone | The last *n* results (10 by default, at most 50) |
| `/score_add <name>` | the two players, bot admins | Record a win for a game played outside the bot |
| `/score_undo` | the two players, bot admins | Remove the newest result |
| `/score_season <name>` | the two players, bot admins | Start a new season from 0 – 0, e.g. `/score_season 2027` |

The first season is created automatically with the current year as its name.

### Website files

On startup and after every change, the bot rewrites these files in `export_dir`:

- `index.html`: the uno-scores chart page (a copy of [web/index.html](web/index.html))
- `scores.csv`: the current season in the uno-scores format (`date,time,score_a,score_i`)
- `old/scores_<season>.csv`: each earlier season, in the same format
- `scores.json`: every result with its details (season, times, winner, outcome, mode, number of players, turns, cards left in the loser's hand), without Telegram ids

Any static web server can serve that folder. For example, next to the bot in `docker-compose.yml`:

```yaml
  scores:
    image: nginx:alpine
    volumes:
      - ./data/web:/usr/share/nginx/html:ro
    ports:
      - "8080:80"
    restart: unless-stopped
```

Right after `/score_season`, `scores.csv` has no rows until the first win, so the chart page shows its "could not load" message until then.

### Importing the uno-scores history

`scripts/import_uno_scores.py` imports the uno-scores CSV files once. Each row becomes one result. `score_a` goes to the first player in `config.json` and `score_i` to the second. Times are read in the configured `timezone`. The script stops with an error, and writes nothing, if a row doesn't add exactly one win. It also stops if the database already holds rivalry data, unless you pass `--replace`.

With docker-compose:

1. Put the `rivalry` block in `config.json` and copy the CSV files into `./data/import/` (for example `scores_2025.csv` and `scores.csv` from the uno-scores repository).
2. Build the new image and stop the bot:
   ```bash
   docker compose build && docker compose stop unobot
   ```
3. Check what would be imported (seasons oldest first, the last one becomes the current season):
   ```bash
   docker compose run --rm --entrypoint python unobot scripts/import_uno_scores.py --dry-run --season 2025=data/import/scores_2025.csv --season 2026=data/import/scores.csv
   ```
4. Run the same command without `--dry-run`. Every season should end with `[OK]`.
5. Start the bot. It writes the website files on startup:
   ```bash
   docker compose up -d
   ```

Without Docker, run the same script from the bot's folder. It uses `$UNO_DB` (or `uno.sqlite3`), and `--db` sets the database path.
