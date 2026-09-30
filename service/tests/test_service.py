from io import BytesIO
import json
from pathlib import Path
import zipfile

from fastapi.testclient import TestClient
import pytest

from service.app import create_app

TOKEN = "integration-test-token-12345"
ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "host/out/20260930T131704Z"
ASSERT_ARCHIVE = ROOT / "host/out/20260930T131224Z"
LOG_ARCHIVE = ROOT / "host/out/20260930T131603Z"


def zip_files(files: dict[str, bytes]):
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def real_files(folder=ARCHIVE):
    if not folder.is_dir():
        pytest.skip("Optional DK hardware archive is not present")
    return {path.name: path.read_bytes() for path in folder.iterdir()
            if path.name in {"manifest.json", "zephyr.elf", "crash.bin", "meta.txt",
                             "events.ndjson", "recent.log"} or
            path.name.startswith("zephyr.") and path.name[7:].isdigit()}


def client(tmp_path):
    return TestClient(create_app(tmp_path, token=TOKEN))


def credentials():
    return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/zip"}


def test_authentication_and_invalid_archive(tmp_path):
    app = client(tmp_path)
    assert app.get("/health").json() == {"status": "ok"}
    assert app.get("/api/v1/reports").status_code == 401
    assert app.post("/api/v1/reports", content=b"not zip").status_code == 401
    assert app.post("/api/v1/reports", headers={"Authorization": f"Bearer {TOKEN}"},
                    content=b"not zip").status_code == 415
    assert app.post("/api/v1/reports", headers=credentials(), content=b"not zip").status_code == 422
    assert app.post("/api/v1/reports", headers=credentials(),
                    content=zip_files({"../escaped": b"bad"})).status_code == 422
    assert not (tmp_path.parent / "escaped").exists()


def test_missing_artifacts_rejected(tmp_path):
    app = client(tmp_path)
    response = app.post("/api/v1/reports", headers=credentials(),
                        content=zip_files({"manifest.json": b"{}"}))
    assert response.status_code == 422


def test_real_archive_issue_and_replay(tmp_path):
    files = real_files()
    app = client(tmp_path)
    body = zip_files(files)
    result = app.post("/api/v1/reports", headers=credentials(), content=body)
    assert result.status_code == 201, result.text
    report_id = result.json()["id"]
    assert "Divide-by-zero UsageFault" in result.json()["reason"]
    assert app.post("/api/v1/reports", headers=credentials(), content=body).json() == {
        "id": report_id, "duplicate": True}
    detail = app.get(f"/api/v1/reports/{report_id}", headers=credentials()).json()
    assert detail["pc"]["function"] == "crash_div_by_zero"
    assert detail["pc"]["line"] == 32
    assert any(event["mismatched_build"] for event in detail["events"])
    assert any(event["location"] and event["location"]["function"] == "crash_schedule"
               for event in detail["events"])
    assert any(log["module"] == "bt_l2cap" for log in detail["logs"])
    assert app.get("/api/v1/issues", headers=credentials()).json()[0]["reports"] == 1
    assert app.get("/api/v1/devices", headers=credentials()).status_code == 200
    assert app.get("/api/v1/releases", headers=credentials()).status_code == 200
    assert app.get("/", headers=credentials()).status_code == 200
    assert app.get(f"/reports/{report_id}", headers=credentials()).status_code == 200
    assert app.get(f"/api/v1/reports/{report_id}/files/crash.bin",
                   headers=credentials()).content == files["crash.bin"]
    assert app.get(f"/api/v1/reports/{report_id}/files/../reports.sqlite3",
                   headers=credentials()).status_code in (404, 422)


def test_wrong_elf_and_wrong_dump_size_rejected(tmp_path):
    files = real_files()
    app = client(tmp_path)
    manifest = json.loads(files["manifest.json"])
    manifest["status"]["fingerprint"] = "0" * 20
    files["manifest.json"] = json.dumps(manifest).encode()
    response = app.post("/api/v1/reports", headers=credentials(), content=zip_files(files))
    assert response.status_code == 422
    files = real_files()
    meta = json.loads(files["meta.txt"])
    meta["dump_size"] += 1
    files["meta.txt"] = json.dumps(meta).encode()
    response = app.post("/api/v1/reports", headers=credentials(), content=zip_files(files))
    assert response.status_code == 422


def test_assertion_uses_application_caller_and_logs_work_without_dump(tmp_path):
    app = client(tmp_path)
    result = app.post("/api/v1/reports", headers=credentials(),
                      content=zip_files(real_files(ASSERT_ARCHIVE)))
    assert result.status_code == 201, result.text
    assert "crash_assert_fail" in result.json()["reason"]
    detail = app.get(f"/api/v1/reports/{result.json()['id']}", headers=credentials()).json()
    assert detail["lr"]["line"] == 40
    logs = app.post("/api/v1/reports", headers=credentials(),
                    content=zip_files(real_files(LOG_ARCHIVE)))
    assert logs.status_code == 201, logs.text
    assert logs.json()["issue_key"] is None
    assert logs.json()["event_count"] > 0


def test_html_escapes_uploaded_device_label(tmp_path):
    files = real_files()
    manifest = json.loads(files["manifest.json"])
    manifest["device"] = "<script>x</script>"
    files["manifest.json"] = json.dumps(manifest).encode()
    app = client(tmp_path)
    result = app.post("/api/v1/reports", headers=credentials(), content=zip_files(files))
    assert result.status_code == 201
    html = app.get("/", headers=credentials()).text
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html
