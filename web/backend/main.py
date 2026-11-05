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
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
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
from src import lark_client
from src.lark_writer import (
    append_row_by_cols,
    check_lark_cli,
    check_lark_api,
    load_config,
    get_spreadsheet_token,
    get_sheet_name,
    get_sheet_id,
    _get_header_row,
)
from src.main import ocr_image
from src.poster_ocr import parse_poster_text
from src.ai_helper import ai_extract_all, ai_extract_surcharge, ai_extract_content, ai_generate_taopiao

app = FastAPI(title="hotels/tickets API", version="1.0.0")

# CORS（同源模式下可不配置，保留以兼容外部调用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件（二维码图片等）
backend_dir = Path(__file__).resolve().parent
static_dir = backend_dir / "static"
static_dir.mkdir(exist_ok=True)

# 注册 auth 和 admin 路由
from web.backend.auth import router as auth_router, get_current_user
from web.backend.admin import router as admin_router
from web.backend.database import increment_usage
app.include_router(auth_router)
app.include_router(admin_router)

# ============================================================
# 数据模型
# ============================================================


class ParseInputRequest(BaseModel):
    raw_text: str
    # 必填：决定走哪段提示词与佣金比例，不能默认成酒店
    product_type: str


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
    packages: list = []  # 门票多套餐列表：[{name, price, rebate, order_id, taopiao, content, ...}]


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
    rebate: str = ""
    packages: list = []  # 门票多套餐列表


class WriteRequest(BaseModel):
    row_data: dict
    spreadsheet_token: Optional[str] = None
    # 必填：api_write 会用 calc_settlement 重算结算价，漏传会默认按酒店 8% 覆盖掉前端算好的门票利润率
    product_type: str


class WriteResponse(BaseModel):
    success: bool
    message: str
    row_number: int = 0
    dropped_columns: list = []   # 表头中不存在的列名，已被丢弃
    empty_columns: list = []     # 表头中存在但值为空的列名


class CalcSettlementRequest(BaseModel):
    live_price: str
    rebate: str
    surcharge: str = ""
    # 必填：漏传时旧默认值 hotel 会让门票产品静默按 8% 算，利润率少 3 个百分点
    product_type: str


class CalcSettlementResponse(BaseModel):
    result: str


class CheckResponse(BaseModel):
    lark_cli_available: bool          # 兼容旧字段名，现表示飞书 API 是否可用
    config_exists: bool
    spreadsheet_token: str
    lark_api_available: bool = False  # 新增：飞书 API 可用性
    lark_api_message: str = ""        # 新增：不可用时的原因


# ============================================================
# API 路由
# ============================================================


@app.get("/api/check", response_model=CheckResponse)
async def api_check():
    """检查环境配置。"""
    status = check_lark_api()
    lark_ok = status.get("available", False)
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
        lark_api_available=lark_ok,
        lark_api_message="" if lark_ok else status.get("message", ""),
    )


# 票种词表：套票名称的【】里填票种，不是营销卖点词
_TICKET_KINDS = [
    '亲子票', '家庭票', '双人票', '单人票', '儿童票', '成人票', '学生票',
    '老人票', '长者票', '团队票', '情侣票', '三人票', '年卡', '次卡',
]


def _detect_ticket_kind(text: str) -> str:
    """
    从门票名判断票种。
    人群特征优先于人数特征："儿童蛛网单人票"是儿童票，不是单人票。
    """
    if re.search(r'\d+\s*大\s*\d+\s*小', text) or '一大一小' in text or '两大一小' in text:
        return '家庭票'
    # 人群词优先于人数词："黄线儿童蛛网单人票"是儿童票，不是单人票
    if '儿童' in text or '小孩' in text or '亲子' in text:
        return '儿童票'
    if '老人' in text or '长者' in text:
        return '老人票'
    if '学生' in text:
        return '学生票'
    if '情侣' in text:
        return '情侣票'
    if '双人' in text or '2大' in text or '两大' in text:
        return '双人票'
    # 人群词再看票种词
    for kind in ('家庭票', '三人票', '双人票', '成人票', '单人票', '团队票', '年卡', '次卡'):
        if kind in text:
            return kind
    return '单人票'


def _compose_ticket_pkg_name(raw_text: str, pkg_name: str, hotel_name: str) -> str:
    """
    套票名称 = 【票种】景区名｜门票名，票种由门票名判断，景区名与门票名之间用全角竖线。
    """
    if hotel_name and pkg_name.startswith(hotel_name):
        clean = pkg_name[len(hotel_name):].strip()
    else:
        clean = pkg_name.strip()
    clean = clean.lstrip('｜|~～+').strip() or pkg_name.strip()

    kind = _detect_ticket_kind(clean)
    if not hotel_name:
        return f"【{kind}】{clean}"
    return f"【{kind}】{hotel_name}｜{clean}"


