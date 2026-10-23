"""Offline contract tests: python -m unittest discover -s tests -v."""
import copy
import datetime
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

# Do not create bytecode outside tests/ when importing the application.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        self.http = self.enterContext(patch("urllib.request.urlopen"))
        self.http.side_effect = AssertionError("Unexpected HTTP request")
        self.tcp = self.enterContext(patch("socket.create_connection"))
        self.tcp.side_effect = AssertionError("Unexpected TCP request")
        self.enterContext(patch.object(server, "HISTORY_STORE", {}))
        self.enterContext(patch.object(server, "LATEST_SNAPSHOT", copy.deepcopy(server.LATEST_SNAPSHOT)))
        self.enterContext(patch.object(server, "LAST_SCAN_AT", None))
        self.enterContext(patch.object(server, "LAST_FORCE_AT", None))

    def service(self, **overrides):
        svc = dict(server.CORE_SERVICES_CATALOG[0])
        svc.update(id="test-service", url="https://example.invalid/", extractVersion=False,
                   validateWsdl=False, expectedCodes=[200], warnLatencyMs=500)
        svc.update(overrides)
        return svc

    def probe(self, *, code=200, body=b"OK", elapsed=0.125, error=None, headers=None, **svc):
        response = MagicMock()
        response.__enter__.return_value = response
        response.getcode.return_value = code
        response.headers = headers or {}
        response.read.return_value = body
        self.http.side_effect = error
        self.http.return_value = response
        with patch.object(server.time, "perf_counter", side_effect=[10.0, 10.0 + elapsed]):
            return server.probe_single_service(self.service(**svc))


class ProbeTests(OfflineTestCase):
    def test_http_200_fast_is_operational(self):
        result = self.probe()
        self.assertEqual(result["status"], "operational")
        self.assertEqual(result["httpCode"], 200)
        self.assertEqual(result["latencyMs"], 125)
        self.tcp.assert_not_called()

    def test_http_200_slow_is_degraded(self):
        self.assertEqual(self.probe(elapsed=1.0)["status"], "degraded")

    def test_expected_http_401_is_mtls(self):
        error = HTTPError("https://example.invalid/", 401, "Unauthorized", {}, io.BytesIO(b""))
        self.addCleanup(error.close)
        result = self.probe(error=error, expectedCodes=[200, 401])
        self.assertEqual(result["status"], "mtls")
        self.assertEqual(result["httpCode"], 401)

    def test_http_500_is_outage(self):
        error = HTTPError("https://example.invalid/", 500, "Error", {}, io.BytesIO(b""))
        self.addCleanup(error.close)
        result = self.probe(error=error)
        self.assertEqual(result["status"], "outage")
        self.assertEqual(result["httpCode"], 500)

    def test_timeout_is_outage(self):
        result = self.probe(error=socket.timeout("timed out"))
        self.assertEqual(result["status"], "outage")
        self.assertTrue(result["error"])

    def test_tcp_connected_is_operational(self):
        self.tcp.side_effect = None
        result = self.probe(tcpHost="127.0.0.1", tcpPort=5433)
        self.assertEqual(result["status"], "operational")
        self.assertEqual(self.tcp.call_args.args[0], ("127.0.0.1", 5433))
        self.http.assert_not_called()

    def test_tcp_refused_is_outage(self):
        self.tcp.side_effect = ConnectionRefusedError("refused")
        result = self.probe(tcpHost="127.0.0.1", tcpPort=5433)
        self.assertEqual(result["status"], "outage")
        self.assertTrue(result["error"])
        self.http.assert_not_called()

    def test_wsdl_valid_contract(self):
        body = b'<definitions xmlns="http://schemas.xmlsoap.org/wsdl/"><service name="CadsusService"/></definitions>'
        result = self.probe(body=body, validateWsdl=True)
        self.assertTrue(result["extraMeta"]["wsdlValid"])

    def test_wsdl_html_is_invalid(self):
        result = self.probe(body=b"<html>Service unavailable</html>", validateWsdl=True)
        self.assertFalse(result["extraMeta"]["wsdlValid"])

    def test_wsdl_words_in_html_do_not_make_a_valid_contract(self):
        result = self.probe(body=b"<html>Error loading definitions for CadsusService</html>", validateWsdl=True)
        self.assertFalse(result["extraMeta"]["wsdlValid"])

    def test_centralizador_extracts_actual_version_and_build(self):
        body = '<font>Versão:</font><font>9.8.7</font><font id="build-date">2025-02-03_04-05-06</font>'.encode()
        meta = self.probe(body=body, extractVersion=True)["extraMeta"]
        self.assertEqual(meta["version"], "9.8.7")
        self.assertEqual(meta["buildDateRaw"], "2025-02-03_04-05-06")
        self.assertIn("03/02/2025", meta["buildDateFormatted"])
        self.assertIn("04:05:06", meta["buildDateFormatted"])

    def test_centralizador_missing_metadata_is_null(self):
        meta = self.probe(body=b"<html>No metadata</html>", extractVersion=True)["extraMeta"]
        for key in ("version", "buildDateRaw", "buildDateFormatted"):
            with self.subTest(key=key):
                self.assertIn(key, meta)
                self.assertIsNone(meta[key])

    def test_centralizador_empty_body_has_null_metadata(self):
        result = self.probe(body=b"", extractVersion=True, id="esus-centralizador-nacional")
        with patch.object(server, "SERVICES_CATALOG", [self.service(id=result["id"])]), patch.object(
            server, "probe_single_service", return_value=result
        ):
            meta = server.run_full_scan()["centralizadorMeta"]
        for key in ("version", "buildDateRaw", "buildDateFormatted", "serverHeader", "f5BigIpPool"):
            with self.subTest(key=key):
                self.assertIn(key, meta)
                self.assertIsNone(meta[key])


