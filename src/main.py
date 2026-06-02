#!/usr/bin/env python3
"""
内容提取工具 - CLI 主入口。

用法:
    python src/main.py                    # 粘贴原始输入，自动提取秀米内容并写入飞书
    python src/main.py extract <url>      # 仅提取秀米内容，不写入飞书
    python src/main.py check              # 检查 lark-cli 是否可用
"""

import re
import sys
import argparse
from datetime import date
from pathlib import Path

# 确保项目根目录在 sys.path 中
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.extractor import XiumiusExtractor
from src.lark_writer import (
    append_row_by_cols,
    check_lark_cli,
    load_config,
)
from src.poster_ocr import ocr_image
from src.ai_helper import ai_extract_surcharge, ai_extract_content, ai_generate_taopiao, ai_extract_all


def parse_raw_input(text: str) -> dict:
    """
    解析用户原始输入，提取各字段。

    输入格式：
        【标题】...
        🆔编号：xxx
        🚗房态：https://...
        [烟花]抢购：https://...
        🎆秀米：https://...
        [红包]普返xx，深返xx
        下单编号：xxx

    Returns:
        dict: 包含 parsed 各字段的字典
    """
    result = {}

    # 提取秀米链接
    # 优先匹配 xiumius.cn 域名
    xiumius_match = re.search(r'https?://[a-z]+\.xiumius\.cn\S+', text)
    if xiumius_match:
        result["xiumius_url"] = xiumius_match.group(0)
    else:
        # 其次匹配 "文案：" 或 "文案链接：" 或 "👀文案：" 后面的链接（可能是 navo.top 等短链接，指向秀米页面）
        wenan_match = re.search(r'(?:文案|文案链接|👀文案)[：:]\s*(https?://\S+)', text)
        if wenan_match:
            result["xiumius_url"] = wenan_match.group(1)

    # 提取编号
    id_match = re.search(r'编号[：:]\s*(\d+)', text)
    if id_match:
        result["编号"] = id_match.group(1)

    # 提取房态链接
    fangtai_match = re.search(r'房态[：:]\s*(https?://\S+)', text)
    if fangtai_match:
        result["房态链接"] = fangtai_match.group(1)

    # 提取抢购链接
    qianggou_match = re.search(r'抢购[：:]\s*(https?://\S+)', text)
    if qianggou_match:
        result["抢购链接"] = qianggou_match.group(1)

    # 提取返佣信息
    # 支持多种格式：[红包]普返xx，深返xx  /  💰普返：40  深度：75  /  普返xx，深返xx
    fan_match = re.search(r'(?:\[红包\]|💰|返佣[：:])?\s*普返\s*[：:]?\s*(\d+(?:\.\d+)?)\s*(?:，|,|、|\s+)?\s*(?:深度?|深返)\s*[：:]?\s*(\d+(?:\.\d+)?)', text)
    if fan_match:
        result["返佣"] = f"普返{fan_match.group(1)}，深返{fan_match.group(2)}"

    # 提取下单编号
    order_match = re.search(r'下单编号[：:]\s*(\d+)', text)
    if order_match:
        result["下单编号"] = order_match.group(1)

    # 提取售卖时间（从文案中，如"抢购日期：即日起--售完即止"）
    # 注意：必须匹配"抢购日期"或"售卖时间"，不能匹配"抢购链接"
    sell_match = re.search(r'(?:抢购日期|售卖时间|售卖日期)[：:]\s*(.+?)(?:\n|$)', text)
    if sell_match:
        result["售卖时间"] = sell_match.group(1).strip()

    # 提取可用日期（从文案中，如"使用日期：即日起--2026年10月31日"）
    avail_match = re.search(r'(?:使用|可用|有效)[日期时间]*[：:]\s*(.+?)(?:\n|$)', text)
    if avail_match:
        result["可用日期"] = avail_match.group(1).strip()

    # 提取标题行（第一行）
    lines = text.strip().split("\n")
    if lines:
        result["标题行"] = lines[0].strip()

    return result


