"""Offline contract tests for diagnostic scripts; no running services required."""

import argparse
import base64
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import telemetry_probe as probe
import verify


def query_response(kind, results):
    return {"status": "success", "data": {"resultType": kind, "result": results}}


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.run = probe.new_run()
        self.payloads = probe.payloads(self.run)

    def test_cumulative_counter_and_unique_gauge_do_not_create_run_labels(self):
        resource_metrics = self.payloads["metrics"]["resourceMetrics"][0]
        metrics = resource_metrics["scopeMetrics"][0]["metrics"]
        counter = metrics[0]["sum"]
        self.assertEqual(counter["aggregationTemporality"], 2)
        self.assertIs(counter["isMonotonic"], True)
        for point in (counter["dataPoints"][0], metrics[1]["gauge"]["dataPoints"][0]):
            self.assertEqual(int(point["asInt"]), self.run["metricValue"])
            self.assertLess(int(point["startTimeUnixNano"]), int(point["timeUnixNano"]))
            self.assertEqual(point["attributes"], [probe.attribute("validation.kind", "smoke")])
        self.assertNotIn(self.run["runId"], json.dumps(self.payloads["metrics"]))

    def test_log_carries_unique_marker_and_trace_correlation(self):
        record = self.payloads["logs"]["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        self.assertEqual(record["body"]["stringValue"], self.run["logMarker"])
        self.assertEqual(record["traceId"], self.run["traceId"])
        self.assertEqual(record["spanId"], self.run["rootSpanId"])
        self.assertRegex(record["traceId"], r"^[0-9a-f]{32}$")
        self.assertIsInstance(record["timeUnixNano"], str)

    def test_trace_contains_valid_parent_child_relationship(self):
        spans = self.payloads["traces"]["resourceSpans"][0]["scopeSpans"][0]["spans"]
        root, child = spans
        self.assertEqual(child["parentSpanId"], root["spanId"])
        self.assertEqual(root["traceId"], child["traceId"])
        self.assertNotEqual(root["spanId"], child["spanId"])
        self.assertNotIn("parentSpanId", root)
        self.assertLess(int(root["startTimeUnixNano"]), int(child["startTimeUnixNano"]))
        self.assertLess(int(child["endTimeUnixNano"]), int(root["endTimeUnixNano"]))
        for span in spans:
            self.assertIsInstance(span["kind"], int)
            self.assertIsInstance(span["status"]["code"], int)

    def test_run_record_rejects_invalid_data(self):
        changes = {"schemaVersion": 2, "runId": "invalid", "traceId": "x" * 32,
                   "metricValue": float("nan"), "createdUnixNano": "0", "logMarker": "unrelated"}
        for key, value in changes.items():
            with self.subTest(key=key), self.assertRaises(verify.ProbeError):
                probe.validate_run(dict(self.run, **{key: value}))


class HttpTests(unittest.TestCase):
    def response(self, body=b"{}", status=200):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = status
        response.read.return_value = body
        return response

    def test_otlp_sets_json_content_type_and_encodes_payload(self):
        with patch.object(verify, "urlopen", return_value=self.response()) as open_mock:
            self.assertEqual(verify.request_json("http://alloy:4318/v1/logs", 3, {"resourceLogs": []}), {})
        request = open_mock.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(json.loads(request.data), {"resourceLogs": []})
        self.assertEqual(open_mock.call_args.kwargs["timeout"], 3)

    def test_failed_http_and_timeout_do_not_print_response_or_credentials(self):
        failures = [HTTPError("http://example/", 503, "secret-token", {}, None),
                    URLError("secret-token"), TimeoutError("secret-token")]
        for failure in failures:
            with self.subTest(error=type(failure).__name__), patch.object(verify, "urlopen", side_effect=failure):
                with self.assertRaises(verify.ProbeError) as raised:
                    verify.request_json("http://alloy:4318/v1/logs")
                self.assertNotIn("secret-token", str(raised.exception))

    def test_invalid_endpoint_credentials_rejected(self):
        with patch.object(verify, "urlopen") as open_mock, self.assertRaises(verify.ProbeError):
            verify.request_json("http://user:secret@alloy:4318/v1/logs")
        open_mock.assert_not_called()

    def test_rejects_non_json_non_object_oversize_and_unexpected_status(self):
        for body, status in ((b"not json", 200), (b"[]", 200), (b"{}", 202),
                             (b"x" * (verify.MAX_RESPONSE_BYTES + 1), 200)):
            with self.subTest(status=status, length=len(body)), patch.object(
                verify, "urlopen", return_value=self.response(body, status)
            ), self.assertRaises(verify.ProbeError):
                verify.request_json("http://alloy:4318/v1/logs")

    def test_http_200_partial_success_is_not_a_success(self):
        for partial in ({"rejectedSpans": "1"}, {"rejectedDataPoints": "0", "errorMessage": "warning"},
                        {"rejectedLogRecords": "invalid"}):
            with self.subTest(partial=partial), self.assertRaises(verify.ProbeError):
                probe.require_otlp_success({"partialSuccess": partial})
        probe.require_otlp_success({})
        probe.require_otlp_success({"partialSuccess": {}})
        probe.require_otlp_success({"partialSuccess": {"rejectedSpans": "0"}})

    def test_partial_export_does_not_retry_or_send_remaining_signals(self):
        with patch.object(probe, "request_json", return_value={"partialSuccess": {"rejectedDataPoints": "1"}}) as request:
            with self.assertRaises(verify.ProbeError):
                probe.emit(probe.new_run(), "http://alloy:4318", 5)
        self.assertEqual(request.call_count, 1)


class ReadinessTests(unittest.TestCase):
    def test_grafana_requires_healthy_database(self):
        with patch.object(verify, "request_json", return_value={"database": "failed"}), self.assertRaises(verify.ProbeError):
            verify.check_service("grafana", "http://grafana:3000", 5)

    def test_alloy_checks_readiness_and_component_health(self):
        with patch.object(verify, "request_text", side_effect=["Alloy is ready.\n", "All Alloy components are healthy.\n"]) as request:
            verify.check_service("alloy", "http://alloy:12345", 5)
        self.assertEqual(request.call_count, 2)

    def test_cadvisor_requires_samples_not_just_metric_help_text(self):
        with patch.object(verify, "request_text", return_value="# HELP container_cpu_usage_seconds_total CPU\n"), self.assertRaises(verify.ProbeError):
            verify.check_service("cadvisor", "http://cadvisor:8080", 5)

    def test_targets_require_all_expected_jobs(self):
        response = query_response("vector", [{"metric": {"job": "prometheus"}, "value": [1, "1"]}])
        with patch.object(verify, "request_json", return_value=response), self.assertRaisesRegex(verify.ProbeError, "missing jobs: linux-server"):
            verify.check_targets("http://prometheus:9090", ["prometheus", "linux-server"], 5)

    def test_target_down_fails_even_when_another_target_of_same_job_is_up(self):
        response = query_response("vector", [
            {"metric": {"job": "linux-server"}, "value": [1, "1"]},
            {"metric": {"job": "linux-server"}, "value": [1, "0"]},
        ])
        with patch.object(verify, "request_json", return_value=response), self.assertRaisesRegex(verify.ProbeError, "targets not up"):
            verify.check_targets("http://prometheus:9090", ["linux-server"], 5)

    def test_query_error_in_http_200_body_fails(self):
        with self.assertRaises(verify.ProbeError):
            verify.query_results({"status": "error", "error": "query failed"}, "vector")


class BackendVerificationTests(unittest.TestCase):
    def setUp(self):
        self.run = probe.new_run()

    def test_metrics_checks_original_run_value_and_timestamp(self):
        response = query_response("vector", [{"value": [1, str(self.run["metricValue"])]}])
        with patch.object(probe, "request_json", return_value=response) as request:
            probe.check_metrics(self.run, "http://prometheus:9090", 5)
        self.assertEqual(request.call_count, 2)
        params = parse_qs(urlsplit(request.call_args.args[0]).query)
        self.assertAlmostEqual(float(params["time"][0]), int(self.run["createdUnixNano"]) / 1e9, places=1)
        self.assertIn('job="platform-validation"', params["query"][0])
        response["data"]["result"][0]["value"][1] = "0"
        with patch.object(probe, "request_json", return_value=response), self.assertRaises(verify.ProbeError):
            probe.check_metrics(self.run, "http://prometheus:9090", 5)

    def test_logs_require_the_emitted_unique_marker(self):
        response = query_response("streams", [{"values": [[self.run["createdUnixNano"], "other test message"]]}])
        with patch.object(probe, "request_json", return_value=response), self.assertRaises(verify.ProbeError):
            probe.check_logs(self.run, "http://loki:3100", 5)
        response["data"]["result"][0]["values"][0][1] = self.run["logMarker"]
        with patch.object(probe, "request_json", return_value=response):
            probe.check_logs(self.run, "http://loki:3100", 5)

    def test_trace_requires_both_expected_spans_and_the_parent_link(self):
        response = probe.payloads(self.run)["traces"]
        with patch.object(probe, "request_json", return_value=response):
            probe.check_trace(self.run, "http://tempo:3200", 5)
        child = response["resourceSpans"][0]["scopeSpans"][0]["spans"][1]
        child["parentSpanId"] = "0" * 16
        with patch.object(probe, "request_json", return_value=response), self.assertRaises(verify.ProbeError):
            probe.check_trace(self.run, "http://tempo:3200", 5)

    def test_tempo_base64_query_ids_are_supported(self):
        response = deepcopy(probe.payloads(self.run)["traces"])
        for span in probe.returned_spans(response):
            for field in ("traceId", "spanId", "parentSpanId"):
                if field in span:
                    span[field] = base64.b64encode(bytes.fromhex(span[field])).decode("ascii")
        # Tempo v1 can call the outer resourceSpans collection batches.
        response = {"batches": response["resourceSpans"]}
        with patch.object(probe, "request_json", return_value=response):
            probe.check_trace(self.run, "http://tempo:3200", 5)

    def test_empty_trace_http_200_does_not_pass(self):
        with patch.object(probe, "request_json", return_value={}), self.assertRaises(verify.ProbeError):
            probe.check_trace(self.run, "http://tempo:3200", 5)

    def test_retry_window_fails_with_pending_signal_details(self):
        args = argparse.Namespace(prometheus_url="http://p", loki_url="http://l", tempo_url="http://t", timeout=1, wait=2)
        with patch.object(probe, "check_metrics", side_effect=verify.ProbeError("missing sample")), \
             patch.object(probe, "check_logs", side_effect=verify.ProbeError("missing log")), \
             patch.object(probe, "check_trace", side_effect=verify.ProbeError("missing trace")), \
             patch.object(probe.time, "monotonic", side_effect=[0, 0, 0, 0, 3]), \
             patch.object(probe.time, "sleep") as sleep:
            with self.assertRaisesRegex(verify.ProbeError, "metrics: missing sample.*logs: missing log.*traces: missing trace"):
                probe.verify_signals(self.run, args)
        sleep.assert_not_called()

    def test_emit_record_can_be_verified_without_resending(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "probe.json")
            with patch.object(probe, "emit") as emit, redirect_stdout(io.StringIO()):
                self.assertEqual(probe.main(["--emit-only", "--record", path]), 0)
                emit.assert_called_once()
            with patch.object(probe, "emit") as emit, patch.object(probe, "verify_signals") as check:
                self.assertEqual(probe.main(["--verify-only", "--record", path]), 0)
                emit.assert_not_called()
                check.assert_called_once()
            with patch.object(probe, "emit") as emit, redirect_stderr(io.StringIO()):
                self.assertEqual(probe.main(["--emit-only", "--record", path]), 1)
                emit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
