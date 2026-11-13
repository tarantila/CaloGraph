from pathlib import Path

from app.ai.typescript import generate_typescript


def test_generated_frontend_contracts_match_public_backend_schema() -> None:
    root = Path(__file__).resolve().parents[1]
    relative = Path("frontend/src/generated/ai-contracts.ts")
    candidates = (root / relative, root.parent / relative)
    source = next((path for path in candidates if path.exists()), None)
    assert source is not None, "Generated frontend contract must be included in the supported backend CI image"
    assert source.read_text() == generate_typescript()
