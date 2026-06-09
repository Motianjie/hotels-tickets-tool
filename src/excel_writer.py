"""
本地 Excel 表格读写模块。

支持新建/打开/追加写入/指定行写入，按标准 15 列表头操作。
与飞书 lark_writer 并行，用户可切换目标。
"""

import os
from pathlib import Path
from typing import Optional

import openpyxl
from openpyxl import Workbook, load_workbook


# 标准表头（15列 A-O）
STANDARD_HEADERS = [
    "填写日期",                    # A
    "产品类型",                    # B
    "酒店/景区",                   # C
    "产品信息",                    # D
    "套票名称",                    # E
    "套票内容",                    # F
    "加收",                        # G
    "其他加收",                    # H
    "售卖时间",                    # I
    "可用日期",                    # J
    "对外价格",                    # K
    "直播价",                      # L
    "结算价格&利润率（已除平台）",  # M
    "备注",                        # N
    "海报",                        # O
]


def create_excel(filepath: str) -> str:
    """
    新建 Excel 文件，写入标准 15 列表头。

    Args:
        filepath: 保存路径（含 .xlsx 后缀）

    Returns:
        文件绝对路径
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    # 写入表头
    for col_idx, header in enumerate(STANDARD_HEADERS, start=1):
        ws.cell(row=1, column=col_idx, value=header)
    wb.save(filepath)
    return os.path.abspath(filepath)


def open_excel(filepath: str) -> list:
    """
    打开已有 Excel 文件，读取第一行作为表头。

    Args:
        filepath: Excel 文件路径

    Returns:
        表头列表（字符串列表）
    """
    wb = load_workbook(filepath, read_only=True)
    ws = wb.active
    headers = []
    for cell in ws[1]:
        headers.append(cell.value or "")
    wb.close()
    return headers


def validate_headers(headers: list) -> None:
    """
    校验表头是否与标准表头完全一致（列数 + 名称 + 顺序）。

    Args:
        headers: 待校验的表头列表

    Raises:
        ValueError: 表头不一致时抛出异常，描述具体差异
    """
    if len(headers) != len(STANDARD_HEADERS):
        raise ValueError(
            f"表头列数不一致：标准 {len(STANDARD_HEADERS)} 列，"
            f"上传文件 {len(headers)} 列"
        )
    for i, (actual, expected) in enumerate(zip(headers, STANDARD_HEADERS)):
        if actual != expected:
            raise ValueError(
                f"表头不一致：第 {i+1} 列应为「{expected}」，"
                f"实际为「{actual}」"
            )


def _get_last_row(filepath: str) -> int:
    """
    获取工作表已有数据的最大行号（含表头）。
    """
    wb = load_workbook(filepath)
    ws = wb.active
    last_row = ws.max_row
    wb.close()
    return last_row


def append_row(filepath: str, col_data: dict) -> int:
    """
    按列名向 Excel 追加一行（自动追加到最后一行之后）。

    Args:
        filepath: Excel 文件路径
        col_data: 字典，key 为列名，value 为内容

    Returns:
        写入的行号（1-based）
    """
    wb = load_workbook(filepath)
    ws = wb.active

    # 读取表头
    headers = []
    for cell in ws[1]:
        headers.append(cell.value or "")

    # 获取当前最后一行行号
    last_row = ws.max_row
    target_row = last_row + 1

    # 按表头顺序构建行数据
    for col_idx, col_name in enumerate(headers, start=1):
        value = col_data.get(col_name, "")
        ws.cell(row=target_row, column=col_idx, value=value)

    wb.save(filepath)
    return target_row


def write_row(filepath: str, col_data: dict, row_number: int) -> None:
    """
    写入指定行号。

    Args:
        filepath: Excel 文件路径
        col_data: 字典，key 为列名，value 为内容
        row_number: 目标行号（1-based）
    """
    wb = load_workbook(filepath)
    ws = wb.active

    # 读取表头
    headers = []
    for cell in ws[1]:
        headers.append(cell.value or "")

    # 按表头顺序写入指定行
    for col_idx, col_name in enumerate(headers, start=1):
        value = col_data.get(col_name, "")
        ws.cell(row=row_number, column=col_idx, value=value)

    wb.save(filepath)
