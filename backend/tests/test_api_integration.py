"""API 集成测试。SPEC §19 阶段 4 验收信号。

核心用例（本产品最容易写错、也最容易在演示时翻车的一条）：

    审核若干条 -> 修正 1 个单价 -> 重跑
    -> 未受影响的裁决**原样保留**
    -> 受影响的那条置 NEEDS_CONFIRMATION，**备注保留**，并能看到旧前提

默认实现（delete-all + insert）会让这些裁决静默归零，而任何测试都抓不到。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.session import init_db, reset_engine
from app.domain.enums import DocumentRole
from tests.conftest import BASE_GRAND_TOTAL, BASE_ITEMS, order_rows, write_xlsx

_TITLES = {
    DocumentRole.QUOTATION: ("QUOTATION", "Quotation No.", "Q2026-001"),
    DocumentRole.PURCHASE_ORDER: ("PURCHASE ORDER", "PO No.", "PO-8899"),
    DocumentRole.PROFORMA_INVOICE: ("PROFORMA INVOICE", "PI No.", "PI-2026-001"),
}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """每个测试一套独立的数据目录与数据库。"""
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    settings.ensure_dirs()
    reset_engine(f"sqlite:///{(tmp_path / 'data' / 'test.sqlite3').as_posix()}")
    init_db()

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
    reset_engine(None)


def _xlsx_bytes(
    tmp_path: Path,
    role: DocumentRole,
    items: list[tuple[str, str, int, str, str, str]] | None = None,
    grand_total: str = BASE_GRAND_TOTAL,
) -> bytes:
    title, label, number = _TITLES[role]
    rows = order_rows(
        title=title,
        doc_label=label,
        doc_no=number,
        date="2026-07-15",
        items=items if items is not None else BASE_ITEMS,
        grand_total=grand_total,
    )
    path = write_xlsx(tmp_path / f"{role.value}.xlsx", {title[:20]: rows})
    return path.read_bytes()


def _upload(
    client: TestClient,
    project_id: str,
    role: DocumentRole,
    payload: bytes,
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{project_id}/documents",
        data={"role": role.value},
        files={
            "file": (
                f"{role.value.lower()}.xlsx",
                payload,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _setup_project(client: TestClient, tmp_path: Path) -> str:
    created = client.post("/api/v1/projects", json={"name": "测试项目"})
    assert created.status_code == 201, created.text
    project_id: str = created.json()["id"]

    po_items = [
        ("AB-100", "Ceramic Mug 350ml", 1200, "PCS", "1.25", "1500.00"),
        *BASE_ITEMS[1:],
    ]
    pi_items = [
        BASE_ITEMS[0],
        ("AB-200", "Ceramic Plate 8in", 500, "PCS", "2.50", "1250.00"),
        BASE_ITEMS[2],
    ]
    _upload(
        client,
        project_id,
        DocumentRole.QUOTATION,
        _xlsx_bytes(tmp_path, DocumentRole.QUOTATION),
    )
    _upload(
        client,
        project_id,
        DocumentRole.PURCHASE_ORDER,
        _xlsx_bytes(tmp_path, DocumentRole.PURCHASE_ORDER, po_items, "3320.00"),
    )
    _upload(
        client,
        project_id,
        DocumentRole.PROFORMA_INVOICE,
        _xlsx_bytes(tmp_path, DocumentRole.PROFORMA_INVOICE, pi_items, "3120.00"),
    )
    return project_id


class TestHealth:
    def test_健康检查(self, client: TestClient) -> None:
        body = client.get("/api/v1/health").json()
        assert body["status"] == "ok"
        assert body["llm_enabled"] is False


class TestFullFlow:
    def test_完整闭环(self, client: TestClient, tmp_path: Path) -> None:
        project_id = _setup_project(client, tmp_path)

        compared = client.post(f"/api/v1/projects/{project_id}/compare")
        assert compared.status_code == 200, compared.text
        payload = compared.json()
        assert payload["status"] == "COMPARED"
        assert sorted(payload["compared_roles"]) == sorted(r.value for r in DocumentRole)
        assert payload["severity_counts"]["CRITICAL"] > 0

        listed = client.get(f"/api/v1/projects/{project_id}/differences").json()
        assert listed["total"] == len(listed["items"])
        assert all(item["evidence"] for item in listed["items"]), "每条差异必须有证据"

    def test_筛选(self, client: TestClient, tmp_path: Path) -> None:
        project_id = _setup_project(client, tmp_path)
        client.post(f"/api/v1/projects/{project_id}/compare")

        criticals = client.get(
            f"/api/v1/projects/{project_id}/differences", params={"severity": "CRITICAL"}
        ).json()
        assert criticals["total"] > 0
        assert all(i["severity"] == "CRITICAL" for i in criticals["items"])

        by_sku = client.get(
            f"/api/v1/projects/{project_id}/differences", params={"sku": "AB-100"}
        ).json()
        assert all("AB-100" in i["subject_key"] for i in by_sku["items"])

    def test_报告可导出且自包含(self, client: TestClient, tmp_path: Path) -> None:
        project_id = _setup_project(client, tmp_path)
        client.post(f"/api/v1/projects/{project_id}/compare")

        report = client.get(
            f"/api/v1/projects/{project_id}/report.html",
            params={"generated_at": "2026-07-26T12:00:00"},
        )
        assert report.status_code == 200, report.text
        html = report.text
        assert "http://" not in html and "https://" not in html, "报告不得引用外部资源"
        assert "<script" not in html.lower(), "报告必须零 JS"
        assert "Content-Security-Policy" in html
        # 一条都没裁决过时不显示裁决列，免得整列「待处理」白占版面
        assert "人工裁决" not in html

    def test_报告带上人工裁决状态与备注(self, client: TestClient, tmp_path: Path) -> None:
        """报告是**发出去**的东西。

        已经确认过的条目若在报告里和从未看过的条目长得一样，收报告的人只能
        把全部条目重看一遍——审核工作等于白做。
        """
        project_id = _setup_project(client, tmp_path)
        client.post(f"/api/v1/projects/{project_id}/compare")

        items = client.get(f"/api/v1/projects/{project_id}/differences").json()["items"]
        target = items[0]
        note = "已与客户邮件确认，按采购订单执行"
        client.put(
            f"/api/v1/projects/{project_id}/reviews/{target['difference_key']}",
            json={"review_status": "ACCEPTED_DIFFERENCE", "review_note": note},
        )

        html = client.get(
            f"/api/v1/projects/{project_id}/report.html",
            params={"generated_at": "2026-07-26T12:00:00"},
        ).text
        assert "人工裁决" in html
        assert "已接受该差异" in html
        assert note in html
        # 未裁决的条目仍显示为待处理，不能被顺手标成已处理
        assert "待处理" in html

    def test_报告里前提已变的裁决被醒目标出(self, client: TestClient, tmp_path: Path) -> None:
        """基于旧数字做出的结论，在报告里必须比「已确认」更醒目。"""
        project_id = _setup_project(client, tmp_path)
        client.post(f"/api/v1/projects/{project_id}/compare")

        items = client.get(f"/api/v1/projects/{project_id}/differences").json()["items"]
        quantity_diff = next(
            i for i in items if i["field_name"] == "quantity" and "AB-100" in i["subject_key"]
        )
        client.put(
            f"/api/v1/projects/{project_id}/reviews/{quantity_diff['difference_key']}",
            json={"review_status": "ACCEPTED_DIFFERENCE", "review_note": "客户已口头加单"},
        )
        client.post(
            f"/api/v1/projects/{project_id}/corrections",
            json={
                "role": "PURCHASE_ORDER",
                "scope": "LINE_ITEM",
                "line_key": "sku:AB-100#1",
                "field_name": "quantity",
                "user_value": "1300",
                "reason": "客户改单",
            },
        )
        client.post(f"/api/v1/projects/{project_id}/compare")

        html = client.get(
            f"/api/v1/projects/{project_id}/report.html",
            params={"generated_at": "2026-07-26T12:00:00"},
        ).text
        assert "前提已变，需重新确认" in html
        assert "请重新确认" in html
        assert "客户已口头加单" in html, "备注必须一并带到报告里"
        # 前提变了就不能再显示成「已接受」——那是拿旧结论盖新数字
        assert "已接受该差异" not in html

    def test_删除项目清库清盘(self, client: TestClient, tmp_path: Path) -> None:
        project_id = _setup_project(client, tmp_path)
        client.post(f"/api/v1/projects/{project_id}/compare")

        files_before = list(settings.files_dir.glob("*.xlsx"))
        assert len(files_before) == 3

        assert client.delete(f"/api/v1/projects/{project_id}").status_code == 204
        assert client.get(f"/api/v1/projects/{project_id}").status_code == 404
        assert list(settings.files_dir.glob("*.xlsx")) == [], "磁盘文件必须一并删除"

        # 孤儿计数 = 0
        from sqlalchemy import func, select

        from app.db.models import Difference, DifferenceReview, Document, Evidence, MatchGroup
        from app.db.session import get_session_factory

        with get_session_factory()() as session:
            for model in (Document, Difference, Evidence, MatchGroup, DifferenceReview):
                count = session.scalar(select(func.count()).select_from(model))
                assert count == 0, f"{model.__tablename__} 残留 {count} 行孤儿数据"


class TestRerunPreservesReviews:
    """SPEC §11.3：**本产品最核心的一条行为**。"""

    def test_修正后重跑保留未受影响的裁决(self, client: TestClient, tmp_path: Path) -> None:
        project_id = _setup_project(client, tmp_path)
        client.post(f"/api/v1/projects/{project_id}/compare")

        items = client.get(f"/api/v1/projects/{project_id}/differences").json()["items"]
        assert len(items) >= 3

        # 全部标为已确认，并写上备注
        for index, item in enumerate(items):
            response = client.put(
                f"/api/v1/projects/{project_id}/reviews/{item['difference_key']}",
                json={"review_status": "CONFIRMED_DIFFERENCE", "review_note": f"备注{index}"},
            )
            assert response.status_code == 200, response.text

        after_review = client.get(f"/api/v1/projects/{project_id}/differences").json()["items"]
        assert all(i["review_status"] == "CONFIRMED_DIFFERENCE" for i in after_review)

        # 修正 PI 上 AB-200 的单价（把 2.50 改回 2.40）
        target = next(
            i
            for i in after_review
            if i["field_name"] == "unit_price" and "AB-200" in i["subject_key"]
        )
        correction = client.post(
            f"/api/v1/projects/{project_id}/corrections",
            json={
                "role": "PROFORMA_INVOICE",
                "scope": "LINE_ITEM",
                "line_key": "sku:AB-200#1",
                "field_name": "unit_price",
                "user_value": "2.40",
                "reason": "与客户电话确认按 2.40 执行",
            },
        )
        assert correction.status_code == 201, correction.text

        client.post(f"/api/v1/projects/{project_id}/compare")
        rerun = client.get(f"/api/v1/projects/{project_id}/differences").json()["items"]
        by_key = {i["difference_key"]: i for i in rerun}

        # 单价冲突因为修正而消失
        assert target["difference_key"] not in by_key

        # 其余强身份裁决必须原样保留
        survivors = [
            i
            for i in after_review
            if i["difference_key"] in by_key and i["identity_strength"] == "STRONG"
        ]
        assert survivors, "至少要有一条强身份差异用于验证继承"
        preserved = [
            by_key[i["difference_key"]]
            for i in survivors
            if by_key[i["difference_key"]]["review_status"] == "CONFIRMED_DIFFERENCE"
        ]
        assert preserved, "重跑后所有裁决都丢了——这正是本产品最不能出的错"

    def test_前提变化的差异置为待确认且保留备注(self, client: TestClient, tmp_path: Path) -> None:
        project_id = _setup_project(client, tmp_path)
        client.post(f"/api/v1/projects/{project_id}/compare")

        items = client.get(f"/api/v1/projects/{project_id}/differences").json()["items"]
        quantity_diff = next(
            i for i in items if i["field_name"] == "quantity" and "AB-100" in i["subject_key"]
        )
        client.put(
            f"/api/v1/projects/{project_id}/reviews/{quantity_diff['difference_key']}",
            json={"review_status": "ACCEPTED_DIFFERENCE", "review_note": "客户已口头加单"},
        )

        # 改动该差异所依据的值：PO 数量 1200 -> 1300
        client.post(
            f"/api/v1/projects/{project_id}/corrections",
            json={
                "role": "PURCHASE_ORDER",
                "scope": "LINE_ITEM",
                "line_key": "sku:AB-100#1",
                "field_name": "quantity",
                "user_value": "1300",
                "reason": "客户改单",
            },
        )
        client.post(f"/api/v1/projects/{project_id}/compare")

        rerun = client.get(f"/api/v1/projects/{project_id}/differences").json()["items"]
        updated = next(i for i in rerun if i["difference_key"] == quantity_diff["difference_key"])
        assert updated["review_status"] == "NEEDS_CONFIRMATION"
        assert updated["review_note"] == "客户已口头加单", "备注必须保留"
        assert updated["stale_premise"], "必须能看到当初是基于什么值做的判断"

    def test_人工修正来源被标注(self, client: TestClient, tmp_path: Path) -> None:
        """报告要发给老板和客户，机器读的还是人填的必须精确到字段。"""
        project_id = _setup_project(client, tmp_path)
        client.post(
            f"/api/v1/projects/{project_id}/corrections",
            json={
                "role": "PROFORMA_INVOICE",
                "scope": "LINE_ITEM",
                "line_key": "sku:AB-200#1",
                "field_name": "unit_price",
                "user_value": "9.99",
                "reason": "测试来源标注",
            },
        )
        client.post(f"/api/v1/projects/{project_id}/compare")
        items = client.get(f"/api/v1/projects/{project_id}/differences").json()["items"]
        target = next(
            i for i in items if i["field_name"] == "unit_price" and "AB-200" in i["subject_key"]
        )
        pi_value = target["values_by_document"]["PROFORMA_INVOICE"]
        assert pi_value["source"] == "USER_CORRECTION"
        assert pi_value["value"] == "9.99"
        assert pi_value["parser_value"] == "2.50"
        assert target["has_user_input"] is True


class TestUploadValidation:
    def test_拒绝pdf并给出明确原因(self, client: TestClient, tmp_path: Path) -> None:
        created = client.post("/api/v1/projects", json={"name": "P"})
        project_id = created.json()["id"]
        response = client.post(
            f"/api/v1/projects/{project_id}/documents",
            data={"role": "QUOTATION"},
            files={"file": ("scan.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )
        assert response.status_code == 400
        body = response.json()
        assert body["error_code"] == "UNSUPPORTED_EXT"
        assert "PDF" in body["message"]

    def test_拒绝伪装成xlsx的非zip文件(self, client: TestClient) -> None:
        created = client.post("/api/v1/projects", json={"name": "P"})
        project_id = created.json()["id"]
        response = client.post(
            f"/api/v1/projects/{project_id}/documents",
            data={"role": "QUOTATION"},
            files={
                "file": (
                    "fake.xlsx",
                    b"not a zip at all",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert response.status_code == 400
        assert response.json()["error_code"] == "CORRUPT"

    def test_两份文件即可运行检查(self, client: TestClient, tmp_path: Path) -> None:
        created = client.post("/api/v1/projects", json={"name": "两份"})
        project_id = created.json()["id"]
        _upload(
            client,
            project_id,
            DocumentRole.PURCHASE_ORDER,
            _xlsx_bytes(tmp_path, DocumentRole.PURCHASE_ORDER),
        )
        _upload(
            client,
            project_id,
            DocumentRole.PROFORMA_INVOICE,
            _xlsx_bytes(tmp_path, DocumentRole.PROFORMA_INVOICE),
        )
        response = client.post(f"/api/v1/projects/{project_id}/compare")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["compared_roles"] == ["PROFORMA_INVOICE", "PURCHASE_ORDER"]
        assert body["skipped_roles"] == ["QUOTATION"]

    def test_只有一份文件时明确拒绝(self, client: TestClient, tmp_path: Path) -> None:
        created = client.post("/api/v1/projects", json={"name": "一份"})
        project_id = created.json()["id"]
        _upload(
            client,
            project_id,
            DocumentRole.PURCHASE_ORDER,
            _xlsx_bytes(tmp_path, DocumentRole.PURCHASE_ORDER),
        )
        response = client.post(f"/api/v1/projects/{project_id}/compare")
        assert response.status_code == 400
        assert response.json()["error_code"] == "NOT_ENOUGH_DOCUMENTS"

    def test_错误信息不泄露服务器路径(self, client: TestClient) -> None:
        response = client.get("/api/v1/projects/does-not-exist")
        assert response.status_code == 404
        message = response.json()["message"]
        assert "C:\\" not in message and "/home/" not in message
