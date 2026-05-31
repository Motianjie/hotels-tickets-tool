#!/usr/bin/env python3
"""快速写入飞书表格 - 跳过交互式输入，直接使用已提取的数据。"""
import sys
from pathlib import Path
from datetime import date
import re

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from src.lark_writer import append_row_by_cols, check_lark_cli, load_config
from src.extractor import XiumiusExtractor


def extract_content_items(raw_content: str) -> str:
    """从提取的秀米内容中提取套餐包含项。"""
    package_tags = {'住', '食', '餐', '玩', '游', '赠', '享', '免', '泡', '浸', '畅', '行'}
    lines = raw_content.split("\n")
    items = []
    in_package_section = False
    package_end_keywords = ['加收说明', '温馨提示', '特别声明', '时间说明', '抢购时间', '使用时间',
                            '酒店地址', '酒店接待规则', '周边热门游玩', '交通指南']

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if '套餐包含' in line:
            in_package_section = True
            continue
        if in_package_section:
            should_stop = False
            for kw in package_end_keywords:
                if kw in line:
                    should_stop = True
                    break
            if should_stop:
                in_package_section = False
                continue
        if in_package_section:
            if re.match(r'^(\d+[、.．]|[①②③④⑤⑥⑦⑧⑨⑩])', line):
                items.append(line)
            elif re.match(r'^[①②③④⑤⑥⑦⑧⑨⑩]', line):
                items.append(line)
            elif re.match(r'^[-—]', line):
                items.append(line)
            elif re.match(r'^\*', line):
                items.append(line)
            elif re.match(r'^【([^】]+)】', line):
                m = re.match(r'^【([^】]+)】', line)
                if m and m.group(1) in package_tags:
                    items.append(line)
            continue
        m = re.match(r'^【([^】]+)】', line)
        if m:
            tag = m.group(1)
            if tag in package_tags:
                items.append(line)
    return "\n".join(items)


def infer_highlight(title: str) -> str:
    if not title:
        return "主推"
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
    return "主推"


def generate_taopiao(title: str) -> str:
    if not title:
        return ""
    highlight = infer_highlight(title)
    hotel_match = re.search(r'【(.+?)】', title)
    hotel_name = hotel_match.group(1) if hotel_match else ""
    fangxing = ""
    fangxing_keywords = ['池景房', '山景房', '海景房', '温泉房', '别墅房', '池景', '别墅', '海景', '房']
    fangxing_pattern = r'酒店([^，,。！!~]+(?:' + '|'.join(fangxing_keywords) + r'))'
    fangxing_matches = re.findall(fangxing_pattern, title)
    if fangxing_matches:
        fangxing = max(fangxing_matches, key=len).strip()
    if not fangxing:
        fangxing_match = re.search(r'秒杀(.+?(?:房|别墅|海景|温泉|池景|山景))', title)
        if fangxing_match:
            fangxing = fangxing_match.group(1).strip()
    if not fangxing:
        fangxing_match = re.search(r'抢(.+?(?:房|别墅|海景|温泉|池景|山景))', title)
        if fangxing_match:
            fangxing = fangxing_match.group(1).strip()
    if not fangxing:
        fangxing_match = re.search(r'\d+元(.+?(?:房|别墅|海景|温泉|池景|山景))', title)
        if fangxing_match:
            fangxing = fangxing_match.group(1).strip()
    if fangxing:
        fangxing = re.sub(r'[（(][^）)]*[）)]', '', fangxing).strip()
    maidian_parts = re.findall(r'\+([^+]+?)(?=\+|~|！|$)', title)
    maidian_list = []
    for p in maidian_parts:
        p = p.strip()
        if len(p) > 15:
            p = p[:15]
        if p and p not in maidian_list:
            maidian_list.append(p)
        if len(maidian_list) >= 2:
            break
    maidian = "+".join(maidian_list) if maidian_list else ""
    parts = [f"【{highlight}】{hotel_name}"]
    if fangxing:
        parts.append(f"·{fangxing}")
    if maidian:
        parts.append(f"｜{maidian}")
    return "".join(parts)


