"""汇总层派生领域服务：校验事实批次派生汇总条目，版本化绑定依赖批次，产出汇总条目数据集。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.review_flow import workflow
from app.store import store


class SummaryLayerService(BaseModuleService):
    MODULE = "summary_layer"
    ENTITY = "汇总条目"
    LIST_FIELDS = ["汇总编号", "来源事实表", "汇总粒度", "关联声明编号", "度量表达式", "派生行数", "依赖批次", "刷新方式", "派生时间"]
    REQUIRED_FIELDS = ["汇总编号", "来源事实表", "汇总粒度"]
    STATUS_ORDER = ["待派生", "派生中", "已派生", "派生失败"]
    ACTION_RULES = {"启动派生": "派生中", "确认派生": "已派生", "标记失败": "派生失败"}
    NEGATIVE_ACTIONS = ["标记失败"]

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """原“确认派生”不再直接落最终状态，而是送入粒度声明页复核待办。"""
        if action in {"确认派生", "批量确认", "批量确认派生", "提交确认"}:
            ok, message, _payload = workflow.confirm_one(entry_id)
            entry = store.find(self.MODULE, entry_id)
            return (entry if ok else None), message
        return super().run_action(entry_id, action)

    def derive_summary(
        self, summary_id: str, source_table: str, granularity: str,
        expression: str, dependency_batch: str, refresh_method: str,
        expected_rows: int, grain_declaration: str = "",
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """派生汇总条目：绑定具体依赖批次，重派生不得覆盖已被引用的旧批次。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("依赖批次") == dependency_batch and r.get("status") == "已派生":
                return False, (
                    f"依赖批次 {dependency_batch} 已有汇总条目 {r.get('汇总编号')}，"
                    f"重派生不得覆盖已被引用的旧批次"
                ), None

        entry = {
            "汇总编号": summary_id, "来源事实表": source_table,
            "汇总粒度": granularity, "关联声明编号": grain_declaration,
            "度量表达式": expression,
            "派生行数": expected_rows, "依赖批次": dependency_batch,
            "刷新方式": refresh_method, "派生时间": "2026-11-21",
            "association_version": 1,
            "status": "已派生", "pending": False, "abnormal": False,
        }
        entry["id"] = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append(entry)
        return True, f"汇总条目 {summary_id} 已派生：{expected_rows} 行", entry

    def check_grain_consistency(
        self, summary_grain: str, declared_grain: str,
    ) -> tuple[bool, str]:
        """校验汇总粒度与粒度声明一致。"""
        if summary_grain != declared_grain:
            return False, f"汇总粒度 {summary_grain} 与声明粒度 {declared_grain} 不一致"
        return True, f"粒度一致：{summary_grain}"