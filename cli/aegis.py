#!/usr/bin/env python3
"""
Aegis-ETL CLI — Professional terminal interface for the Aegis-ETL platform.

Commands:
    aegis ingest <file_or_folder>   Upload a file or queue a folder for ingestion
    aegis status                    Show live job queue dashboard
    aegis logs                      Tail Docker container logs
    aegis backup                    Export a compressed PostgreSQL backup
    aegis restore <file.sql.gz>     Restore a PostgreSQL backup (destructive)
    aegis version                   Show CLI + backend versions

Environment variables:
    AEGIS_API_URL       Backend URL  (default: http://localhost:8000)
    AEGIS_INGEST_KEY    API key for /ingest endpoint
    AEGIS_ADMIN_KEY     API key for /admin endpoints
    AEGIS_PROJECT_DIR   Directory containing docker-compose.yml (default: .)
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLI_VERSION = "1.0.0"

# Supported file extensions for folder ingestion (matches SUPPORTED_MIME_TYPES
# in src/pipeline/validation.py)
_SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".msg",
    ".txt", ".csv", ".md", ".eml",
}

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="aegis",
    help="Aegis-ETL — Enterprise Document Ingestion Platform",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


def _get_api_url() -> str:
    return os.environ.get("AEGIS_API_URL", "http://localhost:8000").rstrip("/")


def _get_ingest_key() -> str:
    key = os.environ.get("AEGIS_INGEST_KEY", "")
    if not key:
        console.print(
            "[bold red]Error:[/] AEGIS_INGEST_KEY environment variable is not set.\n"
            "Set it to your ingest API key: export AEGIS_INGEST_KEY=<key>",
        )
        raise typer.Exit(code=1)
    return key


def _get_admin_key() -> str:
    key = os.environ.get("AEGIS_ADMIN_KEY", "")
    if not key:
        console.print(
            "[bold red]Error:[/] AEGIS_ADMIN_KEY environment variable is not set.\n"
            "Set it to your admin API key: export AEGIS_ADMIN_KEY=<key>",
        )
        raise typer.Exit(code=1)
    return key


def _get_project_dir() -> str:
    return os.environ.get("AEGIS_PROJECT_DIR", ".")


def _compose_cmd() -> list[str]:
    """Return the base docker compose command with the correct project dir."""
    project_dir = _get_project_dir()
    compose_file = os.path.join(project_dir, "docker-compose.yml")
    return ["docker", "compose", "-f", compose_file]


# ---------------------------------------------------------------------------
# aegis ingest
# ---------------------------------------------------------------------------

@app.command()
def ingest(
    path: str = typer.Argument(..., help="File or folder path to ingest"),
) -> None:
    """Upload a file or queue an entire folder for ingestion."""
    target = Path(path)
    if not target.exists():
        console.print(f"[bold red]Error:[/] Path does not exist: {path}")
        raise typer.Exit(code=1)

    api_url = _get_api_url()
    api_key = _get_ingest_key()
    headers = {"X-API-KEY": api_key}

    if target.is_file():
        _ingest_single_file(target, api_url, headers)
    elif target.is_dir():
        _ingest_folder(target, api_url, headers)
    else:
        console.print(f"[bold red]Error:[/] {path} is not a file or directory.")
        raise typer.Exit(code=1)


def _ingest_single_file(
    file_path: Path,
    api_url: str,
    headers: dict[str, str],
) -> Optional[int]:
    """POST a single file to /ingest. Returns job_id on success, None on failure."""
    url = f"{api_url}/ingest"
    file_size = file_path.stat().st_size

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"Uploading {file_path.name}", total=file_size)

        try:
            with open(file_path, "rb") as f:
                # Read the full file for the upload
                content = f.read()
                progress.update(task, completed=file_size)

            resp = requests.post(
                url,
                files={"file": (file_path.name, content)},
                headers=headers,
                timeout=120,
            )
        except requests.ConnectionError:
            console.print(
                f"[bold red]Error:[/] Cannot connect to {api_url}. "
                "Is the Aegis backend running?"
            )
            return None
        except requests.Timeout:
            console.print("[bold red]Error:[/] Upload timed out after 120s.")
            return None

    if resp.status_code == 202:
        data = resp.json()
        job_id = data["job_id"]
        console.print(
            f"[bold green]✓[/] {file_path.name} → Job [cyan]#{job_id}[/] queued"
        )
        return job_id
    elif resp.status_code == 401:
        console.print("[bold red]Error:[/] Invalid API key (401 Unauthorized)")
        return None
    elif resp.status_code == 413:
        console.print(
            f"[bold red]Error:[/] {file_path.name} exceeds the server's "
            "maximum upload size."
        )
        return None
    elif resp.status_code == 429:
        console.print("[bold yellow]Warning:[/] Rate limit exceeded. Retry later.")
        return None
    else:
        detail = resp.json().get("detail", resp.text) if resp.text else "Unknown error"
        console.print(
            f"[bold red]Error:[/] Server returned {resp.status_code}: {detail}"
        )
        return None


def _ingest_folder(
    folder: Path,
    api_url: str,
    headers: dict[str, str],
) -> None:
    """Walk a directory and ingest all supported files one by one."""
    files = sorted(
        f for f in folder.rglob("*")
        if f.is_file() and f.suffix.lower() in _SUPPORTED_EXTENSIONS
    )

    if not files:
        console.print(
            f"[bold yellow]Warning:[/] No supported files found in {folder}\n"
            f"Supported extensions: {', '.join(sorted(_SUPPORTED_EXTENSIONS))}"
        )
        return

    console.print(
        Panel(
            f"Found [bold]{len(files)}[/] file(s) in [cyan]{folder}[/]",
            title="Folder Ingestion",
            border_style="blue",
        )
    )

    succeeded = 0
    failed = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        overall = progress.add_task("Overall progress", total=len(files))

        for file_path in files:
            progress.update(overall, description=f"Queuing {file_path.name}")
            job_id = _ingest_single_file(file_path, api_url, headers)
            if job_id is not None:
                succeeded += 1
            else:
                failed += 1
            progress.advance(overall)

    console.print(
        f"\n[bold green]✓ {succeeded}[/] file(s) queued, "
        f"[bold red]✗ {failed}[/] failed"
    )


# ---------------------------------------------------------------------------
# aegis status
# ---------------------------------------------------------------------------

@app.command()
def status() -> None:
    """Show job queue statistics and recent jobs."""
    api_url = _get_api_url()
    admin_key = _get_admin_key()
    headers = {"X-API-KEY": admin_key}

    try:
        resp = requests.get(f"{api_url}/admin/status", headers=headers, timeout=10)
    except requests.ConnectionError:
        console.print(
            f"[bold red]Error:[/] Cannot connect to {api_url}. "
            "Is the Aegis backend running?"
        )
        raise typer.Exit(code=1)

    if resp.status_code == 401:
        console.print("[bold red]Error:[/] Invalid admin API key (401)")
        raise typer.Exit(code=1)

    if resp.status_code != 200:
        console.print(f"[bold red]Error:[/] Server returned {resp.status_code}")
        raise typer.Exit(code=1)

    data = resp.json()

    # -- Summary panel --
    summary_table = Table(show_header=False, box=None, padding=(0, 2))
    summary_table.add_column("Label", style="bold")
    summary_table.add_column("Value", justify="right")
    summary_table.add_row("Total Jobs", str(data["total"]))
    summary_table.add_row("Pending", f"[yellow]{data['pending']}[/]")
    summary_table.add_row("Processing", f"[blue]{data['processing']}[/]")
    summary_table.add_row("Done", f"[green]{data['done']}[/]")
    summary_table.add_row("Failed", f"[red]{data['failed']}[/]")
    summary_table.add_row(
        "Failed (max retries)", f"[bold red]{data['failed_max_retries']}[/]"
    )

    console.print(Panel(summary_table, title="Queue Summary", border_style="cyan"))

    # -- Recent jobs table --
    if not data["jobs"]:
        console.print("[dim]No jobs found.[/]")
        return

    jobs_table = Table(
        title="Recent Jobs (newest first)",
        show_lines=True,
        border_style="dim",
    )
    jobs_table.add_column("ID", style="cyan", justify="right", width=6)
    jobs_table.add_column("Filename", style="white", max_width=40, overflow="ellipsis")
    jobs_table.add_column("Status", width=20)
    jobs_table.add_column("Retries", justify="center", width=7)
    jobs_table.add_column("Created", width=20)
    jobs_table.add_column("Error", style="dim red", max_width=40, overflow="ellipsis")

    _status_styles = {
        "PENDING": "[yellow]PENDING[/]",
        "PROCESSING": "[blue]PROCESSING[/]",
        "DONE": "[green]DONE[/]",
        "FAILED": "[red]FAILED[/]",
        "FAILED_MAX_RETRIES": "[bold red]FAILED_MAX_RETRIES[/]",
    }

    for job in data["jobs"]:
        status_str = _status_styles.get(job["status"], job["status"])
        created = job["created_at"][:19] if job["created_at"] else "-"
        error = (job["error_message"] or "")[:60]
        jobs_table.add_row(
            str(job["id"]),
            job["filename"],
            status_str,
            str(job["retry_count"]),
            created,
            error,
        )

    console.print(jobs_table)


# ---------------------------------------------------------------------------
# aegis logs
# ---------------------------------------------------------------------------

@app.command()
def logs(
    service: str = typer.Argument(
        "app",
        help="Service name to tail (default: app)",
    ),
    lines: int = typer.Option(100, "--lines", "-n", help="Number of lines to show"),
) -> None:
    """Tail Docker container logs for a service."""
    cmd = _compose_cmd() + ["logs", "-f", "--tail", str(lines), service]

    console.print(
        f"[dim]Tailing logs for service '{service}' "
        f"(Ctrl+C to stop)...[/]\n"
    )

    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        console.print(
            "[bold red]Error:[/] 'docker compose' not found. "
            "Is Docker installed and in your PATH?"
        )
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        console.print("\n[dim]Log tailing stopped.[/]")


# ---------------------------------------------------------------------------
# aegis backup
# ---------------------------------------------------------------------------

@app.command()
def backup() -> None:
    """Export a compressed PostgreSQL backup from the database container."""
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_file = f"aegis_backup_{timestamp}.sql.gz"

    console.print(
        Panel(
            f"Backing up database to [cyan]{output_file}[/]",
            title="Database Backup",
            border_style="green",
        )
    )

    # Execute pg_dump inside the db container, pipe through gzip
    dump_cmd = _compose_cmd() + [
        "exec", "-T", "db",
        "pg_dump", "-U", "aegis", "-d", "aegis",
    ]

    try:
        with open(output_file, "wb") as outfile:
            dump_proc = subprocess.Popen(
                dump_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            gzip_proc = subprocess.Popen(
                ["gzip", "-9"],
                stdin=dump_proc.stdout,
                stdout=outfile,
                stderr=subprocess.PIPE,
            )
            # Allow dump_proc to receive SIGPIPE if gzip exits early
            if dump_proc.stdout:
                dump_proc.stdout.close()

            gzip_proc.wait()
            dump_proc.wait()

        if dump_proc.returncode != 0:
            stderr = dump_proc.stderr.read().decode() if dump_proc.stderr else ""
            console.print(f"[bold red]Error:[/] pg_dump failed: {stderr}")
            # Clean up partial file
            Path(output_file).unlink(missing_ok=True)
            raise typer.Exit(code=1)

        file_size = Path(output_file).stat().st_size
        # Restrict file permissions to owner-only (Unix)
        os.chmod(output_file, 0o600)

        console.print(
            f"[bold green]✓[/] Backup saved: [cyan]{output_file}[/] "
            f"({file_size:,} bytes)"
        )

    except FileNotFoundError:
        console.print(
            "[bold red]Error:[/] 'docker compose' or 'gzip' not found. "
            "Is Docker installed and in your PATH?"
        )
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# aegis restore
# ---------------------------------------------------------------------------

@app.command()
def restore(
    backup_file: str = typer.Argument(
        ..., help="Path to .sql.gz backup file to restore"
    ),
) -> None:
    """Restore a PostgreSQL backup (DESTRUCTIVE — drops and recreates data)."""
    backup_path = Path(backup_file)

    if not backup_path.exists():
        console.print(f"[bold red]Error:[/] File not found: {backup_file}")
        raise typer.Exit(code=1)

    if not backup_path.name.endswith(".sql.gz"):
        console.print(
            "[bold yellow]Warning:[/] File does not end with .sql.gz. "
            "Are you sure this is a valid backup?"
        )

    console.print(
        Panel(
            "[bold red]WARNING: This will DROP all existing data and "
            "restore from the backup file.[/]\n\n"
            f"Backup file: [cyan]{backup_path}[/]\n"
            f"Target: PostgreSQL container 'db'",
            title="⚠️  Destructive Restore",
            border_style="red",
        )
    )

    confirm = typer.confirm("Are you sure you want to proceed?", default=False)
    if not confirm:
        console.print("[dim]Restore cancelled.[/]")
        raise typer.Exit(code=0)

    console.print("[bold blue]Restoring database...[/]")

    # Decompress and pipe into psql inside the container
    psql_cmd = _compose_cmd() + [
        "exec", "-T", "db",
        "psql", "-U", "aegis", "-d", "aegis",
    ]

    try:
        with open(backup_path, "rb") as infile:
            gunzip_proc = subprocess.Popen(
                ["gunzip", "-c"],
                stdin=infile,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            psql_proc = subprocess.Popen(
                psql_cmd,
                stdin=gunzip_proc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            # Allow gunzip to receive SIGPIPE if psql exits early
            if gunzip_proc.stdout:
                gunzip_proc.stdout.close()

            _, psql_stderr = psql_proc.communicate(timeout=600)
            gunzip_proc.wait()

        if psql_proc.returncode != 0:
            console.print(
                f"[bold red]Error:[/] psql restore failed:\n"
                f"{psql_stderr.decode()}"
            )
            raise typer.Exit(code=1)

        console.print("[bold green]✓[/] Database restored successfully.")
        console.print(
            "[dim]Tip: Restart Aegis to pick up restored data: "
            "docker compose restart app[/]"
        )

    except subprocess.TimeoutExpired:
        console.print("[bold red]Error:[/] Restore timed out after 10 minutes.")
        raise typer.Exit(code=1)
    except FileNotFoundError:
        console.print(
            "[bold red]Error:[/] 'docker compose' or 'gunzip' not found. "
            "Is Docker installed and in your PATH?"
        )
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# aegis version
# ---------------------------------------------------------------------------

@app.command()
def version() -> None:
    """Show CLI and backend versions, with mismatch warning."""
    api_url = _get_api_url()

    console.print(f"  CLI version:     [cyan]{CLI_VERSION}[/]")

    try:
        resp = requests.get(f"{api_url}/version", timeout=5)
        if resp.status_code == 200:
            backend_version = resp.json().get("version", "unknown")
            console.print(f"  Backend version: [cyan]{backend_version}[/]")

            if backend_version != CLI_VERSION:
                console.print(
                    "\n[bold yellow]⚠ Version mismatch![/] "
                    "CLI and backend versions differ.\n"
                    "  Update the CLI or pull the latest Docker images."
                )
            else:
                console.print("\n[bold green]✓[/] CLI and backend versions match.")
        else:
            console.print(
                f"  Backend version: [dim]unavailable (HTTP {resp.status_code})[/]"
            )
    except requests.ConnectionError:
        console.print(
            f"  Backend version: [dim]unavailable (cannot reach {api_url})[/]"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
