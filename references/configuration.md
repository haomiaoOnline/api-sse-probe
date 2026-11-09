## v1.9.1 注释模板与精简原则

- **真实运行文件**：`config.local.json`（标准JSON，禁止`//`或块注释），参数按需出现；默认值依照Python代码生效。
- **注释模板**：仓库根目录的 [config.annotated.jsonc](../config.annotated.jsonc)；每个参数前均有用途/默认值/使用场景注释，是公开演示而非可直接运行的配置。不得填写或提交真实Webhook、签名、Token、设备ID。
- **简洁示例**：`config.example.json` 只展示无密钥的CSV测试配置。需要腾讯文档时，替换其`source`结构为JSONC文档所示，配置`source.tencent.doc_url/file_id/sheet_id/range/header_row/columns`及`TENCENT_DOCS_TOKEN`等个人授权。
- **精简但保留行为一致性**：腾讯模式不需要`source.path`或空`source.tencent.sheet_name`；`assertions.default_mode`是未使用的旧字段；HTTP200、连接超时8秒、本地目录`reports`、飞书超时10秒均可依赖默认值。保留自定义闲置/总超时、`require_done`、响应字节上限、`assertions.require_keywords`、飞书`keyword/notification_style`等安全和协作字段。
- **兼容注意**：`source.tencent.file_id`虽然与`doc_url`重复，但当前`failed_sse.target_key()`把配置中的`file_id`用于“最新失败SSE”快照文件名；删除会改变已有快照位置，须先做兼容迁移。`sheet_id`同样用于群卡片源文档链接。
- **真实请求/飞书发送**：`--check` 仅当用户明确要求自检时运行，不是拨测前必需步骤。用户明确提供腾讯文档 URL 并说开始拨测，即本轮应覆盖旧来源身份字段、按新表中当前读取范围内启用有效用例执行；正式拨测默认向已配置群发送，仅用户明确要求测试不通知时加 `--no-feishu`。

# 配置参考（v1.9.0，面向团队）

公开模板：`config.example.json`；个人配置：`config.local.json`，后者不属于 Git/团队ZIP。可用 `--config config.smoke.local.json` 指定另一份**私有**测试配置。首次配置建议从模板复制，不要将某位同事已经填好的配置拷贝给他人。

## 一、顶层 JSON 结构

```text
api                     HTTPS POST JSON目标、请求认证、连接超时、SSE响应限制
source                  csv/json/tencent数据源与字段映射
tencent_docs_auth       腾讯文档只读MCP Token（仅tencent需要）
assertions              断言默认匹配模式、是否要求业务判断条件
reports                 本地报告目录、慢首帧阈值
feishu                  是否启用群通知、WebHook、签名、群关键词、v2样式
```

**默认公开模板**：`source.type=csv`、`source.path=assets/sample_cases.csv`、`feishu.enabled=true`、`feishu.notification_style=v2`。模板无 Webhook/密钥，`--check` 仍可只读运行；真实业务须先配置获授权群及 API URL/认证，腾讯模式还需独立 Token/文件权限。用户明确要求仅测试不通知时使用 `--no-feishu`。

## 二、API 与安全控制

```json
"api": {
  "url": "https://test.example.com/api/stream",
  "headers": {
    "powerkey": {"env": "MY_API_POWERKEY"},
    "signature": {"env": "MY_API_SIGNATURE"}
  },
  "expected_http_status": 200,
  "connect_timeout_seconds": 8,
  "idle_timeout_seconds": 20,
  "total_timeout_seconds": 60,
  "require_done": true,
  "max_event_bytes": 1048576,
  "max_response_bytes": 4194304,
  "body_defaults": {"endpoint": {}, "session": {}}
}
```

- `powerkey` / `signature` 支持直接填写**私有**字符串或 `{"env":"NAME"}` 引用已存在环境变量。`timestamp` 无须写入配置：每个POST自动生成Unix毫秒值。**不会自动计算signature**，当服务端签名需要依赖timestamp时，必须扩展新的签名策略。
- HTTPS / TLS证书验证必需，重定向默认不跟随，不能关闭TLS绕过问题。`connect_timeout_seconds`、`idle_timeout_seconds`、`total_timeout_seconds` 是分别用于连接、流空闲、整请求的边界（并非外部进程的硬超时）。
- 固定请求体形式：`endpoint`、`stream=true`、`session`、`messages=[{role:user, content:语料}]`、`deviceId`、自动 `reqId`。不是随意配置JSON模板。
- 用例 `endpoint_json` / `session_json` 可覆盖默认值（如果来源支持这些字段）；标准八列表格没有这两列时使用 `body_defaults`。

## 三、CSV / JSON 数据源

```json
"source": {
  "type": "csv",
  "path": "assets/sample_cases.csv"
}
```

本地相对路径相对于 Skill 根目录解析。CSV使用表头；JSON必须是对象数组。最少必须有 `case_id`、`content`、`deviceId`、`expected_keywords`。可选字段包括 `scene/category/owner/enabled/special_keywords/forbidden_keywords/match_mode/endpoint_json/session_json`。CSV/JSON中缺少设备ID是硬错误，不像腾讯文档自动跳过空设备行。

