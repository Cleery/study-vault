# AI 发布阻断项实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and superpowers:verification-before-completion for every task.

**Goal:** 补齐 AI 分析版本、隐私边界、后台任务、阈值关联、审核闭环和原始响应审计，保证真实 Relay 不占用 Web 请求。

**Architecture:** `analyze_question` 负责幂等创建分析版本，并仅对占位 Provider 或显式注入 Provider 同步执行。真实 Relay 分析保留为数据库中的 pending 记录，由独立 management worker 认领和执行。分析结果与用户校对字段严格分离，候选先在本地检索和截断，再发送给 Provider。

**Tech Stack:** Django 5.2、SQLite、pytest-django、标准库 HTTP、systemd。

## Task 1：版本语义、校对字段与领域过期保护

**Files:**

1. Modify: `question_bank/ai/service.py`
2. Modify: `question_bank/ai/actions.py`
3. Modify: `question_bank/views.py`
4. Modify: `question_bank/templates/question_bank/question_analysis.html`
5. Test: `question_bank/tests/test_ai_service.py`
6. Test: `question_bank/tests/test_crud_views.py`

Steps:

1. 新增失败测试，覆盖 `force=False` 幂等复用、`force=True` 新版本、校对保存新版本、旧分析对象在领域层被拒绝。
2. 新增失败测试，证明模型识别文本只写 `QuestionAIAnalysis`，题目校对字段保持原值；题目字段为空时页面表单从最新分析展示建议文本。
3. 实现 `force` 参数、页面明确 force 值、校对后强制创建版本、`locked_analysis` 与 `latest` 比较。
4. 运行服务和视图测试。
5. 提交 `fix: enforce AI analysis version ownership`。

## Task 2：本地候选边界、请求大小与原始响应审计

**Files:**

1. Modify: `question_bank/ai/config.py`
2. Modify: `question_bank/ai/providers.py`
3. Modify: `question_bank/ai/service.py`
4. Modify: `.env.example`
5. Modify: `deploy/README.md`
6. Test: `question_bank/tests/test_ai_provider.py`
7. Test: `question_bank/tests/test_ai_service.py`
8. Test: `tests/test_ai_security.py`

Steps:

1. 新增失败测试，构造整科大量知识卡片，断言 Provider 只收到本地命中的有限候选。
2. 新增 `AI_MAX_CANDIDATE_CARDS` 和 `AI_MAX_REQUEST_BYTES` 配置失败测试。
3. 新增请求 JSON 超限失败测试，断言网络 transport 未调用。
4. 新增有效 Relay envelope 和 schema 失败 envelope 的脱敏审计测试，断言原始响应受字节上限并由现有 30 天命令清理。
5. 实现本地候选选择、请求限制、Provider 审计包装和异常携带的脱敏 raw response。
6. 运行 Provider、服务和安全测试。
7. 提交 `fix: bound AI candidate and audit payloads`。

## Task 3：可恢复数据库任务队列与独立 worker

**Files:**

1. Modify: `question_bank/models.py`
2. Create: `question_bank/migrations/0011_questionaianalysis_task_state.py`
3. Modify: `question_bank/ai/service.py`
4. Create: `question_bank/management/commands/process_ai_tasks.py`
5. Modify: `question_bank/views.py`
6. Create: `deploy/ai-worker.service`
7. Modify: `deploy/README.md`
8. Modify: `tests/test_deployment.py`
9. Test: `question_bank/tests/test_ai_service.py`
10. Test: `question_bank/tests/test_crud_views.py`

Steps:

1. 新增失败测试，断言 Relay 页面请求只创建 pending 任务且不调用 Provider，Placeholder 仍可同步。
2. 新增 worker 认领、失败退避、达到上限失败、陈旧 analyzing 重置、重启恢复和重复执行幂等测试。
3. 增加 `attempt_count`、`next_attempt_at`、`started_at` 字段和迁移。
4. 将 Provider 执行提取为指定分析版本的 worker 函数，认领时原子更新状态。
5. 实现 `process_ai_tasks --once` 与持续轮询模式。
6. 增加独立 systemd worker，并从部署文档移除同步请求 timeout 依赖说明。
7. 运行迁移、服务、视图和部署测试。
8. 提交 `feat: process AI analysis in database worker`。

## Task 4：阈值自动关联、审核闭环与端到端回归

**Files:**

1. Modify: `question_bank/ai/service.py`
2. Modify: `question_bank/ai/actions.py`
3. Modify: `question_bank/views.py`
4. Modify: `question_bank/templates/question_bank/question_analysis.html`
5. Test: `question_bank/tests/test_ai_service.py`
6. Test: `question_bank/tests/test_crud_views.py`
7. Test: `tests/test_ai_security.py`

Steps:

1. 新增失败测试，覆盖知识卡片候选三个阈值区间。
2. 高置信度只对最新版本和当前指纹自动关联，写入 `AIReviewOwnership` 与可撤销 confirm action。
3. 中置信度保持普通审核；低置信度增加明确标记且不自动关联。
4. 标签与缺失卡片始终只给建议，不自动确认或发布。
5. 增加完成状态计算，无候选或所有可操作候选已有审核动作时，将分析和题目更新为 completed。
6. 新增端到端测试，覆盖入队、worker、模型建议展示、保存校对、新版本、自动关联、撤销、全部审核完成。
7. 检查并修复文件末尾换行差异。
8. 运行 Provider、服务、安全、部署、Django check、迁移检查和全量测试。
9. 提交 `feat: complete AI review workflow`。

