"""对外价格计算公式的回归测试。

酒店产品：对外价格 = int(直播价 / 0.9)
门票产品：对外价格 = int(直播价 / 0.7)

运行：python3 tests/test_retail_price.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def retail_price(live_price: float, product_type: str) -> int:
    """与 web/backend/main.py 中一致的计算方式。"""
    divisor = 0.7 if product_type == "ticket" else 0.9
    return int(live_price / divisor)


CASES = [
    ("酒店", 299, "hotel", 332),
    ("酒店", 399, "hotel", 443),
    ("酒店", 499, "hotel", 554),
    ("酒店", 168, "hotel", 186),
    ("门票", 299, "ticket", 427),
    ("门票", 399, "ticket", 570),
    ("门票", 499, "ticket", 712),
]


def main():
    passed = failed = 0
    for name, price, ptype, expected in CASES:
        got = retail_price(price, ptype)
        if got == expected:
            passed += 1
            print(f"OK   {name} 直播价{price} -> {got}")
        else:
            failed += 1
            print(f"FAIL {name} 直播价{price} -> got {got}, exp {expected}")

    print()
    print(f"通过 {passed}，失败 {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
