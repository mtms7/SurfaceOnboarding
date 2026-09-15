from __future__ import annotations

from contextlib import redirect_stderr
from datetime import datetime, time, timedelta, timezone
from dataclasses import replace
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from threading import Barrier, Thread
import unittest
from uuid import uuid4

from integration.onboarding.adapters import (DisabledLeonardoAdapter, MockSalesforceAdapter,
                                             SourceReference, SyncScope, SyntheticSalesforceCatalog)
from integration.onboarding.mappings import MAPPING_REGISTRY, mapping_blocker
from integration.onboarding.models import EngineValue, ReasonCode, WorkflowItem, WorkflowState
from integration.onboarding.scope_policy import ScopeCounts, manual_review_reason
from integration.onboarding.state_machine import transition
from integration.onboarding.sync_jobs import SyncFailureCategory, SyncJob, SyncJobStatus
from integration.onboarding.repository import InMemoryWorkflowRepository
from integration.onboarding.sync_job_repository import InMemorySyncJobRepository
from integration.onboarding.authorization import (AuthorizationError, Role, authorize_manual_review,
                                                  authorize_sync_request)
from integration.onboarding.config import AppConfig, PollSchedule, TargetEnvironment
from integration.onboarding.deployment import HostDeploymentProfile, HostPlatform
from integration.onboarding.secrets import DisabledSecretProvider, SecretProvider, SecretProviderStatus
from integration.onboarding.queue_view import QueueRow, queue_rows
from integration.onboarding.service import LocalOnboardingService
from integration.onboarding.intake_decision import decide_intake
from integration.onboarding.audit import AuditEvent, AuditEventType
from integration.onboarding.audit_repository import InMemoryAuditRepository
from integration.onboarding.audit_view import AuditRow, audit_rows
from integration.onboarding.readiness import ApprovalGates, ReadinessGate, readiness_report
from integration.onboarding.synthetic_intake import (SyntheticIntakeCandidate, ingest_synthetic,
                                                     ingest_synthetic_batch)
from integration.onboarding.poll_planner import next_poll_times
from integration.onboarding.app import create_app
from integration.onboarding.origin_policy import LEONARDO_DEVELOPMENT_ORIGIN, require_development_origin
from integration.onboarding.execution_gate import ExecutionGateDecision, evaluate_execution_gate
from integration.onboarding.writeback import (DisabledSalesforceWriteback, SalesforceWritebackBlockedError,
                                               WritebackReceiptRequest)
from integration.onboarding.source_schema import parse_reference_document
from integration.onboarding.manual_review_repository import InMemoryManualReviewRepository
from integration.onboarding.rate_limit import InMemorySyncRateLimiter, SyncRateLimitPolicy
from integration.onboarding.manual_review import (ManualReviewDecision, can_release_manual_review,
                                                   record_manual_review)
from integration.onboarding.source_read import (SourceReadFailure, SourceReadResult,
                                                require_successful_source_read, source_read_blocker)


def stable_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
from integration.onboarding.cli import run as run_cli


def item(state: WorkflowState = WorkflowState.DISCOVERED) -> WorkflowItem:
    return WorkflowItem("a012345", "CO-0001", "2026-09-09T00:00:00Z", EngineValue.CASE_1,
                        "policy-v1", stable_hash("intent"), stable_hash("idempotency"), state, None,
                        datetime.now(timezone.utc))


def revised_item(*, revision: str, idempotency_key: str, state: WorkflowState = WorkflowState.DISCOVERED) -> WorkflowItem:
    return WorkflowItem("a012345", "CO-0001", revision, EngineValue.CASE_1,
                        "policy-v1", stable_hash("intent-" + revision), stable_hash(idempotency_key), state, None,
                        datetime.now(timezone.utc))


class MappingTests(unittest.TestCase):
    def test_all_six_routes_are_present_and_disabled(self):
        self.assertEqual(set(MAPPING_REGISTRY), set(EngineValue))
        self.assertTrue(all(not mapping.enabled for mapping in MAPPING_REGISTRY.values()))
        self.assertTrue(all(mapping.disabled_reason is ReasonCode.MAPPING_NOT_APPROVED for mapping in MAPPING_REGISTRY.values()))

    def test_all_current_routes_are_blocked_and_unknown_route_is_not_mapped(self):
        self.assertTrue(all(mapping_blocker(value) is ReasonCode.MAPPING_NOT_APPROVED for value in EngineValue))
        self.assertEqual(mapping_blocker("case_7"), ReasonCode.CASE_NOT_MAPPED_YET)

    def test_enabled_mapping_requires_an_approved_version_and_no_disabled_reason(self):
        original = MAPPING_REGISTRY[EngineValue.CASE_1]
        with self.assertRaisesRegex(ValueError, "approved_version"):
            replace(original, enabled=True, disabled_reason=None)
        with self.assertRaisesRegex(ValueError, "cannot_have_disabled_reason"):
            replace(original, version="approved-v1", enabled=True)


class ScopePolicyTests(unittest.TestCase):
    def test_threshold_boundaries_and_combined_count(self):
        self.assertIsNone(manual_review_reason(ScopeCounts(60, 0, 60, 60)))
        self.assertEqual(manual_review_reason(ScopeCounts(61, 0, 0, 0)), ReasonCode.MANUAL_REVIEW_REQUIRED)
        self.assertEqual(manual_review_reason(ScopeCounts(30, 31, 0, 0)), ReasonCode.MANUAL_REVIEW_REQUIRED)
        self.assertEqual(manual_review_reason(ScopeCounts(0, 0, 0, 61)), ReasonCode.MANUAL_REVIEW_REQUIRED)

    def test_negative_counts_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            ScopeCounts(-1, 0, 0, 0)


