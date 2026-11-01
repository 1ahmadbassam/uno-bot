"""Nitro penalties use the committed game stream, including the six-sided roll."""

from errors import DeckEmptyError
from datetime import datetime
from fairness import seat_label
from internationalization import __
import rivalry_log
from utils import display_color_group, display_name, send_paced, TIMEOUT


def prompt(game):
    pending = game.nitro_pending
    color = display_color_group(game.last_card.color, game)
    if not pending:
        return __("⚡ Nitro set the color to {color}!", multi=game.translate).format(color=color)
    roller = game.current_player.next if game.uno_holder else game.current_player
    return __("⚡ Nitro set the color to {color}! {name}, use /roll for a {number}-card penalty. "
              "Roll 1 to send it back!", multi=game.translate).format(
                  color=color, name=display_name(roller.user), number=pending['amount'])


def roll(bot, player):
    """Resolve the pending penalty; the roller keeps a fresh, unrestricted turn.

    An opening Nitro has no sender: rolling 1 avoids its penalty entirely.
    """
    game = player.game
    if not game.nitro_pending or player is not game.current_player or game.uno_holder:
        return False
    pending = game.nitro_pending
    source = next((p for p in game.players if p.user.id == pending['source']), None)
    # Send this before consuming the roll or scheduling its result.
    bot.sendMessage(game.chat.id, text='🎲', timeout=TIMEOUT)
    value = game.rng.randbelow(6) + 1
    game.rng.note('nr', value, seat_label(player), pending['amount'])
    recipient = source if value == 1 else player
    game.nitro_pending = None
    actual = 0
    if recipient is not None:
        before = len(recipient.cards)
        try:
            for _ in range(pending['amount']):
                recipient.cards.append(game.deck.draw(seat_label(recipient)))
        except DeckEmptyError:
            pass
        actual = len(recipient.cards) - before
        rivalry_log.nitro_roll(game, player, source, value, pending['amount'], recipient, actual)
        rivalry_log.drew(game, recipient, before, 'nitro', src=rivalry_log.key(source if value != 1 else player))
        text = __("🎲 {name} rolled {roll}. {recipient} takes {number} card.",
                  "🎲 {name} rolled {roll}. {recipient} takes {number} cards.", actual, multi=game.translate).format(
            name=display_name(player.user), roll=value, recipient=display_name(recipient.user), number=actual)
    else:
        rivalry_log.nitro_roll(game, player, source, value, pending['amount'], recipient, actual)
        text = __("🎲 {name} rolled 1. The opening Nitro penalty is avoided!", multi=game.translate).format(
            name=display_name(player.user))
    send_paced(bot, game.chat.id, text=text)
    player.drew = False
    player.drew_card = False
    player.turn_started = datetime.now()
    rivalry_log.turn(game)
    player.anti_cheat += 1
    game.touch()
    if source and source.pending_win:
        source.pending_win = False
        if not source.cards:
            from actions import player_won
            player_won(bot, source)
    return True
