# 用户反馈 → 可复现样例 → 回归验收

反馈登记：区分一次性要求或长期规则；记录反馈 ID、版本/commit、脱敏问题、实际/预期、长期规则或单次要求、根因、修复代码、正反 fixture、四级验收状态和回滚点。敏感原始 SSE 只能存受控私有 evidence；不写入公共样例。

| ID | 历史问题 | 已有防线与检查 |
| --- | --- | --- |
| FB-001 | 缺失关键词及完整响应难定位 | 失败证据及 test_failure_diagnostics.py |
| FB-002 | 飞书卡片过长或失败编号遗漏 | 分页断言和 test_feishu_notification.py |
| FB-003 | 新腾讯文档被旧配置阻断、反复问 case ID | source-url 和 test_source_override.py |
| FB-004 | title/text/skillId 混合误判 | 确定性复合断言、test_v23_golden_accuracy.py |
| FB-005 | 断言过宽与过严反复 | 明确语法、test_deterministic_rule_contract.py |
| FB-006 | 长批次无法可靠续跑 | SQLite 逐条持久化，test_v2_reliability.py；断电仍未验证 |
| FB-007 | 外层超时留下 UNKNOWN 阻断（长期、已批准） | **仅新运行**限次自动重试一次、旧请求留证，test_unknown_auto_retry.py |
| FB-008 | 未完成卡片缺飞书文档链接、关键词被机器人拒绝（长期） | 部分归档及通知回执，test_partial_feishu_archive_v243.py |
| FB-009 | 旧治理文档与嵌套副本导致 Agent 加载冲突（长期） | 根目录唯一入口、历史警示、test_current_skill_contract.py |

闭环：①确定是单次/长期/主观要求；②脱敏复现并编写应通过和应失败样例；③优先修工具或自动检查而不是堆叠 Prompt；④定向及完整离线自动回归；⑤记录 Git/回滚；⑥业务语义、真实飞书视觉等需要人工确认，不以自动验收冒充人工通过。

以上是反馈映射，不宣称本轮重新进行了真实 API / 飞书线上验收。