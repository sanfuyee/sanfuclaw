# 后台部署指南

把 Sanfuclaw 作为常驻服务在自己机器上跑，分两个进程：

- **agent** — `sanfuclaw start --channel all`，启动 config 中声明的所有消息渠道（Telegram、微信、Discord 等）
- **serve** — `sanfuclaw serve`，启动网关（WebChat UI + REST + WebSocket）

只要其中一项的就只装一个 service。

`--channel all` 会同时拉起 CLI 渠道，但在没有 tty 的后台环境里 CLI 会立即 EOF 退出，不影响其他渠道。本文档配置里通过 `StandardInput=null` 显式断开 stdin。

---

## 通用准备

把代码装到独立 venv，避免污染系统 Python，venv 路径后面会写死到 service 里。
下文以仓库目录 `~/sanfuclaw` 为例，venv 放在 `~/sanfuclaw/.venv`（和 README 的安装步骤保持一致）。

```bash
git clone https://github.com/sanfuyee/sanfuclaw.git ~/sanfuclaw
cd ~/sanfuclaw

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

确认 CLI 能用（不激活 venv 也能直接调）：

```bash
~/sanfuclaw/.venv/bin/sanfuclaw --help
```

第一次运行任何 `sanfuclaw` 子命令会自动生成 `~/.sanfuclaw/config.json` 模板，按需填好 `llm.api_key`、`channels.*`、`mcp.servers.*` 即可。

---

## Linux：systemd（推荐）

用「用户级 service」（放在 `~/.config/systemd/user/`）就够了，不需要 root，开机自启靠 linger。

### 1. 一次性开启 linger

让你的 user service 在没登录时也运行：

```bash
sudo loginctl enable-linger $USER
mkdir -p ~/.config/systemd/user
```

### 2. agent service

`~/.config/systemd/user/sanfuclaw-agent.service`

```ini
[Unit]
Description=Sanfuclaw agent (channels)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=%h/sanfuclaw/.venv/bin/sanfuclaw start --channel all
WorkingDirectory=%h
StandardInput=null
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

### 3. serve service

`~/.config/systemd/user/sanfuclaw-serve.service`

```ini
[Unit]
Description=Sanfuclaw gateway (WebChat/REST/WS)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=%h/sanfuclaw/.venv/bin/sanfuclaw serve
WorkingDirectory=%h
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

### 4. 启用并启动

```bash
systemctl --user daemon-reload
systemctl --user enable --now sanfuclaw-agent sanfuclaw-serve
```

### 5. 日常运维

```bash
systemctl --user status   sanfuclaw-agent
systemctl --user restart  sanfuclaw-agent sanfuclaw-serve   # 改完 config 重启
systemctl --user stop     sanfuclaw-agent
journalctl --user -u sanfuclaw-agent -f                     # 实时日志
journalctl --user -u sanfuclaw-agent --since "1 hour ago"
```

可选别名（写到 `~/.bashrc` 或 `~/.zshrc`）：

```bash
alias sfc-log='journalctl --user -u sanfuclaw-agent -f'
alias sfc-restart='systemctl --user restart sanfuclaw-agent sanfuclaw-serve'
alias sfc-status='systemctl --user status sanfuclaw-agent sanfuclaw-serve'
```

---

## macOS：launchd

放在 `~/Library/LaunchAgents/`，无需 sudo。

### 1. agent

`~/Library/LaunchAgents/com.sanfuclaw.agent.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.sanfuclaw.agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOUR_USER/sanfuclaw/.venv/bin/sanfuclaw</string>
    <string>start</string>
    <string>--channel</string><string>all</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardInPath</key><string>/dev/null</string>
  <key>StandardOutPath</key><string>/Users/YOUR_USER/.sanfuclaw/agent.log</string>
  <key>StandardErrorPath</key><string>/Users/YOUR_USER/.sanfuclaw/agent.err</string>
  <key>WorkingDirectory</key><string>/Users/YOUR_USER</string>
</dict>
</plist>
```

### 2. serve

`~/Library/LaunchAgents/com.sanfuclaw.serve.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.sanfuclaw.serve</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOUR_USER/sanfuclaw/.venv/bin/sanfuclaw</string>
    <string>serve</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/YOUR_USER/.sanfuclaw/serve.log</string>
  <key>StandardErrorPath</key><string>/Users/YOUR_USER/.sanfuclaw/serve.err</string>
  <key>WorkingDirectory</key><string>/Users/YOUR_USER</string>
</dict>
</plist>
```

把 `YOUR_USER` 替换成你自己的用户名（`echo $USER` 拿到）。

### 3. 加载与管理

```bash
launchctl load   ~/Library/LaunchAgents/com.sanfuclaw.agent.plist
launchctl load   ~/Library/LaunchAgents/com.sanfuclaw.serve.plist

launchctl list | grep sanfuclaw                                    # 看进程
launchctl unload ~/Library/LaunchAgents/com.sanfuclaw.agent.plist  # 停
tail -f ~/.sanfuclaw/agent.log                                     # 看日志
```

改完 config 要 unload + load 才能生效。

---

## 临时方案

不想配 daemon、就想跑起来看看：

```bash
nohup ~/sanfuclaw/.venv/bin/sanfuclaw start --channel all \
  < /dev/null > ~/.sanfuclaw/agent.log 2>&1 &
nohup ~/sanfuclaw/.venv/bin/sanfuclaw serve \
  < /dev/null > ~/.sanfuclaw/serve.log 2>&1 &
```

停掉：`pkill -f sanfuclaw`。关终端进程就没了。

---

## 注意事项

- **config 改完要重启进程**，没有热加载
- **Telegram/微信渠道**需要在 `~/.sanfuclaw/config.json` 里配好 token / 凭据；微信首次需要 `sanfuclaw weixin-login` 扫码
- **网关对外暴露**：默认 `gateway.host = 127.0.0.1`，只能本机访问。要远程访问改成 `0.0.0.0` 并在防火墙放行 `gateway.port`（默认 30423）；公网暴露建议前面套 nginx + HTTPS，不要裸跑
- **MCP server 进程**由 sanfuclaw 自己启动管理，不需要单独配 service
- **数据库与会话**默认在 `~/.sanfuclaw/`，备份直接打包这个目录
