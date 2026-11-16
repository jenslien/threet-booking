"""Async client for 3T's (3t.no) GraphQL booking API.

Designed to be reused by a Home Assistant integration: pass in HA's shared
aiohttp session via ``async_get_clientsession(hass)``.

Credentials/tokens are only ever held in memory; nothing is logged or persisted.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

API_URL = "https://europe-west3-tttno-396707.cloudfunctions.net/api/graphql"

_ACTIVITY_FIELDS = """
  id name start end bookableEarliest bookableLatest
  center { id slug page { title } }
  instructors { page { title } }
  slot { total available hasWaitingList inWaitingList }
  cancel { isEnabled code }
  groupActivity { id }
"""

Q_CENTERS = """
query Centers {
  Centers(request: { bookingFilterType: GROUPACTIVITY }) { id slug page { title } }
}"""

Q_ACTIVITIES = f"""
query bookableActivities($filter: [FilterInput!]!, $take: Int, $skip: Int) {{
  BookableGroupActivities(request: {{ filter: $filter, take: $take, skip: $skip }}) {{
    countAfterFilters
    all {{ {_ACTIVITY_FIELDS} }}
  }}
}}"""

Q_ACTIVITY = f"""
query BookableActivity($centerId: ID!, $id: ID!) {{
  BookableGroupActivity(request: {{ centerId: $centerId, id: $id }}) {{ {_ACTIVITY_FIELDS} }}
}}"""

Q_MY_BOOKINGS = f"""
query MyBookings($fromDate: Dayjs) {{
  CustomerGroupActivityBookings(fromDate: $fromDate) {{
    __typename
    ... on GroupActivityBooking {{
      id activityId cancel {{ isEnabled code }}
      bookableGroupActivity {{ {_ACTIVITY_FIELDS} }}
    }}
    ... on WaitingListBooking {{
      id activityId waitingListPosition cancel {{ isEnabled code }}
      bookableGroupActivity {{ {_ACTIVITY_FIELDS} }}
    }}
    ... on NoShowBooking {{
      id activityId status noShowType
      bookableGroupActivity {{ {_ACTIVITY_FIELDS} }}
    }}
  }}
}}"""

M_CREATE_BOOKING = """
mutation CreateBooking($id: ID!) {
  CreateBooking(request: { groupActivityId: $id, allowWaitingList: true }) {
    __typename
    ... on GroupActivityBooking { id activityId }
    ... on WaitingListBooking { id activityId waitingListPosition }
  }
}"""

M_CANCEL_BOOKING = """
mutation CancelBooking($bookingId: ID!, $activityStart: Dayjs!) {
  CancelBooking(request: { bookingId: $bookingId, activityStart: $activityStart }) {
    id name start cancel { isEnabled code }
  }
}"""

Q_ACCESS_TOKEN = """
query getAccessToken($password: String!, $username: String!) {
  AccessToken(password: $password, username: $username)
}"""


class ThreeTError(Exception):
    """Generic API error."""


class ThreeTAuthError(ThreeTError):
    """Missing, invalid or expired credentials."""


class ThreeTGraphQLError(ThreeTError):
    """The API answered with GraphQL ``errors``."""


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _jwt_claims(token: str) -> dict[str, Any]:
    """Decode JWT claims without verifying the signature."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return {}


def _jwt_exp(token: str) -> float | None:
    exp = _jwt_claims(token).get("exp")
    return float(exp) if exp is not None else None


@dataclass(frozen=True)
class Center:
    id: str
    slug: str
    name: str


@dataclass(frozen=True)
class GroupActivity:
    id: str
    name: str
    start: datetime
    end: datetime | None
    bookable_earliest: datetime | None
    bookable_latest: datetime | None
    center_id: str
    center_name: str
    instructors: list[str]
    slots_total: int | None
    slots_available: int | None
    has_waiting_list: bool
    in_waiting_list: int | None
    can_cancel: bool
    cancel_code: str
    activity_type_id: str | None
    raw_start: str

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> GroupActivity:
        slot = d.get("slot") or {}
        center = d.get("center") or {}
        cancel = d.get("cancel") or {}
        return cls(
            id=d["id"],
            name=(d.get("name") or "").strip(),
            start=_parse_dt(d["start"]),
            end=_parse_dt(d.get("end")),
            bookable_earliest=_parse_dt(d.get("bookableEarliest")),
            bookable_latest=_parse_dt(d.get("bookableLatest")),
            center_id=center.get("id", ""),
            center_name=(center.get("page") or {}).get("title", ""),
            instructors=[
                (i.get("page") or {}).get("title", "") for i in d.get("instructors") or []
            ],
            slots_total=slot.get("total"),
            slots_available=slot.get("available"),
            has_waiting_list=bool(slot.get("hasWaitingList")),
            in_waiting_list=slot.get("inWaitingList"),
            can_cancel=bool(cancel.get("isEnabled")),
            cancel_code=cancel.get("code") or "",
            activity_type_id=(d.get("groupActivity") or {}).get("id"),
            raw_start=d["start"],
        )


@dataclass(frozen=True)
class Booking:
    id: str
    activity_id: str
    kind: str  # "GroupActivityBooking" | "WaitingListBooking" | "NoShowBooking"
    waiting_list_position: int | None
    can_cancel: bool
    cancel_code: str
    activity: GroupActivity | None

    @property
    def is_waiting_list(self) -> bool:
        return self.kind == "WaitingListBooking"

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> Booking:
        cancel = d.get("cancel") or {}
        bga = d.get("bookableGroupActivity")
        return cls(
            id=d["id"],
            activity_id=d.get("activityId", ""),
            kind=d.get("__typename", ""),
            waiting_list_position=d.get("waitingListPosition"),
            can_cancel=bool(cancel.get("isEnabled")),
            cancel_code=cancel.get("code") or "",
            activity=GroupActivity.from_api(bga) if bga else None,
        )


