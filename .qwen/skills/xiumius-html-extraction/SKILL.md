---
name: xiumius-html-extraction
description: 从秀米JSON数据中提取_$raHTML文本内容的可靠方法，处理surrogate字符导致的re.sub报错；支持navo.top短链接和标准秀米H5页面
source: auto-skill
extracted_at: '2026-06-04T08:58:01.120Z'
---

# 秀米 HTML 文本提取方法

## 问题

秀米 H5 页面的 JSON 数据中，`_$raHTML` 字段可能包含 surrogate 字符（如 `\uD800`-`\uDFFF` 范围的代理对），导致 Python 的 `re.sub(r'<[^>]+>', '', html)` 报错：

```
UnicodeEncodeError: 'utf-8' codec can't encode characters in position X-X: surrogates not allowed
```

## 解决方案

不要使用 `re.sub` 去除 HTML 标签，改用**手动遍历字符**的方式：

```python
def strip_html_tags(html: str) -> str:
    """手动去除HTML标签，避免re.sub处理surrogate字符时报错"""
    text = []
    in_tag = False
    for c in html:
        if c == '<':
            in_tag = True
        elif c == '>':
            in_tag = False
        elif not in_tag:
            text.append(c)
    result = ''.join(text)
    result = result.replace('&nbsp;', ' ').replace('&amp;', '&')
    result = ' '.join(result.split())  # 合并空白
    return result
```

## 秀米 JSON 数据路径

秀米 H5 页面的 JSON 数据中，`_$raHTML` 字段的位置：

```
cubes[0].pages[0].layers[0]._comp._$raHTML
```

注意：`layers[0]._comp` 是一个 dict，`_$raHTML` 是它的一个 key。

### 访问 _$raHTML 的可靠方法

**方法一：直接访问（推荐，大多数情况可用）**

```python
layer = data['cubes'][0]['pages'][0]['layers'][0]
comp = layer['_comp']
html = comp['_$raHTML']
```

**方法二：遍历键名查找（兜底方案）**

某些 Python 版本或 JSON 编码情况下，`comp['_$raHTML']` 可能报 `KeyError: '_'`（键名中的 `$` 或 surrogate 字符导致）。此时应遍历 dict 的键来查找：

```python
comp = data['cubes'][0]['pages'][0]['layers'][0]['_comp']
html = None
for k in comp:
    if 'raHTML' in k or 'html' in k.lower():
        html = comp[k]
        break
```

这个方法能可靠地找到 `_$raHTML` 键，无论键名编码问题如何。

**方法三：递归遍历整个 JSON 树（兜底方案）**

当 `_$raHTML` 可能出现在 JSON 树的多个位置（如多层嵌套的 comp 结构），或者不确定具体路径时，递归遍历整个 JSON：

```python
def extract_all_rahtml(obj):
    """递归遍历JSON树，提取所有_$raHTML字段"""
    results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == '_$raHTML' and isinstance(v, str):
                results.append(v)
            else:
                results.extend(extract_all_rahtml(v))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(extract_all_rahtml(item))
    return results
```

### 判断 JSON 版本

秀米 JSON 有 version 1.0 和 version 2.0 两种结构：

- **version 1.0**：`data['cubes'][0]['pages'][0]['layers'][0]['_comp']['_$raHTML']` — `_comp` 直接包含 `_$raHTML`
- **version 2.0**：结构相同，但 `_comp` 中可能包含更多字段（如 `tplId`, `pose`, `style`, `_$uuid` 等），`_$raHTML` 仍然在 `_comp` 中

两种版本的提取方式相同，无需区分处理。

## 获取秀米 show_data_url 的方法

### 方法一：标准秀米 H5 页面（a.xiumius.cn / v.xiumius.cn / c.xiumius.cn）

从秀米页面 HTML 中提取 `show_data_url`：

```python
import re
# 从页面HTML中查找
m = re.search(r'\"show_data_url\"\s*:\s*\"([^\"]+)\"', html)
if m:
    url = 'https:' + m.group(1)
```

注意：`show_data_url` 在 `injectedData.showInfo` 中，以 URL 编码的 JSON 字符串形式存在。直接搜索 `show_data_url` 字符串即可找到。

### 方法二：navo.top 短链接（指向秀米临时预览页面）

