# 配置说明

个人运行配置是 Skill 根目录下的 `config.local.json`（标准 JSON），**永远不要提交到 Git**。**首次安装使用 `python3 scripts/setup.py init --source tencent` 等安全向导创建**；已有配置不得覆盖。只有无交互终端时才考虑用户本机手工复制 `config.example.json` 后安全编辑。需要逐参数说明时查看 [config.annotated.jsonc](../config.annotated.jsonc)；这是带注释的阅读模板，不是可直接运行的 JSON。

## 主要配置区

| 区域 | 用途 |
| --- | --- |
| `api.url` | 已授权的 HTTPS SSE API 地址 |
| `api.headers.powerkey` / `signature` | 鉴权值；可引用环境变量 |
| `api.body_defaults` | 默认请求参数，如 endpoint、session |
| `source` | 选择 csv/json/tencent/feishu 和具体来源 |
| `tencent_docs_auth` | 腾讯文档 MCP 读取凭据（只读） |
| `feishu_docs_auth` | 飞书表格/云文档应用授权 |
| `assertions.require_keywords` | 是否要求业务断言；建议保持 true |
| `reports` | 私有报告目录和响应保留上限 |
| `feishu` | 已授权群机器人的通知和安全关键词 |
| `feishu_archive` | 可选云文档归档；需单独配置授权 |
| `report_outputs` | 私有文件输出适配器，默认 file |
| `unknown_retry` | 新运行是否允许对 UNKNOWN 最多自动重试一次 |

## 安全的配置方式

```json
{
  "api": {
    "url": "https://example.com/api/sse",
    "headers": {
      "powerkey": {"env": "MY_API_POWERKEY"},
      "signature": {"env": "MY_API_SIGNATURE"}
    }
  },
  "source": {"type": "csv", "path": "assets/sample_cases.csv"},
  "assertions": {"require_keywords": true},
  "reports": {"dir": "reports"},
  "feishu": {"enabled": false, "keyword": "API拨测", "notification_style": "v2"},
  "report_outputs": [{"type": "file", "enabled": true}],
  "unknown_retry": {"enabled": false, "max_retries_per_case": 1}
}
```

以上仅演示配置**结构**，没有真实接口/凭据；首次安装默认关闭群通知；只有用户明确选择启用并提供安全 Webhook/签名后才发送。

## 使用说明

- 安装与获取 Token 的完整步骤见 [Agent 安装指南](installation.md)，包括腾讯文档官方 MCP 授权页和飞书应用权限获取方法。
- `python3 scripts/setup.py doctor` 不联网、不输出密钥；`READY_OFFLINE` 只是本地配置状态，并非 API/Token 真实可用证明。

- 本轮提供 `--source-url '<已授权完整链接>'` 时，临时覆盖原来源的文档身份，不改写 `config.local.json`；仍遵循已配置的读取区间和列映射。
- `--check` 只检查用例、映射和断言，不发送业务 POST 或通知。
- `--case-id` 只选择指定的真实用例；`--no-feishu` 禁用当前运行的群通知和云归档。
- 支持的腾讯文档、飞书表格只读输入需要各自权限；有连接不代表对指定文件有权限。
- `signature` 不会在请求前自动计算；若接口需要动态签名算法，必须提供独立适配方案。
- `unknown_retry.enabled=true` 仅影响**新运行**，未知请求重试可能重复执行业务动作。
- 本地默认用例、响应及原始证据均不要加入公开 Git 仓库。