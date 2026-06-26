import awgpanel.db as db
import awgpanel.web as web
from werkzeug.security import generate_password_hash


def test_health_and_login(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setenv("AWGPANEL_PASSWORD_HASH", generate_password_hash("correct-password"))
    monkeypatch.setenv("AWGPANEL_SECRET_KEY", "test-secret")
    app = web.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    response = client.get("/health")
    assert response.status_code == 200
    assert response.data == b"ok\n"

    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]

    with client.session_transaction() as session:
        session["csrf_token"] = "token"
    response = client.post(
        "/login",
        data={"password": "correct-password", "csrf_token": "token"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
