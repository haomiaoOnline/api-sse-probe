---
name: api-sse-probe
description: Run authorized HTTPS JSON-to-SSE probes with durable serial capture, deterministic assertions, Tencent/Feishu Sheets or CSV/JSON case inputs, private reports and approved Feishu notifications. Requires configured credentials and authorized target.
license: MIT
compatibility: Python 3.9+；标准库，无 pip 依赖；需按功能访问 HTTPS API、腾讯文档 MCP、飞书群机器人
metadata:
  version: "2.4.3"
---

# api-sse-probe｜Agent 执行契约

> **正式源码唯一入口：** 当前唯一正式源是本仓库**根目录** `SKILL.md` 和 `TEAM_MANIFEST.txt`。仓库里的嵌套 `api-sse-probe/` 为未同步历史副本，不得直接作为最新版本加载或未经核实覆盖；ZIP 中同名目录只是安装布局。当前能力、关键执行分支和验收状态请先读 [v2.4.3 权威工作地图](docs/governance/current-v2.4.3.md)。与早期文档冲突时以本入口、当前代码/测试为准。


**重要：SKILL.md 是 Agent 指令入口，不是独立可执行程序。** 新成员必须收到完整、经审核的 `api-sse-probe/` 目录或 release ZIP，至少含 `scripts/`、`references/`、`assets/`、`config.example.json`、`tests/`。只收到本文件时，停止执行、索取完整包；不得臆造脚本。**禁止分发前一位成员的私有配置或报告。** 可在经过审查的源码目录执行 `python3 scripts/build_release.py` 生成仅含发布清单的 ZIP。

## v2.4.3 未完成拨测飞书报告含只读链接

当所有 PENDING 归零仍有 UNKNOWN 时，立即把已有真实 SSE 证据生成独立飞书云文档，未决用例单独展示并标明没有可靠响应，不能伪造 PASS/FAIL。非 UNKNOWN 用例保持失败优先与成功后置，真实响应为脱敏原生代码块。飞书未完成通知使用文档名-Sheet名 丨 拨测报告（未完成），包含经只读权限和正文回读验证的云文档链接，并保留群机器人安全关键词。部分与完整归档互不覆盖，旧通知不盲目重发。已使用 164 条真实证据验证并补发链接卡片 SENT。见 [验收说明](references/architecture.md)。

## v2.4.2 UNKNOWN 自动补测一次（仅新运行）

用户已明确批准发生执行平台超时时对 UNKNOWN 业务请求重新执行。此特性仅对启用 `unknown_retry.enabled=true` **之后新建的运行**生效，单用例最多自动重试一次，新业务 `reqId` 与原请求的 `reqId` 均进入私有 SQLite `retry_history` 审计表；旧已保存响应文件不覆盖。发生第二次 UNKNOWN 或发现旧证据已落盘则停止自动重试，避免无限重复。**同一业务动作可能执行两次，必须接受这个风险。** 历史运行（包括已存在的 #137）保持原有禁止自动重试语义。详见 [设计与验收说明](references/architecture.md)。

## v2.4.1 飞书云文档失败优先排版

每次云归档严格包含两个一级主题：「运行概览」「拨测完整信息」。完整信息分为「失败用例列表」（FAIL/ERROR 前置）和「成功用例列表」（PASS 后置），每个用例依次显示序号、测试语料、失败原因、响应证据，响应以飞书原生 Code Block（`block_type=14`）展示，保留全部已接收内容的脱敏版本；超容量不得截断冒充完整。已用 9 条历史真实 SSE 证据进行飞书云端排版验收，60/60 块完整回读，9/9 原生代码块和链接只读权限通过；不重新请求业务 API、不修改历史原文。详见 [v2.4.1 排版验收](references/architecture.md)。

## v2.4.0 飞书云文档逐次归档及只读群卡片链接

每次完整拨测后先保留原始 SSE/SQLite、完成离线断言和私有 JSON/Markdown 报告，再自动创建**1 份飞书 Docx** 归档，位于统一的云端文件夹。正文逐条展示业务结果、确定性失败原因、证据 SHA-256 与**脱敏后的全部已接收 SSE 字节**；未经脱敏的原始 SSE 不上传到任何人持链接可见的文档。云文档必须通过 `anyone_readable` 写入和官方 GET 读回验证，才允许向群发送含云文档链接的飞书 Card 2.0。卡片标题按 `原文档名-Sheet页名 丨 拨测报告`，保留机器人安全关键词。

