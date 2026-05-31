"""
海报图片 OCR 识别模块。

使用 tesseract OCR 从海报图片中提取文字内容。
支持本地图片文件路径。
"""

import re
from pathlib import Path

try:
    from PIL import Image
    import pytesseract
except ImportError:
    Image = None
    pytesseract = None


def ocr_image(image_path: str, lang: str = "chi_sim+eng") -> str:
    """
    对图片进行 OCR 识别，返回提取的文本。

    Args:
        image_path: 图片文件路径
        lang: OCR 语言，默认简体中文+英文

    Returns:
        识别出的文本内容
    """
    if Image is None or pytesseract is None:
        raise ImportError("请先安装依赖: pip3 install pytesseract Pillow")

    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"图片文件不存在: {image_path}")

    # 支持的图片格式
    valid_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif'}
    if path.suffix.lower() not in valid_extensions:
        raise ValueError(f"不支持的图片格式: {path.suffix}，支持的格式: {valid_extensions}")

    img = Image.open(path)

    # 尝试提高 OCR 准确率：转灰度
    if img.mode != 'L':
        img = img.convert('L')

    # OCR 识别
    text = pytesseract.image_to_string(img, lang=lang)

    # 清理文本
    text = text.strip()
    return text


def parse_poster_text(text: str) -> dict:
    """
    从 OCR 识别的海报文本中智能解析各字段。

    返回字典，包含可能识别的字段：
    - title: 标题行
    - price: 价格
    - content_items: 套餐包含项列表
    - surcharge: 加收信息
    - other_surcharge: 其他加收
    - sell_time: 售卖时间
    - available_date: 可用日期
    - hotel_name: 酒店名称
    - raw_text: 原始 OCR 文本
    """
    result = {
        "title": "",
        "price": "",
        "content_items": "",
        "surcharge": "",
        "other_surcharge": "",
        "sell_time": "",
        "available_date": "",
        "hotel_name": "",
        "raw_text": text,
    }

    lines = text.split("\n")
    non_empty_lines = [l.strip() for l in lines if l.strip()]

    # 1. 提取价格
    price_patterns = [
        r'[¥￥](\d+(?:\.\d+)?)\s*元',
        r'(\d+(?:\.\d+)?)\s*元\s*[/／]?\s*套',
        r'仅[需售]?\s*[¥￥]?(\d+(?:\.\d+)?)',
        r'[¥￥]?(\d+(?:\.\d+)?)\s*元\s*起',
    ]
    for pattern in price_patterns:
        m = re.search(pattern, text)
        if m:
            result["price"] = m.group(1)
            break

    # 2. 提取标题行（通常是最长的行或包含【】的行）
    bracket_lines = [l for l in non_empty_lines if '【' in l and '】' in l]
    if bracket_lines:
        result["title"] = bracket_lines[0]
    else:
        # 找包含酒店/度假/温泉等关键词的行作为标题
        title_keywords = ['酒店', '度假', '温泉', '民宿', '别墅', '公寓']
        for l in non_empty_lines:
            if any(kw in l for kw in title_keywords) and len(l) > 10:
                result["title"] = l
                break

    # 3. 提取酒店名称
    hotel_match = re.search(r'【(.+?)】', result["title"] or text)
    if hotel_match:
        result["hotel_name"] = hotel_match.group(1)
    else:
        # 尝试找酒店名称
        for kw in ['酒店', '度假村', '民宿', '公寓']:
            m = re.search(r'([^，,。！!~\n]+' + kw + ')', text)
            if m:
                result["hotel_name"] = m.group(1).strip()
                break

    # 4. 提取套餐包含项（数字序号列表）
    in_content = False
    content_lines = []
    content_start_keywords = ['套餐包含', '包含', '费用包含', '套餐内容']
    content_end_keywords = ['加收', '温馨提示', '特别声明', '购买须知', '使用说明',
                            '时间说明', '抢购时间', '使用时间', '酒店地址', '交通指南']

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 检测套餐包含标题
        for kw in content_start_keywords:
            if kw in line:
                in_content = True
                break

        if in_content:
            # 检测结束
            should_stop = False
            for kw in content_end_keywords:
                if kw in line:
                    should_stop = True
                    break
            if should_stop:
                in_content = False
                continue

            # 提取数字序号行
            if re.match(r'^(\d+[、.．]|[①②③④⑤⑥⑦⑧⑨⑩])', line):
                content_lines.append(line)
            elif re.match(r'^[①②③④⑤⑥⑦⑧⑨⑩]', line):
                content_lines.append(line)
            elif re.match(r'^[-—]', line):
                content_lines.append(line)
            elif re.match(r'^\*', line):
                content_lines.append(line)

    if content_lines:
        result["content_items"] = "\n".join(content_lines)

    # 5. 提取加收信息
    surcharge_lines = []
    in_surcharge = False
    for line in lines:
        line = line.strip()
        if not line:
            if in_surcharge:
                in_surcharge = False
            continue

        if '加收' in line or '加价' in line:
            in_surcharge = True

        if in_surcharge:
            surcharge_lines.append(line)

    if surcharge_lines:
        result["surcharge"] = "；".join(surcharge_lines)

    # 6. 提取售卖时间和可用日期
    sell_match = re.search(r'(?:抢购|售卖)[时间]*[：:]\s*(.+?)(?:\n|$)', text)
    if sell_match:
        result["sell_time"] = sell_match.group(1).strip()

    date_match = re.search(r'(?:使用|可用|有效)[时间]*[：:]\s*(.+?)(?:\n|$)', text)
    if date_match:
        result["available_date"] = date_match.group(1).strip()

    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python3 poster_ocr.py <图片路径>")
        sys.exit(1)

    text = ocr_image(sys.argv[1])
    print("=" * 50)
    print("OCR 识别结果:")
    print("=" * 50)
    print(text)
    print("=" * 50)

    parsed = parse_poster_text(text)
    print("\n解析结果:")
    for k, v in parsed.items():
        if k != "raw_text":
            print(f"  {k}: {v}")