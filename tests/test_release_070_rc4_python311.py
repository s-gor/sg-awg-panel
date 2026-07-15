from pathlib import Path


def test_web_source_avoids_python_312_only_nested_fstring_quotes() -> None:
    source = (Path(__file__).resolve().parents[1] / "awgpanel" / "web.py").read_text(encoding="utf-8")
    assert "f\"{_safe_filename(f'" not in source
    assert "filename_base = _safe_filename" in source
