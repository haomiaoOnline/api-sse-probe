# 监控报告与统计口径（v1.11.0）

## 一、文件

每次正式执行整批完成后在 `reports/` 下生成 `probe-<UTC时间>-<随机标识>.json` 和同名 `.md`；新建报告在POSIX使用0600权限，Windows须单独检查NTFS ACL。当前报告JSON `version=6`；旧 v5 报告仍按历史结构读取；`--check` 不生成新业务报告，也不发飞书。

报告内容：
1. `summary`：总量PASS/FAIL/ERROR、通过率、有效首帧样本数、平均/最快/最慢首帧、请求累计耗时、墙钟耗时、慢首帧总数和阈值。
2. `results`：每个用例ID、场景、PASS/FAIL/ERROR状态、HTTP、首帧、完整请求耗时和原因；不重复嵌入完整响应。
3. `failure_cases`：每条 FAIL/ERROR 的场景、完整语料、失败断言详情、完整实际接收响应的可读文本视图、编码说明及私有证据校验元数据。
4. `slow_token_cases`：按 `first_token_ms >= slow_token_threshold_ms` 筛出的慢首帧用例。

**报告是高度敏感文件**：本地 JSON/Markdown 失败详情包含完整测试语料及实际接收的完整 SSE/HTTP 响应体，必须放在授权用户可读的受限目录，不能直接放到公开共享位置。Markdown 使用可读 UTF-8 文本；非法 UTF-8 字节转义展示，精确字节以 Base64 私有快照为准。飞书、public_projection 和团队发布包不得包含这些字段。

## 二、统计语义

| 字段 | 含义 | 用户可见单位 |
| --- | --- | --- |
| `total` | 本轮实际进入 `run_case` 的用例数量 | 条 |
| `pass` / `fail` / `error` | 正确业务结果 / 断言或协议未通过 / HTTP、网络、TLS、超时等异常 | 条 |
| `pass_rate` | `100 * pass / total` | %，两位小数 |
| `first_token_ms` | 从开始请求到首个**合法业务 JSON 帧**到达，近似首Token，不是模型内部TTFT | 内部ms，显示s |
| `first_token_sample_count` | 有可用首业务帧的请求数（可包含部分业务FAIL） | 条 |
| `average_latency_ms`、`fastest_ms`、`slowest_ms` | 仅对有有效首帧的样本统计，不把ERROR的空值作为0 | 终端/飞书/Markdown显示 s |
| `duration_ms` | 每条 `run_case` 从开始到返回的完整时长 | s（三位小数） |
| `total_duration_ms` | 本批每条完整时长累加 | s |
| `wall_duration_ms` | 真正执行这一批业务用例的墙钟时间，不含读腾讯表、写报告、发飞书 | s |
| `slow_token_threshold_ms` | 默认3000ms，达到或超过此值的合法首帧为慢首帧 | 配置用ms，展示3.000s |

JSON 同时保留 `*_ms` 与添加的 `*_s` 数值字段保证兼容；如果没有首帧，`first_token_ms=null`，展示为 `-`，平均时延不得按0计。`latency_metric` 固定说明用 `first_token_ms`，不是完整请求时长。

## 三、结果状态定义

- **PASS**：HTTP与SSE业务帧合法，必要 `[DONE]` 存在，用户定义的业务断言全部满足且无禁止词。
- **FAIL**：真实响应已进入业务/协议判断，但某些判断未通过，例如 `assertions_failed`、`done_missing`、`invalid_sse_json` 等。
- **ERROR**：网络、TLS、超时、HTTP错误或配置障碍；不应混同业务关键词缺失。`network_error` 仅是统一分类，无法单凭该字段断言具体根因。

## 四、群内与本地的区别

