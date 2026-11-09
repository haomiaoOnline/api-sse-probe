# 团队可复现交付验收清单

本清单适用于首次安装、更新版本和其他 Agent 的接入验证。**离线测试通过≠真实接口通过**。每一步记录实际输出和版本，不复制密钥、设备 ID 或用户原始语料。

## A. 软件包与安装（无需业务授权）

- [ ] 有完整 `api-sse-probe/` 包、`SKILL.md`、`TEAM_MANIFEST.txt`、`scripts/`、`references/`、`config.example.json`、`tests/`。
- [ ] 验证发布 ZIP 的版本/校验和；检查不含 `config.local.json`、`config.*.local.json`、`reports/`、`.env` 或其他私有文件。
- [ ] 目标 Agent Skills 根目录明确，安装过程不覆盖其他人的已有配置。安装完目标为 `<root>/api-sse-probe`。
- [ ] 使用 Python≥3.9（macOS/Linux `python3`，Windows `py -3`）。
- [ ] `python3 -m unittest discover -s tests -q` 全部通过；如因 Windows POSIX 0600 位模式测试受限，明确记录平台差异并核查 ACL；不要笼统写全通过。
- [ ] 新安装 `config.local.json` 来自公开模板，`feishu.enabled=true` 但目标群凭据尚未配置；权限已限制，真实拨测前须填写获授权目标群。

## B. 用例检查（不执行SSE）

- [ ] 默认 CSV：`python3 scripts/probe.py --check` 通过，`case_ids`、`source_rows`、`skipped_rows` 正确。
- [ ] 腾讯模式（如使用）：当前成员有 Token 和表格读取权限；`python3 scripts/check_tencent_mcp.py` 工具检查通过，`--check` 真实读取对应 sheet。
- [ ] 记录具体 `case_ids` 并与用户授权的测试集合对照。**范围 A2:H11 不是保证等于ID1–10；以实际输出为准**。
- [ ] `skipped_missing_device_id`、`skipped_disabled` 每条均确认合理，未把正式用例误当示例跳过。
- [ ] 对同上/公共/特殊判断项进行抽样核对，确保没有从被跳过行继承断言。

## C. 真实单条测试（必须用户明确授权）

- [ ] 确认 `api.url`、powerkey/signature 来源、目标业务测试环境以及授权测试ID；默认通知的目标群 Webhook/签名已配置，否则业务 POST 前报错。
- [ ] 以 `python3 scripts/probe.py --case-id <已检查ID>` 执行。
- [ ] 验证实际 HTTP 状态、Content-Type、至少一帧合法业务 JSON、必要 [DONE]、预期关键词/禁止词。
- [ ] 退出码、PASS/FAIL/ERROR 和失败具体条件一致；不能把网络 ERROR 判为业务断言 FAIL。
- [ ] 本地 JSON v5 / Markdown 均已生成，含 `summary.total`、`results`、`failure_cases`（若失败）、`slow_token_cases`，不存在设备ID或鉴权头。
- [ ] 首帧和完整请求耗时分开；`first_token_sample_count` 正确，均值使用有合法业务帧的数据。
- [ ] 无权限的协作者不会直接接触本地原始响应片段。

## D. 飞书群完整报告（正式拨测默认发送）

