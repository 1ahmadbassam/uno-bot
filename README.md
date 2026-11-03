# UNO Bot

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](./LICENSE)

Private fork of [jh0ker/mau_mau_bot](https://github.com/jh0ker/mau_mau_bot).

To exclude modes from Random mode, add their IDs to `config.json` and restart the bot:

```json
"random_gamemode_exclusions": ["r8p", "thunderstorm"]
```

The default is `[]`, which includes every mode. Excluded modes remain available for manual
selection. Use IDs from `modes.py` (`fast` is Sonic mode). Unknown IDs and configurations
that exclude every mode are rejected at startup. Random picks remain equally likely
among the eligible modes, and the fairness proof records that list.
