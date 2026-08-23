# Changelog

All notable changes to **ClaudeGate** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-08-23

### Added
- **Universal Anthropic Messages API Bridge**: Full bi-directional protocol translation from Anthropic `/v1/messages` and `/v1/messages/count_tokens` to OpenAI `/v1/chat/completions`.
- **Zero-Crash SSE Streaming**: Safe generator architecture that catches mid-stream upstream errors and translates them to Anthropic error events without crashing the ASGI loop or causing `ECONNRESET`.
- **Automatic Multi-Provider Failover**: Transparent fallback routing across backup providers on transient 500, 503, or 429 overloads.
- **Thinking / CoT Block Sanitization**: Multi-turn history cleanup stripping internal `<thinking>` blocks so upstream reasoning models (DeepSeek R1/V4, Qwen, etc.) do not reject follow-up turns.
- **Secret & PII Redaction Engine**: Real-time outgoing prompt sanitizer scrubbing GitHub PATs, AWS access keys, OpenAI tokens, and SSH private keys (`SANITIZE_SECRETS=true`).
- **24+ Provider Presets**: Out-of-the-box configurations for Stealth Ox Alpha, OpenRouter (Claude Opus 5 / Sonnet 5 / Haiku 4.5), OpenAI (GPT-5.6 Sol / Terra / Luna), Google Gemini (3.1 Pro / 3.7 Flash / 3.5 Flash-Lite), DeepSeek (V4-Pro / V4-Flash), Moonshot Kimi (K3 / K2.7 Code), Alibaba Qwen (Qwen3.8-Max / 3.7-Plus / 3.8-27B), Mistral (Large 3 / Medium 3.5 / Small 4), Meta AI (Muse Spark 1.2 / Llama 4 Maverick / Muse Glimmer), Z.ai GLM (GLM-5.3 / GLM-5-Turbo), Cohere (Command A+ / A / R7B), MiniMax (M3 / M2.7), Perplexity (Sonar Reasoning Pro), Groq, Together, Fireworks, Cerebras, SambaNova, SiliconFlow, local Ollama, LM Studio, vLLM, Azure OpenAI, and Kiro bridge.
- **Interactive CLI & Wizard**: `claudegate --setup`, `claudegate --test`, `claudegate --preset <name>`, and `claudegate --version`.
- **Extended Keep-Alive**: 10-minute HTTP connection keep-alive (`timeout_keep_alive=600`) tailored for Claude Code CLI idle pauses.
- **Docker Support**: Containerized deployment with `Dockerfile` and `docker-compose.yml`.