def infer_highlight(title: str) -> str:
    """
    从标题推理主要卖点（亮点词）。

    规则（按优先级）：
    - 包含"不加收" → "周末不加"
    - 包含"五一"+"一口价" → "51一口价"
    - 包含"五一" → "五一主推"
    - 包含"一口价" → "一口价"
    - 包含"秒杀" → "秒杀"
    - 包含"专场" → "专场"
    - 包含"特惠" → "特惠"
    - 包含"预售" → "预售"
    - 包含"暑期" → "暑期"
    - 包含"开园" → "开园"
    - 包含"含晚餐"或"含早" → "含餐"
    - 默认 → "主推"
    """
    if not title:
        return "主推"
    if "不加收" in title:
        return "周末不加"
    if "五一" in title or "51" in title:
        if "一口价" in title:
            return "51一口价"
        return "五一主推"
    if "一口价" in title:
        return "一口价"
    if "秒杀" in title:
        return "秒杀"
    if "专场" in title:
        return "专场"
    if "特惠" in title:
        return "特惠"
    if "预售" in title:
        return "预售"
    if "暑期" in title:
        return "暑期"
    if "开园" in title:
        return "开园"
    if "含晚餐" in title or "含早" in title:
        return "含餐"
    return "主推"


def generate_taopiao(title: str, content: str = "") -> str:
    """
    从标题行和秀米内容生成套票名称。

    格式：【主要卖点】酒店名称·酒店房型｜套餐权益1+权益2+权益3+等等

    主要卖点从标题推理（如不加收/含晚餐/暑期预售/开园预售等，也可自己提炼）。
    套餐权益从秀米内容中提取【住】【食】【赠】【畅】【玩】【享】【免】等标签行。
    """
    if not title:
        return ""

    # 1. 推理主要卖点（亮点词）
    highlight = infer_highlight(title)

    # 2. 提取酒店名
    hotel_name = ""
    hotel_match = re.search(r'【(.+?)】', title)
    if hotel_match:
        hotel_name = hotel_match.group(1)
    else:
        # 无【】时，从标题中提取酒店名
        # 匹配 "5星级-清远巧克力天樾王国酒店" → "天樾王国酒店"
        name_match = re.search(r'([\u4e00-\u9fff]+酒店)', title)
        if name_match:
            hotel_name = name_match.group(1)
            # 去掉常见前缀（地域名、品牌名等）
            for prefix in ['清远', '惠州', '广州', '深圳', '东莞', '佛山', '珠海',
                           '江门', '肇庆', '中山', '汕头', '湛江', '韶关']:
                if hotel_name.startswith(prefix):
                    hotel_name = hotel_name[len(prefix):]
                    break
            # 再去掉"巧克力"等非地域前缀
            for prefix in ['巧克力']:
                if hotel_name.startswith(prefix):
                    hotel_name = hotel_name[len(prefix):]
                    break

    # 如果标题中没有酒店名，从 content 中提取
    if not hotel_name and content:
        # 查找包含"酒店"的行
        for line in content.split("\n"):
            hotel_in_line = re.search(r'([\u4e00-\u9fff]+酒店)', line)
            if hotel_in_line:
                hotel_name = hotel_in_line.group(1)
                # 去掉常见前缀
                for prefix in ['清远', '惠州', '广州', '深圳', '东莞', '佛山', '珠海',
                               '江门', '肇庆', '中山', '汕头', '湛江', '韶关']:
                    if hotel_name.startswith(prefix):
                        hotel_name = hotel_name[len(prefix):]
                        break
                for prefix in ['巧克力']:
                    if hotel_name.startswith(prefix):
                        hotel_name = hotel_name[len(prefix):]
                        break
                break

    # 3. 提取房型
    fangxing = ""

    # 房型关键词（按长度降序，优先匹配更具体的）
    fangxing_keywords = ['豪华阳台房', '豪华阳台大床或双床', '联排别墅池景房', '池景房',
                         '山景房', '海景房', '温泉房', '别墅房', '阳台房', '高级房',
                         '大床房', '双床房', '臻品房', '豪华房', '大床或双床', '池景',
                         '别墅', '海景', '套房']

    # 策略1：从标题中找已知房型关键词，取最长的匹配（排除酒店名【】内的匹配）
    hotel_end = title.find('】')
    best_kw = ""
    best_idx = -1
    for kw in fangxing_keywords:
        idx = title.find(kw)
        if idx != -1:
            # 跳过酒店名【】内的匹配
            if hotel_end != -1 and idx < hotel_end:
                continue
            # 取更长的关键词（更具体）
            if len(kw) > len(best_kw):
                best_kw = kw
                best_idx = idx

    if best_kw:
        fangxing = best_kw

    # 策略2：找"秒杀"后面的房型
    if not fangxing:
        fangxing_match = re.search(r'秒杀(.+?(?:房|别墅|海景|温泉|池景|山景))', title)
        if fangxing_match:
            fangxing = fangxing_match.group(1).strip()

    # 策略3：找"抢"后面的房型
    if not fangxing:
        fangxing_match = re.search(r'抢(.+?(?:房|别墅|海景|温泉|池景|山景))', title)
        if fangxing_match:
            fangxing = fangxing_match.group(1).strip()

    # 策略4：找"元"后面的房型（只取到第一个标点或+号）
    if not fangxing:
        fangxing_match = re.search(r'(?:元|￥|¥)\s*(\S+?(?:房|别墅|海景|温泉|池景|山景|双床|大床))', title)
        if fangxing_match:
            fangxing = fangxing_match.group(1).strip()
            # 截断到第一个标点或+号
            for sep in ['+', '！', '!', '，', ',', '。', '.']:
                pos = fangxing.find(sep)
                if pos != -1:
                    fangxing = fangxing[:pos]
                    break

    # 策略5：从 content（🈶 行）中提取房型
    if not fangxing and content:
        for line in content.split("\n"):
            line = line.strip()
            for kw in fangxing_keywords:
                if kw in line:
                    fangxing = kw
                    break
            if fangxing:
                break

    # 去掉括号备注
    if fangxing:
        fangxing = re.sub(r'[（(][^）)]*[）)]', '', fangxing).strip()

    # 4. 提取套餐权益
    equity_list = []

    # 优先从 content（秀米内容）中提取【】标签行
    if content:
        package_tags = {'住', '食', '餐', '玩', '游', '赠', '享', '免', '泡', '浸', '畅', '行'}
        for line in content.split("\n"):
            line = line.strip()
            if not line:
                continue
            m = re.match(r'^【([^】]+)】', line)
            if m and m.group(1) in package_tags:
                equity_text = line[m.end():].strip()
                if equity_text:
                    # 过滤掉包含酒店名的行（房型描述，不是权益）
                    if hotel_name and hotel_name in equity_text:
                        continue
                    # 过滤掉纯房型关键词的行
                    if equity_text in fangxing_keywords:
                        continue
                    if len(equity_text) > 20:
                        equity_text = equity_text[:20]
                    equity_list.append(equity_text)

    # 如果 content 中没有提取到权益，从标题中提取卖点作为后备
    if not equity_list:
        # 从标题中提取 + 号连接的卖点
        maidian_parts = re.findall(r'\+([^+]+?)(?=\+|~|！|$)', title)
        for p in maidian_parts:
            p = p.strip()
            if len(p) > 15:
                p = p[:15]
            if p and p not in equity_list:
                equity_list.append(p)

    # 取前4个权益
    equity_list = equity_list[:4]
    equity_str = "+".join(equity_list) if equity_list else ""

    # 5. 构建套票名称
    parts = [f"【{highlight}】{hotel_name}"]
    if fangxing:
        parts.append(f"·{fangxing}")
    if equity_str:
        parts.append(f"｜{equity_str}")

    script_result = "".join(parts)

    # ---- AI 兜底 ----
    # 如果脚本结果缺少关键信息（如没有权益、没有房型），尝试 AI 生成
    if not equity_str or not fangxing:
        try:
            ai_result = ai_generate_taopiao(title, content)
            if ai_result:
                return ai_result
        except Exception:
            pass

    return script_result


