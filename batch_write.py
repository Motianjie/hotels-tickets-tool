#!/usr/bin/env python3
"""
批量写入脚本 - 从文案和海报中提取套餐信息，写入飞书表格。

18列全部填写（A-R）：
A填写日期 B景区 C渠道 D可上架渠道 E套票名称 F内容 G加收
H售卖时间 I可用日期 J对外价格 K直播价 L结算价格&利润率
M备注 N抖音商品id O抖音链接 P海报 Q(空) R(空)

用法：
    python3 batch_write.py                    # 交互模式
    python3 batch_write.py --row 3            # 从第3行开始写入
"""

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter
import pytesseract
import yaml


# ============================================================
# 配置
# ============================================================
CONFIG_PATH = Path(__file__).parent / "config.yaml"
SPREADSHEET_TOKEN = "N6ipsPUPhhjYout9l12cyxT0nIb"
SHEET_TITLE = "KksHes"

# 18列定义（A-R）
COLUMNS = [
    "填写日期",        # A
    "景区",            # B
    "渠道",            # C
    "可上架渠道",      # D
    "套票名称",        # E
    "内容",            # F
    "加收",            # G
    "售卖时间",        # H
    "可用日期",        # I
    "对外价格",        # J
    "直播价",          # K
    "结算价格&利润率",  # L
    "备注",            # M
    "抖音商品id",      # N
    "抖音链接",        # O
    "海报",            # P
    "",                # Q
    "",                # R
]


# ============================================================
# OCR 识别
# ============================================================
def ocr_image(image_path: str) -> str:
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"图片不存在: {image_path}")
    img = Image.open(path)
    w, h = img.size
    img = img.resize((w * 3, h * 3), Image.LANCZOS)
    img = img.convert("L")
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.5)
    img = img.filter(ImageFilter.SHARPEN)
    img = img.point(lambda x: 0 if x < 140 else 255)
    text = pytesseract.image_to_string(img, lang="chi_sim+eng", config="--psm 4")
    return text.strip()


def parse_poster_info(text: str) -> dict:
    info = {
        "scenic_name": "",
        "available_date": "",
        "sell_time": "",
        "surcharge": "",
        "notes": [],
    }
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # 景区名
    for line in lines:
        if "PLUS" in line or "欢乐海岸" in line:
            clean = re.sub(r'[^\u4e00-\u9fff\w\s·～~]', '', line).strip()
            if clean and ("欢乐海岸" in clean or "华侨城" in clean):
                info["scenic_name"] = "顺德华侨城欢乐海岸PLUS"
                break
    if not info["scenic_name"]:
        info["scenic_name"] = "顺德华侨城欢乐海岸PLUS"

    # 可用日期
    date_match = re.search(r'适用日期[：:]\s*(.+?)(?:\n|$)', text)
    if date_match:
        info["available_date"] = date_match.group(1).strip()
    else:
        date_match = re.search(r'(\d{4}年\d{1,2}月\d{1,2}日)', text)
        if date_match:
            info["available_date"] = f"购票次日起-{date_match.group(1)}"

    # 售卖时间
    sell_match = re.search(r'(?:售卖|抢购)[时间]*[：:]\s*(.+?)(?:\n|$)', text)
    if sell_match:
        info["sell_time"] = sell_match.group(1).strip()

    # 加收
    surcharge_lines = []
    for line in lines:
        if "加收" in line or "加价" in line:
            surcharge_lines.append(line)
    if surcharge_lines:
        info["surcharge"] = "；".join(surcharge_lines)

    # 备注
    for line in lines:
        if "限购" in line or "身份证" in line or "手机号" in line or "提前" in line:
            clean = re.sub(r'[^\u4e00-\u9fff\w\s，。！!、：:（）()]', '', line).strip()
            clean = clean.replace("1!", "!").replace("1|", "|").replace("1张1!", "1张!").replace("1张1", "1张")
            if clean:
                info["notes"].append(clean)
        if "开放时间" in line or "营业时间" in line:
            clean = re.sub(r'[^\u4e00-\u9fff\w\s，。！!、：:（）()\[\]~]', '', line).strip()
            clean = clean.replace(" ]", "]").replace("[ ", "[").replace("10:0022:00", "10:00~22:00")
            if clean:
                info["notes"].append(clean)

    return info


