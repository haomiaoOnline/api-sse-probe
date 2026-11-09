> **v1.9.0 历史架构参考：** 本文旧图尚未涵盖 v2.4.3 的冻结用例、SQLite 逐条证据、离线复判、飞书 Docx 归档。当前架构先查看 [v2.4.3 权威工作地图](architecture.md)，下文仅供历史模块背景参考。

# 当前架构与执行逻辑｜v1.9.0

**适用对象**：团队成员、Agent 执行者、未来适配不同 API 的开发者。以下是根据现有 `scripts/` 真实实现而非理想架构整理的行为说明。当前代码主入口 `scripts/probe.py`，无需额外 pip 包。

## 一、功能框架图

```text
用户授权/命令行 (scripts/probe.py --check / --case-id / --config)
       |
       v
[私有配置解析] ──> 认证、接口URL、超时、来源映射、通知设置
       |
       v
[用例源适配]
   ├─ CSV / JSON ──> 本地只读文件
   └─ 腾讯文档 ──> 腾讯 Docs MCP (HTTPS, Token)
                      sheet.get_sheet_info [仅按名称解析时]
                      sheet.get_cell_data [只读, return_csv]
       |
       v
[read_cases / tencent_rows]
   字段映射 → 过滤停用/空设备行 → 继承“同上”
   → 合并公共+特殊判断条件 → 统一 Case 数据结构
       |                        |
       | --check               | --case-id 或真实批次
       v                        v
[打印ID/数量，停止]     [run_case: HTTPS POST JSON]
                                   |
                                   v
                         [SSE事件流解码]
                                   |
                                   v
                         [assertion_rules 业务判定]
                                   |
                                   v
                         [results PASS/FAIL/ERROR]
                                   |
                                   v
                          [reporting.build_analysis]
                             |                 |
                             v                 v
                      [write_reports]    [feishu_notification_router]
                         私有 JSON/MD      legacy 或 v2 卡片
                                            单张五指标卡片 + 全部失败编号
                                              |
                                              v
                                         [feishu_transport]
                                         Webhook HMAC签名 / TLS
```

## 二、模块职责与调用关系

| 文件 | 真实职责 | 团队扩展建议 |
| --- | --- | --- |
| `scripts/probe.py` | 参数、配置、读用例、串行 `run_case`、SSE解析、报告落盘和通知触发 | 后续拆出请求协议适配器及执行器 |
| `scripts/lib/tencent_mcp.py` | 腾讯文档 MCP 只读工具、文档/工作表定位和 A1 区间读取 | 其他平台另建 Provider，而不是改通用断言 |
| `scripts/lib/tencent.py` | 旧版 Tencent OpenAPI 实现仍在代码树中 | 当前主要腾讯文档读取调用 `tencent_mcp.py`；不要误用旧接口 |
| `scripts/lib/assertion_rules.py` | 解析结构化 `questions`、`text` 等与保留判断词的严格匹配 | 新业务规则应新增用例测试 |
| `scripts/lib/reporting.py` | 汇总、首帧与完整耗时、失败明细、慢首帧 | 维持 JSON 字段语义和单位兼容 |
| `scripts/lib/probe_notice_adapter.py` | 把拨测 `summary/failure_cases` 映射到通用通知模型 | 未来其他 Skills 单独做适配层 |
| `scripts/lib/feishu_notifier.py` | 规范化通知结构、Card JSON 2.0 组件和紧凑指标卡 | 渲染与业务请求逻辑分离 |
| `scripts/lib/probe_group_report.py` | 腾讯文档可信链接、单卡优先、超限完整分页、内容边界 | 报告完整性/大小限制测试 |
| `scripts/lib/feishu_notification_router.py` | `legacy/v2/auto`、发送汇总+所有页、部分失败识别 | 回执不明确时禁止重复发 |
| `scripts/lib/feishu_transport.py` | 自定义机器人 Webhook 地址校验、HMAC签名、TLS、20KB大小、回执 | 将来可加不同通知渠道 |
| `scripts/share_report.py` | 校验历史 JSON，显式 `--send` 才补发，不调用 SSE | 用于丢消息后的受控人工补发 |
| `scripts/check_tencent_mcp.py` | 仅检查 MCP 工具可访问，不读取私有文档 | 不能代替实际读表验收 |
| `scripts/install.py` | 按审核清单复制公开文件到 Agent Skills 根目录 | 安装目的地已存在则拒绝覆盖 |

## 三、执行过程细化（当前已实现）

### 阶段 A：数据源和用例验证

