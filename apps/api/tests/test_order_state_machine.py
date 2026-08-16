"""State-machine unit tests (no DB)."""

import pytest

from dosadash_api.services.order_service import ALLOWED_TRANSITIONS, can_transition
from dosadash_shared import OrderState


def test_every_state_has_transition_rules():
    assert set(ALLOWED_TRANSITIONS) == set(OrderState)


@pytest.mark.parametrize(
    ("current", "target", "ok"),
    [
        (OrderState.PLACED, OrderState.CONFIRMED, True),
        (OrderState.PLACED, OrderState.CANCELLED, True),
        (OrderState.PLACED, OrderState.DELIVERED, False),
        (OrderState.CONFIRMED, OrderState.COOKING, True),
        (OrderState.COOKING, OrderState.READY, True),
        (OrderState.COOKING, OrderState.PLACED, False),
        (OrderState.READY, OrderState.OUT_FOR_DELIVERY, True),
        (OrderState.READY, OrderState.CANCELLED, False),
        (OrderState.OUT_FOR_DELIVERY, OrderState.DELIVERED, True),
        (OrderState.DELIVERED, OrderState.REFUNDED, True),
        (OrderState.DELIVERED, OrderState.COOKING, False),
        (OrderState.CANCELLED, OrderState.REFUNDED, True),
        (OrderState.REFUNDED, OrderState.PLACED, False),
    ],
)
def test_transition_table(current: OrderState, target: OrderState, ok: bool):
    assert can_transition(current, target) is ok


def test_terminal_state_refunded_goes_nowhere():
    assert ALLOWED_TRANSITIONS[OrderState.REFUNDED] == frozenset()
