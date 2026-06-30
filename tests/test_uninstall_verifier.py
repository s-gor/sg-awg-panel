from pathlib import Path


def test_verify_uninstall_script_is_present_and_read_only():
    script = Path("verify-uninstall.sh")
    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    assert "Read-only audit" in text
    assert "rm -rf" not in text
    assert "systemctl list-unit-files" in text
    assert "nftables-таблицы" in text
    assert "Policy rules" in text
    assert "Backend TCP 18080" in text
    assert "Amnezia PPA" in text
    assert "Следов SG-AWG-Panel" in text
