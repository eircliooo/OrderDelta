"""安全测试。SPEC §15。

原计划第十五节要求「对恶意或损坏文件进行失败测试」但没说测什么。这里逐条落实，
每个用例对应一种真实攻击面或一种会静默产出错误结果的输入。

**这些测试的价值在于失败时的行为**：必须是**显式拒绝 + 结构化错误码**，
不能是崩溃、不能是静默当成空文件、更不能是假装解析成功。
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.session import init_db, reset_engine
from app.domain.enums import ParseReasonCode, ParseStatus
from app.parsers.base import DocumentInput
from app.parsers.xlsx import XlsxParser
from tests.conftest import BASE_ITEMS, document_input, order_rows, write_xlsx

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    settings.ensure_dirs()
    reset_engine(f"sqlite:///{(tmp_path / 'data' / 'sec.sqlite3').as_posix()}")
    init_db()
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
    reset_engine(None)


def _project(client: TestClient) -> str:
    response = client.post("/api/v1/projects", json={"name": "安全测试"})
    assert response.status_code == 201
    return str(response.json()["id"])


def _upload(client: TestClient, project_id: str, name: str, payload: bytes, mime: str):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/v1/projects/{project_id}/documents",
        data={"role": "QUOTATION"},
        files={"file": (name, payload, mime)},
    )


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class TestUploadRejection:
    """扩展名 + MIME + 魔数三重校验。"""

    def test_拒绝xlsm含宏(self, client: TestClient) -> None:
        response = _upload(client, _project(client), "macro.xlsm", b"PK\x03\x04junk", XLSX_MIME)
        assert response.status_code == 400
        assert response.json()["error_code"] == "UNSUPPORTED_EXT"

    def test_拒绝旧版xls(self, client: TestClient) -> None:
        response = _upload(client, _project(client), "old.xls", _OLE2_MAGIC + b"junk", XLSX_MIME)
        assert response.status_code == 400
        assert response.json()["error_code"] == "UNSUPPORTED_EXT"

    def test_加密文件给出明确原因(self, client: TestClient) -> None:
        """加密的 OOXML 其实是 OLE2 容器。报「已加密」比报「损坏」有用得多。"""
        response = _upload(
            client, _project(client), "locked.xlsx", _OLE2_MAGIC + b"\x00" * 64, XLSX_MIME
        )
        assert response.status_code == 400
        body = response.json()
        assert body["error_code"] == "ENCRYPTED"
        assert "加密" in body["message"]

    def test_拒绝伪装扩展名(self, client: TestClient) -> None:
        response = _upload(client, _project(client), "evil.xlsx", b"MZ\x90\x00", XLSX_MIME)
        assert response.status_code == 400
        assert response.json()["error_code"] == "CORRUPT"

    def test_拒绝超大文件(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "max_upload_bytes", 1024)
        payload = b"PK\x03\x04" + b"0" * 4096
        response = _upload(client, _project(client), "big.xlsx", payload, XLSX_MIME)
        assert response.status_code == 400
        assert response.json()["error_code"] == "FILE_TOO_LARGE"

    def test_拒绝错误mime(self, client: TestClient) -> None:
        response = _upload(client, _project(client), "a.xlsx", b"PK\x03\x04", "text/html")
        assert response.status_code == 400
        assert response.json()["error_code"] == "UNSUPPORTED_MIME"


class TestPathTraversal:
    """**禁止用户控制服务器文件路径**（SPEC §15.1）。"""

    def test_文件名里的路径穿越不落地(self, client: TestClient, tmp_path: Path) -> None:
        project_id = _project(client)
        rows = order_rows(
            title="QUOTATION",
            doc_label="Quotation No.",
            doc_no="Q-1",
            date="2026-07-15",
            items=BASE_ITEMS,
            grand_total="3070.00",
        )
        payload = write_xlsx(tmp_path / "ok.xlsx", {"Q": rows}).read_bytes()

        evil_name = "../../../../Windows/System32/evil.xlsx"
        response = _upload(client, project_id, evil_name, payload, XLSX_MIME)
        assert response.status_code == 201

        body = response.json()
        # 原始文件名只作元数据保存
        assert body["original_filename"] == evil_name
        # 落盘文件名是服务端生成的随机 UUID，绝不含用户可控成分
        stored = list(settings.files_dir.iterdir())
        assert len(stored) == 1
        assert ".." not in stored[0].name
        assert "/" not in stored[0].name and "\\" not in stored[0].name
        assert stored[0].parent.resolve() == settings.files_dir.resolve()

    def test_落盘文件名与原名无关(self, client: TestClient, tmp_path: Path) -> None:
        project_id = _project(client)
        rows = order_rows(
            title="QUOTATION",
            doc_label="Quotation No.",
            doc_no="Q-1",
            date="2026-07-15",
            items=BASE_ITEMS,
            grand_total="3070.00",
        )
        payload = write_xlsx(tmp_path / "ok.xlsx", {"Q": rows}).read_bytes()
        _upload(client, project_id, "客户报价单 2026.xlsx", payload, XLSX_MIME)
        stored = next(iter(settings.files_dir.iterdir()))
        assert "客户" not in stored.name
        assert stored.suffix == ".xlsx"


class TestZipBomb:
    """openpyxl 基于 zipfile + lxml，只限制上传体积挡不住高压缩比炸弹。"""

    @staticmethod
    def _bomb(ratio_target: int = 5000) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", b"\x00" * (1024 * ratio_target))
        return buffer.getvalue()

    def test_解压炸弹被拒绝(self, tmp_path: Path) -> None:
        path = tmp_path / "bomb.xlsx"
        path.write_bytes(self._bomb())
        src = document_input(path)
        capability = XlsxParser().can_parse(src)
        assert not capability.accepted
        assert capability.reason_code is ParseReasonCode.FILE_TOO_LARGE
        assert "炸弹" in (capability.detail or "") or "体积" in (capability.detail or "")

    def test_解压炸弹在解析层显式失败而不是崩溃(self, tmp_path: Path) -> None:
        path = tmp_path / "bomb.xlsx"
        path.write_bytes(self._bomb())
        parsed = XlsxParser().parse(document_input(path))
        assert parsed.status is ParseStatus.REJECTED
        assert parsed.reason_code is ParseReasonCode.FILE_TOO_LARGE


class TestCorruptFiles:
    def test_损坏的zip显式失败(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.xlsx"
        path.write_bytes(b"PK\x03\x04" + b"\x00" * 200)
        parsed = XlsxParser().parse(document_input(path))
        assert parsed.status in (ParseStatus.REJECTED, ParseStatus.FAILED)
        assert parsed.reason_code is not None

    def test_空工作簿不假装成功(self, tmp_path: Path) -> None:
        path = write_xlsx(tmp_path / "empty.xlsx", {"Sheet1": []})
        parsed = XlsxParser().parse(document_input(path))
        assert parsed.status is ParseStatus.REJECTED
        assert parsed.reason_code is ParseReasonCode.NO_TABLE_FOUND

    def test_工作表过多被拒绝(self, tmp_path: Path) -> None:
        from app.parsers.base import ParseLimits

        sheets = {f"S{i}": [["x"]] for i in range(30)}
        path = write_xlsx(tmp_path / "many.xlsx", sheets)
        parsed = XlsxParser().parse(document_input(path), ParseLimits(max_sheets=5))
        assert parsed.status is ParseStatus.REJECTED
        assert parsed.reason_code is ParseReasonCode.SHEET_LIMIT

    def test_行数超限被拒绝(self, tmp_path: Path) -> None:
        from app.parsers.base import ParseLimits

        rows = [["Item No.", "Qty", "Unit Price"]] + [[f"A{i}", i, "1.00"] for i in range(200)]
        path = write_xlsx(tmp_path / "long.xlsx", {"S": rows})
        parsed = XlsxParser().parse(
            document_input(path), ParseLimits(max_rows_per_sheet=1000, max_total_rows=50)
        )
        assert parsed.status is ParseStatus.REJECTED
        assert parsed.reason_code is ParseReasonCode.ROW_LIMIT


class TestFormulaText:
    """客户单据里 `=D5*E5` 这类写法极常见。

    它作为**原文证据**必须原样保留成文本，绝不能在任何输出里被重新求值。
    """

    def test_公式原文作为证据保留(self, tmp_path: Path) -> None:
        rows = [
            ["Item No.", "Description", "Qty", "Unit Price", "Amount"],
            ["AB-100", "Mug", 1000, "1.25", "=C2*D2"],
        ]
        path = write_xlsx(tmp_path / "formula.xlsx", {"S": rows})
        parsed = XlsxParser().parse(document_input(path))
        assert parsed.status in (ParseStatus.OK, ParseStatus.NEEDS_REVIEW)
        # openpyxl 写入时把 =C2*D2 记成公式且没有缓存值 -> 必须显式告警，不能当 0
        assert parsed.reason_code is ParseReasonCode.FORMULA_WITHOUT_CACHE
        assert any("缓存值" in w for w in parsed.warnings)


class TestErrorLeakage:
    """错误信息不得泄露服务器绝对路径（SPEC §15.1）。"""

    @pytest.mark.parametrize(
        "path",
        ["/api/v1/projects/nope", "/api/v1/projects/nope/differences"],
    )
    def test_错误信息不含绝对路径(self, client: TestClient, path: str) -> None:
        response = client.get(path)
        assert response.status_code == 404
        text = response.text
        assert "C:\\" not in text
        assert "/home/" not in text
        assert "site-packages" not in text

    def test_解析失败信息不含绝对路径(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.xlsx"
        path.write_bytes(b"PK\x03\x04" + b"\x00" * 50)
        parsed = XlsxParser().parse(document_input(path))
        detail = parsed.detail or ""
        assert str(tmp_path) not in detail
        assert "C:\\" not in detail


class TestDefaults:
    def test_默认绑定回环地址(self) -> None:
        """MVP 无鉴权，回环绑定是唯一的访问控制手段。"""
        assert settings.host == "127.0.0.1"

    def test_默认不启用任何外部模型(self, client: TestClient) -> None:
        assert client.get("/api/v1/health").json()["llm_enabled"] is False


class TestParserRejectsPdf:
    def test_pdf被明确拒绝且带原因(self, tmp_path: Path) -> None:
        path = tmp_path / "scan.pdf"
        path.write_bytes(b"%PDF-1.7\n")
        src = DocumentInput(
            path=path,
            original_filename="scan.pdf",
            mime_type="application/pdf",
            file_size=path.stat().st_size,
            sha256="x" * 64,
        )
        capability = XlsxParser().can_parse(src)
        assert not capability.accepted
        assert capability.reason_code is ParseReasonCode.UNSUPPORTED_EXT
        assert "PDF" in (capability.detail or "")
