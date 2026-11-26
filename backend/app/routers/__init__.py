"""业务路由汇总：工作流精确路由先注册，最后放通用资源路由。"""
from __future__ import annotations

from app.routers import resource, workflow

ROUTERS = [workflow, resource]