def cmd_extract(url: str) -> str:
    """提取 URL 中的内容。"""
    extractor = XiumiusExtractor()
    print(f"正在提取秀米内容: {url}")
    content = extractor.extract(url)
    print("\n" + "=" * 50)
    print("提取结果:")
    print("=" * 50)
    print(content)
    print("=" * 50)
    return content


def extract_content_items(raw_content: str) -> str:
    """
    从提取的秀米内容中提取套餐包含项。

    先尝试脚本规则提取，如果结果为空，调用 AI 兜底。

    支持两种格式：
    1. 【住】【食】【玩】等单字标签格式
    2. "套餐包含"标题下的数字序号列表（1、2、3...）

    去掉价格、时间、加收等额外信息。
    过滤掉非套餐分类标签（如【特别声明】【温馨提示】【周边游玩推荐】等）。
    """
    # 套餐分类标签关键词（精确匹配，避免子串误匹配）
    package_tags = {'住', '食', '餐', '玩', '游', '赠', '享', '免', '泡', '浸', '畅', '行'}

    # 预处理：将同一行内多个【】标签拆分成多行
    # 例如 "【住】xxx【住】xxx【玩】xxx" → "【住】xxx\n【住】xxx\n【玩】xxx"
    raw_content = re.sub(r'(?=【)', '\n', raw_content).strip()

    lines = raw_content.split("\n")
    items = []
    in_package_section = False
    package_end_keywords = ['加收说明', '温馨提示', '特别声明', '时间说明', '抢购时间', '使用时间',
                            '酒店地址', '酒店接待规则', '周边热门游玩', '交通指南']

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 检测"套餐包含"标题，进入套餐区域
        if '套餐包含' in line:
            in_package_section = True
            continue

        # 检测结束关键词
        if in_package_section:
            should_stop = False
            for kw in package_end_keywords:
                if kw in line:
                    should_stop = True
                    break
            if should_stop:
                in_package_section = False
                continue

        # 在套餐区域内，提取数字序号行或【】标签行
        if in_package_section:
            # 匹配数字序号：1、2、3... 或 ① ② ③...
            if re.match(r'^(\d+[、.．]|[①②③④⑤⑥⑦⑧⑨⑩])', line):
                items.append(line)
            # 匹配子序号：① ② ③... 或 - 开头
            elif re.match(r'^[①②③④⑤⑥⑦⑧⑨⑩]', line):
                items.append(line)
            elif re.match(r'^[-—]', line):
                items.append(line)
            # 匹配 * 开头的备注行
            elif re.match(r'^\*', line):
                items.append(line)
            # 匹配【】标签行（如【住】、【餐】、【泡】等）
            elif re.match(r'^【([^】]+)】', line):
                m = re.match(r'^【([^】]+)】', line)
                if m and m.group(1) in package_tags:
                    items.append(line)
            continue

        # 匹配以【】开头的行（标签格式）
        m = re.match(r'^【([^】]+)】', line)
        if m:
            tag = m.group(1)
            if tag in package_tags:
                items.append(line)

    script_result = "\n".join(items).strip()

    # ---- AI 兜底 ----
    if not script_result:
        try:
            ai_result = ai_extract_content(raw_content)
            if ai_result:
                return ai_result
        except Exception:
            pass

    return script_result


