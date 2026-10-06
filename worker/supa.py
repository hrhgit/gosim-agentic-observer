"""Minimal Supabase client for the worker (PostgREST + Storage over HTTPS, service role). Standard library only."""
from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


class SupabaseError(RuntimeError):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"HTTP {status} from {url}: {body[:500]}")
        self.status = status
        self.body = body


class Supa:
    def __init__(self, url: str, key: str, timeout: float = 60.0):
        if not url or not key:
            raise SupabaseError(0, "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required", url)
        self.url = url.rstrip("/")
        self.key = key
        self.timeout = timeout

    # ---------------------------------------------------------------- http
    def _request(self, method: str, path: str, *, params: Optional[dict] = None, json_body: Any = None, data: Optional[bytes] = None,
                 headers: Optional[dict] = None, raw: bool = False) -> Any:
        url = self.url + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        hdrs = {"apikey": self.key, "Authorization": f"Bearer {self.key}"}
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        elif data is not None:
            body = data
        hdrs.update(headers or {})
        # GET / DELETE / PATCH are safe to retry on transport errors; POST inserts are retried only for uploads (x-upsert)
        retryable = method in ("GET", "DELETE", "PATCH") or (method == "POST" and hdrs.get("x-upsert") == "true")
        attempts = 4 if retryable else 1
        last: Exception | None = None
        for attempt in range(attempts):
            req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = resp.read()
                    if raw:
                        return payload
                    if not payload:
                        return None
                    ctype = resp.headers.get("Content-Type", "")
                    return json.loads(payload.decode("utf-8")) if "json" in ctype else payload.decode("utf-8")
            except urllib.error.HTTPError as exc:
                raise SupabaseError(exc.code, exc.read().decode("utf-8", "replace"), url) from exc
            except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError, OSError) as exc:
                last = exc
                if attempt + 1 < attempts:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise SupabaseError(0, f"transport error after {attempts} attempts: {exc}", url) from exc
        raise SupabaseError(0, f"transport error: {last}", url)

    # ---------------------------------------------------------------- postgrest
    def rpc(self, name: str, args: Optional[dict] = None) -> Any:
        return self._request("POST", f"/rest/v1/rpc/{name}", json_body=args or {})

    def select(self, table: str, *, columns: str = "*", filters: Optional[dict] = None, order: Optional[str] = None, limit: Optional[int] = None) -> list:
        params = {"select": columns}
        params.update(filters or {})
        if order:
            params["order"] = order
        if limit:
            params["limit"] = limit
        return self._request("GET", f"/rest/v1/{table}", params=params) or []

    def select_one(self, table: str, **kwargs) -> Optional[dict]:
        rows = self.select(table, limit=1, **kwargs)
        return rows[0] if rows else None

    def insert(self, table: str, rows: Any, *, upsert: bool = False, on_conflict: Optional[str] = None) -> list:
        prefer = "return=representation" + (",resolution=merge-duplicates" if upsert else "")
        params = {"on_conflict": on_conflict} if on_conflict else None
        return self._request("POST", f"/rest/v1/{table}", params=params, json_body=rows, headers={"Prefer": prefer}) or []

    def update(self, table: str, filters: dict, values: dict) -> list:
        return self._request("PATCH", f"/rest/v1/{table}", params=filters, json_body=values, headers={"Prefer": "return=representation"}) or []

    def delete(self, table: str, filters: dict) -> None:
        self._request("DELETE", f"/rest/v1/{table}", params=filters)

    # ---------------------------------------------------------------- storage
    def upload(self, bucket: str, path: str, data: bytes, content_type: str = "application/octet-stream", upsert: bool = True) -> None:
        self._request("POST", f"/storage/v1/object/{bucket}/{urllib.parse.quote(path)}", data=data,
                      headers={"Content-Type": content_type, "x-upsert": "true" if upsert else "false"})

    def download(self, bucket: str, path: str) -> bytes:
        return self._request("GET", f"/storage/v1/object/{bucket}/{urllib.parse.quote(path)}", raw=True)

    def list_objects(self, bucket: str, prefix: str = "") -> list:
        return self._request("POST", f"/storage/v1/object/list/{bucket}", json_body={"prefix": prefix, "limit": 1000}) or []

    def ensure_bucket(self, bucket: str, public: bool = False) -> None:
        try:
            self._request("POST", "/storage/v1/bucket", json_body={"id": bucket, "name": bucket, "public": public})
        except SupabaseError as exc:
            if exc.status not in (400, 409):
                raise