# ============================================================
# 文案解析
# ============================================================
def parse_packages(text: str) -> list:
    """解析文案中的多个套餐。"""
    order_ids = re.findall(r'编号(\d+)', text)
    pattern = r'(\d+)返(\d+)'
    matches = list(re.finditer(pattern, text))

    segments = []
    for i in range(len(matches)):
        if i == 0:
            start = 0
        else:
            prev_end = matches[i-1].end()
            id_match = re.search(r'/编号\d+', text[prev_end:])
            start = prev_end + id_match.end() if id_match else prev_end
        end = matches[i].end()
        segment = text[start:end].strip().lstrip('/').lstrip('[强]')
        segments.append(segment)

    packages = []
    for i, seg in enumerate(segments):
        m = re.search(r'(\d+)返(\d+)', seg)
        if not m:
            continue
        price = m.group(1)
        rebate = m.group(2)
        name = seg[:m.start()].strip()
        name = re.sub(r'^[~/\\[\]强\s]+', '', name).strip()
        order_id = order_ids[i] if i < len(order_ids) else ""
        packages.append({"name": name, "price": price, "rebate": rebate, "order_id": order_id})

    return packages


# ============================================================
# 计算
# ============================================================
def calc_retail_price(live_price: float) -> int:
    return int(live_price / 0.7)


def calc_settlement(live_price: float, deep_rebate: float) -> str:
    settlement = live_price - deep_rebate
    profit_rate = (deep_rebate / live_price) - 0.05
    profit_pct = profit_rate * 100
    return f"结算价{int(settlement)}；利润率{profit_pct:.1f}%"


# ============================================================
# 飞书写入（支持指定行号）
# ============================================================
def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_sheet_id(spreadsheet_token: str, sheet_title: str) -> str:
    cmd = [
        "lark-cli", "sheets", "+info",
        "--spreadsheet-token", spreadsheet_token,
        "--as", "bot",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"lark-cli 失败: {result.stderr}")
    data = json.loads(result.stdout)
    sheets = data.get("data", {}).get("sheets", {}).get("sheets", [])
    for sheet in sheets:
        if sheet.get("title") == sheet_title or sheet.get("sheet_id") == sheet_title:
            return sheet["sheet_id"]
    raise ValueError(f"未找到工作表 '{sheet_title}'")


def write_row_at(col_data: dict, spreadsheet_token: str, sheet_id: str, row_num: int):
    """
    写入指定行号。
    row_num: 从1开始的行号
    """
    # 构建18列数据
    row = []
    for col_name in COLUMNS:
        row.append(col_data.get(col_name, ""))

    range_str = f"A{row_num}:R{row_num}"
    values_json = json.dumps([row], ensure_ascii=False)

    cmd = [
        "lark-cli", "sheets", "+write",
        "--spreadsheet-token", spreadsheet_token,
        "--sheet-id", sheet_id,
        "--range", range_str,
        "--values", values_json,
        "--as", "bot",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"写入失败: {result.stderr}")
    return json.loads(result.stdout)


