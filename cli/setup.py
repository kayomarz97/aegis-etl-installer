#!/usr/bin/env python3
"""
Aegis-ETL Setup Wizard — Interactive .env generator.

Two-phase design:
  Phase 1 (first run):
    - Prompts for GHCR PAT → validates via docker login
    - Generates auto-secrets (POSTGRES_PASSWORD, API keys, etc.)
    - Writes a partial .env (without LICENSE_KEY / AEGIS_VENDOR_SECRET)
    - Pulls Docker images
    - Runs the container in --diagnostic mode to obtain the fingerprint
    - Prints the fingerprint and tells the user to contact Aegis support

  Phase 2 (--complete flag):
    - Prompts for LICENSE_KEY and AEGIS_VENDOR_SECRET (provided by vendor)
    - Appends them to the existing .env
    - Starts docker compose up -d

Usage:
    python cli/setup.py             # Phase 1: initial setup
    python cli/setup.py --complete  # Phase 2: enter license key and start
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Optional rich import — graceful fallback for minimal environments
# ---------------------------------------------------------------------------
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, IntPrompt, Prompt
    from rich.table import Table

    console = Console()
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

    class _FallbackConsole:
        """Minimal console fallback when rich is not installed."""

        def print(self, *args: object, **kwargs: object) -> None:
            # Strip rich markup for plain output
            text = str(args[0]) if args else ""
            import re
            text = re.sub(r"\[.*?\]", "", text)
            print(text)

    console = _FallbackConsole()  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GHCR_REGISTRY = "ghcr.io"
GHCR_USERNAME = "aegis-client"  # Fixed pull-only username
IMAGE_ORG = "kayomarz97"
ENV_FILE = ".env"

VECTOR_DIM_CHOICES = ["128", "256", "384", "512", "768"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with reasonable defaults."""
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)  # type: ignore[arg-type]


def _validate_ghcr_login(pat: str) -> bool:
    """Attempt docker login to GHCR. Returns True on success."""
    result = _run([
        "docker", "login", GHCR_REGISTRY,
        "-u", GHCR_USERNAME,
        "-p", pat,
    ])
    return result.returncode == 0


def _generate_secret(length: int = 32) -> str:
    """Generate a URL-safe random secret."""
    return secrets.token_urlsafe(length)


def _generate_hex_secret(length: int = 32) -> str:
    """Generate a hex random secret (always >= 32 chars for length >= 16)."""
    return secrets.token_hex(length)


def _prompt(message: str, default: str = "", password: bool = False) -> str:
    """Prompt with rich if available, fallback to input()."""
    if HAS_RICH:
        return Prompt.ask(message, default=default or None, password=password) or default
    if password:
        import getpass
        return getpass.getpass(f"{message}: ") or default
    return input(f"{message} [{default}]: ") or default


def _prompt_int(message: str, default: int = 0) -> int:
    """Prompt for an integer."""
    if HAS_RICH:
        return IntPrompt.ask(message, default=default)
    while True:
        raw = input(f"{message} [{default}]: ") or str(default)
        try:
            return int(raw)
        except ValueError:
            print(f"  Invalid integer: {raw}")


def _confirm(message: str, default: bool = True) -> bool:
    """Prompt for yes/no."""
    if HAS_RICH:
        return Confirm.ask(message, default=default)
    suffix = " [Y/n]: " if default else " [y/N]: "
    raw = input(message + suffix).strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


# ---------------------------------------------------------------------------
# Phase 1: Initial setup
# ---------------------------------------------------------------------------

