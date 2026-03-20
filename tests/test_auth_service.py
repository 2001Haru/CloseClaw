"""Tests for AuthService state transitions."""

from closeclaw.services.auth_service import AuthService


def test_auth_service_remember_and_consume_approved():
    pending = {}
    service = AuthService(pending_auth_requests=pending, admin_user_id="admin")

    auth_id = service.remember({"auth_request_id": "a1", "tool_name": "write_file"})
    assert auth_id == "a1"
    assert "a1" in pending

    status, payload, error = service.consume("a1", user_id="admin", approved=True)
    assert status == "approved"
    assert payload["tool_name"] == "write_file"
    assert error is None


def test_auth_service_consume_rejected_clears_pending():
    pending = {"a2": {"auth_request_id": "a2", "tool_name": "delete_file"}}
    service = AuthService(pending_auth_requests=pending)

    status, payload, error = service.consume("a2", user_id="any", approved=False)
    assert status == "rejected"
    assert payload is None
    assert error is None
    assert "a2" not in pending
