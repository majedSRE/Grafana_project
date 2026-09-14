import argparse
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import capacity_load as load


class CapacityPayloadTests(unittest.TestCase):
    def test_metric_batches_preserve_series_identity_across_samples(self):
        first = load.metric_payload(1000, 10, 200, 100)
        second = load.metric_payload(1000, 10, 300, 100)
        def points(payload):
            return payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["gauge"]["dataPoints"]
        self.assertEqual([p["attributes"] for p in points(first)], [p["attributes"] for p in points(second)])
        self.assertNotEqual(points(first)[0]["timeUnixNano"], points(second)[0]["timeUnixNano"])
        self.assertEqual(points(first)[0]["attributes"][0]["value"]["stringValue"], "1000")

    def test_log_body_budget_is_one_kib_per_record(self):
        records = load.log_payload(3, 200)["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        self.assertEqual(sum(len(item["body"]["stringValue"].encode()) for item in records), 3072)
        self.assertEqual(len(set(item["body"]["stringValue"] for item in records)), 3)

    def test_trace_payload_has_unique_ids_and_representative_size(self):
        spans = load.trace_payload(3, 2_000_000)["resourceSpans"][0]["scopeSpans"][0]["spans"]
        self.assertEqual(len(set(span["traceId"] for span in spans)), 3)
        for span in spans:
            self.assertRegex(span["traceId"], r"^[0-9a-f]{32}$")
            self.assertTrue(900 <= len(json.dumps(span).encode()) <= 1300)

    def test_burst_boundaries_return_to_baseline(self):
        args = argparse.Namespace(burst_at=10, burst_seconds=5, burst_factor=2)
        self.assertEqual([load.burst_factor(t, args) for t in (9, 10, 14, 15)], [1, 2, 2, 1])


if __name__ == "__main__":
    unittest.main()
