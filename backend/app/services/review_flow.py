"""汇总确认 -> 粒度声明复核的跨模块工作流。

这里集中维护批次、复核项、回执和乐观归属。路由层只做 HTTP 分派，
所有“已派 / 改关联 / 待提交 / 并发认领”的判断都在本服务中完成。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.store import store

ACTIVE_REVIEW_STATUSES = {"待接收", "已接收"}
FINAL_ACCEPTED = "已签收"
FINAL_REJECTED = "已驳回"
STALE_STATUS = "归属冲突"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class ReviewWorkflow:
    """内存态复核工作流；一把可重入锁保证并发提交时归属判断原子。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.batches: dict[str, dict[str, Any]] = {}
        self.reviews: dict[int, dict[str, Any]] = {}
        self.receipts: dict[str, dict[str, Any]] = {}
        self.idempotency: dict[str, str] = {}
        self._batch_seq = 0
        self._review_seq = 0
        self._receipt_seq = 0

    # ------------------------------------------------------------------
    # 基础查询与初始化
    # ------------------------------------------------------------------
    def reset(self) -> None:
        with self._lock:
            self.batches.clear()
            self.reviews.clear()
            self.receipts.clear()
            self.idempotency.clear()
            self._batch_seq = 0
            self._review_seq = 0
            self._receipt_seq = 0

    def _summary_row(self, entry_id: int | str) -> dict[str, Any] | None:
        return store.find("summary_layer", int(entry_id)) if _as_int(entry_id) is not None else None

    def _grain_row(self, grain_ref: Any) -> dict[str, Any] | None:
        grain_text = str(grain_ref or "").strip()
        grain_id = _as_int(grain_ref)
        if grain_id is not None:
            row = store.find("grain_declare", grain_id)
            if row is not None:
                return row
        if not grain_text:
            return None
        wanted = grain_text.casefold()
        for row in store.rows("grain_declare"):
            if str(row.get("声明编号", "")).casefold() == wanted:
                return row
        for row in store.rows("grain_declare"):
            if wanted in str(row.get("声明编号", "")).casefold():
                return row
        return None

    def _group_name(self, grain: dict[str, Any] | None) -> str:
        if grain is None:
            return "未分配评审组"
        return str(grain.get("评审组") or grain.get("reviewer_group") or "粒度声明复核组")

    def group_matches(self, actual: str | None, supplied: str | None) -> bool:
        if supplied is None or str(supplied).strip() == "":
            return True
        if actual is None:
            return False
        a = str(actual).strip().casefold()
        q = str(supplied).strip().casefold()
        if a == q:
            return True
        for grain in store.rows("grain_declare"):
            group = str(grain.get("评审组") or "")
            code = str(grain.get("reviewer_group") or "")
            if q in {group.casefold(), code.casefold()}:
                return group == actual or code == actual
        return False

    def review_scope(self, reviewer_group: str | None = None, reviewer: str | None = None) -> dict[str, Any]:
        with self._lock:
            declarations: list[dict[str, str]] = []
            for row in store.rows("grain_declare"):
                group = str(row.get("评审组") or "粒度声明复核组")
                code = str(row.get("reviewer_group") or "")
                if reviewer_group and not self.group_matches(group, reviewer_group) and not self.group_matches(code, reviewer_group):
                    continue
                declarations.append({
                    "id": str(row.get("声明编号") or row.get("id")),
                    "name": str(row.get("声明编号") or ""),
                    "category": str(row.get("声明类别") or ""),
                    "reviewer_group": group,
                    "group_code": code,
                })
            group_label = reviewer_group or "全部评审组"
            return {
                "reviewer": reviewer or "",
                "reviewer_group": group_label,
                "declaration_ids": [item["id"] for item in declarations],
                "declarations": declarations,
                "can_handle": [item["id"] for item in declarations],
                "message": f"{reviewer or '复核人'}（{group_label}）可处理："
                           + ("、".join(item["id"] for item in declarations) or "无有效粒度声明"),
            }

    def _ensure_row_state(self, row: dict[str, Any]) -> None:
        row.setdefault("关联声明编号", "")
        row.setdefault("association_version", 1)
        # 种子中已经“已派生”的条目，视为此前逐条确认已接受的结果。
        if row.get("status") == "已派生" and not row.get("confirmation"):
            row["confirmation"] = {
                "state": "accepted",
                "result": "accepted",
                "accepted_by": row.get("confirmed_by") or "既有逐条确认",
                "accepted_at": row.get("派生时间") or _now(),
                "message": "沿用已有逐条确认结果",
            }

    def _new_receipt(
        self,
        review: dict[str, Any] | None,
        kind: str,
        state: str,
        *,
        batch_id: str | None = None,
        entry_id: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._receipt_seq += 1
        receipt = {
            "receipt_no": f"RCPT-{self._receipt_seq:04d}",
            "kind": kind,
            "state": state,
            "review_id": review.get("id") if review else None,
            "review_no": review.get("review_no") if review else None,
            "batch_id": batch_id or (review.get("batch_id") if review else None),
            "entry_id": entry_id or (review.get("source_id") if review else None),
            "grain_id": review.get("grain_id") if review else None,
            "association_version": review.get("association_version") if review else None,
            "reviewer_group": review.get("reviewer_group") if review else None,
            "issued_at": _now(),
        }
        if extra:
            receipt.update(extra)
        self.receipts[receipt["receipt_no"]] = receipt
        return receipt

    def _audit(self, action: str, target: str, before: str, after: str, operator: str) -> None:
        try:
            rows = store.rows("model_audit")
            next_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
            rows.append({
                "id": next_id,
                "条目编号": f"AUDIT-WF-{next_id:04d}",
                "status": "已记录",
                "pending": False,
                "abnormal": False,
                "操作类型": action,
                "操作对象": target,
                "变更前值": before,
                "变更后值": after,
                "影响下游数": "1",
                "操作者标识": operator,
                "操作时间": _now(),
            })
        except Exception:
            # 审计失败不能阻断主确认链路。
            pass

    # ------------------------------------------------------------------
    # 复核项与归属
    # ------------------------------------------------------------------
    def _find_review(self, review_ref: int | str | None) -> dict[str, Any] | None:
        if review_ref is None:
            return None
        review_id = _as_int(review_ref)
        if review_id is not None and review_id in self.reviews:
            return self.reviews[review_id]
        text = str(review_ref).strip()
        for review in self.reviews.values():
            if text in {review.get("review_no"), review.get("receipt_no"), str(review.get("id"))}:
                return review
        receipt = self.receipts.get(text)
        if receipt and receipt.get("review_id") in self.reviews:
            return self.reviews[int(receipt["review_id"])]
        return None

    def _current_grain_matches(self, review: dict[str, Any], row: dict[str, Any] | None = None) -> bool:
        row = row or self._summary_row(review.get("source_id"))
        if row is None:
            return False
        return (
            str(row.get("关联声明编号") or "") == str(review.get("grain_id") or "")
            and int(row.get("association_version") or 1) == int(review.get("association_version") or 1)
        )

    def _sync_review_validity(self, review: dict[str, Any]) -> bool:
        """返回复核项当前是否仍在有效处理范围内。"""
        row = self._summary_row(review.get("source_id"))
        if row is None:
            review["in_scope"] = False
            review["scope_state"] = "源条目不存在"
            return False
        if review.get("status") == FINAL_ACCEPTED:
            # 已接受结果必须保留；它仍是有效结果，只是通常出现在已办中。
            review["in_scope"] = True
            review["scope_state"] = "已接受，结果保留"
            return True
        matches = self._current_grain_matches(review, row)
        if not matches and review.get("status") in ACTIVE_REVIEW_STATUSES | {STALE_STATUS}:
            old_grain = review.get("grain_id")
            new_grain = row.get("关联声明编号")
            review["status"] = STALE_STATUS
            review["in_scope"] = False
            review["scope_state"] = "改关联，旧评审组回执失效"
            review["conflict"] = {
                "code": "GRAIN_RELINKED",
                "old_grain_id": old_grain,
                "new_grain_id": new_grain,
                "old_group": review.get("reviewer_group"),
                "old_receipt_no": review.get("receive_receipt_no") or review.get("dispatch_receipt_no"),
                "explanation": (
                    f"旧{review.get('reviewer_group')}的接收/派单回执属于 {old_grain}；"
                    f"条目现已改关联到 {new_grain}，旧回执与新归属冲突。"
                    "旧组不能继续完成该项，应按新归属创建复核并由新评审组再审。"
                ),
            }
        else:
            review["in_scope"] = matches
            review["scope_state"] = "有效处理范围" if matches else review.get("scope_state", "不在当前归属范围")
        return matches

    def _invalidate_active_review(
        self,
        row: dict[str, Any],
        reason: str,
        *,
        force: bool = False,
        new_grain_id: str | None = None,
    ) -> dict[str, Any] | None:
        review_id = _as_int(row.get("active_review_id"))
        review = self.reviews.get(review_id) if review_id is not None else None
        if review is None:
            return None
        if review.get("status") == FINAL_ACCEPTED:
            return review
        if not force and self._current_grain_matches(review, row):
            # 已经有按当前归属重建的有效复核时，不能把它误判成旧回执。
            return review
        target_grain = new_grain_id or row.get("关联声明编号")
        review["status"] = STALE_STATUS
        review["in_scope"] = False
        review["invalid_reason"] = reason
        review["invalidated_at"] = _now()
        receipt_no = review.get("receive_receipt_no") or review.get("dispatch_receipt_no")
        review["conflict"] = {
            "code": "GRAIN_RELINKED",
            "old_grain_id": review.get("grain_id"),
            "new_grain_id": target_grain,
            "old_group": review.get("reviewer_group"),
            "old_receipt_no": receipt_no,
            "explanation": (
                f"旧{review.get('reviewer_group')}尚未签收；关联变更后，回执 {receipt_no} "
                f"与新归属 {target_grain} 冲突，旧组不能再签收，需转入新组再审。"
            ),
        }
        return review

    def _create_review_locked(
        self,
        row: dict[str, Any],
        *,
        batch_id: str,
        submitter: str,
        reason: str = "批量确认派生",
        replaces: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        grain = self._grain_row(row.get("关联声明编号"))
        group = self._group_name(grain)
        self._review_seq += 1
        review_id = self._review_seq
        review_no = f"REV-{review_id:04d}"
        version = int(row.get("association_version") or 1)
        review: dict[str, Any] = {
            "id": review_id,
            "review_no": review_no,
            "source_module": "summary_layer",
            "source_id": int(row["id"]),
            "summary_no": row.get("汇总编号"),
            "batch_id": batch_id,
            "grain_id": row.get("关联声明编号"),
            "grain_name": grain.get("一行代表") if grain else "",
            "grain_category": grain.get("声明类别") if grain else "",
            "reviewer_group": group,
            "group_code": grain.get("reviewer_group") if grain else "",
            "association_version": version,
            "status": "待接收",
            "dispatch_state": "已派",
            "in_scope": True,
            "scope_state": "有效处理范围",
            "reason": reason,
            "submitter": submitter,
            "submitted_at": _now(),
            "received_by": "",
            "received_at": "",
            "signed_by": "",
            "signed_at": "",
            "result": None,
            "replaces_review_id": replaces.get("id") if replaces else None,
            "replaces_review_no": replaces.get("review_no") if replaces else None,
        }
        receipt = self._new_receipt(review, "dispatch", "已派", batch_id=batch_id, entry_id=int(row["id"]))
        review["dispatch_receipt_no"] = receipt["receipt_no"]
        review["receipt_no"] = receipt["receipt_no"]
        self.reviews[review_id] = review
        row["active_review_id"] = review_id
        row["active_batch_id"] = batch_id
        row["复核组"] = group
        row["复核状态"] = "待接收"
        row["复核回执"] = receipt["receipt_no"]
        return review

    def _reroute_review_locked(self, old: dict[str, Any], row: dict[str, Any], batch_id: str, actor: str) -> dict[str, Any]:
        old = self.reviews[int(old["id"])]
        if old.get("status") != FINAL_ACCEPTED:
            old["status"] = STALE_STATUS
            old["in_scope"] = False
            old["scope_state"] = "改关联，旧评审组回执失效"
        new = self._create_review_locked(
            row,
            batch_id=batch_id or old.get("batch_id") or "",
            submitter=actor,
            reason="改关联后按新归属重新复核",
            replaces=old,
        )
        new["rerouted_from"] = old.get("review_no")
        old["rerouted_to"] = new.get("review_no")
        return new

    # ------------------------------------------------------------------
    # 关联修改
    # ------------------------------------------------------------------
    def relink_entry(
        self,
        entry_id: int | str,
        grain_ref: str,
        *,
        expected_version: int | None = None,
        operator: str = "汇总员",
        reroute: bool = False,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        with self._lock:
            row = self._summary_row(entry_id)
            if row is None:
                return False, f"汇总条目 {entry_id} 不存在", None
            self._ensure_row_state(row)
            current_version = int(row.get("association_version") or 1)
            if expected_version is not None and int(expected_version) != current_version:
                return False, (
                    f"关联版本已变化：回执/页面版本为 v{expected_version}，"
                    f"当前为 v{current_version}；请按最新回执重试"
                ), {"code": "VERSION_CONFLICT", "current_version": current_version}
            grain = self._grain_row(grain_ref)
            if grain is None:
                return False, f"粒度声明 {grain_ref} 不存在", {"code": "GRAIN_NOT_FOUND"}
            new_grain = str(grain.get("声明编号") or grain_ref)
            old_grain = str(row.get("关联声明编号") or "")
            old_review = None
            stale_receipt = None
            if old_grain != new_grain:
                old_review = self._invalidate_active_review(
                    row, "人工修改关联", force=True, new_grain_id=new_grain)
                if old_review is not None:
                    stale_receipt = old_review.get("receive_receipt_no") or old_review.get("dispatch_receipt_no")
                row["关联声明编号"] = new_grain
                current_version += 1
                row["association_version"] = current_version
                row["复核组"] = self._group_name(grain)
                row["复核状态"] = "改关联待重试" if old_review and old_review.get("status") != FINAL_ACCEPTED else row.get("复核状态")
                self._audit("修改关联", str(row.get("汇总编号") or entry_id), old_grain, new_grain, operator)
            new_review = None
            if reroute and old_review is not None and old_review.get("status") != FINAL_ACCEPTED:
                batch_id = str(old_review.get("batch_id") or f"BATCH-MANUAL-{_as_int(entry_id)}")
                if batch_id not in self.batches:
                    self.batches[batch_id] = {
                        "id": batch_id, "status": "续做中", "entry_ids": [int(row["id"])],
                        "original_grain_id": old_grain, "results": {}, "created_at": _now(),
                    }
                new_review = self._reroute_review_locked(old_review, row, batch_id, operator)
            retained = bool(row.get("confirmation", {}).get("state") == "accepted")
            payload = {
                "ok": True,
                "entry": row,
                "old_grain_id": old_grain,
                "new_grain_id": new_grain,
                "current_version": current_version,
                "stale_receipt_no": stale_receipt,
                "old_review": old_review,
                "new_review": new_review,
                "accepted_result_retained": retained,
                "next_action": "请按原批次/回执重试，由新归属评审组再审" if stale_receipt and not retained else "",
            }
            if retained:
                message = f"关联已改为 {new_grain}；先前已接受结果保留：{row['confirmation'].get('message')}"
            elif stale_receipt:
                message = (
                    f"关联已从 {old_grain} 改为 {new_grain}；旧回执 {stale_receipt} 已冲突，"
                    "旧评审组不能继续完成，请按回执重试并转入新组再审"
                )
            else:
                message = f"关联已改为 {new_grain}（v{current_version}）"
            return True, message, payload

    # ------------------------------------------------------------------
    # 批次提交与续做
    # ------------------------------------------------------------------
    def _normalize_ids(self, entry_ids: list[Any] | None) -> list[int]:
        result: list[int] = []
        for value in entry_ids or []:
            if isinstance(value, dict):
                value = value.get("id") or value.get("entry_id") or value.get("source_id")
            entry_id = _as_int(value)
            if entry_id is not None and entry_id not in result:
                result.append(entry_id)
        return result

    def create_draft(self, entry_ids: list[Any], *, grain_id: str | None = None, operator: str = "汇总员") -> tuple[bool, str, dict[str, Any]]:
        with self._lock:
            ids = self._normalize_ids(entry_ids)
            if not ids:
                return False, "请至少选择一条汇总条目", {"code": "EMPTY_SELECTION"}
            grains: list[str] = []
            preliminary_results: dict[str, dict[str, Any]] = {}
            for entry_id in ids:
                row = self._summary_row(entry_id)
                if row is None:
                    return False, f"汇总条目 {entry_id} 不存在", {"code": "NOT_FOUND"}
                self._ensure_row_state(row)
                grains.append(str(row.get("关联声明编号") or ""))
                item = self._item_result(
                    entry_id, "pending_submit", "pending_submit", "待提交", False,
                    "条目已保存在本批，尚未送出；提交时会重新核对声明归属和并发认领",
                    row=row,
                )
                item["pending_selection"] = True
                preliminary_results[str(entry_id)] = item
            target_grain = grain_id or (grains[0] if len(set(grains)) == 1 else "")
            if not target_grain or len(set(grains)) != 1:
                return False, "只能将关联同一粒度声明的条目选成一批；当前选择包含：" + "、".join(sorted(set(g for g in grains if g))), {
                    "code": "GRAIN_MISMATCH", "grains": grains,
                }
            self._batch_seq += 1
            batch_id = f"BATCH-{self._batch_seq:04d}"
            batch: dict[str, Any] = {
                "id": batch_id,
                "batch_no": batch_id,
                "status": "待提交",
                "case": "待提交",
                "entry_ids": ids,
                "grain_id": target_grain,
                "original_grain_id": target_grain,
                "submitter": operator,
                "created_at": _now(),
                "submitted_at": "",
                "results": preliminary_results,
                "items": [],
            }
            self.batches[batch_id] = batch
            self._finish_batch(batch)
            batch["status"] = "待提交"
            batch["case"] = "待提交"
            batch["message"] = f"已保存待提交批次 {batch_id}，共 {len(ids)} 条；尚未送出的条目保留在原选择"
            return True, batch["message"], self._batch_payload(batch)

    def submit_batch(
        self,
        entry_ids: list[Any] | None = None,
        *,
        batch_id: str | None = None,
        grain_id: str | None = None,
        operator: str = "汇总员",
        idempotency_key: str | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        with self._lock:
            if idempotency_key and idempotency_key in self.idempotency:
                existing_id = self.idempotency[idempotency_key]
                existing = self.batches.get(existing_id)
                if existing:
                    return True, f"相同提交已处理，沿用批次 {existing_id} 的回执", self._batch_payload(existing)
            if batch_id and batch_id in self.batches:
                batch = self.batches[batch_id]
                ids = list(batch.get("entry_ids") or [])
            else:
                ids = self._normalize_ids(entry_ids)
                if not ids:
                    return False, "请至少选择一条汇总条目", {"code": "EMPTY_SELECTION"}
                ok, message, draft = self.create_draft(ids, grain_id=grain_id, operator=operator)
                if not ok:
                    return False, message, draft
                batch_id = str(draft.get("batch_id") or draft.get("id") or draft.get("batch", {}).get("id"))
                batch = self.batches[batch_id]
            if idempotency_key:
                self.idempotency[idempotency_key] = batch_id
            batch["status"] = "提交中"
            batch["submitted_at"] = _now()
            original_grain = str(grain_id or batch.get("grain_id") or batch.get("original_grain_id") or "")
            for entry_id in ids:
                self._dispatch_item(batch, entry_id, original_grain, operator=operator, retry=False)
            self._finish_batch(batch)
            payload = self._batch_payload(batch)
            return bool(payload["ok"]), payload["message"], payload

    def _dispatch_item(
        self,
        batch: dict[str, Any],
        entry_id: int,
        original_grain: str,
        *,
        operator: str,
        retry: bool,
        receipt_no: str | None = None,
    ) -> dict[str, Any]:
        row = self._summary_row(entry_id)
        batch_id = str(batch["id"])
        if row is None:
            result = self._item_result(entry_id, "failed", "not_found", "待提交", False, "汇总条目不存在")
            batch["results"][str(entry_id)] = result
            return result
        self._ensure_row_state(row)
        summary_no = str(row.get("汇总编号") or entry_id)

        accepted = row.get("confirmation") if isinstance(row.get("confirmation"), dict) else None
        if accepted and accepted.get("state") == "accepted":
            result = self._item_result(
                entry_id, "accepted", "already_accepted", "已接受", False,
                "沿用已有逐条确认的结果，不重复送审", row=row, accepted_result=accepted,
            )
            result["retryable"] = False
            batch["results"][str(entry_id)] = result
            return result

        current_grain = str(row.get("关联声明编号") or "")
        if self._grain_row(current_grain) is None:
            result = self._item_result(
                entry_id, "failed", "grain_not_found", "关联声明无效", True,
                f"{summary_no} 关联的粒度声明「{current_grain or '空'}」不存在；请先修改关联后再续做",
                row=row,
            )
            batch["results"][str(entry_id)] = result
            return result
        expected_grain = original_grain or str(batch.get("original_grain_id") or "")
        if expected_grain and current_grain and current_grain != expected_grain:
            existing_current = self._find_review(row.get("active_review_id"))
            if existing_current is not None:
                self._sync_review_validity(existing_current)
            old_review = self._invalidate_active_review(row, "提交/续做时发现改关联")
            if retry:
                if (
                    existing_current is not None
                    and self._current_grain_matches(existing_current, row)
                    and existing_current.get("status") in ACTIVE_REVIEW_STATUSES
                ):
                    receipt_no2 = existing_current.get("receive_receipt_no") or existing_current.get("dispatch_receipt_no")
                    result = self._item_result(
                        entry_id, "dispatched", "already_rerouted", "已派", False,
                        f"新归属复核 {existing_current.get('review_no')} 已创建，沿用回执 {receipt_no2}，请勿重复建项",
                        row=row, review=existing_current,
                        receipt=self.receipts.get(str(receipt_no2)) if receipt_no2 else None,
                    )
                    batch["results"][str(entry_id)] = result
                    return result
                if old_review is None:
                    old_review = existing_current
                if old_review is not None and old_review.get("status") == FINAL_ACCEPTED:
                    pass
                else:
                    new_review = self._reroute_review_locked(old_review or {
                        "id": None, "review_no": None, "reviewer_group": "原评审组",
                        "grain_id": expected_grain, "association_version": 1,
                    }, row, batch_id, operator) if old_review else self._create_review_locked(
                        row, batch_id=batch_id, submitter=operator, reason="按当前关联补建复核")
                    result = self._item_result(
                        entry_id, "rerouted", "grain_relinked_rerouted", "改关联", True,
                        f"检测到改关联，已按 {current_grain} 创建 {new_review['review_no']}，归{new_review['reviewer_group']}再审",
                        row=row, review=new_review,
                    )
                    result["new_review_id"] = new_review["id"]
                    result["new_receipt_no"] = new_review["dispatch_receipt_no"]
                    result["pending_selection"] = False
                    batch["results"][str(entry_id)] = result
                    return result
            stale_receipt = None
            conflict = None
            if old_review is not None:
                stale_receipt = old_review.get("receive_receipt_no") or old_review.get("dispatch_receipt_no")
                conflict = old_review.get("conflict")
            receipt = self._new_receipt(
                old_review, "conflict", "改关联", batch_id=batch_id, entry_id=entry_id,
                extra={"stale_receipt_no": stale_receipt, "current_grain_id": current_grain},
            )
            result = self._item_result(
                entry_id, "relinked", "grain_relinked", "改关联", True,
                f"{summary_no} 已从 {expected_grain} 改关联到 {current_grain}；"
                f"旧回执 {stale_receipt or receipt['receipt_no']} 与新归属冲突，请按回执重试，由新评审组再审",
                row=row, review=old_review, receipt=receipt,
            )
            result["stale_receipt_no"] = stale_receipt or receipt["receipt_no"]
            result["current_grain_id"] = current_grain
            result["pending_selection"] = True
            batch["results"][str(entry_id)] = result
            return result

        active_review = self._find_review(row.get("active_review_id"))
        if active_review is not None:
            self._sync_review_validity(active_review)
            if active_review.get("status") == FINAL_ACCEPTED:
                result = self._item_result(
                    entry_id, "accepted", "already_accepted", "已接受", False,
                    "沿用已有逐条确认的结果", row=row, review=active_review,
                    accepted_result=active_review.get("result"),
                )
                batch["results"][str(entry_id)] = result
                return result
            owner_batch = str(row.get("active_batch_id") or "")
            if owner_batch and owner_batch != batch_id and active_review.get("status") in ACTIVE_REVIEW_STATUSES:
                result = self._item_result(
                    entry_id, "claimed", "claimed_by_other_batch", "已被领走", False,
                    f"已被批次 {owner_batch} 先领走，有效评审组为 {active_review.get('reviewer_group')}；本批未送出项保留在原选择中",
                    row=row, review=active_review,
                )
                result["owner_batch_id"] = owner_batch
                result["pending_selection"] = True
                result["retryable"] = True  # 可在对方办结后续做，以沿用接受结果。
                batch["results"][str(entry_id)] = result
                return result
            if active_review.get("status") in ACTIVE_REVIEW_STATUSES:
                receipt_no2 = active_review.get("receive_receipt_no") or active_review.get("dispatch_receipt_no")
                receipt = self.receipts.get(str(receipt_no2)) if receipt_no2 else None
                result = self._item_result(
                    entry_id, "dispatched", "already_dispatched", "已派", False,
                    f"复核项 {active_review.get('review_no')} 已派给{active_review.get('reviewer_group')}，按已有回执 {receipt_no2} 处理",
                    row=row, review=active_review, receipt=receipt,
                )
                result["pending_selection"] = False
                batch["results"][str(entry_id)] = result
                return result

        if row.get("status") == "派生失败" and not retry:
            # 首次批量提交也允许把失败项送入复核；这里不阻断。
            pass
        review = self._create_review_locked(row, batch_id=batch_id, submitter=operator)
        if row.get("status") in {"待派生", "派生失败"}:
            row["status"] = "派生中"
            row["pending"] = True
            row["abnormal"] = False
        result = self._item_result(
            entry_id, "dispatched", "review_created", "已派", False,
            f"已跨模块创建 {review['review_no']}，并派给{review['reviewer_group']}",
            row=row, review=review, receipt=self.receipts.get(review["dispatch_receipt_no"]),
        )
        batch["results"][str(entry_id)] = result
        return result

    def _item_result(
        self,
        entry_id: int,
        state: str,
        code: str,
        case: str,
        retryable: bool,
        message: str,
        *,
        row: dict[str, Any] | None = None,
        review: dict[str, Any] | None = None,
        receipt: dict[str, Any] | None = None,
        accepted_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "entry_id": entry_id,
            "id": entry_id,
            "summary_no": (row or {}).get("汇总编号"),
            "state": state,
            "code": code,
            "case": case,
            "retryable": retryable,
            "ok": state not in {"failed", "relinked"},
            "message": message,
            "grain_id": (row or {}).get("关联声明编号") or (review or {}).get("grain_id"),
            "association_version": (row or {}).get("association_version") or (review or {}).get("association_version"),
            "review_id": (review or {}).get("id"),
            "review_no": (review or {}).get("review_no"),
            "receipt_no": (receipt or {}).get("receipt_no") or (review or {}).get("receipt_no"),
            "reviewer_group": (review or {}).get("reviewer_group"),
            "accepted_result": accepted_result,
            "pending_selection": state in {"relinked", "claimed", "failed", "pending"},
        }

    def _finish_batch(self, batch: dict[str, Any]) -> None:
        items = [batch["results"].get(str(i)) for i in batch.get("entry_ids", [])]
        items = [item for item in items if item]
        batch["items"] = items
        counts: dict[str, int] = {}
        for item in items:
            counts[item["state"]] = counts.get(item["state"], 0) + 1
        batch["counts"] = counts
        unresolved = [
            int(item["entry_id"]) for item in items
            if item.get("state") in {"relinked", "failed", "pending", "pending_submit"}
        ]
        pending_selection = [
            int(item["entry_id"]) for item in items
            if item.get("pending_selection") or item.get("state") in {"relinked", "claimed", "failed", "pending", "pending_submit"}
        ]
        batch["retry_entry_ids"] = unresolved
        batch["pending_selection_ids"] = pending_selection
        if counts.get("failed"):
            batch["status"] = "部分失败" if counts.get("dispatched") or counts.get("accepted") or counts.get("rerouted") else "失败"
        elif counts.get("relinked"):
            batch["status"] = "待按回执续做"
        elif counts.get("pending_submit") == len(items):
            batch["status"] = "待提交"
        elif counts.get("claimed"):
            batch["status"] = "部分被领走"
        elif all(item["state"] in {"accepted"} for item in items):
            batch["status"] = "已完成"
        elif any(item["state"] in {"dispatched", "rerouted"} for item in items):
            batch["status"] = "复核处理中"
        else:
            batch["status"] = "已提交"
        ok = not counts.get("failed") and not counts.get("relinked")
        batch["ok"] = ok
        parts = [f"{state} {count}" for state, count in counts.items()]
        batch["message"] = f"批次 {batch['id']}：" + ("，".join(parts) or "无条目")
        if unresolved:
            batch["message"] += f"；可失败续做 {len(unresolved)} 条"
        if pending_selection:
            batch["message"] += f"；{len(pending_selection)} 条未送出仍保留在原选择"

    def _batch_payload(self, batch: dict[str, Any]) -> dict[str, Any]:
        items = [batch["results"].get(str(i)) for i in batch.get("entry_ids", [])]
        items = [item for item in items if item]
        succeeded_states = {"accepted", "dispatched", "rerouted"}
        failed_states = {"failed", "rejected"}
        conflict_states = {"relinked", "claimed"}
        succeeded = [item for item in items if item.get("state") in succeeded_states]
        failed = [item for item in items if item.get("state") in failed_states]
        conflicts = [item for item in items if item.get("state") in conflict_states]
        return {
            "ok": bool(batch.get("ok", True)),
            "success": bool(batch.get("ok", True)),
            "message": batch.get("message") or f"批次 {batch.get('id')}",
            "batch_id": batch.get("id"),
            "batch_no": batch.get("id"),
            "batch": {k: v for k, v in batch.items() if k != "results"},
            "items": items,
            "results": items,
            "outcomes": items,
            "result_by_id": dict(batch.get("results", {})),
            "succeeded": succeeded,
            "successful_items": succeeded,
            "failed": failed,
            "failures": failed,
            "conflicts": conflicts,
            "success_count": len(succeeded),
            "failed_count": len(failed),
            "failure_count": len(failed),
            "conflict_count": len(conflicts),
            "total_count": len(items),
            "retry_entry_ids": batch.get("retry_entry_ids", []),
            "failed_ids": [int(item["entry_id"]) for item in failed],
            "pending_selection_ids": batch.get("pending_selection_ids", []),
            "pending_ids": batch.get("pending_selection_ids", []),
            "receipts": [r for r in self.receipts.values() if r.get("batch_id") == batch.get("id")],
        }

    def retry_batch(
        self,
        batch_ref: str,
        entry_ids: list[Any] | None = None,
        *,
        receipt_no: str | None = None,
        operator: str = "汇总员",
    ) -> tuple[bool, str, dict[str, Any] | None]:
        with self._lock:
            batch = self.batches.get(batch_ref)
            receipt = self.receipts.get(receipt_no) if receipt_no else None
            if batch is None and receipt is not None and receipt.get("batch_id"):
                batch = self.batches.get(str(receipt["batch_id"]))
            if batch is None:
                return False, f"批次 {batch_ref} 不存在", {"code": "BATCH_NOT_FOUND"}
            ids = self._normalize_ids(entry_ids)
            if not ids and receipt is not None and receipt.get("entry_id") is not None:
                ids = [int(receipt["entry_id"])]
            if not ids:
                ids = [
                    int(eid) for eid in batch.get("retry_entry_ids") or []
                ] or [
                    int(item["entry_id"]) for item in batch.get("items", [])
                    if item.get("retryable")
                ]
            if not ids:
                payload = self._batch_payload(batch)
                return True, "没有需要续做的失败/冲突条目", payload
            original_grain = str(batch.get("original_grain_id") or batch.get("grain_id") or "")
            for entry_id in ids:
                self._dispatch_item(
                    batch, entry_id, original_grain,
                    operator=operator, retry=True, receipt_no=receipt_no,
                )
            self._finish_batch(batch)
            payload = self._batch_payload(batch)
            payload["retried_entry_ids"] = ids
            return bool(payload["ok"]), "续做完成：" + payload["message"], payload

    def get_batch(self, batch_ref: str) -> dict[str, Any] | None:
        with self._lock:
            batch = self.batches.get(batch_ref)
            return self._batch_payload(batch) if batch else None

    def get_receipt(self, receipt_no: str) -> dict[str, Any] | None:
        with self._lock:
            receipt = self.receipts.get(receipt_no)
            if receipt is None:
                return None
            review = self.reviews.get(int(receipt["review_id"])) if receipt.get("review_id") else None
            batch = self.batches.get(str(receipt.get("batch_id"))) if receipt.get("batch_id") else None
            return {
                **receipt,
                "review": dict(review) if review else None,
                "batch": self._batch_payload(batch) if batch else None,
            }

    def retry_receipt(self, receipt_no: str, *, operator: str = "汇总员") -> tuple[bool, str, dict[str, Any] | None]:
        with self._lock:
            receipt = self.receipts.get(receipt_no)
            if receipt is None:
                return False, f"回执 {receipt_no} 不存在", {"code": "RECEIPT_NOT_FOUND"}
            batch_id = str(receipt.get("batch_id") or "")
            entry_id = receipt.get("entry_id")
            if batch_id and batch_id in self.batches:
                return self.retry_batch(batch_id, [entry_id] if entry_id else None, receipt_no=receipt_no, operator=operator)
            if entry_id is None:
                return False, "回执未关联汇总条目", {"code": "BAD_RECEIPT"}
            ok, message, payload = self.submit_batch([entry_id], operator=operator)
            return ok, message, payload

    def confirm_one(self, entry_id: int | str, *, operator: str = "汇总员") -> tuple[bool, str, dict[str, Any] | None]:
        with self._lock:
            row = self._summary_row(entry_id)
            if row is None:
                return False, f"汇总条目 {entry_id} 不存在", None
            self._ensure_row_state(row)
            self._batch_seq += 1
            batch_id = f"BATCH-SINGLE-{self._batch_seq:04d}"
            batch = {
                "id": batch_id, "status": "提交中", "entry_ids": [int(row["id"])],
                "grain_id": row.get("关联声明编号"), "original_grain_id": row.get("关联声明编号"),
                "results": {}, "created_at": _now(),
            }
            self.batches[batch_id] = batch
            self._dispatch_item(batch, int(row["id"]), str(row.get("关联声明编号") or ""), operator=operator, retry=False)
            self._finish_batch(batch)
            payload = self._batch_payload(batch)
            item = payload["items"][0] if payload["items"] else None
            return bool(item and item["ok"]), str(item["message"] if item else "确认失败"), payload

    # ------------------------------------------------------------------
    # 粒度声明页：复核待办、接收、签收
    # ------------------------------------------------------------------
    def list_reviews(
        self,
        *,
        reviewer_group: str | None = None,
        grain_id: str | None = None,
        scope: str = "todo",
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            result: list[dict[str, Any]] = []
            for review in sorted(self.reviews.values(), key=lambda item: item["id"]):
                self._sync_review_validity(review)
                if reviewer_group and not self.group_matches(review.get("reviewer_group"), reviewer_group) and not self.group_matches(review.get("group_code"), reviewer_group):
                    continue
                if grain_id and str(review.get("grain_id")) != str(grain_id):
                    grain = self._grain_row(grain_id)
                    if not grain or str(grain.get("声明编号")) != review.get("grain_id"):
                        continue
                if status and review.get("status") != status:
                    continue
                if scope == "todo" and review.get("status") not in ACTIVE_REVIEW_STATUSES:
                    continue
                if scope == "valid" and not review.get("in_scope"):
                    continue
                if scope == "invalid" and review.get("in_scope"):
                    continue
                if scope == "done" and review.get("status") not in {FINAL_ACCEPTED, FINAL_REJECTED}:
                    continue
                result.append(dict(review))
            return result

    def receive_review(
        self,
        review_ref: int | str,
        *,
        reviewer_group: str | None = None,
        reviewer: str | None = None,
        receipt_no: str | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        with self._lock:
            review = self._find_review(review_ref)
            if review is None:
                return False, f"复核项 {review_ref} 不存在", {"code": "REVIEW_NOT_FOUND"}
            if reviewer_group and not self.group_matches(review.get("reviewer_group"), reviewer_group) and not self.group_matches(review.get("group_code"), reviewer_group):
                scope = self.review_scope(reviewer_group, reviewer)
                return False, (
                    f"当前复核人不在 {review.get('reviewer_group')} 处理范围内，不能接收 {review.get('review_no')}；"
                    f"当前仅可处理 {('、'.join(scope['declaration_ids']) or '无')}"
                ), {"code": "OUT_OF_SCOPE", "scope": scope, "review": review}
            row = self._summary_row(review.get("source_id"))
            if review.get("status") == FINAL_ACCEPTED:
                return True, f"{review.get('review_no')} 已签收，沿用先前接受结果", {"review": review, "accepted_result": review.get("result")}
            in_scope = self._sync_review_validity(review)
            if not in_scope:
                new_review = None
                if row is not None and review.get("status") == STALE_STATUS:
                    batch_id = str(review.get("batch_id") or f"BATCH-REROUTE-{row['id']}")
                    if batch_id not in self.batches:
                        self.batches[batch_id] = {"id": batch_id, "entry_ids": [int(row["id"])], "results": {}}
                    new_review = self._reroute_review_locked(review, row, batch_id, reviewer or review.get("reviewer_group") or "新评审组")
                conflict = review.get("conflict") or {}
                return False, str(conflict.get("explanation") or "复核项已因改关联超出处理范围"), {
                    "code": "OWNERSHIP_CONFLICT", "review": review, "new_review": new_review,
                    "new_review_no": (new_review or {}).get("review_no"),
                    "new_receipt_no": (new_review or {}).get("dispatch_receipt_no"),
                    "new_group": (new_review or {}).get("reviewer_group"),
                }
            if review.get("status") == "已接收":
                receipt = self.receipts.get(str(review.get("receive_receipt_no")))
                return True, f"{review.get('review_no')} 已由 {review.get('received_by')} 接收，沿用接收回执", {"review": review, "receipt": receipt}
            review["status"] = "已接收"
            review["received_by"] = reviewer or review.get("reviewer_group") or "复核人"
            review["received_at"] = _now()
            receipt = self._new_receipt(
                review, "receive", "已接收",
                extra={"provided_receipt_no": receipt_no} if receipt_no else None,
            )
            review["receive_receipt_no"] = receipt["receipt_no"]
            review["receipt_no"] = receipt["receipt_no"]
            if row is not None:
                row["复核状态"] = "已接收"
                row["复核回执"] = receipt["receipt_no"]
            return True, f"{review['review_no']} 已接收；回执 {receipt['receipt_no']}，签收/重试请携带该回执", {"review": review, "receipt": receipt}

    def sign_review(
        self,
        review_ref: int | str,
        *,
        reviewer_group: str | None = None,
        reviewer: str | None = None,
        decision: str = "通过",
        receipt_no: str | None = None,
        comment: str | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        with self._lock:
            review = self._find_review(review_ref)
            if review is None:
                return False, f"复核项 {review_ref} 不存在", {"code": "REVIEW_NOT_FOUND"}
            row = self._summary_row(review.get("source_id"))

            # 已接受是最强事实：即使另一组并发/改关联后重试，也返回同一结果。
            existing_acceptance = row.get("confirmation", {}) if row is not None else {}
            if review.get("status") == FINAL_ACCEPTED or existing_acceptance.get("state") == "accepted":
                accepted = review.get("result") or existing_acceptance
                current_review = self._find_review(row.get("active_review_id")) if row is not None else None
                effective_group = (
                    (accepted or {}).get("reviewer_group")
                    or (current_review or {}).get("reviewer_group")
                    or review.get("reviewer_group")
                )
                if reviewer_group and effective_group and not self.group_matches(effective_group, reviewer_group):
                    scope = self.review_scope(reviewer_group, reviewer)
                    return False, (
                        f"该项已由有效评审组 {effective_group} 先前接受，结果保留；"
                        f"{reviewer_group} 不是当前有效评审组，不能再次签收。"
                        f"该复核人实际可处理 {('、'.join(scope['declaration_ids']) or '无')}"
                    ), {
                        "code": "ALREADY_ACCEPTED_BY_VALID_GROUP",
                        "out_of_scope": True,
                        "scope": scope,
                        "review": review,
                        "current_review": current_review,
                        "accepted_result": accepted,
                    }
                return True, f"{review.get('review_no')} 先前已接受，结果保留且不产生第二个有效评审组", {
                    "code": "ALREADY_ACCEPTED", "review": review, "accepted_result": accepted,
                }

            if reviewer_group and not self.group_matches(review.get("reviewer_group"), reviewer_group) and not self.group_matches(review.get("group_code"), reviewer_group):
                scope = self.review_scope(reviewer_group, reviewer)
                current = None
                if row is not None:
                    current = self._find_review(row.get("active_review_id"))
                return False, (
                    f"{review.get('review_no')} 已不归 {reviewer_group} 处理；"
                    f"该复核人实际只能处理 {('、'.join(scope['declaration_ids']) or '无')}。"
                    "一个条目只归一个有效评审组"
                ), {"code": "OUT_OF_SCOPE", "scope": scope, "review": review, "current_review": current}

            in_scope = self._sync_review_validity(review)
            if not in_scope:
                new_review = None
                if row is not None:
                    new_review = self._find_review(row.get("active_review_id"))
                    if new_review is None or new_review.get("id") == review.get("id"):
                        batch_id = str(review.get("batch_id") or f"BATCH-REROUTE-{row['id']}")
                        if batch_id not in self.batches:
                            self.batches[batch_id] = {"id": batch_id, "entry_ids": [int(row["id"])], "results": {}}
                        new_review = self._reroute_review_locked(review, row, batch_id, reviewer or "新评审组")
                accepted = (row or {}).get("confirmation")
                return False, str((review.get("conflict") or {}).get("explanation") or "回执已因改关联冲突"), {
                    "code": "OWNERSHIP_CONFLICT", "review": review, "new_review": new_review,
                    "accepted_result": accepted,
                }

            if review.get("status") == "待接收":
                # 兼容直接签收；自动补接收记录。
                self.receive_review(review["id"], reviewer_group=reviewer_group, reviewer=reviewer, receipt_no=receipt_no)
                review = self.reviews[int(review["id"])]

            approve = str(decision).strip() not in {"驳回", "拒绝", "reject", "rejected"}
            operator = reviewer or review.get("received_by") or reviewer_group or "复核人"
            review["signed_by"] = operator
            review["signed_at"] = _now()
            review["comment"] = comment or ""
            if approve:
                review["status"] = FINAL_ACCEPTED
                review["dispatch_state"] = "已接受"
                result = {
                    "state": "accepted", "result": "accepted", "decision": decision,
                    "accepted_by": operator, "accepted_at": review["signed_at"],
                    "review_id": review["id"], "review_no": review["review_no"],
                    "reviewer_group": review.get("reviewer_group"),
                    "receipt_no": receipt_no or review.get("receive_receipt_no"),
                    "message": "复核通过，沿用本次逐条确认结果",
                }
                review["result"] = result
                if row is not None:
                    row["status"] = "已派生"
                    row["pending"] = False
                    row["abnormal"] = False
                    row["confirmation"] = result
                    row["复核状态"] = "已签收"
                    self._audit("复核签收", str(row.get("汇总编号") or row.get("id")), "复核中", "已派生", operator)
                message = f"{review['review_no']} 已由有效评审组 {review.get('reviewer_group')} 签收通过，汇总条目已确认派生"
            else:
                review["status"] = FINAL_REJECTED
                review["dispatch_state"] = "已驳回"
                result = {
                    "state": "rejected", "result": "rejected", "decision": decision,
                    "rejected_by": operator, "rejected_at": review["signed_at"],
                    "review_id": review["id"], "review_no": review["review_no"],
                    "reviewer_group": review.get("reviewer_group"),
                    "message": comment or "复核驳回，可失败续做",
                }
                review["result"] = result
                if row is not None:
                    row["status"] = "派生失败"
                    row["pending"] = True
                    row["abnormal"] = True
                    row["confirmation"] = result
                    row["复核状态"] = "已驳回"
                    self._audit("复核驳回", str(row.get("汇总编号") or row.get("id")), "复核中", "派生失败", operator)
                message = f"{review['review_no']} 已驳回；可在汇总层按批次失败续做"
            sign_receipt = self._new_receipt(review, "sign", review["status"], extra={"decision": decision})
            review["sign_receipt_no"] = sign_receipt["receipt_no"]
            batch = self.batches.get(str(review.get("batch_id") or ""))
            if batch is not None and row is not None:
                if approve:
                    batch_item = self._item_result(
                        int(row["id"]), "accepted", "review_accepted", "已接受", False,
                        message, row=row, review=review, accepted_result=result)
                else:
                    batch_item = self._item_result(
                        int(row["id"]), "failed", "review_rejected", "复核驳回", True,
                        message, row=row, review=review, accepted_result=result)
                batch["results"][str(row["id"])] = batch_item
                self._finish_batch(batch)
            return True, message, {"review": review, "receipt": sign_receipt, "accepted_result": review.get("result")}


workflow = ReviewWorkflow()
