# text/ —— 压测与评测工具

这四个文件解决同一个问题：**把"我做了个 AI 问答系统"变成"我有数字证明它做得怎么样"。**

| 文件 | 干什么 | 什么时候用 |
| --- | --- | --- |
| `load_test.py` | 接口压测，出 QPS / 延迟分位数 / 首字延迟 | 想知道系统能扛多少并发 |
| `build_eval_set.py` | 从新闻库半自动生成评测集草稿 | 建评测集的第一步（关键：保证题目有对应的真实新闻） |
| `eval_set.json` | 评测集（模板 + 字段说明） | 生成草稿后，用它对照字段格式 |
| `run_eval.py` | 跑评测集并打分，出报告 | 想知道 RAG 改完到底好了多少 |
| `test_smoke.py` | **pytest 冒烟测试**（20 个用例） | 想在 PyCharm 里点绿色三角跑；也适合改完代码回归一下 |

---

## 快速上手（三步）

```bash
# 前置：服务要起着（另开一个终端）
uvicorn main:app --reload

# 第 1 步：先压一轮只读接口，确认脚本通
python text/load_test.py --scenario news --users 30 --duration 20

# 第 2 步：生成评测集草稿
python text/build_eval_set.py --limit 15 --use-llm

# 第 3 步：人工核对草稿 → 改名成 eval_set.json → 跑评测
python text/run_eval.py --accounts 3
```

跑完你会得到两个报告：`text/load_test_*.json` 和 `text/eval_report_*.md`。

---

## 在 PyCharm 里怎么跑

### 第 0 步：确认解释器指向项目所用的 Python

本项目**不使用项目内虚拟环境**（原先的 `.venv` 是空壳，已删除），统一使用本机解释器
**`D:\python\python.exe`**（Python 3.13.9），依赖装在它的 `Lib\site-packages` 下。

依赖清单一律以项目根目录的 **`requirements.txt`** 为准，缺包时装：

```powershell
D:\python\python.exe -m pip install -r requirements.txt
```

`File → Settings → Project: backend → Python Interpreter`，
选 **`D:\python\python.exe`**。

选错解释器会报 `ModuleNotFoundError: No module named 'httpx'` —— 因为依赖装在 `D:\python\Lib\site-packages` 下。

### 第 1 步：把服务跑起来（两种方式）

**方式 A（推荐，最省事）**：`Alt + F12` 打开 Terminal，敲

```bash
uvicorn main:app --reload
```

**方式 B（配成 Run Configuration，以后一键启动）**：

`Run → Edit Configurations → + → Python`，按下表填：

| 字段 | 值 |
| --- | --- |
| Name | `server` |
| 顶部选 | **Module name**（不是 Script path） |
| Module name | `uvicorn` |
| Parameters | `main:app --reload --host 127.0.0.1 --port 8000` |
| Working directory | 项目根目录（`backend`） |

### 第 2 步：跑冒烟测试（绿色三角）

`test_smoke.py` 是 **pytest 用例**，所以 PyCharm 会直接显示绿色三角：

- **跑全部**：右键 `test_smoke.py` → `Run 'pytest in test_smoke.py'`
- **只跑一个**：点某个 `test_xx` 左边的绿色三角
- **调试**：在用例里打断点，然后点虫子图标（Debug）——比看报告直观得多，比如在 `score()` 或 `run_item()` 里断点，能看清每一题为什么判挂
- **只跑一类**：在 Run Configuration 的 `Additional Arguments` 里填 `-k news`

**会调用大模型的用例（17~20）默认跳过**。要跑它们，在 Run Configuration 里加环境变量：

```
RUN_LLM_TESTS=1
```

（`Edit Configurations → Environment variables`）

### 第 3 步：跑压测和评测（这两个是「工具」，不是「测试」）

`load_test.py` / `run_eval.py` / `build_eval_set.py` 需要在命令行带参数，**它们没有绿色三角可点**。两个办法：

**办法 A（推荐）**：`Alt + F12` 用 Terminal，直接敲命令。参数多的时候这个最灵活。

**办法 B**：给常用的那几条命令各配一个 Run Configuration：

