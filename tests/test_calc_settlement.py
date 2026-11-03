"""calc_settlement 加收金额解析的回归测试。

重点覆盖：加收列中的"不加收"不应被当成加收金额，
以及跨行文本（如多时间段加收规则）不应把下一行的月份数字误认为加收金额。

运行：python3 tests/test_calc_settlement.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.main import calc_settlement


def settle(live_price, rebate, surcharge, product_type="hotel"):
    return calc_settlement(
        {"返佣": rebate},
        {"直播价": live_price, "加收": surcharge},
        product_type=product_type,
    )


CASES = [
    (
        "跨行加收规则：'不加收' 下一行的月份不能当成加收金额",
        "499元/套",
        "深返80",
        "9月，平日、周末不加收\n8月，平日不加收，周五+150元/间，周六+250元/间\n中秋节：9月25-26日+350元/间",
        "hotel",
        "结算价419；利润率8.0%（加收150结算价569；利润率4.3%；加收250结算价669；利润率2.7%；加收350结算价769；利润率1.4%）",
    ),
    (
        "加收N元/间",
        "168",
        "深返45",
        "周六加收70元/间",
        "hotel",
        "结算价123；利润率18.8%（加收70结算价193；利润率10.9%）",
    ),
    (
        "加收：100元",
        "399",
        "深返40",
        "周六加收：100元",
        "hotel",
        "结算价359；利润率2.0%（加收100结算价459；利润率0.0%）",
    ),
    (
        "加收 100（空格）",
        "399",
        "深返40",
        "周六加收 100",
        "hotel",
        "结算价359；利润率2.0%（加收100结算价459；利润率0.0%）",
    ),
    (
        "多种加收",
        "218",
        "深返35",
        "周六加收30元/间；端午加收50元/间",
        "hotel",
        "结算价183；利润率8.1%（加收30结算价213；利润率6.1%；加收50结算价233；利润率5.1%）",
    ),
    (
        "不加价声明（非金额加收）",
        "299",
        "深返50",
        "周末/节假日不加价",
        "hotel",
        "结算价249；利润率8.7%",
    ),
    (
        "无加收",
        "499",
        "深返80",
        "",
        "hotel",
        "结算价419；利润率8.0%",
    ),
    (
        "门票平台成本 5%",
        "100",
        "深返20",
        "",
        "ticket",
        "结算价80；利润率15.0%",
    ),
]


def main():
    passed = failed = 0
    for name, price, rebate, surcharge, ptype, expected in CASES:
        got = settle(price, rebate, surcharge, ptype)
        if got == expected:
            passed += 1
            print(f"OK   {name}")
        else:
            failed += 1
            print(f"FAIL {name}")
            print(f"     got: {got}")
            print(f"     exp: {expected}")

    print()
    print(f"通过 {passed}，失败 {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
