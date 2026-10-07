# Ubuntu 22.04 部署

以下示例使用：

- 域名：`example.com`
- 服务用户：`mathvault`
- 程序目录：`/srv/math-question-bank/current`
- 虚拟环境：`/srv/math-question-bank/venv`
- 持久数据：`/srv/math-question-bank/shared`
- 本机备份：`/var/backups/math-question-bank`
- 离机挂载点：`/mnt/offline/math-question-bank`

部署前请将配置中的域名和路径替换为实际值。SQLite 仅运行一个 Gunicorn worker。

## 1. 安装系统依赖

```bash
sudo apt update
sudo apt install -y python3-venv nginx apache2-utils certbot python3-certbot-nginx age
sudo useradd --system --user-group --home /srv/math-question-bank --shell /usr/sbin/nologin mathvault
sudo install -d -o mathvault -g www-data -m 0750 \
  /srv/math-question-bank/current \
  /srv/math-question-bank/shared/media \
  /srv/math-question-bank/shared/static
sudo install -d -o mathvault -g mathvault -m 0700 /var/backups/math-question-bank
sudo install -d -o mathvault -g mathvault -m 0700 /mnt/offline/math-question-bank
```

将代码放到 `/srv/math-question-bank/current`，代码目录可由部署账户管理。运行数据目录必须归 `mathvault` 所有。

## 2. Python 环境

```bash
sudo -u mathvault python3 -m venv /srv/math-question-bank/venv
sudo -u mathvault /srv/math-question-bank/venv/bin/pip install -r /srv/math-question-bank/current/requirements.txt
```

## 3. 环境变量

生成 Django 密钥：

```bash
/srv/math-question-bank/venv/bin/python -c "import secrets; print(secrets.token_urlsafe(64))"
```

生成 age 密钥。私钥只用于恢复，放在独立且仅 root 可读的位置：

```bash
sudo age-keygen -o /root/math-question-bank-age-key.txt
sudo chmod 600 /root/math-question-bank-age-key.txt
sudo age-keygen -y /root/math-question-bank-age-key.txt
```

以 `.env.example` 为基础创建 `/etc/math-question-bank.env`，填入真实域名、Django 密钥、age 公钥和离机路径：

```bash
sudo install -o root -g mathvault -m 0640 .env.example /etc/math-question-bank.env
sudo editor /etc/math-question-bank.env
sudo -u mathvault bash -lc 'set -a; source /etc/math-question-bank.env; set +a; cd /srv/math-question-bank/current; /srv/math-question-bank/venv/bin/python deploy/check-production-config.py'
```

`DEBUG` 和 `DEV_AUTH_BYPASS` 必须为 `false`。`SECRET_KEY` 至少包含 50 个字符且不能使用 Django 的不安全前缀。生产预检还要求 `BACKUP_AGE_PUBLIC_KEY` 与 `BACKUP_OFFLINE_PATH` 存在。

AI 默认关闭。启用前需要确认 Provider 对上传图片、识别文本和模型响应的数据保留政策与训练政策，并在 `/etc/math-question-bank.env` 中设置 `AI_ENABLED=true`、`AI_PROVIDER=relay`、Provider 地址、模型名和 `AI_API_KEY`。`AI_BASE_URL` 可填写服务根地址、以 `/v1` 结尾的 API 根地址，或完整的 `/chat/completions` 地址，应用会统一生成 OpenAI 兼容的 Chat Completions 端点。密钥文件权限保持 `0640`，不得把密钥写入代码、模板、JavaScript 或普通日志。

Relay 请求会发送题目图、解答图、校对文本、个人想法和候选知识卡片内容。启用前需要确认这些内容允许离开服务器。`AI_TIMEOUT_SECONDS` 默认值为 `60`；`AI_MAX_RETRIES` 默认值为 `2`，仅对超时、连接错误、HTTP 429 和临时 5xx 响应重试，重试可能造成 Provider 重复处理或重复计费；`AI_MAX_RESPONSE_BYTES` 默认值为 `2097152`，用于限制模型响应体。`AI_RAW_RESPONSE_RETENTION_DAYS` 默认值为 `30`，分析摘要和人工审核记录不受该期限影响。

## 4. 初始化数据和静态文件

```bash
sudo -u mathvault bash -lc 'set -a; source /etc/math-question-bank.env; set +a; cd /srv/math-question-bank/current; /srv/math-question-bank/venv/bin/python manage.py migrate'
sudo -u mathvault bash -lc 'set -a; source /etc/math-question-bank.env; set +a; cd /srv/math-question-bank/current; /srv/math-question-bank/venv/bin/python manage.py seed_system_data'
sudo -u mathvault bash -lc 'set -a; source /etc/math-question-bank.env; set +a; cd /srv/math-question-bank/current; /srv/math-question-bank/venv/bin/python manage.py collectstatic --noinput'
```

