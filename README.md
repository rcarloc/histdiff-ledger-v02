# HistDiff Change Ledger

Automated public ledger tracking wording, pricing, API rate-limit, and policy changes across 50 developer and AI platform pages, backed by the [HistDiff Apify Actor](https://apify.com/accommodating_fuel/histdiff).

- **Live Change Ledger:** https://rcarloc.github.io/histdiff-ledger-v02/
- **Monitored Watchlist (50 pages):** https://rcarloc.github.io/histdiff-ledger-v02/watchlist.html
- **Machine-Readable Indexes:** [`llms.txt`](https://rcarloc.github.io/histdiff-ledger-v02/llms.txt) · [`llms-full.txt`](https://rcarloc.github.io/histdiff-ledger-v02/llms-full.txt) · [`feed.xml`](https://rcarloc.github.io/histdiff-ledger-v02/feed.xml)
- **Suggest a URL to watch:** [Open a Watch Request issue](https://github.com/rcarloc/histdiff-ledger-v02/issues/new?title=Watch+request%3A+%3Cpage+name%3E&body=URL+to+monitor+for+changes+%28https%3A%2F%2F...%29%3A%0A) — validated and added automatically via GitHub Actions.

## Use HistDiff in AI Agents & Workflows

### MCP Server (Claude Desktop, Cursor, Claude Code)
```json
{
  "mcpServers": {
    "histdiff": {
      "command": "npx",
      "args": ["-y", "histdiff-mcp"],
      "env": {
        "APIFY_TOKEN": "YOUR_APIFY_API_TOKEN"
      }
    }
  }
}
```
Or download the packaged assets directly from [GitHub Releases (`v0.2.0`)](https://github.com/rcarloc/histdiff-ledger-v02/releases/tag/v0.2.0):
- `histdiff-mcp-0.2.0.tgz` (MCP stdio server)
- `histdiff-weekly-slack-monitor.json` (4-node n8n weekly Slack alert workflow)