def extract_surcharge(raw_content: str) -> str:
    """
    从秀米原始内容中提取加收信息。

    先尝试脚本规则提取，如果结果为空或质量差，调用 AI 兜底。
    """
    if not raw_content:
        return ""

    # ---- 脚本规则提取 ----
    lines = raw_content.split("\n")
    surcharge_lines = []
    in_surcharge = False
    surcharge_end_keywords = [
        '温馨提示', '特别声明', '时间说明', '抢购时间', '使用时间',
        '酒店地址', '酒店接待规则', '周边热门游玩', '交通指南',
        '退款说明', '购买须知',
    ]
    upgrade_keywords = ['升级', '增订', '前台增订', '前台购买', '可增订', '可选升级']

    for line in lines:
        line = line.strip()
        if not line:
            if in_surcharge:
                in_surcharge = False
            continue

        is_surcharge_header = (
            line.startswith('加收') or
            '加收说明' in line or
            '加收规则' in line or
            '加收标准' in line
        )
        if is_surcharge_header:
            in_surcharge = True
            continue

        if ('加收' in line or '加价' in line or '不加收' in line) and not is_surcharge_header:
            in_surcharge = True

        if in_surcharge:
            should_stop = False
            for kw in surcharge_end_keywords:
                if kw in line:
                    should_stop = True
                    break
            if should_stop:
                in_surcharge = False
                continue

            is_upgrade = any(kw in line for kw in upgrade_keywords)
            if is_upgrade:
                continue

            if re.match(r'^\d+[、.．]', line):
                continue

            surcharge_lines.append(line)

    script_result = "\n".join(surcharge_lines).strip()

    # ---- AI 兜底 ----
    # 如果脚本结果为空，或只有"不加收"等简单信息，尝试 AI 提取
    if not script_result or script_result in ["不加收", "周末不加收", "平日周末不加收"]:
        try:
            ai_result = ai_extract_surcharge(raw_content)
            if ai_result:
                return ai_result
        except Exception:
            pass

    return script_result