def parse_ticket_packages(raw_text: str, hotel_name: str = "", common_content: str = "",
                          default_sell_time: str = "", default_available: str = "",
                          product_type: str = "ticket") -> list:
    """
    从门票文案中解析多个套餐。
    返回 [{name, price, rebate, order_id, retail_price, settlement, content, surcharge, available_date}, ...]
    name 会自动拼接酒店名前缀。
    content = 套餐特定描述 + 通用权益文案。
    加收规则：A票（双人套票）周六/日不适用，其他通用。
    """
    packages = []
    order_ids = re.findall(r'编号(\d+)', raw_text)
    pattern = r'([\d.]+)返(\d+)'
    matches = list(re.finditer(pattern, raw_text))

    segments = []
    for i in range(len(matches)):
        if i == 0:
            seg_start = matches[i].start()
            prefix = raw_text[:seg_start]
            name_start = max(
                prefix.rfind('～'),
                prefix.rfind('~'),
                prefix.rfind('[强]'),
                prefix.rfind('\n'),
            ) + 1
            if name_start <= 0:
                name_start = 0
            segment = raw_text[name_start:matches[i].end()].strip().lstrip('/').lstrip('[强]').lstrip('～').lstrip('~')
        else:
            prev_end = matches[i-1].end()
            id_match = re.search(r'/编号\d+', raw_text[prev_end:])
            start = prev_end + id_match.end() if id_match else prev_end
            segment = raw_text[start:matches[i].end()].strip().lstrip('/').lstrip('[强]').lstrip('+')
        segments.append(segment)

    for i, seg in enumerate(segments):
        m = re.search(r'([\d.]+)返(\d+)', seg)
        if not m:
            continue
        pkg_price = m.group(1)
        pkg_rebate = m.group(2)
        pkg_name = seg[:m.start()].strip()
        # 清理名称中的前缀符号和括号
        pkg_name = re.sub(r'^[~/\\[\]太阳强\s（）()，,、]+', '', pkg_name).strip()
        # 去掉开头的字母+）或字母+）模式，如 "A票）"
        pkg_name = re.sub(r'^[A-Da-d]票[）\)]?\s*', '', pkg_name).strip()
        # 价格前只剩数字/符号（如首行"…~99返7"），说明这段没有套餐名，
        # 属于单套餐文案，交给 AI 结果，不要产出空的套餐行
        if not re.search(r'[\u4e00-\u9fa5A-Za-z]', pkg_name):
            continue
        # 名称还原：用标题行的【卖点】+景区名｜套餐名，避免"景区名+套餐名"裸拼
        pkg_name = _compose_ticket_pkg_name(raw_text, pkg_name, hotel_name)
        pkg_order_id = order_ids[i] if i < len(order_ids) else ""

        # 生成套餐内容：门票产品只包含景区/项目信息，不包含价格返佣、适用日期、加收等
        # 格式：套餐名 + 项目/景区描述
        # 从文案中提取非价格、非日期、非加收、非【】标题的内容行作为项目描述
        desc_lines = []
        for line in raw_text.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            # 跳过第一行（套餐列表行）
            if line == raw_text.strip().split('\n')[0].strip():
                continue
            # 跳过包含价格返佣的行（如"单人票39.9返3"）
            if re.search(r'[\d.]+返\d+', line):
                continue
            # 跳过加收说明行
            if re.match(r'^(端午|周末|节假日)?(不加收|不加价|加收)', line):
                continue
            # 跳过适用日期/售卖时间行
            if re.match(r'^(以上套票)?(适用|使用|有效|售卖|抢购)(日期|时间)', line):
                continue
            # 跳过以【】开头的标题/卖点行（如【早鸟票】观光+泉水乐园套票！）
            if re.match(r'^【', line):
                continue
            desc_lines.append(line)

        # 套票内容：景区名｜套餐名，与套票名称一致但不带【卖点】前缀
        clean_name = re.sub(r'^【[^】]*】', '', pkg_name).strip()
        if hotel_name and clean_name.startswith(hotel_name):
            clean_name = clean_name[len(hotel_name):]
        clean_name = clean_name.lstrip('｜|~～+').strip()
        clean_name = clean_name or pkg_name
        pkg_content = f"{hotel_name}｜{clean_name}" if hotel_name else clean_name
        if desc_lines:
            pkg_content += '，' + '，'.join(desc_lines)

        # 加收和可用日期：没有提供就留空
        pkg_surcharge = ""
        pkg_available = ""

        try:
            live_num = float(pkg_price)
            deep_rebate = float(pkg_rebate)
            retail_price = int(live_num / 0.7)
            settlement = live_num - deep_rebate
            # 平台成本按产品类型：酒店 8%，门票 5%（与 calc_settlement 口径一致）
            platform_cost = 0.08 if product_type == "hotel" else 0.05
            profit_rate = (deep_rebate / live_num) - platform_cost
            settlement_str = f"结算价{int(settlement)}；利润率{profit_rate*100:.1f}%"
        except (ValueError, ZeroDivisionError):
            retail_price = 0
            settlement_str = ""

        packages.append({
            "name": pkg_name,
            "price": pkg_price,
            "rebate": pkg_rebate,
            "order_id": pkg_order_id,
            "retail_price": str(retail_price) if retail_price else "",
            "settlement": settlement_str,
            "content": pkg_content,
            "surcharge": pkg_surcharge,
            "available_date": pkg_available,
        })
    return packages


