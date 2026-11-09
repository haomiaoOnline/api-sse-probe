# Security policy for public releases

Never commit live API URLs, powerkeys, signatures, tokens, OAuth credentials, webhook URLs/secrets, private device IDs, Tencent/Feishu sheet links, customer responses, reports, CSV exports, local databases or runtime logs.

Use local ignored \`config.local.json\` and environment variables for credentials. \`config.example.json\` must keep credentials empty or clearly marked placeholders. The public repository is generated from an explicit allowlist with a clean root commit, not from an internal Git history.

Before future pushes, scan **all staged files and the commits being pushed** for high-entropy secrets and private URLs, and verify ignored files are not tracked. If credentials were accidentally exposed, immediately revoke and rotate them; simply deleting a Git file does not erase public Git history.