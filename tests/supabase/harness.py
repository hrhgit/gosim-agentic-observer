"""Local Supabase-compatible harness for tests: embedded Postgres + real PostgREST + minimal GoTrue/Storage emulation.

One HTTP port fronts everything, like a Supabase project URL:
  /rest/v1/*      -> PostgREST (real, with the project's migrations and RLS)
  /auth/v1/*      -> signup / password login / user / logout / recover / refresh (HS256 JWTs, users inserted into auth.users)
  /storage/v1/*   -> object upload/download/list on the local filesystem (+ team-folder checks from the JWT)
  /realtime/v1/*  -> 404 (clients must tolerate this)

Only what the platform uses is implemented. Everything table-related goes through the real PostgREST + RLS.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pgserver
import psycopg

ROOT = Path(__file__).resolve().parents[2]
TOOLS = Path(os.environ.get("SAC_TOOLS_DIR", "/private/tmp/claude-501/-Users-mac-Library-Application-Support-CindyGlobal-owners-ae98ca1d7b2b6ae48f15-dialogues-2026-09-09-f286d255-69dd-408c-a206-ec1ca3e39544/55e5a0de-c283-422f-848b-e4005e339509/scratchpad/tools"))
POSTGREST = Path(os.environ.get("SAC_POSTGREST_BIN", TOOLS / "postgrest"))
JWT_SECRET = "super-secret-jwt-token-with-at-least-32-characters-long"


# ---------------------------------------------------------------------------
# JWT (HS256)
# ---------------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwt_encode(claims: dict, secret: str = JWT_SECRET) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(sig)}"


def jwt_decode(token: str, secret: str = JWT_SECRET) -> dict | None:
    try:
        header, payload, sig = token.split(".")
        expected = _b64(hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if claims.get("exp") and claims["exp"] < time.time():
            return None
        return claims
    except Exception:
        return None


def anon_key() -> str:
    return jwt_encode({"role": "anon", "iss": "supabase", "iat": 1700000000, "exp": 4102444800})


def service_key() -> str:
    return jwt_encode({"role": "service_role", "iss": "supabase", "iat": 1700000000, "exp": 4102444800})


def user_token(user_id: str, email: str, ttl: int = 3600) -> str:
    now = int(time.time())
    return jwt_encode({"aud": "authenticated", "role": "authenticated", "sub": user_id, "email": email, "iat": now, "exp": now + ttl, "session_id": str(uuid.uuid4())})


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class Harness:
    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="sac-harness-"))
        self.pg = None
        self.db_uri = ""
        self.postgrest = None
        self.rest_port = 0
        self.port = 0
        self.server = None
        self.storage_root = self.dir / "storage"
        self.refresh_tokens: dict[str, str] = {}
        self.signed_uploads: dict[str, tuple[str, str, float]] = {}
        self.thread = None

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> "Harness":
        self.pg = pgserver.get_server(str(self.dir / "pg"))
        self.db_uri = self.pg.get_uri()
        with psycopg.connect(self.db_uri, autocommit=True) as conn:
            conn.execute((ROOT / "tests" / "supabase" / "auth_stub.sql").read_text())
            for m in sorted((ROOT / "supabase" / "migrations").glob("*.sql")):
                conn.execute(m.read_text())
            conn.execute("create role authenticator noinherit login password 'authenticator'")
            conn.execute("grant anon, authenticated, service_role to authenticator")
        self.rest_port = _free_port()
        self.port = _free_port()
        cfg = self.dir / "postgrest.conf"
        host = re.search(r"host=([^&]+)", self.db_uri).group(1)
        cfg.write_text(
            f'db-uri = "postgresql://authenticator:authenticator@/postgres?host={host}"\n'
            'db-schemas = "public,storage"\n'
            'db-anon-role = "anon"\n'
            f'jwt-secret = "{JWT_SECRET}"\n'
            f'server-port = {self.rest_port}\n'
            'server-host = "127.0.0.1"\n'
            'db-pool = 10\n'
            'log-level = "warn"\n'
        )
        self.postgrest = subprocess.Popen([str(POSTGREST), str(cfg)], stdout=open(self.dir / "postgrest.log", "w"), stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.rest_port}/", timeout=1)
                break
            except Exception:
                time.sleep(0.15)
        else:
            raise RuntimeError("postgrest did not start: " + (self.dir / "postgrest.log").read_text()[-2000:])
        self.storage_root.mkdir(parents=True, exist_ok=True)
        harness = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # quiet
                pass

            def do_OPTIONS(self):
                self._cors(204)
                self.end_headers()

            def do_GET(self):
                harness.dispatch(self, "GET")

            def do_POST(self):
                harness.dispatch(self, "POST")

            def do_PUT(self):
                harness.dispatch(self, "PUT")

            def do_PATCH(self):
                harness.dispatch(self, "PATCH")

            def do_DELETE(self):
                harness.dispatch(self, "DELETE")

            def _cors(self, code):
                self.send_response(code)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "*")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
                self.send_header("Access-Control-Expose-Headers", "*")

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
        if self.postgrest:
            self.postgrest.terminate()
            try:
                self.postgrest.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.postgrest.kill()
        if self.pg:
            self.pg.cleanup()
        shutil.rmtree(self.dir, ignore_errors=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def sql(self, query: str, params=None):
        with psycopg.connect(self.db_uri, autocommit=True) as conn:
            cur = conn.execute(query, params)
            try:
                return cur.fetchall()
            except psycopg.ProgrammingError:
                return None

    # ---------------------------------------------------------------- dispatch
    def dispatch(self, h: BaseHTTPRequestHandler, method: str) -> None:
        path = h.path
        length = int(h.headers.get("Content-Length") or 0)
        body = h.rfile.read(length) if length else b""
        try:
            if path.startswith("/rest/v1/"):
                self._proxy_rest(h, method, path, body)
            elif path.startswith("/auth/v1/"):
                self._auth(h, method, path, body)
            elif path.startswith("/storage/v1/"):
                self._storage(h, method, path, body)
            else:
                self._send(h, 404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            self._send(h, 500, {"error": str(exc)})

    def _send(self, h, code: int, payload, content_type: str = "application/json", extra: dict | None = None) -> None:
        data = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload).encode()
        h.send_response(code)
        h.send_header("Access-Control-Allow-Origin", "*")
        h.send_header("Access-Control-Allow-Headers", "*")
        h.send_header("Access-Control-Expose-Headers", "*")
        h.send_header("Content-Type", content_type)
        h.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            h.send_header(k, v)
        h.end_headers()
        h.wfile.write(data)

    def _claims(self, h) -> dict | None:
        auth = h.headers.get("Authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else ""
        return jwt_decode(token) if token else None

    # ---------------------------------------------------------------- rest proxy
    def _proxy_rest(self, h, method, path, body) -> None:
        target = f"http://127.0.0.1:{self.rest_port}" + path[len("/rest/v1"):]
        headers = {k: v for k, v in h.headers.items() if k.lower() in ("authorization", "content-type", "prefer", "accept", "range", "accept-profile", "content-profile", "x-client-info")}
        req = urllib.request.Request(target, data=body if body else None, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
                extra = {k: v for k, v in resp.headers.items() if k.lower() in ("content-range", "preference-applied")}
                self._send(h, resp.status, data, resp.headers.get("Content-Type", "application/json"), extra)
        except urllib.error.HTTPError as exc:
            self._send(h, exc.code, exc.read(), exc.headers.get("Content-Type", "application/json"))

    # ---------------------------------------------------------------- auth emulation
    def _user_json(self, uid: str, email: str, meta: dict) -> dict:
        return {"id": uid, "aud": "authenticated", "role": "authenticated", "email": email, "email_confirmed_at": "2026-01-01T00:00:00Z",
                "app_metadata": {"provider": "email"}, "user_metadata": meta, "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}

    def _session(self, uid: str, email: str, meta: dict) -> dict:
        access = user_token(uid, email)
        refresh = secrets.token_urlsafe(24)
        self.refresh_tokens[refresh] = uid
        return {"access_token": access, "token_type": "bearer", "expires_in": 3600, "expires_at": int(time.time()) + 3600, "refresh_token": refresh, "user": self._user_json(uid, email, meta)}

    def _auth(self, h, method, path, body) -> None:
        url = urllib.parse.urlparse(path)
        route = url.path[len("/auth/v1"):]
        qs = urllib.parse.parse_qs(url.query)
        data = json.loads(body or b"{}") if body else {}
        if route == "/signup" and method == "POST":
            email = (data.get("email") or "").lower().strip()
            password = data.get("password") or ""
            meta = data.get("data") or {}
            if not email or len(password) < 6:
                return self._send(h, 422, {"code": 422, "msg": "Signup requires a valid password", "error_code": "weak_password"})
            if self.sql("select 1 from auth.users where email = %s", (email,)):
                return self._send(h, 422, {"code": 422, "error_code": "user_already_exists", "msg": "User already registered"})
            uid = str(uuid.uuid4())
            self.sql("insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)", (uid, email, json.dumps({**meta, "password_hash": hashlib.sha256(password.encode()).hexdigest()})))
            return self._send(h, 200, self._session(uid, email, meta))
        if route == "/token" and method == "POST":
            grant = qs.get("grant_type", [""])[0]
            if grant == "password":
                email = (data.get("email") or "").lower().strip()
                rows = self.sql("select id::text, raw_user_meta_data from auth.users where email = %s", (email,))
                if not rows or rows[0][1].get("password_hash") != hashlib.sha256((data.get("password") or "").encode()).hexdigest():
                    return self._send(h, 400, {"code": 400, "error_code": "invalid_credentials", "msg": "Invalid login credentials", "error": "invalid_grant", "error_description": "Invalid login credentials"})
                meta = {k: v for k, v in rows[0][1].items() if k != "password_hash"}
                return self._send(h, 200, self._session(rows[0][0], email, meta))
            if grant == "refresh_token":
                uid = self.refresh_tokens.pop(data.get("refresh_token", ""), None)
                if not uid:
                    return self._send(h, 400, {"error": "invalid_grant", "error_description": "Invalid Refresh Token"})
                rows = self.sql("select email, raw_user_meta_data from auth.users where id = %s", (uid,))
                meta = {k: v for k, v in rows[0][1].items() if k != "password_hash"}
                return self._send(h, 200, self._session(uid, rows[0][0], meta))
            return self._send(h, 400, {"error": "unsupported_grant_type"})
        claims = self._claims(h)
        if route == "/user" and method == "GET":
            if not claims or claims.get("role") != "authenticated":
                return self._send(h, 401, {"code": 401, "msg": "invalid JWT"})
            rows = self.sql("select email, raw_user_meta_data from auth.users where id = %s", (claims["sub"],))
            meta = {k: v for k, v in rows[0][1].items() if k != "password_hash"}
            return self._send(h, 200, self._user_json(claims["sub"], rows[0][0], meta))
        if route == "/user" and method == "PUT":
            if not claims or claims.get("role") != "authenticated":
                return self._send(h, 401, {"code": 401, "msg": "invalid JWT"})
            if data.get("password"):
                self.sql("update auth.users set raw_user_meta_data = raw_user_meta_data || %s where id = %s", (json.dumps({"password_hash": hashlib.sha256(data["password"].encode()).hexdigest()}), claims["sub"]))
            rows = self.sql("select email, raw_user_meta_data from auth.users where id = %s", (claims["sub"],))
            meta = {k: v for k, v in rows[0][1].items() if k != "password_hash"}
            return self._send(h, 200, self._user_json(claims["sub"], rows[0][0], meta))
        if route == "/logout" and method == "POST":
            return self._send(h, 204, b"")
        if route == "/recover" and method == "POST":
            # would send an email; expose the recovery link for tests
            email = (data.get("email") or "").lower().strip()
            rows = self.sql("select id::text from auth.users where email = %s", (email,))
            if rows:
                self.last_recovery = {"email": email, "access_token": user_token(rows[0][0], email)}
            return self._send(h, 200, {})
        return self._send(h, 404, {"error": f"auth route {route} not implemented"})

    # ---------------------------------------------------------------- storage emulation
    def _storage_allowed(self, claims: dict | None, bucket: str, name: str, write: bool) -> bool:
        role = (claims or {}).get("role", "anon")
        if role == "service_role":
            return True
        folder = name.split("/")[0] if "/" in name else ""
        if bucket == "scenarios":
            if write:
                return role == "authenticated" and bool(self.sql("select 1 from public.profiles where id = %s and is_admin", (claims["sub"],)))
            parts = name.split("/")
            rows = self.sql("select weather_public, forecasts_public, events_public from public.scenarios where slug = %s and is_active", (folder,))
            if not rows:
                return role == "authenticated" and bool(self.sql("select 1 from public.profiles where id = %s and is_admin", (claims["sub"],)))
            weather_public, forecasts_public, events_public = rows[0]
            fname = parts[-1]
            if len(parts) > 1 and parts[1] == "config":
                return True
            always = {"night_calendar.csv", "slots.csv", "tiles.csv", "targets.csv", "tile_windows.csv", "observation_requests.csv", "observation_request_tiles.csv",
                      "scenario_manifest.json", "calendar_metadata.json", "catalog_metadata.json", "observation_request_metadata.json"}
            if fname in always:
                return True
            if fname in ("weather.csv", "weather_metadata.json"):
                return bool(weather_public)
            if fname == "weather_forecasts.csv":
                return bool(forecasts_public)
            if fname == "weather_events.csv":
                return bool(events_public)
            return False
        if role != "authenticated":
            return False
        team = self.sql("select team_id::text, is_admin from public.profiles where id = %s", (claims["sub"],))
        team_id, is_admin = (team[0] if team else (None, False))
        if bucket == "submissions":
            return folder == team_id or (not write and is_admin)
        if bucket == "results":
            return (not write) and (folder == team_id or is_admin)
        return False

    def _storage(self, h, method, path, body) -> None:
        url = urllib.parse.urlparse(path)
        route = urllib.parse.unquote(url.path[len("/storage/v1"):])
        claims = self._claims(h)
        if route.startswith('/object/upload/sign/'):
            bucket, name = route[len('/object/upload/sign/'):].split('/', 1)
            if '..' in name.split('/') or name.startswith('/'):
                return self._send(h, 400, {'error':'invalid_path'})
            if method == 'POST':
                if not self._storage_allowed(claims, bucket, name, True):
                    return self._send(h, 403, {'error':'Unauthorized'})
                token = secrets.token_urlsafe(24)
                self.signed_uploads[token] = (bucket, name, time.time()+7200)
                return self._send(h, 200, {'url':f'/object/upload/sign/{bucket}/{name}?token={token}'})
            if method == 'PUT':
                token = urllib.parse.parse_qs(url.query).get('token',[''])[0]
                grant = self.signed_uploads.get(token)
                if not grant or grant[:2] != (bucket,name) or grant[2] <= time.time():
                    return self._send(h, 403, {'error':'Unauthorized'})
                f = self.storage_root / bucket / name
                if f.exists():
                    return self._send(h, 409, {'error':'Duplicate'})
                payload = _multipart_file(body,h.headers['Content-Type']) if h.headers.get('Content-Type','').startswith('multipart/form-data') else body
                if len(payload)>52428800:
                    return self._send(h, 413, {'error':'too_large'})
                f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(payload)
                return self._send(h, 200, {'Key':f'{bucket}/{name}'})
        m = re.match(r"^/object/(authenticated/|public/)?([^/]+)/(.+)$", route)
        if route.startswith("/object/list/") and method == "POST":
            bucket = route[len("/object/list/"):]
            data = json.loads(body or b"{}")
            prefix = data.get("prefix", "")
            base = self.storage_root / bucket / prefix
            items = []
            if base.exists():
                for p in sorted(base.iterdir()):
                    items.append({"name": p.name, "id": str(uuid.uuid4()) if p.is_file() else None, "metadata": {"size": p.stat().st_size} if p.is_file() else None})
            return self._send(h, 200, items)
        if route.startswith("/object/sign/") and method == "POST":
            bucket, name = route[len("/object/sign/"):].split("/", 1)
            if not self._storage_allowed(claims, bucket, name, False):
                return self._send(h, 400, {"statusCode": "403", "error": "Unauthorized", "message": "new row violates row-level security policy"})
            token = secrets.token_urlsafe(16)
            return self._send(h, 200, {"signedURL": f"/object/sign/{bucket}/{name}?token={token}"})
        if route == "/bucket" and method == "POST":
            return self._send(h, 200, {"name": json.loads(body or b"{}").get("name")})
        if m and route.startswith("/object/sign/") and method == "GET":
            bucket, name = route[len("/object/sign/"):].split("/", 1)
            f = self.storage_root / bucket / name
            if not f.exists():
                return self._send(h, 404, {"statusCode": "404", "error": "not_found", "message": "Object not found"})
            return self._send(h, 200, f.read_bytes(), "application/octet-stream")
        if m:
            bucket, name = m.group(2), m.group(3)
            f = self.storage_root / bucket / name
            if method in ("POST", "PUT"):
                if not self._storage_allowed(claims, bucket, name, True):
                    return self._send(h, 400, {"statusCode": "403", "error": "Unauthorized", "message": "new row violates row-level security policy"})
                if f.exists() and h.headers.get("x-upsert", "false").lower() != "true" and method == "POST":
                    return self._send(h, 400, {"statusCode": "409", "error": "Duplicate", "message": "The resource already exists"})
                payload = body
                ctype = h.headers.get("Content-Type", "")
                if ctype.startswith("multipart/form-data"):
                    payload = _multipart_file(body, ctype)
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(payload)
                return self._send(h, 200, {"Key": f"{bucket}/{name}", "Id": str(uuid.uuid4())})
            if method == "GET":
                if not self._storage_allowed(claims, bucket, name, False):
                    return self._send(h, 400, {"statusCode": "403", "error": "Unauthorized", "message": "Object not found or access denied"})
                if not f.exists():
                    return self._send(h, 400, {"statusCode": "404", "error": "not_found", "message": "Object not found"})
                ctype = "text/csv" if name.endswith(".csv") else "application/json" if name.endswith(".json") else "application/octet-stream"
                return self._send(h, 200, f.read_bytes(), ctype)
            if method == "DELETE":
                if f.exists():
                    f.unlink()
                return self._send(h, 200, {"message": "Successfully deleted"})
        return self._send(h, 404, {"error": f"storage route {route} not implemented"})


def _multipart_file(body: bytes, content_type: str) -> bytes:
    boundary = content_type.split("boundary=")[1].strip().encode()
    for part in body.split(b"--" + boundary):
        if b"filename=" in part.split(b"\r\n\r\n", 1)[0]:
            return part.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]
    return body


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


if __name__ == "__main__":
    hs = Harness().start()
    print("harness url", hs.url, "anon", anon_key(), "service", service_key())
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        hs.stop()
