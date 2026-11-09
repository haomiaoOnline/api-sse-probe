# Security

本仓库只允许公开代码、模拟数据、明确占位符和通用说明。**禁止提交**个人 Token、API URL、`powerkey`、`signature`、Webhooks、飞书 App Secret、设备 ID、私有在线文档链接、账号身份、真实运行数据、`config.local.json`、`.env`、`reports/`、SQLite 或原始 SSE。

首次授权在本机终端使用 `scripts/setup.py init` 隐藏输入，写入权限受限的私人配置；不要求用户在聊天中提交密钥。安装器和发布清单均不打包私有配置。

对外发布前应扫描**待提交文件和提交历史**，并验证 `TEAM_MANIFEST.txt`、发布 ZIP、公开测试样本均不含真实凭据或内部业务链接。已泄漏的密钥必须撤销/轮换；仅从 Git 删除文件并不能抹去公开历史。

真实业务拨测、飞书群通知和云端归档是独立授权；默认关闭通知。不得关闭 TLS 验证、绕过显式断言或为了返回 PASS 丢弃失败条件。