def calc_settlement(live_price: str, rebate: str, surcharge: str = "") -> str:
    if not live_price or not rebate:
        return ""
    try:
        live_num = float(re.sub(r'[^0-9.]', '', live_price))
    except ValueError:
        return ""
    deep_rebate = 0
    deep_match = re.search(r'深返(\d+(?:\.\d+)?)', rebate)
    if deep_match:
        deep_rebate = float(deep_match.group(1))
    if live_num <= 0 or deep_rebate <= 0:
        return ""
    settlement = live_num - deep_rebate
    profit_rate = (deep_rebate / live_num) - 0.08
    profit_pct = profit_rate * 100
    result = f"结算价{int(settlement)}；利润率{profit_pct:.1f}%"
    surcharge_amounts = re.findall(r'(?:加收|[+])(\d+(?:\.\d+)?)', surcharge)
    if surcharge_amounts:
        extra_parts = []
        for amount_str in surcharge_amounts:
            extra = float(amount_str)
            if extra > 0:
                extra_settlement = (live_num + extra) - deep_rebate
                extra_profit = (deep_rebate / (live_num + extra)) - 0.08
                extra_pct = extra_profit * 100
                extra_parts.append(f"加收{int(extra)}结算价{int(extra_settlement)}；利润率{extra_pct:.1f}%")
        if extra_parts:
            result += "（" + "；".join(extra_parts) + "）"
    return result


def main():
    # 原始输入
    raw_text = """【江门·亚洲福永·温泉酒店】亲子戏水玩泡温泉！平日不加收！￥299抢住江门亚洲福永温泉酒店豪华客房+自助早餐+无限次畅泡半山温泉+儿童乐园+熊猫乐园+钓虾体验+生态园门票！打卡梅家大院，台山碉楼群！尽享陈皮园之旅！
🆔编号：39982728
🚗房态：https://s.zowoyoo.com/CmSry76b2
[烟花]抢购：https://s.leleku.cn/bjmJbebhM
🎆秀米：https://v.xiumius.cn/board/v5/5CCoS/676483777
[红包]普返20，深返50"""

    # 解析
    title = "【江门·亚洲福永·温泉酒店】亲子戏水玩泡温泉！平日不加收！￥299抢住江门亚洲福永温泉酒店豪华客房+自助早餐+无限次畅泡半山温泉+儿童乐园+熊猫乐园+钓虾体验+生态园门票！打卡梅家大院，台山碉楼群！尽享陈皮园之旅！"
    order_id = "39982728"
    rebate = "普返20，深返50"

    # 提取秀米内容
    print("正在提取秀米内容...")
    extractor = XiumiusExtractor()
    content = extractor.extract("https://v.xiumius.cn/board/v5/5CCoS/676483777")
    print("提取完成！")

    # 提取套餐包含项
    content_items = """【住】豪华双床/雅致大床房1间1晚
【餐】两大一小双人自助早餐
【泡】两大一小无限次泡半山温泉
【玩】畅玩熊猫亲子乐园
【玩】双人厚承文化生态园门票(免费试食水果1份)
【享】九龙钓虾场 20 元代金券2张"""
    print(f"\n套餐包含项:\n{content_items}")

    # 生成套票
    taopiao = "【不加收】江门·亚洲福永·温泉酒店·豪华客房｜自助早餐+无限次畅泡半山温泉+儿童乐园"
    print(f"\n套票: {taopiao}")

    # 计算结算（含加收）
    settlement = calc_settlement("299", rebate, "周六加收100元/间/晚")
    print(f"结算: {settlement}")

    # 构建列数据
    col_data = {
        "填写日期": date.today().isoformat(),
        "酒店/景区": "江门·亚洲福永·温泉酒店",
        "渠道": "",
        "可上架渠道": "",
        "产品和房态": raw_text,
        "套票": taopiao,
        "抖音商品id": "",
        "抖音链接": "",
        "内容": content_items,
        "加收": "周六加收100元/间/晚",
        "其他加收": "",
        "售卖时间": "即日起--2026年6月30日",
        "可用日期": "即日起--2026年6月30日",
        "对外价格": "",
        "直播价": "299",
        "结算价格&利润率": settlement,
        "备注": f"下单编号：{order_id}",
        "头图": "",
    }

    # 检查 lark-cli
    if not check_lark_cli():
        print("❌ lark-cli 不可用")
        sys.exit(1)

    # 写入飞书
    print("\n正在写入飞书表格...")
    try:
        result = append_row_by_cols(col_data)
        print(f"✅ 写入成功！")
        print(result)
    except Exception as e:
        print(f"❌ 写入失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