class IntakeDecisionTests(unittest.TestCase):
    def test_current_unapproved_mapping_blocks_before_scope_evaluation(self):
        decision = decide_intake(EngineValue.CASE_1, ScopeCounts(61, 0, 0, 0))
        self.assertEqual(decision.state, WorkflowState.BLOCKED)
        self.assertEqual(decision.reason_code, ReasonCode.MAPPING_NOT_APPROVED)

    def test_enabled_mapping_routes_large_scope_to_manual_review_then_normal_scope_to_pending(self):
        original = MAPPING_REGISTRY[EngineValue.CASE_1]
        MAPPING_REGISTRY[EngineValue.CASE_1] = replace(
            original, version="approved-test-v1", enabled=True, disabled_reason=None,
        )
        try:
            large_scope = decide_intake(EngineValue.CASE_1, ScopeCounts(61, 0, 0, 0))
            normal_scope = decide_intake(EngineValue.CASE_1, ScopeCounts(60, 0, 60, 60))
        finally:
            MAPPING_REGISTRY[EngineValue.CASE_1] = original
        self.assertEqual((large_scope.state, large_scope.reason_code),
                         (WorkflowState.MANUAL_REVIEW_REQUIRED, ReasonCode.MANUAL_REVIEW_REQUIRED))
        self.assertEqual((normal_scope.state, normal_scope.reason_code), (WorkflowState.PENDING, None))

    def test_invalid_counts_contract_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "requires_scope_counts"):
            decide_intake(EngineValue.CASE_1, "61")  # type: ignore[arg-type]


class SyntheticIntakeTests(unittest.TestCase):
    def test_current_mapping_blocks_synthetic_reference_and_same_revision_is_idempotent(self):
        candidate = SyntheticIntakeCandidate(
            SourceReference("a0123456789ABCD", "CO-0001", "2026-09-10T00:00:00Z"),
            EngineValue.CASE_1, "policy-v1", stable_hash("intent-one"), stable_hash("intake-key-one"),
            ScopeCounts(0, 0, 0, 0), datetime(2026, 9, 10, tzinfo=timezone.utc),
        )
        repository = InMemoryWorkflowRepository()
        first = ingest_synthetic(candidate, repository)
        again = ingest_synthetic(candidate, repository)
        self.assertTrue(first.created)
        self.assertEqual((first.item.state, first.item.reason_code),
                         (WorkflowState.BLOCKED, ReasonCode.MAPPING_NOT_APPROVED))
        self.assertFalse(again.created)
        self.assertFalse(again.changed)
        self.assertEqual(again.item, first.item)

    def test_new_source_revision_restarts_and_applies_the_current_gate(self):
        repository = InMemoryWorkflowRepository()
        initial = SyntheticIntakeCandidate(
            SourceReference("a0123456789ABCD", "CO-0001", "rev-one"), EngineValue.CASE_1,
            "policy-v1", stable_hash("intent-one"), stable_hash("intake-key-one"), ScopeCounts(0, 0, 0, 0),
            datetime(2026, 9, 10, tzinfo=timezone.utc),
        )
        revised = SyntheticIntakeCandidate(
            SourceReference("a0123456789ABCD", "CO-0001", "rev-two"), EngineValue.CASE_1,
            "policy-v1", stable_hash("intent-two"), stable_hash("intake-key-two"), ScopeCounts(0, 0, 0, 0),
            datetime(2026, 9, 10, 1, tzinfo=timezone.utc),
        )
        ingest_synthetic(initial, repository)
        result = ingest_synthetic(revised, repository)
        self.assertTrue(result.changed)
        self.assertEqual(result.item.source_revision, "rev-two")
        self.assertEqual(result.item.state, WorkflowState.BLOCKED)

    def test_same_source_revision_with_changed_metadata_fails_closed(self):
        repository = InMemoryWorkflowRepository()
        initial = SyntheticIntakeCandidate(
            SourceReference("a0123456789ABCD", "CO-0001", "rev-one"), EngineValue.CASE_1,
            "policy-v1", stable_hash("intent-one"), stable_hash("intake-key-one"), ScopeCounts(0, 0, 0, 0),
            datetime(2026, 9, 10, tzinfo=timezone.utc),
        )
        conflicting = SyntheticIntakeCandidate(
            SourceReference("a0123456789ABCD", "CO-0001", "rev-one"), EngineValue.CASE_1,
            "policy-v1", stable_hash("intent-changed"), stable_hash("intake-key-two"), ScopeCounts(0, 0, 0, 0),
            datetime(2026, 9, 10, 1, tzinfo=timezone.utc),
        )
        ingest_synthetic(initial, repository)
        with self.assertRaisesRegex(ValueError, "same_source_revision_metadata_conflict"):
            ingest_synthetic(conflicting, repository)

    def test_batch_requires_an_exact_successful_source_snapshot(self):
        first = SourceReference("a0123456789ABCD", "CO-0001", "rev-one")
        second = SourceReference("a0123456789ABCE", "CO-0002", "rev-two")
        candidates = (
            SyntheticIntakeCandidate(first, EngineValue.CASE_1, "policy-v1", stable_hash("intent-one"),
                                     stable_hash("intake-key-one"), ScopeCounts(0, 0, 0, 0),
                                     datetime(2026, 9, 10, tzinfo=timezone.utc)),
            SyntheticIntakeCandidate(second, EngineValue.CASE_2, "policy-v1", stable_hash("intent-two"),
                                     stable_hash("intake-key-two"), ScopeCounts(0, 0, 0, 0),
                                     datetime(2026, 9, 10, tzinfo=timezone.utc)),
        )
        repository = InMemoryWorkflowRepository()
        results = ingest_synthetic_batch(SourceReadResult(SyncScope("all"), (first, second)),
                                         candidates, repository)
        self.assertEqual(len(results), 2)
        with self.assertRaisesRegex(ValueError, "snapshot_mismatch"):
            ingest_synthetic_batch(SourceReadResult(SyncScope("all"), (first,)), candidates,
                                   InMemoryWorkflowRepository())
        with self.assertRaisesRegex(ValueError, "source_read_blocked"):
            ingest_synthetic_batch(SourceReadResult(SyncScope("all"), (), SourceReadFailure.TIMEOUT), (),
                                   InMemoryWorkflowRepository())


class AuditEventTests(unittest.TestCase):
    def test_metadata_only_audit_event_accepts_safe_correlations(self):
        event = AuditEvent(uuid4(), AuditEventType.SYNC_REQUESTED, "operator.one",
                           datetime.now(timezone.utc), workflow_record_id="a0123456789ABCD")
        self.assertEqual(event.event_type, AuditEventType.SYNC_REQUESTED)

    def test_audit_event_rejects_raw_text_and_missing_correlation(self):
        now = datetime.now(timezone.utc)
        with self.assertRaisesRegex(ValueError, "requires_correlation"):
            AuditEvent(uuid4(), AuditEventType.SYNC_REQUESTED, "operator", now)
        with self.assertRaisesRegex(ValueError, "invalid_audit_actor"):
            AuditEvent(uuid4(), AuditEventType.SYNC_REQUESTED, "operator comment: private", now, sync_job_id=uuid4())
        self.assertNotIn("payload", AuditEvent.__dataclass_fields__)
        self.assertNotIn("response", AuditEvent.__dataclass_fields__)
        self.assertNotIn("token", AuditEvent.__dataclass_fields__)