def extract_sell_time(raw_content: str) -> str:
    """
    从秀米原始内容中提取售卖时间。
    """
    if not raw_content:
        return ""

    match = re.search(r'(?:抢购|售卖)[时间]*[：:]\s*(.+?)(?:\n|$)', raw_content)
    if match:
        return match.group(1).strip()
    return ""


def extract_available_date(raw_content: str) -> str:
    """
    从秀米原始内容中提取可用/使用日期。
    """
    if not raw_content:
        return ""

    match = re.search(r'(?:使用|可用|有效)[时间]*[：:]\s*(.+?)(?:\n|$)', raw_content)
    if match:
        return match.group(1).strip()
    return ""


def extract_price(raw_content: str) -> str:
    """
    从秀米原始内容中提取价格（直播价）。
    """
    if not raw_content:
        return ""

    price_patterns = [
        r'抢购价[：:]\s*[¥￥]?(\d+(?:\.\d+)?)',
        r'[¥￥](\d+(?:\.\d+)?)\s*元',
        r'(\d+(?:\.\d+)?)\s*元\s*[/／]?\s*套',
    ]
    for pattern in price_patterns:
        match = re.search(pattern, raw_content)
        if match:
            return match.group(1)
    return ""


def calc_settlement(parsed: dict, col_data: dict, product_type: str = "hotel") -> str:
    """
    计算结算价和利润率。

    酒店产品：
        基础结算价 = 直播价 - 深度返佣
        基础利润率 = 深度返佣 / 直播价 - 8%

    门票产品：
        基础结算价 = 直播价 - 深度返佣
        基础利润率 = 深度返佣 / 直播价 - 5%

    如果加收列有加收金额，额外计算加收后的结算价和利润率：
        加收结算价 = (直播价 + 加收金额) - 深度返佣
        加收利润率 = 深度返佣 / (直播价 + 加收金额) - 平台成本

    返回格式：结算价XXX；利润率XX%（加收说明结算价XXX；利润率XX%）
    """
    live_price = col_data.get("直播价", "")
    rebate = parsed.get("返佣", "")
    surcharge = col_data.get("加收", "")

    if not live_price or not rebate:
        return ""

    # 提取直播价数字
    try:
        live_num = float(re.sub(r'[^0-9.]', '', live_price))
    except ValueError:
        return ""

    # 提取深度返佣数字
    deep_rebate = 0
    deep_match = re.search(r'深返(\d+(?:\.\d+)?)', rebate)
    if deep_match:
        deep_rebate = float(deep_match.group(1))

    if live_num <= 0 or deep_rebate <= 0:
        return ""

    # 根据产品类型选择平台成本
    platform_cost = 0.08 if product_type == "hotel" else 0.05

    # 基础结算
    settlement = live_num - deep_rebate
    profit_rate = (deep_rebate / live_num) - platform_cost
    profit_pct = profit_rate * 100
    result = f"结算价{int(settlement)}；利润率{profit_pct:.1f}%"

    # 解析加收信息，提取加收金额
    surcharge_amounts = re.findall(r'加收(\d+(?:\.\d+)?)', surcharge)
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
            result += "（" + "；".join(extra_parts) + "）"

    return result


