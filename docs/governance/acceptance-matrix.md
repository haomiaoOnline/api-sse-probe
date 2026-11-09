# v2.4.3 当前治理验收矩阵

> 以 [当前权威工作地图](current-v2.4.3.md) 为准；旧矩阵为 v1.16 基线。四类验收：自动验证通过、AI 评估通过、人工确认通过、尚未验证；不与业务 PASS/FAIL/ERROR/UNKNOWN 混用。

| ID | 检查对象 | 自动检查/证据 | 尚需人工确认或未验证 |
| --- | --- | --- | --- |
| GOV-001 | main/回滚 | baseline/pre-governance-v2.4.3 = 44f55af、独立改造分支 | 正式合并/发布 |
| GOV-002 | 正式源码入口和版本 | check_skill_contract.py、test_current_skill_contract.py、Manifest 白名单 | 现有 Agent 真实加载路径尚未逐端验收 |
| GOV-003 | 工具入口及历史文档 | probe.py 为默认入口、probe_v2.py 兼容；历史文档显式非当前说明 | 不依赖旧 v1.16“未实现”描述 |
| GOV-004 | 用例冻结/SSE/复判 | case_snapshot、v2_rejudge、原始哈希及正反 fixtures | 业务语义和真实网络仍有人工判断 |
| GOV-005 | UNKNOWN 限次恢复 | test_unknown_auto_retry.py、v2 状态账本 | 真实断电恢复尚未验证 |
| GOV-006 | 完整/未完成飞书归档 | test_feishu_archive_v24.py、test_partial_feishu_archive_v243.py | 飞书客户端视觉须人工核验 |
| GOV-007 | 反馈闭环 | feedback-loop.md、正反 fixture、完整回归与回滚记录 | 新语义规则须人工确认 |
| GOV-008 | 包安全性 | test_team_package.py、Manifest 私密文件白名单 | 正式交付物未经批准不覆盖 |
| GOV-009 | 全量离线回归 | python3 -m unittest discover -s tests -q | 不代表线上 E2E 通过 |
| GOV-010 | Windows、真实断电、授权业务 E2E | 本轮无现场测试 | 尚未验证 |

内容更新后重跑相关验证；缺证据即标记“尚未验证”。