当前 macOS 私有配置已启用 `feishu_archive.enabled`，凭据位于用户自己的 `feishu_docs_auth.credentials_file`，腾讯文档名映射位于 `feishu_archive.document_titles`。`--no-feishu` 关闭归档和群推送；`--check` 仍纯只读；UNKNOWN/PENDING 不发布假完整文档。在线验收已确认专属文件夹、合成文档及 9 条历史真实 SSE 归档的正文/权限回读，并完成一条新真实 SSE → 飞书 Docx → 群卡片 SENT 的闭环，详见 [实施与安全边界](references/architecture.md)。**原始腾讯/飞书用例表不写入、不修改响应结果列。**

## v2.3.1 nested video-compound rules

Explicitly quoted, parenthesized AND/OR expressions combined with an exact `skillId` in a video control are deterministically parsed and checked against the same code=4145 control's header/body IDs and assistant business content; unknown syntax still rejects before POST. Tested against real 9-case 集团展厅 sheet, 303 offline tests, and 57 historical cases replayed with no changed outcome. Company sheet 19-case live run also completed. The document has four tabs, but the 4.132 tab is empty and the 165-case backup tab has unresolved #140 UNKNOWN and was not re-probed. See [three-sheet live acceptance](references/architecture.md).

## v2.3.0 deterministic assertion engine

In v2.3.0 each positive requirement is compiled into a closed deterministic execution kind before any business POST; the engine does **not** use LLM semantic intent judging. In the private per-case result the `assertion_trace` contains versioned rule hashes, rule kind, evidence scope and pass/fail; lack of complete SSE is `assertion_evaluation_status=NOT_EVALUATED` while preserving existing PASS/FAIL/ERROR reports. Ambiguous rules cannot become a successful keyword fallback. Twenty human-labelled synthetic examples pass, and 38 stored historical real responses replay with no changed status/reason. This does **not** establish 100% real-world semantic accuracy. See [deterministic assertion design and acceptance](docs/governance/deterministic-assertion-v2.3-design.md).

## v2.2.0 release scope

The unified scripts/probe.py executes all frozen cases serially, persists each SSE evidence item, judges offline, saves reports and notifies the authorized Feishu bot. Feishu Sheets is now a real-API-validated read-only input (folder/sheet creation, nine-column write/readback, and public --check). See docs/governance/feishu-live-acceptance-2026-11-07.md. Windows native and actual power-loss testing remain deferred. Personal Feishu client access to app-owned folders is not inferred from application API permissions.

## 适用范围与明确限制

