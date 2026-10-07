# AI 辅助题目分析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 Django 数学题库中加入可审核的 AI 题目分析闭环，支持占位 Provider、OCR 文本校对、知识卡片候选、标签建议、缺失卡片草稿和可追溯的分析版本。

**Architecture:** 新增独立的 `question_bank/ai` 领域模块，负责配置、Provider、结构化结果校验和分析服务。题目模型只保存分析当前状态和校对文本，分析历史、候选和人工确认保存在独立模型中。第一阶段使用同步模拟 Provider 和关键词候选，所有建议进入审核；真实中转站和后台任务放在后续阶段。

**Tech Stack:** Django 5.2、SQLite、pytest-django、Django JSONField、原生 Django 模板、OpenAI 兼容 HTTP 接口（第二阶段）。

---

## 文件与职责

- Create: `question_bank/ai/__init__.py`，AI 模块入口。
- Create: `question_bank/ai/config.py`，读取环境变量和阈值。
- Create: `question_bank/ai/schemas.py`，分析结果、标签类别、状态和 JSON 校验。
- Create: `question_bank/ai/providers.py`，Provider 协议、占位 Provider 和真实接口的扩展边界。
- Create: `question_bank/ai/service.py`，创建分析版本、关键词候选、调用 Provider、幂等检查和保存结果。
- Create: `question_bank/ai/exceptions.py`，Provider、schema 和并发错误。
- Modify: `question_bank/models.py`，增加题目分析状态、校对文本和分析记录相关模型。
- Create: `question_bank/migrations/0007_question_ai_analysis.py`，数据库迁移。
- Modify: `question_bank/forms.py`，增加校对文本和个人信号字段。
- Modify: `question_bank/views.py`，增加分析启动、校对保存、审核操作和结果上下文。
- Modify: `question_bank/urls.py`，增加分析相关 URL。
- Create: `question_bank/templates/question_bank/question_analysis.html`，分析和审核页面。
- Modify: `question_bank/templates/question_bank/question_detail.html`，增加分析状态与入口。
- Create: `static/question_bank/js/question-analysis.js`，状态刷新、确认和忽略交互。
- Modify: `static/question_bank/css/question-form.css` 或 `static/question_bank/css/app.css`，分析页面样式。
- Create: `question_bank/tests/test_ai_schemas.py`，结构化结果和类别校验。
- Create: `question_bank/tests/test_ai_service.py`，分析服务、匹配、阈值和幂等。
- Modify: `question_bank/tests/test_models.py`，模型状态和唯一约束测试。
- Modify: `question_bank/tests/test_crud_views.py`，校对、启动、审核和失败页面测试。
- Modify: `tests/test_templates.py`，模板契约测试。
- Modify: `.env.example`，加入 AI 占位配置。
- Modify: `deploy/README.md`，说明中转站配置、隐私和关闭 AI 时的行为。

---

### Task 1: 建立配置、状态和结构化结果边界

**Files:**
- Create: `question_bank/ai/__init__.py`
- Create: `question_bank/ai/config.py`
- Create: `question_bank/ai/exceptions.py`
- Create: `question_bank/ai/schemas.py`
- Test: `question_bank/tests/test_ai_schemas.py`
- Modify: `.env.example`

- [ ] **Step 1: Write the failing schema tests**

覆盖：有效结果、未知标签类别、置信度超出 `0..1`、候选数量上限、缺失必填字段、分析状态枚举和配置默认值。

- [ ] **Step 2: Run tests to verify they fail**

Run: `C:\Users\14633\anaconda3\python.exe -m pytest question_bank/tests/test_ai_schemas.py -q --basetemp=.pytest-ai-schema-red`

Expected: import failure，因为 AI 模块尚未创建。

- [ ] **Step 3: Implement minimal schemas and configuration**

实现固定类别 `topic`、`method`、`signal`，限制候选数量为知识点 10、标签 20；配置读取 `AI_ENABLED`、Provider、Base URL、模型、超时和两个阈值。解析失败统一抛出领域异常。

- [ ] **Step 4: Run tests to verify they pass**

Run: `C:\Users\14633\anaconda3\python.exe -m pytest question_bank/tests/test_ai_schemas.py -q --basetemp=.pytest-ai-schema-green`

Expected: 全部通过。

- [ ] **Step 5: Commit**

