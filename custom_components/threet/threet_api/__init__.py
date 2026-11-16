"""3T (3t.no) booking API client (no Home Assistant dependencies)."""

from .client import (
    API_URL,
    Booking,
    Center,
    GroupActivity,
    ThreeTAuthError,
    ThreeTClient,
    ThreeTError,
    ThreeTGraphQLError,
)

__all__ = [
    "API_URL",
    "Booking",
    "Center",
    "GroupActivity",
    "ThreeTAuthError",
    "ThreeTClient",
    "ThreeTError",
    "ThreeTGraphQLError",
]
