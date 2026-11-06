"""
DeepSeek API 辅助模块。

用于脚本规则处理不了的场景，调用 DeepSeek 大模型兜底。
每次调用自动加载所有 SKILL.md 作为系统提示词，让 AI 理解业务规则。

用法：
    from src.ai_helper import ai_extract_surcharge, ai_extract_content, ai_generate_taopiao
    
    # 提取加收信息
    surcharge = ai_extract_surcharge(raw_content)
    
    # 提取套餐内容
    content = ai_extract_content(raw_content)
    
    # 生成套票名称
    taopiao = ai_generate_taopiao(title, raw_content)
"""

import json
import os
import re
from pathlib import Path
from typing import Optional

import requests

# ============================================================
# 配置
# ============================================================
# 从项目根目录 .env 加载凭证（不覆盖已存在的环境变量）。
# 与 lark_client 共用同一份加载逻辑，保证两条链路读同一处配置。
from src.lark_client import _load_dotenv
_load_dotenv()

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# SKILL 目录
SKILL_DIR = PROJECT_ROOT / ".qwen" / "skills"


def load_all_skills() -> str:
    """
    加载所有 SKILL.md 文件内容，合并为系统提示词。

    遍历 .qwen/skills/ 下所有子目录中的 SKILL.md 文件，
    拼接成完整的业务规则说明。
    """
    parts = []
    if not SKILL_DIR.exists():
        return ""

    for skill_path in sorted(SKILL_DIR.iterdir()):
        skill_file = skill_path / "SKILL.md"
        if skill_file.exists():
            content = skill_file.read_text(encoding="utf-8")
            # 提取 name 和 description
            name = skill_path.name
            desc_match = re.search(r'description:\s*(.+)', content)
            desc = desc_match.group(1) if desc_match else name
            parts.append(f"=== {name}: {desc} ===")
            parts.append(content)

    return "\n\n".join(parts)


def call_deepseek(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.1,
    max_tokens: int = 10000,
) -> str:
    """
    调用 DeepSeek API。

    Args:
        system_prompt: 系统提示词（注入所有 skill 规则）
        user_prompt: 用户请求
        temperature: 温度参数，越低越确定
        max_tokens: 最大输出 token 数

    Returns:
        AI 返回的文本内容

    Raises:
        RuntimeError: API 调用失败时抛出
    """
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    # deepseek-v4-flash 是 reasoning 模型，会占用 token 做推理
    # 在 system prompt 中明确要求不要推理过程，直接输出结果
    enhanced_system = system_prompt + "\n\n重要：直接输出结果，不要输出推理过程。"

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": enhanced_system},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        resp = requests.post(
            DEEPSEEK_API_URL,
            headers=headers,
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        return content
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"DeepSeek API 调用失败: {e}")
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise RuntimeError(f"DeepSeek API 返回格式异常: {e}")


def load_skill(skill_name: str) -> str:
    """
    加载指定名称的 SKILL.md 文件内容。

    Args:
        skill_name: skill 目录名，如 "xiumius-hotels" 或 "ticket-publish"

    Returns:
        skill 文件内容，未找到时返回空字符串
    """
    skill_path = SKILL_DIR / skill_name / "SKILL.md"
    if not skill_path.exists():
        return ""
    content = skill_path.read_text(encoding="utf-8")
    desc_match = re.search(r'description:\s*(.+)', content)
    desc = desc_match.group(1) if desc_match else skill_name
    return f"=== {skill_name}: {desc} ===\n{content}"


def load_all_skills() -> str:
    """
    加载所有 SKILL.md 文件内容，合并为系统提示词。

    遍历 .qwen/skills/ 下所有子目录中的 SKILL.md 文件，
    拼接成完整的业务规则说明。
    """
    parts = []
    if not SKILL_DIR.exists():
        return ""

    for skill_path in sorted(SKILL_DIR.iterdir()):
        skill_file = skill_path / "SKILL.md"
        if skill_file.exists():
            content = skill_file.read_text(encoding="utf-8")
            # 提取 name 和 description
            name = skill_path.name
            desc_match = re.search(r'description:\s*(.+)', content)
            desc = desc_match.group(1) if desc_match else name
            parts.append(f"=== {name}: {desc} ===")
            parts.append(content)

    return "\n\n".join(parts)


