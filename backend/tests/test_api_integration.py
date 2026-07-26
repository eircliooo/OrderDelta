"""API 集成测试。SPEC §19 阶段 4 验收信号。

核心用例（本产品最容易写错、也最容易在演示时翻车的一条）：

    审核若干条 -> 修正 1 个单价 -> 重跑
    -> 未受影响的裁决**原样保留**
    -> 受影响的那条置 NEEDS_CONFIRMATION，**备注保留**，并能看到旧前提

默认实现（delete-all + insert）会让这些裁决静默归零，而任何测试都抓不到。
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.session import init_db, reset_engine
from app.domain.enums import DocumentRole, Severity
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

    def test_删单份文档不留孤儿证据(self, client: TestClient, tmp_path: Path) -> None:
        """`evidence.document_id` 没有外键，级联只挂在 `project_id` 上。

        不主动清理的话，删掉一份单据后它的证据行会留在库里，仍被
        `difference.evidence_ids` 引用，报告里照样渲染出「引用原文：某某单元格」——
        指向一份已经不在项目里的文件，用户无从核对，也无从察觉。
        """
        from sqlalchemy import func
        from sqlalchemy import select as sa_select

        from app.db.models import Difference, Evidence, MatchGroup
        from app.db.session import session_scope

        project_id = _setup_project(client, tmp_path)
        assert client.post(f"/api/v1/projects/{project_id}/compare").status_code == 200

        with session_scope() as session:
            before = session.scalar(
                sa_select(func.count())
                .select_from(Evidence)
                .where(Evidence.project_id == project_id)
            )
        assert before, "比较后本该有证据，没有的话这个测试测不到东西"

        docs = client.get(f"/api/v1/projects/{project_id}").json()["documents"]
        assert (
            client.delete(f"/api/v1/projects/{project_id}/documents/{docs[0]['id']}").status_code
            == 204
        )

        with session_scope() as session:
            for model in (Evidence, Difference, MatchGroup):
                left = session.scalar(
                    sa_select(func.count()).select_from(model).where(model.project_id == project_id)
                )
                assert left == 0, f"删文档后 {model.__tablename__} 还剩 {left} 行孤儿"

        # 必须退回「未比较」，否则界面会拿着一份已作废的结论当最新结果显示。
        # 但 status 走的是「可用文档数」口径（与上传路径一致），删掉三份里的一份
        # 之后剩下两份仍然可以跑，所以是 READY 而不是 DRAFT——
        # 「刚删过东西」不该让一个跑得动的项目显示成草稿。
        project = client.get(f"/api/v1/projects/{project_id}").json()
        assert project["compared_at"] is None
        assert project["status"] == "READY"
        assert project["severity_counts"] == dict.fromkeys(project["severity_counts"], 0)

        # 没跑过检查就不给导出报告：那份东西最可能被转发给客户
        report = client.get(f"/api/v1/projects/{project_id}/report.html")
        assert report.status_code == 409, report.status_code


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

    def test_调高严重度后旧裁决不再被静默继承(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """这个产品最阴的一种失效：**值一个字没动，只是规则把风险调高了。**

        原实现里前提摘要只由取值构成，于是把某字段从 REVIEW 调成 CRITICAL 之后重跑，
        那条早被标成「已接受差异」的记录会原样带着旧裁决出现在报告里——
        看起来像「这条新的 CRITICAL 已经有人看过并接受了」，恰好在风险刚被调高、
        最该重看的时候失去人工复核。而且它不会以任何形式报错。
        """
        project_id = _setup_project(client, tmp_path)
        client.post(f"/api/v1/projects/{project_id}/compare")
        items = client.get(f"/api/v1/projects/{project_id}/differences").json()["items"]

        target = next(i for i in items if i["severity"] != "CRITICAL" and i["field_name"])
        assert (
            client.put(
                f"/api/v1/projects/{project_id}/reviews/{target['difference_key']}",
                json={"review_status": "ACCEPTED_DIFFERENCE", "review_note": "老板同意按这个走"},
            ).status_code
            == 200
        )

        # 规则改动：把这一条的严重度整体调高（等价于有人编辑了 fields.py 的严重度表）
        import app.comparison.engine as engine_module

        original = engine_module.severity_for

        def louder(key: str, scope: Any, stage: Any) -> Severity:
            if key == target["field_name"]:
                return Severity.CRITICAL
            return original(key, scope, stage)

        monkeypatch.setattr(engine_module, "severity_for", louder)

        client.post(f"/api/v1/projects/{project_id}/compare")
        rerun = {
            i["difference_key"]: i
            for i in client.get(f"/api/v1/projects/{project_id}/differences").json()["items"]
        }
        after = rerun.get(target["difference_key"])
        assert after is not None, "差异本身不该消失，消失就说明这个测试测错了东西"
        assert after["severity"] == "CRITICAL", "规则改动没有生效，本测试没有验证到任何东西"
        assert after["review_status"] == "NEEDS_CONFIRMATION", (
            "严重度被调高后，旧裁决仍被原样继承——"
            f"实际状态 {after['review_status']}。这正是本测试要堵的失效。"
        )
        assert after["review_note"] == "老板同意按这个走", (
            "备注不该丢，人还要靠它回忆当初为什么接受"
        )

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


class TestStaleResultsAreInvalidated:
    """换了文件之后，上一轮的结论必须整体作废。

    这两条都是「屏幕和报告各说一套」的失效：一边读库里的旧结果，
    一边现场重算，同一批数据给出互相矛盾的结论，而屏幕那套还带着
    看起来有效的人工裁决。
    """

    def test_同角色重新上传作废上一轮结果(self, client: TestClient, tmp_path: Path) -> None:
        """**界面上唯一能换文件的操作就是这个**（前端没有删除单份文档的入口）。

        旧实现只在 delete_document 上作废③计算产物，而 supersede 分支会把旧文件
        从磁盘 unlink 掉、却把整套按旧文件算出来的差异与证据留在库里。
        """
        from sqlalchemy import func
        from sqlalchemy import select as sa_select

        from app.db.models import Difference, Evidence
        from app.db.session import session_scope

        project_id = _setup_project(client, tmp_path)
        assert client.post(f"/api/v1/projects/{project_id}/compare").status_code == 200
        before = client.get(f"/api/v1/projects/{project_id}/differences").json()["items"]
        assert before, "先得有差异，否则这条测试测不到东西"

        # 客户改单，用新的 PO 覆盖上传同一个角色
        rows = order_rows(
            title="PURCHASE ORDER",
            doc_label="PO No.",
            doc_no="PO-8899-R2",
            date="2026-07-20",
            items=[
                ("AB-100", "Ceramic Mug 350ml", 1500, "PCS", "1.25", "1875.00"),
                *BASE_ITEMS[1:],
            ],
            grand_total="3695.00",
        )
        path = write_xlsx(tmp_path / "po_v2.xlsx", {"PURCHASE ORDER": rows})
        _upload(client, project_id, DocumentRole.PURCHASE_ORDER, path.read_bytes())

        project = client.get(f"/api/v1/projects/{project_id}").json()
        assert project["compared_at"] is None, "换了文件却还挂着上次的检查时间"
        assert client.get(f"/api/v1/projects/{project_id}/differences").json()["items"] == [], (
            "换文件后仍返回按旧文件算出来的差异"
        )
        with session_scope() as session:
            for model in (Evidence, Difference):
                left = session.scalar(
                    sa_select(func.count()).select_from(model).where(model.project_id == project_id)
                )
                assert left == 0, f"{model.__tablename__} 还剩 {left} 行指向已被替换的文件"

        # 重新跑一次应当正常产出（作废不等于把项目弄坏）
        assert client.post(f"/api/v1/projects/{project_id}/compare").status_code == 200
        assert client.get(f"/api/v1/projects/{project_id}/differences").json()["items"]

    def test_未解释差额变化后旧裁决不再被静默继承(self, client: TestClient, tmp_path: Path) -> None:
        """「存在未解释差额 X」这条差异的关键数字 X 不在取值里，只活在说明参数里。

        摘要若只取「各角色取值 + 严重度 + 规则 id」，X 从 250 变成 1150 时三者全都不变
        （severity 恒为 REVIEW、规则 id 恒为常量），旧的「已接受」原样继承——
        报告上写着「1150 的差额已经有人看过并接受了」。

        自己造场景而不用 `_setup_project`：那份夹具的 Σ行金额与总金额是对得上的，
        跑不出未解释差额。**不用 skip**（硬约束 #16 零 skip）。
        """
        created = client.post("/api/v1/projects", json={"name": "未解释差额"})
        project_id: str = created.json()["id"]
        # PI 的总金额比 Σ行金额多 250（真实单据里的运费/模具费），PO 对得上
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
            _xlsx_bytes(
                tmp_path,
                DocumentRole.PROFORMA_INVOICE,
                grand_total=str(Decimal(BASE_GRAND_TOTAL) + Decimal("250.00")),
            ),
        )
        client.post(f"/api/v1/projects/{project_id}/compare")
        items = client.get(f"/api/v1/projects/{project_id}/differences").json()["items"]

        delta = next(i for i in items if i["explanation_key"] == "unexplained_total_delta")
        assert delta["explanation_params"]["delta"] == "250.00", delta["explanation_params"]

        assert (
            client.put(
                f"/api/v1/projects/{project_id}/reviews/{delta['difference_key']}",
                json={
                    "review_status": "ACCEPTED_DIFFERENCE",
                    "review_note": "运费 250，已跟客户确认",
                },
            ).status_code
            == 200
        )

        # 某一行金额被抄错一位，**总金额一个字没动** -> 差额从 250 涨到 1150
        assert (
            client.post(
                f"/api/v1/projects/{project_id}/corrections",
                json={
                    "role": "PROFORMA_INVOICE",
                    "scope": "LINE_ITEM",
                    "line_key": "sku:AB-100#1",
                    "field_name": "line_total",
                    "user_value": "350.00",
                    "reason": "核对后发现抄错一位",
                },
            ).status_code
            == 201
        )
        client.post(f"/api/v1/projects/{project_id}/compare")
        rerun = {
            i["difference_key"]: i
            for i in client.get(f"/api/v1/projects/{project_id}/differences").json()["items"]
        }
        after = rerun.get(delta["difference_key"])
        assert after is not None, "差异本身不该消失，消失说明这条测试测错了东西"
        assert after["explanation_params"]["delta"] != "250.00", (
            "差额没变，这条测试没有验证到任何东西"
        )
        assert after["review_status"] == "NEEDS_CONFIRMATION", (
            f"差额从 250.00 变成 {after['explanation_params']['delta']}，"
            f"旧裁决却仍被原样继承（{after['review_status']}）"
        )
        assert after["review_note"] == "运费 250，已跟客户确认", "备注不该丢"


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
