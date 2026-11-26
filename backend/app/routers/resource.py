"""统一资源接口：查询、导出、登记和动作按模块 key 分派到领域服务。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services import service_for
from app.services.review_flow import workflow

router = APIRouter(prefix="/api", tags=["业务资源"])


def require_service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


@router.get("/{module}", response_model=PageResult[dict])
def list_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, total = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{module}/export")
def export_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, _ = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=1, size=1_000_000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *service.LIST_FIELDS, "status"])
    for item in items:
        values = [item.get("id"), *(item.get(field, "") for field in service.LIST_FIELDS),
                  item.get("status", "")]
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{module}.csv"'},
    )


@router.get("/{module}/{entry_id}", response_model=dict)
def get_entry(module: str, entry_id: int) -> dict[str, Any]:
    service = require_service(module)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_id} 不存在或已归档")
    return entry


@router.post("/{module}", response_model=ActionResult)
def create_entry(module: str, payload: EntryPayload) -> ActionResult:
    service = require_service(module)
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"{service.ENTITY}已登记", entry=entry)


@router.post("/{module}/actions")
def run_module_action(module: str, payload: ActionPayload) -> dict[str, Any]:
    """模块级动作：目前承载汇总层批量确认、失败续做和改关联。"""
    service = require_service(module)
    action = payload.action.strip()
    ids = payload.entry_ids or payload.ids or payload.entryIds or payload.selectedIds or []
    if module == "summary_layer":
        if action in {"保存为待提交批次", "待提交", "保存草稿", "draft"}:
            ok, message, result = workflow.create_draft(ids, grain_id=payload.grain_id)
            return {"ok": ok, "message": message, **(result or {})}
        if action in {"确认派生", "批量确认", "批量确认派生", "批量确认并创建复核", "创建批次", "提交批次"}:
            ok, message, result = workflow.submit_batch(
                ids,
                batch_id=payload.batch_id,
                grain_id=payload.grain_id,
                idempotency_key=payload.idempotency_key,
            )
            return {"ok": ok, "message": message, **(result or {})}
        if action in {"失败续做", "重试失败", "按回执重试", "重试"}:
            if payload.receipt_no and not payload.batch_id:
                ok, message, result = workflow.retry_receipt(payload.receipt_no)
            else:
                ok, message, result = workflow.retry_batch(
                    str(payload.batch_id or ""), ids or None, receipt_no=payload.receipt_no)
            return {"ok": ok, "message": message, **(result or {})}
        if action in {"改关联", "修改关联", "重新关联"}:
            values = payload.values or {}
            entry_id = values.get("entry_id") or values.get("id") or (ids[0] if ids else None)
            grain_id = payload.grain_id or values.get("grain_id") or values.get("关联声明编号")
            ok, message, result = workflow.relink_entry(
                entry_id, str(grain_id or ""), expected_version=payload.expected_version)
            return {"ok": ok, "message": message, **(result or {})}
    if module == "grain_declare":
        values = payload.values or {}
        review_ref = (
            payload.review_id or payload.source_id
            or values.get("review_id") or values.get("id")
            or (ids[0] if ids else None)
        )
        group = payload.reviewer_group or values.get("reviewer_group")
        reviewer = payload.reviewer or values.get("reviewer")
        if action in {"接收", "接收复核", "领取", "claim", "receive"}:
            ok, message, result = workflow.receive_review(
                review_ref, reviewer_group=group, reviewer=reviewer, receipt_no=payload.receipt_no)
            return {"ok": ok, "message": message, **(result or {})}
        if action in {"签收", "签收通过", "通过", "完成复核", "accept", "sign"}:
            ok, message, result = workflow.sign_review(
                review_ref, reviewer_group=group, reviewer=reviewer,
                decision=payload.decision or action, receipt_no=payload.receipt_no,
                comment=payload.remark)
            return {"ok": ok, "message": message, **(result or {})}
        if action in {"驳回", "拒绝", "reject"}:
            ok, message, result = workflow.sign_review(
                review_ref, reviewer_group=group, reviewer=reviewer,
                decision="驳回", receipt_no=payload.receipt_no, comment=payload.remark)
            return {"ok": ok, "message": message, **(result or {})}
    return {"ok": False, "message": f"动作「{action}」不是模块 {module} 的模块级动作", "service": service.MODULE}


@router.post("/{module}/{entry_id}/actions", response_model=ActionResult)
def run_action(module: str, entry_id: int, payload: ActionPayload) -> ActionResult:
    service = require_service(module)
    entry, message = service.run_action(entry_id, payload.action.strip())
    return ActionResult(ok=entry is not None, message=message, entry=entry)
