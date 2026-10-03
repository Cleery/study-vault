# 待复习、标签与统计工作台实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按已确认的分区工作台设计调整待复习、标签管理和统计页面，保留原有数据与业务规则。

**Architecture:** 三个页面分别修改现有 Django 视图和模板。标签页增加一个只处理 GET 状态的展示辅助模块和独立的确认页，实际写入仍通过现有 POST 服务函数。页面样式放入独立 CSS，避免继续扩大共享样式。

**Tech Stack:** Django 5.2、Django Templates、原生 CSS、pytest、SQLite。

**Spec:** `docs/superpowers/specs/2026-10-02-review-tags-stats-workbench-design.md`

---

## 工作区约束

当前工作树 `C:/Users/14633/study-vault/.worktrees/math-question-bank` 已有大量未提交改动。实施前执行 `git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank status --short`，只编辑本计划列出的文件。不得清理、回退或整目录暂存。若需要提交，只暂存本任务明确修改的文件；已有同文件改动与本任务交织时暂停提交并向用户说明。测试在 Windows 下使用工作树内的独立 `--basetemp`，避免系统临时目录权限错误。

## 文件分工

| 文件 | 责任 |
| --- | --- |
| `question_bank/views.py` | 复习队列状态、标签页面上下文、确认流程、统计展示上下文 |
| `question_bank/urls.py` | 标签归档和合并的 GET 确认页路由 |
| `question_bank/tag_workspace.py` | 解析标签页 GET 参数，构造分类、搜索、选中及返回链接状态 |
| `question_bank/templates/question_bank/review_list.html` | 复习队列工作台 |
| `question_bank/templates/question_bank/tag_manage.html` | 标签分类、列表和详情工作台 |
| `question_bank/templates/question_bank/tag_confirm.html` | 归档或合并的站内确认页 |
| `question_bank/templates/question_bank/stats.html` | 四个数字卡片和两块汇总 |
| `static/question_bank/css/review-workbench.css` | 待复习页样式 |
| `static/question_bank/css/tag-workbench.css` | 标签页与确认页样式 |
| `static/question_bank/css/stats-workbench.css` | 统计页样式 |
| `question_bank/tests/test_review.py` | 队列状态和复习入口测试 |
| `question_bank/tests/test_search_and_tags.py` | 标签状态与写入安全测试 |
| `tests/test_templates.py` | 页面结构与统计展示测试 |

## Task 1：待复习队列工作台

**Files:** `question_bank/views.py`、`question_bank/templates/question_bank/review_list.html`、`static/question_bank/css/review-workbench.css`、`question_bank/tests/test_review.py`、`tests/test_templates.py`。

- [ ] 新增视图与样式契约测试：无效 `queue` 回退 `due`；选择知识卡片后切换三个队列仍保留该 ID；清除筛选链接保留当前队列；题目卡片的“开始复习”指向 `review-detail`；专属 CSS 包含工作台容器与窄屏媒体查询。
- [ ] 分别执行 `pytest -q --basetemp=.pytest-review-red-a question_bank/tests/test_review.py -k 'review_workbench or review_queue_fallback'` 和 `pytest -q --basetemp=.pytest-review-red-b tests/test_templates.py -k review_workbench`。确认测试因缺少页面状态或结构而失败。
- [ ] 在 `review_list` 中将 `queue` 限定为 `due/recent/overdue`，无效值设为 `due`；保持现有三个查询函数和卡片过滤逻辑，传入明确的 `queue_tabs`，每项包含 key、标题、数量和当前状态。
- [ ] 模板添加顶部三个队列入口、知识卡片筛选及清除链接、题目卡片和空状态。入口沿用现有 GET 参数，无 JavaScript 也可使用。引入专属 CSS，窄屏改为单列。
- [ ] 运行上述目标测试，再运行 `pytest -q --basetemp=.pytest-review-all question_bank/tests/test_review.py tests/test_templates.py`，确认通过。执行 `python manage.py check`。

## Task 2：标签页状态与分类列表

**Files:** `question_bank/tag_workspace.py`、`question_bank/views.py`、`question_bank/templates/question_bank/tag_manage.html`、`static/question_bank/css/tag-workbench.css`、`question_bank/tests/test_search_and_tags.py`。

- [ ] 新增测试：默认只显示未归档标签；`kind` 使用 `Tag.KIND_CHOICES` 过滤；`q` 按名称搜索；`show_archived=1` 显示归档标签且页面有可点击的归档入口；`selected` 选中有效标签；无效 ID 返回未选中状态；专属 CSS 包含工作台容器与窄屏媒体查询。组合过滤采用“分类与搜索同时满足”。
- [ ] 执行 `pytest -q --basetemp=.pytest-tag-state-red question_bank/tests/test_search_and_tags.py -k tag_workspace`，确认因缺少状态处理而失败。
- [ ] 在 `tag_workspace.py` 实现一个纯读取函数，输入 `request.GET` 和可选的编辑标签，返回当前分类、搜索词、归档开关、选中标签、分类计数、过滤后的标签列表和可复用的查询字符串。无效 `kind` 回退“全部”；搜索最长 100 字符；`selected` 使用 UUID 安全解析。
- [ ] `tag_manage` 调用该函数，保留现有 `TagForm` 创建及编辑路径。模板提供顶部搜索、新建及“查看已归档标签”入口，左侧分类，右侧列表与选中详情；编辑区域使用现有表单。此阶段“更多操作”区域可以先显示不可提交的说明，Task 3 再接通确认页链接。新增专属 CSS，窄屏按分类、列表、详情顺序排列。
- [ ] 运行目标测试，再运行 `pytest -q --basetemp=.pytest-tag-state-all question_bank/tests/test_search_and_tags.py`。核对新建、编辑的现有测试仍通过。

