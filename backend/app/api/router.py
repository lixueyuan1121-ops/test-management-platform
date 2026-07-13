"""集中注册所有路由。"""
from fastapi import APIRouter

from app.api import auth, issues, members, projects, reports, stats, tasks, users

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(members.router)
api_router.include_router(users.router)
api_router.include_router(tasks.router)
api_router.include_router(reports.router)
api_router.include_router(stats.router)
api_router.include_router(issues.router)