- [ ] 确认目标是获授权的群，Webhook签名、关键词和飞书群可见范围已获同意；仅用户明确要求测试不通知时加 `--no-feishu`。
- [ ] 开启 `feishu.notification_style=v2`；一张汇总卡显示**总数/通过/失败/异常/通过率五指标**，含可点击的真实腾讯来源链接（源文件读权限另行管理）。
- [ ] FAIL + ERROR 为 N 时，**优先一张卡**；容量超限先完整预检、再按页 1/N、2/N 发送。所有页的失败/异常 case_id 合起来必须恰好出现一次，不能漏发或重复。
- [ ] 每条 FAIL/ERROR 必须显示**完整case_id、状态、场景与精简原因**；容量高时可压缩部分耗时和较长诊断文本，但不能遗漏用例或截断case_id；**不能用“其余见本地报告”替代用例清单**。
- [ ] 使用 42 条模拟失败场景分别验证单卡可容纳与必须分页的两种情况：每个编号全量恰好出现一次；模拟发送只允许获授权的测试群，不可替代真实业务群回执验收。
- [ ] 服务器业务 code=0 后仍要在飞书客户端视觉检查单卡内容、完整编号和文档链接。
- [ ] 回执不明确时绝不自动重试；所有页必须预先通过容量校验，无法生成合法分页时发送前拒绝；不得盲目 `share_report.py --send` 重发造成重复。

## E. 批次与长期运行（当前未全部实现）

- [ ] 用户批准本轮实际请求量、结束条件和回滚方案。
- [ ] 预估总时长，确认外部执行工具/网络连接限制；当前串行、无逐条检查点，超时会失去本轮未落盘数据。
- [ ] 如果工具要求在线/电脑开机，明确说明，不得承诺关机后云端自动继续。
- [ ] 想满足云端常驻、断点恢复、团队工作台，需要先实施 roadmap 中的调度与状态存储，不能把当前功能误判已实现。

## 交付汇报模板（严禁伪造）

```text
技能版本/来源Tag：...
安装系统和路径：...
离线用例测试：.../...
真实目标（隐去Token）：...
用例ID和数量：...
技术链路：已验证/未验证（HTTP/SSE/Done）
业务结果：PASS=... FAIL=... ERROR=...
有效首帧：.../...；均值...s；墙钟...s
JSON/MD报告：...
飞书：disabled / sent / failed / partial；预检分页数/已尝试数/已确认数/失败页/回执是否不确定...
权限和隐私检查：...
已知缺陷和未完成门禁：...
是否允许全量/上线：是/否（依据）
```

## v1.9.0：原始失败流的交付验收

- [ ] 用模拟 SSE 流完成 FAIL 测试：解码 `raw_sse_base64` 后逐字节与输入 `data:` / `event:` / 空行 / `[DONE]` 相同，SHA-256一致。
- [ ] PASS用例的原始SSE不会留存；ERROR请求没拿到流时标为 `not_received`；中断流为 `partial_or_unterminated`。
- [ ] 同一目标第二轮原子替换前次 `latest-failed-sse/<hash>.json`；全部PASS覆盖为零项；不同目标有不同文件，互不覆盖。
- [ ] 模拟原子写入前异常或超限，已完成的上一次诊断文件保持原样。
- [ ] 文件权限与发布包隐私检查通过：原始 SSE 仅进入受限本地证据；v1.11.0 的可读完整响应文本只进入本地 JSON/Markdown，不进入飞书群或团队 ZIP。
- [ ] 对自动留存范围说明准确：本轮仅能保存收到的响应字节；历史常规统计报告不在自动覆盖范围。

## v1.9.0 OutputWriter通用性验收

- [ ] 默认或旧版无 `report_outputs` 配置仍生成 JSON v6 / Markdown，且群通知与历史补发不被破坏；旧 JSON v5 可只读解析。
- [ ] `build_report_document` 与 `render_markdown` 不执行任何网络请求或写盘；`LocalFileOutput` 独立输出并使用正确的私有文件权限。
- [ ] 未注册 `feishu_bitable` / `feishu_doc`、重复Sink、空Sink等配置在**业务POST之前**报错；不能对外声称已支持真实飞书多维表格/云文档写入。
- [ ] 测试中的假云适配器仅收到 `public_projection`；不得出现用户语料、助手摘录、设备ID、reqId和完整SSE。
- [ ] 当前飞书群 Card v2单卡汇总及完整失败编号不受本次ReportWriter拆分影响；完整语料/响应不进入飞书/public_projection；原始失败SSE最新文件单独保存，不由云Writer读取。
- [ ] 实际云端Writer将来需要单独的应用权限、字段映射、创建/覆盖/回滚、幂等和真实写入回执验收。