@app.post("/api/parse-input", response_model=ParseInputResponse)
async def api_parse_input(req: ParseInputRequest, user: dict = Depends(get_current_user)):
    """
    解析完整的原始输入（标题+链接+返佣等），提取秀米内容并生成各字段。

    全 AI 模式：一次调用提取所有字段。
    """
    raw_text = req.raw_text.strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="输入内容为空")

    # 记录使用次数
    increment_usage(user["id"])

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

    # 3. 全 AI 解析
    try:
        ai_result = ai_extract_all(title, raw_content, product_type=req.product_type)
    except Exception as e:
        # AI 不可用时直接报错，不能把全空字段当成解析结果返回给前端
        raise HTTPException(
            status_code=502,
            detail=f"AI 解析失败，请检查 DEEPSEEK_API_KEY 配置与网络：{e}",
        )

    taopiao = ai_result.get("taopiao", "")
    content_items = ai_result.get("content_items", "")
    surcharge = ai_result.get("surcharge", "")
    other_surcharge = ai_result.get("other_surcharge", "")
    sell_time = ai_result.get("sell_time", "")
    available_date = ai_result.get("available_date", "")
    price = ai_result.get("price", "")
    hotel_name = ai_result.get("hotel_name", "")

    # 兜底：AI 没提取到价格时，从原文提取"售价"或"价格"
    if not price:
        price_match = re.search(r'(?:售价|价格|直播价)[：:]\s*(?:RMB|R|\$)?\s*(\d+(?:\.\d+)?)', raw_text)
        if price_match:
            price = price_match.group(1) + "元/套"
    # 兜底：从标题行提取"XXX一口价="格式的价格
    if not price:
        title_price = re.search(r'(\d+(?:\.\d+)?)\s*一口价\s*=', title)
        if title_price:
            price = title_price.group(1) + "元/套"
    # 兜底：标题里常见的"¥799抢…"、"799元抢…"格式
    if not price:
        yen_match = re.search(r'[¥￥]\s*(\d+(?:\.\d+)?)', title)
        if not yen_match:
            yen_match = re.search(r'(\d+(?:\.\d+)?)\s*元\s*抢', title)
        if yen_match:
            price = yen_match.group(1) + "元/套"
    # 兜底：套票名称为空时，从标题拆出【卖点】+景区名，不要塞整条标题
    if not taopiao and title.strip():
        bracket = re.match(r'^【([^】]+)】', title.strip())
        scene = re.match(r'^【[^】]*】\s*[\d.]+\s*元?\s*[抢秒售价超]?购?[！!]?\s*([^~～]+)', title.strip())
        if bracket and scene:
            taopiao = f"【{bracket.group(1)}】{scene.group(1).strip()}"
        elif bracket:
            taopiao = f"【{bracket.group(1)}】"
    # 兜底：加收为空时，尝试从原文"平日不加收""周六加收100元"等表述提取
    if not surcharge:
        surcharge_bits = re.findall(
            r'(?:平日|周末|周六|周日|周五|节假日|中秋|国庆|春节|元旦)'
            r'[^\n，。；]{0,12}?'
            r'(?:不加收|加收\s*\d+(?:\.\d+)?\s*元?[^\n，。；]*)',
            raw_text,
        )
        if surcharge_bits:
            seen = []
            for bit in surcharge_bits:
                bit = bit.strip()
                if bit and bit not in seen:
                    seen.append(bit)
            surcharge = "\n".join(seen)

    # 兜底：AI 没提取到返佣时，从原文提取"结算返"
    rebate = parsed.get("返佣", "")
    if not rebate:
        rebate_match = re.search(r'结算返\s*(\d+(?:\.\d+)?)\s*元', raw_text)
        if rebate_match:
            rebate = f"深返{rebate_match.group(1)}"
            parsed["返佣"] = rebate

    # AI 没提取到酒店名时，从标题中提取
    if not hotel_name:
        hotel_match = re.search(r'【(.+?)】', title)
        if hotel_match:
            hotel_name = hotel_match.group(1)

    # 门票产品：AI 已提取到景区名时直接采用，仅在为空时用正则兜底
    if req.product_type == "ticket" and not hotel_name:
        first_line = raw_text.split('\n')[0].strip()
        name_match = re.match(r'^(.+?)[～~]', first_line)
        if name_match:
            raw_name = name_match.group(1).strip()
            # 剥离"【早场票9-12点】"这类卖点前缀与"29.9元抢购！"这类促销语
            raw_name = re.sub(r'^【[^】]*】', '', raw_name)
            raw_name = re.sub(r'^[\d.]+\s*元?\s*[抢秒售价超]购?[！!~～\s]*', '', raw_name)
            raw_name = re.sub(r'^[！!，,、~～\s]+', '', raw_name).strip()
            # 去掉"大门票"、"门票"等后缀，提取景区名
            scenic_match = re.match(r'^(.+?)(?:大门票|门票|景区|旅游)', raw_name)
            if scenic_match:
                hotel_name = scenic_match.group(1).strip()
            else:
                hotel_name = raw_name

    # 4. 日期/加收处理：优先用文案中的，文案没有则信任 LLM 从秀米提取的结果
    # 文案中有日期关键词时，用 parse_raw_input 提取的文案日期覆盖 LLM 结果
    raw_has_date = any(kw in raw_text for kw in ['售卖时间', '售卖日期', '抢购日期', '使用时间', '有效期', '使用日期', '适用日期', '即日起', '即日', '2026', '2025', '2024'])
    if raw_has_date:
        raw_sell = parsed.get("售卖时间", "")
        raw_avail = parsed.get("可用日期", "")
        if raw_sell:
            sell_time = raw_sell
        if raw_avail:
            available_date = raw_avail
    # 文案中没有日期关键词 → LLM 从秀米提取的结果保留不动，不清空

    # 售卖时间如果包含"即日起--售完即止"且没有明确的文案来源，改为与可用日期一致
    if "即日起" in sell_time and "售完即止" in sell_time and available_date:
        sell_time = available_date

    # 加收同理：文案中有加收关键词时，尝试从文案提取覆盖 LLM 结果
    # 文案中没有加收关键词 → LLM 从秀米提取的结果保留不动，不清空

    # 门票产品：套票内容从原始文案中提取（景区介绍、游玩项目等，不含价格日期加收）
    if req.product_type == "ticket" and raw_text:
        content_from_raw = ""
        lines = raw_text.strip().split('\n')
        first_line = lines[0].strip() if lines else ""
        if len(lines) > 1:
            rest_lines = []
            for line in lines[1:]:
                line = line.strip()
                if not line:
                    continue
                # 跳过包含价格返佣的行
                if re.search(r'[\d.]+返\d+', line):
                    continue
                # 跳过加收说明行
                if re.match(r'^(端午|周末|节假日)?(不加收|不加价|加收)', line):
                    continue
                # 跳过适用日期/售卖时间行
                if re.match(r'^(以上套票)?(适用|使用|有效|售卖|抢购)(日期|时间)', line):
                    continue
                # 跳过以【】开头的标题/卖点行
                if re.match(r'^【', line):
                    continue
                rest_lines.append(line)
            if rest_lines:
                content_from_raw = '，'.join(rest_lines)
        # 如果 AI 提取的内容为空或太短，用文案内容覆盖
        if not content_items or len(content_items) < 20:
            if content_from_raw:
                content_items = content_from_raw

    # 5. 对外价格自动计算：酒店产品直播价/0.9，门票产品直播价/0.7，取整数
    external_price = ""
    if price:
        try:
            price_num = float(re.sub(r'[^0-9.]', '', price))
            if price_num > 0:
                divisor = 0.7 if req.product_type == "ticket" else 0.9
                external_price = str(int(price_num / divisor))
        except ValueError:
            pass

    # 6. 门票产品：从文案中解析多个套餐
    packages = []
    if req.product_type == "ticket" and raw_text:
        # 从文案第一行提取通用权益（~ 后面的内容，但排除直接以【开头的套餐列表）
        first_line = raw_text.split('\n')[0].strip()
        common_match = re.search(r'[～~](.+)', first_line)
        common_content = ""
        if common_match:
            after_tilde = common_match.group(1).strip()
            # 如果 ~ 后面直接是套餐信息（以【开头），则没有通用权益
            if not after_tilde.startswith('【'):
                common_content = after_tilde
        packages = parse_ticket_packages(raw_text, hotel_name=hotel_name, common_content=common_content,
                                        product_type=req.product_type)

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
        packages=packages,
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
    product_type: Optional[str] = Form("hotel"),
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

    # 修正百度 OCR 常见识别错误
    ocr_clean = ocr_text
    ocr_replacements = {
        "隐贤5水求季": "",
        "清凉开泼多": "",
        "奥特曼": "",
        "六月限定": "",
        "主题秀": "",
        "烟花秀": "",
        "泼水狂欢": "",
        "东莞科A隐贤则庄": "东莞4A隐贤山庄",
        "水灶乐园": "水上乐园",
        "动的园": "动物园",
        "幸福四园": "幸福田园",
        "18顺游乐顶阳": "18项游乐项目",
        "1⑧个游乐顾目": "18个游乐项目",
        "嫦贼过山车": "嫦娥过山车",
        "北极熊肿浪": "北极熊冲浪",
        "幻影花境": "幻影秘境",
        "烽火印呢": "烽火印记",
        "抢购▣期": "抢购日期",
        "以不": "以下",
        "套票均泡含": "套票均包含",
        "1伏小套裹": "",
        "或大1老)": "",
        "129149": "",
        "高空翔": "高空飞翔",
        "摇头椅": "摇头飞椅",
        "恐龙车梦": "恐龙飞车",
    }
    for old, new in ocr_replacements.items():
        ocr_clean = ocr_clean.replace(old, new)

    # 全 AI 解析：文案为主，OCR 结果为辅
    if text and text.strip():
        # 文案作为主要内容，OCR 结果作为补充参考
        ai_raw = text.strip()
        if ocr_clean.strip():
            ai_raw += "\n\n【海报原文信息（供参考）】\n" + ocr_clean.strip()
    else:
        ai_raw = ocr_clean
    try:
        ai_result = ai_extract_all(title="", raw_content=ai_raw, product_type=product_type)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"AI 解析失败，请检查 DEEPSEEK_API_KEY 配置与网络：{e}",
        )

    # 从 OCR 结果中提取日期信息，覆盖 AI 提取的默认值
    # 文案+海报模式：海报中的日期信息应该被提取
    if ocr_text.strip():
        # 从 OCR 文本中提取"抢购日期"后面的内容作为售卖时间
        sell_ocr_match = re.search(r'抢购日期[：:]\s*(.+?)(?:\n|$)', ocr_text)
        if sell_ocr_match:
            sell_ocr = sell_ocr_match.group(1).strip()
            # 去掉末尾的【】备注和多余的分号内容
            sell_ocr = re.sub(r'[；;].*$', '', sell_ocr).strip()
            sell_ocr = re.sub(r'[【\[].*$', '', sell_ocr).strip()
            if sell_ocr:
                ai_result["sell_time"] = sell_ocr

        # 从 OCR 文本中提取"适用日期"后面的内容作为可用日期
        avail_ocr_match = re.search(r'适用日期[：:]\s*(.+?)(?:\n|$)', ocr_text)
        if avail_ocr_match:
            avail_ocr = avail_ocr_match.group(1).strip()
            # 去掉末尾的【】备注
            avail_ocr = re.sub(r'[【\[].*$', '', avail_ocr).strip()
            if avail_ocr:
                ai_result["available_date"] = avail_ocr

        # 兜底：如果没找到"抢购日期"或"适用日期"，尝试匹配通用日期格式
        if not ai_result.get("sell_time") and not ai_result.get("available_date"):
            date_pattern = r'(\d{4}年\d{1,2}月\d{1,2}日\s*[~\-—]\s*\d{4}年\d{1,2}月\d{1,2}日)'
            ocr_dates = re.findall(date_pattern, ocr_text)
            if ocr_dates:
                ai_result["sell_time"] = ocr_dates[0].replace(' ', '')
                ai_result["available_date"] = ocr_dates[0].replace(' ', '')

    # 售卖时间如果包含"即日起"且没有明确的抢购日期来源，改为与可用日期一致
    sell = ai_result.get("sell_time", "")
    if "即日起" in sell and "售完即止" in sell:
        avail = ai_result.get("available_date", "")
        if avail:
            ai_result["sell_time"] = avail

    # 文案+海报模式：海报中的日期/加收信息优先于文案
    # 如果海报中有日期信息，保留海报提取的日期
    # 如果海报中有加收信息，保留海报提取的加收
    # 注意：纯原始输入模式（无海报）时，在 api_parse_input 中处理清空逻辑

    # 多套餐：从AI结果中获取（门票和酒店都走AI多套餐）
    packages = []
    ai_packages = ai_result.get("packages", [])
    if ai_packages:
        for pkg in ai_packages:
            # 构建套票内容 = 差异项 + 相同项
            content_parts = []
            if pkg.get("diff"):
                content_parts.append(pkg["diff"])
            if pkg.get("common"):
                content_parts.append(pkg["common"])
            pkg_content = "\n".join(content_parts) if content_parts else (pkg.get("content", "") or ai_result.get("content_items", ""))

            # 清理 OCR 脏文本：去掉英文行、单个字母/数字行、过短的行
            clean_lines = []
            for line in pkg_content.split('\n'):
                line_stripped = line.strip()
                # 去掉纯英文行（含空格和标点）
                if line_stripped and re.match(r'^[a-zA-Z\s.,;:!?\'\"-]+$', line_stripped):
                    continue
                # 去掉过短的无意义行（1-3个字符且不含中文）
                if line_stripped and len(line_stripped) <= 3 and not re.search(r'[\u4e00-\u9fff]', line_stripped):
                    continue
                # 去掉纯数字行（如"159."、"199"）
                if line_stripped and re.match(r'^\d+\.?\s*$', line_stripped):
                    continue
                clean_lines.append(line)
            pkg_content = '\n'.join(clean_lines)

            # 从 OCR 文本中提取"项目N选M"等详细项目列表，追加到套票内容
            if ocr_text.strip():
                # 匹配"项目N选M"及其后面的详细列表（只匹配括号内的内容）
                item_match = re.search(r'(项目\d+选\d+[：:：\s]*【[^】]+】)', ocr_text)
                if not item_match:
                    item_match = re.search(r'(项目\d+选\d+[：:：\s]*[^\n]+)', ocr_text)
                if item_match:
                    item_detail = item_match.group(1).strip()
                    if item_detail and item_detail not in pkg_content:
                        pkg_content += "\n" + item_detail

            # 追加海报中的详细权益信息（水上乐园项目、漂流描述、营业时间等）
            if ocr_text.strip():
                # 提取"天然山泉水·水上乐园"后面的内容
                water_match = re.search(r'(天然山泉水·水上乐园[^\n]*)', ocr_text)
                if water_match:
                    water_detail = water_match.group(1).strip()
                    if water_detail and water_detail not in pkg_content:
                        pkg_content += "\n" + water_detail
                # 提取漂流描述
                raft_match = re.search(r'(狂野漂流[^\n]*)', ocr_text)
                if raft_match:
                    raft_detail = raft_match.group(1).strip()
                    if raft_detail and raft_detail not in pkg_content:
                        pkg_content += "\n" + raft_detail
                # 提取营业时间
                time_match = re.search(r'(营业时间[：:][^\n]*)', ocr_text)
                if time_match:
                    time_detail = time_match.group(1).strip()
                    if time_detail and time_detail not in pkg_content:
                        pkg_content += "\n" + time_detail
                # 提取注意事项
                note_match = re.search(r'(注[：:][^\n]*)', ocr_text)
                if note_match:
                    note_detail = note_match.group(1).strip()
                    if note_detail and note_detail not in pkg_content:
                        pkg_content += "\n" + note_detail
            # 每个套餐的独立加收
            pkg_surcharge = pkg.get("surcharge", "").strip()
            if not pkg_surcharge:
                pkg_surcharge = ai_result.get("surcharge", "")
            # 原始输入模式：如果文案中没有加收信息，套餐加收也留空
            if text and text.strip() and not any(kw in text for kw in ['加收', '加价', '周末', '节假日', '平日']):
                pkg_surcharge = ""

            # 从文案中匹配返佣（门票产品）
            pkg_price = pkg.get("price", "")
            pkg_rebate = ""
            pkg_order_id = ""
            pkg_retail_price = ""
            pkg_settlement = ""
            if text and text.strip():
                # 先尝试匹配"深返XX"、"返佣XX"、"反佣XX"格式（所有套餐共用同一个返佣，支持带冒号/空格写法）
                common_rebate_match = re.search(r'(?:深返|返佣|反佣)[：:]?\s*(\d+(?:\.\d+)?)', text)
                common_rebate = common_rebate_match.group(1) if common_rebate_match else ""

                # 再匹配"价格返返佣"格式（每个套餐独立返佣）
                rebate_pattern = r'(\d+)返(\d+)'
                for m in re.finditer(rebate_pattern, text):
                    price_str = m.group(1)
                    rebate_str = m.group(2)
                    if pkg_price and (price_str == re.sub(r'[^0-9.]', '', pkg_price)):
                        pkg_rebate = rebate_str
                        break
                # 如果没匹配到独立返佣，尝试按顺序匹配
                if not pkg_rebate:
                    all_rebates = list(re.finditer(rebate_pattern, text))
                    pkg_idx = ai_packages.index(pkg)
                    if pkg_idx < len(all_rebates):
                        m = all_rebates[pkg_idx]
                        pkg_price = m.group(1)
                        pkg_rebate = m.group(2)

                # 如果还是没有返佣，使用公共返佣
                if not pkg_rebate and common_rebate:
                    pkg_rebate = common_rebate

                # 提取下单编号
                order_ids = re.findall(r'编号(\d+)', text)
                pkg_order_id = order_ids[ai_packages.index(pkg)] if ai_packages.index(pkg) < len(order_ids) else ""

                # 计算结算价
                if pkg_price and pkg_rebate:
                    try:
                        price_clean = re.sub(r'[^0-9.]', '', pkg_price)
                        live_num = float(price_clean)
                        deep_rebate = float(pkg_rebate)
                        # 门票/酒店对外价格计算不同
                        if product_type == "ticket":
                            retail_price = int(live_num / 0.7)
                            platform_cost = 0.05
                        else:
                            retail_price = int(live_num / 0.9)
                            platform_cost = 0.08
                        settlement = live_num - deep_rebate
                        profit_rate = (deep_rebate / live_num) - platform_cost
                        pkg_settlement = f"结算价{int(settlement)}；利润率{profit_rate*100:.1f}%"

                        # 解析加收信息，提取加收金额，计算加收后的结算
                        # 用 [ \t]* 而非 \s*，避免跨行把"不加收"下一行的"8月"当成加收金额
                        surcharge_amounts = re.findall(r'加收[ \t]*[：:]?[ \t]*(\d+(?:\.\d+)?)', pkg_surcharge)
                        if not surcharge_amounts:
                            surcharge_amounts = re.findall(r'[+＋](\d+(?:\.\d+)?)', pkg_surcharge)
                        if surcharge_amounts:
                            extra_parts = []
                            for amount_str in surcharge_amounts:
                                extra = float(amount_str)
                                if extra > 0:
                                    extra_settlement = (live_num + extra) - deep_rebate
                                    extra_profit = (deep_rebate / (live_num + extra)) - platform_cost
                                    extra_pct = extra_profit * 100
                                    extra_parts.append(f"加收{int(extra)}结算价{int(extra_settlement)}；利润率{extra_pct:.1f}%")
                            if extra_parts:
                                pkg_settlement += "（" + "；".join(extra_parts) + "）"

                        pkg_retail_price = str(retail_price)
                    except (ValueError, ZeroDivisionError):
                        pass

                # 兜底：如果文案中有"结算价：XXX元"，直接提取并反推利润率
                if not pkg_settlement and text and text.strip():
                    settlement_match = re.search(r'结算价[：:]?\s*(\d+(?:\.\d+)?)\s*元', text)
                    if settlement_match and pkg_price:
                        try:
                            live_num = float(re.sub(r'[^0-9.]', '', pkg_price))
                            settle_num = float(settlement_match.group(1))
                            if live_num > 0:
                                platform_cost = 0.08 if product_type == "hotel" else 0.05
                                deep_rebate = live_num - settle_num
                                if deep_rebate > 0:
                                    profit_rate = (deep_rebate / live_num) - platform_cost
                                    pkg_settlement = f"结算价{int(settle_num)}；利润率{profit_rate*100:.1f}%"
                                else:
                                    pkg_settlement = f"结算价{int(settle_num)}"
                        except (ValueError, ZeroDivisionError):
                            pkg_settlement = f"结算价{int(float(settlement_match.group(1)))}"

            # 构建套票名称：【套餐名】景区名~主体内容+差异项
            pkg_name_raw = pkg.get("name", "")
            hotel_name = ai_result.get("hotel_name", "")
            common_items = ai_result.get("common_items", "")
            pkg_diff = pkg.get("diff", "")
            # 主体内容取相同项的前60个字
            main_body = common_items[:60] if common_items else ""
            # 差异项关键词（去掉"无"、"没有"等无效内容）
            diff_keyword = ""
            if pkg_diff and pkg_diff.strip() not in ("无", "没有", ""):
                diff_keyword = pkg_diff[:30]
            if main_body:
                if diff_keyword and diff_keyword not in main_body:
                    pkg_taopiao_name = f"【{pkg_name_raw}】{hotel_name}~{main_body}+{diff_keyword}"
                else:
                    pkg_taopiao_name = f"【{pkg_name_raw}】{hotel_name}~{main_body}"
            else:
                pkg_taopiao_name = f"【{pkg_name_raw}】{hotel_name}~{diff_keyword}" if diff_keyword else f"【{pkg_name_raw}】{hotel_name}"

            # 清理价格，去掉非数字字符
            pkg_price_clean = re.sub(r'[^0-9.]', '', pkg_price) if pkg_price else pkg_price

            packages.append({
                "name": pkg_taopiao_name,
                "price": pkg_price_clean,
                "content": pkg_content,
                "surcharge": pkg_surcharge,
                "available_date": ai_result.get("available_date", ""),
                "rebate": pkg_rebate,
                "order_id": pkg_order_id,
                "retail_price": pkg_retail_price,
                "settlement": pkg_settlement,
            })
    elif product_type == "ticket" and text and text.strip():
        # 兜底：AI没识别出多套餐时，用脚本解析
        packages = parse_ticket_packages(text.strip(), product_type=product_type)

    # 如果 packages 为空但文案中有结算价，包装成单套餐
    if not packages and text and text.strip():
        settlement_match = re.search(r'结算价[：:]?\s*(\d+(?:\.\d+)?)\s*元', text)
        if settlement_match:
            pkg_price = ai_result.get("price", "")
            pkg_settlement = ""
            if pkg_price:
                try:
                    live_num = float(re.sub(r'[^0-9.]', '', pkg_price))
                    settle_num = float(settlement_match.group(1))
                    if live_num > 0:
                        platform_cost = 0.08 if product_type == "hotel" else 0.05
                        deep_rebate = live_num - settle_num
                        if deep_rebate > 0:
                            profit_rate = (deep_rebate / live_num) - platform_cost
                            pkg_settlement = f"结算价{int(settle_num)}；利润率{profit_rate*100:.1f}%"
                        else:
                            pkg_settlement = f"结算价{int(settle_num)}"
                except (ValueError, ZeroDivisionError):
                    pkg_settlement = f"结算价{int(float(settlement_match.group(1)))}"
            else:
                pkg_settlement = f"结算价{int(float(settlement_match.group(1)))}"

            packages.append({
                "name": ai_result.get("taopiao", ""),
                "price": ai_result.get("price", ""),
                "content": ai_result.get("content_items", ""),
                "surcharge": ai_result.get("surcharge", ""),
                "available_date": ai_result.get("available_date", ""),
                "rebate": "",
                "order_id": "",
                "retail_price": "",
                "settlement": pkg_settlement,
            })

    # 从文案中提取佣金信息
    rebate_value = ""
    if text and text.strip():
        # 匹配"深返XX"、"返佣XX"、"反佣XX"等格式（支持"返佣：83"、"返佣 83"等带冒号/空格写法）
        rebate_match = re.search(r'(?:深返|返佣|反佣)[：:]?\s*(\d+(?:\.\d+)?)', text)
        if rebate_match:
            rebate_value = f"深返{rebate_match.group(1)}"
        else:
            # 匹配"结算返XX元"
            rebate_match2 = re.search(r'结算返\s*(\d+(?:\.\d+)?)\s*元', text)
            if rebate_match2:
                rebate_value = f"深返{rebate_match2.group(1)}"

    return OCRResponse(
        raw_text=ai_raw,
        title="",
        hotel_name=ai_result.get("hotel_name", ""),
        price=ai_result.get("price", ""),
        content_items=ai_result.get("content_items", ""),
        surcharge=ai_result.get("surcharge", ""),
        other_surcharge=ai_result.get("other_surcharge", ""),
        sell_time=ai_result.get("sell_time", ""),
        available_date=ai_result.get("available_date", ""),
        taopiao=ai_result.get("taopiao", ""),
        rebate=rebate_value,
        packages=packages,
    )


