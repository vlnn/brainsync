from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlencode

import requests

ENV_VARS = ("BRAIN_API_URL", "BRAIN_API_TOKEN", "BRAIN_ID")


def normalize_base_url(raw: str) -> str:
    trimmed = raw.rstrip("/")
    return trimmed if trimmed.endswith("/api") else f"{trimmed}/api"


@dataclass(frozen=True)
class ApiConfig:
    base_url: str
    token: str
    brain_id: str

    @classmethod
    def from_file(cls, path: Path) -> ApiConfig:
        fields = json.loads(path.read_text())
        return cls(base_url=fields["base_url"], token=fields["token"], brain_id=fields["brain_id"])

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ApiConfig | None:
        if not all(name in env for name in ENV_VARS):
            return None
        return cls(base_url=env["BRAIN_API_URL"], token=env["BRAIN_API_TOKEN"], brain_id=env["BRAIN_ID"])


@dataclass(frozen=True)
class ApiClient:
    config: ApiConfig
    session: requests.Session

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.token}"}

    def _root(self) -> str:
        return normalize_base_url(self.config.base_url)

    def _check(self, response: requests.Response, url: str) -> None:
        if response.status_code == 401:
            raise SystemExit(
                f"Local API rejected the API key (401) for {url} — re-copy it from Settings > User > Local API"
            )
        if response.status_code == 403:
            raise requests.HTTPError(
                f"Local API says 403, this request is not permitted: {url}", response=response
            )
        try:
            response.raise_for_status()
        except requests.HTTPError as error:
            raise requests.HTTPError(
                f"{error} — body: {response.text[:200]!r}", response=response
            ) from None

    def _get(self, path: str) -> dict | list:
        url = f"{self._root()}{path}"
        response = self.session.get(url, headers=self._headers(), timeout=10)
        self._check(response, url)
        try:
            return response.json()
        except ValueError:
            raise SystemExit(
                f"{url} answered {response.status_code} with a non-JSON body: {response.text[:120]!r}\n"
                "the route probably differs on the local server — check the endpoint docs in Settings > User > Local API"
            )

    def _post(self, path: str, payload: dict) -> None:
        url = f"{self._root()}{path}"
        response = self.session.post(url, headers=self._headers(), json=payload, timeout=10)
        self._check(response, url)

    def brain(self) -> dict:
        return self._get(f"/brains/{self.config.brain_id}")

    def tags(self) -> list[dict]:
        return self._get(f"/thoughts/{self.config.brain_id}/tags")

    def thought(self, thought_id: str) -> dict:
        return self._get(f"/thoughts/{self.config.brain_id}/{thought_id}")

    def thought_graph(self, thought_id: str) -> dict:
        return self._get(f"/thoughts/{self.config.brain_id}/{thought_id}/graph")

    def types(self) -> list[dict]:
        return self._get(f"/thoughts/{self.config.brain_id}/types")

    def modifications(
        self, max_logs: int | None = None, start_time: str | None = None, end_time: str | None = None
    ) -> list[dict]:
        params = {"maxLogs": max_logs, "startTime": start_time, "endTime": end_time}
        query = urlencode({key: value for key, value in params.items() if value is not None})
        return self._get(f"/brains/{self.config.brain_id}/modifications{'?' if query else ''}{query}")

    def note_markdown(self, thought_id: str) -> str:
        return self._get(f"/notes/{self.config.brain_id}/{thought_id}")["markdown"]

    def attachment_content(self, attachment_id: str) -> bytes:
        url = f"{self._root()}/attachments/{self.config.brain_id}/{attachment_id}/file-content"
        response = self.session.get(url, headers=self._headers(), timeout=30)
        self._check(response, url)
        return response.content

    def fetch_bytes(self, url: str) -> bytes:
        response = self.session.get(url, timeout=30)
        if not response.ok:
            response = self.session.get(url, headers=self._headers(), timeout=30)
        if not response.ok:
            try:
                response.raise_for_status()
            except requests.HTTPError as error:
                raise requests.HTTPError(
                    f"{error} — body: {response.text[:200]!r}", response=response
                ) from None
        return response.content

    def update_note_markdown(self, thought_id: str, markdown: str) -> None:
        self._post(f"/notes/{self.config.brain_id}/{thought_id}/update", {"markdown": markdown})


CONFIG_FILENAME = ".brainsync.json"


def load_config(directory: Path, env: Mapping[str, str]) -> ApiConfig:
    if config := ApiConfig.from_env(env):
        return config
    path = directory / CONFIG_FILENAME
    if path.is_file():
        return ApiConfig.from_file(path)
    raise SystemExit(
        f"no API config: set {'/'.join(ENV_VARS)} or create {path} "
        '({"base_url": ..., "token": ..., "brain_id": ...})'
    )