| Name | Script path | Parameters |
| --- | --- | --- |
| `压测-新闻` | `text/load_test.py` | `--scenario news --users 30 --duration 20` |
| `压测-聊天` | `text/load_test.py` | `--scenario chat --users 10 --duration 60 --accounts 10` |
| `生成评测集` | `text/build_eval_set.py` | `--limit 15 --use-llm` |
| `跑评测` | `text/run_eval.py` | `--accounts 3` |

三个都要把 **Working directory 设成项目根目录**，否则 `build_eval_set.py` 找不到项目模块。

### 第 4 步：看报告

- `eval_report_*.md` —— PyCharm 打开后点右上角 **Preview** 看渲染效果
- `load_test_*.json` —— PyCharm 会自动格式化，想折叠看可以按 `Ctrl + -`

### 顺手要改的两个设置

| 设置项 | 位置 | 改成 |
| --- | --- | --- |
| 文件编码 | Settings → Editor → File Encodings | 全部 **UTF-8**（否则中文注释和输出乱码） |
| 实时输出 | Run Configuration → 勾选 `Emulate terminal in output console` | 勾上。压测和评测是边跑边打印，不勾会等到全部结束才一次性吐出来 |

---

## 一、压测脚本 `load_test.py`

### 四个场景

| 场景 | 压什么 | 要不要账号 |
| --- | --- | --- |
| `news` | 新闻分类 / 列表 / 详情（只读） | 不用 |
| `chat` | 聊天流式接口（最重，走 LLM） | 要 |
| `login` | 登录（bcrypt 校验，吃 CPU） | 要 |
| `mixed` | 按权重混合：新闻 4 : 聊天 3 : 登录 1 | 要 |

### 常用参数

```bash
python text/load_test.py \
  --scenario news \        # 场景
  --users 50 \             # 并发协程数
  --duration 30 \          # 压多少秒
  --base-url http://127.0.0.1:8000
```

聊天场景额外两个：

```bash
python text/load_test.py --scenario chat --users 10 --duration 60 \
  --accounts 10 \                  # 开 10 个测试账号
  --sessions-per-account 2         # 每个账号预建 2 个会话
```

### ⚠️ 聊天压测必须知道的一件事

你有两个限流/配额约束会直接影响压测结果：

1. **聊天限流是「每用户每分钟 20 次」**（`cache/chat_cache.py` 的 `RATELIMIT_MAX`）。
   所以压聊天时的吞吐天花板 = `20 × 账号数` 次/分钟。
   - 想要更大的压力 → 加大 `--accounts`（每个账号独立配额）
   - 想测聊天接口的真实吞吐上限 → 临时把 `RATELIMIT_MAX` 改成 `999999`，**压完记得改回来**
2. **429 不算失败。** 脚本会把 429 单独统计成「限流命中」。
   这不是 bug，反而是个可以用在面试里的发现：
   > "压测发现限流阈值成了聊天接口的吞吐天花板，也验证了限流确实生效。"

### 结果怎么读

```
平均 QPS                205.8      ← 每秒成功处理多少请求
延迟 P50/P90/P95/P99    98.1 / 245.6 / 380.2 / 612.4
首字延迟 P50/P95 (ms)   412.0 / 890.5   ← 只有流式接口才有这行
```

**看数先看这三件事：**

1. **QPS 是不是随并发线性涨？** 不是的话，拐点在哪 —— 那里就是瓶颈。
2. **P99 是 P50 的几倍？** 差 3 倍以内算健康；差 10 倍说明有请求在排队或撞到了锁/阻塞。
3. **加了并发之后 P95 是不是崩了？** 如果是，去查异步链路里有没有同步阻塞调用。

**建议至少压两组对比：**

```bash
python text/load_test.py --scenario news --users 10  --duration 20
python text/load_test.py --scenario news --users 100 --duration 20
```
两组数字放在一起，就能写出"单机 QPS 从 X 提到 Y"这种话 —— 比单独一个数字有说服力得多。

---

## 二、评测集怎么建

### 为什么必须从真实新闻出题