当用户提供的是 `navo.top` 短链接（如 `http://navo.top/A3UvEr`）时，该链接会重定向到秀米的临时预览页面。预览页面同样包含 `injectedData.showInfo`，但以 `decodeURIComponent` 包裹的 URL 编码 JSON 形式存在。

**提取步骤：**

1. 用 `curl -sL` 跟随重定向获取页面 HTML
2. 用正则提取 `injectedData.showInfo = JSON.parse(decodeURIComponent("..."))` 中的编码字符串
3. URL 解码后解析 JSON
4. 从 JSON 中提取 `show_data_url`

```python
import json, urllib.parse, re

# 1. 获取页面HTML
# curl -sL 'http://navo.top/xxx'

# 2. 提取URL编码的showInfo
match = re.search(
    r'injectedData\.showInfo\s*=\s*JSON\.parse\(decodeURIComponent\(\"([^\"]+)\"\)\)',
    html
)
if match:
    encoded = match.group(1)
    decoded = urllib.parse.unquote(encoded)
    show_info = json.loads(decoded)
    show_data_url = show_info.get('show_data_url', '')
    # 补上 https: 前缀
    if show_data_url.startswith('//'):
        show_data_url = 'https:' + show_data_url
```

## 完整提取流程

### 标准流程（JSON 文件已知路径）

```python
import gzip, json

# 1. 下载JSON（注意：秀米服务器返回gzip压缩数据）
# curl -s -H "Accept-Encoding: identity" "<url>" -o /tmp/data.json.gz

# 2. 解压并加载
with gzip.open('/tmp/data.json.gz', 'rt', encoding='utf-8') as f:
    data = json.load(f)

# 3. 提取HTML
html = data['cubes'][0]['pages'][0]['layers'][0]['_comp']['_$raHTML']

# 4. 去除HTML标签（手动遍历，不用re.sub）
text = []
in_tag = False
for c in html:
    if c == '<':
        in_tag = True
    elif c == '>':
        in_tag = False
    elif not in_tag:
        text.append(c)
text = ''.join(text)
text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
text = ' '.join(text.split())
```

### 从 navo.top 短链接到纯文本的完整流程

```python
import subprocess, json, urllib.parse, re

# 1. 获取navo.top页面HTML（跟随重定向）
result = subprocess.run(
    ['curl', '-sL', 'http://navo.top/XXXX'],
    capture_output=True, text=True, timeout=30
)
html = result.stdout

# 2. 提取showInfo
match = re.search(
    r'injectedData\.showInfo\s*=\s*JSON\.parse\(decodeURIComponent\(\"([^\"]+)\"\)\)',
    html
)
encoded = match.group(1)
show_info = json.loads(urllib.parse.unquote(encoded))
show_data_url = 'https:' + show_info['show_data_url']

# 3. 下载JSON数据（注意：sd.xiumius.cn返回gzip压缩）
result = subprocess.run(
    ['curl', '-sL', '-H', 'Accept-Encoding: gzip', show_data_url],
    capture_output=True, timeout=30
)
# 用gunzip解压
result2 = subprocess.run(
    ['gunzip'],
    input=result.stdout, capture_output=True, timeout=30
)
data = json.loads(result2.stdout)

# 4. 递归提取所有_$raHTML
def extract_all_rahtml(obj):
    results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == '_$raHTML' and isinstance(v, str):
                results.append(v)
            else:
                results.extend(extract_all_rahtml(v))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(extract_all_rahtml(item))
    return results

htmls = extract_all_rahtml(data)

# 5. 去除HTML标签得到纯文本
for h in htmls:
    text = re.sub(r'<[^>]+>', '', h)  # 如果报surrogate错误，改用strip_html_tags
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if text and len(text) > 5:
        print(text)
```

## 注意事项

- **gzip 压缩**：`sd.xiumius.cn` 的 JSON 数据文件使用 gzip 压缩传输，必须用 `gunzip` 或 `gzip.open()` 解压
- **surrogate 字符**：`_$raHTML` 中可能包含 surrogate 字符，用 `re.sub` 去 HTML 标签会报错，应使用手动遍历方式
- **navo.top 临时预览**：navo.top 短链接指向的是秀米临时预览页面，有有效期限制，过期后无法访问
- **递归提取**：某些秀米 JSON 的 `_$raHTML` 分布在多个位置（如多层 comp 嵌套），递归遍历比固定路径更可靠
