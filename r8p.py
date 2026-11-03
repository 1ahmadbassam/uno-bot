"""R8P's hidden, rising volcano pressure, measured in basis points.

Every completed turn adds 2–6 percentage points (uniform whole basis points).
Draws roll against that pressure; eruptions reset it to 5%, without a lifetime cap.
Both random choices use the game's committed stream for post-game verification.
"""

BASE = 500
SCALE = 10000
MIN_RISE = 200
RISE_RANGE = 401


def advance(game):
    rise = MIN_RISE + game.rng.randbelow(RISE_RANGE)
    game.r8p_pressure = min(SCALE, game.r8p_pressure + rise)
    game.rng.note('rt', game.turns, rise, game.r8p_pressure)


def roll(game):
    pressure = game.r8p_pressure
    hit = game.rng.randbelow(SCALE) < pressure
    game.rng.note('rv', pressure, hit)
    game.r8p_expected += pressure / SCALE
    if hit:
        game.r8p_pressure = BASE
    return hit