最容易踩的坑是**凭想象出题**：出的题库里根本没有对应新闻，测出来的低分只能说明题目不合格，说明不了系统差。

所以流程是：`build_eval_set.py` 从 `news` 表按分类抽样取真实新闻 → 围绕真实新闻出题 → 每条题目都带 `reference`（答案出处）→ 这样"答错"才是真的答错。

### 两种生成方式

```bash
# A. 只导出素材，题目留 TODO 自己写（不需要 API Key，最保险）
python text/build_eval_set.py --limit 20

# B. 让模型自动出题 + 提要点（需要 .env 里的 DASHSCOPE_API_KEY）
python text/build_eval_set.py --limit 15 --use-llm

# C. 按分类均衡抽样，避免某一类新闻霸占整个评测集
python text/build_eval_set.py --per-category 3 --use-llm
```

### ⚠️ 自动生成的草稿必须人工过一遍

重点核对三件事：

1. **`expected_keywords` 是不是真的能判定对错。** 好的要点是具体信息（人名、机构、地点、数字、结论）；坏的要点是"新闻""报道""相关"这类任何回答都有的词 —— 用了空词就会永远满分，等于没测。
2. **时间限定题的年份写对没有。** 顺手把其他年份填进 `forbidden_keywords` —— 这是抓"混入其他时间段"最有效的手段。
3. **`should_refuse` 设对没有。** 库里确实查不到的题必须设 `true`。

核对完把 `eval_set.draft.json` 改名成 `eval_set.json`。

### 评测集的五类题

| category | 测什么 | 建议条数 |
| --- | --- | --- |
| `single_topic` | 单话题问答，测**找得准不准** | 8~10 |
| `time_bound` | 时间限定，测**会不会把别的时间段的新闻混进来**（当前设计最容易翻车的一类） | 5~8 |
| `follow_up` | 追问，测**多轮记忆有没有断点** | 4~6 |
| `out_of_scope` | 库里没有的问题，测**会不会编**（编造率的主要来源） | 6~8 |
| `chitchat` | 闲聊，测**有没有走错分支**（闲聊不该去检索、不该插播新闻） | 4~5 |

**合计 30~50 条就够用了**，不用贪多。重点是每一类都要有，不然你只会看到"平均分还行"，看不出问题在哪。

---

## 三、打分脚本 `run_eval.py`

```bash
# 先跑 3 条确认接口和账号都通
python text/run_eval.py --max-items 3

# 全量（3 个账号，绕开 20 次/分钟限流）
python text/run_eval.py --accounts 3

# 只盯某一类
python text/run_eval.py --category time_bound
```

### 打分口径

每条题按三件事判定是否通过：

| 判定项 | 规则 | 抓什么问题 |
| --- | --- | --- |
| 要点命中 | `expected_keywords` 命中比例 ≥ `expected_hit_ratio` | 答非所问、检索没召回对 |
| 禁词违规 | `forbidden_keywords` 出现任意一个 → 直接判挂 | 时间限定题里混入其他时间段 |
| 拒答正确性 | `should_refuse=true` 却没拒答 → 判挂 | **编造的最强信号** |

另外**单独统计**「疑似误拒」——不该拒答的题却出现了拒答话术。这类不计挂，但它是覆盖率损失，值得盯。

### 报告怎么读

```
通过率              27/32  (84.4%)     ← 总体
平均要点命中率       71.2%              ← 比通过率更能反映"答得全不全"
禁词违规次数         3                 ← 每一个都值得去查为什么
拒答正确率          6/7  (85.7%)       ← 有没有硬编
疑似误拒            4                 ← 拒太多了，覆盖率在掉
首字延迟 P50/P95    412 / 890 ms
```

**按分类看通过率才是重点。** 典型情况是这样：

- `out_of_scope` 通过率低 → prompt 里"禁止编造、允许说不知道"的约束没生效
- `time_bound` 通过率低 → 检索那段时间排序的问题（见面试文档 Q2）
- `follow_up` 通过率低 → 多轮历史没正确传进模型

**这份分类通过率表，就是你改进路线的地图。**

---

## 四、怎么把结果写进简历

