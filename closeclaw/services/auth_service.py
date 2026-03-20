"""Authorization request state management service."""

from __future__ import annotations

from typing import Any, Optional


class AuthService:
    """Manages pending authorization requests and approval transitions."""

    def __init__(self, pending_auth_requests: dict[str, Any], admin_user_id: Optional[str] = None) -> None:
        self.pending_auth_requests = pending_auth_requests
        # Keep constructor compatibility; approval authority is validated at channel layer.
        _ = admin_user_id

    def remember(self, metadata: dict[str, Any] | None) -> Optional[str]:
        if not metadata:
            return None
        auth_request_id = metadata.get("auth_request_id")
        if not auth_request_id:
            return None
        self.pending_auth_requests[auth_request_id] = metadata
        return auth_request_id

    def consume(self, auth_request_id: str, user_id: str, approved: bool) -> tuple[str, dict[str, Any] | None, str | None]:
        """Consume an auth request and return (status, payload, error)."""
        _ = user_id

        if not approved:
            self.pending_auth_requests.pop(auth_request_id, None)
            return "rejected", None, None

        pending_auth = self.pending_auth_requests.pop(auth_request_id, None)
        if not pending_auth:
            return "error", None, "Auth request not found"

        return "approved", pending_auth, None
