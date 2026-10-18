"""Conservative CI dependency routing, usable before installing the Python workspace."""

import json
import sys
from pathlib import Path

ALL = ["retrieval", "rag", "support", "inventory", "copilot"]


def select_suites(paths: list[str]) -> dict:
    relevant = False
    selected = set()
    for path in paths:
        if path.startswith(("knowledge/", "apps/ai/src/dosadash_ai/rag/")):
            relevant = True
            selected.update(["retrieval", "rag"])
        elif path.startswith(
            (
                "apps/ai/",
                "apps/api/",
                "packages/shared/",
                "packages/ml/",
                "evals/",
                "scripts/run-live-evals.sh",
                ".github/workflows/",
                "pyproject.toml",
                "uv.lock",
            )
        ):
            relevant = True
            selected.update(ALL)
    return {"relevant": relevant, "suites": [name for name in ALL if name in selected]}


if __name__ == "__main__":
    result = select_suites(Path(sys.argv[1]).read_text().splitlines())
    print(f"relevant={str(result['relevant']).lower()}")
    print(f"suites={','.join(result['suites'])}")
    print(f"routing_json={json.dumps(result)}")
