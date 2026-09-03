from pathlib import Path

from utils.logger import redact_secrets, setup_logger


def test_redact_secrets_removes_common_tokens():
    text = "hf_abc123 sk-ant-api03-xyz Authorization: Bearer secret-value api_key=topsecret"
    redacted = redact_secrets(text)
    assert "hf_abc123" not in redacted
    assert "sk-ant-api03-xyz" not in redacted
    assert "secret-value" not in redacted
    assert "topsecret" not in redacted


def test_log_file_rotates(tmp_path):
    import utils.logger as mod

    old_size, old_count = mod._LOG_MAX_BYTES, mod._LOG_BACKUP_COUNT
    try:
        mod._LOG_MAX_BYTES = 256
        mod._LOG_BACKUP_COUNT = 2
        log = setup_logger(Path(tmp_path), level="INFO")
        for i in range(100):
            log.info("message %s %s", i, "x" * 80)
        files = list(Path(tmp_path).glob("app.log*"))
        assert Path(tmp_path, "app.log").exists()
        assert len(files) >= 2
        assert len(files) <= 3
    finally:
        mod._LOG_MAX_BYTES, mod._LOG_BACKUP_COUNT = old_size, old_count
        setup_logger(None)
