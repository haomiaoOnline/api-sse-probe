# 团队安装、授权与安全分发

目标：同事把完整发布包交给其 Agent 后，Agent 可以**在授权范围内**完成环境检测、复制模板、只读用例检查、单条真实验收和飞书可见性确认。本文**不能替代各人获得接口/腾讯文档/飞书群的授权**。

## 1. 交付物检查（拿到文件后第一步）

必须提供完整 `api-sse-probe/` 目录或经审核 ZIP，至少包含：

- `SKILL.md`、`README.md`、`TEAM_MANIFEST.txt`、`config.example.json`
- `scripts/probe.py`、`scripts/lib/*.py`、`scripts/share_report.py`、`scripts/install.py`
- `references/*.md`、`assets/sample_cases.csv`、`tests/test_*.py`

**只拿到 SKILL.md，不能运行**；Agent 应请求完整 ZIP 或受信任的 Git 仓库。发布包**不得**含：`config.local.json`、`config.*.local.json`、`.env`、`reports/`、Token、私钥、Webhooks、私有业务输入、缓存或 Git 本机配置。`TEAM_MANIFEST.txt` 指定可信安装文件；不要对正在运行的个人 Skill 目录直接打包。

### 维护者通过审核清单制作 ZIP

```bash
python3 -m unittest discover -s tests -q
python3 scripts/build_release.py
# artifacts/releases/ 下会生成团队 ZIP 和SHA-256校验文件
```

安装脚本和发布脚本共用 `TEAM_MANIFEST.txt`，不会打包个人Token、report目录、Git历史或运行器的其他文件。不得压缩某位成员正在使用的整个私人 Skill 目录。

## 2. 环境要求

| 项目 | 必需 / 可选 | 说明 |
| --- | --- | --- |
| Python 3.9+ | 必需 | 仅使用标准库，无 pip 依赖；能执行命令行和读写自身安装目录 |
| 目标业务 API HTTPS | 实际拨测必需 | 可解析/建立TLS连接，证书有效；必须取得接口授权 |
| 腾讯文档 MCP HTTPS | `source=tencent` 必需 | 有效 Token、目标电子表格阅读权限；官方只读工具 |
| CSV/JSON | 离线测试可用 | 模板数据，不需要 Tencent Token |
| 飞书群自定义机器人 | 可选，团队共享才需 | Webhook、签名密钥、关键词/IP白名单、正确的群成员可见性 |
| 文件权限 | 必需 | macOS/Linux 0600 私有配置；Windows 通过 NTFS ACL 控制实际访问 |
| Agent 加载支持 | 可选（用于 Skill 自动发现） | 每个 Agent 的 Skills 根目录、刷新命令不同；本 Skill 不假设统一路径 |

## 3. 安装（一次成功、失败时安全退出）

### 直接解压/运行

```bash
cd api-sse-probe
python3 --version
# 只有用户要求离线自检时才运行：python3 -m unittest discover -s tests -q
# 仅当私有配置不存在时：
cp config.example.json config.local.json
chmod 600 config.local.json
# python3 scripts/probe.py --check   # 仅用户明确要求时执行
```

默认示例 `source.type=csv`；`--check` 是可选离线自检，不触发业务 API 或飞书，但只有用户明确要求时才运行。日常拨测不执行自动自检；首次正式运行仍必须确认业务 API、真实用例范围和群目标授权。

### 装入特定 Agent Skills 根目录

```bash
# 要求 /path/to/skills 是已存在的文件夹
python3 scripts/install.py --target /path/to/skills
# 安装到 /path/to/skills/api-sse-probe
```

安装脚本按 `TEAM_MANIFEST.txt` **逐项复制审核文件**，并从公开模板生成全新 `config.local.json`，拒绝覆盖已有目标。若安装目标已存在，先备份并比较版本；不要暴力复制整个运行目录，更不能覆盖旧 Token、Webhook 与报告。安装后需按具体 Agent 产品的方式刷新/重新扫描 Skills。

### Windows PowerShell

```powershell
cd .\api-sse-probe
py -3 --version
# py -3 -m unittest discover -s tests -q  # 用户要求时执行
if (!(Test-Path .\config.local.json)) {
    Copy-Item .\config.example.json .\config.local.json
}
# py -3 scripts\probe.py --check  # 用户要求时执行
```

NTFS ACL 应限制到授权用户/服务账户；不要把 `os.chmod(..., 0o600)` 的 POSIX 位测试当作 Windows 隔离证明。若仅因 Windows 文件模式测试异常失败，需核验实际 ACL，并将其列为平台验证差异，不要默认所有测试通过。

## 4. 配置步骤（按需填写，不收集无关凭据）

复制出来的配置默认为 `feishu.enabled=true`，但不含 Webhook/签名；`--check` 仍可离线验证，正式拨测前必须配置获授权群的目标。正式业务最少需：

| 配置字段 | 来源/用途 | 是否敏感 |
| --- | --- | --- |
| `api.url` | 经授权的 HTTPS 接口 | 可能涉内部拓扑 |
| `api.headers.powerkey` | 业务认证头 | **是** |
| `api.headers.signature` | 当前接口认证头的原始字符串，不自动签名 | **是** |
| `api.connect_timeout_seconds` / `idle_timeout_seconds` / `total_timeout_seconds` | 单次请求等待/空闲/总时长 | 否 |
| `source.type` | `csv` / `json` / `tencent` | 否 |
| `source.path` | CSV/JSON 的本地用例路径 | 可能涉内部路径 |
| `source.tencent.doc_url` / `sheet_id` / `sheet_name` / `range` / `header_row` / `columns` | 腾讯文档与字段映射 | 文档ID、链接可能涉内网业务 |
| `tencent_docs_auth.token` 或 `TENCENT_DOCS_TOKEN` | 访问腾讯文档官方 MCP | **是** |
| `feishu.enabled` | 是否允许群消息 | 否 |
| `feishu.WEBHOOK_URL` / `SECRET` | 群机器人地址/签名 | **是** |
| `feishu.keyword` | 群机器人关键词安全策略 | 否 |
| `feishu.notification_style` | `v2` 完整群报告、`legacy` 旧版 | 否 |
| `reports.dir` / `slow_token_threshold_ms` | 本地报告目录 / 慢首帧阈值 | 可能涉目录 |

