"""批量确认与跨模块复核工作流接口。

为兼容不同调用习惯，同一能力保留 batches / batch-confirm / review-todos 等路径别名；
业务判断全部委托给 ReviewWorkflow。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from app.services.review_flow import workflow

router = APIRouter(prefix="/api", tags=["批量确认与复核待办"])


def _data(payload: dict[str, Any] | None) -> dict[str, Any]:
    return payload or {}


def _ids(data: dict[str, Any]) -> list[Any]:
    for key in (
        "entry_ids", "ids", "selected_ids", "selectedIds", "entryIds",
        "row_ids", "items", "rows",
    ):
        if key in data and data[key] is not None:
            value = data[key]
            return value if isinstance(value, list) else [value]
    return []


def _grain(data: dict[str, Any]) -> str | None:
    value = (
        data.get("grain_id") or data.get("grainId")
        or data.get("declaration_id") or data.get("declarationId")
        or data.get("关联声明编号") or data.get("grain")
    )
    return str(value) if value is not None else None


def _operator(data: dict[str, Any]) -> str:
    return str(data.get("operator") or data.get("submitter") or data.get("actor") or "汇总员")


def _reviewer(data: dict[str, Any]) -> tuple[str | None, str | None]:
    group = data.get("reviewer_group") or data.get("group") or data.get("reviewerGroup")
    reviewer = data.get("reviewer") or data.get("operator") or data.get("assignee")
    return (str(group) if group is not None else None, str(reviewer) if reviewer is not None else None)


# ----------------------------------------------------------------------
# 汇总层：批次提交、草稿、续做、改关联
# ----------------------------------------------------------------------
@router.post("/summary_layer/batches/draft")
def create_draft(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    data = _data(payload)
    ok, message, result = workflow.create_draft(
        _ids(data), grain_id=_grain(data), operator=_operator(data))
    return {"ok": ok, "message": message, **(result or {})}


@router.post("/summary_layer/batches")
@router.post("/summary_layer/batch/confirm")
@router.post("/summary_layer/batch-confirm")
@router.post("/summary_layer/batch_confirm")
@router.post("/summary_layer/confirm-batch")
@router.post("/summary_layer/confirm_batch")
@router.post("/summary_layer/batchConfirm")
@router.post("/summary_layer/batch/actions")
def submit_batch(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    data = _data(payload)
    action = str(data.get("action") or "")
    if action in {"retry", "失败续做", "按回执重试"} and (data.get("batch_id") or data.get("receipt_no")):
        if data.get("receipt_no") and not data.get("batch_id"):
            ok, message, result = workflow.retry_receipt(str(data["receipt_no"]), operator=_operator(data))
        else:
            ok, message, result = workflow.retry_batch(
                str(data["batch_id"]), _ids(data) or None,
                receipt_no=data.get("receipt_no"), operator=_operator(data))
        return {"ok": ok, "message": message, **(result or {})}
    if action in {"draft", "save_draft", "待提交"}:
        ok, message, result = workflow.create_draft(
            _ids(data), grain_id=_grain(data), operator=_operator(data))
        return {"ok": ok, "message": message, **(result or {})}
    ok, message, result = workflow.submit_batch(
        _ids(data) or None,
        batch_id=data.get("batch_id"),
        grain_id=_grain(data),
        operator=_operator(data),
        idempotency_key=data.get("idempotency_key") or data.get("request_id"),
    )
    return {"ok": ok, "message": message, **(result or {})}


@router.get("/summary_layer/batches/{batch_ref}")
def get_batch(batch_ref: str) -> dict[str, Any]:
    result = workflow.get_batch(batch_ref)
    return {"ok": result is not None, "message": "" if result else f"批次 {batch_ref} 不存在", **(result or {})}


@router.get("/summary_layer/receipts/{receipt_no}")
@router.get("/review-flow/receipts/{receipt_no}")
@router.get("/receipts/{receipt_no}")
def get_receipt(receipt_no: str) -> dict[str, Any]:
    result = workflow.get_receipt(receipt_no)
    return {"ok": result is not None, "message": "" if result else f"回执 {receipt_no} 不存在", **(result or {})}


@router.post("/summary_layer/batches/{batch_ref}/submit")
def submit_saved_batch(batch_ref: str, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    data = _data(payload)
    ok, message, result = workflow.submit_batch(batch_id=batch_ref, operator=_operator(data))
    return {"ok": ok, "message": message, **(result or {})}


@router.post("/summary_layer/batches/{batch_ref}/retry")
def retry_batch(batch_ref: str, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    data = _data(payload)
    ok, message, result = workflow.retry_batch(
        batch_ref, _ids(data) or None,
        receipt_no=data.get("receipt_no"), operator=_operator(data))
    return {"ok": ok, "message": message, **(result or {})}


@router.post("/summary_layer/retry")
@router.post("/summary_layer/retry_batch")
@router.post("/summary_layer/batch-retry")
def retry_by_body(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    data = _data(payload)
    if data.get("receipt_no") and not data.get("batch_id"):
        ok, message, result = workflow.retry_receipt(str(data["receipt_no"]), operator=_operator(data))
    else:
        ok, message, result = workflow.retry_batch(
            str(data.get("batch_id") or ""), _ids(data) or None,
            receipt_no=data.get("receipt_no"), operator=_operator(data))
    return {"ok": ok, "message": message, **(result or {})}


@router.post("/summary_layer/receipts/{receipt_no}/retry")
@router.post("/review-flow/receipts/{receipt_no}/retry")
@router.post("/receipts/{receipt_no}/retry")
def retry_receipt(receipt_no: str, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    ok, message, result = workflow.retry_receipt(receipt_no, operator=_operator(_data(payload)))
    return {"ok": ok, "message": message, **(result or {})}


@router.post("/summary_layer/{entry_ref}/relink")
@router.post("/summary_layer/{entry_ref}/association")
@router.post("/summary_layer/{entry_ref}/link")
def relink_summary(entry_ref: str, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    data = _data(payload)
    grain = _grain(data) or data.get("target")
    version = data.get("expected_version") or data.get("association_version") or data.get("version")
    try:
        expected_version = int(version) if version not in (None, "") else None
    except (TypeError, ValueError):
        expected_version = None
    ok, message, result = workflow.relink_entry(
        entry_ref, str(grain or ""), expected_version=expected_version,
        operator=_operator(data), reroute=bool(data.get("reroute", False)))
    return {"ok": ok, "message": message, "entry": (result or {}).get("entry"), **(result or {})}


@router.api_route("/summary_layer/{entry_ref}", methods=["PATCH", "PUT"])
def update_summary(entry_ref: str, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    data = _data(payload)
    values = data.get("values") if isinstance(data.get("values"), dict) else data
    grain = (
        values.get("grain_id") or values.get("grainId")
        or values.get("关联声明编号") or values.get("grain")
    )
    if not grain:
        return {"ok": False, "message": "当前仅支持通过该接口修改关联声明编号"}
    version = values.get("expected_version") or values.get("association_version") or values.get("version")
    try:
        expected_version = int(version) if version not in (None, "") else None
    except (TypeError, ValueError):
        expected_version = None
    ok, message, result = workflow.relink_entry(
        entry_ref, str(grain), expected_version=expected_version, operator=_operator(data))
    return {"ok": ok, "message": message, "entry": (result or {}).get("entry"), **(result or {})}


@router.post("/summary_layer/{entry_ref}/confirm")
def confirm_summary(entry_ref: str, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    ok, message, result = workflow.confirm_one(entry_ref, operator=_operator(_data(payload)))
    return {"ok": ok, "message": message, **(result or {})}


# ----------------------------------------------------------------------
# 粒度声明页：复核待办与评审组操作
# ----------------------------------------------------------------------
@router.get("/grain_declare/review-scope")
@router.get("/review-todos/scope")
def review_scope(reviewer_group: str | None = None, reviewer: str | None = None) -> dict[str, Any]:
    return {"ok": True, **workflow.review_scope(reviewer_group, reviewer)}


def _review_list(reviewer_group: str | None, grain_id: str | None, scope: str, status: str | None) -> dict[str, Any]:
    items = workflow.list_reviews(
        reviewer_group=reviewer_group, grain_id=grain_id, scope=scope, status=status)
    return {"ok": True, "items": items, "total": len(items), "page": 1, "size": max(len(items), 1)}


@router.get("/grain_declare/review-todos")
@router.get("/grain_declare/review_todos")
@router.get("/grain_declare/review-items")
@router.get("/grain_declare/review_items")
@router.get("/grain_declare/reviews")
@router.get("/grain_declare/todos")
@router.get("/review-todos")
@router.get("/review_todos")
@router.get("/review-items")
@router.get("/reviews")
def list_review_todos(
    reviewer_group: str | None = None,
    grain_id: str | None = None,
    declaration_id: str | None = None,
    grain: str | None = None,
    scope: str = "todo",
    status: str | None = None,
    group: str | None = None,
) -> dict[str, Any]:
    return _review_list(reviewer_group or group, grain_id or declaration_id or grain, scope, status)


@router.post("/grain_declare/review-todos")
@router.post("/grain_declare/review_todos")
@router.post("/grain_declare/reviews")
@router.post("/review-todos")
@router.post("/review_todos")
def create_review_todos(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    data = _data(payload)
    ids = _ids(data)
    if not ids and data.get("source_id") is not None:
        ids = [data["source_id"]]
    if not ids and data.get("summary_id") is not None:
        ids = [data["summary_id"]]
    ok, message, result = workflow.submit_batch(
        ids, grain_id=_grain(data), operator=_operator(data),
        idempotency_key=data.get("idempotency_key") or data.get("request_id"))
    return {"ok": ok, "message": message, **(result or {})}


@router.post("/grain_declare/review-todos/actions")
@router.post("/review-todos/actions")
def review_action_by_body(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    data = _data(payload)
    review_ref = data.get("review_id") or data.get("id") or data.get("review_ref")
    if review_ref is None and _ids(data):
        review_ref = _ids(data)[0]
    if review_ref is None:
        return {"ok": False, "message": "缺少 review_id"}
    return review_action(str(review_ref), data)


@router.get("/grain_declare/{grain_ref}/review-todos")
@router.get("/grain_declare/{grain_ref}/reviews")
def list_reviews_for_declaration(
    grain_ref: str,
    reviewer_group: str | None = None,
    scope: str = "todo",
    status: str | None = None,
) -> dict[str, Any]:
    return _review_list(reviewer_group, grain_ref, scope, status)


@router.post("/grain_declare/review-todos/{review_ref}/receive")
@router.post("/grain_declare/review_todos/{review_ref}/receive")
@router.post("/grain_declare/reviews/{review_ref}/receive")
@router.post("/review-todos/{review_ref}/receive")
@router.post("/review_todos/{review_ref}/receive")
@router.post("/reviews/{review_ref}/receive")
def receive_review(review_ref: str, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    data = _data(payload)
    group, reviewer = _reviewer(data)
    ok, message, result = workflow.receive_review(
        review_ref, reviewer_group=group, reviewer=reviewer, receipt_no=data.get("receipt_no"))
    return {"ok": ok, "message": message, **(result or {})}


@router.post("/grain_declare/review-todos/{review_ref}/claim")
def claim_review(review_ref: str, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return receive_review(review_ref, payload)


@router.post("/grain_declare/review-todos/{review_ref}/sign")
@router.post("/grain_declare/review_todos/{review_ref}/sign")
@router.post("/grain_declare/review-todos/{review_ref}/accept")
@router.post("/grain_declare/review-todos/{review_ref}/complete")
@router.post("/grain_declare/reviews/{review_ref}/sign")
@router.post("/review-todos/{review_ref}/sign")
@router.post("/review_todos/{review_ref}/sign")
@router.post("/reviews/{review_ref}/sign")
def sign_review(review_ref: str, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    data = _data(payload)
    group, reviewer = _reviewer(data)
    ok, message, result = workflow.sign_review(
        review_ref, reviewer_group=group, reviewer=reviewer,
        decision=str(data.get("decision") or data.get("action") or "通过"),
        receipt_no=data.get("receipt_no"), comment=data.get("comment"))
    return {"ok": ok, "message": message, **(result or {})}


@router.post("/grain_declare/review-todos/{review_ref}/actions")
def review_action(review_ref: str, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    data = _data(payload)
    action = str(data.get("action") or "")
    if action in {"接收", "接收复核", "claim", "receive"}:
        return receive_review(review_ref, payload)
    return sign_review(review_ref, payload)
