"""DosaDash load test (Phase 9 hardening — docs/05 week 12).

Realistic traffic mix against a running stack (local compose, staging, or
prod off-peak): anonymous menu browsing (incl. Tamil), logged-in customers
(demo-OTP login on start → real per-user JWTs), optional order placement
and agent chat.

Run headless:

    uv run --group loadtest locust -f infra/loadtest/locustfile.py \
        --host http://localhost:8000 --headless -u 30 -r 3 -t 2m \
        --csv infra/loadtest/out

Env knobs (both default OFF — they write data / spend LLM money):

    LOADTEST_PLACE_ORDERS=1   Customer users place real COD orders
    LOADTEST_CHAT=1           Customer users hit the order agent (LLM spend!)

Rate-limiter interplay (dosadash_api/ratelimit.py):
    - Customers log in on start, so their traffic lands in per-USER buckets —
      realistic per-account limits apply.
    - Anonymous browsers share the runner's IP, so the read tier (240/min/IP)
      becomes the ceiling by design. For pure capacity measurement set
      API_RATE_LIMIT_ENABLED=false on the target; leave it on to watch the
      limiter defend the service (429s counted separately below).
"""

import os
import random
from typing import Any

from locust import HttpUser, between, events, task

PLACE_ORDERS = os.environ.get("LOADTEST_PLACE_ORDERS", "") == "1"
CHAT = os.environ.get("LOADTEST_CHAT", "") == "1"

# 429s are marked success (the limiter WORKING is not a service failure) but
# counted here and printed on shutdown so runs against a limited target stay
# honest about how much traffic was shed.
RATE_LIMITED_COUNT = {"n": 0}


@events.quitting.add_listener
def _print_rate_limited(environment: Any, **_kwargs: Any) -> None:
    print(f"[ratelimit] 429 responses observed during run: {RATE_LIMITED_COUNT['n']}")


CHAT_PROMPTS = [
    "Do you have masala dosa?",
    "What is good for a vegan?",
    "ghee roast dosa spicy ah?",
    "What time do you close today?",
]


def _rate_limited(response: Any) -> bool:
    """Count 429s as their own outcome — the limiter working is not a failure
    of the SERVICE, but we want it visible in the report."""
    if response.status_code == 429:
        RATE_LIMITED_COUNT["n"] += 1
        response.success()
        return True
    return False


class AnonymousBrowser(HttpUser):
    """Menu browsing without login — the bulk of real traffic."""

    weight = 3
    wait_time = between(1, 5)

    _item_ids: list[int] = []

    @task(5)
    def browse_menu(self) -> None:
        with self.client.get("/api/v1/menu", catch_response=True) as resp:
            if _rate_limited(resp):
                return
            if resp.status_code == 200:
                items = resp.json()
                if isinstance(items, list) and items:
                    AnonymousBrowser._item_ids = [i["id"] for i in items if "id" in i][:60]

    @task(2)
    def browse_categories(self) -> None:
        with self.client.get("/api/v1/menu/categories", catch_response=True) as resp:
            _rate_limited(resp)

    @task(2)
    def item_detail(self) -> None:
        if not AnonymousBrowser._item_ids:
            return
        item_id = random.choice(AnonymousBrowser._item_ids)
        with self.client.get(
            f"/api/v1/menu/items/{item_id}", name="/api/v1/menu/items/[id]", catch_response=True
        ) as resp:
            _rate_limited(resp)

    @task(1)
    def tamil_menu(self) -> None:
        with self.client.get(
            "/api/v1/menu?lang=ta", name="/api/v1/menu?lang=ta", catch_response=True
        ) as resp:
            _rate_limited(resp)


class Customer(HttpUser):
    """Logged-in customer: demo-OTP signup on start, then history/menu reads;
    order placement and agent chat are env-gated (writes / LLM spend)."""

    weight = 2
    wait_time = between(2, 6)

    def on_start(self) -> None:
        self.token = None
        self.item_ids: list[int] = []
        phone = "+919" + "".join(random.choices("0123456789", k=9))
        with self.client.post(
            "/api/v1/auth/otp/request", json={"phone": phone}, catch_response=True
        ) as resp:
            if _rate_limited(resp) or resp.status_code != 200:
                return  # rate-limited or non-demo channel — stay anonymous
            otp = resp.json().get("demo_otp")
        if not otp:
            return
        with self.client.post(
            "/api/v1/auth/otp/verify", json={"phone": phone, "otp": otp}, catch_response=True
        ) as resp:
            if _rate_limited(resp):
                return
            if resp.status_code == 200:
                self.token = resp.json()["access_token"]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    @task(3)
    def my_orders(self) -> None:
        if not self.token:
            return
        with self.client.get(
            "/api/v1/orders", headers=self._headers(), catch_response=True
        ) as resp:
            _rate_limited(resp)

    @task(3)
    def browse_menu(self) -> None:
        with self.client.get("/api/v1/menu", headers=self._headers(), catch_response=True) as resp:
            if _rate_limited(resp):
                return
            if resp.status_code == 200:
                items = resp.json()
                if isinstance(items, list):
                    # public /menu returns only orderable items (86'd excluded)
                    self.item_ids = [i["id"] for i in items if "id" in i][:40]

    @task(1)
    def place_order(self) -> None:
        if not (PLACE_ORDERS and self.token and self.item_ids):
            return
        items = [
            {"item_id": random.choice(self.item_ids), "qty": random.randint(1, 2)}
            for _ in range(random.randint(1, 3))
        ]
        with self.client.post(
            "/api/v1/orders", json={"items": items}, headers=self._headers(), catch_response=True
        ) as resp:
            if _rate_limited(resp):
                return
            # 503 = kitchen paused / outside business hours — a correct answer,
            # not a service failure (schedule enforcement is a feature).
            if resp.status_code in (201, 503):
                resp.success()

    @task(1)
    def chat(self) -> None:
        if not (CHAT and self.token):
            return
        with self.client.post(
            "/api/v1/chat",
            json={"message": random.choice(CHAT_PROMPTS), "history": []},
            headers=self._headers(),
            catch_response=True,
        ) as resp:
            _rate_limited(resp)