def _phase1() -> None:
    """Phase 1: validate GHCR PAT, generate secrets, write partial .env."""
    if HAS_RICH:
        console.print(Panel(
            "[bold cyan]Aegis-ETL Setup Wizard[/]\n\n"
            "This wizard will configure your Aegis-ETL deployment.\n"
            "You will need your [bold]GHCR access token[/] (provided by Aegis support).",
            title="Welcome",
            border_style="cyan",
        ))
    else:
        print("\n=== Aegis-ETL Setup Wizard ===\n")

    # --- Check prerequisites ---
    docker_check = _run(["docker", "--version"])
    if docker_check.returncode != 0:
        console.print("[bold red]Error:[/] Docker is not installed or not in PATH.")
        sys.exit(1)

    compose_check = _run(["docker", "compose", "version"])
    if compose_check.returncode != 0:
        console.print(
            "[bold red]Error:[/] Docker Compose (v2) is not available.\n"
            "Install it via: https://docs.docker.com/compose/install/"
        )
        sys.exit(1)

    # --- Step 1: GHCR PAT ---
    console.print("\n[bold]Step 1: Container Registry Authentication[/]")
    ghcr_pat = _prompt("  Enter your GHCR access token", password=True)

    if not ghcr_pat:
        console.print("[bold red]Error:[/] Token cannot be empty.")
        sys.exit(1)

    console.print("  Validating token...")
    if not _validate_ghcr_login(ghcr_pat):
        console.print(
            "[bold red]Error:[/] docker login to ghcr.io failed.\n"
            "  Check that your token has [bold]read:packages[/] scope."
        )
        sys.exit(1)
    console.print("  [green]✓ GHCR authentication successful[/]")

    # --- Step 2: Service configuration ---
    console.print("\n[bold]Step 2: Service Configuration[/]")

    pii_enabled = _confirm("  Enable PII masking?", default=True)
    worker_concurrency = _prompt_int("  Worker concurrency", default=4)
    max_ocr_jobs = _prompt_int("  Max concurrent OCR jobs", default=2)
    vector_dims = _prompt(
        "  Vector dimensions (128/256/384/512/768)",
        default="768",
    )
    if vector_dims not in VECTOR_DIM_CHOICES:
        console.print(
            f"[bold red]Error:[/] Invalid choice '{vector_dims}'. "
            f"Must be one of: {', '.join(VECTOR_DIM_CHOICES)}"
        )
        sys.exit(1)

    swagger_enabled = _confirm("  Enable Swagger docs (dev only)?", default=False)

    # --- Step 3: Generate secrets ---
    console.print("\n[bold]Step 3: Generating Secrets[/]")

    postgres_password = _generate_secret(24)
    ingest_api_key = _generate_secret(32)
    admin_api_key = _generate_secret(32)
    pii_vault_key = _generate_hex_secret(32) if pii_enabled else ""

    console.print("  [green]✓ All secrets generated[/]")

    # --- Step 4: Write .env ---
    env_path = Path(ENV_FILE)
    if env_path.exists():
        overwrite = _confirm(
            f"  [yellow]{ENV_FILE} already exists. Overwrite?[/]",
            default=False,
        )
        if not overwrite:
            console.print("  Setup cancelled.")
            sys.exit(0)

    env_lines = [
        "# Aegis-ETL Configuration",
        "# Generated by setup wizard — do not edit manually unless required.",
        "#",
        "# Phase 1: auto-generated values. LICENSE_KEY and AEGIS_VENDOR_SECRET",
        "# will be added in Phase 2 after fingerprint registration.",
        "",
        "# --- Database ---",
        f"POSTGRES_PASSWORD={postgres_password}",
        "",
        "# --- API Keys ---",
        f"INGEST_API_KEY={ingest_api_key}",
        f"ADMIN_API_KEY={admin_api_key}",
        "",
        "# --- PII Masking ---",
        f"PII_MASKING_ENABLED={'true' if pii_enabled else 'false'}",
    ]
    if pii_enabled:
        env_lines.append(f"PII_VAULT_KEY={pii_vault_key}")
    env_lines += [
        "",
        "# --- Worker ---",
        f"WORKER_CONCURRENCY={worker_concurrency}",
        f"MAX_CONCURRENT_OCR_JOBS={max_ocr_jobs}",
        "",
        "# --- Vectors ---",
        f"VECTOR_DIMENSIONS={vector_dims}",
        "",
        "# --- Feature Toggles ---",
        f"SWAGGER_ENABLED={'true' if swagger_enabled else 'false'}",
        "",
        "# --- Licensing (filled in Phase 2) ---",
        "# AEGIS_VENDOR_SECRET=",
        "# LICENSE_KEY=",
        "",
    ]

    env_content = "\n".join(env_lines)
    env_path.write_text(env_content)
    os.chmod(ENV_FILE, 0o600)

    console.print(f"  [green]✓ {ENV_FILE} written (permissions: 600)[/]")

    # --- Step 5: Pull images ---
    console.print("\n[bold]Step 4: Pulling Docker Images[/]")
    console.print("  This may take several minutes on first run...")

    pull_result = subprocess.run(
        ["docker", "compose", "pull"],
        capture_output=False,
    )
    if pull_result.returncode != 0:
        console.print(
            "[bold red]Error:[/] docker compose pull failed.\n"
            "  Check your network connection and GHCR token."
        )
        sys.exit(1)

    console.print("  [green]✓ Images pulled[/]")

    # --- Step 6: Get fingerprint ---
    console.print("\n[bold]Step 5: Obtaining Installation Fingerprint[/]")
    console.print("  Starting a temporary container to compute the fingerprint...")

    fp_result = _run([
        "docker", "compose", "run", "--rm",
        "-e", f"AEGIS_VENDOR_SECRET={'x' * 32}",  # dummy secret for diagnostic
        "app",
        "python", "src/licensing/validator.py", "--diagnostic",
    ])

    if fp_result.returncode != 0:
        console.print(
            "[bold yellow]Warning:[/] Could not obtain fingerprint automatically.\n"
            "  After running 'docker compose up', use:\n"
            "    docker compose exec app python src/licensing/validator.py --diagnostic"
        )
        fingerprint = None
    else:
        fingerprint = fp_result.stdout.strip()

    # --- Summary ---
    console.print("")
    if HAS_RICH:
        summary = Table(show_header=False, border_style="dim", padding=(0, 2))
        summary.add_column("Key", style="bold")
        summary.add_column("Value")
        summary.add_row("INGEST_API_KEY", ingest_api_key[:8] + "..." + ingest_api_key[-4:])
        summary.add_row("ADMIN_API_KEY", admin_api_key[:8] + "..." + admin_api_key[-4:])
        summary.add_row("PII Masking", "enabled" if pii_enabled else "disabled")
        summary.add_row("Workers", str(worker_concurrency))
        summary.add_row("Vector Dims", vector_dims)
        if fingerprint:
            summary.add_row("Fingerprint", fingerprint[:16] + "...")
        console.print(Panel(summary, title="Setup Summary", border_style="green"))

    if fingerprint:
        console.print(
            Panel(
                f"[bold]Your installation fingerprint:[/]\n\n"
                f"  [cyan]{fingerprint}[/]\n\n"
                "Send this fingerprint to [bold]support@aegis-etl.com[/] to receive\n"
                "your LICENSE_KEY and AEGIS_VENDOR_SECRET.\n\n"
                "Then run: [bold]python cli/setup.py --complete[/]",
                title="Next Step",
                border_style="yellow",
            )
        )
    else:
        console.print(
            "\n[bold yellow]Next step:[/] Contact support@aegis-etl.com with your\n"
            "fingerprint (obtained via --diagnostic), then run:\n"
            "  python cli/setup.py --complete"
        )


