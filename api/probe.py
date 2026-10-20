# -*- coding: utf-8 -*-
"""
Status SUS — Vercel Serverless Function (/api/probe)
"""

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
except Exception as e:
    server = None
    _import_error = str(e)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if not server:
            return self._send_json({"error": "Erro ao carregar módulo do servidor", "detail": _import_error}, 500)

        svc_id = qs.get("id", [""])[0]
        if not svc_id and "/probe/" in parsed.path:
            svc_id = parsed.path.split("/probe/", 1)[1].strip("/")

        target = next((s for s in server.SERVICES_CATALOG if s["id"] == svc_id), None)
        if not target:
            return self._send_json({"error": f"Serviço '{svc_id}' não encontrado."}, 404)

        res = server.probe_single_service(target)
        return self._send_json(res)

    def _send_json(self, payload: dict, status_code: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
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
