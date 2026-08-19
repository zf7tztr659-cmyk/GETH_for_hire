# Geth MVP traceability

Status: implemented and verified coverage. Paths and test node IDs below identify primary proofs; several requirements also have defense-in-depth coverage elsewhere. `tests/test_traceability.py` parses the normative requirement IDs and requires a matching automated `@pytest.mark.requirement("...")` marker with no unknown IDs.

| Directive principle | Requirement | Implemented module | Primary automated proof |
|---|---|---|---|
| CONST-01 owner authority | GOV-001 | `policy/authority.py`, `application/approvals.py` | `tests/component/test_denial_audit.py::test_forged_approval_decision_is_denied_and_audited` |
| CONST-02 bounded delegation | GOV-002 | `policy/delegations.py` | `tests/unit/test_delegations.py::test_delegation_must_be_strict_subset` |
| CONST-03 central accountability | GOV-003 | `application/orchestrator.py`, `domain/transitions.py` | `tests/unit/test_reference_monitor.py::test_provider_boundary_cannot_carry_trusted_state_mutations` |
| CONST-15/16 reviewed, visible change | GOV-004 | `application/memory.py`, `policy/engine.py` | `tests/component/test_improvements.py::test_change_proposal_never_activates_runtime_version` |
| CONST-03/18 state and stop authority | FUN-001 | `domain/transitions.py` | `tests/unit/test_transitions.py::test_complete_legal_and_illegal_transition_matrix` |
| CONST-05 multiple bounded minds | FUN-002 | `domain/enums.py`, `application/orchestrator.py` | `tests/e2e/test_orchestration.py::test_approve_creates_one_exact_file_then_readback_verifier_completes` |
| CONST-05 typed exchange | FUN-003 | `domain/messages.py` | `tests/unit/test_messages.py::test_immutable_versioned_message_round_trip` |
| CONST-06/08 independent deliberation | FUN-004 | `application/consensus.py` | `tests/e2e/test_consensus.py::test_proposals_are_independent_before_critique` |
| CONST-08/09 advisory consensus | FUN-005 | `application/consensus.py`, `policy/engine.py` | `tests/e2e/test_consensus.py::test_pass_revise_escalate_dissent_and_veto` |
| CONST-09 no manufactured unanimity | FUN-006 | `application/consensus.py`, `domain/models.py` | `tests/unit/test_consensus_limits.py::test_material_disagreement_escalates_at_round_limit` |
| CONST-06 provenance and uncertainty | FUN-007 | `domain/models.py` | `tests/unit/test_evidence.py::test_evidence_requires_provenance_and_uncertainty` |
| CONST-07 bottom-up verification | FUN-008 | `application/orchestrator.py` | `tests/e2e/test_orchestration.py::test_approve_creates_one_exact_file_then_readback_verifier_completes` |
| CONST-08/10/19 concise accountable result | FUN-009 | `cli.py`, `observability/audit.py` | `tests/cli/test_output.py::test_answer_first_output_retains_dissent_and_audit_link` |
| CONST-03/04 deterministic boundary | SAF-001 | `policy/engine.py`, `tools/broker.py` | `tests/component/test_denial_audit.py::test_unknown_tool_invocation_is_denied_and_audited` |
| CONST-03 explicit policy | SAF-002 | `domain/enums.py`, `policy/engine.py` | `tests/unit/test_policy.py::test_every_risk_class_has_explicit_decision` |
| CONST-06 bounded evidence | SAF-003 | `tools/filesystem.py`, `policy/engine.py` | `tests/component/test_read_tool.py::test_bounded_read_returns_redacted_utf8_and_digest` |
| CONST-01/14 exact owner scope | SAF-004 | `policy/actions.py`, `application/approvals.py` | `tests/unit/test_policy.py::test_exact_write_requires_approval_and_every_change_changes_digest` |
| CONST-01/18 non-replayable authority | SAF-005 | `application/approvals.py`, `persistence/repositories.py` | `tests/component/test_approvals.py::test_exact_approval_is_one_time_and_bound` |
| CONST-03/14 unsupported stays denied | SAF-006 | `policy/engine.py`, `tools/broker.py` | `tests/unit/test_policy.py::test_unsupported_classes_cannot_be_approved` |
| CONST-04 adapters subordinate | SAF-007 | `tools/protocol.py`, `tools/broker.py` | `tests/unit/test_tool_registry.py::test_registry_rejects_incomplete_tools_and_exposes_only_closed_mvp_set` |
| CONST-14 no scope escape | SAF-008 | `tools/paths.py` | `tests/component/test_path_containment.py::test_ambiguous_or_escaping_paths_are_rejected` |
| CONST-14 exact authorized effect | SAF-009 | `tools/filesystem.py` | `tests/component/test_write_tool.py::test_write_creates_exactly_one_exact_file_and_never_overwrites` |
| CONST-04 untrusted adapters/content | SAF-010 | `policy/engine.py`, `application/orchestrator.py` | `tests/e2e/test_orchestration.py::test_prompt_injection_shaped_objective_cannot_bypass_approval` |
| CONST-18 stop precedence | SAF-011 | `application/orchestrator.py`, `application/recovery.py` | `tests/e2e/test_orchestration.py::test_emergency_stop_between_policy_and_precommit_prevents_commit` |
| CONST-19 durable accountability | AUD-001 | `persistence/event_store.py`, `persistence/projections.py` | `tests/component/test_event_store.py::test_projection_rebuild_uses_canonical_journal` |
| CONST-19 inspectable integrity | AUD-002 | `persistence/canonical_json.py`, `persistence/integrity.py` | `tests/component/test_event_store.py::test_verification_detects_payload_mutation` |
| CONST-08/10/19 complete history | AUD-003 | `persistence/event_store.py`, `observability/audit.py` | `tests/cli/test_audit.py::test_audit_show_is_ordered_minimized_and_optionally_verbose` |
| CONST-06/19 truthful limits | AUD-004 | `observability/audit.py`, `cli.py` | `tests/cli/test_audit.py::test_verify_reports_unanchored_chain_limitations` |
| CONST-17/18 durable bounded work | OPR-001 | `application/recovery.py`, `persistence/projections.py` | `tests/cli/test_commands.py::test_full_console_command_and_error_matrix` |
| CONST-14/19 no duplicate uncertain effect | OPR-002 | `application/recovery.py`, `tools/protocol.py` | `tests/component/test_runtime_recovery_service.py::test_startup_never_retries_a_missing_or_mismatched_effect` |
| CONST-11 private by default | PRV-001 | `policy/redaction.py`, `observability/logging.py` | `tests/unit/test_policy.py::test_recursive_secret_redaction_and_sensitive_paths` |
| CONST-06/11 data minimization | PRV-002 | `tools/filesystem.py`, `policy/redaction.py` | `tests/component/test_read_tool.py::test_sensitive_paths_and_excess_bytes_are_denied` |
| CONST-15 provenance-aware memory | MEM-001 | `application/memory.py`, `domain/models.py` | `tests/component/test_memory.py::test_only_explicit_allowed_provenance_linked_memory_is_retained` |
| CONST-15 owner-controlled memory | MEM-002 | `application/memory.py`, `persistence/projections.py` | `tests/component/test_memory.py::test_search_export_correct_and_owner_authorization` |
| CONST-11/15 deletable memory | MEM-003 | `application/memory.py`, `persistence/event_store.py` | `tests/component/test_memory.py::test_forget_removes_content_and_leaves_content_free_tombstone` |
| CONST-15/16 reviewed improvement | MEM-004 | `application/memory.py` | `tests/component/test_improvements.py::test_candidate_does_not_change_active_versions` |
| CONST-06/09/17 bounded breadth/work | OPS-001 | `domain/models.py`, `application/orchestrator.py` | `tests/unit/test_budgets.py::test_all_limits_below_at_and_above_boundary` |
| CONST-17 no hidden or idle work | OPS-002 | `providers/fake.py`, `application/bootstrap.py` | `tests/e2e/test_offline_entrypoint.py::test_declared_console_entrypoint_initializes_offline_without_background_work` |
| CONST-10/11 visible and private | OPS-003 | `observability/logging.py`, `cli.py` | `tests/cli/test_output.py::test_logs_are_structured_redacted_concise_or_verbose` |
| CONST-12 self-watchfulness | OPS-004 | `application/health.py` | `tests/cli/test_doctor.py::test_doctor_reports_health_without_provider_or_network` |
| CONST-11/17 local private state | OPS-005 | `config.py`, `application/bootstrap.py` | `tests/component/test_runtime_paths.py::test_explicit_runtime_state_is_outside_workspace_and_private` |
| CONST-01/10 explicit owner interface | CLI-001 | `cli.py` | `tests/cli/test_commands.py::test_full_console_command_and_error_matrix` |
| CONST-06 truthful verified quality | QLT-001 | project configuration and full package | `tests/e2e/test_offline_entrypoint.py::test_declared_console_entrypoint_initializes_offline_without_background_work` plus repository-wide pytest/Ruff/mypy gates |
| CONST-06/19 no fabricated implementation | QLT-002 | `docs/requirements.md`, this document | `tests/test_traceability.py::test_every_requirement_has_automated_coverage_marker` |
| CONST-16 preserve governing source | QLT-003 | repository hygiene check | `tests/test_repository_hygiene.py::test_prime_directive_pdf_hash_is_unchanged` |

