"""Typed osu! API v2 client with OAuth client-credentials authentication."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from types import TracebackType
from typing import Final, Self, final

import anyio
import httpx2
from httpx2._types import QueryParamTypes

from .errors import ApiError
from .models import BeatmapAttributesResponse, Beatmapset, Difficulty, User, UserTag

LOGGER = logging.getLogger(__name__)
MAX_ATTEMPTS: Final = 4
HTTP_BAD_REQUEST: Final = 400
HTTP_UNAUTHORIZED: Final = 401
HTTP_TOO_MANY_REQUESTS: Final = 429
HTTP_SERVER_ERROR: Final = 500
JsonBody = Mapping[str, str | list[str]]


@final
class OsuApi:
    """Small public-scope osu! API client used by the crawler."""

    def __init__(self, client_id: str, client_secret: str, *, request_delay: float = 1.0) -> None:
        self._client_id: str = client_id
        self._client_secret: str = client_secret
        self._request_delay: float = request_delay
        self._client: httpx2.AsyncClient = httpx2.AsyncClient(
            base_url="https://osu.ppy.sh",
            timeout=httpx2.Timeout(connect=10.0, read=45.0, write=15.0, pool=10.0),
            follow_redirects=True,
            http2=True,
            limits=httpx2.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30.0,
            ),
        )
        self._token: str | None = None
        self._token_lock = anyio.Lock()
        self._rate_lock = anyio.Lock()
        self._last_request_at: float | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._client.aclose()

    async def _access_token(self) -> str:
        async with self._token_lock:
            if self._token is not None:
                return self._token

            await self._wait_for_slot()
            response = await self._client.post(
                "https://osu.ppy.sh/oauth/token",
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "client_credentials",
                    "scope": "public",
                },
            )
            if response.status_code >= HTTP_BAD_REQUEST:
                raise ApiError("POST", "/oauth/token", response.status_code, response.text[:500])
            payload = response.json()
            token = payload.get("access_token")
            if not isinstance(token, str) or not token:
                raise ApiError(
                    "POST", "/oauth/token", response.status_code, "response has no access_token"
                )
            self._token = token
            return token

    async def _wait_for_slot(self) -> None:
        """Enforce the documented global request interval across concurrent tasks."""
        async with self._rate_lock:
            now = time.monotonic()
            if self._last_request_at is not None:
                wait_for = self._request_delay - (now - self._last_request_at)
                if wait_for > 0:
                    await anyio.sleep(wait_for)
            self._last_request_at = time.monotonic()

    async def _request(
        self,
        method: str,
        path: str,
        params: QueryParamTypes | None = None,
        json_body: JsonBody | None = None,
    ) -> httpx2.Response:
        last_status: int | None = None
        for attempt in range(MAX_ATTEMPTS):
            token = await self._access_token()
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            try:
                await self._wait_for_slot()
                response = await self._client.request(
                    method,
                    path,
                    headers=headers,
                    params=params,
                    json=json_body,
                )
            except httpx2.RequestError as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    raise ApiError(method, path, None, str(exc)) from exc
                await anyio.sleep(2**attempt)
                continue

            last_status = response.status_code
            if response.status_code == HTTP_UNAUTHORIZED and attempt == 0:
                self._token = None
                continue
            if (
                response.status_code == HTTP_TOO_MANY_REQUESTS
                or response.status_code >= HTTP_SERVER_ERROR
            ):
                if attempt == MAX_ATTEMPTS - 1:
                    break
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                await anyio.sleep(delay)
                continue
            if response.status_code >= HTTP_BAD_REQUEST:
                raise ApiError(method, path, response.status_code, response.text[:500])
            return response

        raise ApiError(method, path, last_status, "request failed after retries")

    async def user(self, username_or_id: str | int) -> User:
        """Resolve a username or numeric user ID."""
        response = await self._request("GET", f"/api/v2/users/{username_or_id}")
        return User.model_validate(response.json())

    async def user_beatmapsets(
        self, user_id: int, kind: str, *, limit: int = 100
    ) -> list[Beatmapset]:
        """Fetch all mapsets for a user and API category."""
        results: list[Beatmapset] = []
        offset = 0
        while True:
            response = await self._request(
                "GET",
                f"/api/v2/users/{user_id}/beatmapsets/{kind}",
                params={"limit": limit, "offset": offset},
            )
            page = [Beatmapset.model_validate(item) for item in response.json()]
            results.extend(page)
            if not page:
                return results
            offset += len(page)

    async def beatmapset(self, mapset_id: int) -> Beatmapset:
        """Fetch a canonical mapset when a listing omitted its difficulties."""
        response = await self._request("GET", f"/api/v2/beatmapsets/{mapset_id}")
        return Beatmapset.model_validate(response.json())

    async def beatmaps(self, beatmap_ids: list[int]) -> list[Difficulty]:
        """Fetch beatmaps in API-sized batches, including owners."""
        results: list[Difficulty] = []
        for start in range(0, len(beatmap_ids), 50):
            batch = beatmap_ids[start : start + 50]
            response = await self._request(
                "GET",
                "/api/v2/beatmaps",
                params=tuple(("ids[]", str(beatmap_id)) for beatmap_id in batch),
            )
            payload = response.json()
            results.extend(Difficulty.model_validate(item) for item in payload.get("beatmaps", []))
        return results

    async def beatmap_attributes(
        self, beatmap_id: int, *, ruleset: str = "osu", mods: list[str] | None = None
    ) -> BeatmapAttributesResponse:
        """Fetch difficulty attributes without fetching a beatmap file."""
        response = await self._request(
            "POST",
            f"/api/v2/beatmaps/{beatmap_id}/attributes",
            json_body={"ruleset": ruleset, "mods": mods or []},
        )
        return BeatmapAttributesResponse.model_validate(response.json())

    async def beatmap_file(self, beatmap_id: int) -> bytes:
        response = await self._request("GET", f"/osu/{beatmap_id}")
        content = response.content
        if b"osu file format" not in content[:128].lower():
            raise ApiError("GET", f"/osu/{beatmap_id}", response.status_code, "invalid osu file")
        return content

    async def tags(self) -> list[UserTag]:
        """Fetch the public user-tag catalogue once per crawl process."""
        response = await self._request("GET", "/api/v2/tags")
        payload = response.json()
        items = payload.get("tags", [])
        return [UserTag.model_validate(item) for item in items]
