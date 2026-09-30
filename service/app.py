"""Local, token-authenticated diagnostic archive and investigation UI."""

from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape
from io import BytesIO
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid
import zipfile
import zlib

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse

from .analysis import analyze_crash, analyze_events, elf_sections, verify_elf

MAX_UPLOAD = 20 * 1024 * 1024
MAX_ELF = 16 * 1024 * 1024
MAX_DUMP = 65536
MAX_LOG_LINES = 10000
MAX_LOG_LINE = 4096
LOG_FILE = re.compile(r"zephyr\.\d{4}")
LOG_LINE = re.compile(r"^\[(?P<timestamp>[^]]+)]\s+<(?P<level>[^>]+)>\s+"
                      r"(?P<module>[^:]+):\s*(?P<message>.*)$")
SIMPLE_FILES = {"manifest.json", "zephyr.elf", "crash.bin", "meta.txt",
                "events.ndjson", "recent.log"}

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY, upload_sha TEXT NOT NULL UNIQUE,
  device TEXT NOT NULL, fingerprint TEXT NOT NULL, build TEXT NOT NULL,
  elf_sha TEXT NOT NULL, dump_sha TEXT, created TEXT NOT NULL,
  issue_key TEXT, reason TEXT, explanation TEXT, pc TEXT, lr TEXT,
  trace TEXT, event_count INTEGER NOT NULL, log_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  report_id TEXT NOT NULL, seq INTEGER NOT NULL, data TEXT NOT NULL,
  FOREIGN KEY (report_id) REFERENCES reports(id)
);
CREATE TABLE IF NOT EXISTS logs (
  report_id TEXT NOT NULL, seq INTEGER NOT NULL, data TEXT NOT NULL,
  FOREIGN KEY (report_id) REFERENCES reports(id)
);
CREATE INDEX IF NOT EXISTS reports_by_device ON reports(device, created DESC);
CREATE INDEX IF NOT EXISTS reports_by_issue ON reports(issue_key, created DESC);
"""


def _archive_files(body: bytes) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(BytesIO(body)) as archive:
            names = archive.namelist()
            if len(names) > 20 or len(names) != len(set(names)):
                raise ValueError("Too many or duplicate archive entries")
            result = {}
            total = 0
            for info in archive.infolist():
                name = info.filename
                if name not in SIMPLE_FILES and not LOG_FILE.fullmatch(name):
                    raise ValueError(f"Unsupported archive entry: {name}")
                if info.is_dir() or info.file_size > (MAX_ELF if name == "zephyr.elf" else
                                                    MAX_DUMP if name == "crash.bin" else
                                                    1024 * 1024):
                    raise ValueError(f"Oversized/invalid archive entry: {name}")
                total += info.file_size
                if total > MAX_UPLOAD:
                    raise ValueError("Uncompressed archive exceeds limit")
                result[name] = archive.read(info)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, EOFError,
            NotImplementedError, zlib.error) as exc:
        raise ValueError(f"Invalid ZIP archive: {exc}") from exc
    if not {"manifest.json", "zephyr.elf"}.issubset(result):
        raise ValueError("Archive requires manifest.json and unstripped zephyr.elf")
    if ("crash.bin" in result) != ("meta.txt" in result):
        raise ValueError("Crash dump and metadata must be provided together")
    return result


def _logs(files: dict[str, bytes]) -> list[dict]:
    entries = []
    for name in sorted(files):
        if not LOG_FILE.fullmatch(name):
            continue
        for raw in BytesIO(files[name]):
            if len(entries) >= MAX_LOG_LINES:
                raise ValueError(f"Log line count exceeds {MAX_LOG_LINES}")
            raw = raw.rstrip(b"\r\n")
            if not raw:
                continue
            if len(raw) > MAX_LOG_LINE:
                raise ValueError(f"Log line exceeds {MAX_LOG_LINE} bytes")
            text = raw.decode("utf-8", errors="replace")
            match = LOG_LINE.match(text)
            entries.append({"file": name, "timestamp": match["timestamp"] if match else None,
                            "level": match["level"] if match else None,
                            "module": match["module"] if match else None,
                            "message": match["message"] if match else text})
    return entries


def _prepare(files: dict[str, bytes], folder: Path) -> tuple[dict, list[dict], list[dict]]:
    try:
        manifest = json.loads(files["manifest.json"])
        status = manifest["status"]
        fingerprint = status["fingerprint"]
        device = manifest["device"]
        build = status["build"]
        if (not isinstance(device, str) or not 1 <= len(device) <= 100 or
                not isinstance(build, str) or not 1 <= len(build) <= 128):
            raise ValueError("Invalid device/build metadata")
        elf_sha = verify_elf(files["zephyr.elf"], fingerprint)
        if manifest.get("elf_sha256") != elf_sha:
            raise ValueError("ELF SHA-256 does not match the upload manifest")
        sections = elf_sections(files["zephyr.elf"])
        events = analyze_events(files.get("events.ndjson", b""), folder / "zephyr.elf",
                                sections, fingerprint)
        logs = _logs(files)
        crash = None
        dump_sha = None
        if "crash.bin" in files:
            meta = json.loads(files["meta.txt"])
            if meta.get("fingerprint") != fingerprint or meta.get("dump_size") != len(files["crash.bin"]):
                raise ValueError("Coredump build fingerprint/size mismatch")
            if status.get("crash_seq") != meta.get("crash_seq"):
                raise ValueError("Coredump sequence changed during collection")
            dump_sha = hashlib.sha256(files["crash.bin"]).hexdigest()
            crash = analyze_crash(files["crash.bin"], folder / "zephyr.elf", sections,
                                  folder / "crash.bin")
        issue_key = None
        if crash:
            # Group the same reason/function across firmware releases.
            location = crash["lr_call_site"] if "via " in crash["explanation"] else crash["pc"]
            source = Path(location["file"]).parts[-2:] if location.get("file") else ()
            issue_key = hashlib.sha256(
                f"arm:{crash['reason']}:{'/'.join(source)}:{crash['issue_function']}".encode()
            ).hexdigest()[:20]
        report = {"device": device, "fingerprint": fingerprint, "build": build,
                  "elf_sha": elf_sha, "dump_sha": dump_sha,
                  "created": datetime.now(timezone.utc).isoformat(),
                  "issue_key": issue_key, "reason": crash["reason"] if crash else None,
                  "explanation": crash["explanation"] if crash else None,
                  "pc": json.dumps(crash["pc"]) if crash else None,
                  "lr": json.dumps(crash["lr_call_site"]) if crash else None,
                  "trace": crash["gdb_backtrace"] if crash else None,
                  "event_count": len(events), "log_count": len(logs)}
        return report, events, logs
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Malformed manifest/metadata: {exc}") from exc


def create_app(data_dir: str | Path | None = None, token: str | None = None) -> FastAPI:
    secret = token if token is not None else os.getenv("DIAG_SERVICE_TOKEN", "")
    if len(secret) < 16:
        raise RuntimeError("DIAG_SERVICE_TOKEN must contain at least 16 characters")
    data = Path(data_dir or os.getenv("DIAG_SERVICE_DATA", "service/data")).resolve()
    data.mkdir(parents=True, exist_ok=True)
    (data / "reports").mkdir(exist_ok=True)
    db_path = data / "reports.sqlite3"

    @contextmanager
    def db():
        connection = sqlite3.connect(db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    with db() as connection:
        connection.executescript(SCHEMA)

    def auth(request: Request):
        header = request.headers.get("authorization", "")
        candidate = ""
        if header.startswith("Bearer "):
            candidate = header[7:]
        elif header.startswith("Basic "):
            try:
                username, password = base64.b64decode(header[6:], validate=True).decode().split(":", 1)
                candidate = password if username == "admin" else ""
            except (ValueError, UnicodeError):
                pass
        if not hmac.compare_digest(candidate, secret):
            raise HTTPException(401, "Authentication required",
                                headers={"WWW-Authenticate": 'Basic realm="Diagnostics"'})

    app = FastAPI(title="Zephyr Diagnostic Service", version="0.1.0")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/v1/reports", dependencies=[Depends(auth)], status_code=201)
    async def upload(request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/zip":
            raise HTTPException(415, "Expected Content-Type: application/zip")
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > MAX_UPLOAD:
                raise HTTPException(413, "Archive exceeds 20 MiB limit")
            content.extend(chunk)
        upload_sha = hashlib.sha256(content).hexdigest()
        with db() as connection:
            existing = connection.execute("SELECT id FROM reports WHERE upload_sha=?", (upload_sha,)).fetchone()
            if existing:
                return {"id": existing["id"], "duplicate": True}
        try:
            files = await run_in_threadpool(_archive_files, bytes(content))
            report_id = uuid.uuid4().hex
            folder = data / "reports" / report_id
            folder.mkdir(mode=0o700)
            try:
                for name, body in files.items():
                    (folder / name).write_bytes(body)
                report, events, logs = await run_in_threadpool(_prepare, files, folder)
                with db() as connection:
                    connection.execute(
                        "INSERT INTO reports (id,upload_sha,device,fingerprint,build,elf_sha,dump_sha,"
                        "created,issue_key,reason,explanation,pc,lr,trace,event_count,log_count) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (report_id, upload_sha, *(report[key] for key in (
                            "device", "fingerprint", "build", "elf_sha", "dump_sha", "created",
                            "issue_key", "reason", "explanation", "pc", "lr", "trace",
                            "event_count", "log_count"))))
                    connection.executemany("INSERT INTO events VALUES (?,?,?)",
                                           ((report_id, i, json.dumps(event))
                                            for i, event in enumerate(events)))
                    connection.executemany("INSERT INTO logs VALUES (?,?,?)",
                                           ((report_id, i, json.dumps(log))
                                            for i, log in enumerate(logs)))
            except Exception:
                shutil.rmtree(folder)  # Only the newly allocated UUID folder.
                raise
        except sqlite3.IntegrityError:
            with db() as connection:
                existing = connection.execute(
                    "SELECT id FROM reports WHERE upload_sha=?", (upload_sha,)
                ).fetchone()
            if existing:
                return {"id": existing["id"], "duplicate": True}
            raise HTTPException(409, "Concurrent report insertion conflict")
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": report_id, "duplicate": False, "issue_key": report["issue_key"],
                "reason": report["explanation"], "event_count": len(events), "log_count": len(logs)}

    @app.get("/api/v1/reports", dependencies=[Depends(auth)])
    def reports(limit: int = 50, offset: int = 0):
        if not 1 <= limit <= 200 or offset < 0:
            raise HTTPException(400, "Invalid pagination")
        with db() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT id,device,fingerprint,build,created,issue_key,reason,explanation,"
                "event_count,log_count FROM reports ORDER BY created DESC LIMIT ? OFFSET ?",
                (limit, offset))]

    def report_row(report_id: str):
        if not re.fullmatch(r"[a-f0-9]{32}", report_id):
            raise HTTPException(404, "Unknown report")
        with db() as connection:
            row = connection.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Unknown report")
        return row

    @app.get("/api/v1/reports/{report_id}", dependencies=[Depends(auth)])
    def detail(report_id: str, limit: int = 200, offset: int = 0):
        row = report_row(report_id)
        if not 1 <= limit <= 1000 or offset < 0:
            raise HTTPException(400, "Invalid pagination")
        with db() as connection:
            events = [json.loads(r[0]) for r in connection.execute(
                "SELECT data FROM events WHERE report_id=? ORDER BY seq LIMIT ? OFFSET ?",
                (report_id, limit, offset))]
            logs = [json.loads(r[0]) for r in connection.execute(
                "SELECT data FROM logs WHERE report_id=? ORDER BY seq LIMIT ? OFFSET ?",
                (report_id, limit, offset))]
        result = dict(row)
        for key in ("pc", "lr"):
            result[key] = json.loads(result[key]) if result[key] else None
        result["events"] = events
        result["logs"] = logs
        return result

    @app.get("/api/v1/reports/{report_id}/files/{filename}", dependencies=[Depends(auth)])
    def artifact(report_id: str, filename: str):
        report_row(report_id)
        if filename not in SIMPLE_FILES and not LOG_FILE.fullmatch(filename):
            raise HTTPException(404, "Unknown artifact")
        file = data / "reports" / report_id / filename
        if not file.is_file():
            raise HTTPException(404, "Unknown artifact")
        return FileResponse(file, filename=filename)

    @app.get("/api/v1/issues", dependencies=[Depends(auth)])
    def issues():
        with db() as connection:
            return [dict(r) for r in connection.execute(
                "SELECT issue_key,reason,explanation,COUNT(*) AS reports,MAX(created) AS last_seen "
                "FROM reports WHERE issue_key IS NOT NULL GROUP BY issue_key "
                "ORDER BY last_seen DESC")]

    @app.get("/api/v1/devices", dependencies=[Depends(auth)])
    def devices():
        with db() as connection:
            return [dict(r) for r in connection.execute(
                "SELECT device,COUNT(*) AS reports,MAX(created) AS last_seen "
                "FROM reports GROUP BY device ORDER BY last_seen DESC")]

    @app.get("/api/v1/releases", dependencies=[Depends(auth)])
    def releases():
        with db() as connection:
            return [dict(r) for r in connection.execute(
                "SELECT fingerprint,build,COUNT(*) AS reports,MAX(created) AS last_seen "
                "FROM reports GROUP BY fingerprint,build ORDER BY last_seen DESC")]

    style = ("<style>body{font:16px system-ui;max-width:1100px;margin:3rem auto;"
             "background:#101820;color:#eee}a{color:#78d4ff}table{width:100%;"
             "border-collapse:collapse}td,th{padding:.55rem;border-bottom:1px solid #445}"
             "pre{overflow:auto;background:#202c37;padding:1rem;white-space:pre-wrap}"
             "small{color:#aaa}</style>")

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def index():
        rows = reports()
        cells = "".join(
            f"<tr><td><a href='/reports/{r['id']}'>{escape(r['created'])}</a></td>"
            f"<td>{escape(r['device'])}</td><td>{escape(r['build'])}</td>"
            f"<td>{escape(r['explanation'] or 'Logs/metrics only')}</td>"
            f"<td>{r['event_count']}</td><td>{r['log_count']}</td></tr>" for r in rows)
        return "<!doctype html><title>Zephyr Diagnostics</title>" + style + (
            "<h1>Zephyr Diagnostics</h1><p>Reports, crash reasons, events and logs. "
            "API: <a href='/docs'>/docs</a></p><table><tr><th>Time</th><th>Device</th>"
            "<th>Build</th><th>Reason</th><th>Events</th><th>Logs</th></tr>" + cells + "</table>")

    @app.get("/reports/{report_id}", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def page(report_id: str):
        item = detail(report_id, limit=1000)
        event_rows = "".join(
            f"<tr><td>{escape(str(e.get('level')))}</td><td>{escape(str(e.get('category')))}</td>"
            f"<td>{escape(str(e.get('code')))}</td><td>{escape(str(e.get('value')))}</td>"
            f"<td>{escape(str(e.get('location') or 'different build'))}</td></tr>"
            for e in item["events"])
        log_rows = "".join(
            f"<tr><td>{escape(str(l['timestamp']))}</td><td>{escape(str(l['level']))}</td>"
            f"<td>{escape(str(l['module']))}</td><td>{escape(l['message'])}</td></tr>"
            for l in item["logs"])
        return "<!doctype html><title>Diagnostic Report</title>" + style + (
            f"<p><a href='/'>← Reports</a></p><h1>{escape(item['device'])}</h1>"
            f"<p>Build {escape(item['build'])} · {escape(item['fingerprint'])}</p>"
            f"<h2>{escape(item['explanation'] or 'Logs/metrics only')}</h2>"
            f"<p>Reason: {escape(item['reason'] or 'No crash')} · "
            f"Issue: {escape(item['issue_key'] or 'none')}</p>"
            f"<pre>{escape(item['trace'] or json.dumps({'PC': item['pc'], 'LR': item['lr']}, indent=2))}</pre>"
            "<h2>Events (ELF-matched only)</h2><table><tr><th>Level</th><th>Category</th>"
            f"<th>Code</th><th>Value</th><th>Location</th></tr>{event_rows}</table>"
            "<h2>Zephyr logs (text has no PC)</h2><table><tr><th>Time</th><th>Level</th>"
            f"<th>Module</th><th>Message</th></tr>{log_rows}</table>")

    return app


# `uvicorn service.app:app` requires a token; never silently start open access.
app = create_app() if os.getenv("DIAG_SERVICE_TOKEN") else None
