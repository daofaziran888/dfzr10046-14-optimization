"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None
    entry_ids: list[Any] | None = None
    ids: list[Any] | None = None
    selectedIds: list[Any] | None = None
    entryIds: list[Any] | None = None
    values: dict[str, Any] | None = None
    batch_id: str | None = None
    receipt_no: str | None = None
    review_id: str | int | None = None
    source_id: str | int | None = None
    grain_id: str | None = None
    reviewer_group: str | None = None
    reviewer: str | None = None
    decision: str | None = None
    expected_version: int | None = None
    idempotency_key: str | None = None
