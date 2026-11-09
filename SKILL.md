---
name: api-sse-probe
description: 为 Agent 安装并配置授权的 HTTPS JSON→SSE 批量拨测器。读取 CSV/JSON/腾讯文档/飞书表格，确定性断言、私有 SSE 证据、离线复判和可选飞书通知。
license: MIT
compatibility: Python 3.9+；macOS/Linux；标准库无 pip 依赖；线上读取与拨测需用户授权
metadata:
  version: "2.5.0"
---

# api-sse-probe

**唯一正式入口：** `scripts/probe.py`。本 Skill 是完整目录，不是单独的提示词。执行流程：读取用例 → 请求前验证规则 → 冻结快照 → 串行 HTTPS SSE 拨测 → 保存原始证据 → 离线判定 → 私有报告 → 按授权通知。

## Agent 安装任务：从零到可用

用户说“安装 api-sse-probe”“接入测试表”“用这个 Skill 拨测”时，按以下顺序执行；**安装和真实请求是两次独立授权**。

1. **检查环境**：识别 OS、Python 版本、Git、网络访问及 Agent 实际 Skills 根目录。需要 Python ≥3.9、Git（从 GitHub 安装时）；Python 运行仅依赖标准库，**无需 pip、Node、mcporter 或独立腾讯文档 MCP 插件**。缺少工具时先说明官方安装方式，涉及 sudo/系统变更需用户授权。Windows 原生 v2 运行尚未验收，不自动进行真实拨测。
2. **获取完整源码**：已有完整本地目录则复用；只有本文时必须获取整个仓库，不能凭空编造脚本。公开源：`https://github.com/haomiaoOnline/api-sse-probe`，只使用审核过的版本；不要运行来历不明的远程 shell 脚本。
3. **执行安装**：查明 Agent 的真实 Skills 根目录（不要猜测）并安装完整发布清单，拒绝覆盖已有安装：

   ```bash
   git clone --depth 1 https://github.com/haomiaoOnline/api-sse-probe.git
   cd api-sse-probe
   python3 scripts/check_skill_contract.py
   python3 scripts/install.py --target "/path/to/AGENT_SKILLS_ROOT"
   cd "/path/to/AGENT_SKILLS_ROOT/api-sse-probe"
   ```

4. **引导提供信息**：先问用例来源是 CSV、JSON、腾讯文档还是飞书表格，以及是否需要飞书群通知；只问非敏感选择。引导用户从**自己本机的交互式终端**启动向导，凭据用隐藏输入，不要让用户在聊天窗口发送 Token、powerkey、signature、App Secret、Webhook 或私有接口地址：

   ```bash
   python3 scripts/setup.py init --source tencent   # 也支持 csv / json / feishu
   python3 scripts/setup.py doctor
   ```

   若 `config.local.json` 已存在，**不得覆盖**，只执行 `doctor` 并在用户同意后更新其私人配置。向导只创建 `0600` 私有 JSON，不发网络请求；默认**禁用飞书通知和云归档**。
5. **先只读验收**：`doctor` 输出 `READY_OFFLINE` 表示本机结构/凭据存在，**并不证明线上授权有效**。在用户允许读取测试表后执行 `python3 scripts/probe.py --check`，确认表格权限、列映射和有效用例数量；此步骤不发业务 POST、不发飞书。
6. **真实拨测单独确认**：向用户说明目标环境、测试表、有效用例数量、是否通知以及重复执行风险，获得授权后再运行 `python3 scripts/probe.py`。不得因为安装成功自行触发接口。

详细安装、升级和平台选择：[安装指南](references/installation.md)。

## 关键信息从哪里获取

| 项目 | 获取途径与用途 |
| --- | --- |
| 业务 API URL / `powerkey` / `signature` | 向**业务 API 提供方或网关负责人**申请已授权的 HTTPS SSE 测试接口和鉴权参数；本 Skill 不生成动态 signature |
| 腾讯文档 MCP Token | 登录 [腾讯文档官方授权页](https://docs.qq.com/open/auth/mcp.html)，复制个人 Token；**不是**腾讯云 API 密钥，不能靠浏览器 Cookie 代替 |
| 腾讯测试表 | 从腾讯文档复制含 `?tab=` 的完整 Sheet 链接，确认该 Token 所属账号有读取权限 |
| 飞书表格 App ID / Secret | 登录 [飞书开放平台](https://open.feishu.cn/app)，创建/选择企业自建应用，在“凭证与基础信息”读取；还要单独开通表格读取权限和文件访问权限 |
| 飞书群通知 Webhook / Secret | 在目标群设置 → 群机器人 → 自定义机器人，获取 Webhook，启用签名与关键词；详见[官方指南](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot?lang=zh-CN) |

当前腾讯文档读取器内置 `https://docs.qq.com/openapi/mcp` 的只读 `sheet.get_sheet_info` / `sheet.get_cell_data` 调用，发送 `Authorization: <个人Token>`；无需另装 MCP 服务。**不要在日志或聊天中打印该请求头**。

## 运行规范

```bash
python3 scripts/probe.py --check                  # 用户授权的只读检查
python3 scripts/probe.py                          # 使用私有配置执行
python3 scripts/probe.py --source-url '<授权的完整表格链接>'
python3 scripts/probe.py --case-id 123            # 仅指定用例
python3 scripts/probe.py --no-feishu              # 明确要求不通知时
python3 -B -m unittest discover -s tests -q      # 离线回归
```

- 用户给定带 `tab=` 的腾讯文档并要求整表拨测时，按配置读取范围内**全部启用且有效用例**处理；无故不重复要求用户先做 `--check` 或自行改变数量限制。表格只读，不写回“响应结果”。
- **公共判断依据 + 特殊判断依据默认全部满足**；内部的“或”是精确 OR。 `文本回复包含“暂停”` 对“无法暂停播放”仍为真，不代表真实暂停成功。规则不清楚或不支持时在 POST 前拒绝。语法见[判断依据](references/assertions.md)。
- HTTP、SSE 完整性、`[DONE]`（若要求）和业务断言分别校验，缺失证据不能冒充 PASS。按实际保存的 SSE 离线判定，给出明确失败项。
- 默认串行执行并保存原始证据；中断后只使用程序返回的 `resume_command`。`UNKNOWN` 不能推断结果；仅新运行明确设置 `unknown_retry.enabled=true` 才允许至多补测一次，可能重复触发业务动作。
- 飞书通知仅在用户配置启用时发送；云端归档还需另外的应用授权、脱敏及权限校验。未脱敏原始 SSE、语料和 Header 只留在私有本地。

## 安全与交付标准

- **绝不上传/分享**个人 Token、`powerkey`、`signature`、App Secret、Webhook、API 地址、设备 ID、私有表格链接、`config.local.json`、`.env`、`reports/`、数据库和原始 SSE。
- 更新已有安装前先备份个人配置与私有报告；安装器拒绝覆盖现有 Skill。凭据错误只输出错误代码，不回显值；不关闭 TLS。
- 交付必须报告：安装路径、检查项状态、数据源类型、有效用例数（经过线上只读检查才有）、真实请求是否执行、报告位置及通知状态；未做的验收不得声称完成。
- 重要规则变更、正式发布和影响历史证据的操作需要人工确认。独立完整安装参见 [安装指南](references/installation.md)，架构参见 [工作流](references/architecture.md)，排障参见 [问题定位](references/troubleshooting.md)。