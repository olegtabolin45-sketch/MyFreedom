"""Валидация входных данных при регистрации."""
import pytest


def test_weak_password_rejected(client, unique_email):
    resp = client.post(
        "/api/register",
        json={"username": "Тест", "email": unique_email, "password": "weak"},
    )
    assert resp.status_code == 422


def test_extra_field_forbidden(client, unique_email):
    resp = client.post(
        "/api/register",
        json={
            "username": "Тест",
            "email": unique_email,
            "password": "Passw0rd!",
            "is_admin": True,
        },
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("bad_username", ["<script>", "a" * 51, "x"])
def test_bad_username_rejected(client, unique_email, bad_username):
    resp = client.post(
        "/api/register",
        json={"username": bad_username, "email": unique_email, "password": "Passw0rd!"},
    )
    assert resp.status_code == 422


def test_invalid_email_rejected(client):
    resp = client.post(
        "/api/register",
        json={"username": "Тест", "email": "not-an-email", "password": "Passw0rd!"},
    )
    assert resp.status_code == 422