def _build_system_prompt(task_description: str, skill_name: str = "") -> str:
    """构建系统提示词：指定 skill 规则 + 当前任务说明。

    Args:
        task_description: 任务描述
        skill_name: 指定加载的 skill 名称（如 "xiumius-hotels"），
                    为空时加载所有 skill
    """
    if skill_name:
        skills_content = load_skill(skill_name)
    else:
        skills_content = load_all_skills()
    return f"""你是一个旅游/酒店套餐内容提取助手。以下是所有业务规则：

{skills_content}

当前任务：{task_description}

请严格按照上述规则处理，只返回结果内容，不要添加额外解释。"""


# ============================================================
# 加收提取（AI 兜底）
# ============================================================

def ai_extract_surcharge(raw_content: str) -> str:
    """
    用 AI 从秀米原始内容中提取加收信息。

    当脚本规则提取不到或提取不完整时调用此函数兜底。

    Args:
        raw_content: 秀米提取的原始文本内容

    Returns:
        提取到的加收规则文本，如果无加收返回空字符串
    """
    if not raw_content:
        return ""

    system_prompt = _build_system_prompt("从酒店/景区套餐内容中提取加收规则")

    user_prompt = f"""从以下秀米内容中提取【加收规则】部分。

规则：
1. 只提取纯粹的加收规则，如"周六加收100元/间"、"节假日加收200元"、"周末不加收"等
2. 过滤掉套餐升级/增订类内容（如"加150元升级含巧克力乐园门票"、"可增订XX套餐"等）
3. 过滤掉数字序号列表（如"1、升级房型加收..."、"2、..."）
4. 如果内容中有"全部平日、周末不加收"这类信息，也要包含
5. 如果没有任何加收规则，返回空字符串
6. 只返回加收规则原文，不要添加任何解释
7. **按时间段分行**：不同月份/节假日（如6月、7月、8月、端午节、中秋节、国庆节等）的加收规则，每个时间段单独一行，用换行符分隔。例如：
   6月、9月平日周末不加收
   7月、8月平日不加收，周五+150元/间，周六+250元/间
   端午节6月19-20日+350元/间
   中秋节9月25-26日+350元/间

秀米内容：
---
{raw_content}
---

加收规则："""

    try:
        result = call_deepseek(system_prompt, user_prompt, temperature=0.1)
        # 如果 AI 返回"无"或"没有"等，视为空
        if not result or re.match(r'^[无没]$', result):
            return ""
        return result
    except Exception:
        return ""


# ============================================================
# 套餐内容提取（AI 兜底）
# ============================================================

def ai_extract_content(raw_content: str) -> str:
    """
    用 AI 从秀米原始内容中提取套餐包含项。

    当脚本规则提取不到或提取不完整时调用此函数兜底。

    Args:
        raw_content: 秀米提取的原始文本内容

    Returns:
        提取到的套餐包含项文本
    """
    if not raw_content:
        return ""

    system_prompt = _build_system_prompt("从酒店/景区套餐内容中提取套餐包含项")

    user_prompt = f"""从以下秀米内容中提取【套餐包含项】。

规则：
1. 只保留以【】开头的套餐包含项行，如【住】、【食】、【玩】、【赠】、【享】、【游】、【泡】、【畅】等
2. 去掉价格信息（如"抢购价：328元/套"）
3. 去掉时间信息（如"售卖时间：..."、"使用时间：..."）
4. 去掉加收信息（如"加收说明：..."）
5. 去掉温馨提示、特别声明等非套餐内容
6. 保留原始【】标签格式
7. 如果没有任何套餐包含项，返回空字符串
8. 只返回提取结果，不要添加任何解释

秀米内容：
---
{raw_content}
---

套餐包含项："""

    try:
        result = call_deepseek(system_prompt, user_prompt, temperature=0.1)
        if not result:
            return ""
        return result
    except Exception:
        return ""


# ============================================================
# 套票名称生成（AI 兜底）
# ============================================================

