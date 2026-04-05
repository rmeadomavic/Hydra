"""Unit tests for auth failure rate-limit helpers."""

from hydra_detect.web import server


def test_recent_auth_failures_prunes_expired_entries():
    """Expired entries are removed and only in-window failures are returned."""
    server._auth_failures.clear()
    now = 1000.0
    server._auth_failures["1.2.3.4"] = [
        now - server._AUTH_FAIL_WINDOW - 1,
        now - 1,
        now - 5,
    ]

    failures = server._recent_auth_failures("1.2.3.4", now)

    assert failures == [now - 1, now - 5]
    assert server._auth_failures["1.2.3.4"] == [now - 1, now - 5]


def test_recent_auth_failures_removes_empty_ip_bucket():
    """IP bucket is deleted when all entries are expired."""
    server._auth_failures.clear()
    now = 2000.0
    server._auth_failures["4.3.2.1"] = [now - server._AUTH_FAIL_WINDOW - 1]

    failures = server._recent_auth_failures("4.3.2.1", now)

    assert failures == []
    assert "4.3.2.1" not in server._auth_failures


def test_record_auth_failure_creates_and_appends():
    """Failure recorder creates buckets and appends timestamps."""
    server._auth_failures.clear()
    now = 3000.0

    server._record_auth_failure("5.6.7.8", now)
    server._record_auth_failure("5.6.7.8", now + 1)

    assert server._auth_failures["5.6.7.8"] == [now, now + 1]