class DiagnosticsTests(OfflineTestCase):
    def cadsus(self, soap, web):
        diagnostics = server.build_intelligent_diagnostics({
            "cadsus-soap-wsdl": {"status": soap, "latencyMs": 100},
            "cadsus-web-humano": {"status": web, "latencyMs": 200},
        })
        return next(d for d in diagnostics if d["id"] == "diag-cadsus")

    def test_cadsus_both_available(self):
        self.assertEqual(self.cadsus("operational", "operational")["severity"], "operational")

    def test_cadsus_web_down_soap_available(self):
        self.assertEqual(self.cadsus("operational", "outage")["severity"], "degraded")

    def test_cadsus_soap_down(self):
        for web in ("operational", "outage"):
            with self.subTest(web=web):
                self.assertEqual(self.cadsus("outage", web)["severity"], "outage")


class ScanTests(OfflineTestCase):
    def scan(self, results):
        catalog = [{"id": r["id"]} for r in results]
        by_id = {r["id"]: r for r in results}
        with patch.object(server, "SERVICES_CATALOG", catalog), patch.object(
            server, "probe_single_service", side_effect=lambda svc: by_id[svc["id"]]
        ), patch.object(server, "build_intelligent_diagnostics", return_value=[]):
            return server.run_full_scan()

    def test_summary_and_weighted_health_score(self):
        results = [dict(id=str(i), status=status, latencyMs=latency) for i, (status, latency) in enumerate(
            [("operational", 100), ("mtls", 200), ("degraded", 900), ("outage", 5000)])]
        snapshot = self.scan(results)
        self.assertEqual(snapshot["summary"], {
            "total": 4, "operational": 2, "degraded": 1, "outage": 1,
            "healthScorePct": 62.5, "avgLatencyMs": 400,
        })
        self.assertEqual([r["id"] for r in snapshot["services"]], ["0", "1", "2", "3"])
        self.assertTrue(snapshot["updatedAt"])
        self.assertIsInstance(snapshot["ttlSeconds"], int)
        self.assertIsNotNone(datetime.datetime.fromisoformat(snapshot["updatedAtIso"]).tzinfo)

    def test_empty_scan(self):
        summary = self.scan([])["summary"]
        self.assertEqual(summary["total"], 0)
        self.assertIsNone(summary["healthScorePct"])
        self.assertEqual(summary["avgLatencyMs"], 0)

    def test_probe_exception_becomes_outage_without_crashing_scan(self):
        with patch.object(server, "SERVICES_CATALOG", [self.service()]), patch.object(
            server, "probe_single_service", side_effect=RuntimeError("probe failed")
        ):
            snapshot = server.run_full_scan()
        self.assertEqual(snapshot["summary"]["outage"], 1)
        self.assertEqual(snapshot["summary"]["healthScorePct"], 0.0)