def prompt_col_data(parsed: dict, extracted_content: str) -> dict:
    """交互式提示用户填写各列数据。"""
    col_data = {}

    # 自动填入
    col_data["填写日期"] = date.today().isoformat()
    col_data["产品类型"] = ""  # 交互式填写
    col_data["产品信息"] = parsed.get("原始输入", "")
    col_data["套票内容"] = extract_content_items(extracted_content)
    col_data["结算价格&利润率（已除平台）"] = ""  # 留空，后续计算
    col_data["备注"] = ""  # 留空

    # 套票名称从标题自动生成
    title = parsed.get("标题行", "")
    default_taopiao = generate_taopiao(title, extracted_content)

    # 从标题行提取酒店名称作为默认值
    hotel_match = re.search(r'【(.+?)】', title)
    default_hotel = hotel_match.group(1) if hotel_match else ""

    # 需要用户填写的字段
    prompt_fields = [
        ("产品类型", ""),
        ("酒店/景区", default_hotel),
        ("套票名称", default_taopiao),
        ("加收", ""),
        ("其他加收", ""),
        ("售卖时间", ""),
        ("可用日期", ""),
        ("对外价格", ""),
        ("直播价", ""),
        ("海报", ""),
    ]

    print("\n请填写以下信息（直接回车使用默认值或跳过）：")
    print("-" * 40)
    for field, default in prompt_fields:
        prompt_text = f"{field}"
        if default:
            prompt_text += f" [{default}]"
        prompt_text += ": "
        value = input(prompt_text).strip()
        if value:
            col_data[field] = value
        elif default:
            col_data[field] = default

    # 自动计算结算价格&利润率
    settlement = calc_settlement(parsed, col_data)
    if settlement:
        col_data["结算价格&利润率（已除平台）"] = settlement
        print(f"\n📊 自动计算：{settlement}")
    else:
        col_data["结算价格&利润率（已除平台）"] = ""

    return col_data


def cmd_interactive() -> None:
    """交互模式：粘贴原始输入 → 提取秀米 → 填写各列 → 写入飞书。"""
    print("请粘贴原始输入内容（包含秀米链接等信息），然后按 Ctrl+D（或输入空行后按两次回车）结束：")
    print("-" * 40)

    lines = []
    try:
        while True:
            line = input()
            lines.append(line)
    except EOFError:
        pass

    raw_text = "\n".join(lines).strip()
    if not raw_text:
        print("❌ 未输入任何内容")
        sys.exit(1)

    print("\n" + "=" * 50)
    print("解析输入...")
    print("=" * 50)

    # 解析原始输入
    parsed = parse_raw_input(raw_text)
    parsed["原始输入"] = raw_text

    print(f"标题: {parsed.get('标题行', '')}")
    print(f"编号: {parsed.get('编号', '')}")
    print(f"秀米链接: {parsed.get('xiumius_url', '未找到')}")
    print(f"返佣: {parsed.get('返佣', '')}")
    print(f"下单编号: {parsed.get('下单编号', '')}")

    # 提取秀米内容
    xiumius_url = parsed.get("xiumius_url")
    if xiumius_url:
        content = cmd_extract(xiumius_url)
    else:
        print("\n⚠️  未找到秀米链接，无法自动提取套餐内容")
        content = ""

    # 检查 lark-cli
    if not check_lark_cli():
        print("\n⚠️  lark-cli 未安装或不可用，无法写入飞书表格。")
        sys.exit(1)

    # 检查配置
    try:
        config = load_config()
        token = config.get("lark", {}).get("spreadsheet_token", "")
        if not token:
            print("\n⚠️  飞书表格 token 未配置。")
            sys.exit(1)
    except FileNotFoundError:
        print("\n⚠️  配置文件 config.yaml 不存在。")
        sys.exit(1)

    # 交互填写各列
    col_data = prompt_col_data(parsed, content)

    # 写入飞书
    print("\n正在写入飞书表格...")
    try:
        result = append_row_by_cols(col_data)
        print(f"✅ 写入成功！")
    except Exception as e:
        print(f"❌ 写入失败: {e}")
        sys.exit(1)


