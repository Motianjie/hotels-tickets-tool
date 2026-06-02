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


# 表格列定义（按顺序，15列 A-O）
# 实际表头：A=填写日期 B=产品类型 C=酒店/景区 D=产品信息 E=套票名称
#           F=套票内容 G=加收 H=其他加收 I=售卖时间 J=可用日期
#           K=对外价格 L=直播价 M=结算价格&利润率（已除平台） N=备注 O=海报
COLUMNS = [
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
    "结算价格&利润率（已除平台）", # M
    "备注",                        # N
    "海报",                        # O
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
        "--as", "user",
    ]
    data = _run_lark_cli(cmd)
    sheets = data.get("data", {}).get("sheets", {}).get("sheets", [])
    for sheet in sheets:
        if sheet.get("title") == sheet_title:
            return sheet["sheet_id"]
    raise ValueError(f"未找到工作表 '{sheet_title}'")


def append_row_by_cols(col_data: dict,
                       spreadsheet_token: Optional[str] = None,
                       sheet_title: Optional[str] = None) -> str:
    """
    按列名向飞书表格追加一行。

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

    # 按 COLUMNS 顺序构建一行数据
    row = []
    for col_name in COLUMNS:
        row.append(col_data.get(col_name, ""))

    values_json = json.dumps([row], ensure_ascii=False)

    cmd = [
        "lark-cli", "sheets", "+append",
        "--spreadsheet-token", spreadsheet_token,
        "--sheet-id", sheet_id,
        "--values", values_json,
        "--as", "user",
    ]

    data = _run_lark_cli(cmd)
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
