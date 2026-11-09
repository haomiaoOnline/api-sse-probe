# v2.4.3 当前权威工作地图

**使用范围**：以仓库根目录 `SKILL.md`（version 2.4.3）为唯一正式源码入口，配套 `TEAM_MANIFEST.txt` 与 `scripts/install.py`。当前代码状态以 `main@44f55af` 为改造前基线。**嵌套的 `api-sse-probe/` 目录是未同步的历史副本**，本轮保留且不自动加载、覆盖、清理；安装包目录前缀 `api-sse-probe/` 与源码路径并非一回事。若 Agent 实际加载嵌套副本，必须先核实引用，而非静默切换。

本文件是当前版本唯一状态索引。历史 `docs/governance/README.md`、`unified-entry-migration.md`、`references/architecture.md` 中 v1.16 或开发阶段的“未实现”“禁止 UNKNOWN 重试”等文字**不是当前执行指令**。现行行为以源代码、测试及 v2.4.2/v2.4.3 发布文档为准。

## Feature Map（输入、输出与分支）

| 阶段 | 输入与工具 | 产出及门禁 |
| --- | --- | --- |
| 1. 授权与输入 | `scripts/probe.py` → `lib/unified_workflow.py`；腾讯/飞书 Sheet 或 CSV/JSON；已配置的私有凭据 | `--source-url` 仅覆盖本轮来源；`--check` 只读，不发业务 POST/群消息；缺授权在 POST 前停止 |
| 2. 用例冻结 | `read_cases`、`case_snapshot.py` | 唯一 ID、合法断言及不可变 run 来源 |
| 3. SSE 证据 | `lib/v2_engine.py`、`lib/v2_repository.py`、`probe.run_case` | 串行请求；`PENDING → DISPATCH_INTENT → CAPTURED`；完整已接收原始 SSE、SHA-256、SQLite 逐条持久化 |
| 4. 异常恢复 | `RunStore.plan`、`queue_unknown_retry` | 已捕获不重发；**新运行**显式启用 `unknown_retry.enabled=true` 可自动重试 UNKNOWN **最多一次**，旧运行不自动补测、第二次 UNKNOWN 人工对账；可能重复执行同一业务动作 |
| 5. 业务断言 | `lib/v2_rejudge.py` 的 `judge_case` | PASS / FAIL / ERROR 依据冻结规则与证据离线复判；UNKNOWN 不能伪造响应 |
| 6. 报告与飞书 | `v2_engine.report/partial_report`、`feishu_archive.publish`、`notify/notify_partial` | 私有 JSON/Markdown；完整与未完成 Docx 分离，失败在前、成功在后、SSE 原生代码块；确认只读链接及回读后发送保留安全关键词的群卡片 |
| 7. 开发验证 | unittest、Manifest 白名单、`scripts/check_skill_contract.py` | 改动后自动回归、证据与回滚指针；正式发布/合并需人工确认 |

运行关键分支：`--max-cases/--max-seconds` 限制返回 `CONTINUE_REQUIRED` 时仅续跑剩余冻结任务；`--no-feishu` 禁用该轮归档和通知；UNKNOWN 且 PENDING 清零时发**未完成**报告及链接，不得冒充 100% 完成。日常公开 CLI 为 `scripts/probe.py`；`scripts/probe_v2.py` 仅保留历史阶段化命令兼容。

## 四类交付验收（不同于业务 PASS/FAIL/ERROR/UNKNOWN）

- **自动验证通过**：含明确检查对象、命令、预期与实际证据；离线测试不等于现场业务通过。
- **AI 评估通过**：判断与证据引用充分，但没有人工签收。
- **人工确认通过**：有明确的确认范围与依据；飞书视觉效果和重要规则应在此类。
- **尚未验证**：没有对应证据时必须标记此状态。

**已经有实现或历史验收记录**：逐条持久化、确定性断言、一次 UNKNOWN 重试、部分/完整飞书云归档及含只读链接的通知。对应证据见 `tests/test_unknown_auto_retry.py`、`tests/test_partial_feishu_archive_v243.py` 和 v2.4.0～v2.4.3 的验收记录。

**尚未验证或不得泛化**：Windows 实机适配、真实断电恢复、所有真实生产网络条件和业务语义的准确性、所有飞书客户端视觉。历史 165 条运行中的 #137 UNKNOWN 不会自动补成 PASS。

## 人工确认边界、反馈与回滚

Agent 可在**新分支**自主修改文档、非关键工具与离线测试；正式合并、发布、覆盖重要原件、重要业务断言/安全规则更改、未获授权的真实业务请求及飞书目标变更均需用户确认。反馈先分单次/长期，优先固化正反 fixture、工具检查或程序修复，必要时再更新说明，参见 `feedback-loop.md`。

改造前回滚指针：`baseline/pre-governance-v2.4.3` = `44f55af`；返回稳定版可使用 `git switch main`，勿覆盖私有 `config.local.json` 或 `reports/`。