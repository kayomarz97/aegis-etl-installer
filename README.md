# Aegis-ETL

**On-premise enterprise document ingestion. Your data never leaves your server.**

Aegis-ETL reads your documents, finds and encrypts any personal information, converts
the content into AI-searchable vectors, and stores everything in your own private
database — all running inside Docker on your machine.

---

## What It Does

Think of it as a private filing system that reads every document you give it and makes
the content searchable — without sending anything to the cloud.

```
You upload a file
    ↓
System reads it (PDF, Word, Excel, email, etc.)
    ↓
Personal info (names, emails, phone numbers) is detected and encrypted
    ↓
Content is split into searchable chunks
    ↓
AI converts each chunk into numbers (embeddings) for semantic search
    ↓
Everything is stored in your private database
    ↓
You query it through the API
```

---

## Prerequisites

| Requirement | Minimum | How to check |
|---|---|---|
| Docker | 20.10+ | `docker --version` |
| Docker Compose | v2.0+ | `docker compose version` |
| Python | 3.9+ | `python3 --version` |

**Linux:** Works on any modern distribution.
**macOS:** Requires Docker Desktop.
**Windows:** Requires Docker Desktop with WSL2 backend.

---

## Quick Install

```bash
curl -sL https://raw.githubusercontent.com/kayomarz97/aegis-etl-installer/master/install.sh | bash
```

The installer downloads the required files, runs the setup wizard, handles payment,
and starts all services automatically. No manual configuration required.

---

## What the Setup Wizard Does

The wizard runs interactively in your terminal and handles everything:

### Step 1 — Configuration questions

It asks you a few questions about how you want the system to behave:

| Question | Default | What it controls |
|---|---|---|
| Enable PII masking? | Yes | Whether personal information is detected and encrypted |
| Worker concurrency | 4 | How many documents can be processed at the same time |
| Max OCR jobs | 2 | How many scanned PDFs can be read simultaneously |
| Vector dimensions | 768 | Precision of AI search (higher = more accurate, more storage) |
| Enable Swagger docs? | No | Interactive API browser at `/docs` |

### Step 2 — Secret generation

The wizard generates every password and API key automatically using a cryptographically
secure random generator. You do not need to invent any passwords.

### Step 3 — License activation

The wizard computes a **hardware fingerprint** — a unique identifier tied to your
server — and opens a payment page in your browser. After payment, your license key
is delivered automatically to your terminal within seconds. No email, no waiting.

> **In plain English:** The license is mathematically tied to your specific server.
> It cannot be copied to another machine. If you move to a new server, contact support
> for a transfer.

### Step 4 — Services start

The wizard logs into the private Docker registry, downloads the application image,
and starts all three services automatically.

> **Note:** On first run, the AI models download in the background (about 2 GB total,
> taking 5–20 minutes depending on your connection). The API starts responding once
> the models are fully loaded. Watch progress with:
> ```bash
> docker logs -f aegis-etl-ollama-1
> ```

---

## Resuming an Interrupted Setup

If the wizard is interrupted after payment (e.g., browser closed, network dropped):

```bash
cd ~/aegis-etl
python3 cli/setup.py
```

It detects the saved payment state and resumes automatically from where it left off —
no need to pay again.

---

## Verifying Everything Works

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{"status": "ok"}
```

If you see `"status": "degraded"`, the AI models are still loading — wait a few
minutes and try again.

---

## Uploading Documents

### From the terminal

```bash
curl -X POST http://localhost:8000/ingest \
  -H "X-API-KEY: your-ingest-key" \
  -F "file=@/path/to/document.pdf"
```

Replace `your-ingest-key` with the `INGEST_API_KEY` value printed during setup
(also stored in `~/aegis-etl/.env`).

### Response

```json
{"job_id": 42, "status": "PENDING"}
```

The system processes documents in the background. Use the job ID to check status:

```bash
curl http://localhost:8000/admin/override \
  -X POST \
  -H "X-API-KEY: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{"job_id": 42}'
