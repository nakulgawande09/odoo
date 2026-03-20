"""API key authentication middleware."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

security = HTTPBearer(auto_error=False)

# Set by the app at startup
_valid_keys: set[str] = set()


def configure_auth(api_keys: list[str]) -> None:
    """Configure valid API keys. Called at app startup."""
    global _valid_keys
    _valid_keys = set(api_keys)


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Security(security),
) -> str | None:
    """Verify the API key. If no keys configured, allow all requests."""
    if not _valid_keys:
        return None  # No auth configured, open access

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
        )

    if credentials.credentials not in _valid_keys:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )

    return credentials.credentials