## 5. Basic Auth、Nginx 与 HTTPS

创建唯一用户的密码文件：

```bash
sudo htpasswd -c /etc/nginx/.htpasswd-math-question-bank your-name
sudo chown root:www-data /etc/nginx/.htpasswd-math-question-bank
sudo chmod 0640 /etc/nginx/.htpasswd-math-question-bank
```

先将 `deploy/nginx.conf` 中的 `example.com` 替换为真实域名，再安装配置并申请证书：

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/math-question-bank
sudo ln -s /etc/nginx/sites-available/math-question-bank /etc/nginx/sites-enabled/math-question-bank
sudo mkdir -p /var/www/certbot
sudo certbot certonly --webroot -w /var/www/certbot -d example.com
sudo nginx -t
sudo systemctl reload nginx
```

Basic Auth 配置位于 HTTPS `server` 级别，应用、媒体和静态文件均受保护。Nginx 单次请求限制为 12 MB，应用仍限制单张图片本体不超过 10 MB。

## 6. Gunicorn 服务

```bash
sudo cp deploy/gunicorn.service /etc/systemd/system/math-question-bank.service
sudo systemctl daemon-reload
sudo systemctl enable --now math-question-bank.service
sudo systemctl status math-question-bank.service
curl -u your-name https://example.com/health/
```

Gunicorn 的 worker timeout 固定为 `240` 秒。默认 `AI_TIMEOUT_SECONDS=60` 且最多重试 2 次，最坏请求等待约为 180 秒，剩余时间用于失败状态和分析结果回写。若提高 Provider 超时或重试次数，应按总尝试次数同步提高 Gunicorn timeout，并保留充足的数据库回写余量，否则同步分析请求可能在状态写回前被终止。

日志由 journald 保存：

```bash
journalctl -u math-question-bank.service -f
```

Ubuntu 默认的 journald 和 Nginx logrotate 配置负责日志轮转。可按磁盘容量调整 `/etc/systemd/journald.conf` 与 `/etc/logrotate.d/nginx`。

## 7. 每日加密备份

确认 `/mnt/offline/math-question-bank` 是独立磁盘或远程挂载点。随后安装并启动定时器：

```bash
sudo cp deploy/backup.service /etc/systemd/system/math-question-bank-backup.service
sudo cp deploy/backup.timer /etc/systemd/system/math-question-bank-backup.timer
sudo systemctl daemon-reload
sudo systemctl enable --now math-question-bank-backup.timer
sudo systemctl start math-question-bank-backup.service
sudo systemctl status math-question-bank-backup.service
systemctl list-timers math-question-bank-backup.timer
```

备份服务会停止 Gunicorn，创建 SQLite 一致性快照、复制媒体文件、生成校验清单、使用 age 加密并复制到离机目录，最后重新启动 Gunicorn。成功和失败记录可通过以下命令查看：

```bash
journalctl -u math-question-bank-backup.service
```

## 8. 每月恢复演练

恢复到全新目录，禁止覆盖生产目录：

```bash
sudo install -d -o root -g root -m 0700 /var/lib/math-question-bank-restore-test
sudo BACKUP_AGE_IDENTITY_FILE=/root/math-question-bank-age-key.txt \
  /srv/math-question-bank/current/deploy/restore-backup.sh \
  /mnt/offline/math-question-bank/question-bank-YYYYMMDDTHHMMSS.tar.gz.age \
  /var/lib/math-question-bank-restore-test
```

脚本会验证文件校验和与 SQLite `PRAGMA integrity_check`。演练后还应在隔离环境中指向恢复数据库与媒体目录，启动单独实例检查题目和图片关系。

## 9. 更新与排错

使用 systemd timer 或 cron 每日执行原始响应清理，默认删除超过 30 天的 Provider 原始响应：

```bash
sudo -u mathvault bash -lc 'set -a; source /etc/math-question-bank.env; set +a; cd /srv/math-question-bank/current; /srv/math-question-bank/venv/bin/python manage.py cleanup_ai_raw_responses'
```

可使用 `--days` 临时指定更短或更长的保留天数。修改期限前应重新核对 Provider 的数据保留政策和训练政策。

每次更新执行迁移、静态文件收集和服务重启：

```bash
sudo -u mathvault bash -lc 'set -a; source /etc/math-question-bank.env; set +a; cd /srv/math-question-bank/current; /srv/math-question-bank/venv/bin/python manage.py migrate'
sudo -u mathvault bash -lc 'set -a; source /etc/math-question-bank.env; set +a; cd /srv/math-question-bank/current; /srv/math-question-bank/venv/bin/python manage.py collectstatic --noinput'
sudo systemctl restart math-question-bank.service
sudo nginx -t
```

常用检查：

```bash
sudo systemctl status math-question-bank.service
sudo systemctl status nginx
sudo ss -lx | grep math-question-bank
curl -u your-name https://example.com/health/
```