class CatalogTests(OfflineTestCase):
    def test_temporary_config_adds_enabled_custom_endpoints(self):
        enabled = self.service(id="enabled", enabled=True)
        implicit = self.service(id="implicit")
        disabled = self.service(id="disabled", enabled=False)
        original = copy.deepcopy(server.CORE_SERVICES_CATALOG)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            config = Path(directory) / "config.json"
            config.write_text(json.dumps({"customEndpoints": [enabled, disabled, implicit]}), encoding="utf-8")
            with patch.object(server, "CONFIG_FILE", config):
                with patch.object(server, "CONFIG", server.load_config()):
                    loaded = server.load_services_catalog()
        self.assertEqual(loaded, original + [enabled, implicit])
        self.assertEqual(server.CORE_SERVICES_CATALOG, original)

    def test_missing_config_uses_core_catalog(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            with patch.object(server, "CONFIG_FILE", Path(directory) / "config.json"):
                with patch.object(server, "CONFIG", server.load_config()):
                    self.assertEqual(server.load_services_catalog(), server.CORE_SERVICES_CATALOG)


class RateLimitTests(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.clock = self.enterContext(patch.object(server.time, "monotonic", return_value=100.0))
        self.scan = self.enterContext(patch.object(server, "run_full_scan"))

    def request(self, path):
        # Exercise routing/serialization without binding any socket.
        handler = object.__new__(server.StatusSUSHandler)
        handler.path = path
        handler.wfile = io.BytesIO()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.do_GET()
        return handler, json.loads(handler.wfile.getvalue())

    def test_first_force_scans_and_returns_200(self):
        handler, payload = self.request("/api/status?force=1")
        handler.send_response.assert_called_once_with(200)
        self.scan.assert_called_once_with()
        self.assertIn("services", payload)
        self.assertIs(payload["forceThrottled"], False)

    def test_repeated_force_returns_cached_200_with_throttled_flag(self):
        self.request("/api/status?force=1")
        self.scan.reset_mock()
        self.clock.return_value = 101.0
        handler, payload = self.request("/api/status?force=1")
        handler.send_response.assert_called_once_with(200)
        self.assertIs(payload["forceThrottled"], True)
        self.assertEqual(payload["services"], server.LATEST_SNAPSHOT["services"])
        self.scan.assert_not_called()

    def test_force_allowed_at_ten_seconds_boundary(self):
        self.request("/api/status?force=1")
        self.clock.return_value = 109.999
        rejected, payload = self.request("/api/status?force=1")
        rejected.send_response.assert_called_once_with(200)
        self.assertIs(payload["forceThrottled"], True)
        self.clock.return_value = 110.0
        accepted, payload = self.request("/api/status?force=1")
        accepted.send_response.assert_called_once_with(200)
        self.assertIs(payload["forceThrottled"], False)
        self.assertEqual(self.scan.call_count, 2)

    def test_regular_get_not_rate_limited_and_does_not_scan(self):
        self.request("/api/status?force=1")
        self.scan.reset_mock()
        for path in ("/api/status", "/api/status?force=0"):
            with self.subTest(path=path):
                handler, payload = self.request(path)
                handler.send_response.assert_called_once_with(200)
                self.assertIs(payload["forceThrottled"], False)
        self.scan.assert_not_called()

    def test_contract_constants_can_be_patched(self):
        self.assertEqual(server.FORCE_MIN_INTERVAL_S, 10)
        self.assertEqual(server.SNAPSHOT_TTL_S, 30)
        with patch.object(server, "FORCE_MIN_INTERVAL_S", 2):
            self.request("/api/status?force=1")
            self.clock.return_value = 102.0
            _, payload = self.request("/api/status?force=1")
        self.assertIs(payload["forceThrottled"], False)
        self.assertEqual(self.scan.call_count, 2)

    def test_unknown_probe_returns_404(self):
        handler, payload = self.request("/api/probe/no-such-service")
        handler.send_response.assert_called_once_with(404)
        self.assertTrue(payload["error"])
        self.scan.assert_not_called()

    def test_serverless_uses_fresh_cache_and_refreshes_after_ttl(self):
        with patch.object(server, "LAST_SCAN_AT", 100.0), patch.object(
            server, "_run_full_scan"
        ) as refresh:
            self.clock.return_value = 130.0
            _, code = server.get_status(serverless=True)
            self.assertEqual(code, 200)
            refresh.assert_not_called()
            self.clock.return_value = 130.001
            server.get_status(serverless=True)
            refresh.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
