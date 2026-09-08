from __future__ import annotations

import ast
import json
import os
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

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
from fpl_decision_engine.artifact_snapshot import (
    ArtifactSnapshotError,
    ReadOnceArtifactSnapshot,
)
from fpl_decision_engine.trusted_artifact_reader import (
    TrustedArtifactValidationError,
    gameweek_decision_openapi_components,
    load_verified_gameweek_decision,
)
from web_fixture_support import materialized_synthetic_completed_decision


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def imported_module_names(tree: ast.AST) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(
                f"{node.module}.{alias.name}" for alias in node.names
            )
    return imported


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

    def load(self, final_manifest_path: Path, final_manifest_bytes: bytes):
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

    def test_authoritative_chain_uses_facade_bytes_then_snapshot_paths(self) -> None:
        final_body = self.fixture.final_manifest_path.read_bytes()
        original_open = Path.open
        fixture_root = self.fixture.root.resolve()

        def reject_original_path_reads(path: Path, *args, **kwargs):
            resolved = path.resolve()
            mode = args[0] if args else kwargs.get("mode", "r")
            if (
                (resolved == fixture_root or fixture_root in resolved.parents)
                and "r" in mode
            ):
                raise AssertionError("trusted validators reopened an original artifact")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", reject_original_path_reads):
            verified = load_verified_gameweek_decision(
                self.fixture.final_manifest_path,
                final_manifest_bytes=final_body,
            )
        self.assertEqual(verified.decision_id, self.fixture.decision_id)
        self.assertEqual(
            verified.canonical_payload,
            self.fixture.gameweek_decision_path.read_bytes(),
        )

    def test_snapshot_reuses_stable_bytes_after_original_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary).resolve() / "artifact.json"
            original = b'{"state":"original"}\n'
            artifact.write_bytes(original)
            with ReadOnceArtifactSnapshot() as snapshot:
                self.assertEqual(snapshot.read_bytes(artifact), original)
                digest = snapshot.sha256(artifact)
                materialized = snapshot.materialized_path(artifact)
                artifact.write_bytes(b'{"state":"replaced"}\n')
                self.assertEqual(snapshot.read_bytes(artifact), original)
                self.assertEqual(snapshot.sha256(artifact), digest)
                self.assertEqual(materialized.read_bytes(), original)
            self.assertFalse(materialized.exists())

    def test_snapshot_rejects_final_and_intermediate_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            target_directory = root / "target"
            target_directory.mkdir()
            target = target_directory / "artifact.json"
            target.write_bytes(b"trusted\n")
            final_link = root / "artifact-link.json"
            final_link.symlink_to(target)
            directory_link = root / "directory-link"
            directory_link.symlink_to(target_directory, target_is_directory=True)

            for candidate in (final_link, directory_link / target.name):
                with self.subTest(candidate=candidate):
                    with ReadOnceArtifactSnapshot() as snapshot:
                        with self.assertRaises(ArtifactSnapshotError):
                            snapshot.read_bytes(candidate)

    def test_snapshot_rejects_fifo_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fifo = Path(temporary).resolve() / "artifact.pipe"
            os.mkfifo(fifo)
            real_open = os.open
            observed_source_open = False

            def require_nonblocking(path, flags, *args, **kwargs):
                nonlocal observed_source_open
                if path == fifo.name and not flags & os.O_DIRECTORY:
                    observed_source_open = True
                    self.assertTrue(flags & os.O_NONBLOCK)
                    self.assertTrue(flags & os.O_NOFOLLOW)
                return real_open(path, flags, *args, **kwargs)

            with patch(
                "fpl_decision_engine.artifact_snapshot.os.open",
                side_effect=require_nonblocking,
            ):
                with ReadOnceArtifactSnapshot() as snapshot:
                    with self.assertRaisesRegex(
                        ArtifactSnapshotError, "single-link regular file"
                    ):
                        snapshot.read_bytes(fifo)
            self.assertTrue(observed_source_open)

    def test_snapshot_rejects_hardlinked_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "artifact.json"
            alias = root / "artifact-alias.json"
            source.write_bytes(b"trusted\n")
            os.link(source, alias)
            with ReadOnceArtifactSnapshot() as snapshot:
                with self.assertRaisesRegex(
                    ArtifactSnapshotError, "single-link regular file"
                ):
                    snapshot.read_bytes(source)

    def test_snapshot_files_are_private_at_atomic_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "source.json"
            source.write_bytes(b"captured\n")
            real_fdopen = os.fdopen
            creation_modes: list[int] = []

            def inspect_mode(fd, *args, **kwargs):
                creation_modes.append(stat.S_IMODE(os.fstat(fd).st_mode))
                return real_fdopen(fd, *args, **kwargs)

            with patch(
                "fpl_decision_engine.artifact_snapshot.os.fdopen",
                side_effect=inspect_mode,
            ):
                with ReadOnceArtifactSnapshot() as snapshot:
                    snapshot.seed(root / "seeded.json", b"seeded\n")
                    snapshot.read_bytes(source)
            self.assertEqual(len(creation_modes), 2)
            self.assertTrue(all(mode & 0o077 == 0 for mode in creation_modes))

    def test_facade_passes_the_exact_hashed_manifest_bytes_to_the_loader(self) -> None:
        final_path = self.fixture.final_manifest_path
        original = final_path.read_bytes()

        class ReplacingLoader:
            def load(self, path: Path, body: bytes):
                self.body = body
                path.write_bytes(b'{"replaced_after_facade_read":true}\n')
                return load_verified_gameweek_decision(
                    path,
                    final_manifest_bytes=body,
                )

        loader = ReplacingLoader()
        try:
            envelope = self.facade(loader=loader).read_decision(
                principal_id=LOCAL_SINGLE_USER_PRINCIPAL,
                decision_id=self.fixture.decision_id,
            )
            self.assertEqual(loader.body, original)
            self.assertEqual(
                envelope["artifact_identity"]["final_manifest_sha256"],
                self.fixture.final_manifest_sha256,
            )
        finally:
            final_path.write_bytes(original)

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

    def test_openapi_payload_schema_is_the_authoritative_engine_schema(self) -> None:
        document = create_app().openapi()
        schema = document["components"]["schemas"]
        expected = gameweek_decision_openapi_components()
        self.assertLessEqual(expected.keys(), schema.keys())
        self.assertEqual(
            {name: schema[name] for name in expected},
            expected,
        )
        self.assertEqual(
            schema["DecisionReadResponse"]["properties"]["payload"],
            {"$ref": "#/components/schemas/GameweekDecision"},
        )

        def resolve_local_reference(reference: str) -> object:
            value: object = document
            for token in reference.removeprefix("#/").split("/"):
                self.assertIsInstance(value, dict)
                value = value[token.replace("~1", "/").replace("~0", "~")]
            return value

        references = []
        pending: list[object] = [expected]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                reference = value.get("$ref")
                if isinstance(reference, str):
                    references.append(reference)
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        self.assertTrue(references)
        for reference in references:
            with self.subTest(reference=reference):
                self.assertTrue(reference.startswith("#/"))
                self.assertIsInstance(resolve_local_reference(reference), dict)

    def test_trusted_engine_never_imports_application_package(self) -> None:
        for path in (REPOSITORY_ROOT / "src" / "fpl_decision_engine").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported = imported_module_names(tree)
            self.assertFalse(
                any(name.startswith("fpl_decision_app") for name in imported),
                path,
            )

    def test_import_extraction_expands_from_package_members(self) -> None:
        tree = ast.parse(
            "from fpl_decision_engine import decision_journal as journal"
        )
        self.assertIn(
            "fpl_decision_engine.decision_journal",
            imported_module_names(tree),
        )

    def test_application_imports_only_the_public_trusted_reader_seam(self) -> None:
        forbidden = {
            "duckdb",
            "highspy",
            "fpl_decision_engine.decision",
            "fpl_decision_engine.decision_journal",
            "fpl_decision_engine.decision_reliability",
            "fpl_decision_engine.features",
            "fpl_decision_engine.predictions",
            "fpl_decision_engine.transfer_decision",
        }
        for path in (REPOSITORY_ROOT / "src" / "fpl_decision_app").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported = imported_module_names(tree)
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