如果成员不使用腾讯文档，就无需提供 Tencent Token。默认飞书通知要求在真实拨测前配置获授权目标群的 Webhook/签名；缺失时在业务 POST 前停止。用户明确要求仅测试不发通知时，本轮可用 `--no-feishu`。

**推荐在私有配置中通过环境变量解析业务认证头**，无需把值直接写入 JSON：

```json
"api": {
  "headers": {
    "powerkey": {"env": "MY_API_POWERKEY"},
    "signature": {"env": "MY_API_SIGNATURE"}
  }
}
```

`TENCENT_DOCS_TOKEN` 可作为 `tencent_docs_auth.token` 的备选来源。飞书机器人配置当前使用私有文件的 `WEBHOOK_URL`/`SECRET`，请把其文件权限管好。不要给文档添加真实Token示例。

## 5. 腾讯文档多成员配置

当前仅支持腾讯**电子表格**只读 MCP，不等同腾讯多维表格。最常见 A-H 列为：序号、场景、类别、语料、公共判断、特殊判断、负责人、deviceId。建议完整范围含表头：

```json
"tencent": {
  "doc_url": "https://docs.qq.com/sheet/EXAMPLE_FILE_ID?tab=EXAMPLE_TAB",
  "sheet_name": "",
  "sheet_id": "exampleTab",
  "range": "A1:H500",
  "header_row": 1,
  "columns": {
    "case_id": "序号", "scene": "场景", "category": "类别",
    "content": "语料", "expected_keywords": "判断依据（公共）",
    "special_keywords": "特殊判断依据", "owner": "研发负责人",
    "deviceId": "deviceId"
  }
}
```

当仅抽取不含表头的一个区间（例如 `A2:H11`）时，可设 `header_row=0` 并将 `columns` 映射为绝对 Excel 列字母 `A/B/C/D/E/F/G/H`。**抽取范围后应先经用户确认真实 case_ids；`--check` 仅在用户要求离线自检时执行，不要凭 A1 行号推测业务编号**。无 `deviceId` 的行将跳过，表格作者须确认不是漏填的正式用例。

两个团队成员都要有目标文档的阅读权限。即便群汇总卡提供可点击腾讯文档链接，也不会通过机器人自动授予文档权限。

## 6. 分层验收（只做有权限的下一步）

1. **包完整性**：文件清单齐全；离线 `unittest` 全通过。
2. **CSV/JSON结构**：仅在用户要求时运行 `python3 scripts/probe.py --check`（不发业务 POST）；日常执行不以此为前置。
3. **腾讯 MCP**：配置腾讯 Token 和范围，明确获授权的真实用例 ID；只在用户明确请求诊断时运行 `check_tencent_mcp.py` 或 `--check`。
4. **授权的单条真实请求**：`python3 scripts/probe.py --case-id <已存在ID>`；检查 HTTP、SSE、业务 PASS/FAIL/ERROR 与报告路径。
5. **飞书验收**：只有明确群和相关成员已授权时才设 `enabled=true`，核对**仅一张**五指标及全部 FAIL/ERROR 编号、标题关键词、腾讯链接访问权限。业务失败也应看到报告。
6. **全量验收**：估算时长、确认执行环境不受单次工具超时影响后由用户明确批准。当前不支持断点续跑/运行中检查点。

## 7. 分发与升级/回滚原则

- 团队 release ZIP 只从**审核过的 Git 标签或审核清单**产生，禁止压缩当前个人安装目录（可能有 `config.acceptance*.local.json` 等临时私有文件）。
- 源码和安装版文件必须对齐；不要把尚未版本控制的本地热修复漏掉。
- 已有安装**不能直接使用初装脚本覆盖**；升级前备份旧包、`config.local.json`、`reports/`，仅更换公开文件，私有配置不变。
- 依赖开发的脚本或插件没有通过安全审计/隔离测试时，不得把项目当作已完成的统一自动化平台对外宣称。
- 回滚时使用上一个已验收的团队包和原先备份的**同一成员自己的**私有配置；当前 `legacy` 模式因无法保证失败清单完整而被拒绝；回滚整个Skill版本前须明确告知历史版本的多卡行为。

## v1.9.0 安装版私有原始SSE

安装后的 `reports/latest-failed-sse/` 存储每个拨测目标的最近一次失败响应快照。它是**运行产生的个人私有目录**，不进入安装发布清单。升级前不得覆盖或移动用户私人报告；此功能会在新一轮完整拨测结束后替换同目标最新文件，尚不自动清理其他目标的快照。

## 安装包中的输出适配器（v1.9.0）

团队发布清单必须包含 `scripts/lib/report_document.py`、`scripts/lib/diagnostic_context.py`、`scripts/lib/structured_assertions.py` 和 `scripts/lib/outputs/{__init__,base,file,registry}.py`；默认本地FileOutput已实现，云端Writer**尚未开发**。安装新成员时不要复制其他成员的 Feishu App 凭据、Webhook、文档ID和报告内容。报告Writer和群通知分别配置/授权。