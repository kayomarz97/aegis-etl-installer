#!/usr/bin/env python3
"""
Aegis-ETL Setup Wizard — Interactive .env generator + automated license delivery.

Two-phase design:
  Phase 1 (first run):
    - Checks prerequisites (Docker, Docker Compose)
    - Prompts for service configuration
    - Generates auto-secrets (POSTGRES_PASSWORD, API keys, etc.)
    - Writes a partial .env (LICENSE_KEY / AEGIS_VENDOR_SECRET commented out)
    - Computes fingerprint locally (pure Python — no container needed)
    - Creates a Razorpay order via the Aegis portal
    - Opens payment URL in browser
    - Polls portal until payment confirmed
    - Logs in to the Aegis registry and pulls Docker images
    - Writes full credentials to .env
    - Starts docker compose up -d

  Phase 2 (--complete flag):
    Manual fallback: prompts for LICENSE_KEY and AEGIS_VENDOR_SECRET
    and starts services. Use only if Phase 1 polling was interrupted
    and you received credentials by other means.

Usage:
    python cli/setup.py             # Phase 1: initial setup
    python cli/setup.py --complete  # Phase 2: manual license entry fallback
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
import webbrowser
from pathlib import Path, PurePosixPath

try:
    import urllib.request
    import urllib.error
except ImportError:
    pass

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
            text = str(args[0]) if args else ""
            import re
            text = re.sub(r"\[.*?\]", "", text)
            print(text)

    console = _FallbackConsole()  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PORTAL_BASE_URL = os.environ.get("AEGIS_PORTAL_URL", "http://161.97.93.228:8080")
ORDER_STATE_FILE = ".aegis_order.json"
POLL_INTERVAL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 600   # 10 minutes
ENV_FILE = ".env"
VECTOR_DIM_CHOICES = ["128", "256", "384", "512", "768"]

# Pinned hostname — must match docker-compose.yml `hostname: aegis-node`
_PINNED_HOSTNAME = "aegis-node"


# ---------------------------------------------------------------------------
# Helpers — subprocess / HTTP
# ---------------------------------------------------------------------------

def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)  # type: ignore[arg-type]


def _http_post(url: str, body: dict, timeout: int = 15) -> dict:
    """Simple HTTP POST — no external dependencies."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} from portal: {body_text}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Aegis portal at {PORTAL_BASE_URL}: {e.reason}") from e