```powershell
git add question_bank/ai .env.example question_bank/tests/test_ai_schemas.py
git commit -m "feat: add AI analysis schemas and config"
```

### Task 2: 增加分析模型和迁移

**Files:**
- Modify: `question_bank/models.py`
- Create: `question_bank/migrations/0007_question_ai_analysis.py`
- Test: `question_bank/tests/test_models.py`

- [ ] **Step 1: Write failing model tests**

测试 `Question` 的分析状态、校对文本、最新版本；测试分析记录与题目关联、版本唯一性、状态默认值、候选 JSON 保存和重分析历史保留。

- [ ] **Step 2: Run tests to verify they fail**

Run: `C:\Users\14633\anaconda3\python.exe -m pytest question_bank/tests/test_models.py -k ai -q --basetemp=.pytest-ai-model-red`

Expected: 字段或模型不存在。

- [ ] **Step 3: Implement models**

增加：

- `Question.ai_status`，默认 `pending`。
- `Question.recognized_statement`、`Question.recognized_solution`、`Question.personal_signals`。
- `QuestionAIAnalysis`，保存题目、版本、输入指纹、状态、Provider、模型、识别结果、候选结果、原始响应、错误信息、时间和完成时间。
- `QuestionAIAnalysisAction`，保存候选确认、忽略、撤销和用户编辑结果。

使用 `(question, version)` 唯一约束；输入指纹用于幂等；JSON 字段默认空对象；分析删除策略跟随题目归档数据保留规则。

- [ ] **Step 4: Generate and inspect migration**

Run: `C:\Users\14633\anaconda3\python.exe manage.py makemigrations question_bank`

确认迁移只包含 AI 字段和模型，不修改既有附件字段。

- [ ] **Step 5: Run model tests and migration check**

Run: `C:\Users\14633\anaconda3\python.exe -m pytest question_bank/tests/test_models.py -k ai -q --basetemp=.pytest-ai-model-green` and `C:\Users\14633\anaconda3\python.exe manage.py makemigrations --check --dry-run`。

- [ ] **Step 6: Commit**

```powershell
git add question_bank/models.py question_bank/migrations question_bank/tests/test_models.py
git commit -m "feat: store question AI analysis versions"
```

### Task 3: 实现占位 Provider 和分析服务

**Files:**
- Create: `question_bank/ai/providers.py`
- Create: `question_bank/ai/service.py`
- Test: `question_bank/tests/test_ai_service.py`

- [ ] **Step 1: Write failing service tests**

覆盖：AI 关闭时不调用 Provider；占位 Provider 返回固定结构；关键词命中已有知识卡片；标签建议使用三类枚举；高阈值结果暂存为候选；重复输入返回已有分析版本；新校对文本生成新版本；旧版本不能写回。

- [ ] **Step 2: Run tests to verify they fail**

Run: `C:\Users\14633\anaconda3\python.exe -m pytest question_bank/tests/test_ai_service.py -q --basetemp=.pytest-ai-service-red`

Expected: AI 服务和 Provider 不存在。

- [ ] **Step 3: Implement Provider boundary**

定义 `analyze(images, text, cards, personal_notes)` 接口。`PlaceholderProvider` 返回稳定模拟结果。预留 `RelayProvider`，AI 未启用时不得发送网络请求；真实 Provider 尚不执行网络调用。

- [ ] **Step 4: Implement service and idempotency**

服务计算题目图片、校对文本、个人想法和候选卡片的输入指纹。事务内创建唯一版本，调用 Provider 后再次检查版本和输入指纹，只有最新版本可以写回。关键词候选使用知识卡片名称、正式表述、条件、证明和使用信号，不引入向量数据库。

- [ ] **Step 5: Run service tests to verify they pass**

Run: `C:\Users\14633\anaconda3\python.exe -m pytest question_bank/tests/test_ai_service.py -q --basetemp=.pytest-ai-service-green`

- [ ] **Step 6: Commit**

```powershell
git add question_bank/ai question_bank/tests/test_ai_service.py
git commit -m "feat: add placeholder question analysis service"
```

### Task 4: 加入题目分析页面和校对流程