class AuditRepositoryTests(unittest.TestCase):
    def test_recorder_accepts_only_validated_events_and_returns_a_snapshot(self):
        repository = InMemoryAuditRepository()
        event = AuditEvent(uuid4(), AuditEventType.SYNC_REQUESTED, "operator.one",
                           datetime.now(timezone.utc), sync_job_id=uuid4())
        self.assertIs(repository.record(event), event)
        self.assertEqual(repository.list_events(), (event,))
        with self.assertRaisesRegex(ValueError, "requires_audit_event"):
            repository.record("unvalidated")  # type: ignore[arg-type]


class AuditViewTests(unittest.TestCase):
    def test_projection_excludes_all_correlation_ids_and_preserves_append_order(self):
        first = AuditEvent(uuid4(), AuditEventType.SYNC_REQUESTED, "operator.one",
                           datetime(2026, 9, 10, 8, tzinfo=timezone.utc), sync_job_id=uuid4())
        second = AuditEvent(uuid4(), AuditEventType.SYNC_COALESCED, "admin.one",
                            datetime(2026, 9, 10, 9, tzinfo=timezone.utc), sync_job_id=uuid4(),
                            reason_code=ReasonCode.MAPPING_NOT_APPROVED)
        rows = audit_rows((first, second))
        self.assertEqual([row.event_type for row in rows],
                         [AuditEventType.SYNC_REQUESTED, AuditEventType.SYNC_COALESCED])
        self.assertEqual(set(AuditRow.__dataclass_fields__),
                         {"event_type", "actor", "occurred_at", "reason_code"})
        self.assertNotIn("id", " ".join(AuditRow.__dataclass_fields__).lower())


class ReadinessTests(unittest.TestCase):
    def test_default_report_surfaces_all_unapproved_or_unconfigured_gates(self):
        config = AppConfig(TargetEnvironment.LEONARDO_DEVELOPMENT,
                           PollSchedule("UTC", (time(8), time(16))))
        report = readiness_report(config, approvals=ApprovalGates(), mappings=MAPPING_REGISTRY,
                                  secret_provider=DisabledSecretProvider())
        self.assertFalse(report.ready)
        self.assertEqual([item.gate for item in report.unmet_gates], [
            ReadinessGate.HOST_DEPLOYMENT_APPROVED,
            ReadinessGate.WEB_IDENTITY_APPROVED,
            ReadinessGate.PERSISTENCE_OPERATIONS_APPROVED,
            ReadinessGate.SALESFORCE_READ_IDENTITY_APPROVED,
            ReadinessGate.LEONARDO_AUTH_APPROVED,
            ReadinessGate.SECRETS_PROVIDER_READY,
            ReadinessGate.ALL_ROUTE_MAPPINGS_APPROVED,
        ])
        self.assertTrue(all(item.reason is not None for item in report.unmet_gates))


class MigrationDraftTests(unittest.TestCase):
    def test_schema_draft_has_metadata_constraints_and_no_sensitive_columns(self):
        migration = Path(__file__).parents[1] / "migrations" / "001_initial_metadata.sql"
        schema = migration.read_text(encoding="utf-8").lower()
        self.assertIn("create table workflow_items", schema)
        self.assertIn("create table sync_jobs", schema)
        self.assertIn("create table manual_review_decisions", schema)
        self.assertIn("create table audit_events", schema)
        self.assertIn("one_active_sync_job_per_scope", schema)
        self.assertIn("unique (workflow_record_id, source_revision, intent_hash)", schema)
        for forbidden in ("password", "cookie", "authorization_header", "raw_payload", "raw_response"):
            self.assertNotIn(forbidden, schema)


class CliTests(unittest.TestCase):
    def test_queue_and_sync_commands_use_only_local_safe_service_methods(self):
        workflows = InMemoryWorkflowRepository()
        workflows.upsert(item())
        service = LocalOnboardingService(workflows, InMemorySyncJobRepository())
        queue_output = StringIO()
        self.assertEqual(run_cli(["queue"], service=service, output=queue_output), 0)
        self.assertIn('"co_number":"CO-0001"', queue_output.getvalue())
        sync_output = StringIO()
        self.assertEqual(run_cli(["sync", "--actor", "local.operator", "--role", "operator", "--all"],
                                 service=service, output=sync_output), 0)
        self.assertIn('"status":"queued"', sync_output.getvalue())
        self.assertNotIn("salesforce", sync_output.getvalue().lower())

    def test_audit_command_exposes_only_the_safe_projection(self):
        service = LocalOnboardingService()
        service.request_sync(SyncScope("all"), requested_by="local.operator", role=Role.OPERATOR)
        audit_output = StringIO()
        self.assertEqual(run_cli(["audit"], service=service, output=audit_output), 0)
        rows = json.loads(audit_output.getvalue())
        self.assertEqual(set(rows[0]), {"event_type", "actor", "occurred_at", "reason_code"})
        self.assertNotIn("id", audit_output.getvalue().lower())
        self.assertNotIn("scope", audit_output.getvalue().lower())

    def test_readiness_command_reports_gaps_without_external_details(self):
        output = StringIO()
        self.assertEqual(run_cli(["readiness"], output=output), 0)
        report = json.loads(output.getvalue())
        self.assertFalse(report["ready"])
        self.assertTrue(any(row["gate"] == "leonardo_auth_approved" for row in report["items"]))
        self.assertNotIn("token", output.getvalue().lower())
        self.assertNotIn("password", output.getvalue().lower())

    def test_sync_cli_does_not_echo_rejected_request_details(self):
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaisesRegex(SystemExit, "2"):
            run_cli(["sync", "--actor", "viewer", "--role", "viewer", "--all"])
        self.assertIn("sync_request_rejected", stderr.getvalue())
        self.assertNotIn("not_authorized", stderr.getvalue())


