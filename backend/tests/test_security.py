from app.auth.security import hash_password, verify_password


def test_password_hash_never_contains_plaintext_and_verifies() -> None:
    encoded = hash_password("correct horse battery staple")

    assert "correct horse battery staple" not in encoded
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("incorrect", encoded)
