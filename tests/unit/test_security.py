from adaptiveroute.api.security import generate_api_key, hash_api_key, parse_prefix


def test_generated_key_round_trips_through_parser() -> None:
    key, prefix = generate_api_key()
    assert key.startswith(f"ar_{prefix}_")
    assert parse_prefix(key) == prefix


def test_keys_are_unique_and_high_entropy() -> None:
    keys = {generate_api_key()[0] for _ in range(1000)}
    assert len(keys) == 1000
    assert all(len(k) == 3 + 8 + 1 + 43 for k in keys)


def test_parse_prefix_rejects_malformed_keys() -> None:
    for bad in [
        "",
        "ar_short",
        "xx_deadbeef_" + "a" * 43,
        "ar_DEADBEEF_" + "a" * 43,
        "ar_deadbeef_" + "a" * 42,
    ]:
        assert parse_prefix(bad) is None


def test_hash_depends_on_pepper() -> None:
    key, _ = generate_api_key()
    assert hash_api_key(key, "p1") != hash_api_key(key, "p2")
    assert hash_api_key(key, "p1") == hash_api_key(key, "p1")
    assert key not in hash_api_key(key, "p1")