1. `main()` 从 `--config`（默认 Skill 根目录内的 `config.local.json`）读取 JSON，不存在或损坏不会自动创建/修复。
2. 若 `source.type=tencent`：`document_identity` 根据 `doc_url`、`file_id`、`sheet_id`/`sheet_name` 定位，`read_values` 通过 Token 调用官方只读 MCP。未提供有效 Token、文件权限不足、工作表定位冲突，均报错而非使用缓存。
3. `tencent_rows` 将返回的矩形 A1 范围按表头名称或绝对 Excel 列字母映射到 `case_id`、`content`、`deviceId`、`expected_keywords`、`special_keywords` 等。`header_row` 指的是**工作表的绝对行号**；设为 `0` 表示所读取范围不含表头（此时需列字母映射）。`range=A2:H11` 等工作表行号**不必与业务用例ID一致**。
4. `read_cases` 对启用状态、空设备、重复ID、必填内容、JSON扩展参数进行校验。腾讯模式空 `deviceId` 的行跳过并计入统计（**也可能误跳过正式用例**）；被跳过的行会中断 `同上` 继承链。公共+特殊断言去重合并，默认所有条件须满足。
5. `--check` 输出用例ID、计数、数据源类型和跳过统计，然后停止；不会发送接口请求或飞书通知，也不会检查 API 认证能否通过。**用户要求“前 N 条”时，必须先对比真实 `case_ids` 与授权集合，不得只根据 Excel 行号推测。**

### 阶段 B：单次真实 API 请求

`run_case` 当前构造固定请求体：

```json
{
  "endpoint": {},
  "stream": true,
  "session": {},
  "messages": [{"role": "user", "content": "<authorized case content>"}],
  "deviceId": "<authorized device identifier>",
  "reqId": "<generated UUID>"
}
```

- `api.headers.powerkey` / `signature` 为授权字符串或环境变量引用；`timestamp` 每个 POST 自动生成 Unix 毫秒值（13位），**不是**动态签名计算。
- 强制 HTTPS/TLS 证书验证、不允许重定向。连接阶段默认8秒、等待响应头默认8秒、SSE空闲20秒、总请求60秒（硬截止），受 `api.connect_timeout_seconds`、`idle_timeout_seconds`、`total_timeout_seconds` 配置控制。每次阻塞操作遵守对应阶段及总截止时间；不等于业务服务故障定责。
- `run_case` 从 SSE 帧解析 JSON：至少一个有效业务JSON帧；根据 `require_done` 需要 `[DONE]`；禁止无效帧/错误事件；使用 `evaluate_assertions` 判断实际文本和业务字段。`PASS` 与 `FAIL` 为断言/协议结论，`ERROR` 为网络/HTTP错误/超时/TLS/配置结论。
- `network_error` 仍不是可直接定责的根因，但v1.10.0会额外保存 `error_phase`、`exception_type`、安全的异常类型链、HTTP头状态与已接收字节数；原始异常消息（可能包含敏感数据）不记录。请结合网关/服务端`reqId`查证。

### 阶段 C：汇总、报告、飞书通知

- 所有用例完成后，调用 `build_analysis` 计算总体、PASS/FAIL/ERROR、通过率、首业务帧有效样本与耗时、慢首帧；再调用 `write_reports` 写 JSON v6 + Markdown。
- 首帧是**首个合法 JSON 业务帧**到达的耗时（`first_token_ms`），近似TTFT，单位用户可见为秒（s、三位小数），内部仍保留ms。若未收到首帧则不计入均值。
- `reports/` 里的失败详情有完整测试语料和完整实际接收响应，新建文件在 POSIX 模式使用0600；**不可当作面向所有群成员的共享 URL**。
- `feishu.enabled=true` 且 `notification_style=v2`：优先同排五指标单卡；超出 Webhook 单卡容量时先预检全部分页再按序发送，每页标记 i/N，全部 FAIL/ERROR 编号只出现一次；中途拒绝/回执不确定时停止后续页，不自动重试。`legacy` 因可能漏失失败编号而阻止投递。`--check` 不触发通知。
- `share_report.py --report latest` 从原配置 `reports.dir` 检查完整历史 JSON；加 `--send` 时使用原报告时间标注“历史报告补发”，**不产生业务接口POST**。只允许从指定 reports 目录读取报告，避免误传其他文件。

## 四、运行时序与真实边界

当前 `main()` 串行执行所有 `run_case`，没有运行中的逐条日志、检查点或逐条落盘。整批完成才写报告。超出 Agent/IDE 工具 60 分钟时限的批次很可能被终止，已经发出的真实请求并不因此撤销；此时不能简单凭日志推测“0条执行”，也不能盲目启动第二个全量任务。

当前飞书仅用于推送：机器人不能自动托管本地 Markdown/JSON 结果，也不能赋予腾讯文档阅读权限。群内完整可见的是**失败摘要、断言与耗时**，不是每条原始 SSE 响应；需要团队共享响应时，另建有权限的报告存储设计。