def ai_generate_taopiao(title: str, raw_content: str) -> str:
    """
    用 AI 从标题和秀米内容生成套票名称。

    当脚本规则生成的套票名称不理想时调用此函数兜底。

    Args:
        title: 标题行
        raw_content: 秀米提取的原始文本内容

    Returns:
        生成的套票名称
    """
    if not title:
        return ""

    system_prompt = _build_system_prompt("从标题和套餐内容中生成套票名称")

    user_prompt = f"""根据以下标题和套餐内容，生成套票名称。

规则：
1. 格式：【主要卖点】酒店名称·酒店房型｜套餐权益1+权益2+权益3+权益4
2. 主要卖点从标题推理（按优先级：不加收→周末不加，五一+一口价→51一口价，五一→五一主推，一口价→一口价，秒杀→秒杀，专场→专场，特惠→特惠，预售→预售，暑期→暑期，开园→开园，含晚餐/含早→含餐，默认→主推）
3. 酒店名不要加地域前缀（如"清远·"、"惠州·"）
4. 酒店名和房型之间用 · 分隔
5. 套餐权益从秀米内容的【】标签中提取，取前4个
6. 权益保留人数前缀（如"2大2小无限次汤泉"）
7. 只返回套票名称，不要添加任何解释

标题：
{title}

套餐内容：
{raw_content}

套票名称："""

    try:
        result = call_deepseek(system_prompt, user_prompt, temperature=0.2)
        if not result:
            return ""
        return result
    except Exception:
        return ""


# ============================================================
# 通用 AI 提取函数
# ============================================================

def ai_extract_field(raw_content: str, field_name: str, instruction: str) -> str:
    """
    通用 AI 提取函数，用于提取任意字段。

    Args:
        raw_content: 原始文本内容
        field_name: 字段名称（用于提示）
        instruction: 提取规则说明

    Returns:
        AI 提取的结果
    """
    if not raw_content:
        return ""

    system_prompt = _build_system_prompt(f"从套餐内容中提取{field_name}")

    user_prompt = f"""从以下内容中提取{field_name}。

规则：
{instruction}

内容：
---
{raw_content}
---

{field_name}："""

    try:
        result = call_deepseek(system_prompt, user_prompt, temperature=0.1)
        if not result or re.match(r'^[无没]$', result):
            return ""
        return result
    except Exception:
        return ""


# ============================================================
# 全 AI 提取（一次调用提取所有字段）
# ============================================================