```

### Job statuses

| Status | Meaning |
|---|---|
| `PENDING` | Queued, waiting to be processed |
| `PROCESSING` | Being read and indexed right now |
| `DONE` | Stored and searchable |
| `FAILED` | Something went wrong — will retry automatically (up to 3 times) |
| `FAILED_MAX_RETRIES` | Gave up after 3 attempts — check logs for details |

---

## Supported File Types

| Format | Extensions | How it's read |
|---|---|---|
| PDF (text-based) | `.pdf` | Direct text extraction |
| PDF (scanned) | `.pdf` | OCR (optical character recognition) |
| Word document | `.docx`, `.doc` | Structured text extraction |
| Excel spreadsheet | `.xlsx`, `.xls` | Cell-by-cell extraction |
| Outlook email | `.msg` | Headers + body, no GPL libraries |
| Email file | `.eml` | Headers + body |
| Plain text | `.txt` | Direct |
| CSV | `.csv` | Row-by-row |
| Markdown | `.md` | Direct |

Maximum file size: **100 MB**.

---

## CLI Tools

After setup, a command-line tool is available for managing your deployment:

```bash
# View current job queue
python3 cli/aegis.py status

# Ingest a single file
python3 cli/aegis.py ingest report.pdf

# Ingest a whole folder (processes files one by one)
python3 cli/aegis.py ingest ./documents/

# Watch live logs from all services
python3 cli/aegis.py logs

# Back up the database to a compressed file
python3 cli/aegis.py backup

# Restore from a backup (replaces current data — irreversible)
python3 cli/aegis.py restore aegis_backup_20260408.sql.gz