- **Supported inputs and scope**: Authorized HTTPS POST JSON to SSE only, with endpoint, stream:true, session, messages[0].content, deviceId, UUID reqId, powerkey/signature request headers and runtime timestamp. CSV, JSON, Tencent Docs read-only MCP and Feishu Sheets read-only API are supported. Feishu Sheets live cloud create/write/readback and public check verified on 2026-11-07. This is not an arbitrary API or write-back client.
- **不是任意 API 的零配置客户端**：不同 API 的认证算法、请求体结构、SSE 帧结构及断言规则必须写新适配器和测试。当前 `signature` 按原样发送，**不自动计算和更新**。
- **输出适配器（v1.9.0）**：标准化报告由 `lib/report_document.py` 构建/渲染，交由 `lib/outputs/registry.py` 选择Writer。默认 `report_outputs=[{"type":"file","enabled":true}]` 通过 `LocalFileOutput` 输出私有JSON/Markdown。飞书多维表格/云文档仍属**未实现的扩展接口**，在使用未注册类型时，必须在真实业务POST前拒绝执行。未来云适配器只能默认获得删掉语料、助手响应、请求ID的 `public_projection`，任何云写入都需成员授权。
- **输出（v1.12.0）**：PASS/FAIL/ERROR、首业务帧与完整耗时、JSON/Markdown 私有报告；飞书 Card2.0 以单卡为优先，超过单卡容量则预检并自动拆为带序号的多张卡片。每条 FAIL/ERROR 编号恰好出现一次；任何页预检失败时零投递，发送中途失败则停止后续页，明确已确认投递数、失败页和回执不确定性，绝不自动重试。
- **本轮文档覆盖（v1.13.0）**：用户明确发送 `https://docs.qq.com/sheet/<文档ID>?tab=<工作表ID>` 并要求“拨测这个文档/开始拨测”时，**该链接优先于 config.local.json 中旧文档**，直接以 `python3 scripts/probe.py --source-url '<完整链接>'` 加载该工作表，串行执行所有启用且有效的用例（限既有 `range`）；**不需要额外索取 case_id、数量或先运行 --check**。旧 `file_id/sheet_id/sheet_name` 在本次运行中清除，新 tab 唯一生效；不得修改私有配置文件。正式拨测默认发送到私有配置中已授权的飞书目标群；仅用户明确要求测试不通知或不发飞书时附加 `--no-feishu`。
- **Feishu Sheets v2.2.0**: Use --source-url with a trusted HTTPS Feishu Sheets link, or source.type=feishu with feishu_docs_auth kept private; --check never sends business POSTs or bot notifications. Live cloud creation, write/readback and public --check have passed. Client-side personal access permissions are a separate user check. See docs/governance/feishu-live-acceptance-2026-11-07.md.
- **分阶段超时与诊断（v1.10.0）**：真实HTTP请求分别标记 `dns/tcp_connect/tls_handshake/request_send/response_headers/sse_read`。`connect_timeout_seconds` 默认8s用于DNS/TCP/TLS/发送，`response_header_timeout_seconds` 默认20s专管POST发送之后的HTTP头等待；`idle_timeout_seconds` 专管SSE读取，`total_timeout_seconds` 是每个阻塞阶段都必须遵守的绝对总截止。失败报告新增 `error_phase/timeout_kind/http_headers_received/response_bytes_received/stage_timings_ms/exception_chain`；只记录异常类型，不能凭响应头前超时直接认定服务端未收到请求，超时数值须依接口SLA约定。详见[超时与诊断协议](references/timeout-and-diagnostics.md)。
- **结构化字段断言**：引号包裹的词如 `“机票”` 规范化为 `机票`；支持助手业务 JSON 字段 `deeplink`、`sdk.package` 的 `以…开头`、`是/等于`、`包含`、`存在/不为空`，并提供真实字段缺失/值不匹配诊断；不搜索用户请求语料或顶层元数据。
- **复合断言（v1.13.2）**：`包含“新闻联播”“CCTV1”，skillId 是"VIDEO..."` 同时校验助手文本或视频控制参数中的每个业务词，以及同一 `code=4145` 帧内的双 `skillId`；`title是“灵犀服务”，内容包含“小猫”“喂食”` 分别校验助手标题与回答内容。衍生问题、用户请求及顶层元数据不计入上述业务判断；失败报告显示缺失项与实际值。
- **明确类型与自然语言条件（v1.16.0，默认）**：公共列列出实际检查类型，如 `HTTP 200 / 文本回复 / 衍生问题 / title / skillId / SDK / deeplink`；只填写该用例需要的类型。特殊列写明确条件，例如 `title是“灵犀服务”`、`回复中包含“杭州”和“天气”`、`回复中有“音量”或“指令已下达”`、`有追问信息`、`skillId是“完整ID”`、`sdk.package是“包名”`。按业务用例和已知响应设置必需 ID、针对性标题及允许文本分支，不固定随机推荐、日期和价格，不把“围绕主题/体现意图/有效回复”当作判断依据。见 [断言编写说明](references/assertions.md)。
- **Deterministic rule preflight**: scripts/lib/rule_contract.py validates typed assertions, Boolean conditions, structured rules, and precise keywords before any business POST; vague prose and malformed rules are rejected with case ID and column. Existing 80 historical real-world assertions remain valid.
- **字段与文本不可替代**：回复条件只查助手正文，title 只查真实业务标题，追问信息只查非空 questions；skillId 只查真实业务字段，不能从正文或元数据凑齐。同一候选记录若同时含 header/body ID，两者必须一致且等于预期；单独 `skillId是` 不隐含要求指定 code 或必须同时存在双字段。旧精确字段与 JSON 条件保留，v1.15 的宽泛意图写法在真实请求前提示迁移，不再隐式放宽。
- **中文九列 CSV 与有限验收**：可直接读取 `序号、场景、类别、语料、判断依据（公共）、特殊判断依据、研发负责人、deviceId、响应结果`。I列只留作历史参考，绝不作为本轮响应参与判断。用户限定数量时，先从本地完整 CSV 提取不超过上限的批次 CSV，再使用 `--source csv --cases <批次文件>`；不得以默认全表规则覆盖用户数量限制。
- **Default execution**: scripts/probe.py runs every frozen PENDING case serially in one invocation, persists raw evidence per case, evaluates offline, saves private reports, then sends authorized Feishu notifications. Optional max-cases/max-seconds are disabled unless explicitly set. For newly created runs with `unknown_retry.enabled=true`, UNKNOWN is retried once with a new reqId and retained attempt history; legacy runs and second UNKNOWN remain blocked.
- **失败完整原始 SSE（v1.11.0）**：FAIL/ERROR 的实际接收字节以 Base64 无损编码保存在 `reports/latest-failed-sse/<目标摘要>.json`；同时以可读文本形式写入本地私有 JSON/Markdown 失败详情，便于直接排障。Markdown 的原始文本视图对非法 UTF-8 字节使用 `\\xNN` 转义，Base64 私有快照仍是精确字节依据。`capture_state` 区分 `protocol_done`、`partial_or_unterminated`、`partial_or_size_limit`、`http_error_body`、`non_sse_http_body`、`not_received`；不能将部分/非 SSE 响应冒称完整 SSE。单轮失败响应累计默认上限128MiB（可配置，不超512MiB）；触限明确报错并保留上轮有效文件。异常退出不替换旧快照。**完整语料/响应只进入受限本地报告，不进入飞书、public_projection 或团队 ZIP**。
- **失败诊断展示与隐私**：每个失败用例在本地报告中只展示四项：场景、完整语料、具体失败判断原因（含预期/实际）、完整已接收 SSE 响应参数；不重复输出设备ID、reqId、请求/响应头清单、分阶段统计或12000字符响应摘录。必要 HTTP 状态、Content-Type、字节数、捕获状态和 SHA-256 放在 SSE 项内。完整语料和业务响应属于敏感内容，只保存在受限本地 JSON/Markdown 与私有证据快照；飞书、public_projection 和团队 ZIP 均不包含这些内容。Windows 需 NTFS ACL，POSIX 文件权限不能等同 Windows 访问控制。