class ThreeTClient:
    """Client for the 3T GraphQL API.

    Authenticate either with an existing ``access_token`` or with
    ``username``/``password`` (uses the ``AccessToken`` query).
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        access_token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout: float = 20,
    ) -> None:
        self._session = session
        self._token = access_token
        self._username = username
        self._password = password
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    # -- transport ---------------------------------------------------------

    async def _post(
        self, query: str, variables: dict[str, Any] | None, *, auth: bool
    ) -> dict[str, Any]:
        headers = {"content-type": "application/json"}
        if auth:
            await self._ensure_token()
            headers["authorization"] = f"Bearer {self._token}"
        async with self._session.post(
            API_URL,
            json={"query": query, "variables": variables or {}},
            headers=headers,
            timeout=self._timeout,
        ) as resp:
            if resp.status in (401, 403):
                raise ThreeTAuthError(f"HTTP {resp.status}")
            if resp.status >= 400:
                raise ThreeTError(f"HTTP {resp.status}")
            body = await resp.json(content_type=None)
        if errors := body.get("errors"):
            msg = "; ".join(e.get("message", "?") for e in errors)
            if any(
                (e.get("extensions") or {}).get("code") in ("UNAUTHENTICATED", "FORBIDDEN")
                for e in errors
            ):
                raise ThreeTAuthError(msg)
            raise ThreeTGraphQLError(msg)
        return body["data"]

    async def _request(
        self, query: str, variables: dict[str, Any] | None = None, *, auth: bool = True
    ) -> dict[str, Any]:
        try:
            return await self._post(query, variables, auth=auth)
        except ThreeTAuthError:
            if not (auth and self._username and self._password):
                raise
            _LOGGER.debug("Auth failed, logging in again")
            self._token = None
            return await self._post(query, variables, auth=auth)

    # -- auth --------------------------------------------------------------

    @property
    def user_id(self) -> str | None:
        """3T user id (JWT ``sub``), available after login."""
        sub = _jwt_claims(self._token).get("sub") if self._token else None
        return str(sub) if sub is not None else None

    @property
    def token_expires_at(self) -> float | None:
        return _jwt_exp(self._token) if self._token else None

    async def _ensure_token(self) -> None:
        exp = self.token_expires_at
        if self._token and (exp is None or exp - time.time() > 300):
            return
        if not (self._username and self._password):
            raise ThreeTAuthError("Access token missing or expired")
        await self.login()

    async def login(self) -> None:
        """Exchange username/password for an access token."""
        try:
            data = await self._post(
                Q_ACCESS_TOKEN,
                {"username": self._username, "password": self._password},
                auth=False,
            )
        except ThreeTGraphQLError as err:
            raise ThreeTAuthError(f"Login failed: {err}") from err
        token = data.get("AccessToken")
        if not token:
            raise ThreeTAuthError("Login failed")
        self._token = token

    # -- read operations ---------------------------------------------------

    async def get_centers(self) -> list[Center]:
        data = await self._request(Q_CENTERS, auth=False)
        return [
            Center(id=c["id"], slug=c["slug"], name=(c.get("page") or {}).get("title", ""))
            for c in data["Centers"]
        ]

    async def get_group_activities(
        self,
        center_ids: list[str] | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        *,
        take: int = 200,
        skip: int = 0,
        authenticated: bool = True,
    ) -> list[GroupActivity]:
        filters = [{"type": "CENTER", "value": str(c)} for c in center_ids or []]
        if from_date:
            filters.append({"type": "FROM_DATE", "value": from_date.isoformat()})
        if to_date:
            filters.append({"type": "TO_DATE", "value": to_date.isoformat()})
        data = await self._request(
            Q_ACTIVITIES,
            {"filter": filters, "take": take, "skip": skip},
            auth=authenticated,
        )
        return [GroupActivity.from_api(a) for a in data["BookableGroupActivities"]["all"]]

    async def get_group_activity(self, center_id: str, activity_id: str) -> GroupActivity:
        data = await self._request(Q_ACTIVITY, {"centerId": center_id, "id": activity_id})
        return GroupActivity.from_api(data["BookableGroupActivity"])

    async def get_my_bookings(self, from_date: datetime | None = None) -> list[Booking]:
        variables = {"fromDate": from_date.isoformat()} if from_date else {}
        data = await self._request(Q_MY_BOOKINGS, variables)
        return [Booking.from_api(b) for b in data["CustomerGroupActivityBookings"]]

    # -- write operations (have side effects: real bookings / no-show fees) -

    async def book(self, activity_id: str) -> dict[str, Any]:
        """Book a class (or join its waiting list if full).

        Returns ``{"__typename", "id", "activityId", ["waitingListPosition"]}``.
        """
        data = await self._request(M_CREATE_BOOKING, {"id": activity_id})
        return data["CreateBooking"]

    async def cancel(self, booking: Booking) -> dict[str, Any]:
        """Cancel a booking. Uses booking.id and the activity's original start string."""
        if booking.activity is None:
            raise ThreeTError("Booking has no activity information")
        data = await self._request(
            M_CANCEL_BOOKING,
            {"bookingId": booking.id, "activityStart": booking.activity.raw_start},
        )
        return data["CancelBooking"]