**用「动作 + 技术 + 数字」的格式，每条都要有数字。**

✅ 好的写法：

> - 独立完成后端与 AI 助手，端到端跑通用户/新闻/收藏/历史 + 智能问答；单机压测 QPS 达 X（30 并发），首字延迟 P95 从 3s 降至 400ms（SSE 流式 + 全链路异步）
> - 构建站内新闻向量知识库（N 条），用文件指纹做增量索引，内容变更才重建；embedding 结果缓存命中率 Z%
> - 搭建 N 条评测集量化效果，定位并修复"检索按时间倒排把高相关旧闻挤出 Top-K"的问题，要点命中率从 A% 提升至 B%
> - 定位并修复三处隐患：越权删除会话消息、异步链路阻塞事件循环、ORM 默认值导致时间戳冻结

❌ 差的写法（堆名词、没数字）：

> 使用 FastAPI + LangGraph 实现了基于 RAG 的智能问答系统，支持多轮对话、月度报告、SSE 流式输出。

### 三条纪律

1. **不要编数字。** 压测出来的数字会有波动，汇报时说明环境和条件（单机、什么配置、什么并发），比一个漂亮但说不清来源的数字安全得多。
2. **改前后都跑一遍，数字才有对比意义。** 比如 `chunk_size` 从 200 改到 600，评测集通过率从多少涨到多少 —— 这个"涨幅"比绝对值有说服力。
3. **报告文件留着。** 面试时被问"这个数字哪来的"，能当场翻出 `eval_report_*.md`，可信度完全不同。

---

## 五、常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| `连不上 http://127.0.0.1:8000` | 服务没起。先 `uvicorn main:app --reload` |
| **服务没起，却报 `502` 或返回一堆 502** | **本机开着代理软件。** Windows 的 `HTTP_PROXY`/`HTTPS_PROXY` 环境变量会让 localhost 请求被代理截走，把"连接被拒绝"变成一个 502 响应，看起来像服务自身出错。三个办法：<br>① 脚本已加 `trust_env=False`，不会再读代理（如果你在别的脚本里手写 httpx，记得也加）<br>② 设环境变量 `NO_PROXY=127.0.0.1,localhost`<br>③ 临时关掉代理软件 |
| 报 `ModuleNotFoundError: No module named 'httpx'` | PyCharm 解释器没指到 `D:\python\python.exe`。见上文「第 0 步」 |
| 报 `No module named 'pytest'` | 装一下：`D:\python\python.exe -m pip install -r requirements.txt` |
| `test_smoke.py` 左边不显示绿色三角 | ① pytest 没装（见上一条）② 解释器不对 ③ 右键该文件选 `Run 'pytest in ...'` 触发一次即可 |
| `test_smoke.py` 全部显示 `skipped` | 服务没启动。这是**故意设计的**——本地没起服务时给一片红没有意义 |
| `没有可用账号` / 账号准备失败 | MySQL 没起，或者 `config/db_config.py` 里的连接串不对 |
| 聊天压测里出现大量 `429` | 正常的限流。加大 `--accounts`，或临时调大 `RATELIMIT_MAX` |
| `build_eval_set.py` 报 `新闻表里没查到数据` | `news` 表是空的，先往库里灌点新闻 |
| `build_eval_set.py` 的 SQL 日志刷屏 | `config/db_config.py` 里 `echo=True`。脚本里已经用 `logging.getLogger("sqlalchemy.engine").setLevel(WARNING)` 压掉了，如仍刷屏检查那行是否被改动 |
| 控制台中文乱码 | 脚本已经强制切到 UTF-8；如果还乱，先执行 `chcp 65001`，并把 PyCharm 的文件编码设成 UTF-8 |
| `run_eval.py` 提示有 TODO 占位 | 草稿还没填完。这些条目仍会跑，但要点命中率没意义（TODO 关键字被自动忽略） |
| 不想让 `text/` 被 Git 跟踪 | 在根目录 `.gitignore` 里加 `text/__pycache__/`、`.pytest_cache/`、`text/*_report_*.md`、`text/*_report_*.json`、`text/eval_set.draft.json` |
