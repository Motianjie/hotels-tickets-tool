"""
支付模块：创建订单、生成二维码、模拟支付。
"""

import os
import uuid
from pathlib import Path
from typing import Optional

import qrcode
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from web.backend.auth import get_current_user
from web.backend.database import (
    create_payment_order,
    get_payment_order,
    mark_order_paid,
    update_user_plan,
    get_user_by_id,
)

router = APIRouter(prefix="/api/payment", tags=["payment"])

# 二维码保存目录
QR_DIR = Path(__file__).resolve().parent / "static" / "qrcodes"
QR_DIR.mkdir(parents=True, exist_ok=True)

# 套餐定价
PLANS = {
    "pro": {"name": "Pro 套餐", "price": 30},
    "max": {"name": "Max 套餐", "price": 50},
}


# ============================================================
# 数据模型
# ============================================================

class CreateOrderRequest(BaseModel):
    plan: str  # 'pro' | 'max'
    method: str = "wechat"  # 'wechat' | 'alipay'


class CreateOrderResponse(BaseModel):
    success: bool
    order_id: int
    qr_code_url: str
    amount: int
    plan: str


class CheckOrderRequest(BaseModel):
    order_id: int


class CheckOrderResponse(BaseModel):
    success: bool
    status: str
    paid: bool


class MockPayRequest(BaseModel):
    order_id: int


class MockPayResponse(BaseModel):
    success: bool
    message: str


class PlansResponse(BaseModel):
    plans: list


# ============================================================
# API 路由
# ============================================================

@router.get("/plans", response_model=PlansResponse)
async def api_plans():
    """获取套餐列表。"""
    return PlansResponse(plans=[
        {"id": "pro", "name": "Pro 套餐", "price": 30, "description": "基础功能，适合个人使用"},
        {"id": "max", "name": "Max 套餐", "price": 50, "description": "全部功能，适合团队使用"},
    ])


@router.post("/create-order", response_model=CreateOrderResponse)
async def api_create_order(req: CreateOrderRequest, user: dict = Depends(get_current_user)):
    """创建支付订单，生成二维码。"""
    if req.plan not in PLANS:
        raise HTTPException(status_code=400, detail="无效的套餐")

    plan_info = PLANS[req.plan]
    amount = plan_info["price"]

    if req.method not in ("wechat", "alipay"):
        raise HTTPException(status_code=400, detail="无效的支付方式")

    # 生成二维码图片
    qr_filename = f"pay_{user['id']}_{uuid.uuid4().hex[:8]}.png"
    qr_path = str(QR_DIR / qr_filename)

    # 二维码内容：模拟支付链接
    pay_url = f"http://localhost:8000/api/payment/mock-pay-page?order_id=ORDER_ID&amount={amount}"
    qr = qrcode.make(pay_url)
    qr.save(qr_path)

    # 创建订单
    order = create_payment_order(
        user_id=user["id"],
        plan=req.plan,
        amount=amount,
        method=req.method,
        qr_path=qr_filename,
    )

    return CreateOrderResponse(
        success=True,
        order_id=order["id"],
        qr_code_url=f"/static/qrcodes/{qr_filename}",
        amount=amount,
        plan=req.plan,
    )


@router.post("/check-order", response_model=CheckOrderResponse)
async def api_check_order(req: CheckOrderRequest):
    """查询订单状态。"""
    order = get_payment_order(req.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    return CheckOrderResponse(
        success=True,
        status=order["status"],
        paid=order["status"] == "paid",
    )


@router.post("/mock-pay", response_model=MockPayResponse)
async def api_mock_pay(req: MockPayRequest, user: dict = Depends(get_current_user)):
    """模拟支付完成。"""
    order = get_payment_order(req.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权操作此订单")
    if order["status"] == "paid":
        return MockPayResponse(success=True, message="该订单已支付")

    # 标记订单已支付
    mark_order_paid(req.order_id)

    # 开通套餐
    update_user_plan(user["id"], order["plan"])

    return MockPayResponse(
        success=True,
        message=f"{PLANS[order['plan']]['name']}已开通，有效期30天",
    )
