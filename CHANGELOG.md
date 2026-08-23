# Changelog

All notable changes to **ClaudeGate** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-08-23

### Added
- **Complete Anthropic Messages API Bridge**: Full translation from Anthropic `/v1/messages` and `/v1/messages/count_tokens` to OpenAI `/v1/chat/completions`.
- **Zero-Crash SSE Streaming**: Safe generator architecture that catches mid-stream upstream errors and translates them to Anthropic error events without crashing the ASGI loop or causing `ECONNRESET`.
- **Thinking / CoT Block Sanitization**: Multi-turn history cleanup stripping internal `<thinking>` blocks so upstream providers (like Qwen/DeepSeek) do not reject follow-up turns.
- **Provider Presets**: Out-of-the-box configurations for OpenRouter, Groq, Ollama (100% local), DeepSeek, OpenAI, and Azure OpenAI.
- **Interactive CLI & Wizard**: `claudegate --setup`, `claudegate --test`, `claudegate --preset <name>`, and `claudegate --version`.
- **Extended Keep-Alive**: 10-minute HTTP connection keep-alive (`timeout_keep_alive=600`) tailored for Claude Code CLI idle pauses.
- **Docker Support**: Containerized deployment with `Dockerfile` and `docker-compose.yml`.