**Files:**
- Modify: `question_bank/forms.py`
- Modify: `question_bank/views.py`
- Modify: `question_bank/urls.py`
- Create: `question_bank/templates/question_bank/question_analysis.html`
- Modify: `question_bank/templates/question_bank/question_detail.html`
- Create: `static/question_bank/js/question-analysis.js`
- Modify: `static/question_bank/css/question-form.css`
- Test: `question_bank/tests/test_crud_views.py`
- Test: `tests/test_templates.py`

- [ ] **Step 1: Write failing view and template tests**

覆盖：详情页显示分析状态；分析页面显示原图、识别文本、个人信号、候选卡片、标签建议和缺失卡片；校对文本保存后版本增加；AI 关闭时显示“等待配置”；失败状态显示错误和重新分析按钮；未经认证的普通访问继续由现有 Nginx 保护。

- [ ] **Step 2: Run tests to verify they fail**

Run: `C:\Users\14633\anaconda3\python.exe -m pytest question_bank/tests/test_crud_views.py -k analysis tests/test_templates.py -k analysis -q --basetemp=.pytest-ai-view-red`

Expected: URL、上下文或模板断言失败。

- [ ] **Step 3: Implement routes and views**

增加：分析页面 GET、校对文本 POST、启动或重新分析 POST、候选确认 POST、候选忽略 POST、缺失卡片草稿 POST。所有操作使用 `require_POST`、题目锁和当前分析版本检查。普通题目保存不调用 AI。

- [ ] **Step 4: Implement templates and browser behavior**

分析页面按四个区域展示原图、校对文本、候选和个人信号。按钮带当前版本和输入指纹，重复点击显示已有结果。JavaScript 只负责刷新状态和提交确认，不保存 API Key。

- [ ] **Step 5: Run view and template tests**

Run: `C:\Users\14633\anaconda3\python.exe -m pytest question_bank/tests/test_crud_views.py -k analysis tests/test_templates.py -k analysis -q --basetemp=.pytest-ai-view-green`

- [ ] **Step 6: Commit**

```powershell
git add question_bank/forms.py question_bank/views.py question_bank/urls.py question_bank/templates/question_bank question_bank/tests/test_crud_views.py tests/test_templates.py static/question_bank
git commit -m "feat: add question analysis review page"
```

### Task 5: 实现候选审核、标签确认和缺失卡片草稿

**Files:**
- Modify: `question_bank/views.py`
- Modify: `question_bank/templates/question_bank/question_analysis.html`
- Modify: `question_bank/models.py` if action constraints need adjustment
- Test: `question_bank/tests/test_ai_service.py`
- Test: `question_bank/tests/test_crud_views.py`

- [ ] **Step 1: Write failing action tests**

覆盖：确认已有卡片幂等关联；忽略候选不产生关联；撤销自动关联；确认标签后才进入正式标签集合；未知类别拒绝；缺失卡片创建草稿且不发布证明；旧版本操作返回冲突。

- [ ] **Step 2: Run tests to verify they fail**

Run: `C:\Users\14633\anaconda3\python.exe -m pytest question_bank/tests/test_ai_service.py question_bank/tests/test_crud_views.py -k "analysis or candidate or suggestion" -q --basetemp=.pytest-ai-action-red`

- [ ] **Step 3: Implement transactional actions**

在事务中锁定题目和分析记录；使用现有 `Question.knowledge_cards` 和 `Question.tags` 关系；标签通过规范化名称和类别查找或创建，建议状态不会直接写入正式标签；缺失卡片创建 `draft=True` 的知识卡片草稿或待建立记录，不自动填入正式证明。

- [ ] **Step 4: Run action tests**

Run: `C:\Users\14633\anaconda3\python.exe -m pytest question_bank/tests/test_ai_service.py question_bank/tests/test_crud_views.py -k "analysis or candidate or suggestion" -q --basetemp=.pytest-ai-action-green`

- [ ] **Step 5: Commit**

```powershell
git add question_bank question_bank/tests
git commit -m "feat: review AI knowledge and tag suggestions"
```

### Task 6: 增加失败、并发、隐私和回归测试

**Files:**
- Modify: `question_bank/tests/test_ai_service.py`
- Modify: `question_bank/tests/test_crud_views.py`
- Create: `tests/test_ai_security.py`
- Modify: `tests/test_templates.py`
- Modify: `deploy/README.md`

- [ ] **Step 1: Write failing regression tests**

