from __future__ import annotations

import ast
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from fpl_decision_app.api import create_app
from fpl_decision_app.artifacts import (
    ARTIFACT_INDEX_VERSION,
    ArtifactNotFoundError,
    ArtifactStoreUnavailableError,
    DecisionArtifactReference,
    FilesystemDecisionArtifactStore,
)
from fpl_decision_app.authorization import (
    AuthorizationDeniedError,
    AuthorizationRequest,
    LOCAL_SINGLE_USER_PRINCIPAL,
)
from fpl_decision_app.read_facade import (
    ReadFailureCode,
    TrustedArtifactReadFacade,
    TrustedReadError,
)
from fpl_decision_engine.trusted_artifact_reader import (
    TrustedArtifactValidationError,
    load_verified_gameweek_decision,
)
from web_fixture_support import materialized_synthetic_completed_decision


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class RecordingPolicy:
    def __init__(self, events: list[str], *, deny: bool = False) -> None:
        self.events = events
        self.deny = deny
        self.requests: list[AuthorizationRequest] = []

    def authorize(self, request: AuthorizationRequest) -> None:
        self.events.append("authorize")
        self.requests.append(request)
        if self.deny:
            raise AuthorizationDeniedError("denied by test policy")


class RecordingStore:
    def __init__(
        self,
        events: list[str],
        reference: DecisionArtifactReference | None,
        error: Exception | None = None,
    ) -> None:
        self.events = events
        self.reference = reference
        self.error = error
        self.calls: list[str] = []

    def resolve_decision(self, decision_id: str) -> DecisionArtifactReference:
        self.events.append("resolve")
        self.calls.append(decision_id)
        if self.error is not None:
            raise self.error
        if self.reference is None:
            raise ArtifactNotFoundError("not found")
        return self.reference


class StubLoader:
    def __init__(self, value=None, error: Exception | None = None) -> None:
        self.value = value
        self.error = error

    def load(self, final_manifest_path: Path):
        if self.error is not None:
            raise self.error
        return self.value


class WebApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture_manager = materialized_synthetic_completed_decision()
        cls.fixture = cls.fixture_manager.__enter__()
        cls.verified = load_verified_gameweek_decision(
            cls.fixture.final_manifest_path
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture_manager.__exit__(None, None, None)

    def reference(self, digest: str | None = None) -> DecisionArtifactReference:
        return DecisionArtifactReference(
            decision_id=self.fixture.decision_id,
            final_manifest_path=self.fixture.final_manifest_path,
            expected_final_manifest_sha256=(
                digest or self.fixture.final_manifest_sha256
            ),
        )

    def facade(
        self,
        *,
        policy: RecordingPolicy | None = None,
        store: RecordingStore | None = None,
        loader=None,
    ) -> TrustedArtifactReadFacade:
        events: list[str] = []
        return TrustedArtifactReadFacade(
            authorization_policy=policy or RecordingPolicy(events),
            artifact_store=store or RecordingStore(events, self.reference()),
            loader=loader or StubLoader(self.verified),
        )

    def test_authorization_runs_before_resolution_on_every_authoritative_read(self) -> None:
        events: list[str] = []
        policy = RecordingPolicy(events)
        store = RecordingStore(events, self.reference())
        facade = self.facade(policy=policy, store=store)

        first = facade.read_decision(
            principal_id=LOCAL_SINGLE_USER_PRINCIPAL,
            decision_id=self.fixture.decision_id,
        )
        second = facade.read_decision(
            principal_id=LOCAL_SINGLE_USER_PRINCIPAL,
            decision_id=self.fixture.decision_id,
        )

        self.assertEqual(first, second)
        self.assertEqual(events, ["authorize", "resolve", "authorize", "resolve"])
        self.assertEqual(len(policy.requests), 2)
        self.assertEqual(len(store.calls), 2)

    def test_denial_stops_artifact_resolution(self) -> None:
        events: list[str] = []
        policy = RecordingPolicy(events, deny=True)
        store = RecordingStore(events, self.reference())
        with self.assertRaises(TrustedReadError) as caught:
            self.facade(policy=policy, store=store).read_decision(
                principal_id="unauthorized-subject",
                decision_id=self.fixture.decision_id,
            )
        self.assertEqual(caught.exception.code, ReadFailureCode.UNAUTHORIZED)
        self.assertEqual(events, ["authorize"])
        self.assertEqual(store.calls, [])

    def test_guessed_or_invalid_identity_cannot_bypass_authorization(self) -> None:
        events: list[str] = []
        policy = RecordingPolicy(events, deny=True)
        store = RecordingStore(events, self.reference())
        with self.assertRaises(TrustedReadError) as caught:
            self.facade(policy=policy, store=store).read_decision(
                principal_id="unauthorized-subject",
                decision_id="latest",
            )
        self.assertEqual(caught.exception.code, ReadFailureCode.UNAUTHORIZED)
        self.assertEqual(events, ["authorize"])
        self.assertEqual(store.calls, [])

        events.clear()
        allowed = RecordingPolicy(events)
        with self.assertRaises(TrustedReadError) as invalid:
            self.facade(policy=allowed, store=store).read_decision(
                principal_id=LOCAL_SINGLE_USER_PRINCIPAL,
                decision_id="latest",
            )
        self.assertEqual(invalid.exception.code, ReadFailureCode.ARTIFACT_INVALID)
        self.assertEqual(events, ["authorize"])

    def test_explicit_id_loads_through_existing_completed_evidence_validation(self) -> None:
        events: list[str] = []
        facade = TrustedArtifactReadFacade(
            authorization_policy=RecordingPolicy(events),
            artifact_store=RecordingStore(events, self.reference()),
        )
        envelope = facade.read_decision(
            principal_id=LOCAL_SINGLE_USER_PRINCIPAL,
            decision_id=self.fixture.decision_id,
        )
        expected_payload = json.loads(
            self.fixture.gameweek_decision_path.read_bytes()
        )
        self.assertEqual(envelope["payload"], expected_payload)
        self.assertEqual(
            envelope["artifact_identity"]["semantic_id"],
            self.fixture.decision_id,
        )
        self.assertNotIn("path", json.dumps(envelope).lower())

    def test_missing_artifact_and_final_manifest_hash_mismatch_fail_closed(self) -> None:
        events: list[str] = []
        for store, expected in (
            (RecordingStore(events, None), ReadFailureCode.NOT_FOUND),
            (
                RecordingStore(events, self.reference("f" * 64)),
                ReadFailureCode.HASH_MISMATCH,
            ),
        ):
            with self.subTest(expected=expected):
                with self.assertRaises(TrustedReadError) as caught:
                    self.facade(store=store).read_decision(
                        principal_id=LOCAL_SINGLE_USER_PRINCIPAL,
                        decision_id=self.fixture.decision_id,
                    )
                self.assertEqual(caught.exception.code, expected)

    def test_unavailable_artifact_store_fails_closed(self) -> None:
        store = RecordingStore(
            [],
            None,
            ArtifactStoreUnavailableError("synthetic unavailable store"),
        )
        with self.assertRaises(TrustedReadError) as caught:
            self.facade(store=store).read_decision(
                principal_id=LOCAL_SINGLE_USER_PRINCIPAL,
                decision_id=self.fixture.decision_id,
            )
        self.assertEqual(caught.exception.code, ReadFailureCode.UPSTREAM_UNAVAILABLE)

    def test_corrupted_child_artifact_fails_the_existing_trust_chain(self) -> None:
        path = self.fixture.gameweek_decision_path
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"tamper")
            with self.assertRaises(TrustedReadError) as caught:
                TrustedArtifactReadFacade(
                    authorization_policy=RecordingPolicy([]),
                    artifact_store=RecordingStore([], self.reference()),
                ).read_decision(
                    principal_id=LOCAL_SINGLE_USER_PRINCIPAL,
                    decision_id=self.fixture.decision_id,
                )
            self.assertEqual(caught.exception.code, ReadFailureCode.TRUST_CHAIN_INVALID)
        finally:
            path.write_bytes(original)

    def test_unsupported_schema_and_mismatched_identity_fail_closed(self) -> None:
        unsupported = replace(self.verified, schema_version="9.0.0")
        mismatch = replace(self.verified, decision_id="decision_" + "f" * 64)
        cases = (
            (unsupported, ReadFailureCode.UNSUPPORTED_SCHEMA),
            (mismatch, ReadFailureCode.TRUST_CHAIN_INVALID),
        )
        for value, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(TrustedReadError) as caught:
                    self.facade(loader=StubLoader(value)).read_decision(
                        principal_id=LOCAL_SINGLE_USER_PRINCIPAL,
                        decision_id=self.fixture.decision_id,
                    )
                self.assertEqual(caught.exception.code, expected)

    def test_malformed_trusted_loader_result_never_returns_partial_payload(self) -> None:
        loader = StubLoader(
            error=TrustedArtifactValidationError("synthetic malformed chain")
        )
        with self.assertRaises(TrustedReadError) as caught:
            self.facade(loader=loader).read_decision(
                principal_id=LOCAL_SINGLE_USER_PRINCIPAL,
                decision_id=self.fixture.decision_id,
            )
        self.assertEqual(caught.exception.code, ReadFailureCode.TRUST_CHAIN_INVALID)

    def test_filesystem_store_uses_explicit_index_and_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "decision" / "final_operational_manifest.json"
            manifest.parent.mkdir()
            manifest.write_bytes(self.fixture.final_manifest_path.read_bytes())
            index = root / "index.json"
            index.write_text(
                json.dumps(
                    {
                        "version": ARTIFACT_INDEX_VERSION,
                        "decisions": [
                            {
                                "decision_id": self.fixture.decision_id,
                                "final_manifest_relative_path": (
                                    "decision/final_operational_manifest.json"
                                ),
                                "final_manifest_sha256": self.fixture.final_manifest_sha256,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            store = FilesystemDecisionArtifactStore.from_index_file(root, index)
            self.assertEqual(
                store.resolve_decision(self.fixture.decision_id).final_manifest_path,
                manifest.resolve(),
            )
            escaping = FilesystemDecisionArtifactStore(
                root,
                {self.fixture.decision_id: ("../outside.json", "a" * 64)},
            )
            with self.assertRaisesRegex(Exception, "escapes"):
                escaping.resolve_decision(self.fixture.decision_id)

    def test_api_returns_only_the_canonical_payload_inside_versioned_envelope(self) -> None:
        facade = self.facade()
        with TestClient(create_app(facade)) as client:
            health = client.get("/api/v1/health")
            response = client.get(f"/api/v1/decisions/{self.fixture.decision_id}")
            repeated = client.get(f"/api/v1/decisions/{self.fixture.decision_id}")
        self.assertEqual(health.json(), {"api_version": "1.0", "status": "ready"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), repeated.json())
        self.assertEqual(
            response.headers["etag"],
            f'"{self.fixture.gameweek_decision_sha256}"',
        )
        self.assertEqual(response.headers["cache-control"], "private, immutable")
        body = response.json()
        self.assertEqual(
            set(body), {"api_version", "artifact_identity", "trust", "payload"}
        )
        self.assertEqual(
            body["payload"],
            json.loads(self.fixture.gameweek_decision_path.read_bytes()),
        )
        for duplicated in (
            "recommended_action",
            "starting_xi",
            "captain",
            "vice_captain",
            "formation",
        ):
            self.assertNotIn(duplicated, body)

    def test_api_failure_is_machine_readable_and_has_no_trusted_payload(self) -> None:
        events: list[str] = []
        facade = self.facade(
            policy=RecordingPolicy(events, deny=True),
            store=RecordingStore(events, self.reference()),
        )
        with TestClient(create_app(facade)) as client:
            response = client.get(f"/api/v1/decisions/{self.fixture.decision_id}")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "UNAUTHORIZED")
        self.assertNotIn("payload", response.json())
        self.assertEqual(events, ["authorize"])

    def test_no_authoritative_latest_route_or_store_method_exists(self) -> None:
        with TestClient(create_app(self.facade())) as client:
            paths = set(client.app.openapi()["paths"])
        self.assertNotIn("/api/v1/decisions/latest", paths)
        self.assertFalse(
            hasattr(FilesystemDecisionArtifactStore, "resolve_latest_decision")
        )


class DependencyBoundaryTests(unittest.TestCase):
    def test_checked_openapi_contract_matches_application(self) -> None:
        expected = json.loads(
            (REPOSITORY_ROOT / "contracts" / "api" / "v1" / "openapi.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(create_app().openapi(), expected)

    def test_trusted_engine_never_imports_application_package(self) -> None:
        for path in (REPOSITORY_ROOT / "src" / "fpl_decision_engine").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported = [
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            ] + [
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            ]
            self.assertFalse(
                any(name.startswith("fpl_decision_app") for name in imported),
                path,
            )

    def test_application_imports_only_the_public_trusted_reader_seam(self) -> None:
        forbidden = {
            "duckdb",
            "highspy",
            "fpl_decision_engine.decision",
            "fpl_decision_engine.decision_reliability",
            "fpl_decision_engine.features",
            "fpl_decision_engine.predictions",
            "fpl_decision_engine.transfer_decision",
        }
        for path in (REPOSITORY_ROOT / "src" / "fpl_decision_app").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported = [
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            ] + [
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            ]
            violations = sorted(
                name
                for name in imported
                if any(name == item or name.startswith(item + ".") for item in forbidden)
            )
            self.assertEqual(violations, [], f"forbidden imports in {path}")

    def test_task026b_fixture_is_synthetic_and_contains_no_manager_evidence(self) -> None:
        task_files = (
            REPOSITORY_ROOT / "tests" / "web_fixture_support.py",
            REPOSITORY_ROOT / "tests" / "test_web_application.py",
            REPOSITORY_ROOT / "web" / "src" / "test" / "fixture.ts",
            REPOSITORY_ROOT / "contracts" / "api" / "v1" / "openapi.json",
        )
        forbidden = (
            "664" + "4775",
            "humiliation" + " kink",
            "natta" + "wat",
            "codex" + "-clipboard",
            ".p" + "ng",
        )
        content = "\n".join(path.read_text(encoding="utf-8") for path in task_files).lower()
        for value in forbidden:
            self.assertNotIn(value, content)


if __name__ == "__main__":
    unittest.main()
