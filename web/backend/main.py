"""
hotels/tickets 上品工具 - FastAPI 后端。

直接复用 src/main.py 中的完整处理逻辑。
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# 确保项目根目录在 sys.path 中
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

# 直接复用 src/main.py 中的核心函数
from src.main import (
    parse_raw_input,
    infer_highlight,
    generate_taopiao,
    extract_content_items,
    extract_surcharge,
    extract_sell_time,
    extract_available_date,
    extract_price,
    calc_settlement,
    cmd_extract,
)
from src.extractor import XiumiusExtractor
from src.lark_writer import (
    append_row_by_cols,
    check_lark_cli,
    load_config,
    get_spreadsheet_token,
    get_sheet_name,
)
from src.poster_ocr import ocr_image, parse_poster_text
from src.ai_helper import ai_extract_all, ai_extract_surcharge, ai_extract_content, ai_generate_taopiao

app = FastAPI(title="hotels/tickets API", version="1.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# 数据模型
# ============================================================


class ParseInputRequest(BaseModel):
    raw_text: str
    mode: str = "hybrid"  # script: 全脚本, ai: 全AI, hybrid: 先脚本后AI兜底
    product_type: str = "hotel"


class ParseInputResponse(BaseModel):
    title: str
    xiumius_url: str
    qianggou_url: str
    fangtai_url: str
    bianhao: str
    rebate: str
    order_no: str
    hotel_name: str
    taopiao: str
    content_items: str
    raw_content: str
    surcharge: str
    other_surcharge: str = ""
    sell_time: str
    available_date: str
    price: str
    external_price: str = ""


class OCRRequest(BaseModel):
    image_path: str


class OCRResponse(BaseModel):
    raw_text: str
    title: str
    hotel_name: str
    price: str
    content_items: str
    surcharge: str
    other_surcharge: str = ""
    sell_time: str
    available_date: str
    taopiao: str


class WriteRequest(BaseModel):
    row_data: dict
    spreadsheet_token: Optional[str] = None


class WriteResponse(BaseModel):
    success: bool
    message: str
    row_number: int = 0


class CalcSettlementRequest(BaseModel):
    live_price: str
    rebate: str
    surcharge: str = ""
    product_type: str = "hotel"


class CalcSettlementResponse(BaseModel):
    result: str


class CheckResponse(BaseModel):
    lark_cli_available: bool
    config_exists: bool
    spreadsheet_token: str


# ============================================================
# API 路由
# ============================================================


@app.get("/api/check", response_model=CheckResponse)
async def api_check():
    """检查环境配置。"""
    lark_ok = check_lark_cli()
    config_path = Path(project_root) / "config.yaml"
    config_ok = config_path.exists()
    token = ""
    if config_ok:
        try:
            token = get_spreadsheet_token()
        except Exception:
            pass
    return CheckResponse(
        lark_cli_available=lark_ok,
        config_exists=config_ok,
        spreadsheet_token=token,
    )


@app.post("/api/parse-input", response_model=ParseInputResponse)
async def api_parse_input(req: ParseInputRequest):
    """
    解析完整的原始输入（标题+链接+返佣等），提取秀米内容并生成各字段。

    支持三种模式：
    - script: 全脚本解析
    - ai: 全 AI 解析
    - hybrid: 先脚本，后 AI 兜底（默认）
    """
    raw_text = req.raw_text.strip()
    mode = req.mode
    if not raw_text:
        raise HTTPException(status_code=400, detail="输入内容为空")

    # 1. 解析原始输入（复用 src/main.py 的 parse_raw_input）
    parsed = parse_raw_input(raw_text)
    title = parsed.get("标题行", "")
    xiumius_url = parsed.get("xiumius_url", "")
    rebate = parsed.get("返佣", "")

    # 2. 提取秀米内容
    raw_content = ""
    content_items = ""
    if xiumius_url:
        try:
            raw_content = cmd_extract(xiumius_url)
        except Exception as e:
            raw_content = f"（秀米提取失败: {e}）"

    # 从原始输入中提取 🈶 行内容作为套餐权益（当秀米内容为空时的后备）
    you_lines = re.findall(r'🈶(.+)', raw_text)
    you_content = ""
    if you_lines:
        you_content = "\n".join(f"【享】{line.strip()}" for line in you_lines)
        if not raw_content:
            raw_content = you_content

    # 3. 根据 mode 选择解析方式
    hotel_name = ""
    taopiao = ""
    surcharge = ""
    other_surcharge = ""
    sell_time = ""
    available_date = ""
    price = ""

    if mode == "ai":
        # ---- 全 AI 模式 ----
        print(f"[DEBUG] ai mode: title={repr(title[:80])}, raw_content len={len(raw_content)}")
        ai_result = ai_extract_all(title, raw_content, product_type=req.product_type)
        print(f"[DEBUG] ai_result={ai_result}")
        taopiao = ai_result.get("taopiao", "")
        content_items = ai_result.get("content_items", "")
        surcharge = ai_result.get("surcharge", "")
        other_surcharge = ai_result.get("other_surcharge", "")
        sell_time = ai_result.get("sell_time", "")
        available_date = ai_result.get("available_date", "")
        price = ai_result.get("price", "")
        hotel_name = ai_result.get("hotel_name", "")

        # AI 没提取到酒店名时，从标题中提取
        if not hotel_name:
            hotel_match = re.search(r'【(.+?)】', title)
            if hotel_match:
                hotel_name = hotel_match.group(1)

    elif mode == "script":
        # ---- 全脚本模式 ----
        content_items = extract_content_items(raw_content) if raw_content else ""
        if not content_items and you_content:
            content_items = you_content

        taopiao = generate_taopiao(title, content_items or raw_content)

        # 提取酒店名
        hotel_match = re.search(r'【(.+?)】', title)
        if hotel_match:
            hotel_name = hotel_match.group(1)
        else:
            name_match = re.search(r'(?:[\u4e00-\u9fff]+星级[-\u4e00-\u9fff]*)?([\u4e00-\u9fff]+酒店)', title)
            if name_match:
                hotel_name = name_match.group(1)
            else:
                name_match = re.search(r'([\u4e00-\u9fff]+酒店)', title)
                if name_match:
                    hotel_name = name_match.group(1)
        if not hotel_name:
            hotel_from_raw = re.search(r'\[红包\].*?([\u4e00-\u9fff]+酒店)', raw_text)
            if hotel_from_raw:
                hotel_name = hotel_from_raw.group(1)
                hotel_name = re.sub(r'^[\u4e00-\u9fff]+星级-', '', hotel_name)

        surcharge = extract_surcharge(raw_content) if raw_content else ""
        # 优先从原始文案提取售卖时间和可用日期（文案中常有"抢购日期/使用日期"），
        # 文案没有再从秀米内容提取
        sell_time = parsed.get("售卖时间", "") or (extract_sell_time(raw_content) if raw_content else "")
        available_date = parsed.get("可用日期", "") or (extract_available_date(raw_content) if raw_content else "")
        price = extract_price(raw_content) if raw_content else ""

    else:
        # ---- hybrid 模式（默认：先脚本，后 AI 兜底） ----
        content_items = extract_content_items(raw_content) if raw_content else ""
        if not content_items and you_content:
            content_items = you_content

        taopiao = generate_taopiao(title, content_items or raw_content)

        # 提取酒店名
        hotel_match = re.search(r'【(.+?)】', title)
        if hotel_match:
            hotel_name = hotel_match.group(1)
        else:
            name_match = re.search(r'(?:[\u4e00-\u9fff]+星级[-\u4e00-\u9fff]*)?([\u4e00-\u9fff]+酒店)', title)
            if name_match:
                hotel_name = name_match.group(1)
            else:
                name_match = re.search(r'([\u4e00-\u9fff]+酒店)', title)
                if name_match:
                    hotel_name = name_match.group(1)
        if not hotel_name:
            hotel_from_raw = re.search(r'\[红包\].*?([\u4e00-\u9fff]+酒店)', raw_text)
            if hotel_from_raw:
                hotel_name = hotel_from_raw.group(1)
                hotel_name = re.sub(r'^[\u4e00-\u9fff]+星级-', '', hotel_name)

        surcharge = extract_surcharge(raw_content) if raw_content else ""
        # 优先从原始文案提取售卖时间和可用日期（文案中常有"抢购日期/使用日期"），
        # 文案没有再从秀米内容提取
        sell_time = parsed.get("售卖时间", "") or (extract_sell_time(raw_content) if raw_content else "")
        available_date = parsed.get("可用日期", "") or (extract_available_date(raw_content) if raw_content else "")
        price = extract_price(raw_content) if raw_content else ""

    # 4. 售卖时间规则：如果为"即日起--售完即止"，改为与可用日期一致
    if sell_time and available_date:
        if re.match(r'即日起[\s\-—]*售完即止', sell_time):
            sell_time = available_date

    # 5. 对外价格自动计算：直播价/0.5，取整数
    external_price = ""
    if price:
        try:
            price_num = float(re.sub(r'[^0-9.]', '', price))
            if price_num > 0:
                external_price = str(int(price_num / 0.5))
        except ValueError:
            pass

    return ParseInputResponse(
        title=title,
        xiumius_url=xiumius_url,
        qianggou_url=parsed.get("抢购链接", ""),
        fangtai_url=parsed.get("房态链接", ""),
        bianhao=parsed.get("编号", ""),
        rebate=rebate,
        order_no=parsed.get("下单编号", ""),
        hotel_name=hotel_name,
        taopiao=taopiao,
        content_items=content_items,
        raw_content=raw_content,
        external_price=external_price,
        surcharge=surcharge,
        other_surcharge=other_surcharge,
        sell_time=sell_time,
        available_date=available_date,
        price=price,
    )


@app.post("/api/ocr", response_model=OCRResponse)
async def api_ocr(file: UploadFile = File(...)):
    """OCR 识别海报图片。"""
    suffix = Path(file.filename).suffix if file.filename else ".jpg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        text = ocr_image(tmp_path)
    except Exception as e:
        os.unlink(tmp_path)
        raise HTTPException(status_code=400, detail=f"OCR 识别失败: {str(e)}")

    os.unlink(tmp_path)

    parsed = parse_poster_text(text)
    taopiao = generate_taopiao(parsed["title"], parsed["content_items"])

    return OCRResponse(
        raw_text=text,
        title=parsed["title"],
        hotel_name=parsed["hotel_name"],
        price=parsed["price"],
        content_items=parsed["content_items"],
        surcharge=parsed["surcharge"],
        sell_time=parsed["sell_time"],
        available_date=parsed["available_date"],
        taopiao=taopiao,
    )


@app.post("/api/ocr-with-text", response_model=OCRResponse)
async def api_ocr_with_text(
    file: UploadFile = File(...),
    text: Optional[str] = Form(None),
):
    """
    OCR 识别海报图片 + 可选文案内容。
    如果提供了文案，将文案与 OCR 结果合并后解析，提高识别准确率。
    """
    suffix = Path(file.filename).suffix if file.filename else ".jpg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        ocr_text = ocr_image(tmp_path)
    except Exception as e:
        os.unlink(tmp_path)
        raise HTTPException(status_code=400, detail=f"OCR 识别失败: {str(e)}")

    os.unlink(tmp_path)

    # 如果有文案内容，合并到 OCR 结果中（文案在前，OCR 结果在后）
    combined_text = ocr_text
    if text and text.strip():
        combined_text = text.strip() + "\n" + ocr_text

    # 全 AI 解析
    ai_result = ai_extract_all(title="", raw_content=combined_text, product_type="ticket")

    return OCRResponse(
        raw_text=combined_text,
        title="",
        hotel_name=ai_result.get("hotel_name", ""),
        price=ai_result.get("price", ""),
        content_items=ai_result.get("content_items", ""),
        surcharge=ai_result.get("surcharge", ""),
        other_surcharge=ai_result.get("other_surcharge", ""),
        sell_time=ai_result.get("sell_time", ""),
        available_date=ai_result.get("available_date", ""),
        taopiao=ai_result.get("taopiao", ""),
    )


@app.post("/api/write", response_model=WriteResponse)
async def api_write(req: WriteRequest):
    """
    写入飞书表格。
    接收完整的 14 列数据，自动计算结算价格&利润率。
    """
    row_data = dict(req.row_data)

    # 自动填入填写日期
    if not row_data.get("填写日期"):
        row_data["填写日期"] = date.today().isoformat()

    # 自动计算结算价格&利润率（复用 src/main.py 的 calc_settlement）
    live_price = row_data.get("直播价", "")
    rebate = row_data.get("返佣", "")
    surcharge = row_data.get("加收", "")
    if live_price and rebate:
        parsed_for_calc = {"返佣": rebate}
        settlement = calc_settlement(parsed_for_calc, row_data)
        if settlement:
            row_data["结算价格&利润率（已除平台）"] = settlement

    try:
        # 优先使用请求中传入的 token，否则从 config.yaml 读取
        token = req.spreadsheet_token
        sheet_name = "Sheet1"
        if not token:
            config_path = project_root / "config.yaml"
            config = load_config(str(config_path))
            token = get_spreadsheet_token(config)
            sheet_name = get_sheet_name(config)
        result = append_row_by_cols(row_data, spreadsheet_token=token, sheet_title=sheet_name)
        # 从返回结果中提取行号（tableRange 格式如 "a15f24!A126:B126" → 126）
        row_number = 0
        try:
            result_data = json.loads(result) if isinstance(result, str) else result
            if isinstance(result_data, dict):
                table_range = (result_data.get("data", {}) or result_data).get("tableRange", "")
                if table_range:
                    import re
                    m = re.search(r'!A(\d+)', table_range)
                    if m:
                        row_number = int(m.group(1))
        except (json.JSONDecodeError, AttributeError, ValueError):
            pass
        return WriteResponse(success=True, message="写入成功", row_number=row_number)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入失败: {str(e)}")


@app.post("/api/calc-settlement", response_model=CalcSettlementResponse)
async def api_calc_settlement(req: CalcSettlementRequest):
    """计算结算价和利润率。"""
    parsed = {"返佣": req.rebate}
    col_data = {"直播价": req.live_price, "加收": req.surcharge}
    result = calc_settlement(parsed, col_data, product_type=req.product_type)
    return CalcSettlementResponse(result=result)


@app.get("/api/config")
async def api_config():
    """获取飞书配置信息。"""
    try:
        config = load_config()
        return {
            "spreadsheet_token": config.get("lark", {}).get("spreadsheet_token", ""),
            "sheet_name": config.get("lark", {}).get("sheet_name", "Sheet1"),
        }
    except Exception as e:
        return {"spreadsheet_token": "", "sheet_name": "Sheet1", "error": str(e)}


@app.post("/api/create-sheet")
async def api_create_sheet():
    """
    创建新的飞书表格，设置 15 列表头，并更新 config.yaml。
    """
    # 1. 创建表格
    headers = [
        "填写日期", "产品类型", "酒店/景区", "产品信息", "套票名称",
        "套票内容", "加收", "其他加收", "售卖时间", "可用日期",
        "对外价格", "直播价", "结算价格&利润率（已除平台）", "备注", "海报",
    ]
    headers_json = json.dumps(headers, ensure_ascii=False)

    cmd = [
        "lark-cli", "sheets", "+create",
        "--title", "hotels/tickets上品工具",
        "--headers", headers_json,
        "--as", "user",
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="未找到 lark-cli 命令，请先安装飞书 CLI 工具")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="创建表格超时")

    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip()
        raise HTTPException(status_code=500, detail=f"创建表格失败: {error_msg}")

    data = json.loads(result.stdout)
    spreadsheet_token = data.get("data", {}).get("spreadsheet_token", "")
    url = data.get("data", {}).get("url", "")

    if not spreadsheet_token:
        raise HTTPException(status_code=500, detail="创建表格返回数据异常")

    return {
        "success": True,
        "spreadsheet_token": spreadsheet_token,
        "url": url,
        "message": "表格创建成功",
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True,
                reload_dirs=[str(project_root / "src"), str(project_root / "web/backend")])
