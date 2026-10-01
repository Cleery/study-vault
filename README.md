# 数学题库自习室

面向个人使用的数学分析与高等代数考研题库。项目使用 Django、SQLite、Markdown、LaTeX 和本地图片存储，提供题目录入、知识卡片、层级标签、组合检索、复习计划、统计及加密备份。

## 功能

- 题目支持 Markdown、LaTeX 与多张 PNG、JPEG、WebP 图片
- 科目和章节使用结构化字段，标签用于方法、题型、来源与错误类型
- 定义、定理、引理、判别法和公式可整理为知识卡片
- 关键词、科目、章节、标签、知识卡片、掌握程度和到期状态可组合检索
- 复习结果自动更新掌握状态和下次复习时间
- 支持版本化 ZIP 导出导入、统计页面与 age 加密备份
- 生产环境使用 Nginx Basic Auth、HTTPS、Gunicorn 和 systemd

## 本地运行

需要 Python 3.11 或更高版本。

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Linux：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

本地 `.env` 至少调整以下值：

```dotenv
SECRET_KEY=local-development-secret
DEBUG=true
DEV_AUTH_BYPASS=true
ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_PATH=db.sqlite3
MEDIA_ROOT=media
STATIC_ROOT=staticfiles
CSRF_COOKIE_SECURE=false
SESSION_COOKIE_SECURE=false
SECURE_SSL_REDIRECT=false
SECURE_HSTS_SECONDS=0
SECURE_HSTS_INCLUDE_SUBDOMAINS=false
SECURE_HSTS_PRELOAD=false
```

初始化并启动：

```bash
python manage.py migrate
python manage.py seed_system_data
python manage.py runserver 127.0.0.1:8000
```

访问 `http://127.0.0.1:8000/`。本地开发服务器没有 Basic Auth，生产访问控制由 Nginx 提供。

## 测试与检查

```bash
python -m pytest -q --basetemp .runtime/pytest
python manage.py check
python manage.py collectstatic --noinput
```

生产设置检查：

```bash
python deploy/check-production-config.py
python manage.py check --deploy
```

## 导入、导出与备份

导出完整题库包：

```bash
python manage.py export_bundle --output backups/question-bank.zip
```

导入题库包：

```bash
python manage.py import_bundle --input backups/question-bank.zip
```

加密备份依赖 `age`，并要求 `.env` 中配置 `BACKUP_AGE_PUBLIC_KEY` 和 `BACKUP_OFFLINE_PATH`：

```bash
python manage.py backup_bundle --output-dir backups --keep 14
```

Ubuntu 22.04 的用户、目录权限、HTTPS、Basic Auth、systemd 定时器和恢复演练步骤见 [deploy/README.md](deploy/README.md)。

## 目录

```text
config/                  Django 配置
question_bank/           题库业务、模板和管理命令
static/question_bank/    样式、交互和角色素材
deploy/                  Ubuntu、Nginx、systemd 与恢复脚本
tests/                   跨模块验收测试
```