class StateMachineTests(unittest.TestCase):
    def test_unapproved_mapping_cannot_become_ready_and_terminal_state_is_immutable(self):
        current = item()
        for target in (WorkflowState.VALIDATING, WorkflowState.PENDING, WorkflowState.VALIDATING):
            current = transition(current, target, source_revision=current.source_revision)
        with self.assertRaisesRegex(ValueError, "mapping_not_approved"):
            transition(current, WorkflowState.READY, source_revision=current.source_revision)
        current = transition(current, WorkflowState.BLOCKED, source_revision=current.source_revision,
                             reason_code=ReasonCode.MAPPING_NOT_APPROVED)
        with self.assertRaisesRegex(ValueError, "forbidden"):
            transition(current, WorkflowState.READY, source_revision=current.source_revision)

    def test_stale_revision_and_uncertain_result_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "stale"):
            transition(item(), WorkflowState.VALIDATING, source_revision="old")
        current = item(WorkflowState.READY)
        current = transition(current, WorkflowState.EXECUTING, source_revision=current.source_revision)
        result = transition(current, WorkflowState.UNCERTAIN_EXTERNAL_RESULT, source_revision=current.source_revision)
        self.assertEqual(result.reason_code, ReasonCode.UNCERTAIN_EXTERNAL_RESULT)
        with self.assertRaisesRegex(ValueError, "forbidden"):
            transition(result, WorkflowState.READY, source_revision=result.source_revision)

    def test_nonterminal_state_cannot_retain_a_blocker_reason(self):
        with self.assertRaisesRegex(ValueError, "nonterminal_transition_cannot_have_reason"):
            transition(item(), WorkflowState.VALIDATING, source_revision=item().source_revision,
                       reason_code=ReasonCode.MAPPING_NOT_APPROVED)


class AdapterTests(unittest.TestCase):
    def test_only_mock_sync_and_disabled_leonardo_are_available(self):
        result = MockSalesforceAdapter().request_sync(SyncScope("co_number", "CO-0001"), requested_by="operator")
        self.assertEqual(result.job_id, "mock-sync-co_number-CO-0001")
        self.assertEqual(DisabledLeonardoAdapter().health(), "authentication_blocked")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            SyncScope("soql", "SELECT Id FROM Account")
        with self.assertRaisesRegex(ValueError, "invalid_co"):
            SyncScope("co_number", "Customer name and raw comment")

    def test_synthetic_catalog_returns_only_fixed_scope_metadata(self):
        first = SourceReference("a0123456789ABCD", "CO-0001", "2026-09-10T00:00:00Z")
        second = SourceReference("a0123456789ABCE", "CO-0002", "2026-09-10T01:00:00Z")
        catalog = SyntheticSalesforceCatalog((first, second))
        self.assertEqual(catalog.read_references(SyncScope("all")), (first, second))
        self.assertEqual(catalog.read_references(SyncScope("co_number", "CO-0002")), (second,))
        self.assertEqual(catalog.read_references(SyncScope("record_id", first.salesforce_record_id)), (first,))
        self.assertEqual(set(SourceReference.__dataclass_fields__),
                         {"salesforce_record_id", "co_number", "source_revision"})
        self.assertNotIn("query", SyntheticSalesforceCatalog.__dict__)


class SyncJobContractTests(unittest.TestCase):
    def test_metadata_only_job_accepts_a_fixed_safe_scope(self):
        now = datetime.now(timezone.utc)
        job = SyncJob(uuid4(), SyncScope("co_number", "CO-0001"), "operator.one", now, SyncJobStatus.QUEUED)
        self.assertEqual(job.status, SyncJobStatus.QUEUED)
        self.assertIsNone(job.failure_category)

    def test_invalid_status_and_failure_combinations_fail_closed(self):
        now = datetime.now(timezone.utc)
        with self.assertRaisesRegex(ValueError, "invalid_status"):
            SyncJob(uuid4(), SyncScope("all"), "operator", now, "queued")  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "requires_failure"):
            SyncJob(uuid4(), SyncScope("all"), "operator", now, SyncJobStatus.FAILED,
                    started_at=now, completed_at=now)
        with self.assertRaisesRegex(ValueError, "cannot_have_failure"):
            SyncJob(uuid4(), SyncScope("all"), "operator", now, SyncJobStatus.QUEUED,
                    failure_category=SyncFailureCategory.TIMEOUT)

    def test_job_has_no_source_payload_field_or_free_text_requester(self):
        self.assertNotIn("source_payload", SyncJob.__dataclass_fields__)
        self.assertNotIn("raw_response", SyncJob.__dataclass_fields__)
        with self.assertRaisesRegex(ValueError, "invalid_requester"):
            SyncJob(uuid4(), SyncScope("all"), "operator comment: private data", datetime.now(timezone.utc), SyncJobStatus.QUEUED)


class SyncJobRepositoryTests(unittest.TestCase):
    def test_same_scope_coalesces_while_active(self):
        repository = InMemorySyncJobRepository()
        now = datetime.now(timezone.utc)
        first = repository.request(SyncScope("co_number", "CO-0001"), requested_by="operator", role=Role.OPERATOR, now=now)
        again = repository.request(SyncScope("co_number", "CO-0001"), requested_by="admin", role=Role.ADMIN, now=now)
        self.assertFalse(first.coalesced)
        self.assertTrue(again.coalesced)
        self.assertEqual(again.job.job_id, first.job.job_id)

    def test_terminal_job_releases_scope_for_a_new_request(self):
        repository = InMemorySyncJobRepository()
        first = repository.request(SyncScope("all"), requested_by="operator", role=Role.OPERATOR)
        running = repository.start(first.job.job_id)
        completed = repository.finish(running.job_id, succeeded=True)
        self.assertEqual(completed.status, SyncJobStatus.SUCCEEDED)
        second = repository.request(SyncScope("all"), requested_by="operator", role=Role.OPERATOR)
        self.assertFalse(second.coalesced)
        self.assertNotEqual(second.job.job_id, first.job.job_id)

    def test_invalid_lifecycle_and_failure_category_are_rejected(self):
        repository = InMemorySyncJobRepository()
        job = repository.request(SyncScope("all"), requested_by="operator", role=Role.OPERATOR).job
        with self.assertRaisesRegex(ValueError, "must_be_running"):
            repository.finish(job.job_id, succeeded=True)
        running = repository.start(job.job_id)
        with self.assertRaisesRegex(ValueError, "requires_failure"):
            repository.finish(running.job_id, succeeded=False)