## Task 3：标签写入后状态及确认流程

**Files:** `question_bank/views.py`、`question_bank/urls.py`、`question_bank/templates/question_bank/tag_confirm.html`、`question_bank/templates/question_bank/tag_manage.html`、`question_bank/tests/test_search_and_tags.py`。

- [ ] 新增测试：归档与合并入口先 GET 确认页；确认页显示来源；合并目标首项为空且服务端拒绝空目标；确认 POST 执行原有归档或合并；失败保留来源、目标与错误；取消链接保留分类、搜索、归档状态及选中标签。
- [ ] 新增测试：访问旧 `tag-edit` URL 时选中其标签；新建或编辑验证失败时保留输入、分类、搜索、`show_archived` 和 `selected`；成功后返回当前分类、搜索与归档显示状态；归档或合并成功后清除已失效的 `selected` 并显示消息。直接 POST 旧路由仍须做服务端校验，不能依赖确认页阻止无效操作。
- [ ] 运行 `pytest -q --basetemp=.pytest-tag-actions-red question_bank/tests/test_search_and_tags.py -k 'tag_confirmation or tag_workspace_write'`，确认预期失败。
- [ ] 在 `urls.py` 新增 `tags/<uuid:pk>/archive/confirm/` 和 `tags/<uuid:pk>/merge/confirm/` 两条 GET 路由。在视图中加载来源与允许的目标，渲染共享确认模板。合并目标选择框以空选项开头，排除来源、已归档和重定向标签；POST 时再次校验目标。
- [ ] 将“更多操作”区域的归档与合并入口接到确认页链接。POST 路由成功后重定向至带有保留筛选参数的标签页；失败时渲染确认页及错误。返回地址只由白名单 GET 或隐藏字段构建，禁止开放重定向。编辑表单失败则在同一工作台显示错误。
- [ ] 运行目标测试，再运行 `pytest -q --basetemp=.pytest-tag-actions-all question_bank/tests/test_search_and_tags.py question_bank/tests/test_crud_views.py`，确认通过。

## Task 4：统计卡片与汇总

**Files:** `question_bank/views.py`、`question_bank/templates/question_bank/stats.html`、`static/question_bank/css/stats-workbench.css`、`tests/test_templates.py`、`tests/test_export_stats.py`。

- [ ] 新增模板与样式契约测试：四个指标都来自 `get_statistics()`；掌握程度显示 `Question.MASTERY_CHOICES` 的中文名；无题目时数字为 0、科目区域显示“暂无数据”；专属 CSS 包含工作台容器与窄屏媒体查询。
- [ ] 执行 `pytest -q --basetemp=.pytest-stats-red tests/test_templates.py -k stats_workbench`，确认缺少新结构或中文标签导致失败。
- [ ] 在 `stats` 视图中根据 `Question.MASTERY_CHOICES` 构造有序的展示行，数值从既有 `questions_by_mastery` 读取。模板呈现四个数字卡片和科目、掌握程度两块汇总。引入专属 CSS，窄屏卡片堆叠。不得修改 `get_statistics()` 的过滤口径或日期计算。
- [ ] 运行目标测试以及 `pytest -q --basetemp=.pytest-stats-all tests/test_templates.py tests/test_export_stats.py`，确认通过。

## Task 5：整体验证与本机预览

**Files:** 只在发现本任务引入的问题时修改上述相关文件。

- [ ] 运行 `pytest -q --basetemp=.pytest-three-pages-final`，记录通过、失败和跳过数。运行 `python manage.py check`、`python manage.py makemigrations --check --dry-run` 和 `python manage.py collectstatic --noinput`。
- [ ] 运行 `git -c safe.directory=C:/Users/14633/study-vault/.worktrees/math-question-bank diff --check`，只审查本任务文件差异，避免把既有改动归入本任务。
- [ ] 使用当前工作树启动或复用开发服务器，分别打开 `/review/`、`/tags/`、`/stats/`。检查桌面和窄屏布局，以及无 JavaScript 下的筛选、确认和提交路径。若浏览器自动化可用，执行已有浏览器测试；无法执行时如实报告。
- [ ] 向用户提供本机预览地址和验证结果。未经用户要求，不部署 Ubuntu 服务器，不清理已有工作树改动。
