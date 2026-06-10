"""
用户认证模块：注册、登录、邮箱验证码、JWT。
使用 163 SMTP 发送邮件验证码。
"""

import os
import re
import smtplib
import random
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from web.backend.database import (
    create_user,
    get_user_by_email,
    get_user_by_id,
    save_verification_code,
    verify_code,
    can_send_code,
    update_user_password,
)

# ============================================================
# JWT 配置
# ============================================================

SECRET_KEY = os.environ.get("JWT_SECRET", "hotel-tools-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ============================================================
# 163 SMTP 邮件配置
# ============================================================

SMTP_HOST = "smtp.163.com"
SMTP_PORT = 465

# 从环境变量读取
SMTP_USER = os.environ.get("SMTP_USER", "")       # 163邮箱地址
SMTP_PASS = os.environ.get("SMTP_PASS", "")        # 163邮箱授权码（非登录密码）


def send_email(to_email: str, code: str) -> bool:
    """通过 163 SMTP 发送验证码邮件。"""
    if not SMTP_USER or not SMTP_PASS:
        print(f"[模拟] 验证码 {code} 发送至 {to_email}（163 SMTP 未配置）")
        return False

    subject = "上品工具 - 邮箱验证码"
    body = f"""
<div style="max-width:600px;margin:0 auto;padding:20px;font-family:Arial,sans-serif;">
    <h2 style="color:#4f46e5;">上品工具</h2>
    <p>您好，</p>
    <p>您的验证码为：</p>
    <div style="background:#f3f4f6;padding:20px;text-align:center;border-radius:12px;margin:20px 0;">
        <span style="font-size:36px;font-weight:bold;color:#4f46e5;letter-spacing:8px;">{code}</span>
    </div>
    <p style="color:#6b7280;font-size:14px;">验证码有效期为5分钟，请勿泄露给他人。</p>
    <p style="color:#6b7280;font-size:14px;">如果这不是您的操作，请忽略此邮件。</p>
</div>
"""

    msg = MIMEText(body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = to_email

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, [to_email], msg.as_string())
        print(f"[邮件] 验证码已发送至 {to_email}")
        return True
    except Exception as e:
        print(f"[ERROR] 邮件发送失败: {e}")
        return False


# ============================================================
# 数据模型
# ============================================================

class SendCodeRequest(BaseModel):
    email: str
    purpose: str = "register"  # register | reset_password


class SendCodeResponse(BaseModel):
    success: bool
    message: str


class RegisterRequest(BaseModel):
    email: str
    code: str
    password: str


class RegisterResponse(BaseModel):
    success: bool
    message: str
    account_no: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    success: bool
    message: str
    token: str = ""
    user: dict = {}


class ResetPasswordRequest(BaseModel):
    email: str
    code: str
    new_password: str


class ResetPasswordResponse(BaseModel):
    success: bool
    message: str


class UserInfoResponse(BaseModel):
    id: int
    email: str
    account_no: str
    plan: str
    plan_expires_at: str = ""


# ============================================================
# 工具函数
# ============================================================

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """从 JWT 中解析当前用户。"""
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="无效的认证格式")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=401, detail="无效的 token")
    except JWTError:
        raise HTTPException(status_code=401, detail="无效的 token")

    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


def require_plan(user: dict = Depends(get_current_user)) -> dict:
    """要求用户已开通套餐。"""
    if user["plan"] == "none":
        raise HTTPException(status_code=403, detail="请先开通套餐")
    return user


EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


# ============================================================
# API 路由
# ============================================================

@router.post("/send-code", response_model=SendCodeResponse)
async def api_send_code(req: SendCodeRequest):
    """发送邮箱验证码。"""
    email = req.email.strip().lower()
    purpose = req.purpose

    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="请输入正确的邮箱地址")

    if purpose not in ("register", "reset_password"):
        raise HTTPException(status_code=400, detail="无效的验证码用途")

    # 注册时检查邮箱是否已注册
    if purpose == "register":
        existing = get_user_by_email(email)
        if existing:
            raise HTTPException(status_code=400, detail="该邮箱已注册")

    # 找回密码时检查邮箱是否已注册
    if purpose == "reset_password":
        existing = get_user_by_email(email)
        if not existing:
            raise HTTPException(status_code=400, detail="该邮箱未注册")

    if not can_send_code(email):
        raise HTTPException(status_code=429, detail="请60秒后再试")

    # 生成6位随机验证码
    code = f"{random.randint(100000, 999999)}"
    save_verification_code(email, code)

    # 发送邮件
    sent = send_email(email, code)

    if sent:
        msg = "验证码已发送至邮箱，请查收"
    else:
        msg = f"验证码已发送（模拟：{code}）"

    return SendCodeResponse(success=True, message=msg)


@router.post("/register", response_model=RegisterResponse)
async def api_register(req: RegisterRequest):
    """注册新用户。"""
    email = req.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="请输入正确的邮箱地址")

    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少6位")

    # 校验验证码
    if not verify_code(email, req.code.strip()):
        raise HTTPException(status_code=400, detail="验证码错误或已过期")

    # 创建用户
    try:
        password_raw = req.password[:72]
        password_hash = pwd_context.hash(password_raw)
        user = create_user(email, password_hash)
        return RegisterResponse(
            success=True,
            message="注册成功",
            account_no=user["account_no"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/login", response_model=LoginResponse)
async def api_login(req: LoginRequest):
    """用户登录。"""
    email = req.email.strip().lower()
    user = get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=400, detail="邮箱未注册")

    if not pwd_context.verify(req.password[:72], user["password_hash"]):
        raise HTTPException(status_code=400, detail="密码错误")

    token = create_access_token({"user_id": user["id"]})
    return LoginResponse(
        success=True,
        message="登录成功",
        token=token,
        user={
            "id": user["id"],
            "email": user["email"],
            "account_no": user["account_no"],
            "plan": user["plan"],
            "plan_expires_at": user.get("plan_expires_at") or "",
        },
    )


@router.get("/me", response_model=UserInfoResponse)
async def api_me(user: dict = Depends(get_current_user)):
    """获取当前用户信息。"""
    return UserInfoResponse(
        id=user["id"],
        email=user["email"],
        account_no=user["account_no"],
        plan=user["plan"],
        plan_expires_at=user.get("plan_expires_at") or "",
    )


@router.post("/reset-password", response_model=ResetPasswordResponse)
async def api_reset_password(req: ResetPasswordRequest):
    """找回密码：验证码校验后重置密码。"""
    email = req.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="请输入正确的邮箱地址")

    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="密码至少6位")

    # 校验验证码
    if not verify_code(email, req.code.strip()):
        raise HTTPException(status_code=400, detail="验证码错误或已过期")

    # 检查用户是否存在
    user = get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=400, detail="该邮箱未注册")

    # 更新密码
    password_raw = req.new_password[:72]
    password_hash = pwd_context.hash(password_raw)
    update_user_password(email, password_hash)

    return ResetPasswordResponse(success=True, message="密码重置成功")
