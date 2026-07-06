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
from openpyxl.utils import get_column_letter
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side


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

# ── 样式常量 ──────────────────────────────────────────
HEADER_FONT = Font(name="微软雅黑", bold=True, size=11, color="FFFFFF")
HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)

BODY_FONT = Font(name="微软雅黑", size=10)
BODY_ALIGNMENT = Alignment(horizontal="left", vertical="center", wrap_text=True)

THIN_BORDER = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)

# 列宽配置（字符数），按列名匹配
COLUMN_WIDTHS = {
    "填写日期":                    18,
    "产品类型":                    10,
    "酒店/景区":                   30,
    "产品信息":                    50,
    "套票名称":                    50,
    "套票内容":                    50,
    "加收":                        20,
    "其他加收":                    20,
    "售卖时间":                    25,
    "可用日期":                    25,
    "对外价格":                    12,
    "直播价":                      12,
    "结算价格&利润率（已除平台）":  22,
    "备注":                        20,
    "海报":                        10,
}

# 行高
HEADER_ROW_HEIGHT = 30
BODY_ROW_HEIGHT = 30


def _apply_header_style(ws) -> None:
    """给表头行应用样式：蓝底白字、居中、边框、行高。"""
    ws.row_dimensions[1].height = HEADER_ROW_HEIGHT
    for col_idx in range(1, len(STANDARD_HEADERS) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGNMENT
        cell.border = THIN_BORDER


def _apply_column_widths(ws) -> None:
    """按 COLUMN_WIDTHS 配置设置列宽。"""
    for col_idx, header in enumerate(STANDARD_HEADERS, start=1):
        width = COLUMN_WIDTHS.get(header, 15)
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = width


def _apply_body_style(ws, row_number: int) -> None:
    """给数据行应用样式：自动换行、垂直居中、边框。"""
    for col_idx in range(1, len(STANDARD_HEADERS) + 1):
        cell = ws.cell(row=row_number, column=col_idx)
        cell.font = BODY_FONT
        cell.alignment = BODY_ALIGNMENT
        cell.border = THIN_BORDER


def _auto_row_height(ws, row_number: int) -> None:
    """
    根据【套票内容】列（F列=6）的文本长度估算行高。
    策略：取套票内容文本，按每行约 30 字符换行，每行约 15pt 高度。
    """
    max_lines = 1
    # 只取套票内容列（F列，col_idx=6）
    cell = ws.cell(row=row_number, column=6)
    text = str(cell.value or "")
    if text:
        lines = text.split("\n")
        for line in lines:
            col_width = COLUMN_WIDTHS.get("套票内容", 50)
            char_width = 2  # 中文字符宽度系数
            effective_chars = len(line) * char_width
            wraps = max(1, (effective_chars // (col_width * 1.8)) + 1)
            max_lines = max(max_lines, wraps)

    # 每行文本约 15pt，加上边距
    height = max_lines * 15 + 6
    height = max(30, min(height, 200))
    ws.row_dimensions[row_number].height = height


def create_excel(filepath: str) -> str:
    """
    新建 Excel 文件，写入标准 15 列表头，应用样式。

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
    # 应用样式
    _apply_header_style(ws)
    _apply_column_widths(ws)
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
    按列名向 Excel 追加一行（自动追加到最后一行之后），
    自动应用样式和自适应行高。

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

    # 应用样式和自适应行高
    _apply_body_style(ws, target_row)
    _auto_row_height(ws, target_row)

    wb.save(filepath)
    return target_row


def write_row(filepath: str, col_data: dict, row_number: int) -> None:
    """
    写入指定行号，自动应用样式和自适应行高。

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

    # 应用样式和自适应行高
    _apply_body_style(ws, row_number)
    _auto_row_height(ws, row_number)

    wb.save(filepath)