## 五、接口/安全和兼容性边界

- 只读腾讯文档：官方 MCP HTTPS、读工具与授权 Token；不进行文档写入。
- 业务请求：仅用户授权接口；鉴权头、设备ID、原始请求体不打印、不入群。
- 报告：本地私有文件，组织数据保密级别按团队政策管理；Windows 要检查 NTFS ACL 而不是相信 `chmod`。
- 飞书通知：群成员能看到失败摘要，需事先确认群信息等级和投递目标；Webhook 的服务器 `code=0` 不等于客户端 UI 已人工验收。
- 安装升级：代码版本与真实安装目录应对齐。v1.9.0 纳入了实际安装版已有的 SSE 分块 `read1` 流读取逻辑，修复源码与安装版不一致的问题，不自动变更已有私有配置。
- 扩展 API：需要引入 Provider/RequestBuilder/ResponseExtractor/RuleParser 等接口，不能通过改 `config.example.json` 就假定适用完全不同的协议。

相关检查请看 [验收手册](acceptance.md) 和 [通用化路线图](team-generalization-roadmap.md)。

## v1.9.0：失败SSE原始字节留存与自动覆盖

- `scripts/probe.py` 的 `run_case` 在 SSE 读取过程中将**实际收到的原始字节**写入该用例的临时内存缓存（只保留当前用例，受 `api.max_response_bytes` 限制）。SSE事件头、注释、换行和 `[DONE]` 均保留；只对 FAIL/ERROR 写入专门的持久化文件，PASS 不持久化原始流。
- 新模块 `scripts/lib/failed_sse.py` 提供 `LatestFailureCapture`：目标由API URL + 用例源/工作表 + 默认endpoint哈希标识，私有 `reports/latest-failed-sse/<target_hash>.json` 一目标一文件；哈希文件名不会明文暴露URL、密钥或设备ID。
- 运行时写同名 `<hash>.pending`，每完成一个失败请求即向该临时快照追加 Base64 编码的原始字节、用例ID、状态、原因、收到的字节数与SHA-256。
- **只有整轮结束、正常JSON/Markdown报告写完后**才通过 `os.replace` 原子替换同目标最新 `.json`；零失败也替换为零项。中途报错或报告写失败时保留上轮已完成快照。异常强杀可能留下一个同目标 `.pending`，下次同目标运行时自动覆盖，避免按运行次数无限累积文件。
- `capture_state` 有 `protocol_done`（见到[DONE]）、`partial_or_unterminated`（流不完整）、`partial_or_size_limit`（到达读取限制）、`not_received`（没有拿到字节）；只能保证存储**实际接收到的完整原始字节序列**，不能凭空还原服务端尚未发送的部分。
- 原始SSE文件总量默认受128MiB上限保护（可调最大512MiB）；超限显式失败、旧版快照不覆盖。完整SSE文件绝不进入飞书、普通JSON/Markdown报告或Skill发布包。
- 这些逐条写入的 `.pending` **不是断点续跑检查点**；当前批次依旧串行执行，不支持多进程同时拨测同一目标。

## v1.9.0 报告模型与输出Writer解耦

原先 `probe.py.write_reports` 同时建汇总、渲染Markdown、创建文件；现在其旧函数名只作为兼容Facade，真正职责已拆分：
1. `lib/report_document.py.build_report_document`：用 `build_analysis` 生成标准 JSON v5（包含 summary/results/failure_cases/slow_token_cases），纯数据逻辑。
2. `lib/report_document.py.render_markdown`：只消费标准JSON并输出Markdown文本，不读写磁盘。
3. `lib/outputs/registry.py`：在任何真实POST前验证 `report_outputs`。报告生成后按显式注册表派发；未知或未启用目标会硬失败而不静默丢结果。
4. `lib/outputs/file.py`：`LocalFileOutput` 负责私有JSON/Markdown输出和文件权限（POSIX0600）。
5. `lib/outputs/base.py`：可扩展的 `ReportOutput.publish(document, context)` 与上下文契约。

默认配置省略 `report_outputs` 也会自动使用 `file`，并在控制台 `report_outputs` 字段中返回写入回执。对未来云端Writer，Registry默认只会发送 `public_projection`（去除语料、响应摘录、请求ID；不包含原始SSE），但云端数据仍需审批。

飞书**群通知**仍独立于报告存储Writer，已有Card v2行为保持不变；`failed_sse.py` 完整原始流按目标覆盖也独立于报告Writer，禁止云输出自动获得其内容。新增任意输出形式请读 [output-adapters.md](output-adapters.md)。
\n当前阶段诊断/异常证据详情： [分阶段超时与诊断](timeout-and-diagnostics.md)。\n