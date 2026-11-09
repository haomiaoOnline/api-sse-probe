# 报告输出适配器设计｜v1.11.0

目标：拨测器只负责读取用例、执行 SSE、业务断言和生成标准化报告，**不直接操作文件或云 API**。每种输出介质通过独立的 Writer/Sink 实现，避免以后将飞书多维表格或云文档写入逻辑塞入 `probe.py`。

## 当前已经实现

- `scripts/lib/report_document.py`：`build_report_document()` 生成结构化 JSON v6；`render_markdown()` 纯渲染、不写文件；`public_projection()` 生成限制字段的团队共享视图。
- `scripts/lib/outputs/base.py`：`ReportOutput` 的最小接口约定与 `OutputContext`。
- `scripts/lib/outputs/file.py`：`LocalFileOutput.publish(document,context)`，保存私有 JSON + Markdown（0600 POSIX）。
- `scripts/lib/outputs/registry.py`：`register_output(kind,factory,privacy="shared")`、`validate_report_outputs()` 和 `dispatch_report_outputs()`，白名单控制适配器。
- `scripts/probe.py`：只负责报告对象生成和派发；`write_reports()` 仍保留旧入口、内部委托给输出分发器，确保旧调用方兼容。
- `scripts/lib/failed_sse.py`：FAIL/ERROR 原始响应字节的私有最新快照；`failure_cases` 同时包含可读完整响应文本，供本地 JSON/Markdown 直接排障。无损 Base64 快照仍是精确字节依据，不进入远程输出；`feishu_notification_router.py` 仍是现有群消息通知渠道，不等同于飞书多维表格/云文档持久化。

## 架构

```text
CSV / JSON / Tencent Sheet → CaseNormalizer → SSE Executor → Assertion Engine
                                                   |
                                            ReportDocument (JSON v6)
                                                   |
                              +--------------------+--------------------+
                              |                                         |
                        OutputRegistry                         NotificationRouter
                              |                                (Feishu群自动分页摘要+全部失败编号)
                    +---------+----------+
                    |                    |
                FileOutput         Future Cloud Output
                (private)          (public_projection)
                JSON + Markdown       |          |
                                 FeishuBitable  FeishuCloudDoc
                                 [未实现]        [未实现]

另一路：SSE Executor → LatestFailureCapture → 私有按目标覆盖的完整失败SSE
```

## 报告数据契约

`build_report_document(source,config,results,cases,wall_duration_ms)` 返回：
- `version=6`、`run_id`（整轮全局UUID，用于未来跨输出目标幂等关联）、`timestamp_utc`、`source_type`。
- `summary`：总数、PASS/FAIL/ERROR、首业务帧、耗时和通过率。
- `results`：逐用例状态、耗时、HTTP 码、失败原因。
- `failure_cases`：场景、完整用户语料、失败断言、完整已接收响应文本及本地私有证据校验元数据。
- `slow_token_cases`：首业务帧超过阈值的用例。

`render_markdown(document)` 不做 IO；本地私有 JSON/Markdown 的 `failure_cases` 包含完整语料、失败判断和实际接收的完整 SSE 文本。`public_projection(document)` 只保留明确允许的 ID、场景、状态、耗时、失败断言等字段，排除语料、完整响应、私有快照路径和请求 ID。**即使只剩场景与断言，也可能属于内部业务信息，云写入前必须经成员批准并设置目标权限。**

## 现有配置（向后兼容）

```json
{
  "report_outputs": [
    {"type": "file", "enabled": true}
  ]
}
```

- 旧版配置没有 `report_outputs` 时仍默认为本地 `file`，输出 `reports/probe-*.json` 和 `.md`。
- 目前内置仅 `file`；`feishu_bitable`、`feishu_doc` 等属于未来适配器，尚未注册。若直接在配置中启用，会在实际业务 POST 之前明确报 `report_output_not_supported`，**不会假装已经保存或暗中丢失报告**。
- 输出清单不能空、不能全部关闭、不能重复类型。`file` 输出原有完整私有报告，并返回 `{"type":"file","status":"saved","json_report":"...","markdown_report":"..."}` 回执。
- 当前 `feishu.enabled` / `notification_style` 仍只管理群内通知，**不代表已支持飞书多维表格或云文档写入**。
- 如果将来配置仅云端输出，`share_report.py` 的历史补发仍依赖本地 JSON；需要另加受控云端回读能力，不能擅自沿用本地命令。

## 新 Writer 接口

可信开发者必须实现 `ReportOutput.publish(document, context)`：成功保存后返回字典（`type`、`status="saved"` 和目标稳定 ID/URL）；失败必须抛异常，**不能仅凭发起 HTTP 请求就宣称持久化成功**。

```python
class YourApprovedOutput:
    def publish(self, document, context):
        # document: 默认只有 public_projection 的数据
        # context: 无API认证、无用户完整请求/原始SSE
        # 凭据：未来 Sink 自身的私有配置或团队密钥管理
        # 验证服务端确认存储成功后返回
        return {"type": "your_output", "status": "saved", "reference": "record-id"}
```

通过可信代码显式调用 `register_output("your_output", factory, privacy="shared")`；不允许在 JSON 中填写任意模块名后动态 import。注册额外云端适配器默认只允许 `shared` 数据视图，不能申请 `private` 全量数据。未来需要导出完整响应时，必须单独设计分级授权和日志审计，而不是绕过安全投影。

## 飞书多维表格与云文档：建议后续功能

| 新适配器 | 数据设计建议 | 验收要求 |
| --- | --- | --- |
| `FeishuBitableOutput` | 一张 Run 表 + 一张 CaseResult 表，以 `run_id + case_id` 幂等 upsert | 应用授权、目标表/字段映射、分页批量、幂等、权限、限流与重试门禁 |
| `FeishuCloudDocOutput` | 一轮一个云文档或每目标一个最新文档，内含概览、用例和失败诊断链接 | 富文本块限制、文件夹权限、协作者、文档版本与更新冲突、敏感字段脱敏 |
| `TeamWebOutput` | 可授权查询的历史报表和项目维度 | 登录、角色权限、数据留存策略、审计 |
| `LocalFileOutput` | 目前私有 JSON + Markdown，可升级为SQLite | POSIX/NTFS访问控制、历史清理和可用空间 |

**以上 Bitable、Cloud Doc 仍属设计与接口预留，不是已经连通的真实写入能力。** 对任何新云服务，先在获授权的测试应用、测试群/文档中验证写入、读取、回滚，再允许生产配置开启。

## 必须通过的验证

- 旧配置不声明 `report_outputs` 时 JSON v5/Markdown 和报告路径完全兼容；`write_reports` 旧调用仍有效。
- `report_outputs` 写未注册类型/全部禁用/重复类型时，在发送业务 SSE 之前报错。
- 测试用的假云适配器仅能收到 `public_projection`，不含语料、响应摘录、设备号、请求ID、原始SSE。
- 飞书群通知 v1.12.0 优先单卡（五指标+全部 FAIL/ERROR 编号），超容量则全量预检并分页发送；真实云写入需要单独权限授权和实际测试，不能用模拟回执代替生产验收。