# ============================================================
# 主流程
# ============================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="批量写入飞书表格")
    parser.add_argument("--row", type=int, default=0, help="起始行号（从1开始），默认追加")
    args = parser.parse_args()

    print("=" * 60)
    print("批量写入飞书表格")
    print("=" * 60)

    # 1. OCR 识别海报
    print("\n[1/4] OCR 识别海报 test.jpg ...")
    image_path = Path(__file__).parent / "test.jpg"
    ocr_text = ocr_image(str(image_path))
    poster_info = parse_poster_info(ocr_text)

    print(f"  景区: {poster_info['scenic_name']}")
    print(f"  可用日期: {poster_info['available_date']}")
    print(f"  加收: {poster_info['surcharge'] or '(无)'}")
    print(f"  备注: {'; '.join(poster_info['notes']) or '(无)'}")

    # 2. 解析文案
    print("\n[2/4] 解析文案中的套餐 ...")
    raw_text = "顺德华侨城欢乐海岸PLUS~单次摩天轮双人票159返14/编号4167387[强]欢乐时光1大1小亲子畅享票（单次摩天轮+机动游戏畅享票）189返15/编号41673849"
    packages = parse_packages(raw_text)

    for i, pkg in enumerate(packages):
        print(f"  套餐{i+1}: {pkg['name']} | 直播价={pkg['price']} | 深返={pkg['rebate']} | 编号={pkg['order_id']}")

    if len(packages) != 2:
        print(f"⚠️  解析到 {len(packages)} 个套餐，期望 2 个")
        sys.exit(1)

    # 3. 构建数据（18列）
    print("\n[3/4] 构建数据 ...")

    today = date.today().isoformat()
    scenic_name = "顺德华侨城欢乐海岸PLUS"
    available_date = poster_info["available_date"] or "购票次日起-2026年5月30日"
    sell_time = poster_info["sell_time"] or available_date
    surcharge = poster_info["surcharge"] or ""
    notes_text = "；".join(poster_info["notes"]) if poster_info["notes"] else ""

    rows_data = []
    for pkg in packages:
        live_price = float(pkg["price"])
        deep_rebate = float(pkg["rebate"])
        retail_price = calc_retail_price(live_price)
        settlement = calc_settlement(live_price, deep_rebate)

        # 内容
        if "双人票" in pkg["name"] and "摩天轮" in pkg["name"]:
            content = "单次摩天轮双人票（不含机动游戏）"
        elif "欢乐时光" in pkg["name"] or "亲子" in pkg["name"] or "畅享票" in pkg["name"]:
            content = "欢乐时光1大1小亲子畅享票（单次摩天轮+机动游戏畅享票）"
        else:
            content = pkg["name"]

        # 备注
        remark = f"下单编号：{pkg['order_id']}"
        if notes_text:
            remark += f"；{notes_text}"

        row = {
            "填写日期": today,
            "景区": scenic_name,
            "渠道": "",
            "可上架渠道": "",
            "套票名称": pkg["name"],
            "内容": content,
            "加收": surcharge,
            "售卖时间": sell_time,
            "可用日期": available_date,
            "对外价格": str(retail_price),
            "直播价": pkg["price"],
            "结算价格&利润率": settlement,
            "备注": remark,
            "抖音商品id": "",
            "抖音链接": "",
            "海报": "",
            "": "",
        }
        rows_data.append(row)

    # 展示数据
    print("\n" + "-" * 60)
    for i, row in enumerate(rows_data):
        print(f"\n📋 行{i+1}:")
        for col in COLUMNS:
            val = row.get(col, "")
            label = col if col else "(空列)"
            print(f"  {label}: {val}")
    print("\n" + "-" * 60)

    # 4. 确认后写入
    print("\n[4/4] 准备写入飞书表格 ...")
    confirm = input("\n确认写入以上数据？(y/N): ").strip().lower()
    if confirm != "y":
        print("❌ 已取消")
        sys.exit(0)

    # 获取 sheet_id
    print("  获取 sheet_id ...")
    sheet_id = get_sheet_id(SPREADSHEET_TOKEN, SHEET_TITLE)

    # 确定起始行号
    start_row = args.row
    if start_row == 0:
        # 自动获取当前行数
        try:
            result = subprocess.run([
                "lark-cli", "sheets", "+read",
                "--spreadsheet-token", SPREADSHEET_TOKEN,
                "--sheet-id", sheet_id,
                "--range", "A:A",
                "--as", "bot",
            ], capture_output=True, text=True, timeout=60)
            data = json.loads(result.stdout)
            values = data.get("data", {}).get("valueRange", {}).get("values", [])
            start_row = len(values) + 1
        except Exception:
            start_row = 3  # 默认从第3行开始
    print(f"  起始行号: {start_row}")

    # 逐行写入
    for i, row in enumerate(rows_data):
        row_num = start_row + i
        print(f"  写入第{row_num}行 ...")
        try:
            result = write_row_at(row, SPREADSHEET_TOKEN, sheet_id, row_num)
            print(f"  ✅ 第{row_num}行 写入成功")
        except Exception as e:
            print(f"  ❌ 第{row_num}行 写入失败: {e}")
            sys.exit(1)

    print("\n✅ 全部写入完成！")


if __name__ == "__main__":
    main()