## Agent 必须遵守的执行顺序

1. **确认完整包与 Python**。定位本文件所在 Skill 根目录，核查 `scripts/probe.py`、`scripts/lib/tencent_mcp.py`、`config.example.json`、`references/`、`tests/` 是否存在；缺失则索取完整包。确认 Python 3.9+、网络策略及明确的目标环境，不假设 Agent 自己有本机文件或授权密钥。
2. **准备个人私有配置**。运行配置 `config.local.json` 必须是**严格 JSON，不支持 // 注释**；首次安装复制简洁的 `config.example.json`（默认 CSV + 飞书开启，但没有 Webhook/密钥，不能直接执行真实拨测），腾讯文档或团队接入时逐项参照 **[config.annotated.jsonc](config.annotated.jsonc)** 的字段注释，并将所需内容填写到私人JSON中。参考模板不包含任何实际密钥，不可直接作为程序配置输入。如不存在 `config.local.json`，从 `config.example.json` 创建（存在则绝不覆盖）。从获授权的成员/密钥管理取得 `api.url`、`api.headers.powerkey`、`api.headers.signature`。腾讯文档的默认源可设置 `source.tencent.doc_url`、`sheet_id` 或 `sheet_name`、`range`、`header_row`、`columns`；本轮有新链接时通过 `--source-url` 覆盖旧身份字段，无需修改配置，仍复用 `range/header_row/columns` 和 `tencent_docs_auth.token`（也支持环境变量 `TENCENT_DOCS_TOKEN`）。首次正式拨测前须在私人配置中填写获授权目标群的 Webhook、密钥和关键词；未配置时运行会在业务 POST 前失败。**禁止将他人凭据写入仓库/聊天/日志**。
3. **离线自检仅按需执行（v1.12.0）**。日常拨测不自动执行 `python3 -m unittest discover -s tests -q`、`python3 scripts/probe.py --check` 或其他离线自检。只有用户明确要求自检、测试、预检时才运行相应命令；飞书源尚在验收阶段，仅在明确授权飞书数据读取、凭据和目标文档后执行；`--check` 只读取 CSV/JSON 或有权限的腾讯文档，不发业务 POST/飞书。只有未指定文档/用例范围或接口授权不明确时才向用户核实；给出带 tab 的链接并要求整份拨测时，范围即该 tab 在配置读取范围内的全部启用有效用例，不得再索取具体 ID 或重复申请只读检查权限。不能猜测行号，也不能把离线检查变成每轮的固定前置步骤。真实执行仍需配置/权限/输出适配器基础校验。
4. **检查输出策略**。私人配置 `report_outputs` 缺省为本地 `file`；禁止将尚未实现的 `feishu_doc/feishu_bitable` 写成已成功接入；必须在任何真实POST前核验输出适配器已注册，群消息 `feishu` 与报告持久化不是一回事。
5. **确定本次范围与授权**。如果用户给出一个带 `tab=` 的腾讯文档链接并说“开始拨测”，即明确授权该文档在**当前配置读取范围内的所有启用有效用例**，Agent 直接使用 `--source-url`，无需要求先 `--check`、手工指定 case_id 或再次确认数量。首次读用例不是离线自检，是正常执行必需的数据加载。API 目标与凭据仍来自已有授权配置。只有用户指定个别用例时才使用 `--case-id <真实case_id>`；仅当多个候选链接、URL 缺 tab、列映射不适配、接口授权缺失或范围矛盾时才澄清。**不能以配置中的旧文档不一致为理由拒绝新文档**。
6. **真实执行与客观判定**。正式版统一入口 `probe.py` 默认 v2；指定新腾讯文档时执行 `--source-url '<完整文档链接>'`；无新链接时沿用配置默认源，单用例才使用 `--case-id`。正式拨测默认发送飞书；仅用户明确表示仅测试不通知或不发飞书时添加 `--no-feishu`。`0`=全PASS；`1`=有 FAIL/ERROR；`2`=配置/用例/报告读取等失败；`3`=业务全PASS但通知失败。退出码非零也可能已经生成报告/发送群消息，必须同时检查 JSON 报告与通知回执。HTTP200或飞书 `sent` 不等于业务PASS。
7. **核对失败原始流**。正式发布 v2.0.1 的旧流程有 `failed_sse_report`，而当前统一入口通过 `run_dir/evidence/` 保存每条已接收证据；确认本轮全部用例及常规 JSON/Markdown 已成功完成后，历史兼容入口可能给出 `failed_sse_report`（仅授权工程师阅读）。`failed_cases[].raw_sse_base64` 无损还原实际收到的 SSE 数据，`received_bytes` 和 `sha256` 用于验证，`capture_state` 说明完整/部分/未收到。该文件按目标覆盖，不自动归档历史失败流；不得发到群内。
8. **团队群交付**。配置获授权目标群后 `feishu.notification_style="v2"` 一轮优先一张 Card2.0；超过容量则先构建并校验全量分页，再按 1/N、2/N 顺序逐张发送。每页包含五指标、来源和失败编号的对应子集，不遗漏/不重复；默认页间间隔 1 秒。任何页发送失败或回执不确定时停止后续页，不自动重试；汇报 `cards_total/cards_confirmed/cards_attempted/failed_page/failed_page_outcome`。`legacy` 模式拒绝发送。历史报告先只读 `share_report.py --report latest` 预检分页张数，明确要求补发后 `--send` 才发送；不重跑 SSE。
9. **汇报实际结果**。给出 `total/pass/fail/error`、首帧有效样本量及平均秒数、墙钟耗时、报告文件路径、分页预检张数与群投递状态；分开标注“技术链路通过”和“业务判断通过”。未知根因应列为未确认，不得凭一次 TLS 检查推断所有失败均由 TLS 导致。