class AuthorizationTests(unittest.TestCase):
    def test_operator_and_admin_can_request_sync(self):
        authorize_sync_request(Role.OPERATOR)
        authorize_sync_request(Role.ADMIN)

    def test_viewer_reviewer_and_untyped_role_are_rejected(self):
        for role in (Role.VIEWER, Role.REVIEWER, "admin"):
            with self.assertRaisesRegex(AuthorizationError, "not_authorized"):
                authorize_sync_request(role)  # type: ignore[arg-type]

    def test_repository_enforces_the_role_policy(self):
        repository = InMemorySyncJobRepository()
        with self.assertRaisesRegex(AuthorizationError, "not_authorized"):
            repository.request(SyncScope("all"), requested_by="viewer", role=Role.VIEWER)

    def test_only_reviewer_can_record_a_manual_review_decision(self):
        authorize_manual_review(Role.REVIEWER)
        for role in (Role.VIEWER, Role.OPERATOR, Role.ADMIN, "reviewer"):
            with self.assertRaisesRegex(AuthorizationError, "manual_review_not_authorized"):
                authorize_manual_review(role)  # type: ignore[arg-type]


class ManualReviewTests(unittest.TestCase):
    def test_approval_requires_exact_snapshot_and_an_enabled_mapping(self):
        candidate = WorkflowItem("a0123456789ABCD", "CO-0001", "rev-one", EngineValue.CASE_1,
                                 "policy-v1", stable_hash("intent-one"), stable_hash("key-one"),
                                 WorkflowState.MANUAL_REVIEW_REQUIRED,
                                 ReasonCode.MANUAL_REVIEW_REQUIRED, datetime.now(timezone.utc))
        decision = ManualReviewDecision(candidate.salesforce_record_id, candidate.source_revision,
                                        candidate.intent_hash, "reviewer.one", True,
                                        datetime.now(timezone.utc))
        self.assertIs(record_manual_review(role=Role.REVIEWER, decision=decision), decision)
        self.assertFalse(can_release_manual_review(decision, candidate))
        original = MAPPING_REGISTRY[EngineValue.CASE_1]
        MAPPING_REGISTRY[EngineValue.CASE_1] = replace(
            original, version="approved-test-v1", enabled=True, disabled_reason=None,
        )
        try:
            self.assertTrue(can_release_manual_review(decision, candidate))
            self.assertFalse(can_release_manual_review(
                replace(decision, source_revision="different"), candidate))
        finally:
            MAPPING_REGISTRY[EngineValue.CASE_1] = original

    def test_repository_allows_only_one_immutable_decision_per_snapshot(self):
        decision = ManualReviewDecision("a0123456789ABCD", "rev-one", stable_hash("intent"),
                                        "reviewer.one", True, datetime(2026, 9, 10, tzinfo=timezone.utc))
        repository = InMemoryManualReviewRepository()
        self.assertIs(repository.save(decision), decision)
        self.assertIs(repository.save(decision), decision)
        self.assertFalse(repository.save_with_status(decision).created)
        self.assertEqual(repository.get(workflow_record_id=decision.workflow_record_id,
                                        source_revision=decision.source_revision,
                                        intent_hash=decision.intent_hash), decision)
        with self.assertRaisesRegex(ValueError, "already_recorded"):
            repository.save(replace(decision, approved=False))


class ConfigurationTests(unittest.TestCase):
    def test_development_only_non_secret_config_is_valid(self):
        config = AppConfig(TargetEnvironment.LEONARDO_DEVELOPMENT,
                           PollSchedule("America/Los_Angeles", (time(8), time(16))))
        self.assertFalse(config.web_enabled)

    def test_production_web_listener_and_bad_schedules_are_rejected(self):
        schedule = PollSchedule("UTC", (time(8), time(16)))
        with self.assertRaisesRegex(ValueError, "production"):
            AppConfig("production", schedule)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "identity_approval"):
            AppConfig(TargetEnvironment.LEONARDO_DEVELOPMENT, schedule, web_enabled=True)
        with self.assertRaisesRegex(ValueError, "invalid_poll_timezone"):
            PollSchedule("not/a-timezone", (time(8), time(16)))
        with self.assertRaisesRegex(ValueError, "must_differ"):
            PollSchedule("UTC", (time(8), time(8)))

    def test_config_and_secret_boundary_have_no_secret_retrieval(self):
        self.assertNotIn("password", AppConfig.__dataclass_fields__)
        self.assertNotIn("token", AppConfig.__dataclass_fields__)
        self.assertNotIn("get_secret", SecretProvider.__dict__)
        self.assertEqual(DisabledSecretProvider().health(), SecretProviderStatus.NOT_CONFIGURED)

    def test_web_factory_is_blocked_before_fastapi_or_listener_initialization(self):
        config = AppConfig(TargetEnvironment.LEONARDO_DEVELOPMENT,
                           PollSchedule("UTC", (time(8), time(16))))
        with self.assertRaisesRegex(RuntimeError, "identity_approval"):
            create_app(config)


class DeploymentPreparationTests(unittest.TestCase):
    def test_host_profile_is_pinned_to_approved_ubuntu_host_and_loopback(self):
        profile = HostDeploymentProfile("workato-opa-01", HostPlatform.UBUNTU_2204)
        self.assertEqual((profile.web_bind_host, profile.web_bind_port), ("127.0.0.1", 8000))
        with self.assertRaisesRegex(ValueError, "unapproved_deployment_host"):
            HostDeploymentProfile("another-host", HostPlatform.UBUNTU_2204)
        with self.assertRaisesRegex(ValueError, "web_listener_must_bind_loopback"):
            HostDeploymentProfile("workato-opa-01", HostPlatform.UBUNTU_2204, "0.0.0.0")

    def test_deployment_templates_are_guarded_and_unscheduled(self):
        root = Path(__file__).parents[1] / "deployment"
        web = (root / "systemd" / "surface-onboarding-web.service.template").read_text(encoding="utf-8")
        poll = (root / "systemd" / "surface-onboarding-poll.service.template").read_text(encoding="utf-8")
        timer = (root / "systemd" / "surface-onboarding-poll.timer.template").read_text(encoding="utf-8")
        baseline = (root / "SECURITY_BASELINE.md").read_text(encoding="utf-8")
        threat_model = (root / "THREAT_MODEL.md").read_text(encoding="utf-8")
        self.assertIn("ConditionPathExists=/etc/surface-onboarding/approved-web-identity", web)
        self.assertIn("--host 127.0.0.1", web)
        self.assertIn("ConditionPathExists=/etc/surface-onboarding/approved-salesforce-read-identity", poll)
        for template in (web, poll):
            self.assertIn("NoNewPrivileges=yes", template)
            self.assertIn("CapabilityBoundingSet=", template)
            self.assertIn("ProtectSystem=strict", template)
            self.assertIn("SystemCallArchitectures=native", template)
            self.assertIn("RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6", template)
            self.assertIn("RuntimeDirectoryMode=0750", template)
        self.assertIn("corporate SSO/MFA", baseline)
        self.assertIn("must not reuse the Workato OPA", baseline)
        self.assertIn("direct Internet ingress", baseline)
        self.assertIn("RND VPN connectivity is a network boundary", threat_model)
        self.assertIn("Forged proxy/forwarded identity headers", threat_model)
        self.assertIn("Supply-chain or archive tampering", threat_model)
        self.assertNotIn("OnCalendar=", timer)
        self.assertIn("No OnCalendar value is permitted", timer)