def _http_get(url: str, timeout: int = 15) -> dict:
    """Simple HTTP GET — no external dependencies."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} from portal: {body_text}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Aegis portal at {PORTAL_BASE_URL}: {e.reason}") from e


# ---------------------------------------------------------------------------
# Helpers — user prompts
# ---------------------------------------------------------------------------

def _generate_secret(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def _generate_hex_secret(length: int = 32) -> str:
    return secrets.token_hex(length)


def _prompt(message: str, default: str = "", password: bool = False) -> str:
    if HAS_RICH:
        return Prompt.ask(message, default=default or None, password=password) or default
    if password:
        import getpass
        return getpass.getpass(f"{message}: ") or default
    return input(f"{message} [{default}]: ") or default


def _prompt_int(message: str, default: int = 0) -> int:
    if HAS_RICH:
        return IntPrompt.ask(message, default=default)
    while True:
        raw = input(f"{message} [{default}]: ") or str(default)
        try:
            return int(raw)
        except ValueError:
            print(f"  Invalid integer: {raw}")


def _confirm(message: str, default: bool = True) -> bool:
    if HAS_RICH:
        return Confirm.ask(message, default=default)
    suffix = " [Y/n]: " if default else " [y/N]: "
    raw = input(message + suffix).strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


# ---------------------------------------------------------------------------
# Fingerprint (mirrors src/licensing/validator.py — keep in sync)
# ---------------------------------------------------------------------------

def _compute_fingerprint(install_dir: Path) -> str:
    """
    Compute the hardware fingerprint in pure Python.
    Mirrors validator.py logic exactly:
      sha256(install_id + ":" + hostname + ":" + cpu_count)
    install_id is a UUID persisted to data/.aegis_install_id
    hostname is hardcoded to "aegis-node" (pinned in docker-compose)
    cpu_count uses os.cpu_count() — same value inside and outside Docker
    """
    id_file = install_dir / "data" / ".aegis_install_id"
    id_file.parent.mkdir(parents=True, exist_ok=True)

    if id_file.exists():
        install_id = id_file.read_text().strip()
    else:
        install_id = str(uuid.uuid4())
        id_file.write_text(install_id)

    cpu_count = os.cpu_count() or 1
    raw = f"{install_id}:{_PINNED_HOSTNAME}:{cpu_count}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Order state persistence
# ---------------------------------------------------------------------------

def _save_order_state(state: dict) -> None:
    p = Path(ORDER_STATE_FILE)
    p.write_text(json.dumps(state, indent=2))
    os.chmod(p, 0o600)


def _load_order_state() -> dict | None:
    p = Path(ORDER_STATE_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None


def _delete_order_state() -> None:
    try:
        Path(ORDER_STATE_FILE).unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Portal API calls
# ---------------------------------------------------------------------------

def _create_order(fingerprint: str) -> dict:
    """POST /api/v1/order/create. Returns {order_id, payment_url, ...}."""
    console.print("  Contacting Aegis portal...")
    try:
        result = _http_post(
            f"{PORTAL_BASE_URL}/api/v1/order/create",
            {"fingerprint": fingerprint},
        )
    except RuntimeError as e:
        console.print(f"[bold red]Error:[/] {e}")
        sys.exit(1)
    return result


def _poll_for_license(order_id: str) -> dict:
    """
    Poll GET /api/v1/order/{order_id}/status every POLL_INTERVAL_SECONDS.
    Returns full credentials dict when status == 'paid'.
    Saves order state on timeout so next run can resume.
    """
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    spinner = ["|", "/", "-", "\\"]
    i = 0

    while time.time() < deadline:
        try:
            data = _http_get(f"{PORTAL_BASE_URL}/api/v1/order/{order_id}/status")
        except RuntimeError as e:
            console.print(f"\n[bold yellow]Warning:[/] {e} — retrying...")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        if data.get("status") == "paid":
            console.print("\n  [green]✓ Payment confirmed![/]")
            return data

        # Still pending — show spinner
        print(f"\r  Waiting for payment... {spinner[i % 4]}", end="", flush=True)
        i += 1
        time.sleep(POLL_INTERVAL_SECONDS)

    # Timed out
    print()
    console.print(
        f"\n[bold yellow]Timed out waiting for payment.[/]\n"
        f"Your order ID has been saved to [bold]{ORDER_STATE_FILE}[/].\n"
        f"Re-run [bold]python cli/setup.py[/] to resume."
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Registry login + image pull
# ---------------------------------------------------------------------------

def _configure_insecure_registry(host: str) -> None:
    """Add host to /etc/docker/daemon.json insecure-registries if not already present."""
    daemon_json = Path("/etc/docker/daemon.json")
    try:
        if daemon_json.exists():
            config = json.loads(daemon_json.read_text())
        else:
            config = {}
        registries: list = config.get("insecure-registries", [])
        if host not in registries:
            registries.append(host)
            config["insecure-registries"] = registries
            content = json.dumps(config, indent=2)
            # Write via sudo tee
            proc = subprocess.run(
                ["sudo", "tee", str(daemon_json)],
                input=content,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip())
            subprocess.run(["sudo", "systemctl", "reload", "docker"], check=True)
            console.print(f"  [green]✓ Configured Docker insecure registry for {host}[/]")
    except Exception as e:
        console.print(
            f"[bold yellow]Warning:[/] Could not configure insecure registry automatically: {e}\n"
            f"  If login fails, add {host!r} to /etc/docker/daemon.json manually."
        )


def _login_registry(host: str, username: str, password: str) -> None:
    console.print(f"  Logging in to registry {host}...")
    _configure_insecure_registry(host)
    result = subprocess.run(
        ["docker", "login", host, "-u", username, "--password-stdin"],
        input=password,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(
            f"[bold red]Error:[/] docker login failed:\n  {result.stderr.strip()}"
        )
        sys.exit(1)
    console.print("  [green]✓ Registry login successful[/]")


# ---------------------------------------------------------------------------
# Phase 1: Automated payment + license delivery
# ---------------------------------------------------------------------------

def _phase1() -> None:
    if HAS_RICH:
        console.print(Panel(
            "[bold cyan]Aegis-ETL Setup Wizard[/]\n\n"
            "This wizard will configure your Aegis-ETL deployment.\n"
            "You will be directed to a payment page to activate your license.",
            title="Welcome",
            border_style="cyan",
        ))
    else:
        print("\n=== Aegis-ETL Setup Wizard ===\n")

    install_dir = Path.cwd()

    # --- Prerequisites ---
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

    # --- Check for saved order (resume after interruption) ---
    saved_state = _load_order_state()
    if saved_state:
        order_id = saved_state["order_id"]
        fingerprint = saved_state["fingerprint"]
        payment_url = saved_state["payment_url"]
        console.print(
            f"\n[bold yellow]Resuming previous order:[/] {order_id}\n"
            f"  Payment URL: {payment_url}\n"
        )
        if not _confirm("  Open payment page and continue waiting?", default=True):
            sys.exit(0)
        webbrowser.open(payment_url)
        creds = _poll_for_license(order_id)
        _finalize(creds, fingerprint, install_dir)
        return

    # --- Service configuration ---
    console.print("\n[bold]Step 1: Service Configuration[/]")

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

    # --- Generate secrets ---
    console.print("\n[bold]Step 2: Generating Secrets[/]")

    postgres_password = _generate_secret(24)
    ingest_api_key = _generate_secret(32)
    admin_api_key = _generate_secret(32)
    pii_vault_key = _generate_hex_secret(32) if pii_enabled else ""

    console.print("  [green]✓ All secrets generated[/]")

    # --- Write partial .env ---
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
        "# --- Licensing (written after payment) ---",
        "# AEGIS_VENDOR_SECRET=",
        "# LICENSE_KEY=",
        "",
    ]

    env_content = "\n".join(env_lines)
    env_path.write_text(env_content)
    os.chmod(ENV_FILE, 0o600)
    console.print(f"  [green]✓ {ENV_FILE} written[/]")

    # --- Compute fingerprint locally ---
    console.print("\n[bold]Step 3: Computing Installation Fingerprint[/]")
    fingerprint = _compute_fingerprint(install_dir)
    console.print(f"  [green]✓ Fingerprint:[/] {fingerprint[:16]}...")

    # --- Create order ---
    console.print("\n[bold]Step 4: Creating Payment Order[/]")
    order = _create_order(fingerprint)
    order_id = order["order_id"]
    payment_url = order["payment_url"]

    _save_order_state({
        "order_id": order_id,
        "fingerprint": fingerprint,
        "payment_url": payment_url,
    })

    console.print(
        f"\n  [bold]Payment URL:[/] {payment_url}\n"
        f"  Opening in browser..."
    )
    webbrowser.open(payment_url)

    # --- Poll for license ---
    console.print("\n[bold]Step 5: Waiting for Payment Confirmation[/]")
    console.print("  Complete payment in your browser, then return here.\n")
    creds = _poll_for_license(order_id)

    _finalize(creds, fingerprint, install_dir)


def _finalize(creds: dict, fingerprint: str, install_dir: Path) -> None:
    """After payment: login registry, pull images, write .env, start services."""

    # --- Registry login ---
    console.print("\n[bold]Step 6: Registry Authentication[/]")
    registry_host = creds["registry_host"]
    _login_registry(
        registry_host,
        creds["registry_username"],
        creds["registry_password"],
    )

    # --- Pull images ---
    console.print("\n[bold]Step 7: Pulling Docker Images[/]")
    console.print("  This may take several minutes on first run...")
    pull_result = subprocess.run(["docker", "compose", "pull"], capture_output=False)
    if pull_result.returncode != 0:
        console.print("[bold red]Error:[/] docker compose pull failed.")
        sys.exit(1)
    console.print("  [green]✓ Images pulled[/]")

    # --- Write credentials to .env ---
    env_path = Path(ENV_FILE)
    env_content = env_path.read_text()
    env_content = env_content.replace(
        "# AEGIS_VENDOR_SECRET=",
        f"AEGIS_VENDOR_SECRET={creds['aegis_vendor_secret']}",
    )
    env_content = env_content.replace(
        "# LICENSE_KEY=",
        f"LICENSE_KEY={creds['license_key']}",
    )
    env_content += (
        f"\n# --- Registry ---\n"
        f"REGISTRY_HOST={registry_host}\n"
    )
    env_path.write_text(env_content)
    os.chmod(ENV_FILE, 0o600)
    console.print("  [green]✓ License credentials written to .env[/]")

    # --- Start services ---
    console.print("\n[bold]Step 8: Starting Aegis-ETL[/]")
    start_result = subprocess.run(["docker", "compose", "up", "-d"], capture_output=False)
    if start_result.returncode != 0:
        console.print("[bold red]Error:[/] docker compose up failed.")
        sys.exit(1)

    # --- Clean up order state ---
    _delete_order_state()

    console.print(
        Panel(
            "[bold green]Aegis-ETL is running![/]\n\n"
            "  API:    http://localhost:8000\n"
            "  Health: http://localhost:8000/health\n"
            "  Docs:   http://localhost:8000/docs (if Swagger enabled)\n\n"
            "CLI commands:\n"
            "  [cyan]python cli/aegis.py status[/]      — View job queue\n"
            "  [cyan]python cli/aegis.py ingest FILE[/] — Ingest a document\n"
            "  [cyan]python cli/aegis.py logs[/]        — Tail container logs\n"
            "  [cyan]python cli/aegis.py backup[/]      — Backup database",
            title="Setup Complete",
            border_style="green",
        )
    )


# ---------------------------------------------------------------------------
# Phase 2: Manual fallback — enter credentials directly
# ---------------------------------------------------------------------------

def _phase2() -> None:
    """Phase 2: manual license entry fallback. Used if Phase 1 polling was missed."""
    env_path = Path(ENV_FILE)

    if not env_path.exists():
        console.print(
            "[bold red]Error:[/] .env file not found. Run Phase 1 first:\n"
            "  python cli/setup.py"
        )
        sys.exit(1)

    if HAS_RICH:
        console.print(Panel(
            "[bold cyan]Aegis-ETL Setup — Manual License Entry[/]\n\n"
            "Enter the credentials provided by your Aegis vendor.",
            title="Complete Setup",
            border_style="cyan",
        ))
    else:
        print("\n=== Aegis-ETL Setup — Manual License Entry ===\n")

    vendor_secret = _prompt("  Enter AEGIS_VENDOR_SECRET", password=True)
    if len(vendor_secret) < 32:
        console.print("[bold red]Error:[/] AEGIS_VENDOR_SECRET must be at least 32 characters.")
        sys.exit(1)

    license_key = _prompt("  Enter LICENSE_KEY", password=True)
    if not license_key:
        console.print("[bold red]Error:[/] LICENSE_KEY cannot be empty.")
        sys.exit(1)

    registry_host = _prompt("  Enter REGISTRY_HOST (e.g. 161.97.93.228:5000)")
    registry_username = _prompt("  Enter REGISTRY_USERNAME")
    registry_password = _prompt("  Enter REGISTRY_PASSWORD", password=True)

    env_content = env_path.read_text()
    env_content = env_content.replace("# AEGIS_VENDOR_SECRET=", f"AEGIS_VENDOR_SECRET={vendor_secret}")
    env_content = env_content.replace("# LICENSE_KEY=", f"LICENSE_KEY={license_key}")
    env_content += f"\n# --- Registry ---\nREGISTRY_HOST={registry_host}\n"
    env_path.write_text(env_content)
    os.chmod(ENV_FILE, 0o600)
    console.print("  [green]✓ .env updated[/]")

    _login_registry(registry_host, registry_username, registry_password)

    console.print("\n[bold]Pulling images...[/]")
    subprocess.run(["docker", "compose", "pull"], check=True)

    console.print("\n[bold]Starting Aegis-ETL...[/]")
    start_result = subprocess.run(["docker", "compose", "up", "-d"], capture_output=False)
    if start_result.returncode != 0:
        console.print("[bold red]Error:[/] docker compose up failed.")
        sys.exit(1)

    _delete_order_state()
    console.print("[bold green]Done! Aegis-ETL is running.[/]")


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
