"""Owner-facing command line for the deterministic offline Geth MVP."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated

import typer

from geth_ai.application.approvals import action_from_record
from geth_ai.application.bootstrap import Runtime, build_runtime
from geth_ai.application.orchestrator import RunResult
from geth_ai.config import Settings
from geth_ai.domain.enums import MemoryCategory
from geth_ai.observability import JsonLogger, render_integrity, render_timeline
from geth_ai.policy.redaction import redact_text
from geth_ai.providers import FakeScenario

app = typer.Typer(
    name="geth-ai",
    no_args_is_help=True,
    help="Safe, local-first, auditable multi-agent orchestration.",
)
task_app = typer.Typer(no_args_is_help=True, help="Inspect durable tasks.")
audit_app = typer.Typer(no_args_is_help=True, help="Inspect and verify the audit journal.")
memory_app = typer.Typer(no_args_is_help=True, help="Manage explicit owner-controlled memory.")
app.add_typer(task_app, name="task")
app.add_typer(audit_app, name="audit")
app.add_typer(memory_app, name="memory")


@dataclass(slots=True)
class CliState:
    settings: Settings
    verbose: bool

    @property
    def logger(self) -> JsonLogger:
        return JsonLogger(verbose=self.verbose)


DataDirOption = Annotated[
    Path | None,
    typer.Option(
        "--data-dir",
        help="Platform-local runtime directory (or GETH_AI_DATA_DIR).",
        file_okay=False,
        dir_okay=True,
    ),
]
WorkspaceOption = Annotated[
    Path | None,
    typer.Option(
        "--workspace",
        help="Owner-selected local read/list root (or GETH_AI_WORKSPACE).",
        file_okay=False,
        dir_okay=True,
    ),
]


@app.callback()
def main(
    ctx: typer.Context,
    data_dir: DataDirOption = None,
    workspace: WorkspaceOption = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Emit redacted structured diagnostics.")
    ] = False,
) -> None:
    """Configure one explicit, bounded CLI process; no daemon is started."""

    ctx.obj = CliState(
        settings=Settings.from_environment(
            data_dir=data_dir,
            workspace_root=workspace,
        ),
        verbose=verbose,
    )


def _state(ctx: typer.Context) -> CliState:
    state = ctx.obj
    if not isinstance(state, CliState):
        raise RuntimeError("CLI context was not initialized")
    return state


def _runtime(
    ctx: typer.Context,
    *,
    scenario: FakeScenario = FakeScenario.PASS,
    recover: bool = True,
) -> Runtime:
    state = _state(ctx)
    runtime = build_runtime(state.settings, scenario=scenario, recover=recover)
    state.logger.emit(
        "debug",
        "runtime_ready",
        {
            "provider": runtime.settings.provider,
            "data_dir": str(runtime.settings.data_dir),
            "recovery": recover,
        },
    )
    return runtime


def _fail(ctx: typer.Context, exc: Exception, *, code: int = 1) -> None:
    message = redact_text(str(exc)) or exc.__class__.__name__
    _state(ctx).logger.emit("error", "command_failed", {"error": message})
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code) from exc


def _print_result(runtime: Runtime, result: RunResult) -> None:
    typer.echo(result.state.value.upper())
    typer.echo(f"Recommendation: {result.recommendation}")
    typer.echo(f"Status: {result.summary}")
    typer.echo(f"Confidence: {result.confidence_basis_points / 100:.2f}%")
    if result.dissent:
        typer.echo("Dissent:")
        for dissent in result.dissent:
            typer.echo(f"- {dissent}")
    if result.approval_id is not None:
        approval = runtime.repositories.approvals.require(result.approval_id)
        action = action_from_record(approval)
        typer.echo(f"Approval: {approval.approval_id}")
        typer.echo(f"Action: {action.tool_name}")
        typer.echo(f"Target: sandbox/{result.sandbox_target}")
        typer.echo(f"Digest: {action.digest}")
        typer.echo(f"Expires: {approval.expires_at}")
    typer.echo(f"Verified: {'yes' if result.verified else 'no'}")
    typer.echo(f"Run: {result.run_id}")
    typer.echo(f"Audit: {result.audit_hint}")


@app.command("init")
def initialize(ctx: typer.Context) -> None:
    """Create private local state and apply SQLite migrations without provider calls."""

    try:
        runtime = _runtime(ctx, recover=False)
        typer.echo("Geth initialized.")
        typer.echo(f"Data: {runtime.settings.data_dir}")
        typer.echo(f"Database: {runtime.settings.database_path}")
        typer.echo(f"Sandbox: {runtime.settings.sandbox_root}")
        typer.echo("Provider: fake (offline)")
    except Exception as exc:
        _fail(ctx, exc)


@app.command()
def demo(
    ctx: typer.Context,
    scenario: Annotated[
        FakeScenario,
        typer.Option(help="Deterministic fake-provider scenario."),
    ] = FakeScenario.PASS,
) -> None:
    """Plan the deterministic demo and pause before its exact sandbox write."""

    objective = "Demonstrate bounded multi-role planning and one exact sandbox artifact."
    try:
        runtime = _runtime(ctx, scenario=scenario)
        result = asyncio.run(runtime.orchestrator.start_run(objective))
        _print_result(runtime, result)
    except Exception as exc:
        _fail(ctx, exc)


@app.command("run")
def run_objective(
    ctx: typer.Context,
    objective: Annotated[str, typer.Argument(help="Human objective to analyze.")],
    provider: Annotated[
        str,
        typer.Option(help="Provider adapter; the MVP supports only 'fake'."),
    ] = "fake",
    scenario: Annotated[
        FakeScenario,
        typer.Option(help="Deterministic fake-provider scenario."),
    ] = FakeScenario.PASS,
) -> None:
    """Analyze an objective and pause before any material side effect."""

    try:
        if provider != "fake":
            raise ValueError("the MVP supports only the deterministic fake provider")
        runtime = _runtime(ctx, scenario=scenario)
        result = asyncio.run(runtime.orchestrator.start_run(objective))
        _print_result(runtime, result)
    except Exception as exc:
        _fail(ctx, exc)


@task_app.command("list")
def task_list(ctx: typer.Context) -> None:
    """List durable runs without provider or tool calls."""

    try:
        runtime = _runtime(ctx)
        runs = runtime.repositories.runs.list()
        if not runs:
            typer.echo("No tasks.")
            return
        for run in runs:
            typer.echo(f"{run.run_id} {run.state} {run.objective_summary}")
    except Exception as exc:
        _fail(ctx, exc)


@task_app.command("show")
def task_show(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Durable run ID.")],
) -> None:
    """Show state, bounded work, and approvals for one run."""

    try:
        runtime = _runtime(ctx)
        run = runtime.repositories.runs.require(run_id)
        work_items = runtime.repositories.work_items.list_for_run(run_id)
        messages = runtime.repositories.messages.list_for_run(run_id)
        approvals = runtime.repositories.approvals.list_for_run(run_id)
        typer.echo(f"Run: {run.run_id}")
        typer.echo(f"State: {run.state}")
        typer.echo(f"Objective: {run.objective_summary}")
        typer.echo(f"Work items: {len(work_items)}")
        typer.echo(f"Messages: {len(messages)}")
        typer.echo(f"Approvals: {len(approvals)}")
        for approval in approvals:
            typer.echo(
                f"- {approval.approval_id} {approval.status} "
                f"digest={approval.action_digest} expires={approval.expires_at}"
            )
        typer.echo(f"Audit: geth-ai audit show {run_id}")
    except Exception as exc:
        _fail(ctx, exc)


@app.command()
def approve(
    ctx: typer.Context,
    approval_id: Annotated[str, typer.Argument(help="Exact approval request ID.")],
) -> None:
    """As the local owner, approve and redeem exactly one persisted action."""

    try:
        runtime = _runtime(ctx)
        approval = runtime.repositories.approvals.require(approval_id)
        action = action_from_record(approval)
        typer.echo("Approving exact action:")
        typer.echo(f"Action: {action.tool_name}")
        typer.echo(f"Target: {action.target}")
        typer.echo(f"Digest: {action.digest}")
        typer.echo(f"Content SHA-256: {action.content_sha256}")
        typer.echo(f"Content bytes: {action.content_length}")
        typer.echo(f"Expires: {approval.expires_at}")
        result = asyncio.run(runtime.orchestrator.approve(approval_id))
        _print_result(runtime, result)
    except Exception as exc:
        _fail(ctx, exc)


@app.command()
def deny(
    ctx: typer.Context,
    approval_id: Annotated[str, typer.Argument(help="Exact approval request ID.")],
) -> None:
    """Deny one exact action; no tool is invoked."""

    try:
        runtime = _runtime(ctx)
        _print_result(runtime, runtime.orchestrator.deny(approval_id))
    except Exception as exc:
        _fail(ctx, exc)


@app.command()
def cancel(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Durable run ID.")],
) -> None:
    """Cancel a run and revoke its pending authority."""

    try:
        runtime = _runtime(ctx)
        _print_result(runtime, runtime.orchestrator.cancel(run_id))
    except Exception as exc:
        _fail(ctx, exc)


@audit_app.command("show")
def audit_show(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Durable run ID.")],
) -> None:
    """Show an ordered, content-minimized local timeline."""

    try:
        runtime = _runtime(ctx)
        runtime.repositories.runs.require(run_id)
        typer.echo(render_timeline(runtime.events.list(run_id=run_id), verbose=_state(ctx).verbose))
    except Exception as exc:
        _fail(ctx, exc)


@audit_app.command("verify")
def audit_verify(ctx: typer.Context) -> None:
    """Verify the unanchored local hash chain and state its limitations."""

    try:
        runtime = _runtime(ctx, recover=False)
        report = runtime.events.verify()
        typer.echo(render_integrity(report))
        if not report.valid:
            raise typer.Exit(2)
    except typer.Exit:
        raise
    except Exception as exc:
        _fail(ctx, exc)


@memory_app.command("search")
def memory_search(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="SQLite FTS5 query.")],
    limit: Annotated[int, typer.Option(min=1, max=100)] = 20,
) -> None:
    """Search explicit retained memory as the local owner."""

    try:
        runtime = _runtime(ctx)
        records = runtime.memory.search(query, owner=runtime.owner, limit=limit)
        if not records:
            typer.echo("No memory matches.")
            return
        for record in records:
            typer.echo(
                f"{record.memory_id} {record.category} {record.status} "
                f"{record.content or '[forgotten]'}"
            )
    except Exception as exc:
        _fail(ctx, exc)


@memory_app.command("remember")
def memory_remember(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Provenance run ID.")],
    content: Annotated[str, typer.Argument(help="Explicit content to retain.")],
    category: Annotated[
        MemoryCategory,
        typer.Option(help="Allowed provenance-aware memory category."),
    ] = MemoryCategory.OWNER_FEEDBACK,
) -> None:
    """Explicitly retain one provenance-linked memory item."""

    try:
        runtime = _runtime(ctx)
        runtime.repositories.runs.require(run_id)
        record = runtime.memory.remember(
            owner=runtime.owner,
            run_id=run_id,
            content=content,
            category=category,
        )
        typer.echo(f"Remembered: {record.memory_id}")
    except Exception as exc:
        _fail(ctx, exc)


@memory_app.command("export")
def memory_export(ctx: typer.Context) -> None:
    """Export inspectable owner memory, including provenance."""

    try:
        runtime = _runtime(ctx)
        records = runtime.memory.export(owner=runtime.owner)
        typer.echo(
            json.dumps(
                [asdict(record) for record in records],
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            )
        )
    except Exception as exc:
        _fail(ctx, exc)


@memory_app.command("correct")
def memory_correct(
    ctx: typer.Context,
    memory_id: Annotated[str, typer.Argument(help="Memory ID to supersede.")],
    run_id: Annotated[str, typer.Option(help="Provenance run ID.")],
    content: Annotated[str, typer.Argument(help="Corrected content.")],
) -> None:
    """Create a provenance-linked correction and preserve supersession metadata."""

    try:
        runtime = _runtime(ctx)
        runtime.repositories.runs.require(run_id)
        record = runtime.memory.correct(
            memory_id,
            owner=runtime.owner,
            content=content,
            run_id=run_id,
        )
        typer.echo(f"Corrected: {record.memory_id}; supersedes {record.supersedes_id}")
    except Exception as exc:
        _fail(ctx, exc)


@memory_app.command("forget")
def memory_forget(
    ctx: typer.Context,
    memory_id: Annotated[str, typer.Argument(help="Memory ID to forget.")],
) -> None:
    """Delete memory content/FTS state while retaining a content-free tombstone."""

    try:
        runtime = _runtime(ctx)
        record = runtime.memory.forget(memory_id, owner=runtime.owner)
        typer.echo(f"Forgotten: {record.memory_id}")
    except Exception as exc:
        _fail(ctx, exc)


@app.command("emergency-stop")
def emergency_stop(ctx: typer.Context) -> None:
    """Activate the durable global stop latch."""

    try:
        runtime = _runtime(ctx, recover=False)
        runtime.emergency_stop.activate(
            actor=str(runtime.owner.principal_id),
            at=runtime.clock.now(),
        )
        typer.echo("Emergency stop: ACTIVE")
    except Exception as exc:
        _fail(ctx, exc)


@app.command("emergency-reset")
def emergency_reset(
    ctx: typer.Context,
    confirmation: Annotated[
        str,
        typer.Option("--confirm", help="Exact value RESET is required."),
    ],
) -> None:
    """Reset the global stop latch with explicit owner confirmation."""

    try:
        runtime = _runtime(ctx, recover=False)
        runtime.emergency_stop.reset(confirmation=confirmation)
        typer.echo("Emergency stop: clear")
    except Exception as exc:
        _fail(ctx, exc)


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Check local configuration and integrity without provider or network calls."""

    try:
        runtime = _runtime(ctx, recover=False)
        report = runtime.health.check()
        typer.echo("Geth doctor: HEALTHY" if report.healthy else "Geth doctor: DEGRADED")
        for check in report.checks:
            typer.echo(f"[{check.level.value.upper()}] {check.name}: {check.summary}")
        if not report.healthy:
            raise typer.Exit(2)
    except typer.Exit:
        raise
    except Exception as exc:
        _fail(ctx, exc)


if __name__ == "__main__":
    app()
