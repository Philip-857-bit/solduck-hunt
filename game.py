"""Pure game rules with no Telegram or database dependencies."""

from __future__ import annotations

import random
import secrets

import config
import messages

BOX_COUNT = 9


def generate_hidden_slot() -> int:
    """Return the secret outcome slot for a new board.

    Slots 0-8 map to boxes. Higher slots are losing boards. Therefore any
    particular selected box wins with exactly 1 / WIN_CHANCE probability.
    """
    return secrets.randbelow(config.WIN_CHANCE)


def is_winning_pick(hidden_slot: int, selected_box: int) -> bool:
    return 0 <= selected_box < BOX_COUNT and hidden_slot == selected_box


def random_losing_message() -> str:
    return random.choice(messages.LOSING_MESSAGES)
