# -*- coding: utf-8 -*-
"""Handlers compartilhados pelas funções Vercel."""

from http.server import BaseHTTPRequestHandler
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    import server
except Exception as exc:
    server = None
    _import_error = str(exc)


class APIHandler(BaseHTTPRequestHandler):
    endpoint = None

    def do_GET(self):
        if server is None:
            return self._send_json({"error": "Erro ao carregar módulo do servidor", "detail": _import_error}, 500)
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if self.endpoint == "status" or parsed.path.endswith("/status"):
            return self._send_json(*server.get_status(
                force=qs.get("force", ["0"])[0] == "1", serverless=True))
        if self.endpoint == "probe" or "/probe/" in parsed.path:
            svc_id = qs.get("id", [""])[0]
            if not svc_id and "/probe/" in parsed.path:
                svc_id = parsed.path.split("/probe/", 1)[1].strip("/")
            return self._send_json(*server.get_probe(svc_id))
        return self._send_json({
            "service": "Status SUS API (Vercel Serverless)",
            "status": "online",
            "endpoints": ["/api/status", "/api/probe/<service_id>"]
        })

    def _send_json(self, payload: dict, status_code: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, fmt, *args):
        pass
