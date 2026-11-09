# 常见问题

| 现象 | 先检查什么 |
| --- | --- |
| 无法启动 | Python 3.9+、完整 Skill 目录、`scripts/probe.py` 和必需模块是否齐全 |
| 缺少配置 | 在已安装目录运行 `python3 scripts/setup.py init --source tencent`；已有配置不得覆盖 |
| 正式拨测未发请求 | 先运行 `python3 scripts/setup.py doctor`；检查 API HTTPS、认证头、输出适配器及选择启用的飞书通知 |
| 在线表格读不到用例 | 是否有读取权限、有效 `tab=`、匹配的列名、非空语料及 `deviceId` |
| 断言被拒绝 | 写明确的字段、引号关键词、AND/OR 条件；不要写“意图合理”等语义要求 |
| HTTP 200 却 FAIL | HTTP 状态通过不代表 SSE 完整或业务断言满足；查看具体缺失规则 |
| 缺少 `[DONE]` | 检查 SSE 是否中断、返回大小上限及服务器流结束协议 |
| `UNKNOWN` / `CONTINUE_REQUIRED` | 检查运行快照和 `resume_command`；不无条件重新发出未知请求 |
| 飞书未发送 | 群机器人权限、安全关键词、Webhook、签名及分页回执；不盲目重发 |
| 报告或日志含敏感数据 | 只在私有目录查看，禁止放入 Git、群消息、公开链接或安装包 |
| Windows 运行失败 | 当前持久化 v2 的原生 Windows 兼容性尚未验证，不应宣称支持 |

## 排查顺序

本机 `setup.py doctor` → 授权后 `probe.py --check` → 用例源及列映射 → HTTP/TLS → SSE 协议完整性 → 业务断言 → 私有报告 → 飞书通知。

任何环节失败都应保留真实错误信息，不用模型臆测成功结果。具体超时字段见 [请求超时与诊断](timeout-and-diagnostics.md)，条件语法见 [判断依据](assertions.md)。