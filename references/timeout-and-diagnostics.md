# 请求超时与诊断

拨测阶段分开记录：`dns`、`tcp_connect`、`tls_handshake`、`request_send`、`response_headers`、`sse_read`。不要把“连接超时”直接等同于“业务服务器未收到请求”。

## 主要配置

| 配置项 | 作用 |
| --- | --- |
| `connect_timeout_seconds` | DNS、连接、TLS 及请求发送阶段的预算 |
| `response_header_timeout_seconds` | POST 发出后等待响应头的预算 |
| `idle_timeout_seconds` | SSE 无新数据时的读取等待预算 |
| `total_timeout_seconds` | 单条用例的整体截止时间 |
| `max_event_bytes` | 单个 SSE 事件体积上限 |
| `max_response_bytes` | 单条请求已接收响应字节上限 |
| `require_done` | 是否要求接收 SSE 的 `[DONE]` 标记 |

应根据接口 SLA 和授权测试环境配置预算，不能为了得到 PASS 随意忽略协议错误或关闭 TLS 验证。

## 排障原则

1. 保存准确的错误阶段、实际收到的响应字节数、HTTP 状态和安全的异常类型。
2. `timeout`、`HTTP null`、无 SSE 帧只能说明**当前客户端证据不足**，不能断言请求绝对未到服务端。
3. 对已发送但结果未知的请求，需用私有 `reqId` 和服务端日志对账；不得默认重发或按 FAIL 统计。
4. 网络恢复、SSE 正常完成和业务断言通过是三个不同结论，应分别记录。