# Check software version
python3 cli/aegis.py version
```

Set your keys as environment variables so you do not need to type them each time:

```bash
export AEGIS_INGEST_KEY=<value from .env>
export AEGIS_ADMIN_KEY=<value from .env>
```

---

## Security

### API key separation

Two completely separate keys control two completely separate routes:

| Key | Route | What it can do |
|---|---|---|
| `INGEST_API_KEY` | `/ingest` only | Upload documents |
| `ADMIN_API_KEY` | `/admin/*` only | Inspect jobs, trigger retries |

The keys are not interchangeable. An ingest key cannot access admin endpoints and
vice versa. Give the ingest key to any system that uploads documents. Keep the admin
key strictly private.

> **In plain English:** Even if someone gets your upload key, they cannot see job
> details, trigger retries, or access any administrative function.

### PII encryption vault

When PII masking is enabled, personal information (names, emails, phone numbers, IDs,
SSNs, credit card numbers, passport numbers, IP addresses, IBAN codes) is:

1. **Detected** by an AI named entity recognition model (spaCy + Presidio)
2. **Replaced** in the stored text with placeholder tokens like `[PERSON_1]`
3. **Encrypted** using Fernet symmetric encryption and stored in a separate vault table
4. **Never sent to the database in plaintext** — encryption happens in Python before any SQL

The encryption key (`PII_VAULT_KEY`) is never sent to PostgreSQL. The database only
ever sees opaque encrypted bytes. Even with full database access, PII cannot be read
without the vault key.

> **In plain English:** If someone steals your database backup, they cannot read any
> names, emails, or personal details from it. The actual data is locked in an encrypted
> vault that requires a separate key to open.

### Rate limiting

The `/ingest` endpoint is rate-limited to 30 requests per minute per API key. Excess
requests receive a `429 Too Many Requests` response. This prevents accidental or
deliberate flooding of the processing queue.

### Path traversal prevention

The system never uses uploaded filenames to create files on disk. Filenames are
discarded at the boundary. Only the file content is processed, and it is written to
a path generated internally that is verified to stay within the data directory.

> **In plain English:** You cannot trick the system into overwriting system files by
> naming your upload `../../etc/passwd` — the filename is ignored entirely.

### License hardware binding

Your license is tied to three things about your server:

- A randomly generated installation ID (stored in a Docker volume, survives restarts)
- A pinned hostname (`aegis-node`, set in the compose file)
- The number of CPU cores

The license key is `HMAC-SHA256(vendor_secret, fingerprint)`. This means:
- The key is mathematically derived from your hardware — it cannot be copied to another machine
- Timing-safe comparison (`hmac.compare_digest`) prevents timing attacks
- If the vendor secret or fingerprint does not match, the container exits before starting

### CORS control

Cross-Origin Resource Sharing headers are configurable. Default is `["*"]` (permissive,
fine for internal networks). Restrict in production:

```
CORS_ALLOWED_ORIGINS=["https://your-app.example.com"]
```

---

## Architecture

```
Your server
│
├── Container: db  (PostgreSQL 16 + pgvector)
│   ├── ingestion_jobs table   — job queue with status, retries, stage flags
│   ├── document_chunks table  — text chunks with embeddings (HNSW vector index)
│   └── pii_vault table        — encrypted personal information
│
├── Container: ollama  (AI model server)
│   ├── gemma4:e2b        — language model (~1.5 GB), understands document content
│   └── embeddinggemma:300m — embedding model (~622 MB), converts text to vectors
│
└── Container: app  (Aegis-ETL)
    │
    ├── License gate       — validates HMAC before anything else starts
    ├── /ingest            — accepts uploads, validates MIME type, queues jobs
    ├── /health            — reports system status including ollama liveness
    ├── /admin/override    — inspect jobs, trigger retries, adjust priority
    │
    └── Background worker
          ├── Claims up to 4 jobs at once (configurable)
          ├── Runs them concurrently using asyncio
          │
          ├── Stage 1: EXTRACT
          │   Text-based PDF, Word, Excel, email → structured text
          │   Scanned PDF → OCR (Tesseract + pdf2image, limited to 2 concurrent)
          │
          ├── Stage 2: MASK
          │   Presidio NER detects 9 entity types
          │   Fernet encryption, stored in pii_vault
          │   masked_text → Stage 3
          │
          ├── Stage 3: EMBED
          │   Split into 1000-char chunks (200 overlap)
          │   LiteLLM → Ollama embeddinggemma:300m → 768-dim vectors
          │   Batch insert into document_chunks (transactional)
          │
          └── Housekeeping (every 60s)
                ├── Reset stuck jobs (heartbeat > 5 min stale)
                ├── Purge dead-letter jobs (FAILED_MAX_RETRIES older than 7 days)
                └── VACUUM ANALYZE ingestion_jobs (database maintenance)
```

All inter-service traffic stays inside Docker's `aegis-net` bridge network.
Only the app's port (default 8000) is exposed to the host.

---

## Optional Configuration

Add any of these to `~/aegis-etl/.env` and restart to apply:

```bash
docker compose down && docker compose up -d
```

| Setting | Default | Effect |
|---|---|---|
| `PII_MASKING_ENABLED=false` | `true` | Disables PII detection. Use for industries like legal or construction where masking is not required. Changing after ingestion requires full re-ingest. |
| `SWAGGER_ENABLED=true` | `false` | Enables interactive API browser at `http://localhost:8000/docs`. Turn off in production. |
| `AEGIS_PORT=8001` | `8000` | Changes the port the API listens on. |
| `WORKER_CONCURRENCY=2` | `4` | Number of documents processed simultaneously. Reduce on low-RAM servers. |
| `MAX_CONCURRENT_OCR_JOBS=1` | `2` | Simultaneous scanned-PDF jobs. OCR is CPU-heavy — reduce if server is slow. |
| `CORS_ALLOWED_ORIGINS=["https://app.example.com"]` | `["*"]` | Restrict API access to specific origins in production. |
| `VECTOR_DIMENSIONS=384` | `768` | One-time choice. Lower = faster search, less storage. Cannot be changed after ingestion without wiping data. |

---

## Stopping and Starting

```bash
# Stop all services (data is preserved)
docker compose down

# Start again
docker compose up -d

# Stop and delete all data (irreversible)
docker compose down -v
```

---

## Troubleshooting

### "Invalid or mismatched license key"

The license key does not match this server. This can happen if you moved the Docker
volume to a different machine or changed the server's CPU count.

```bash
# Print your current hardware fingerprint
docker compose run --rm app python src/licensing/validator.py --diagnostic
```

Contact support with the fingerprint output.

### Health returns `"status": "degraded"`

The AI models are still loading. This is normal on first start and takes 5–20 minutes.
Watch progress:

```bash
docker logs -f aegis-etl-ollama-1
```

### Port 8000 already in use

Add `AEGIS_PORT=8001` to `~/aegis-etl/.env` and restart.

### Container keeps restarting

```bash
docker compose logs app --tail=50
```

The most common causes are a missing `.env` value or a wrong `LICENSE_KEY`.

### "Permission denied" on data directory

If using a custom bind-mount path, it must be owned by UID 999 (the internal service user):

```bash
sudo chown -R 999:999 /path/to/your/data-folder
```

### Jobs stuck in PROCESSING

The housekeeping process resets stuck jobs automatically every 5 minutes. If a job
has been `PROCESSING` for more than 5 minutes, it will be automatically moved back to
`PENDING` for retry. Check logs for the root cause:

```bash
docker compose logs app --tail=100 | grep "ERROR\|WARNING"
```

---

## Getting Help

Collect this before contacting support:

```bash
docker compose ps > status.txt
docker compose logs app --tail=100 > app-logs.txt
```

Check `app-logs.txt` to confirm it contains no API keys or passwords, then send it
along with a description of the problem.

Contact: *(support contact coming soon)*