@app.post("/api/write", response_model=WriteResponse)
async def api_write(req: WriteRequest):
    """
    写入飞书表格。
    接收完整的列数据，自动计算结算价格&利润率。
    自动读取表格表头，将结算价填入匹配的列。
    """
    import logging

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
        settlement = calc_settlement(parsed_for_calc, row_data, product_type=req.product_type)
        if settlement:
            # 动态找到结算价列名
            token = req.spreadsheet_token
            if token:
                try:
                    sheet_id = get_sheet_id(token, "Sheet1")
                    headers = _get_header_row(token, sheet_id)
                    for h in headers:
                        if "结算" in h or "利润率" in h:
                            row_data[h] = settlement
                            break
                    else:
                        row_data["结算价格&利润率（已除平台）"] = settlement
                except Exception:
                    row_data["结算价格&利润率（已除平台）"] = settlement
            else:
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
        # 表头校验：row_data 中表头不存在的列会被静默丢弃，这里显式暴露出来
        dropped_columns = []
        empty_columns = []
        try:
            check_sheet_id = get_sheet_id(token, sheet_name)
            valid_headers = [h for h in _get_header_row(token, check_sheet_id) if h]
            if valid_headers:
                dropped_columns = [k for k in row_data if k not in valid_headers]
                empty_columns = [
                    h for h in valid_headers
                    if not str(row_data.get(h, "") or "").strip()
                ]
                if dropped_columns:
                    logging.warning(
                        f"[api_write] 以下列在表头中不存在，已丢弃: {dropped_columns}"
                    )
                if empty_columns:
                    logging.warning(
                        f"[api_write] 以下列写入为空: {empty_columns}"
                    )
        except Exception as e:
            logging.warning(f"[api_write] 表头校验跳过: {e}")

        result = append_row_by_cols(row_data, spreadsheet_token=token, sheet_title=sheet_name)
        # 从返回结果中提取行号（_row_number 由 lark_writer 注入）
        row_number = 0
        try:
            result_data = json.loads(result) if isinstance(result, str) else result
            if isinstance(result_data, dict):
                row_number = result_data.get("_row_number", 0)
        except (json.JSONDecodeError, AttributeError, ValueError):
            pass
        msg = "写入成功"
        if empty_columns:
            msg += f"；{len(empty_columns)} 列为空: {'、'.join(empty_columns[:5])}"
        if dropped_columns:
            msg += f"；{len(dropped_columns)} 列不在表头已丢弃: {'、'.join(dropped_columns[:5])}"
        return WriteResponse(
            success=True,
            message=msg,
            row_number=row_number,
            dropped_columns=dropped_columns,
            empty_columns=empty_columns,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入失败: {str(e)}")


@app.post("/api/calc-settlement", response_model=CalcSettlementResponse)
async def api_calc_settlement(req: CalcSettlementRequest):
    """计算结算价和利润率。"""
    parsed = {"返佣": req.rebate}
    col_data = {"直播价": req.live_price, "加收": req.surcharge}
    result = calc_settlement(parsed, col_data, product_type=req.product_type)
    return CalcSettlementResponse(result=result)


class RecalcRequest(BaseModel):
    spreadsheet_token: str = ""


class RecalcResponse(BaseModel):
    success: bool
    message: str
    updated_rows: int = 0


@app.post("/api/recalc-settlement", response_model=RecalcResponse)
async def api_recalc_settlement(req: RecalcRequest):
    """
    重新计算表格中已有行的结算价。
    读取表格中所有行，找到直播价和备注（返佣），重新计算结算价并更新。
    """
    token = req.spreadsheet_token
    if not token:
        return RecalcResponse(success=False, message="请提供飞书表格链接")

    try:
        sheet_id = get_sheet_id(token, "Sheet1")
        headers = _get_header_row(token, sheet_id)
        if not headers:
            return RecalcResponse(success=False, message="无法读取表格表头")

        # 找到各列索引
        live_idx = None
        settle_idx = None
        remark_idx = None
        surcharge_idx = None
        type_idx = None
        for i, h in enumerate(headers):
            if h == "直播价":
                live_idx = i
            elif "结算" in h or "利润率" in h:
                settle_idx = i
            elif h == "备注":
                remark_idx = i
            elif h == "加收":
                surcharge_idx = i
            elif h == "产品类型":
                type_idx = i

        if live_idx is None or settle_idx is None:
            return RecalcResponse(success=False, message="表格缺少直播价或结算价列")

        # 读取所有行
        col_letter = chr(65 + max(live_idx, settle_idx, remark_idx or 0, surcharge_idx or 0, type_idx or 0))
        range_str = f"A:{col_letter}"
        values = lark_client.read_values(token, sheet_id, range_str)

        if len(values) <= 1:
            return RecalcResponse(success=False, message="表格中没有数据行")

        updated = 0
        for row_idx, row in enumerate(values):
            if row_idx == 0:
                continue  # 跳过表头

            # 跳过空行
            if not row or all(not cell for cell in row):
                continue

            # 如果结算价列已有值，跳过
            if settle_idx < len(row) and row[settle_idx]:
                continue

            # 获取直播价
            live_price = row[live_idx] if live_idx < len(row) else ""
            if not live_price:
                continue

            # 从备注中提取返佣
            rebate = ""
            if remark_idx is not None and remark_idx < len(row):
                remark = row[remark_idx] or ""
                rebate_match = re.search(r'返佣[：:]?\s*(\d+)', remark)
                if rebate_match:
                    rebate = rebate_match.group(1)

            if not rebate:
                continue

            # 获取加收
            surcharge = row[surcharge_idx] if surcharge_idx is not None and surcharge_idx < len(row) else ""

            # 判断产品类型
            product_type = "hotel"
            if type_idx is not None and type_idx < len(row):
                type_val = row[type_idx] or ""
                if "门票" in type_val:
                    product_type = "ticket"

            # 计算结算价
            parsed = {"返佣": rebate}
            col_data = {"直播价": live_price, "加收": surcharge}
            settlement = calc_settlement(parsed, col_data, product_type=product_type)
            if not settlement:
                continue

            # 更新该行的结算价列
            row_num = row_idx + 1  # 1-based
            settle_col = chr(65 + settle_idx)
            cell_range = f"{settle_col}{row_num}"
            lark_client.update_values(token, sheet_id, cell_range, [[settlement]])
            updated += 1

        return RecalcResponse(
            success=True,
            message=f"重新计算完成，已更新 {updated} 行的结算价",
            updated_rows=updated,
        )

    except Exception as e:
        return RecalcResponse(success=False, message=f"重新计算失败: {str(e)}")


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


@app.get("/api/headers")
async def api_headers(spreadsheet_token: str = ""):
    """读取飞书表格表头。"""
    try:
        if not spreadsheet_token:
            config = load_config()
            spreadsheet_token = get_spreadsheet_token(config)
        sheet_name = "Sheet1"
        sheet_id = get_sheet_id(spreadsheet_token, sheet_name)
        headers = _get_header_row(spreadsheet_token, sheet_id)
        return {"headers": headers}
    except Exception as e:
        return {"headers": [], "error": str(e)}


@app.post("/api/create-sheet")
async def api_create_sheet():
    """
    创建新的飞书表格，设置 15 列表头，并更新 config.yaml。
    """
    # 1. 创建表格
    headers = [
        "填写日期", "产品类型", "酒店/景区", "产品信息", "套票名称",
        "套票内容", "加收", "其他加收", "售卖时间", "可用日期",
        "对外价格", "直播价", "结算价格&利润率（已除平台）", "供应商信息", "海报",
    ]
    try:
        created = lark_client.create_spreadsheet("hotels/tickets上品工具")
        spreadsheet_token = created.get("spreadsheet_token", "")
        url = created.get("url", "")
        if not spreadsheet_token:
            raise HTTPException(status_code=500, detail="创建表格返回数据异常")

        # 应用创建的文档默认仅应用可访问，放开为组织内可编辑
        share_ok = lark_client.set_tenant_editable(spreadsheet_token)

        # 新表默认 Sheet1，写入表头
        sheets = lark_client.list_sheets(spreadsheet_token)
        if sheets:
            sheet_id = sheets[0]["sheet_id"]
            end_col = chr(64 + len(headers))
            lark_client.update_values(
                spreadsheet_token, sheet_id, f"{sheet_id}!A1:{end_col}1", [headers]
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建表格失败: {e}")

    return {
        "success": True,
        "spreadsheet_token": spreadsheet_token,
        "url": url,
        "tenant_editable": share_ok,
        "message": "表格创建成功" + ("" if share_ok else "（提示：自动开放组织内编辑权限失败，请在飞书中手动添加协作者）"),
    }


# ============================================================
# Excel 本地表格 API
# ============================================================

EXCEL_DIR = project_root / "excel_files"
EXCEL_DIR.mkdir(exist_ok=True)


def _get_excel_path(user_id: int) -> str:
    """按用户 ID 返回独立的 Excel 文件路径。"""
    return str(EXCEL_DIR / f"上品工具_{user_id}.xlsx")


class ExcelWriteRequest(BaseModel):
    row_data: dict


class ExcelWriteResponse(BaseModel):
    success: bool
    message: str
    row_number: int = 0


@app.post("/api/excel/init")
async def api_excel_init(user: dict = Depends(get_current_user)):
    """初始化 Excel 文件：如果文件不存在则创建，已存在则跳过。"""
    from src.excel_writer import create_excel
    excel_path = _get_excel_path(user["id"])
    try:
        if not os.path.exists(excel_path):
            create_excel(excel_path)
        return {
            "success": True,
            "filename": "上品工具.xlsx",
            "message": "Excel 文件已就绪",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"初始化 Excel 失败: {str(e)}")


@app.post("/api/excel/reset")
async def api_excel_reset(user: dict = Depends(get_current_user)):
    """重置 Excel 文件：强制覆盖，清空所有数据行。"""
    from src.excel_writer import create_excel
    excel_path = _get_excel_path(user["id"])
    try:
        create_excel(excel_path)
        return {
            "success": True,
            "filename": "上品工具.xlsx",
            "message": "Excel 文件已重置",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重置 Excel 失败: {str(e)}")


@app.post("/api/excel/write", response_model=ExcelWriteResponse)
async def api_excel_write(req: ExcelWriteRequest, user: dict = Depends(get_current_user)):
    """写入一行到 Excel 文件（追加模式）。"""
    excel_path = _get_excel_path(user["id"])
    if not os.path.exists(excel_path):
        raise HTTPException(status_code=400, detail="Excel 文件未初始化，请先调用 /api/excel/init")

    row_data = dict(req.row_data)

    # 自动填入填写日期
    if not row_data.get("填写日期"):
        row_data["填写日期"] = date.today().isoformat()

    try:
        from src.excel_writer import append_row
        row_number = append_row(excel_path, row_data)
        return ExcelWriteResponse(
            success=True,
            message=f"已写入第 {row_number} 行",
            row_number=row_number,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入 Excel 失败: {str(e)}")


@app.get("/api/excel/read")
async def api_excel_read(user: dict = Depends(get_current_user)):
    """读取 Excel 全部数据，返回表头和行数据。"""
    excel_path = _get_excel_path(user["id"])
    if not os.path.exists(excel_path):
        return {"headers": [], "rows": [], "total_rows": 0}

    try:
        from openpyxl import load_workbook
        wb = load_workbook(excel_path, read_only=True)
        ws = wb.active

        # 读取表头
        headers = []
        for cell in ws[1]:
            headers.append(cell.value or "")

        # 读取数据行（从第2行开始）
        rows = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            row_data = []
            for cell in row:
                row_data.append(str(cell) if cell is not None else "")
            rows.append(row_data)

        wb.close()
        return {
            "headers": headers,
            "rows": rows,
            "total_rows": len(rows),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取 Excel 失败: {str(e)}")


@app.get("/api/excel/download")
async def api_excel_download(user: dict = Depends(get_current_user)):
    """下载 Excel 文件。"""
    excel_path = _get_excel_path(user["id"])
    if not os.path.exists(excel_path):
        raise HTTPException(status_code=404, detail="文件不存在")
    from fastapi.responses import FileResponse
    return FileResponse(
        path=excel_path,
        filename="上品工具.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# 静态文件和前端页面（放在所有 API 路由之后，避免覆盖 API 路由）
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=80, reload=True,
                reload_dirs=[str(project_root / "src"), str(project_root / "web/backend")])
