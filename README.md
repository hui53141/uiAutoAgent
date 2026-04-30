# uiAutoAgent

**Android UI + Hardware + CLI 自动化框架** — 中心化生产，分布式执行，AI 驱动自愈。

---

## 架构概览

```
uiAutoAgent/
├── central/                    # 中心层（管理机）
│   ├── server.py               # FastAPI 编排服务
│   ├── healer/
│   │   ├── failure_aggregator.py   # 多节点失败聚合 + 去重
│   │   ├── screenshot_analyzer.py  # 多模态 LLM 截图分析
│   │   └── fix_committer.py        # 生成修复并提交 GitHub
│   └── code_generator/
│       └── aw_generator.py         # LLM 生成 AW Python 代码
│
├── executor/                   # 执行层（每台 PC 部署）
│   ├── agent.py                # 任务执行主循环（拉取 + 运行 + 上报）
│   ├── device_manager.py       # 连接池 + 设备锁
│   └── drivers/
│       ├── base_driver.py      # 抽象基类
│       ├── appium_driver.py    # Appium UI 驱动（三级定位兜底）
│       ├── maestro_driver.py   # Maestro CLI 驱动
│       └── hardware_driver.py  # Paramiko SSH + Serial 继电器
│
├── aw/                         # Action Words（业务大粒度封装）
│   ├── base_aw.py              # 基类（定位、重试、截图）
│   └── examples/
│       ├── login_aw.py
│       └── settings_aw.py
│
├── locators/                   # 语义定位符（版本化 YAML）
│   ├── v1.0/
│   └── v2.0/
│
├── configs/
│   ├── settings.yaml           # 全局配置
│   ├── nodes.yaml              # 执行节点清单
│   └── tasks.yaml              # 测试任务定义
│
├── scripts/
│   ├── start_central.py        # 启动中心服务
│   ├── start_executor.py       # 启动执行节点
│   └── run_task.py             # 下发任务 / 生成 AW
│
└── tests/                      # 单元测试
```

---

## 核心设计

### 1. 三级定位兜底策略

UI 元素查找按优先级依次回退，确保 UI 轻微变更不影响执行：

| Level | 策略 | 场景 |
|---|---|---|
| L1 | `accessibility_id` | 最稳定，优先使用 |
| L2 | `id` / `xpath` | 语义回退 |
| L3 | `image` | 视觉模板，最后手段 |

### 2. Token 成本控制：集中修复，一次广播

```
执行节点失败 → POST /failures → FailureAggregator 聚合去重
  → 阈值触发（默认 2 个节点同一失败）
    → ScreenshotAnalyzer (LLM 调用一次)
      → FixCommitter → git commit + push
        → 所有节点下次 git pull 自动获取修复
```

**LLM 只在中心被调用一次**，无论多少台节点报告同一失败。

### 3. 版本化定位符

```yaml
# locators/v2.0/login_page.yaml
version: "2.0"
elements:
  login_button:
    strategies:
      - strategy: accessibility_id
        value: btn_login_v2
```

App 版本升级时只需新建版本目录，无需修改任何 AW 代码。

---

## 快速开始

### 安装

```bash
pip install -r requirements.txt
# 或
pip install -e .
```

### 配置

1. 编辑 `configs/settings.yaml` 设置 GitHub repo、LLM provider 等
2. 编辑 `configs/nodes.yaml` 填入执行节点信息
3. 设置环境变量：

```bash
export OPENAI_API_KEY="sk-..."     # LLM 自愈（可选）
export GITHUB_TOKEN="ghp_..."      # 自动提交修复（可选）
export TEST_USERNAME="testuser"
export TEST_PASSWORD="testpass"
```

### 启动中心服务

```bash
python scripts/start_central.py --host 0.0.0.0 --port 8000
```

API 文档：http://localhost:8000/docs

### 启动执行节点（每台 PC 运行）

```bash
python scripts/start_executor.py --node-id node-01 --central-url http://<central-ip>:8000
```

### 下发任务

```bash
# 下发到指定节点
python scripts/run_task.py run --task smoke-login --nodes node-01 node-02

# 下发到所有注册节点
python scripts/run_task.py run --task regression-settings --all-nodes

# 查看中心服务状态
python scripts/run_task.py status
```

### LLM 生成新 AW（当新 UI 功能上线时）

```bash
python scripts/run_task.py generate-aw \
    --page checkout \
    --class-name CheckoutAW \
    --operations "add item to cart" "proceed to checkout" "confirm order"
```

---

## 开发新 AW

```python
# aw/examples/checkout_aw.py
from uiAutoAgent.aw.base_aw import BaseAW, retry

class CheckoutAW(BaseAW):
    PAGE = "checkout"  # 对应 locators/v1.0/checkout_page.yaml

    @retry(max_attempts=3, delay=2.0)
    def add_to_cart(self, product_name: str) -> None:
        self.tap("add_to_cart_btn")
        assert self.is_visible("cart_badge"), "Cart badge not visible after add"

    def checkout(self) -> None:
        self.tap("checkout_btn")
        self.wait_for("confirm_btn", timeout=10)
        self.tap("confirm_btn")
```

---

## 运行测试

```bash
pytest tests/ -v
```

---

## 规模扩展

- **添加执行节点**：在 `configs/nodes.yaml` 新增条目，执行节点 PC 运行 `start_executor.py`
- **支持新 App 版本**：在 `locators/` 新建版本目录，填写 YAML，无需修改代码
- **支持新 UI 页面**：新增 locator YAML + 对应 AW 类（或调用 `generate-aw` 命令生成）

---

## 技术栈

| 组件 | 技术 |
|---|---|
| 语言 | Python 3.11+ |
| UI 驱动 | Appium (UiAutomator2) / Maestro |
| 硬件/CLI | Paramiko (SSH) + PySerial |
| 中心服务 | FastAPI + Uvicorn |
| 同步分发 | Git / GitHub |
| AI 层 | OpenAI GPT-4o / Anthropic Claude |
| 配置 | YAML |
| 测试 | pytest |