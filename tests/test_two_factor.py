"""Двухфакторная аутентификация: модуль TOTP и сценарий входа."""
import pyotp

from app import two_factor


def test_verify_code_roundtrip():
    secret = two_factor.generate_secret()
    code = pyotp.TOTP(secret).now()
    assert two_factor.verify_code(secret, code) is True


def test_verify_code_rejects_wrong():
    secret = two_factor.generate_secret()
    assert two_factor.verify_code(secret, "000000") is False


def test_verify_code_empty():
    assert two_factor.verify_code("", "123456") is False


def test_qr_is_data_uri():
    secret = two_factor.generate_secret()
    qr = two_factor.qr_png_base64(secret, "user@example.com")
    assert qr.startswith("data:image/png;base64,")


def test_full_2fa_login_flow(client, registered):
    token = registered["access_token"]

    setup = client.post("/api/2fa/setup", params={"token": token})
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert setup.json()["qr_code"].startswith("data:image/png")

    enable = client.post(
        "/api/2fa/enable",
        params={"token": token},
        json={"code": pyotp.TOTP(secret).now()},
    )
    assert enable.status_code == 200

    # Логин без кода — требуется второй фактор
    r1 = client.post(
        "/api/login",
        json={"email": registered["email"], "password": registered["password"]},
    )
    assert r1.status_code == 200
    assert r1.json().get("requires_2fa") is True

    # Логин с неверным кодом
    r2 = client.post(
        "/api/login",
        json={
            "email": registered["email"],
            "password": registered["password"],
            "totp_code": "000000",
        },
    )
    assert r2.status_code == 401

    # Логин с верным кодом
    r3 = client.post(
        "/api/login",
        json={
            "email": registered["email"],
            "password": registered["password"],
            "totp_code": pyotp.TOTP(secret).now(),
        },
    )
    assert r3.status_code == 200
    assert r3.json()["access_token"]
