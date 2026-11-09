> **历史基线提示：** 本文主要记录 v1.16.0 治理建立时的“已实现/未实现”状态，不能作为 v2.4.3 的当前执行规范。请优先读取 [v2.4.3 权威工作地图](current-v2.4.3.md)；原始 SSE/SQLite、UNKNOWN 限次重试、部分飞书文档及链接卡片均已在后续版本实施。以下旧正文为审计追溯而保留。

> 注意：本索引原基线为 v1.16.0，当前独立分支正在升级统一入口；新状态见 [统一入口迁移说明](unified-entry-migration.md)，下方“未实现”描述限旧基线。

# api-sse-probe v1.16.0｜工作地图与治理入口

基线：main / 2d9f0af；本文件是治理索引，不表示 v2 已交付。

| 阶段 | 输入 | 工具 | 验收条件与关键分支 |
| --- | --- | --- | --- |
| 包、配置、授权 | 完整包、私有 API/飞书设置 | install.py / OutputRegistry | 版本、白名单、授权齐全，否则在 POST 前停止 |
| 用例 | CSV/JSON/腾讯文档 tab、列映射 | probe.py / source_override.py / tencent_mcp.py | 唯一 ID、启用/跳过、同上继承；列不匹配或权限不足停止 |
| 采集 | HTTPS POST JSON、SSE | probe.py / phase_transport.py | HTTP、合法业务帧、DONE、耗时与错误阶段；部分数据不冒充完整 |
| 判定 | 公共类型和特殊判断依据 | assertion_rules / natural_assertions / expression_assertions | 正文/title/skillId/SDK 分别取真实业务字段；具体预期和实际可追溯 |
| 报告 | 逐条结果与失败原始流 | report_document / failed_sse / outputs | 私有 JSON/MD、失败流字节数+SHA256；中断无可靠逐条断点 |
| 通知 | 脱敏摘要、授权群 | feishu_notification_router / probe_group_report | 单卡优先、超过容量自动分页、失败 ID 恰好出现一次；未知回执不自动重试 |
| 反馈/发布 | 复现样例、问题记录 | tests / CHANGELOG / TEAM_MANIFEST | 正反用例、测试证据、commit、回滚指令 |

## 现有能力与未实现能力

已实现：串行拨测、结构化与自然语言条件、FAIL/ERROR 原始响应受限留存、报告、飞书分页、历史报告授权补发。

未实现：PASS 原始 SSE 全量留存、整批逐条可靠检查点与恢复、离线 rejudge、飞书投递持久账本。docs/refactor 的 v2 设计源自旧 v1.10.0，仅作实施参考；未来必须基于 v1.16.0 重新增量建设。

## 执行模式与验收级别

日常正式拨测遵循 SKILL.md：明确带 tab 的腾讯链接优先于旧配置，直接读取当前配置范围内全部启用有效用例；不强制每轮执行 unittest 或 --check，只有已获授权才调用真实 API 和飞书。

代码变更和发布时则必须做离线回归、治理契约检查、Git 差异审查；真实业务 E2E 需单独授权。

- **自动验证通过**：保留命令、fixture、预期/实际、commit；离线通过不等于生产通过。
- **AI 评估通过**：保留判断理由及证据引用；不冒充人工结论。
- **人工确认通过**：保留批准人、日期、范围和依据。
- **尚未验证**：无适用证据时明确记录。

业务 PASS/FAIL/ERROR 是拨测结果，不等于上述验收等级；HTTP200 或飞书 sent 不能代表业务和客户端 UI 通过。

## 安全与回滚

不得将 config.local.json、reports、密钥、设备ID、真实语料或完整 SSE 放入 Git/发布包。允许 Agent 在完整自动回归通过后修改正式判断规则，但必须保留新旧 commit、正反 fixture、测试结果与回滚指令；不能为提高通过率放宽明确必需的业务字段。

开发改动留在独立 Git 分支，不覆盖 main 或既有报告。完成审查后才考虑合并。详见 [反馈闭环](feedback-loop.md) 与 [验收矩阵](acceptance-matrix.md)。

## v2 逐步实施状态

M1 首切片：新增只读数据源后的私有不可变 CaseSnapshot（scripts/snapshot_cases.py）；不发业务 POST/飞书，不改变原 probe.py 的运行流程。注意逐条证据、rejudge、安全断点恢复仍未实现。

真实验收对照可运行 `python3 scripts/verify_snapshot_report.py --snapshot <私有快照> --report <私有报告>`；它只检查相同 ID 顺序、状态及 summary，不发请求，也**不能证明报告与快照之间具有密码学关联**。强关联需要后续集成 run_id/attempt_id。

M2—M5 的独立 opt-in 执行器说明见 [v2 执行器操作与验收](v2-m2-m5.md)。真实验收摘要参见 [2026-11-06 M2—M5 记录](v2-acceptance-2026-11-06.md)；用户已依据真实 iOS 截图确认飞书视觉验收，Windows 与真实断电恢复本版暂不处理。

正式版 v2.0.0 的全量 19 用例最终记录见 [2026-11-07 真实数据验收](v2-acceptance-2026-11-07-final.md)；保留用例 #15 真实业务 FAIL，按准确判定与留证验收。

长批次 165 条测试的执行器外层超时与安全分批恢复，见 [长批次架构复盘](v2-long-batch-recovery.md)；相关优化在独立修复分支，未替换稳定正式版本。