from dosadash_shared import HealthStatus, OrderState, Role


def test_order_states_complete():
    assert [s.value for s in OrderState] == [
        "PLACED",
        "CONFIRMED",
        "COOKING",
        "READY",
        "OUT_FOR_DELIVERY",
        "DELIVERED",
        "CANCELLED",
        "REFUNDED",
    ]


def test_roles():
    assert {r.value for r in Role} == {"customer", "kitchen_staff", "admin", "owner"}


def test_health_status_defaults():
    h = HealthStatus(service="api")
    assert h.status == "ok"
    assert h.service == "api"
