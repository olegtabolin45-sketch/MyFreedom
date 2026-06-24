"""Заголовки безопасности проставляются на ответы."""


def test_security_headers_present(client):
    resp = client.get("/")
    assert resp.status_code == 200
    h = resp.headers
    assert "content-security-policy" in h
    assert h["x-frame-options"] == "DENY"
    assert h["x-content-type-options"] == "nosniff"
    assert "strict-transport-security" in h
    assert "referrer-policy" in h
    assert "permissions-policy" in h
