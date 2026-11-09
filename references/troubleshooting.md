# 故障排查与已知限制

按**症状 → 安全验证 → 判断 → 下一步**处理；未经授权不得为排障执行额外真实 POST、关闭 TLS 验证或公开认证信息。

## 安装和环境

| 症状 | 可能原因和处理 |
| --- | --- |
| Agent 有 SKILL.md 却找不到 probe.py | 只有说明文件，没有完整包。索要团队 ZIP，不能由 Agent 虚构实现 |
| 技能已复制却不可发现 | 核对真实 Agent 的 Skills 根目录与是否重新加载索引 |
| 安装目录已存在 | 为保护私有配置安装器拒绝覆盖；先备份/核对版本，再升级 |
| `ModuleNotFoundError: lib` | 从错误目录/脚本单独复制；在完整 Skill 根目录执行 `python3 scripts/probe.py --check` |
| `python3` 不存在 | Windows 可用 `py -3` 或 `python`，确认Python≥3.9 |
| Windows chmod(0600) 断言失败 | Windows 文件权限靠 NTFS ACL，不能以 POSIX 模式位作为依据 |
| Git与安装版行为不同 | 比对版本/文件哈希；v1.9.0 已纳入实际安装版的 SSE 分块读取修复 |

## 数据源与用例

| 症状 | 核查与解决 |
| --- | --- |
| `tencent_docs_token_missing_or_invalid` | 用户本人需有有效腾讯 Token；保存在私有配置/环境变量 |
| MCP连接失败/工具不存在 | `check_tencent_mcp.py` 只检查工具，不能证明目标文件有读权限；然后执行 `--check` 核对具体表 |
| 用例数不正确 | 核查 `sheet_id/sheet_name`、`range`、`header_row`、`columns`，逐项看 `case_ids` 和跳过统计 |
| 期望1–10却读到10–19 | Excel 物理行号不等于 case_id；先 `--check` 校验再执行。表头不在所读区间时设 `header_row=0` 并用列字母映射 |
| 缺deviceId的正式用例被跳过 | 腾讯模式自动跳过空设备行且计入 `skipped_missing_device_id`；源表作者必须确认 |
| 断言 `同上` 解析错误 | 上一有效业务行缺对应断言或中间分组/停用行断开继承链 |
| CSV/JSON数据缺deviceId | 本地源这是硬错误，不能随意硬编码设备号替代 |

## SSE 网络与业务结论

| 报告结果 | 实际含义 | 下一步 |
| --- | --- | --- |
| PASS | HTTP、SSE、完成标记、业务断言满足 | 保留对应报告作为证据 |
| FAIL / assertions_failed | 缺失具体判断项或命中禁止词 | 查 `failure_cases[].missing_keywords`、本地回复摘录 |
| ERROR / network_error | 网络层抛出错误，仅保存统一分类 | 分别查DNS/TCP/TLS/代理/源站；**当前不能根据此码断言具体根因** |
| ERROR / tls_error | SSL错误 | 查证书/SNI/源站/代理；**禁止关闭证书验证** |
| ERROR / timeout | 连接/读取超过限制 | 查看 `connect_timeout_seconds`、`idle_timeout_seconds`、`total_timeout_seconds`；调大不等于解决根因 |
| HTTP200但业务FAIL | SSE、[DONE] 或业务条件不符合预期 | 检查本地报告 reason 及响应摘录 |
| 首帧为 -、平均样本少 | 没有收到合法业务JSON帧 | 查 `first_token_sample_count`，不可按0s算 |
| 第一条PASS，后续连续网络ERROR | 可能是网络波动、代理、源站连接压力等 | 先停止全量重复，低频受控排查；不能仅凭一次TLS检查锁定原因 |

**已知长批次问题**：当前 `main()` 串行执行所有用例，整批结束才写最终报告。若受限于外层命令的60分钟超时可能被中断，已执行请求不自动撤销、结果也没有逐条检查点；不能保证自动恢复或后台常驻。

