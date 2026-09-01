from pathlib import Path


def test_no_boolean_can_claim_venue_margin_is_verified() -> None:
    source = Path("src").read_text(encoding="utf-8") if Path("src").is_file() else ""
    python_source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(Path("src").rglob("*.py"))
    )
    assert "venue_margin_verified" not in source + python_source
    assert "margin_verification_path" in python_source
