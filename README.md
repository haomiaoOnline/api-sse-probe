# api-sse-probe

A Python Agent Skill for authorized HTTPS JSON-to-SSE probing with deterministic assertions, durable response capture, offline rejudging, local reports, and optional approved Feishu notifications.

## Safety first

**This is a public, sanitized source release with a new Git history.** It contains no live credentials, local probe reports, production target URLs, private Tencent/Feishu document identifiers, or private acceptance logs. Do not add them to this repository.

To configure an authorized target:

1. Copy \`config.example.json\` to \`config.local.json\` (ignored by Git).
2. Set the API URL and sensitive header fields in the private file or environment variables; never commit actual \`powerkey\`, \`signature\`, API tokens, bot webhooks, tenant credentials or device identifiers.
3. Supply an authorized CSV/JSON or online sheet and run \`python3 scripts/probe.py\`.
4. See \`SKILL.md\`, \`references/configuration.md\`, and \`references/assertions.md\` for examples and supported modes.

## Workflow

\`\`\`mermaid
flowchart TD
  A[Read cases from configured source] --> B[Compile and validate deterministic assertions]
  B --> C{Valid rules?}
  C -- No --> Z[Fail before sending request]
  C -- Yes --> D[Freeze case snapshot]
  D --> E[Capture HTTPS SSE and persist evidence]
  E --> F[Offline rejudge: verify HTTP, SSE and DONE]
  F --> G[Extract assistant business fields]
  G --> H[Run typed assertions AND / OR / field rules]
  H --> I[Private report and authorized notifications]
\`\`\`

## Source files

- \`scripts/probe.py\` — public entry point and HTTP/SSE capture.
- \`scripts/lib/rule_contract.py\` — deterministic assertion validation and compilation.
- \`scripts/lib/assertion_rules.py\` — business-field extraction and assertion dispatch.
- \`scripts/lib/natural_assertions.py\` and \`scripts/lib/expression_assertions.py\` — explicit AND/OR grammar and execution.
- \`scripts/lib/structured_assertions.py\` — exact field validation.
- \`scripts/lib/v2_rejudge.py\` and \`scripts/lib/v2_engine.py\` — durable offline evaluation and orchestration.

Run the included offline unit tests with \`python3 -B -m unittest discover -s tests -q\`.

## License

MIT. See \`LICENSE\`.