class OriginPolicyTests(unittest.TestCase):
    def test_exact_development_origin_is_the_only_allowed_origin(self):
        self.assertEqual(require_development_origin(LEONARDO_DEVELOPMENT_ORIGIN),
                         LEONARDO_DEVELOPMENT_ORIGIN)
        for origin in (
            "https://app.pentera.io",
            "http://leonardo.dev.app.pentera.io",
            "https://leonardo.dev.app.pentera.io:443",
            "https://leonardo.dev.app.pentera.io/login",
            "https://leonardo.dev.app.pentera.io.evil.example",
            "https://leonardo.dev.app.pentera.io?next=anything",
        ):
            with self.subTest(origin=origin), self.assertRaisesRegex(ValueError, "not_approved"):
                require_development_origin(origin)


class ExecutionGateTests(unittest.TestCase):
    def test_gate_requires_ready_state_mapping_origin_and_auth_approval(self):
        ready = WorkflowItem("a012345", "CO-0001", "rev-one", EngineValue.CASE_1,
                             "policy-v1", stable_hash("intent"), stable_hash("idempotency"),
                             WorkflowState.READY, None, datetime.now(timezone.utc))
        self.assertEqual(evaluate_execution_gate(ready, approvals=ApprovalGates(),
                                                 origin=LEONARDO_DEVELOPMENT_ORIGIN),
                         ExecutionGateDecision(False, ReasonCode.MAPPING_NOT_APPROVED))
        original = MAPPING_REGISTRY[EngineValue.CASE_1]
        MAPPING_REGISTRY[EngineValue.CASE_1] = replace(
            original, version="approved-test-v1", enabled=True, disabled_reason=None,
        )
        try:
            self.assertEqual(evaluate_execution_gate(ready, approvals=ApprovalGates(),
                                                     origin=LEONARDO_DEVELOPMENT_ORIGIN),
                             ExecutionGateDecision(False, ReasonCode.AUTHENTICATION_BLOCKED))
            self.assertEqual(evaluate_execution_gate(
                ready, approvals=ApprovalGates(leonardo_auth_approved=True),
                origin="https://app.pentera.io"),
                ExecutionGateDecision(False, ReasonCode.VERIFICATION_FAILED))
            self.assertEqual(evaluate_execution_gate(
                ready, approvals=ApprovalGates(leonardo_auth_approved=True),
                origin=LEONARDO_DEVELOPMENT_ORIGIN), ExecutionGateDecision(True))
        finally:
            MAPPING_REGISTRY[EngineValue.CASE_1] = original


class WritebackBoundaryTests(unittest.TestCase):
    def test_writeback_stub_rejects_every_validated_request(self):
        request = WritebackReceiptRequest("a0123456789ABCD", "rev-one", stable_hash("verified"))
        with self.assertRaisesRegex(SalesforceWritebackBlockedError, "not_approved"):
            DisabledSalesforceWriteback().request(request)
        self.assertEqual(set(WritebackReceiptRequest.__dataclass_fields__),
                         {"salesforce_record_id", "source_revision", "verification_hash"})
        self.assertNotIn("uuid", WritebackReceiptRequest.__dataclass_fields__)
        self.assertNotIn("field_value", WritebackReceiptRequest.__dataclass_fields__)


class PollPlannerTests(unittest.TestCase):
    def test_returns_the_next_configured_local_times_without_scheduling_work(self):
        schedule = PollSchedule("America/Los_Angeles", (time(8), time(16)))
        after = datetime(2026, 9, 10, 15, tzinfo=timezone.utc)
        next_runs = next_poll_times(schedule, after=after, count=3)
        self.assertEqual([run.isoformat() for run in next_runs], [
            "2026-09-10T16:00:00-07:00",
            "2026-09-11T08:00:00-07:00",
            "2026-09-11T16:00:00-07:00",
        ])

    def test_rejects_naive_time_and_nonpositive_count(self):
        schedule = PollSchedule("UTC", (time(8), time(16)))
        with self.assertRaisesRegex(ValueError, "timezone_aware"):
            next_poll_times(schedule, after=datetime(2026, 9, 10, 8))
        with self.assertRaisesRegex(ValueError, "positive"):
            next_poll_times(schedule, after=datetime.now(timezone.utc), count=0)


class RateLimitTests(unittest.TestCase):
    def test_limiter_enforces_a_configured_window_without_executing_work(self):
        limiter = InMemorySyncRateLimiter(SyncRateLimitPolicy(2, timedelta(minutes=10)))
        now = datetime(2026, 9, 10, tzinfo=timezone.utc)
        self.assertTrue(limiter.check("operator.one", now=now).allowed)
        self.assertTrue(limiter.check("operator.one", now=now + timedelta(minutes=1)).allowed)
        limited = limiter.check("operator.one", now=now + timedelta(minutes=2))
        self.assertFalse(limited.allowed)
        self.assertEqual(limited.retry_after_seconds, 480)
        self.assertTrue(limiter.check("operator.one", now=now + timedelta(minutes=10)).allowed)

    def test_limiter_rejects_invalid_policy_actor_and_time(self):
        with self.assertRaisesRegex(ValueError, "max_requests"):
            SyncRateLimitPolicy(0, timedelta(minutes=1))
        limiter = InMemorySyncRateLimiter(SyncRateLimitPolicy(1, timedelta(minutes=1)))
        with self.assertRaisesRegex(ValueError, "invalid_rate_limit_actor"):
            limiter.check("operator comment: private", now=datetime.now(timezone.utc))
        with self.assertRaisesRegex(ValueError, "timezone_aware"):
            limiter.check("operator", now=datetime(2026, 9, 10))


