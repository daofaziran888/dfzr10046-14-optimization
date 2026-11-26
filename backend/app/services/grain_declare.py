"""粒度声明领域服务：声明事实表业务粒度数据集和度量可加性，校验聚合转换规则。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.review_flow import workflow
from app.store import store


class GrainDeclareService(BaseModuleService):
    MODULE = "grain_declare"
    ENTITY = "粒度声明"
    LIST_FIELDS = ["声明编号", "所属事实表", "声明类别", "评审组", "一行代表", "维度组合", "度量可加性", "重复判定键", "声明版本", "最近核对"]
    REQUIRED_FIELDS = ["声明编号", "所属事实表", "一行代表"]
    STATUS_ORDER = ["草稿", "已声明", "待修订", "已作废"]
    ACTION_RULES = {"确认声明": "已声明", "提交修订": "待修订", "作废声明": "已作废"}
    NEGATIVE_ACTIONS = []

    def list_entries(self, **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        items, total = super().list_entries(**kwargs)
        for grain in items:
            declaration_no = str(grain.get("声明编号") or "")
            reviews = workflow.list_reviews(grain_id=declaration_no, scope="all")
            active = [r for r in reviews if r.get("status") in {"待接收", "已接收"} and r.get("in_scope")]
            grain["有效评审组"] = grain.get("评审组") or workflow._group_name(grain)
            grain["复核待办数"] = len(active)
            grain["review_todo_count"] = len(active)
            grain["review_todos"] = [
                {
                    "id": r.get("id"),
                    "review_no": r.get("review_no"),
                    "source_module": r.get("source_module"),
                    "source_id": r.get("source_id"),
                    "summary_no": r.get("summary_no"),
                    "status": r.get("status"),
                    "in_scope": r.get("in_scope"),
                    "reviewer_group": r.get("reviewer_group"),
                    "receipt_no": r.get("receipt_no"),
                    "batch_id": r.get("batch_id"),
                }
                for r in reviews
            ]
        return items, total

    def declare_grain(
        self, declare_id: str, fact_table: str, one_row_means: str,
        dim_combo: str, additivity: str, dedup_key: str, version: str,
        category: str = "自定义声明", review_group: str = "粒度声明复核组",
        group_code: str = "grain-custom",
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """声明事实表粒度：一行代表 + 维度组合 + 度量可加性。"""
        rows = store.rows(self.MODULE)
        entry = {
            "声明编号": declare_id, "所属事实表": fact_table,
            "声明类别": category, "评审组": review_group,
            "reviewer_group": group_code,
            "一行代表": one_row_means, "维度组合": dim_combo,
            "度量可加性": additivity, "重复判定键": dedup_key,
            "声明版本": version, "最近核对": "2026-11-21",
            "status": "已声明", "pending": False, "abnormal": False,
        }
        entry["id"] = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append(entry)
        return True, f"粒度声明 {declare_id} 已生效：{one_row_means}", entry

    def revise_grain(self, declare_id: str, new_dims: str) -> tuple[bool, str]:
        """修订粒度声明：触发汇总层重算与下游引用更新。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("声明编号") == declare_id:
                r["status"] = "待修订"
                r["维度组合"] = new_dims
                r["pending"] = True
                return True, f"粒度声明 {declare_id} 已提交修订：{new_dims}"
        return False, f"粒度声明 {declare_id} 不存在"