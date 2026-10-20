# -*- coding: utf-8 -*-
"""
Status SUS — Vercel Serverless Function (Python Runtime)
Fornece os endpoints /api/status e /api/probe/<id> na infraestrutura Vercel Serverless.
"""

from http.server import BaseHTTPRequestHandler
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Insere a raiz do projeto no path para importar o motor de sondagem de server.py
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    import server
    server.seed_initial_history()
except Exception as e:
    server = None
    _import_error = str(e)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if not server:
            return self._send_json({"error": "Erro ao carregar módulo do servidor", "detail": _import_error}, 500)

        # Endpoint /api/status
        if path.endswith("/status") or path == "/api/status":
            force = qs.get("force", ["0"])[0] == "1"
            if force or not server.LATEST_SNAPSHOT["updatedAt"]:
                server.run_full_scan()
            return self._send_json(server.LATEST_SNAPSHOT)

        # Endpoint /api/probe/<id>
        if "/probe/" in path:
            svc_id = path.split("/probe/", 1)[1].strip("/")
            target = next((s for s in server.SERVICES_CATALOG if s["id"] == svc_id), None)
            if not target:
                return self._send_json({"error": f"Serviço '{svc_id}' não encontrado."}, 404)
            res = server.probe_single_service(target)
            return self._send_json(res)

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
        self.send_header("Cache-Control", "s-maxage=15, stale-while-revalidate=45")
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
