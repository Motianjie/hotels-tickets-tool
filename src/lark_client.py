"""
飞书开放平台 HTTP 客户端。

使用企业自建应用的 app_id / app_secret 获取 tenant_access_token，
直接调用飞书电子表格 API，替代原先依赖 lark-cli 的本地命令行方案。

相比 lark-cli 的优势：
- 无状态，多人并发使用时不会互相挤占登录态
- 用户无需登录个人飞书账号
- token 集中管理，自动刷新
"""

import os
import re
import threading
import time
from pathlib import Path

import requests
from openpyxl.utils import get_column_letter


# ── 配置 ──────────────────────────────────────────────
DEFAULT_API_BASE = "https://open.feishu.cn"
TOKEN_REFRESH_MARGIN = 300  # 提前 5 分钟刷新，避免临界过期
REQUEST_TIMEOUT = 60

# 飞书接口有频控限制（HTTP 429 / code 90217），并发写入需要限流 + 指数退避重试
MIN_REQUEST_INTERVAL = 0.35   # 同一进程内两次请求的最小间隔（秒）
MAX_RETRIES = 5               # 频控/限流时的最大重试次数
RATE_LIMIT_CODES = {90217}    # 飞书返回的 "too many request"


class LarkAPIError(RuntimeError):
    """飞书 API 返回非 0 code 时抛出。"""


def _load_dotenv() -> None:
    """把项目根目录 .env 中的键值对注入 os.environ（不覆盖已存在的变量）。"""
    if os.environ.get("LARK_APP_ID") and os.environ.get("LARK_APP_SECRET"):
        return
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _credentials() -> tuple:
    """读取 app_id / app_secret，优先取环境变量。"""
    _load_dotenv()
    app_id = os.environ.get("LARK_APP_ID", "").strip()
    app_secret = os.environ.get("LARK_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise LarkAPIError(
            "缺少飞书应用凭证，请在项目根目录 .env 中配置 "
            "LARK_APP_ID 与 LARK_APP_SECRET"
        )
    return app_id, app_secret


def _api_base() -> str:
    _load_dotenv()
    return os.environ.get("LARK_API_BASE", DEFAULT_API_BASE).rstrip("/")


# ── Token 缓存 ────────────────────────────────────────
_token_lock = threading.Lock()
_token_cache = {"token": "", "expires_at": 0.0}


def get_tenant_token(force_refresh: bool = False) -> str:
    """获取 tenant_access_token，带进程内缓存。"""
    with _token_lock:
        now = time.time()
        if (
            not force_refresh
            and _token_cache["token"]
            and now < _token_cache["expires_at"] - TOKEN_REFRESH_MARGIN
        ):
            return _token_cache["token"]

        app_id, app_secret = _credentials()
        resp = requests.post(
            f"{_api_base()}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise LarkAPIError(
                f"获取 tenant_access_token 失败: {data.get('msg')} (code={data.get('code')})"
            )

        _token_cache["token"] = data["tenant_access_token"]
        _token_cache["expires_at"] = now + int(data.get("expire", 7200))
        return _token_cache["token"]


# ── 请求限流 ──────────────────────────────────────────
_rate_lock = threading.Lock()
_last_request_at = [0.0]


def _throttle() -> None:
    """进程内串行化请求，保证两次请求间隔不低于 MIN_REQUEST_INTERVAL。"""
    with _rate_lock:
        elapsed = time.time() - _last_request_at[0]
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        _last_request_at[0] = time.time()


# ── 底层请求 ──────────────────────────────────────────
def _request(method: str, path: str, **kwargs) -> dict:
    """
    发起飞书 API 请求。

    - 自动携带 tenant_access_token，失效时刷新重试
    - 进程内限流，避免触发飞书频控
    - 遇频控（HTTP 429 / code 90217）指数退避重试
    """
    url = f"{_api_base()}{path}"
    payload = kwargs.pop("json", None)
    params = kwargs.pop("params", None)

    token_refreshed = False
    last_error = ""

    for attempt in range(MAX_RETRIES + 1):
        _throttle()
        headers = {"Authorization": f"Bearer {get_tenant_token(force_refresh=token_refreshed)}"}
        resp = requests.request(
            method,
            url,
            headers=headers,
            json=payload,
            params=params,
            timeout=REQUEST_TIMEOUT,
            **kwargs,
        )

        # HTTP 层频控
        if resp.status_code == 429 or resp.status_code >= 500:
            last_error = f"HTTP {resp.status_code}"
            if attempt < MAX_RETRIES:
                _backoff(attempt, resp.headers.get("Retry-After"))
                continue
            resp.raise_for_status()

        data = resp.json()
        code = data.get("code")

        # token 失效：刷新一次后重试
        if code in (99991663, 99991661) and not token_refreshed:
            token_refreshed = True
            continue

        # 业务层频控：指数退避重试
        if code in RATE_LIMIT_CODES:
            last_error = f"{data.get('msg')} (code={code})"
            if attempt < MAX_RETRIES:
                _backoff(attempt, resp.headers.get("Retry-After"))
                continue

        if code != 0:
            raise LarkAPIError(
                f"飞书 API 调用失败 [{method} {path}]: "
                f"{data.get('msg')} (code={code})"
            )
        return data.get("data", {})

    raise LarkAPIError(
        f"飞书 API 调用失败 [{method} {path}]: {last_error}（已重试 {MAX_RETRIES} 次）"
    )


