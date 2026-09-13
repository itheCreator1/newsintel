from app.auth.sessions import generate_session_token, hash_session_token


def test_session_tokens_are_random_and_only_hashes_are_persistable() -> None:
    first = generate_session_token()
    second = generate_session_token()

    assert first != second
    assert len(first) >= 43
    assert hash_session_token(first) != first
    assert hash_session_token(first) == hash_session_token(first)