- 本地JSON/Markdown：可以由**授权工程师**查看每条失败用例的场景、完整语料、失败判断原因和完整已接收 SSE/HTTP 响应体。其他慢首帧统计保留在 JSON summary/slow_token_cases 中，不重复堆叠到失败诊断区。
- 飞书群 v2：五指标卡片优先单张；容量不够时按页号 1/N、2/N 自动拆分全部失败/异常编号及精简原因。所有页先完成内容和大小校验，发送中途失败即停且不自动重发；团队成员可以按页查看失败摘要，无需访问执行机器。
- 群里**不提供**所有原始响应，也没有链接到本机报告文件；需要在线共享完整响应应另建有权限的云报告平台。群里的腾讯表格入口只链接**测试数据源**而不是详细结果报告。
- 历史报告补发不会改变原测试时间、不重新请求业务SSE，但会再次发送群卡片，必须确认已授权并避免重复。

## 五、长批次边界

当前 `main()` 等所有用例串行执行完成后才写一次完整报告。若运行器/CLI的外部超时使任务提前终止，可能没有新的JSON/MD可供恢复。**不要把旧报告误当本轮结果**；后续需要逐条落盘/断点续跑，见 [team-generalization-roadmap.md](team-generalization-roadmap.md)。

## v1.9.0：受限失败原始SSE最新快照

- 普通统计报告仍是按时间生成的 `probe-*.json`（schema version 6）和 `probe-*.md`；每条失败详情均包含完整语料和完整已接收响应文本。另生成 **失败完整响应接收字节** 文件 `reports/latest-failed-sse/<target_hash>.json`（当前独立 schema version 2，仍兼容读取 v1）。两者不混淆。
- 新文件每次整轮成功生成普通报告后**按目标覆盖上一次**，不按运行时间新增文件。文件只含 FAIL/ERROR 的 `raw_sse_base64`（原样字节无损编码）、`sha256`、`received_bytes`、`capture_state`、`case_id`，以及私有 `request`（请求体、设备 ID、请求头）和 `response`（HTTP 状态、完整响应头等上下文）。可使用 `scripts/inspect_failed_sse.py` 验证/导出原始字节；PASS 原始流目前不留存，v2 统一全量捕获尚待后续重构阶段。
- 状态：`protocol_done` 表示收到 `[DONE]`；`partial_or_unterminated` 表示只收到部分流或没收到正常结束标记；`partial_or_size_limit` 表示因响应大小限制停止；`http_error_body` / `non_sse_http_body` 表示实际收到的 HTTP 错误/非 SSE 响应体；`not_received` 表示未收到响应字节。不得把部分流或非 SSE 错误体称为完整 SSE。
- 全部PASS会原子覆盖为 `failed_total=0`，避免旧失败残留；批次中断或普通报告写失败不会覆盖旧文件。另有正在运行中的同名 `.pending`，意外被杀时最多留下一个同目标临时文件。
- `reports.failed_sse_max_total_bytes` 默认134217728字节（128MiB），最高536870912字节（512MiB）；超过上限则明确报错、保留上一份成功结果。普通 `probe-*.json/.md` 历史报表**未采用本功能的自动覆盖策略**。
- 完整SSE可能包含敏感业务数据，必须放在权限受限的磁盘目录（POSIX 0600/0700，Windows NTFS ACL）；原始流不推送飞书或加入发布包。

## v1.9.0 报告结构与输出介质分离

`build_report_document` 现在一次生成通用 JSON v6 数据模型（summary/results/failure_cases/slow_token_cases）；`render_markdown` 不做文件IO；`OutputRegistry` 控制落地。当前 `LocalFileOutput` 保持按时间生成 `probe-*.json/.md` 与原字段结构，`write_reports` 旧入口兼容。

控制台 `report_outputs` 返回每种输出的独立回执（`type/status` 和本地 JSON/Markdown 路径）。未来Bitable、CloudDoc等Writer需要返回经过服务器确认的稳定记录ID/文档ID；仅发出HTTP请求不能视为已写成功。云端默认只能收到 `public_projection`，绝不能直接复制私有语料、完整响应和原始SSE。`report_outputs` 与飞书群通知和 `latest-failed-sse` 原始流诊断是三条不同的输出路径。

- v1.9.0 标准化 `ReportDocument` 新增 `run_id` UUID，未来同一轮写入多种输出时可据此关联，保留旧 v5 报告的只读兼容，不影响历史报告读取。