断言支持分隔符 `;`、`；`、`|`、`/` 和换行，默认 AND。`match_mode=any` 可调整预期条件匹配方式；禁止词命中仍失败。完整参考 [assertions.md](assertions.md)。

## v1.13.0 单次拨测覆盖腾讯文档 URL（推荐）

当用户在 Agent 指令中提供新的 `https://docs.qq.com/sheet/EXAMPLE_FILE_ID?tab=EXAMPLE_TAB 并要求开始拨测，直接传递：

```bash
python3 scripts/probe.py --source-url 'https://docs.qq.com/sheet/EXAMPLE_FILE_ID?tab=EXAMPLE_TAB'
```

`--source-url` 是**本轮覆盖**：新 URL 的文档 ID 和 tab 自动优先于配置中的旧 `source.tencent.doc_url/file_id/sheet_id/sheet_name`，旧源不同不报冲突、不写回私有配置文件。仍继承 `range/header_row/columns` 和腾讯文档 Token/业务 API 鉴权；如果配置没有 `source` 节，也支持通过 URL 从标准表头读取。自动拨测的是当前 `range` 内所有启用且合法的用例，不要求用户先提供 case_id 或 `--check`；只有指定单例时再使用 `--case-id`。`--no-feishu` 仅对本轮禁止飞书群投递，用户明确要求测试不通知时使用。URL 缺 tab、域名不可信、权限不足或列映射不兼容时业务 POST 前报错，不回退旧文档。

## 四、腾讯文档电子表格

配置 `source.type="tencent"`，Token放到 `tencent_docs_auth.token` 或环境变量 `TENCENT_DOCS_TOKEN`：

```json
"source": {
  "type": "tencent",
  "tencent": {
    "doc_url": "https://docs.qq.com/sheet/EXAMPLE_FILE_ID?tab=EXAMPLE_TAB",
    "file_id": "",
    "sheet_name": "",
    "sheet_id": "exampleTab",
    "range": "A1:H500",
    "header_row": 1,
    "columns": {
      "case_id": "序号",
      "scene": "场景",
      "category": "类别",
      "content": "语料",
      "expected_keywords": "判断依据（公共）",
      "special_keywords": "特殊判断依据",
      "owner": "研发负责人",
      "deviceId": "deviceId"
    }
  }
},
"tencent_docs_auth": {"token": ""}
```

### 典型 A-H 八列

| 列 | 表头 | 字段 |
| --- | --- | --- |
| A | 序号 | `case_id` |
| B | 场景 | `scene` |
| C | 类别 | `category` |
| D | 语料 | `content`（实际发送给业务接口） |
| E | 判断依据（公共） | `expected_keywords` |
| F | 特殊判断依据 | `special_keywords` |
| G | 研发负责人 | `owner`（仅元数据） |
| H | deviceId | `deviceId`（真实请求使用，不发群） |

`columns` 可采用准确的表头名称，也可以采用Excel绝对列字母。`header_row` 是工作表的**绝对行号**，并且应位于读取的 `range` 内；若所读范围**没有表头行**，设为 `0` 并使用列字母映射。

例如小批次选 `A2:H11`，可以设 `"header_row":0`，列映射设为 `case_id:A`、`scene:B`、`category:C`、`content:D`、`expected_keywords:E`、`special_keywords:F`、`owner:G`、`deviceId:H`。**这只是选取工作表行范围，必须 `--check` 查看真实业务ID后才能发 POST。**

无设备 ID 的腾讯文档行（示例/分组标题或漏填正式行）会跳过，`--check` 报 `skipped_missing_device_id`，绝不能忽略这个计数。`同上` 在公共/特殊判断列分别继承上一有效业务行，被跳过行会断开继承链。

### MCP 工作方式

官方服务 `https://docs.qq.com/openapi/mcp`；脚本自行调用只读 `sheet.get_sheet_info`（配置名称定位时）和 `sheet.get_cell_data`。单次读取限制20000个单元格；`A1:H500` 计4000个单元格。文件必须是可由这些工具读取的电子表格，Token需具备当前表格权限。**不需要另装所谓官方腾讯文档 Skill**。

```bash
python3 scripts/check_tencent_mcp.py  # 工具可用性，不读任何私人表格
python3 scripts/probe.py --check       # 真实只读数据源与用例内容
```

`check_tencent_mcp.py` 成功并不保证目标文档可读。若另配置 `sheet_name`，程序会核实它与 `sheet_id` 指向同一工作表，冲突时报错。

## 五、飞书群与本地报告

```json
"feishu": {
  "enabled": true,
  "WEBHOOK_URL": "",
  "SECRET": "",
  "keyword": "API拨测",
  "timeout_seconds": 10,
  "notification_style": "v2"
},
"reports": {"dir": "reports", "slow_token_threshold_ms": 3000}
```

