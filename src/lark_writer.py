"""
飞书表格写入模块。

通过飞书开放平台 HTTP API 操作飞书在线表格。
支持按列名写入结构化数据。

历史说明：早期版本通过 lark-cli 命令行工具写入，依赖服务器上的单人登录态，
无法支持多人并发使用，已改为企业自建应用 + tenant_access_token 的 API 方式。
"""

import json
import yaml
from pathlib import Path
from typing import Optional

from src import lark_client


# 表格列定义（兜底用）。
# 线上表格的实际表头以读取结果为准；此常量仅在读取表头失败时作为兜底。
# 顺序与线上真实表格（15 列 A-O）保持一致。
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
    "结算价格&利润率",            # M
    "供应商信息",                  # N
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


def get_sheet_id(spreadsheet_token: str, sheet_title: str = "Sheet1") -> str:
    """按工作表标题获取 sheet_id。"""
    return lark_client.find_sheet_id(spreadsheet_token, sheet_title)


def _get_header_row(spreadsheet_token: str, sheet_id: str) -> list:
    """读取表格第一行（表头），返回列名列表。"""
    values = lark_client.read_values(spreadsheet_token, sheet_id, "A1:Z1")
    if values:
        return [v if v is not None else "" for v in values[0]]
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
        JSON 字符串，包含写入结果与 _row_number（写入的起始行号）。
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
        headers = COLUMNS          # 兜底：静态常量
    else:
        headers = [h for h in headers if h]   # 过滤空列名

    # 按表头顺序构建一行数据
    row = [col_data.get(col_name, "") for col_name in headers]

    result = lark_client.append_rows(spreadsheet_token, sheet_id, [row])
    result["_row_number"] = result.get("row_number", 0)
    return json.dumps(result, ensure_ascii=False, indent=2)


def check_lark_cli() -> bool:
    """
    检查飞书写入通道是否可用（保留原名以兼容既有调用）。

    现已改为校验 API 凭证，不再检测 lark-cli 命令。
    """
    return lark_client.check_api().get("available", False)


def check_lark_api() -> dict:
    """检查飞书 API 可用性，返回详细状态。"""
    return lark_client.check_api()
