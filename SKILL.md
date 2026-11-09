---
name: api-sse-probe
description: 对已授权的 HTTPS JSON→SSE 接口进行批量拨测，使用确定性断言校验结果，保存完整证据并生成私有报告；支持 CSV、JSON、腾讯文档与飞书表格只读用例源。
license: MIT
compatibility: Python 3.9+；标准库，无 pip 依赖；联网功能需相应接口及文档授权
metadata:
  version: "2.4.3"
---

# api-sse-probe

读取测试用例 → 检查判断依据 → 串行发送 HTTPS 请求 → 保存 SSE 原始证据 → 离线判定 PASS/FAIL/ERROR → 生成报告并按配置通知。

**唯一正式执行入口：** `scripts/probe.py`。本文件供 Agent 决定如何调用 Skill；**不能单独执行**。安装时必须使用包含 `scripts/`、`references/`、`config.example.json` 的完整目录或审核过的发布 ZIP。

## 使用前提

1. 只拨测用户授权的接口和用例；目标必须是受支持的 HTTPS POST JSON → SSE 协议，不自动适配任意 REST API。
2. 在项目根目录复制 `config.example.json` 为 `config.local.json`，填写**自己的** API 地址、`powerkey`、`signature`、数据源和可选飞书授权。该文件只能保存在本机，不加入 Git/发布包。
3. `config.local.json` 是标准 JSON；字段含义见 [配置说明](references/configuration.md) 和 [注释模板](config.annotated.jsonc)。示例文件没有真实凭据，不能直接发起生产拨测。
4. 当前 `signature` 按配置值原样发送，不自动计算；必须验证 TLS。macOS/Linux 已有执行路径，**Windows 原生运行和断电恢复未完成验证**。

## 执行命令

在 Skill 根目录运行：

```bash
# 使用私有配置中的默认用例源，正常执行
python3 scripts/probe.py

# 明确指定本轮测试表（不会覆盖本地配置）
python3 scripts/probe.py --source-url 'https://docs.qq.com/sheet/EXAMPLE_FILE_ID?tab=EXAMPLE_TAB'

# 只检查用例、列映射和断言；不发送业务 POST 或群通知
python3 scripts/probe.py --check

# 只执行用户指定的真实用例编号
python3 scripts/probe.py --case-id 123

# 中断后使用命令输出的 resume_command 继续
# python3 scripts/probe.py --run-dir <实际运行目录>
```

用户明确提供带 `tab=` 的测试表并要求“拨测整表”时，按配置读取范围内**全部启用、有效的用例**执行；不要擅自缩小范围，也不必无故重复要求用户先运行 `--check`。指定少量用例时严格遵守指定范围。腾讯文档通过 MCP 只读，飞书表格通过获授权的读取接口；不写回原测试表。

正式执行默认采用已授权的飞书通知配置；**只有用户明确要求不通知时**才附加 `--no-feishu`（同时禁止云归档）。未配置有效通知目标时应明确报错，不自行选择群组。

## 判断依据

测试表的 **“判断依据（公共）”** 和 **“特殊判断依据”** 都是必需条件来源；默认**全部满足（AND）**才算业务通过。两列的“同上”分别继承本列上一个有效值。表中的既有“响应结果”不作为本次判定依据。

| 表格填写示例 | 实际判定 |
| --- | --- |
| `HTTP 200 / 文本回复` | HTTP 状态为 200，且助手有非空正文 |
| `文本回复包含“暂停”` | 助手正文含字面子串“暂停” |
| `文本回复包含“暂停”或“停止”` | 两个关键词至少命中一个（OR） |
| `回复中包含“杭州”和“天气”` | 两个关键词都命中（AND） |
| `回复中不包含“播放失败”` | 助手正文不能出现该子串 |
| `title是“灵犀服务”` | 真实业务标题精确匹配 |
| `skillId是“VIDEO123”` | 实际业务字段中的 ID 精确匹配 |
| `deeplink以“cmcc://example”开头` | 实际结构化字段前缀匹配 |

**只做程序化的字面和结构化字段校验，不做 AI 意图推测。** 例如“无法暂停播放”包含“暂停”，所以单纯包含规则通过；“已停止当前视频”不含“暂停”，需明确写成“暂停”或“停止”才通过。含糊、不支持或格式错误的判断依据应在业务请求前拒绝，绝不能静默丢弃规则、将 HTTP 200 当成业务成功。更详细的语法见 [断言说明](references/assertions.md)。

## 运行、结果与恢复

- 每条请求先记录执行意图，再保存实际接收的 SSE 原始字节、HTTP 信息和证据摘要；之后由离线断言引擎复判，不靠模型分析返回文本。
- 协议层先校验 HTTP 状态、SSE JSON 业务帧及要求的 `[DONE]`；证据不足不能判为 PASS。
- `PASS`：全部必需条件满足；`FAIL`：有协议或断言失败；`ERROR`：请求/执行异常。外部状态为 `UNKNOWN` 时必须明确告知不能判断业务结果。
- 默认一次启动串行完成所有有效用例。若返回 `workflow_status=CONTINUE_REQUIRED`，按照输出的 `resume_command` 续跑；不猜测已发出但结果未知的请求状态。
- `unknown_retry.enabled=true` **只对新建运行有效**：允许下一次恢复时对 UNKNOWN 最多自动重试一次；可能使同一业务动作执行两次，只有用户接受此风险的场景才启用。再次未知需人工核对。
- 原始 SSE 和完整语料保存在受限本地报告/证据目录。可选飞书云文档归档只在获得授权、内容脱敏及只读权限核验完成后执行；群消息发送摘要和链接，不发送未脱敏原始响应。未完成批次只能出明确标注的非正式报告。

## 安全及人工确认

- **不得提交或分享敏感信息**：真实 Token、`powerkey`、`signature`、Webhook、App Secret、API 地址、设备标识、私有文档 ID、`config.local.json`、`.env`、`reports/`、SQLite 或真实 SSE。
- 不关闭 TLS 验证；不发起未授权请求；不把真实响应写回原始腾讯/飞书测试表。
- 重要断言规则修改、正式发布或改写/删除已有证据前需要人工确认。失败时保留实际日志和错误原因，不假报成功。
- 离线测试可运行 `python3 -B -m unittest discover -s tests -q`，但不得把测试用例或模拟结果冒称真实拨测验收。

## 参考文件

- [安装与使用配置](references/configuration.md)
- [断言语法与边界](references/assertions.md)
- [当前架构和工作流](references/architecture.md)
- [网络超时与排障](references/timeout-and-diagnostics.md)
- [飞书通知](references/feishu-notification.md)
- [常见问题](references/troubleshooting.md)