class SourceReadTests(unittest.TestCase):
    def test_success_returns_only_prevalidated_references(self):
        reference = SourceReference("a0123456789ABCD", "CO-0001", "rev-one")
        result = SourceReadResult(SyncScope("all"), (reference,))
        self.assertIsNone(source_read_blocker(result))
        self.assertEqual(require_successful_source_read(result), (reference,))
        self.assertEqual(set(SourceReadResult.__dataclass_fields__), {"scope", "references", "failure"})
        self.assertNotIn("raw_output", SourceReadResult.__dataclass_fields__)

    def test_masked_failures_block_without_returning_references(self):
        expected = {
            SourceReadFailure.AUTHENTICATION: ReasonCode.AUTHENTICATION_BLOCKED,
            SourceReadFailure.TIMEOUT: ReasonCode.RETRYABLE_READ_FAILURE,
            SourceReadFailure.TRANSPORT: ReasonCode.RETRYABLE_READ_FAILURE,
            SourceReadFailure.INVALID_JSON: ReasonCode.SOURCE_DATA_MISMATCH,
            SourceReadFailure.SCHEMA_DRIFT: ReasonCode.SOURCE_DATA_MISMATCH,
            SourceReadFailure.AMBIGUOUS_RESULT: ReasonCode.SOURCE_DATA_MISMATCH,
        }
        for failure, reason in expected.items():
            with self.subTest(failure=failure):
                result = SourceReadResult(SyncScope("all"), (), failure)
                self.assertEqual(source_read_blocker(result), reason)
                with self.assertRaisesRegex(ValueError, reason):
                    require_successful_source_read(result)
        reference = SourceReference("a0123456789ABCD", "CO-0001", "rev-one")
        with self.assertRaisesRegex(ValueError, "cannot_return_references"):
            SourceReadResult(SyncScope("all"), (reference,), SourceReadFailure.SCHEMA_DRIFT)

    def test_targeted_result_requires_one_matching_reference(self):
        first = SourceReference("a0123456789ABCD", "CO-0001", "rev-one")
        second = SourceReference("a0123456789ABCE", "CO-0002", "rev-two")
        with self.assertRaisesRegex(ValueError, "unambiguous"):
            SourceReadResult(SyncScope("record_id", first.salesforce_record_id), (first, second))
        with self.assertRaisesRegex(ValueError, "record_scope_mismatch"):
            SourceReadResult(SyncScope("record_id", first.salesforce_record_id), (second,))
        with self.assertRaisesRegex(ValueError, "co_scope_mismatch"):
            SourceReadResult(SyncScope("co_number", first.co_number), (second,))

    def test_schema_parser_reduces_input_to_metadata_or_masked_failure(self):
        valid = ('[{"salesforce_record_id":"a0123456789ABCD","co_number":"CO-0001",'
                 '"source_revision":"rev-one"}]')
        result = parse_reference_document(SyncScope("all"), valid)
        self.assertEqual(result.references,
                         (SourceReference("a0123456789ABCD", "CO-0001", "rev-one"),))
        self.assertIsNone(result.failure)
        self.assertEqual(parse_reference_document(SyncScope("all"), "not-json").failure,
                         SourceReadFailure.INVALID_JSON)
        self.assertEqual(parse_reference_document(
            SyncScope("all"), '[{"salesforce_record_id":"a0123456789ABCD"}]'
        ).failure, SourceReadFailure.SCHEMA_DRIFT)
        self.assertNotIn("document", SourceReadResult.__dataclass_fields__)


class QueueViewTests(unittest.TestCase):
    def test_projection_returns_only_safe_metadata_in_newest_first_order(self):
        repository = InMemoryWorkflowRepository()
        older = WorkflowItem("a012345", "CO-0001", "2026-09-09T00:00:00Z", EngineValue.CASE_1,
                             "policy-v1", stable_hash("intent-one"), stable_hash("idempotency-one"), WorkflowState.PENDING,
                             ReasonCode.MAPPING_NOT_APPROVED, datetime(2026, 9, 10, 8, tzinfo=timezone.utc))
        newer = WorkflowItem("a098765", "CO-0002", "2026-09-10T00:00:00Z", EngineValue.CASE_2,
                             "policy-v1", stable_hash("intent-two"), stable_hash("idempotency-two"), WorkflowState.BLOCKED,
                             ReasonCode.SOURCE_DATA_MISMATCH, datetime(2026, 9, 10, 9, tzinfo=timezone.utc))
        repository.upsert(older)
        repository.upsert(newer)
        rows = queue_rows(repository)
        self.assertEqual([row.co_number for row in rows], ["CO-0002", "CO-0001"])
        self.assertEqual(set(QueueRow.__dataclass_fields__), {"co_number", "engine_value", "state", "reason_code", "updated_at"})
        self.assertNotIn("salesforce_record_id", QueueRow.__dataclass_fields__)
        self.assertNotIn("intent_hash", QueueRow.__dataclass_fields__)


