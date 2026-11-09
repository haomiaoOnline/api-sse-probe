# v1.10.0 分阶段超时与诊断协议

本功能用于判断**请求在哪个阶段未完成**，不是自动判定网络或业务服务责任。历史两轮的 `timeout + HTTP null + 零事件/帧`，不能据此判定服务端未接收 POST，也没有证据说明 SSE 解析器漏掉事件。必须结合网关/服务日志中的 `reqId` 完成归因。

## 请求阶段

```text
配置 → DNS → TCP连接 → TLS握手 → 发送POST → HTTP状态行/响应头 → SSE读取/业务断言
          └── connect_timeout_seconds（连接阶段）
                                              └── response_header_timeout_seconds
                                                               └── idle_timeout_seconds
总截止：total_timeout_seconds 从单条拨测开始，覆盖以上所有阶段。
```

| 字段 | 默认值 | 实际作用 |
| --- | --- | --- |
| `api.connect_timeout_seconds` | 8s | DNS、TCP、TLS、POST 建连与发送阶段的阻塞预算；**不是**HTTP响应头等待 |
| `api.response_header_timeout_seconds` | 20s | 发送 POST 后等待 HTTP 状态行/头的独立预算。此分支按已确认需求从8秒提高至20秒；部署前仍应核对接口 SLA |
| `api.idle_timeout_seconds` | 20s | 拿到响应头后单次 SSE 读取闲置等待；收到流片段可刷新等待 |
| `api.total_timeout_seconds` | 60s | 从单请求开始计时的绝对截止时间，约束阻塞读取与慢速分块连续流 |

原有私有配置的 `idle_timeout_seconds=40`、`total_timeout_seconds=90` 不变，不会覆盖独立的20秒响应头等待预算。设备的旧版 Skill 如果在私有配置里显式配置8秒，升级发布时应单独修改至20秒；超过8秒仍无响应头并不能区分网关排队、服务端处理慢或链路问题，也不能证明请求未送达。

生产 HTTPS 流程使用自有标准库分阶段传输：有界DNS解析、TCP/TLS/POST阶段控制、HTTP头单独计时、SSE读空闲与总截止 watchdog。**特殊限制**：Python 系统DNS解析可能无法真正取消底层解析线程，超时会放弃结果；当前直接传输在发现要求HTTPS代理的环境时拒绝静默绕过代理，需另加安全代理适配器。显式注入 `opener` 的测试兼容路径不代表生产阶段精度。

## JSON v5 与Markdown诊断字段

`results[]`、`failure_cases[]` 中可使用：

- `error_phase`：可能为 `dns`、`tcp_connect`、`tls_handshake`、`request_send`、`response_headers`、`sse_read`、`sse_parse`、`business_assertion` 等。
- `timeout_kind`：`response_header_timeout`、`stream_idle_timeout`、`total_deadline_exceeded` 等具体超时类别。
- `http_headers_received`：是否真正取得HTTP响应头；false **不能**证明服务端没有接收POST。
- `response_bytes_received`：实际读取的HTTP响应体原始字节数，不含HTTP头。
- `stage_timings_ms`：观察到的分阶段毫秒耗时，受操作系统调度影响。
- `exception_type`、`exception_chain`、`exception_errno`：仅保留类型和整数错误码，不能回显包含私密URL/Token/语料的原始异常字符串。
- `reqId` 仍用于和网关/服务日志匹配，**不进入飞书群卡片**。

Markdown 现在分别说明响应头是否收到、已收到流字节数和错误阶段，**不再把空响应摘录一律解释为连接超时**。

## 归因门禁

- `response_headers` 失败：客户端在状态行/头阶段中断，POST 可能已完整到达服务端；不能直接下结论为服务端故障。
- `sse_read` 失败：已取得头，流读取停顿或超过整体截止时间。
- HTTP200 + JSON帧 + [DONE] 且断言 FAIL：真实收到了业务响应，不属于零采集。
- 当前 DNS/TCP/TLS 检查成功不能排除历史测试时段的异常。
- 对历史 `155`、`24`、`35` 应以报告保存的 `reqId` 查网关和服务日志；对少量固定输入按授权测试，只调整响应头预算并同步采集两端时间线。

`tests/test_phase_timeouts.py` 在本机 HTTP 服务验证延迟响应头、流闲置、持续缓慢发送、总截止、部分 SSE 字节和TLS握手停顿。不会追加真实业务 POST 或飞书投递。