## v1.11.0 失败诊断与版本一致性验收

- [ ] 每条 FAIL/ERROR 的 Markdown 详情严格包含场景、完整语料、失败判断原因、完整实际接收响应四项；不再重复输出失败表格、设备ID、reqId、请求/响应头清单、阶段统计或短摘录。
- [ ] 使用超过 12,000 字符的多帧 SSE fixture，确认首帧/中间帧/DONE 后尾部字节全部出现在本地报告；原始 Base64 快照 SHA-256 与实际接收字节一致。
- [ ] 无响应头、部分流、HTTP 错误体、非 SSE 响应体分别标注实际状态；未收到的字节不得伪造为完整响应。
- [ ] 本地 JSON v6 和 Markdown 包含完整失败语料/响应；`public_projection`、飞书卡片、团队 ZIP 均不包含这些私有字段。
- [ ] SKILL 元数据、README、CHANGELOG、JSONC配置参考和发布 ZIP 的版本一致；每次迭代版本号递增；发布清单包含新增运行模块及回归测试。

## v1.10.0 历史验收项

- 旧版单卡方案曾要求超容量拒发；该策略已在 v1.12.0 替换为自动分页。

## v1.12.0 及后续版本适用验收项

- [ ] 日常拨测不自动触发 unittest、--check 或其他离线自检；仅用户明确要求时调用。业务请求仍要求有效配置、权限、用例范围及输出策略校验。
- [ ] 单卡容纳全部失败时只发一张，保留五指标、腾讯文档链接及全部失败用例 ID。
- [ ] 超出单卡容量时先预渲染全部分页，每页不超过 18,000 字节卡片预算、整体 POST 不超过 20,000 字节，失败 ID 全部出现恰好一次且页序 1/N、2/N 连续。
- [ ] 任一分页不合法或超容量时零发送；第 N 页拒绝或回执不确定时停止后续页、不能自动重发，准确记录总页数、已确认数、已尝试数和失败页状态。
- [ ] 历史 JSON v5/v6 的只读预检准确返回 group_cards，--send 必须显式授权且不重发业务 POST。
- [ ] 私有设备、Token、Webhook、语料和完整 SSE 不进入卡片；发布时 SKILL/README/CHANGELOG/ZIP 的版本号同步到本次目标版本，变更只能在隔离分支完成验收后合并。

## v1.16.0 治理验收补充

- [ ] 检查 [工作地图与验收等级](architecture.md)、[反馈闭环](architecture.md) 和 [治理验收矩阵](architecture.md)。
- [ ] 新增或修改断言必须有脱敏正反样例、自动回归、commit 与回滚证据；无法证明业务语义时保留人工复核。
- [ ] main v1.16.0 没有全量 PASS SSE 持久化、断点恢复和离线 rejudge；这些是未来 v2 工作，不能提前宣告通过。


## 2.2.0-dev 新增待验收项目

- Feishu Sheets 九列映射读取：离线可回放输入；真实端需应用自身文件权限、工作表读取及 2 行回读与本地用例一致，未完成不得声称生产可用。
- 本地私有 `feishu_docs_auth` 不得包含在运行快照、发布 ZIP、飞书报告或日志。
- `--check` 飞书源只读用例且不会业务 POST 或发通知。
- 断言合同：不确定/格式错误的判断依据应在业务请求前失败；所有兼容业务用例继续得到相同评判。


## v2.2.0 real cloud acceptance closure

Feishu app authentication, folder creation, spreadsheet creation, nine-column data write/readback and read-only public CLI --check have been verified using a real test sheet. Empty padded rows no longer inflate skipped counts. No business POST or group messages were sent in this verification. See `../docs/governance/feishu-live-acceptance-2026-11-07.md`. Personal Feishu client access is still an explicit not-verified item; this does not invalidate app API case-source acceptance.