class LocalServiceTests(unittest.TestCase):
    def test_service_composes_queue_and_coalesced_sync_request_without_execution(self):
        workflows = InMemoryWorkflowRepository()
        workflows.upsert(item())
        service = LocalOnboardingService(workflows, InMemorySyncJobRepository())
        self.assertEqual([row.co_number for row in service.queue()], ["CO-0001"])
        first = service.request_sync(SyncScope("all"), requested_by="operator", role=Role.OPERATOR)
        again = service.request_sync(SyncScope("all"), requested_by="admin", role=Role.ADMIN)
        self.assertFalse(first.coalesced)
        self.assertTrue(again.coalesced)
        self.assertEqual(again.job.job_id, first.job.job_id)
        events = service.audit_events()
        self.assertEqual([event.event_type for event in events],
                         [AuditEventType.SYNC_REQUESTED, AuditEventType.SYNC_COALESCED])
        self.assertEqual([event.actor_id for event in events], ["operator", "admin"])
        self.assertTrue(all(event.sync_job_id == first.job.job_id for event in events))
        self.assertNotIn("start", LocalOnboardingService.__dict__)
        self.assertNotIn("execute", LocalOnboardingService.__dict__)

    def test_synthetic_intake_audits_only_created_or_revised_workflows(self):
        service = LocalOnboardingService()
        initial = SyntheticIntakeCandidate(
            SourceReference("a0123456789ABCD", "CO-0001", "rev-one"), EngineValue.CASE_1,
            "policy-v1", stable_hash("intent-one"), stable_hash("intake-key-one"), ScopeCounts(0, 0, 0, 0),
            datetime(2026, 9, 10, tzinfo=timezone.utc),
        )
        revised = SyntheticIntakeCandidate(
            SourceReference("a0123456789ABCD", "CO-0001", "rev-two"), EngineValue.CASE_1,
            "policy-v1", stable_hash("intent-two"), stable_hash("intake-key-two"), ScopeCounts(0, 0, 0, 0),
            datetime(2026, 9, 10, 1, tzinfo=timezone.utc),
        )
        service.ingest_synthetic(initial)
        service.ingest_synthetic(initial)
        service.ingest_synthetic(revised)
        self.assertEqual([event.event_type for event in service.audit_events()], [
            AuditEventType.WORKFLOW_CREATED,
            AuditEventType.WORKFLOW_TRANSITIONED,
        ])
        self.assertEqual([event.actor_id for event in service.audit_events()],
                         ["local.synthetic", "local.synthetic"])

    def test_configured_rate_limit_blocks_before_job_or_audit_creation(self):
        limiter = InMemorySyncRateLimiter(SyncRateLimitPolicy(1, timedelta(hours=1)))
        service = LocalOnboardingService(sync_rate_limiter=limiter)
        service.request_sync(SyncScope("all"), requested_by="operator", role=Role.OPERATOR)
        with self.assertRaisesRegex(PermissionError, "rate_limited"):
            service.request_sync(SyncScope("co_number", "CO-0001"), requested_by="operator",
                                 role=Role.OPERATOR)
        self.assertEqual([event.event_type for event in service.audit_events()],
                         [AuditEventType.SYNC_REQUESTED])

    def test_synthetic_batch_uses_the_same_local_audit_boundary(self):
        service = LocalOnboardingService()
        source = SourceReference("a0123456789ABCD", "CO-0001", "rev-one")
        candidate = SyntheticIntakeCandidate(
            source, EngineValue.CASE_1, "policy-v1", stable_hash("intent-one"), stable_hash("intake-key-one"),
            ScopeCounts(0, 0, 0, 0), datetime(2026, 9, 10, tzinfo=timezone.utc),
        )
        results = service.ingest_synthetic_batch(SourceReadResult(SyncScope("all"), (source,)),
                                                 (candidate,))
        self.assertEqual(len(results), 1)
        self.assertEqual([event.event_type for event in service.audit_events()],
                         [AuditEventType.WORKFLOW_CREATED])

    def test_service_records_reviewer_decision_without_releasing_workflow(self):
        service = LocalOnboardingService()
        decision = ManualReviewDecision("a0123456789ABCD", "rev-one", stable_hash("intent"),
                                        "reviewer.one", True, datetime(2026, 9, 10, tzinfo=timezone.utc))
        self.assertEqual(service.record_manual_review(decision, role=Role.REVIEWER), decision)
        self.assertEqual(service.record_manual_review(decision, role=Role.REVIEWER), decision)
        self.assertEqual([event.event_type for event in service.audit_events()],
                         [AuditEventType.MANUAL_REVIEW_RECORDED])
        self.assertNotIn("execute", LocalOnboardingService.__dict__)


class InMemoryRepositoryTests(unittest.TestCase):
    def test_workflow_rejects_malformed_hashes_before_repository_use(self):
        with self.assertRaisesRegex(ValueError, "invalid_intent_hash"):
            WorkflowItem("a012345", "CO-0001", "rev-one", EngineValue.CASE_1,
                         "policy-v1", "not-a-hash", stable_hash("idempotency"),
                         WorkflowState.DISCOVERED, None, datetime.now(timezone.utc))
        with self.assertRaisesRegex(ValueError, "invalid_idempotency_key"):
            WorkflowItem("a012345", "CO-0001", "rev-one", EngineValue.CASE_1,
                         "policy-v1", stable_hash("intent"), "not-a-hash",
                         WorkflowState.DISCOVERED, None, datetime.now(timezone.utc))

    def test_insert_is_idempotent_for_an_identical_item(self):
        repository = InMemoryWorkflowRepository()
        candidate = item()
        self.assertTrue(repository.upsert(candidate).created)
        result = repository.upsert(candidate, expected_source_revision=candidate.source_revision)
        self.assertFalse(result.created)
        self.assertFalse(result.changed)
        self.assertEqual(repository.get(candidate.salesforce_record_id), candidate)

    def test_stale_revision_and_duplicate_idempotency_key_are_rejected(self):
        repository = InMemoryWorkflowRepository()
        existing = item()
        repository.upsert(existing)
        with self.assertRaisesRegex(ValueError, "stale_source_revision"):
            repository.upsert(revised_item(revision="2026-09-10T00:00:00Z", idempotency_key="new-key"), expected_source_revision="old")
        other = WorkflowItem("a098765", "CO-0002", "2026-09-09T00:00:00Z", EngineValue.CASE_2,
                             "policy-v1", stable_hash("intent-two"), existing.idempotency_key, WorkflowState.DISCOVERED, None, datetime.now(timezone.utc))
        with self.assertRaisesRegex(ValueError, "idempotency_key_already_bound"):
            repository.upsert(other)

    def test_new_source_revision_restarts_discovered_and_save_is_compare_and_swap(self):
        repository = InMemoryWorkflowRepository()
        initial = item()
        repository.upsert(initial)
        updated = revised_item(revision="2026-09-10T00:00:00Z", idempotency_key="new-key")
        self.assertTrue(repository.upsert(updated, expected_source_revision=initial.source_revision).changed)
        validating = transition(updated, WorkflowState.VALIDATING, source_revision=updated.source_revision)
        self.assertEqual(repository.save(validating, expected_source_revision=updated.source_revision,
                                         expected_state=WorkflowState.DISCOVERED), validating)
        with self.assertRaisesRegex(ValueError, "stale_workflow_state"):
            repository.save(validating, expected_source_revision=updated.source_revision,
                            expected_state=WorkflowState.DISCOVERED)
        non_discovered = revised_item(revision="2026-09-11T00:00:00Z", idempotency_key="another-key", state=WorkflowState.READY)
        with self.assertRaisesRegex(ValueError, "restart_discovered"):
            repository.upsert(non_discovered, expected_source_revision=updated.source_revision)

    def test_concurrent_identical_upserts_create_only_one_item(self):
        repository = InMemoryWorkflowRepository()
        candidate = item()
        gate = Barrier(4)
        results = []

        def submit() -> None:
            gate.wait()
            results.append(repository.upsert(candidate))

        workers = [Thread(target=submit) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

        self.assertEqual(sum(result.created for result in results), 1)
        self.assertEqual(len(results), 4)
