"""Онбординг и чтение финансовых целей для дашборда."""


def _onboard(client, token):
    return client.post(
        "/api/onboarding",
        params={"token": token},
        json={
            "currency": "USD",
            "risk_profile": "moderate",
            "initial_capital": 100000,
            "monthly_deposit": 15000,
            "target_income": 2000,
            "years_horizon": 15,
        },
    )


def test_goals_404_before_onboarding(client, registered):
    resp = client.get("/api/goals", params={"token": registered["access_token"]})
    assert resp.status_code == 404


def test_goals_returned_after_onboarding(client, registered):
    token = registered["access_token"]
    assert _onboard(client, token).status_code == 200

    resp = client.get("/api/goals", params={"token": token})
    assert resp.status_code == 200
    data = resp.json()
    assert data["currency"] == "USD"
    assert data["initial_capital"] == 100000
    assert data["monthly_deposit"] == 15000
    assert data["target_income"] == 2000
    assert data["years_horizon"] == 15
    assert data["risk_profile"] == "moderate"


def test_goals_requires_valid_token(client):
    resp = client.get("/api/goals", params={"token": "garbage.token"})
    assert resp.status_code == 401
