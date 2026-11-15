# Changelog

All notable changes to Jarvis are listed here. This project started as a fork of
[OpenJarvis](https://github.com/open-jarvis/OpenJarvis); that project's earlier history is in the git log.

## Unreleased

### Added
- **Coding agent**: `/goal` loop with an evidence-based reviewer, `/plan`, `/review`, `/init`, automatic goals for
  clear build requests, read-only `explore`/`plan`/`review` subagents, `edit`/`grep`/`glob`/`todo_write` tools,
  tool-argument repair, `AGENTS.md` discovery and structured context compaction.
- **Voice mode**: hands-free conversation with local Whisper, spoken replies and speak-to-interrupt.
- **Desktop and browser control**: open apps and URLs, screen OCR, click/type/keys, window focus, behind a
  desktop-control switch; thread-safe Playwright browser tools.
- **Folder permissions** and an approvals flow that asks only for risky actions.
- **Auto model routing**: discovers local and OpenRouter models, probes tool-calling and learns from failures and speed.
- `computer-use` extra and `scripts/start-jarvis.ps1`.

### Changed
- The Settings page and model picker show which cloud keys the server holds, and list OpenRouter's full catalogue.
- Documentation, links and metadata now point at this repository; upstream's showcase, leaderboard site, release
  automation and branding assets were removed. The "share savings" popup no longer opens on its own.

### Fixed
- Tool calls from OpenRouter/OpenAI-backed models arrived with empty names; schemas rejected by OpenAI-backed
  models are sanitised; memory tools that returned "no backend" no longer make models claim they forget the chat.
