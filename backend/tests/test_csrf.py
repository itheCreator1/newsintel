from app.auth.csrf import issue_csrf_token, verify_csrf_token


def test_csrf_token_is_bound_to_secret_and_expires() -> None:
    token = issue_csrf_token("secret", now=1_000)

    assert verify_csrf_token(token, "secret", now=1_100, lifetime_seconds=300)
    assert not verify_csrf_token(token, "wrong", now=1_100, lifetime_seconds=300)
    assert not verify_csrf_token(token, "secret", now=1_301, lifetime_seconds=300)