## 统一入口续跑约定（v2.4.3 正式版）

使用 `scripts/probe.py` 的首次命令与旧 `--source-url` 用法相同，所有真实 POST 经 v2 状态机执行；默认一次启动即全量串行直至 `COMPLETE`（业务结果有 FAIL/ERROR 时可以返回非零退出码）；仅明确指定可选限额或发生意外退出后，可能需要重启/续跑。返回 `workflow_status=CONTINUE_REQUIRED` 时由 Agent 使用 `resume_command` 继续；新运行启用 `unknown_retry.enabled=true` 时，UNKNOWN 首次在下一次恢复时自动重新请求一次（新 reqId），第二次 UNKNOWN 仍进入 `BLOCKED_UNKNOWN_REQUIRES_RECONCILIATION` 并停止。旧运行不会被自动重试。详见 [统一入口迁移记录](references/architecture.md)。

## 复制即用的安全命令

```bash
# 仅在用户明确授权后执行日常拨测；不自动离线自检
cd api-sse-probe
python3 --version
python3 scripts/probe.py --source-url 'https://docs.qq.com/sheet/EXAMPLE_FILE_ID?tab=EXAMPLE_TAB'
# 仅用户明确要求仅测试不发通知时添加 --no-feishu
# 单个用例（仅明确要求时）：python3 scripts/probe.py --case-id 1

# 仅当用户明确要求离线自检时再执行以下命令：
# python3 -m unittest discover -s tests -q
# python3 scripts/probe.py --check

# 历史报告只读分页预检；不发业务请求、不发飞书
python3 scripts/share_report.py --report latest
# 仅用户明确要求补发历史报告时运行：
# python3 scripts/share_report.py --report latest --send
```