覆盖 Provider 超时、Provider 错误、schema 错误、候选检索错误、重复提交、旧版本晚到、错误状态重试、API Key 不进入模板和日志、原始响应保留期限字段、AI 关闭时普通题目保存不受影响。

- [ ] **Step 2: Run tests to verify failures**

Run: `C:\Users\14633\anaconda3\python.exe -m pytest question_bank/tests/test_ai_service.py question_bank/tests/test_crud_views.py tests/test_ai_security.py -q --basetemp=.pytest-ai-regression-red`

- [ ] **Step 3: Implement minimal protections**

补充版本条件更新、过期原始响应清理命令或服务函数、错误信息脱敏、页面访问字段白名单和配置文档。旧结果保留摘要与人工动作，原始响应按默认 30 天过期。

- [ ] **Step 4: Run regression and existing suite**

Run: `C:\Users\14633\anaconda3\python.exe -m pytest question_bank/tests/test_ai_service.py question_bank/tests/test_crud_views.py tests/test_ai_security.py -q --basetemp=.pytest-ai-regression-green` and `C:\Users\14633\anaconda3\python.exe -m pytest -q --basetemp=.pytest-ai-final`。

- [ ] **Step 5: Commit**

```powershell
git add question_bank tests deploy/README.md
git commit -m "test: harden AI analysis failure and concurrency handling"
```

### Task 7: 接入真实 OpenAI 兼容中转站 Provider

**Files:**
- Modify: `question_bank/ai/providers.py`
- Modify: `question_bank/ai/config.py`
- Modify: `requirements.txt` only if an already approved HTTP client is unavailable
- Test: `question_bank/tests/test_ai_provider.py`
- Modify: `deploy/README.md`

- [ ] **Step 1: Write provider contract tests**

使用本地 fake HTTP server 或 mocked transport，验证 Base URL、Bearer Key、模型名、图片编码、多图请求、超时、非 2xx 响应和结构化 JSON 解析。测试中不得使用真实 API Key。

- [ ] **Step 2: Run tests to verify they fail**

Run: `C:\Users\14633\anaconda3\python.exe -m pytest question_bank/tests/test_ai_provider.py -q --basetemp=.pytest-ai-provider-red`

- [ ] **Step 3: Implement RelayProvider**

实现 OpenAI 兼容请求、超时、有限重试、响应大小限制和 schema 校验。图片只从服务端读取，API Key 只从环境变量读取，日志只记录请求 ID 和错误类别，不记录图片、密钥和完整个人想法。

- [ ] **Step 4: Run provider tests and full suite**

Run: `C:\Users\14633\anaconda3\python.exe -m pytest question_bank/tests/test_ai_provider.py -q --basetemp=.pytest-ai-provider-green` and `C:\Users\14633\anaconda3\python.exe -m pytest -q --basetemp=.pytest-ai-provider-final`。

- [ ] **Step 5: Commit**

```powershell
git add question_bank/ai requirements.txt deploy/README.md question_bank/tests/test_ai_provider.py
git commit -m "feat: add OpenAI-compatible relay provider"
```

### Task 8: 发布前检查和部署

**Files:**
- Modify: `README.md` if local setup needs AI configuration notes
- Modify: `deploy/README.md` if deployment sequence changes

- [ ] **Step 1: Run fresh verification**

```powershell
C:\Users\14633\anaconda3\python.exe manage.py check
C:\Users\14633\anaconda3\python.exe manage.py makemigrations --check --dry-run
C:\Users\14633\anaconda3\python.exe -m pytest -q --basetemp=.pytest-ai-release
node --check static/question_bank/js/question-analysis.js
git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank diff --check
```

- [ ] **Step 2: Review configuration and secrets**

确认 `.env.example` 只有占位值，真实 API Key 未进入 Git，生产 `AI_ENABLED=false` 时普通上传不触发模型请求。

- [ ] **Step 3: Commit release documentation**

```powershell
git add README.md deploy/README.md .env.example
git commit -m "docs: document AI analysis deployment"
```

- [ ] **Step 4: Push and deploy only after explicit release request**

推送前检查 GitHub `main` 是否有新提交。服务器先创建数据库快照，拉取新发布目录，运行迁移和静态文件收集，原子切换 `current`，重启服务，验证 `/health/`、认证保护、新建题目页面和分析页面。保留旧发布目录和快照用于回滚。

