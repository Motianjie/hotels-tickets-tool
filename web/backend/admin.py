"""
管理员 API：账号管理、套餐管理。
需要管理员权限（is_admin=1）。
"""

import re
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from passlib.context import CryptContext
from pydantic import BaseModel

from web.backend.auth import get_current_user, require_admin
from web.backend.database import (
    create_user,
    get_user_by_email,
    get_user_by_id,
    get_all_users,
    update_user_plan,
    update_user_password,
    toggle_user_status,
    generate_initial_password,
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

router = APIRouter(prefix="/api/admin", tags=["admin"])

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


# ============================================================
# 数据模型
# ============================================================

class CreateUserRequest(BaseModel):
    email: str
    plan: str = "none"
    plan_days: int = 0
    max_usage: int = 0
    note: str = ""


class CreateUserResponse(BaseModel):
    success: bool
    message: str
    email: str = ""
    initial_password: str = ""
    account_no: str = ""


class UserListItem(BaseModel):
    id: int
    email: str
    account_no: str
    plan: str
    plan_expires_at: str = ""
    usage_count: int = 0
    max_usage: int = 0
    status: str = "active"
    note: str = ""
    initial_password: str = ""
    is_admin: int = 0
    created_at: str = ""


class UserListResponse(BaseModel):
    success: bool
    users: list[UserListItem]


class UpdatePlanRequest(BaseModel):
    user_id: int
    plan: str
    days: int = 30


class UpdatePlanResponse(BaseModel):
    success: bool
    message: str


class ToggleStatusRequest(BaseModel):
    user_id: int


class ToggleStatusResponse(BaseModel):
    success: bool
    message: str
    new_status: str = ""


class ResetPasswordRequest(BaseModel):
    user_id: int


class ResetPasswordResponse(BaseModel):
    success: bool
    message: str
    new_password: str = ""


# ============================================================
# API 路由
# ============================================================

@router.post("/create-user", response_model=CreateUserResponse)
async def api_create_user(req: CreateUserRequest, admin: dict = Depends(require_admin)):
    """管理员创建账号。"""
    email = req.email.strip().lower()

    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="请输入正确的邮箱地址")

    # 检查是否已存在
    existing = get_user_by_email(email)
    if existing:
        raise HTTPException(status_code=400, detail="该邮箱已存在")

    # 生成随机初始密码
    initial_password = generate_initial_password(8)
    password_hash = pwd_context.hash(initial_password)

    # 计算套餐过期时间
    plan_expires_at = ""
    if req.plan != "none" and req.plan_days > 0:
        plan_expires_at = (datetime.now() + timedelta(days=req.plan_days)).strftime("%Y-%m-%d %H:%M:%S")

    try:
        user = create_user(
            email=email,
            password_hash=password_hash,
            initial_password=initial_password,
            plan=req.plan,
            plan_expires_at=plan_expires_at,
            max_usage=req.max_usage,
            note=req.note,
        )
        return CreateUserResponse(
            success=True,
            message="账号创建成功",
            email=email,
            initial_password=initial_password,
            account_no=user["account_no"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/users", response_model=UserListResponse)
async def api_list_users(admin: dict = Depends(require_admin)):
    """获取所有用户列表。"""
    users = get_all_users()
    return UserListResponse(
        success=True,
        users=[UserListItem(
            id=u["id"],
            email=u["email"],
            account_no=u["account_no"],
            plan=u["plan"],
            plan_expires_at=u.get("plan_expires_at") or "",
            usage_count=u.get("usage_count", 0),
            max_usage=u.get("max_usage", 0),
            status=u.get("status", "active"),
            note=u.get("note", ""),
            initial_password=u.get("initial_password", ""),
            is_admin=u.get("is_admin", 0),
            created_at=u.get("created_at", ""),
        ) for u in users],
    )


@router.post("/update-plan", response_model=UpdatePlanResponse)
async def api_update_plan(req: UpdatePlanRequest, admin: dict = Depends(require_admin)):
    """修改套餐/续费。"""
    user = get_user_by_id(req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if req.plan not in ("none", "pro", "max"):
        raise HTTPException(status_code=400, detail="无效的套餐类型")

    if req.days < 1:
        raise HTTPException(status_code=400, detail="有效期天数至少为1")

    update_user_plan(req.user_id, req.plan, req.days)

    plan_names = {"none": "无", "pro": "Pro", "max": "Max"}
    return UpdatePlanResponse(
        success=True,
        message=f"已为用户 {user['email']} 开通 {plan_names.get(req.plan, req.plan)} 套餐，有效期{req.days}天",
    )


@router.post("/toggle-status", response_model=ToggleStatusResponse)
async def api_toggle_status(req: ToggleStatusRequest, admin: dict = Depends(require_admin)):
    """启用/禁用账号。"""
    user = get_user_by_id(req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 不能禁用自己
    if req.user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="不能禁用自己")

    new_status = toggle_user_status(req.user_id)
    status_text = "启用" if new_status == "active" else "禁用"
    return ToggleStatusResponse(
        success=True,
        message=f"账号 {user['email']} 已{status_text}",
        new_status=new_status,
    )


@router.post("/reset-password", response_model=ResetPasswordResponse)
async def api_admin_reset_password(req: ResetPasswordRequest, admin: dict = Depends(require_admin)):
    """管理员重置用户密码。"""
    user = get_user_by_id(req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    new_password = generate_initial_password(8)
    password_hash = pwd_context.hash(new_password)
    update_user_password(user["email"], password_hash, initial_password=new_password)

    return ResetPasswordResponse(
        success=True,
        message=f"密码已重置",
        new_password=new_password,
    )
