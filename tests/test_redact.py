from redact import redact_text, redact_obj, redact_state


def test_redact_api_key():
    assert "***REDACTED***" in redact_text("api_key=sk-abcdefghijklmnopqrstuvwxyz")
    assert "sk-***REDACTED***" in redact_text("token sk-abcdefghijklmnopqrstuvwxyz0123")


def test_redact_bearer():
    out = redact_text("Authorization: Bearer abcdefghijklmnop")
    assert "REDACTED" in out
    assert "abcdefghijklmnop" not in out


def test_redact_nested():
    obj = {"password": "secret12345", "nested": {"token": "xoxb-1234567890-abcdef"}}
    # Our patterns look for key=value style and known prefixes; ensure deep walk doesn't crash
    out = redact_obj(obj)
    assert isinstance(out, dict)


def test_redact_state_fail_open():
    assert isinstance(redact_state({"a": "b"}), dict)
