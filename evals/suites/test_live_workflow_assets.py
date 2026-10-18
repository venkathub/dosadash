import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("select_suites", ROOT / "evals/select_suites.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_dependency_routing():
    assert module.select_suites(["knowledge/policies.md"])["suites"] == ["retrieval", "rag"]
    for path in [
        "apps/api/src/dosadash_api/services/order_service.py",
        "apps/ai/prompts/support_agent_v1.md",
        "packages/shared/src/dosadash_shared/agent.py",
        "evals/config.json",
        "uv.lock",
    ]:
        assert module.select_suites([path])["suites"] == module.ALL
    assert module.select_suites(["design/madras-pop/tokens.css"]) == {
        "relevant": False,
        "suites": [],
    }


def test_required_workflow_does_not_mask_detector_failure():
    workflow = (ROOT / ".github/workflows/live-evals.yml").read_text()
    assert "needs.changes.result != 'success'" in workflow
    assert "name: Live eval gate (order_accuracy >= 0.95)" in workflow
    assert "schedule:" in workflow and '--profile "$EVAL_PROFILE"' in workflow
    assert "EVAL_DATABASE_URL:" in workflow and "AI_SEMCACHE_ENABLED:" in workflow
    assert "| grep" not in workflow
