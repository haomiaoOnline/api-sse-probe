# Agent 安装、授权与首次验收

这里是**当前版本安装操作说明**。Agent 应先读 [SKILL.md](../SKILL.md) 确认真实执行授权，不能直接把安装当成拨测授权。

## 1. 环境检查与依赖

支持 macOS/Linux 的 Python 3.9+；Windows 原生 v2 尚未验证，不进行 Windows 实时拨测。要求 `git` 用于从 GitHub 获取源码，`python3` 运行；**核心 Skill 只使用 Python 标准库，无须 pip requirements、Node、mcporter 或独立腾讯文档 MCP 插件**。

Agent 可以执行 `python3 --version`、`git --version`；工具缺失时提示用户使用对应系统官方安装包或系统包管理器安装，未经允许不要执行 sudo、改环境变量或覆盖系统 Python。Agent Skill 扫描目录因产品/版本不同，应先查该 Agent 实际配置，不可猜测成统一路径。

## 2. 安装完整 Skill

```bash
git clone --depth 1 https://github.com/haomiaoOnline/api-sse-probe.git
cd api-sse-probe
python3 scripts/check_skill_contract.py
python3 scripts/install.py --target "/用户确认的/Agent-Skills-根目录"
```

安装脚本严格依据 `TEAM_MANIFEST.txt` 复制文件；如果 `api-sse-probe` 已存在，直接拒绝覆盖，保护此前的私有配置和证据。安装**不**自动创建 `config.local.json`。升级要先备份个人配置、运行证据，核查新旧接口/断言兼容后再重新安装，不合并陌生人的密钥。

## 3. 告知用户需要准备什么

| 必备信息 | 获取途径 | 是否可发送到聊天 |
| --- | --- | --- |
| 用例来源 | 本地 CSV/JSON 或腾讯文档/飞书表格 | 仅允许分享非敏感来源类型 |
| 业务 API URL | 业务方/测试环境 API 提供者 | **不建议**，在终端填写 |
| `powerkey`、`signature` | 网关/接口负责人；程序不自动计算签名 | **不可以** |
| 腾讯文档 Token（选腾讯时） | [腾讯文档官方 MCP 授权页](https://docs.qq.com/open/auth/mcp.html) 登录后复制个人 Token | **不可以** |
| 腾讯文档表格链接 | 在授权账号内打开目标表，复制 `https://docs.qq.com/sheet/...` 且包含 `tab=` | 含私有 ID，不宜公开 |
| 飞书 App ID / App Secret（选飞书表格时） | [飞书开放平台](https://open.feishu.cn/app) → 自建应用 → 凭证与基础信息，另需授权读取表格 | Secret **不可以** |
| 飞书机器人 Webhook / Secret（选通知时） | 目标群 → 设置 → 群机器人 → 自定义机器人 → 添加并配置安全关键词/签名 | **不可以** |

**腾讯文档 Token 的使用方式**：本 Skill 直接调用官方 `https://docs.qq.com/openapi/mcp`，请求头为 `Authorization: 个人Token`，使用 `sheet.get_sheet_info` 和 `sheet.get_cell_data` 两个只读工具。无需把 Token 当作云服务商 API Key、Cookie 或通过浏览器开发者工具导出。可选通过本机环境变量 `TENCENT_DOCS_TOKEN` 提供，也可由向导存到私人 JSON；不把它写入源码或发布包。官方指南：[腾讯文档 MCP](https://developer.cloud.tencent.com/mcp/server/11803)。

**飞书通知**：若只需群消息，用[群自定义机器人](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot?lang=zh-CN)即可；机器人 Webhook 不等于飞书应用的 App Secret。若需要读取飞书表格或归档云文档，需要另外的应用凭据、权限和目标文档可见范围；有 App ID 不代表已经有文件访问权。

## 4. 隐藏输入，创建个人配置

进入**已安装**的 Skill 根目录，用本地交互式终端运行：

```bash
python3 scripts/setup.py init --source tencent
# 或 --source csv、--source json、--source feishu
python3 scripts/setup.py doctor
```

向导依次采集 API URL、`powerkey`、`signature`、用例来源、对应 Token/应用凭据，以及是否启用飞书群通知。默认不通知、不云归档；隐藏输入不回显，在本机创建 `0600` 私人文件。**已有 `config.local.json` 拒绝覆盖**；请人工核对并在本地编辑。无交互终端时，只能提供如何安全地编辑私有配置的说明，禁止请求用户把 Secret 粘进 Agent 聊天。

自动映射以“序号、场景、类别、语料、判断依据（公共）、特殊判断依据、研发负责人、deviceId”等表头为默认。用户表头不一致时，先用表格只读检查识别差异，修改私有 `source.*.columns`，不得凭猜测默默跳过用例。

## 5. 验收：分两级

```bash
python3 scripts/setup.py doctor       # 完全离线，不发请求
python3 scripts/probe.py --check      # 只读读取获授权用例，无业务 POST
```

`READY_OFFLINE` 只代表本地配置存在、结构合理；**不代表 Token 或接口一定在线**。`--check` 可验证表格权限、真实列映射及有效用例，但读取私有表格前要得到用户许可。若 Token 鉴权失败，回官方授权页核查；若文档不可读，核查当前账号对目标文件的共享权限；飞书应用还需授权后在文档侧允许应用访问。

确认数量/范围与目标后再执行：

```bash
python3 scripts/probe.py
# 或一次性覆盖源文件，不写回配置
python3 scripts/probe.py --source-url '<获授权的完整表格链接>'
```

拨测会发送真实业务请求，可能产生开销或业务动作；Agent 必须先告知用户目标及范围并取得确认。若有 UNKNOWN，不得伪造 PASS/FAIL 或无限重试。报告、证据只留在私人目录；飞书消息与云文档归档须另行授权。

## 6. 故障与升级

- 没有 Python/Git：先按官方发行版安装，验证版本；不要从不明来源 curl 执行 shell。
- 腾讯 Token 缺失/错误：官方授权页重新获取，密钥不要打印或提交；Token 有效不等于对目标文档有权限。
- `doctor` 通过、`--check` 失败：优先排查文档权限、列名、工作表 `tab=`、`deviceId` 和“判断依据”列。
- 安装目标已存在：先备份本人的 `config.local.json` 和报告；不自动覆盖，不拷贝别人的 Token。
- Windows：可检视代码与配置，但当前不能将原生 v2 实时拨测宣称已支持。

维护/交付时只报告状态和安全路径；不要输出 Token、鉴权参数、真实请求/响应内容。