def cmd_check() -> None:
    """检查 lark-cli 是否可用。"""
    if check_lark_cli():
        print("✅ lark-cli 可用")
    else:
        print("❌ lark-cli 未安装或不可用")


def cmd_poster(image_path: str) -> None:
    """
    海报模式：识别海报图片 → AI 解析 → 直接写入飞书。
    """
    print(f"正在识别海报: {image_path}")
    try:
        text = ocr_image(image_path)
    except Exception as e:
        print(f"❌ OCR 识别失败: {e}")
        sys.exit(1)

    if not text.strip():
        print("❌ OCR 未识别到文字内容")
        sys.exit(1)

    # 全 AI 解析（海报默认门票产品）
    print("🤖 AI 正在解析海报内容...")
    parsed = ai_extract_all(title="", raw_content=text, product_type="ticket")

    # 检查 lark-cli
    if not check_lark_cli():
        print("⚠️  lark-cli 未安装或不可用，无法写入飞书表格。")
        sys.exit(1)

    # 检查配置
    try:
        config = load_config()
        token = config.get("lark", {}).get("spreadsheet_token", "")
        if not token:
            print("⚠️  飞书表格 token 未配置。")
            sys.exit(1)
    except FileNotFoundError:
        print("⚠️  配置文件 config.yaml 不存在。")
        sys.exit(1)

    # 构建写入数据
    col_data = {
        "填写日期": date.today().isoformat(),
        "产品类型": "",
        "酒店/景区": parsed["hotel_name"],
        "产品信息": text,
        "套票名称": parsed["taopiao"],
        "套票内容": parsed["content_items"],
        "加收": parsed["surcharge"],
        "其他加收": parsed["other_surcharge"],
        "售卖时间": parsed["sell_time"],
        "可用日期": parsed["available_date"],
        "对外价格": "",
        "直播价": parsed["price"],
        "结算价格&利润率（已除平台）": "",
        "备注": "",
        "海报": "",
    }

    # 自动计算结算价
    parsed_for_calc = {"返佣": ""}
    settlement = calc_settlement(parsed_for_calc, col_data)
    if settlement:
        col_data["结算价格&利润率（已除平台）"] = settlement

    # 写入飞书
    print("正在写入飞书表格...")
    try:
        result = append_row_by_cols(col_data)
        print(f"✅ 写入成功！")
    except Exception as e:
        print(f"❌ 写入失败: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="套餐内容提取工具 - 提取秀米内容并写入飞书表格",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default=None,
        help="extract <url> 仅提取, poster <图片路径> 海报识别, check 检查环境, 留空进入交互模式",
    )
    parser.add_argument(
        "url_or_path",
        nargs="?",
        default=None,
        help="秀米页面链接 或 海报图片路径",
    )

    args = parser.parse_args()

    if args.command is None:
        cmd_interactive()
        sys.exit(0)

    if args.command == "check":
        cmd_check()
        sys.exit(0)

    if args.command == "extract":
        if not args.url_or_path:
            print("错误: extract 命令需要提供 URL")
            print("用法: python src/main.py extract <url>")
            sys.exit(1)
        cmd_extract(args.url_or_path)
        sys.exit(0)

    if args.command == "poster":
        if not args.url_or_path:
            print("错误: poster 命令需要提供图片路径")
            print("用法: python src/main.py poster <图片路径>")
            sys.exit(1)
        cmd_poster(args.url_or_path)
        sys.exit(0)

    print(f"错误: 未知命令 '{args.command}'")
    print("用法: python src/main.py                    # 交互模式")
    print("      python src/main.py extract <url>       # 仅提取秀米内容")
    print("      python src/main.py poster <图片路径>    # 海报识别并写入")
    print("      python src/main.py check               # 检查环境")
    sys.exit(1)


if __name__ == "__main__":
    main()