# ---------------------------------------------------------------------------
# Phase 2: Complete setup with license key
# ---------------------------------------------------------------------------

def _phase2() -> None:
    """Phase 2: add LICENSE_KEY + AEGIS_VENDOR_SECRET, start services."""
    env_path = Path(ENV_FILE)

    if not env_path.exists():
        console.print(
            "[bold red]Error:[/] .env file not found. Run Phase 1 first:\n"
            "  python cli/setup.py"
        )
        sys.exit(1)

    if HAS_RICH:
        console.print(Panel(
            "[bold cyan]Aegis-ETL Setup — Phase 2[/]\n\n"
            "Enter the LICENSE_KEY and AEGIS_VENDOR_SECRET provided by Aegis support.",
            title="Complete Setup",
            border_style="cyan",
        ))
    else:
        print("\n=== Aegis-ETL Setup — Phase 2 ===\n")

    vendor_secret = _prompt("  Enter AEGIS_VENDOR_SECRET", password=True)
    if len(vendor_secret) < 32:
        console.print(
            "[bold red]Error:[/] AEGIS_VENDOR_SECRET must be at least 32 characters."
        )
        sys.exit(1)

    license_key = _prompt("  Enter LICENSE_KEY", password=True)
    if not license_key:
        console.print("[bold red]Error:[/] LICENSE_KEY cannot be empty.")
        sys.exit(1)

    # Update .env — replace commented-out placeholders with real values
    env_content = env_path.read_text()
    env_content = env_content.replace(
        "# AEGIS_VENDOR_SECRET=",
        f"AEGIS_VENDOR_SECRET={vendor_secret}",
    )
    env_content = env_content.replace(
        "# LICENSE_KEY=",
        f"LICENSE_KEY={license_key}",
    )
    env_path.write_text(env_content)
    os.chmod(ENV_FILE, 0o600)

    console.print("  [green]✓ .env updated with license credentials[/]")

    # Start services
    console.print("\n[bold]Starting Aegis-ETL...[/]")
    start_result = subprocess.run(
        ["docker", "compose", "up", "-d"],
        capture_output=False,
    )

    if start_result.returncode != 0:
        console.print("[bold red]Error:[/] docker compose up failed.")
        sys.exit(1)

    console.print(
        Panel(
            "[bold green]Aegis-ETL is running![/]\n\n"
            "  API:    http://localhost:8000\n"
            "  Health: http://localhost:8000/health\n"
            "  Docs:   http://localhost:8000/docs (if Swagger enabled)\n\n"
            "CLI commands:\n"
            "  [cyan]python cli/aegis.py status[/]    — View job queue\n"
            "  [cyan]python cli/aegis.py ingest FILE[/] — Ingest a document\n"
            "  [cyan]python cli/aegis.py logs[/]      — Tail container logs\n"
            "  [cyan]python cli/aegis.py backup[/]    — Backup database",
            title="🚀 Setup Complete",
            border_style="green",
        )
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if "--complete" in sys.argv:
        _phase2()
    else:
        _phase1()


if __name__ == "__main__":
    main()