## Vertical-slice proof

| Scenario | Requirements proved together | Implemented automated proof |
|---|---|---|
| Pause, approve, exact write, verify | GOV-001, FUN-002–009, SAF-001–009, AUD-001–003, OPR-001, OPS-001–003 | `tests/e2e/test_orchestration.py::test_approve_creates_one_exact_file_then_readback_verifier_completes` and `tests/cli/test_commands.py` |
| Pause, deny, zero effect | GOV-001, SAF-004–005, SAF-009, AUD-003 | `tests/e2e/test_orchestration.py::test_deny_blocks_run_and_has_zero_effect` |
| Pause, restart, cancel | FUN-001, SAF-005, SAF-011, OPR-001–002 | `tests/cli/test_commands.py::test_full_console_command_and_error_matrix` and `tests/e2e/test_orchestration.py::test_cancel_revokes_pending_authority_and_later_approval_fails` |

## Required test infrastructure

The suite must provide a frozen UTC and monotonic clock, deterministic ID generator, role/round-scripted fake provider with a hard unexpected-call failure, temporary database and sandbox fixtures, hostile path tree, owner/agent/delegated principals, canonical action/approval factories, event-chain corruption fixtures, secret canaries, fault checkpoints, and an autouse socket/network guard. Tests must not inherit credentials or use the repository for runtime state.