## 飞书与报告

| 现象 | 处理 |
| --- | --- |
| 首次配置没有 Webhook/签名 | 默认飞书通知在业务 POST 前阻断；填写获授权目标群凭据，用户明确要求仅测试不通知时才加 `--no-feishu` |
| 服务器回复 sent，但客户端排版异常 | sent 不等于客户端UI验收通过 |
| 卡片只有汇总、没有失败明细 | 检查v2渲染、实际FAIL/ERROR数量；所有分页在发送前必须通过大小校验，不能暗中截断 |
| 单卡容量不足 | v1.12.0+ 自动预检并按页发送所有失败编号；只有无法形成合法分页时才拒绝投递，绝不截断或遗漏 |
| 群中无法查看本地报告 | 本地报告不是公网链接；v2群内已经发送所有失败摘要，但完整响应需要受控共享平台 |
| 群成员打不开腾讯文档链接 | 机器人没有赋予文件权限的能力，须由文档管理员授权 |
| 补发重复消息 | `share_report.py --send` 会再向群发，先核对最新报告和已到页码 |
| `--check` 不发飞书 | **正常行为**，只读检查不是一轮正式拨测 |

## 退出码

- 0：全部PASS；1：有业务FAIL/ERROR（仍可能已保存报告和发送群卡）；2：配置/读取/写报告异常；3：业务全PASS但飞书失败。
- 即使返回1或2，也要分别核对报告是否生成和飞书通知状态；不能只凭进程码判断每个环节成败。
- 本地报告含敏感语料和响应片段，禁止将完整文件/日志贴进普通群。可只对有权限的工程师交付证据，并区分已验证与推断。

## v1.9.0：完整失败SSE文件排查

- 本轮完成后控制台给出 `failed_sse_report` 和 `failed_sse_cases`。只有业务FAIL/ERROR会进入 `failed_cases`。`raw_sse_base64` 是原始SSE的无损编码，可在授权的本机解码后核对 `sha256`；不要把解码内容打印到公开日志或飞书群。
- 没收到业务字节时是 `not_received`，超时导致数据不完整则是 `partial_or_unterminated`，不是代码遗漏了服务端完整回复。
- 同目标最近文件重复覆盖是预期行为；不同源表/API有不同目标摘要。全部PASS会把上轮失败清单更新为零条。
- `failed_sse_total_byte_limit_exceeded` 表示本轮失败流过大，上一份快照仍保留；评估API的 `max_response_bytes` 和 `reports.failed_sse_max_total_bytes`，不得通过关闭安全检查绕过。
- `.pending` 文件可能因进程被强杀而存在，同目标下一轮会覆盖它；**这不是断点续跑依据**。避免同目标同时启动两个批次。
- 普通统计 `probe-*.json/.md` 仍按时间增加；用户此次“覆盖”只针对原始失败SSE独立文件。

## 报告输出接口常见故障（v1.9.0）

- `report_output_not_supported`：配置了尚未开发/注册的 `feishu_doc`、`feishu_bitable` 等；不会调用业务API。当前请使用 `file`，不要声称已写入云端。
- `report_outputs_all_disabled` 或 `report_output_duplicate`：输出清单格式不合法，按 [output-adapters.md](output-adapters.md) 修复后执行 `--check`。
- 旧代码直接调用 `write_reports()` 仍兼容，实际工作由 `report_document.py` 和 `outputs/file.py` 负责。
- `report_outputs` 回执成功表示对应Writer完成持久化，不代表群卡片已成功投递；群消息请另看 `feishu_notification`。
- 飞书云文档/多维表格权限、字段映射与幂等写入仍属于后续开发，不能靠当前配置自动解决。

## 治理规则

当前超过单卡大小时应使用自动分页（v1.12.0+）；早期“超限拒发不分页”仅为旧版历史口径。参见 [工作地图](architecture.md) 与 [反馈闭环](architecture.md)。