# Aegis-ETL Installer

Enterprise Document Ingestion Platform — quick-start installer.

## Prerequisites

| Requirement | Minimum Version |
|---|---|
| Docker | 20.10+ |
| Docker Compose | v2.0+ |
| Python | 3.8+ |

## Quick Install (Linux / macOS)

```bash
curl -sL https://raw.githubusercontent.com/kayomarz97/aegis-etl-installer/main/install.sh | bash
```

## Quick Install (Windows PowerShell)

```powershell
irm https://raw.githubusercontent.com/kayomarz97/aegis-etl-installer/main/install.ps1 | iex
```

## Manual Install

```bash
git clone https://github.com/kayomarz97/aegis-etl-installer.git
cd aegis-etl-installer
pip install -r cli/requirements.txt
python cli/setup.py
```

## Setup Flow

### Phase 1: Initial Configuration

The setup wizard will:

1. Validate your **GHCR access token** (provided by Aegis support)
2. Ask configuration questions (PII masking, worker count, vector dimensions)
3. Generate secure API keys and database passwords automatically
4. Pull the Docker images from the private registry
5. Display your **installation fingerprint**

> **Send your fingerprint to `support@aegis-etl.com`** to receive your license key.

### Phase 2: Activate License

After receiving your LICENSE_KEY and AEGIS_VENDOR_SECRET from Aegis support:

```bash
python cli/setup.py --complete
```

This adds the license credentials and starts all services.

## CLI Commands

After setup, use the Aegis CLI to manage your deployment:

```bash
# Set your API keys (printed during setup)
export AEGIS_INGEST_KEY=<your-ingest-key>
export AEGIS_ADMIN_KEY=<your-admin-key>

# Ingest a document
python cli/aegis.py ingest document.pdf

# Ingest a folder of documents
python cli/aegis.py ingest ./documents/

# View job queue status
python cli/aegis.py status

# Tail container logs
python cli/aegis.py logs

# Backup the database
python cli/aegis.py backup

# Restore from backup (destructive)
python cli/aegis.py restore aegis_backup_20260407.sql.gz

# Check version
python cli/aegis.py version
```

## Environment Variables

| Variable | Purpose | Set by |
|---|---|---|
| `AEGIS_API_URL` | Backend URL (default: `http://localhost:8000`) | User |
| `AEGIS_INGEST_KEY` | API key for `/ingest` endpoint | Setup wizard |
| `AEGIS_ADMIN_KEY` | API key for `/admin` endpoints | Setup wizard |
| `AEGIS_PROJECT_DIR` | Directory containing docker-compose.yml | User (optional) |

## Support

Contact `support@aegis-etl.com` for licensing, configuration, and technical support.
