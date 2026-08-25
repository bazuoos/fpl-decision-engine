from __future__ import annotations

import logging
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError

from fpl_decision_engine.pipeline import (
    HTTPStatusError,
    InvalidJSONError,
    NetworkError,
    SnapshotExistsError,
    fetch_bootstrap_static,
)


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.data_root = Path(self.temporary_directory.name)
        self.now = datetime(2026, 8, 24, 1, 2, 3, 456789, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def opener_for(body: bytes, status: int = 200):
        def opener(url: str, *, timeout: float) -> FakeResponse:
            return FakeResponse(body, status)

        return opener

    def test_saves_exact_response_in_timestamped_season_directory(self) -> None:
        body = b'{\n  "events": [], "elements": [{"id": 1}]\n}\n'

        with self.assertLogs(level=logging.INFO) as logs:
            path = fetch_bootstrap_static(
                data_root=self.data_root,
                season="2026-27",
                opener=self.opener_for(body),
                now=self.now,
            )

        expected = (
            self.data_root
            / "2026-27"
            / "20260824T010203.456789Z"
            / "bootstrap-static.json"
        )
        self.assertEqual(path, expected)
        self.assertEqual(path.read_bytes(), body)
        self.assertIn("Starting FPL bootstrap-static fetch", logs.output[0])
        self.assertTrue(any("fetch succeeded" in line for line in logs.output))
        self.assertTrue(any(str(path) in line for line in logs.output))

    def test_never_overwrites_an_existing_snapshot(self) -> None:
        first_body = b'{"version": 1, "elements": [{"id": 1}]}'
        path = fetch_bootstrap_static(
            data_root=self.data_root,
            opener=self.opener_for(first_body),
            now=self.now,
        )

        with self.assertRaises(SnapshotExistsError):
            fetch_bootstrap_static(
                data_root=self.data_root,
                opener=self.opener_for(
                    b'{"version": 2, "elements": [{"id": 2}]}'
                ),
                now=self.now,
            )

        self.assertEqual(path.read_bytes(), first_body)

    def test_rejects_non_success_http_status(self) -> None:
        with self.assertRaises(HTTPStatusError):
            fetch_bootstrap_static(
                data_root=self.data_root,
                opener=self.opener_for(b'{"error": true}', status=503),
                now=self.now,
            )
        self.assertEqual(list(self.data_root.iterdir()), [])

    def test_wraps_network_errors(self) -> None:
        def failing_opener(url: str, *, timeout: float) -> FakeResponse:
            raise URLError("offline")

        with self.assertRaises(NetworkError):
            fetch_bootstrap_static(
                data_root=self.data_root,
                opener=failing_opener,
                now=self.now,
            )

    def test_rejects_invalid_json_without_creating_snapshot(self) -> None:
        with self.assertRaises(InvalidJSONError):
            fetch_bootstrap_static(
                data_root=self.data_root,
                opener=self.opener_for(b"not json"),
                now=self.now,
            )
        self.assertEqual(list(self.data_root.iterdir()), [])

    def test_rejects_valid_json_with_wrong_bootstrap_schema(self) -> None:
        with self.assertRaisesRegex(InvalidJSONError, "elements"):
            fetch_bootstrap_static(
                data_root=self.data_root,
                opener=self.opener_for(b'{"error": "temporarily unavailable"}'),
                now=self.now,
            )
        self.assertEqual(list(self.data_root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
