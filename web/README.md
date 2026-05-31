# hotels/tickets 上品工具

将 xiumius-hotels skill 的酒店/门票套餐提取功能做成 Web 界面，支持三种模式：

## 快速启动

```bash
cd web
./start.sh
```

启动后访问 http://localhost:3000

## 手动启动

### 1. 启动后端 API

```bash
cd web/backend
pip install -r requirements.txt
python main.py
```

后端运行在 http://localhost:8000，API 文档在 http://localhost:8000/docs

### 2. 启动前端

```bash
cd web/frontend
python3 -m http.server 3000
```

前端运行在 http://localhost:3000

## 三种模式

### 1. 秀米链接模式
- 输入 `a.xiumius.cn` / `v.xiumius.cn` 链接
- 自动提取套餐内容、生成套票
- 编辑确认后写入飞书

### 2. 海报识别模式
- 粘贴文案内容（可选）+ 上传海报图片
- OCR 自动识别文字，结合文案解析各字段
- 编辑确认后写入飞书

### 3. 手动输入模式
- 直接填写全部 14 列数据
- 适合已有完整文案的场景

## 飞书表格列说明

| 列 | 字段 | 说明 |
|----|------|------|
| A | 填写日期 | 自动填入当天日期 |
| B | 酒店/景区 | 酒店或景区名称 |
| C | 产品信息 | 原始输入或产品描述 |
| D | 套票名称 | 自动生成的套票名称 |
| E | 套票内容 | 套餐包含内容 |
| F | 加收 | 加收信息 |
| G | 其他加收 | 其他加收信息 |
| H | 售卖时间 | 售卖时间范围 |
| I | 可用日期 | 可用日期范围 |
| J | 对外价格 | 对外标价 |
| K | 直播价 | 直播价格 |
| L | 结算价格&利润率（已除平台） | 自动计算 |
| M | 备注 | 备注信息 |
| N | 海报 | 海报链接或图片 |

## 项目结构

```
web/
├── start.sh              # 一键启动脚本
├── README.md
├── backend/
│   ├── main.py           # FastAPI 后端（封装现有 Python 逻辑）
│   └── requirements.txt  # Python 依赖
└── frontend/
    └── index.html        # 前端页面（Bootstrap + 原生 JS）
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/check | 检查环境配置 |
| POST | /api/extract | 提取秀米链接内容 |
| POST | /api/ocr | OCR 识别海报图片 |
| POST | /api/write | 写入飞书表格 |
| POST | /api/calc-settlement | 计算结算价和利润率 |
| GET | /api/config | 获取飞书配置 |

## 依赖

- Python 3.8+
- FastAPI, uvicorn, requests, pyyaml
- pytesseract + tesseract-ocr（海报模式需要）
- lark-cli（飞书命令行工具）
