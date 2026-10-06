#!/usr/bin/env python3
"""One-time local GitHub App setup; credentials go directly to macOS Keychain.

Run this on the organizer's Mac, open the printed localhost URL with the logged-in
Cindy browser, and review GitHub's creation screen. No PEM/.env file is written.
The app is public only so it can be installed in the six separate organizations;
the runtime independently restricts installations to configured organization IDs.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEYCHAIN_SERVICE = "agentic-observer26-github-app"


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--account",default="BH3GEI")
    parser.add_argument("--port",type=int,default=0)
    args=parser.parse_args()
    if sys.platform!="darwin":
        parser.error("This bootstrap stores credentials in macOS Keychain.")
    manifest=json.loads((Path(__file__).resolve().parents[1]/"ops/github-app-manifest.json").read_text())
    state=secrets.token_urlsafe(32)
    complete=threading.Event()
    public_result={}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_args): pass  # OAuth callback query strings are sensitive.

        def reply(self,status,body):
            data=body.encode()
            self.send_response(status)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Cache-Control","no-store")
            self.send_header("Referrer-Policy","no-referrer")
            self.send_header("Content-Length",str(len(data)))
            self.end_headers(); self.wfile.write(data)

        def do_GET(self):
            if self.headers.get("Host")!=f"127.0.0.1:{self.server.server_port}":
                return self.reply(400,"Invalid host")
            url=urllib.parse.urlsplit(self.path)
            if url.path=="/":
                action="https://github.com/settings/apps/new?"+urllib.parse.urlencode({"state":state})
                return self.reply(200,'<!doctype html><meta charset="utf-8"><title>Observer GitHub App</title>'
                    '<h1>Register the competition GitHub App</h1>'
                    '<p>Credentials will be saved directly to macOS Keychain.</p>'
                    f'<form method="post" action="{html.escape(action,quote=True)}">'
                    f'<input type="hidden" name="manifest" value="{html.escape(json.dumps(manifest),quote=True)}">'
                    '<button type="submit">Continue to GitHub</button></form>')
            if url.path=="/complete":
                if not complete.is_set():
                    return self.reply(409,"App setup is not complete")
                return self.reply(200,"<!doctype html><meta charset=utf-8><h1>GitHub App created</h1>"
                    "<p>Credentials are stored in macOS Keychain.</p><p>"+html.escape(public_result["slug"])+"</p>")
            if url.path!="/callback" or complete.is_set():
                return self.reply(404,"Not found")
            query=urllib.parse.parse_qs(url.query)
            if not secrets.compare_digest(query.get("state",[""])[0],state):
                return self.reply(403,"Invalid setup state")
            code=query.get("code",[""])[0]
            if not re.fullmatch(r"[A-Za-z0-9_-]{10,200}",code):
                return self.reply(400,"Invalid conversion code")
            try:
                req=urllib.request.Request("https://api.github.com/app-manifests/"+code+"/conversions",
                    data=b"{}",method="POST",headers={"Accept":"application/vnd.github+json",
                    "Content-Type":"application/json","User-Agent":"Agentic-Observer-Setup"})
                with urllib.request.urlopen(req,timeout=30) as response:
                    credentials=json.load(response)
                if not credentials.get("pem") or not credentials.get("id"):
                    raise ValueError("Invalid app response")
                saved=subprocess.run(["security","add-generic-password","-U","-s",KEYCHAIN_SERVICE,
                    "-a",args.account,"-w",json.dumps(credentials)],capture_output=True)
                if saved.returncode:
                    raise RuntimeError("Keychain could not save the app")
                public_result.update({k:credentials.get(k) for k in ("id","slug","html_url","client_id")})
                complete.set()
                print(json.dumps({"created":public_result,"keychain_service":KEYCHAIN_SERVICE}),flush=True)
                self.send_response(303); self.send_header("Location","/complete")
                self.send_header("Cache-Control","no-store"); self.end_headers()
            except Exception:
                self.reply(503,"Setup failed; no credential details are displayed. Check local Keychain access.")

    server=ThreadingHTTPServer(("127.0.0.1",args.port),Handler)
    manifest["redirect_url"]=f"http://127.0.0.1:{server.server_port}/callback"
    print(f"Open http://127.0.0.1:{server.server_port}/",flush=True)
    server.serve_forever()


if __name__=="__main__":
    main()