- **默认开启**：首次真实拨测前填写获授权群的 `WEBHOOK_URL` / `SECRET`，否则在业务 POST 前报错。`keyword` 必须满足群机器人的关键词策略。用户明确要求本轮仅测试不发通知时加 `--no-feishu`；群Webhook的签名以Unix**秒**时间戳HMAC实现，业务 POST header timestamp 则是**毫秒**。
- `v2`：五指标完整卡片；若全部 FAIL/ERROR 编号装不下单卡，则全量预检后自动分页，每张标注页号，文档链接过滤敏感查询串。`legacy`：不满足完整失败清单要求而被拒绝；`auto`：与 v2 共用分页路径，不自动降级重试或重发不确定页。
- 旧配置 `include_failure_content` 与 `include_failure_response_preview` 已弃用，即使仍设为 true 也不会将请求语料或助手响应全文发到群里。完整 SSE 仅允许在本地私有存储核对。
- 本地报告为JSON v6/Markdown，含失败语料和完整已接收响应（旧 v5 只读兼容），仅授权工程师可读，不能当成可由群内所有成员打开的在线文档。

## 六、调试边界与退出码

- `python3 scripts/probe.py --check`：只有用户明确要求离线自检时才运行，仅检查本地/腾讯文档用例结构，不发业务 SSE 和飞书；`--case-id`：单个case ID的真实业务请求（除非附带 `--check`）。
- `python3 scripts/share_report.py --report latest`：验证最近完整报告，**不发飞书**；加 `--send` 会再次发群并标识历史报告，不调用 SSE。
- 退出码0=业务PASS，1=至少一例FAIL/ERROR，2=配置/读取/报告异常，3=业务全部PASS但通知失败。必须另查 `feishu_notification` 的状态，可能出现“业务不通过但报告/飞书都成功”。
- 密钥、设备ID、完整响应不得出现在Git、普通Agent对话或群消息中；Windows NTFS ACL 必须单独核实。

## v1.9.0：失败原始SSE保留配置

在私有 `config.local.json` 的 `reports` 中设置（默认已在公开模板中）：

```json
"reports": {
  "dir": "reports",
  "slow_token_threshold_ms": 3000,
  "failed_sse_max_total_bytes": 134217728
}
```

`reports/latest-failed-sse/<hash>.json` 仅留存**当前目标最近一次完整执行**的 FAIL/ERROR 原始SSE接收字节（Base64格式）。目标按 API URL + 用例源/工作表 + 默认endpoint摘要区分，下一轮整批结束后原子覆盖；不同目标不会互相覆盖。全部PASS时清空旧失败（写入零条）；异常中止时不破坏以前的完整快照。默认累计上限128MiB，最高512MiB；超过则显式失败。文件是本地敏感信息，不发飞书，不要上传Git。注意不同目标仍各有一份文件，历史时间戳普通报告不会自动删除。

## v1.9.0 报告输出目标配置（与飞书群通知分离）

默认公开模板增加顶层 `report_outputs`：

```json
"report_outputs": [
  {"type": "file", "enabled": true}
]
```

- `file` 是目前唯一内置Writer，输出私有 JSON v5/Markdown，与旧版本行为一致；旧配置完全省略该字段时仍使用 `file`。
- 已预留多个Writer的注册接口，但 `feishu_bitable` 和 `feishu_doc` **还不是可使用的类型**。写入后会在真实SSE请求前以 `report_output_not_supported` 拒绝执行，不会悄悄吞掉结果。
- 输出目标不允许重复、空数组、全部关闭或非布尔 `enabled`。未来可信插件要开发并注册、取得对应云应用授权、测试后才可增加配置。
- 群消息仍由单独的 `feishu` 配置控制，而不是 `report_outputs`；完整失败原始SSE由单独私有 `LatestFailureCapture` 写入并按目标覆盖，不会进入Writer默认数据。
- 详见 [output-adapters.md](output-adapters.md)。


## 2.2.0-dev：飞书 Sheets 九列用例来源（开发分支）

飞书电子表格新增 `source.type=feishu` 与 `source.feishu.doc_url / range / header_row / columns`，字段和值语义保持原腾讯文档九列完全一致。凭据放在独立私有 `feishu_docs_auth.credentials_file`（或私有 `app_id/app_secret`、环境变量）；禁止把鉴权字段放入 `source.feishu`，防止进入冻结快照。

可使用 `--source-url 'https://example.feishu.cn/wiki/EXAMPLE_ID<Token>?sheet=<sheetId>'` 进行本轮只读来源覆盖；飞书群机器人通知仍使用原有独立 `feishu` 配置，两者不能混淆。严格禁止将生产密钥、运行证据、用户语料复制到示例表。离线模拟通过，真实云端建表和读取尚未验收。详见 [完整设计与配置示例](architecture.md)。


## v2.2.0 live status

Feishu Sheets application authentication, isolated test folder and spreadsheet creation, nine-column write/readback, and public read-only --check were verified on 2026-11-07. Older candidate-status text above was written before this acceptance. See `../docs/governance/feishu-live-acceptance-2026-11-07.md`. User-owned client visibility of app-created assets is not established by API identity alone.