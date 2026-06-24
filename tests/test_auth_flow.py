"""Жизненный цикл аутентификации: регистрация, вход, refresh, logout."""


def test_register_returns_tokens(registered):
    assert registered["access_token"]
    assert registered["refresh_token"]


def test_duplicate_registration_rejected(client, registered):
    resp = client.post(
        "/api/register",
        json={
            "username": "Дубль",
            "email": registered["email"],
            "password": registered["password"],
        },
    )
    assert resp.status_code == 400


def test_login_success(client, registered):
    resp = client.post(
        "/api/login",
        json={"email": registered["email"], "password": registered["password"]},
    )
    assert resp.status_code == 200
    assert resp.json()["access_token"]


def test_login_wrong_password(client, registered):
    resp = client.post(
        "/api/login",
        json={"email": registered["email"], "password": "WrongPass1!"},
    )
    assert resp.status_code == 401


def test_access_token_works_on_protected_endpoint(client, registered):
    resp = client.get("/api/user/status", params={"token": registered["access_token"]})
    assert resp.status_code == 200
    assert resp.json()["is_onboarded"] is False


def test_refresh_rotation(client, registered):
    old_refresh = registered["refresh_token"]
    resp = client.post("/api/refresh", json={"refresh_token": old_refresh})
    assert resp.status_code == 200
    assert resp.json()["access_token"]
    # Старый refresh-токен после ротации больше не действителен
    resp2 = client.post("/api/refresh", json={"refresh_token": old_refresh})
    assert resp2.status_code == 401


def test_logout_blacklists_access(client, registered):
    access = registered["access_token"]
    # До logout access работает
    assert client.get("/api/user/status", params={"token": access}).status_code == 200
    resp = client.post(
        "/api/logout",
        json={"refresh_token": registered["refresh_token"], "access_token": access},
    )
    assert resp.status_code == 200
    # После logout access отозван
    assert client.get("/api/user/status", params={"token": access}).status_code == 401


def test_invalid_token_rejected(client):
    resp = client.get("/api/user/status", params={"token": "garbage.token.value"})
    assert resp.status_code == 401
