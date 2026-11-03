"""
飞书表格写入模块。

通过 lark-cli 命令行工具操作飞书在线表格。
支持按列名写入结构化数据。
"""

import json
import subprocess
import yaml
from pathlib import Path
from typing import Optional


# 表格列定义（按顺序，18列 A-R）
# 实际表头：A=填写日期 B=酒店/景区 C=渠道 D=可上架渠道 E=产品和房态
#           F=套票 G=抖音商品id H=抖音链接 I=内容 J=加收
#           K=其他加收 L=售卖时间 M=可用日期 N=对外价格 O=直播价
#           P=结算价格&利润率 Q=备注 R=头图
COLUMNS = [
    "填写日期",                    # A
    "酒店/景区",                   # B
    "渠道",                        # C
    "可上架渠道",                  # D
    "产品和房态",                  # E
    "套票",                        # F
    "抖音商品id",                  # G
    "抖音链接",                    # H
    "内容",                        # I
    "加收",                        # J
    "其他加收",                    # K
    "售卖时间",                    # L
    "可用日期",                    # M
    "对外价格",                    # N
    "直播价",                      # O
    "结算价格&利润率",            # P
    "备注",                        # Q
    "头图",                        # R
]


def load_config(config_path: str = "config.yaml") -> dict:
    """加载配置文件。"""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_spreadsheet_token(config: Optional[dict] = None) -> str:
    """获取飞书电子表格 token。"""
    if config is None:
        config = load_config()
    token = config.get("lark", {}).get("spreadsheet_token", "")
    if not token:
        raise ValueError(
            "飞书表格 token 未配置，请在 config.yaml 中设置 lark.spreadsheet_token"
        )
    return token


def get_sheet_name(config: Optional[dict] = None) -> str:
    """获取工作表名称（标题）。"""
    if config is None:
        config = load_config()
    return config.get("lark", {}).get("sheet_name", "Sheet1")


def _run_lark_cli(cmd: list, timeout: int = 60) -> dict:
    """执行 lark-cli 命令并返回解析后的 JSON。"""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        raise FileNotFoundError("未找到 lark-cli 命令，请先安装飞书 CLI 工具")
    except subprocess.TimeoutExpired:
        raise RuntimeError("lark-cli 命令执行超时")

    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"lark-cli 执行失败: {error_msg}")

    return json.loads(result.stdout)


def get_sheet_id(spreadsheet_token: str, sheet_title: str = "Sheet1") -> str:
    """
    获取工作表的 ID。

    通过 lark-cli sheets +info 获取表格信息，根据 sheet 标题查找对应的 sheet_id。
    """
    cmd = [
        "lark-cli", "sheets", "+info",
        "--spreadsheet-token", spreadsheet_token,
        "--as", "bot",
    ]
    data = _run_lark_cli(cmd)
    sheets = data.get("data", {}).get("sheets", {}).get("sheets", [])
    for sheet in sheets:
        if sheet.get("title") == sheet_title:
            return sheet["sheet_id"]
    raise ValueError(f"未找到工作表 '{sheet_title}'")


def _get_last_row(spreadsheet_token: str, sheet_id: str) -> int:
    """
    获取工作表已有数据的最大行号（含表头）。
    通过读取 A 列所有非空单元格来确定。
    """
    cmd = [
        "lark-cli", "sheets", "+read",
        "--spreadsheet-token", spreadsheet_token,
        "--sheet-id", sheet_id,
        "--range", f"{sheet_id}!A:A",
        "--as", "bot",
    ]
    try:
        data = _run_lark_cli(cmd)
        values = data.get("data", {}).get("valueRange", {}).get("values", [])
        return len(values)  # 行数 = 最后非空行号
    except Exception:
        return 0  # 读不到就从头开始


def _get_header_row(spreadsheet_token: str, sheet_id: str) -> list:
    """读取表格第一行（表头），返回列名列表。"""
    cmd = [
        "lark-cli", "sheets", "+read",
        "--spreadsheet-token", spreadsheet_token,
        "--range", f"{sheet_id}!A1:Z1",
        "--as", "bot",
    ]
    result = _run_lark_cli(cmd)
    values = result.get("data", {}).get("valueRange", {}).get("values", [])
    if values:
        return [v or "" for v in values[0]]
    return []


def append_row_by_cols(col_data: dict,
                       spreadsheet_token: Optional[str] = None,
                       sheet_title: Optional[str] = None) -> str:
    """
    按列名向飞书表格追加一行（自动追加到最后一行之后，不会覆盖已有内容）。

    自动读取表格第一行作为表头，按表头顺序映射列数据。
    未提供的列留空。

    Args:
        col_data: 字典，key 为列名，value 为要写入的内容。
                  例如 {"酒店/景区": "惠州龙门·龙之泉温泉", "内容": "..."}
                  未提供的列留空。
        spreadsheet_token: 飞书电子表格 token，不传则从配置读取。
        sheet_title: 工作表标题，不传则从配置读取。

    Returns:
        lark-cli 命令的输出。
    """
    if spreadsheet_token is None:
        config = load_config()
        spreadsheet_token = get_spreadsheet_token(config)
        sheet_title = sheet_title or get_sheet_name(config)

    sheet_title = sheet_title or "Sheet1"
    sheet_id = get_sheet_id(spreadsheet_token, sheet_title)

    # 动态读取表头
    headers = _get_header_row(spreadsheet_token, sheet_id)
    if not headers:
        # 兜底：使用静态 COLUMNS
        headers = COLUMNS
    else:
        # 过滤掉空列名，只保留有内容的列
        headers = [h for h in headers if h]

    # 获取当前最后一行行号
    last_row = _get_last_row(spreadsheet_token, sheet_id)
    target_row = last_row + 1  # 从下一行开始写入

    # 按表头顺序构建一行数据
    row = []
    for col_name in headers:
        row.append(col_data.get(col_name, ""))

    values_json = json.dumps([row], ensure_ascii=False)
    range_str = f"{sheet_id}!A{target_row}"

    cmd = [
        "lark-cli", "sheets", "+write",
        "--spreadsheet-token", spreadsheet_token,
        "--sheet-id", sheet_id,
        "--range", range_str,
        "--values", values_json,
        "--as", "user",
    ]

    data = _run_lark_cli(cmd)
    # 在返回结果中注入行号信息
    data["_row_number"] = target_row
    return json.dumps(data, ensure_ascii=False, indent=2)


def check_lark_cli() -> bool:
    """检查 lark-cli 是否可用。"""
    try:
        result = subprocess.run(
            ["which", "lark-cli"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