Windows 可用 `python` 或 `py -3` 替代 `python3`；`--source-url` 只影响当前这一轮，继承原有列映射和读取范围，不修改私有配置。单用例示例中的 `1` 必须替换为用户确认的实际 ID。腾讯文档 MCP 内建于 `scripts/lib/tencent_mcp.py`。

## 安全边界与人工门禁

- 禁止将 `config.local.json`、`config.*.local.json`、`reports/`、`.env`、认证头、设备ID、飞书Webhook、Token拷贝进团队包或 Git。签名算法未知不得猜测；TLS验证不得关闭，非明确授权的 HTTP 接口不得访问。
- 腾讯文档通过只读 `sheet.get_sheet_info`、`sheet.get_cell_data`。MCP连接成功≠目标文件有权限≠列映射正确，所有步骤分别验收。
- HTTP状态、合法 SSE、至少1个有效业务JSON帧、必要 `[DONE]` 及业务断言都满足才PASS；FAIL表示断言或协议/业务失败，ERROR表示连接/HTTP异常/超时/配置故障。
- 主要时延指标是首个有效业务JSON帧到达（近似TTFT），秒数显示三位；无业务帧的请求不计入平均时延。默认慢首帧阈值3000ms，内部仍使用ms比较。
- 正式拨测默认向私人配置中的飞书目标群发送失败断言摘要；首次安装须先配置获授权群的 Webhook/签名，否则在业务 POST 前报配置错误。`--check` 永不通知；仅用户明确要求不发通知时使用 `--no-feishu`。

## 进一步阅读（先按需加载，不必全部放进 Agent 上下文）

- [飞书云表格九列兼容与确定性断言契约（开发中）](references/architecture.md)
- [团队安装与授权](references/team-installation.md)
- [Agent 交接与自动操作流程](references/agent-handoff.md)
- [现有架构、执行时序与模块依赖](references/architecture.md)
- [逐参数注释配置模板（JSONC，仅供阅读）](config.annotated.jsonc)
- [配置字段与数据源](references/configuration.md)
- [断言引擎](references/assertions.md)
- [报告与秒数统计口径](references/monitoring-report.md)
- [飞书群卡片容量不足时自动分页](references/feishu-notification.md)
- [HTTP分阶段超时、硬截止与诊断](references/timeout-and-diagnostics.md)
- [排障手册](references/troubleshooting.md)
- [全链路验收清单](references/acceptance.md)
- [工作地图、四级证据与版本治理](docs/governance/README.md)
- [反馈记录与自动回归流程](docs/governance/feedback-loop.md)
- [轻量治理验收矩阵](docs/governance/acceptance-matrix.md)
- [独立开发版 v2 M2-M5 执行器、可靠性门禁](references/architecture.md)
- [统一正式入口迁移、自动续跑约定与人工门禁](references/architecture.md)
- [长批次自动分批、未知请求对账、部分报告与安全恢复](references/architecture.md)
- [报告输出适配器、飞书多维表格与云文档扩展契约](references/output-adapters.md)
- [团队通用化路线图](references/team-generalization-roadmap.md)

**缺任何执行必须的授权信息时，只完成可以安全进行的准备、模拟测试和配置模板工作，并列出仍需用户提供的具体参数；绝不假报安装/运行成功。**