def _backoff(attempt: int, retry_after: str = None) -> None:
    """指数退避等待，优先采用服务端给出的 Retry-After。"""
    if retry_after:
        try:
            time.sleep(min(float(retry_after), 30))
            return
        except (TypeError, ValueError):
            pass
    time.sleep(min(0.5 * (2 ** attempt), 8))


# ── 电子表格 API ──────────────────────────────────────
def list_sheets(spreadsheet_token: str) -> list:
    """列出表格下所有工作表，返回 [{sheet_id, title, row_count, column_count}, ...]。"""
    data = _request(
        "GET", f"/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query"
    )
    result = []
    for sheet in data.get("sheets", []):
        grid = sheet.get("grid_properties", {}) or {}
        result.append({
            "sheet_id": sheet.get("sheet_id", ""),
            "title": sheet.get("title", ""),
            "row_count": grid.get("row_count", 0),
            "column_count": grid.get("column_count", 0),
        })
    return result


def find_sheet_id(spreadsheet_token: str, sheet_title: str) -> str:
    """按工作表标题查找 sheet_id；找不到时抛错并列出可用标题。"""
    sheets = list_sheets(spreadsheet_token)
    for sheet in sheets:
        if sheet["title"] == sheet_title or sheet["sheet_id"] == sheet_title:
            return sheet["sheet_id"]
    available = "、".join(s["title"] for s in sheets) or "(空)"
    raise ValueError(f"未找到工作表 '{sheet_title}'，可用工作表: {available}")


def read_values(spreadsheet_token: str, sheet_id: str, range_str: str) -> list:
    """
    读取单元格区域，返回二维数组（rows）。

    range_str 形如 "A1:R1" 或 "A:A"，会自动拼接 sheet_id 前缀。
    """
    full_range = range_str if "!" in range_str else f"{sheet_id}!{range_str}"
    data = _request(
        "GET",
        f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/{full_range}",
    )
    return data.get("valueRange", {}).get("values", []) or []


def append_rows(spreadsheet_token: str, sheet_id: str, rows: list) -> dict:
    """
    在表格末尾追加若干行（由飞书服务端定位末行，并发安全）。

    返回 {"updated_range": ..., "updated_rows": ..., "row_number": 起始行号}。
    """
    width = max((len(r) for r in rows), default=1)
    end_col = get_column_letter(width)
    data = _request(
        "POST",
        f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values_append",
        params={"insertDataOption": "OVERWRITE"},
        json={
            "valueRange": {
                "range": f"{sheet_id}!A1:{end_col}1",
                "values": rows,
            }
        },
    )
    updated_range = data.get("updates", {}).get("updatedRange", "")
    return {
        "updated_range": updated_range,
        "updated_rows": data.get("updates", {}).get("updatedRows", 0),
        "row_number": _parse_row_from_range(updated_range),
    }


def update_values(spreadsheet_token: str, sheet_id: str, range_str: str, rows: list) -> dict:
    """覆盖写入指定区域。"""
    full_range = range_str if "!" in range_str else f"{sheet_id}!{range_str}"
    return _request(
        "PUT",
        f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values",
        json={"valueRange": {"range": full_range, "values": rows}},
    )


def create_spreadsheet(title: str, folder_token: str = "") -> dict:
    """新建电子表格，返回 {spreadsheet_token, url}。"""
    body = {"title": title}
    if folder_token:
        body["folder_token"] = folder_token
    data = _request("POST", "/open-apis/sheets/v3/spreadsheets", json=body)
    spreadsheet = data.get("spreadsheet", {}) or {}
    return {
        "spreadsheet_token": spreadsheet.get("spreadsheet_token", ""),
        "url": spreadsheet.get("url", ""),
    }


def set_tenant_editable(spreadsheet_token: str) -> bool:
    """
    把表格的链接分享权限设为「组织内获得链接的人可编辑」。

    应用创建的文档默认只有应用自己可访问，需显式放开，
    否则组织内成员打开链接会提示无权限。
    """
    try:
        _request(
            "PATCH",
            f"/open-apis/drive/v2/permissions/{spreadsheet_token}/public",
            params={"type": "sheet"},
            json={"link_share_entity": "tenant_editable"},
        )
        return True
    except LarkAPIError:
        return False


def check_api() -> dict:
    """
    健康检查：验证凭证可用且 token 能正常换取。

    返回 {"available": bool, "message": str}。
    """
    try:
        get_tenant_token(force_refresh=True)
        return {"available": True, "message": "飞书 API 可用"}
    except Exception as e:
        return {"available": False, "message": str(e)}


def _parse_row_from_range(range_str: str) -> int:
    """从 updatedRange（如 "Sheet1!A5:R5"）中解析起始行号。"""
    match = re.search(r"[A-Z]+(\d+)", range_str.split("!")[-1])
    return int(match.group(1)) if match else 0