def ai_extract_all(title: str, raw_content: str, product_type: str = "hotel") -> dict:
    """
    全 AI 模式：一次调用提取所有字段。

    返回 dict，包含：
    - taopiao: 套票名称
    - content_items: 套餐包含项
    - surcharge: 加收规则（平日/周末/节假日加收）
    - other_surcharge: 其他加收（可选升级房型加收）
    - sell_time: 售卖时间
    - available_date: 可用日期
    - price: 直播价
    - hotel_name: 酒店名称

    Args:
        title: 标题行
        raw_content: 秀米提取的原始文本内容
        product_type: 产品类型，"hotel" 或 "ticket"

    Returns:
        包含各字段的 dict，提取失败时对应字段为空字符串
    """
    result = {
        "taopiao": "",
        "content_items": "",
        "surcharge": "",
        "other_surcharge": "",
        "sell_time": "",
        "available_date": "",
        "price": "",
        "hotel_name": "",
    }

    if not raw_content and not title:
        return result

    # 根据产品类型选择对应的 skill
    skill_name = "ticket-publish" if product_type == "ticket" else "xiumius-hotels"
    system_prompt = _build_system_prompt("从标题和套餐内容中提取所有字段", skill_name=skill_name)

    if product_type == "ticket":
        # ---- 门票产品 prompt ----
        user_prompt = f"""根据以下标题和套餐内容，提取所有字段。

请严格按照以下格式返回，每行一个字段，不要添加任何额外内容：

套票名称：【票种】景区名｜票种+其他备注
套餐包含项：景区名｜门票名，后接使用规则/注意事项等备注（按原文提取完整内容，保留原文格式和换行）
加收规则：周末/节假日不加价（没有则留空）
其他加收：可选升级项目的加收（没有则留空）
售卖时间：
可用日期：
直播价：XXX元
酒店名称：XXX景区
多套餐列表：
套餐1名称：票种｜价格｜差异项｜加收规则
套餐2名称：票种｜价格｜差异项｜加收规则
（如果有多个套餐，请列出所有；如果只有一个套餐，留空。第一个竖线段写票种，与套票名称方括号内的票种完全一致）
所有套餐相同项：所有套餐共有的权益内容（没有则留空）

规则：
1. **套票名称格式**：【票种】景区名｜票种+其他备注。竖线后面先重复一次竖线前的那个票种，再接原文里的其他备注（如"即买即用"、"周末不加价"、"含自助晚餐"）。
   - **方括号里必须填"票种"**——告诉游客这是什么票，例如单人票、双人票、家庭票、儿童票、亲子票、成人票、老人票、学生票。票种从门票名判断（门票名写"单人票/双人票/家庭票/2大1小"就填对应票种，如"家庭票"）；"小童/长者""儿童"等写儿童票，"老人/长者"等写老人票；门票名看不出票种时填"单人票"。同一票种多个受众时只取主票种（"小童/长者"→儿童票，不要写"儿童/老人票"）。
   - **竖线后面 = 票种 + 其他备注**，票种部分必须与方括号内完全一致。附加成人群门槛（如"需1名成人陪同"）视为备注，追加在后面。
   - **绝对不要填营销卖点词**（如"早场票"、"秒杀"、"特价"、"不加收"、"玻璃桥"），也不要带时间段、价格、促销语、下单编号。
   - 景区名与后面内容用**全角竖线｜**分隔，不要用 ~ 或 +。
   - 示例："【即买即用】成人128返12" → 【成人票】东莞隐贤山庄｜成人票+即买即用；"【早场票9-12点】29.9元抢购！佛山西樵山森吧飞越树上探险乐园～黄线儿童蛛网单人票" → 【儿童票】佛山西樵山森吧飞越树上探险乐园｜儿童票+黄线儿童蛛网；"东莞4A隐贤山庄家庭票" → 【家庭票】东莞4A隐贤山庄｜家庭票+含动物园+水上乐园+18项游乐项目各玩1次+《梁祝》烟花秀
2. **套餐包含项**：如果输入中有【海报原文信息（供参考）】标记，则只提取该标记之后的内容作为套餐包含项，保留原文的换行和格式。包含剧场表演、景区项目、水上乐园设施、限购说明、游乐项目列表、营业时间、注意事项等所有信息。不要精简，不要改写格式。如果没有【海报原文信息】标记，则从标题和套餐内容中提取。
**格式要求：套餐包含项以"景区名｜门票名"开头（与套票名称一致，但不带【票种】前缀），后面接使用规则、限购说明、营业时间、注意事项等备注文案；一张门票只有名称没有备注时，就只写"景区名｜门票名"。** 示例：原文"佛山西樵山森吧飞越树上探险乐园~黄线儿童蛛网单人票"→"佛山西樵山森吧飞越树上探险乐园｜黄线儿童蛛网单人票"；原文还写了"需提前1天预约、限1人使用"，则返回"佛山西樵山森吧飞越树上探险乐园｜黄线儿童蛛网单人票，需提前1天预约，限1人使用"。
3. **加收规则**只填平日/周末/节假日是否加收，如"周末/节假日不加价"或"周六加收70元"。**不包含升级项目加收**。**按时间段分行**：不同月份/节假日的加收规则每个时间段单独一行
4. **其他加收**填可选升级项目的加收，如"升级VIP通道需加收50元"。**不包含平日/周末加收**
5. **售卖时间**是从"抢购时间"、"售卖时间"、"可售时间"或"抢购日期"后面提取的**销售区间**（游客什么时候能买），例如"10月26日到11月11日"。**只有当原文没写售卖时间时，才让它等于可用日期**；不要用使用有效期代替售卖时间
6. **可用日期**就是使用有效期，从"使用时间"、"有效期"、"使用日期"或"适用日期"后面提取。**末尾日期一律归一化为"即日起-月日"格式**：原文"购买后次日至12月31日""使用日期至12月31日"都返回"即日起-12月31日"，不要照抄"购买后次日"这类描述性文字。若原文明确写了起始日期（如"11月1日-12月31日"），则保留起止两端。**如果有效期是按天数算的（如"购买后30天内有效""下单后90天内可用"），就按原文写成"即日起30天内有效"、"即日起90天内可用"，不要拿售卖结束日期代替，也不要自己换算成具体日期**
7. 直播价匹配 ¥XXX元 / XXX元 等格式
8. 酒店名称改为景区名称，从标题中提取
9. **关键：没有对应信息时留空，不要填"无"或"没有"，也不要填示例数据。如果原文中完全没有日期信息，售卖时间和可用日期都留空。**
10. **多套餐列表**：如果原文中有多个价格或套餐（如基础套票、玻璃桥套票等），说明有多个套餐。每个套餐一行，格式：套餐名称｜价格｜差异项（该套餐独有内容，如"含玻璃桥"）｜加收规则。差异项要简洁，只写该套餐独有、其他套餐没有的内容。
11. **所有套餐相同项**：所有套餐都包含的共有权益，用自然语言描述。

标题：
{title}

套餐内容：
{raw_content if raw_content else "（无，请完全依据上面的标题提取字段）"}

提取结果："""
    else:
        # ---- 酒店产品 prompt ----
        user_prompt = f"""根据以下标题和套餐内容，提取所有字段。

请严格按照以下格式返回，每行一个字段，不要添加任何额外内容：

套票名称：【主要卖点】酒店名称·酒店房型｜套餐权益1+权益2+权益3+权益4
套餐包含项：【住】...（每个【】标签单独一行，保留原文换行和空行分组）
加收规则：平日、周末、节假日是否加收（如"周末不加收"、"周六加收70元"等，没有则留空）
其他加收：可选升级房型的加收（如"升级豪华房需加收100元"等，没有则留空）
售卖时间：
可用日期：
直播价：XXX元/套
酒店名称：XXX酒店
多套餐列表：
套餐1名称：套餐1名称｜价格｜差异项｜加收规则
套餐2名称：套餐2名称｜价格｜差异项｜加收规则
（如果有多个套餐，请列出所有；如果只有一个套餐，留空）
所有套餐相同项：所有套餐共有的权益内容（没有则留空）

规则：
1. **套票名称格式**：【套餐名】酒店名称｜主体权益+差异项。套餐名从原文提取（如"夏日畅玩套票"、"双早双晚套票"），主体权益为所有套餐共有的核心权益，差异项为该套餐独有内容。
   - **方括号里绝对不要填价格档位**（"399套餐""599元套餐""399档"等都不是套餐名）；原文没有真正的套餐名时，方括号整段留空，写成"酒店名称｜权益"即可。
   - **竖线后只保留4项权益**，用 + 连接，不要把整段描述照抄进来；不要用顿号、逗号罗列。
   - 酒店名称里不要带"温泉/亚投/假日/君廷"等品牌并列，只写主酒店名；房型算权益之一。
   - 示例："399套餐 惠州双月湾享海温泉酒店 海景房（7楼以上）、无限次无边际泳池、健身房、儿童乐园、停车、双人自助早餐" → 【】惠州双月湾享海温泉酒店｜海景房+无限次无边际泳池+健身房+儿童乐园
2. 酒店名不要加地域前缀
3. 套餐权益从【】标签中提取，取前4个，保留人数前缀
4. 套餐包含项只保留【住】【食】【餐】【玩】【游】【赠】【享】【免】【泡】【浸】【畅】【行】标签行，**每个标签单独一行，不要空行，连续排列**
5. **加收规则**只填平日/周末/节假日是否加收，如"平日、周末、端午、暑假不加收"或"周六加收70元；节假日加收200元"。**不包含升级房型加收**。**按时间段分行**：不同月份/节假日的加收规则每个时间段单独一行
6. **其他加收**填可选升级房型的加收，如"升级豪华双床房需加收50元；升级轻奢loft房需加收160元"。**不包含平日/周末加收**
7. 售卖时间从"抢购时间"、"售卖时间"或"售卖日期"后面提取
8. 可用日期从"使用时间"、"有效期"或"使用日期"后面提取。**末尾日期一律归一化为"即日起-月日"格式**：原文"购买后次日至12月31日""使用日期至12月31日"都返回"即日起-12月31日"，不要照抄"购买后次日"这类描述性文字。若原文明确写了起始日期（如"11月1日-12月31日"），则保留起止两端
9. 直播价匹配 ¥XXX元 / XXX元/套 等格式
10. **关键：没有对应信息时留空，不要填"无"或"没有"，也不要填示例数据。如果原文中完全没有日期信息，售卖时间和可用日期都留空。**
11. **多套餐列表**：如果原文中有多个价格（如599和799），说明有多个套餐。每个套餐一行，格式：套餐名称｜价格｜差异项（该套餐独有、其他套餐没有的内容）｜加收规则。差异项要简洁，只写该套餐独有内容。
12. **所有套餐相同项**：所有套餐都包含的共有权益，用自然语言描述，不要用【】标签格式。

标题：
{title}

套餐内容：
{raw_content}

提取结果："""

    try:
        ai_text = call_deepseek(system_prompt, user_prompt, temperature=0.1, max_tokens=10000)
        if not ai_text:
            return result

        # 解析 AI 返回的字段（支持多行内容）
        current_field = None
        current_lines = []

        def flush_field():
            """将当前收集的字段写入 result"""
            nonlocal current_field, current_lines
            if current_field is None:
                return
            val = "\n".join(current_lines).strip()
            if val and val not in ("无", "没有"):
                result[current_field] = val
            current_field = None
            current_lines = []

        for line in ai_text.split("\n"):
            line_stripped = line.strip()

            # 检测新字段开始
            if line_stripped.startswith("套票名称："):
                flush_field()
                current_field = "taopiao"
                current_lines = [line_stripped[len("套票名称："):].strip()]
            elif line_stripped.startswith("套餐包含项："):
                flush_field()
                current_field = "content_items"
                current_lines = [line_stripped[len("套餐包含项："):].strip()]
            elif line_stripped.startswith("加收规则："):
                flush_field()
                current_field = "surcharge"
                current_lines = [line_stripped[len("加收规则："):].strip()]
            elif line_stripped.startswith("其他加收："):
                flush_field()
                current_field = "other_surcharge"
                current_lines = [line_stripped[len("其他加收："):].strip()]
            elif line_stripped.startswith("售卖时间："):
                flush_field()
                current_field = "sell_time"
                current_lines = [line_stripped[len("售卖时间："):].strip()]
            elif line_stripped.startswith("可用日期："):
                flush_field()
                current_field = "available_date"
                current_lines = [line_stripped[len("可用日期："):].strip()]
            elif line_stripped.startswith("直播价："):
                flush_field()
                current_field = "price"
                current_lines = [line_stripped[len("直播价："):].strip()]
            elif line_stripped.startswith("酒店名称："):
                flush_field()
                current_field = "hotel_name"
                current_lines = [line_stripped[len("酒店名称："):].strip()]
            elif line_stripped.startswith("多套餐列表："):
                flush_field()
                current_field = "multi_packages"
                current_lines = []
            elif line_stripped.startswith("所有套餐相同项："):
                flush_field()
                current_field = "common_items"
                current_lines = [line_stripped[len("所有套餐相同项："):].strip()]
            elif current_field is not None:
                # 当前字段的续行
                current_lines.append(line_stripped)

        flush_field()

        # 后处理：将同一行内多个【】标签拆分成多行，并去掉空行
        if result["content_items"]:
            result["content_items"] = re.sub(r'(?=【)', '\n', result["content_items"]).strip()
            # 去掉所有空行，连续排列
            result["content_items"] = re.sub(r'\n+', '\n', result["content_items"])
        if result["surcharge"]:
            result["surcharge"] = re.sub(r'(?=【)', '\n', result["surcharge"]).strip()

        # 解析多套餐列表
        multi_packages = result.get("multi_packages", "")
        common_items = result.get("common_items", "")
        if multi_packages:
            packages = []
            for line in multi_packages.split("\n"):
                line = line.strip()
                if not line:
                    continue
                # 格式1：套餐1名称：名称｜价格｜差异项｜加收规则
                # 格式2：名称｜价格｜差异项｜加收规则
                parts = line.split("｜")
                if len(parts) >= 2:
                    name_part = parts[0]
                    # 去掉 "套餐X名称：" 前缀（如果有）
                    name = re.sub(r'^套餐\d+名称[：:]', '', name_part).strip()
                    price = parts[1].strip()
                    diff = parts[2].strip() if len(parts) >= 3 else ""
                    surcharge = parts[3].strip() if len(parts) >= 4 else ""
                    packages.append({
                        "name": name,
                        "price": price,
                        "diff": diff,
                        "common": common_items,
                        "surcharge": surcharge,
                    })
            result["packages"] = packages

        return result
    except Exception as e:
        print(f"[ai_extract_all ERROR] {e}")
        # 不吞异常：调用方必须知道解析失败，否则会拿着全空字段继续写表
        raise RuntimeError(f"AI 解析失败: {e}") from e


if __name__ == "__main__":
    # 测试
    print("=" * 50)
    print("测试 AI 辅助模块")
    print("=" * 50)

    # 测试加载 skill
    skills = load_all_skills()
    print(f"\n已加载 {len(skills)} 字符的 skill 内容")

    # 测试 API 连通性
    print("\n测试 API 连通性...")
    try:
        result = call_deepseek(
            "你是一个测试助手。",
            "回复'OK'表示API连通正常。",
            temperature=0,
            max_tokens=10,
        )
        print(f"  ✅ API 连通正常: {result}")
    except Exception as e:
        print(f"  ❌ API 连通失败: {e}")
