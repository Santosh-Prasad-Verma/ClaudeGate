<p align="center">
  <img src="./assets/ClaudeGate.png" alt="ClaudeGate Logo" width="320" style="border-radius: 16px;">
</p>

<h1 align="center">ClaudeGate</h1>

<p align="center">
  <strong>Universal AI Gateway for Claude Code CLI, OpenAI Codex CLI, OpenCode, and Beyond.</strong><br>
  <em>A high-performance, lightweight local proxy that connects autonomous coding agents to DeepSeek, Ollama, OpenAI, Gemini, Groq, OpenRouter, and self-hosted vLLM.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-3776AB?style=flat&logo=python&logoColor=white" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/Test_Suite-39_Passed-22c55e?style=flat&logo=pytest&logoColor=white" alt="Pytest 39 Passed">
  <img src="https://img.shields.io/badge/License-MIT-blue.svg?style=flat" alt="MIT License">
  <img src="https://img.shields.io/badge/Docker-Ready-2496ED?style=flat&logo=docker&logoColor=white" alt="Docker Ready">
</p>

---

## What is ClaudeGate?

Autonomous terminal assistants like [Claude Code CLI](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview) and **OpenAI Codex CLI** can read repositories, edit files, execute commands, and run tests independently. However, they are typically locked into their respective proprietary APIs.

**ClaudeGate is a universal proxy and protocol translation gateway for your computer.** It runs locally on your machine and translates bidirectional agent protocols on the fly:
- **Anthropic Messages API (`/v1/messages`)** &rarr; Translated to OpenAI Chat Completions or Responses.
- **Codex Responses Wire API (`/v1/responses`)** &rarr; Native streaming support for Codex CLI.
- **OpenAI Chat API (`/v1/chat/completions`)** &rarr; Drop-in endpoint for OpenCode, Aider, and custom agents.

```text
┌───────────────────────────────┐
│     Coding Agent CLIs         │
│  • Claude Code CLI            │
│  • OpenAI Codex CLI           │
│  • OpenCode / Aider           │
└───────────────┬───────────────┘
                │ HTTP (localhost:8082)
                ▼
┌───────────────────────────────┐       ┌────────────────────────────────────┐
│          ClaudeGate           │       │    Upstream AI Backends            │
│  • Stream-through Tool Engine │ ────► │  • 100% Free / Local: Ollama, vLLM │
│  • Circuit Breaker Failover   │ ◄──── │  • Fast Cloud: DeepSeek, Groq      │
│  • Anti-ECONNRESET Heartbeat  │       │  • Frontier: OpenAI, Gemini, Kimi  │
│  • Minimal SVG Live Console   │       │  • Multi-Tier Routing (Opus/Sonnet)│
└───────────────────────────────┘       └────────────────────────────────────┘
```

You get the complete autonomous capability of elite CLI coding agents, with total control over which AI model powers them.

---

## Key Features

### 1. Multi-Agent Protocol Support
- **Claude Code CLI**: Full Anthropic Messages streaming API, tool calling, token counter (`/v1/messages/count_tokens`), and extended thinking support.
- **OpenAI Codex CLI**: Direct support for the `/v1/responses` wire protocol with full tool invocation and SSE streaming.
- **Universal OpenAI (`/v1/chat/completions`)**: Seamless integration with OpenCode, Aider, CommandCode, and Cursor.

### 2. Live Telemetry Console (`/dashboard`)
- Built-in real-time web console at `http://127.0.0.1:8082/dashboard`.
- **Minimalist SVG UI**: Fast, clean, dark-mode design with zero emojis and zero hover animations.
- **Flight Recorder**: Live in-memory ring buffer tracking recent requests, latency, status codes, and tool calls.
- **Live Metrics**: Gateway success rate, average latency, and real-time input/output/cached token counts.
- **Local Connectivity Probe**: Instant "Ping Test" button to verify upstream model health with real latency numbers.

### 3. System & Model Doctor (`--doctor`)
- Run `python start_proxy.py --doctor` to perform 4-stage health diagnostics:
  1. Environment & active `.env` configuration audit.
  2. Automatic local AI engine discovery (Ollama on `:11434`, LM Studio on `:1234`, vLLM on `:8000`).
  3. Upstream connectivity and latency ping.
  4. Synthetic function-calling probe to guarantee the upstream model correctly invokes tools.

### 4. Zero-Latency Circuit Breaker & Automatic Failover
- 3-state state machine (`CLOSED`, `OPEN`, `HALF_OPEN`).
- If your primary provider fails or hits rate limits (`429`, `500`, `502`, `503`), requests are routed **immediately to your fallback provider with zero retry delay**.
- Automatically tests primary provider recovery after a configurable reset timeout.

### 5. Multi-Host Per-Tier Model Routing
- Route Opus, Sonnet, and Haiku requests to different models **and completely different backend hosts**:
  - `BIG_MODEL_BASE_URL` (e.g. OpenAI GPT-4o / Azure)
  - `MIDDLE_MODEL_BASE_URL` (e.g. DeepSeek V3 / VyceAI)
  - `SMALL_MODEL_BASE_URL` (e.g. Local Ollama / Groq)

### 6. Production Reliability Hardening
- **Direct Stream-Through Tool Calling**: Function arguments are streamed in real time to the CLI without buffering latency.
- **Anti-`ECONNRESET` SSE Heartbeat Pings**: Sends compliant 15-second keep-alive frames so clients never disconnect during deep reasoning pauses.
- **Extended Thinking & Reasoning**: Automatically translates DeepSeek `reasoning_content` and Groq `reasoning` into native Anthropic thinking blocks, while stripping in-band `<think>` tags.
- **Context Overflow Guard**: Automatically compacts ancient intermediate tool outputs when conversations approach context limits, preserving context without crashing.
- **PII & Secret Redaction**: Scrubs AWS secrets, GitHub PATs, private keys, and passwords from prompts before sending upstream.
- **OpenAI o1/o3/o4 Compatibility**: Automatically maps `max_tokens` to `max_completion_tokens` and sets `reasoning_effort` based on thinking budgets.
- **Vision Fallback**: Gracefully degrades image inputs to textual placeholders when targeting text-only code models.

---

## Quick Start

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/Santosh-Prasad-Verma/ClaudeGate.git
cd ClaudeGate

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Your AI Provider

Choose from 24+ pre-configured provider presets or run the interactive setup wizard:

```bash
# Option A: Interactive Setup Wizard
python start_proxy.py --setup

# Option B: Quick-load a preset
python start_proxy.py --preset deepseek    # DeepSeek Official
python start_proxy.py --preset ollama      # 100% Free / Local Ollama
python start_proxy.py --preset groq        # Ultra-fast Groq
python start_proxy.py --preset gemini      # Google Gemini
python start_proxy.py --preset openrouter  # OpenRouter
```

*(Edit `.env` to insert your API key if required by your chosen provider).*

### 3. Verify System Health & Start

```bash
# Run the diagnostic doctor
python start_proxy.py --doctor

# Test upstream connectivity
python start_proxy.py --test

# Start ClaudeGate proxy
python start_proxy.py
```

ClaudeGate is now running on `http://127.0.0.1:8082` and the live dashboard is available at `http://127.0.0.1:8082/dashboard`.

---

## Connecting Your Coding Agents

### Claude Code CLI

#### Permanent Setup (Recommended)
Add to your `~/.claude/settings.json`:
```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8082",
    "ANTHROPIC_API_KEY": "sk-claudegate-local"
  }
}
```

#### Terminal Session Only
```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:8082"
export ANTHROPIC_API_KEY="sk-claudegate-local"
claude
```

---

### OpenAI Codex CLI

Configure Codex CLI to point to ClaudeGate's native `/v1/responses` wire endpoint:

```bash
export OPENAI_BASE_URL="http://127.0.0.1:8082/v1"
export OPENAI_API_KEY="sk-claudegate-local"
codex
```

Or configure `~/.codex/config.toml`:
```toml
model_provider = "openai"
base_url = "http://127.0.0.1:8082/v1"
wire_api = "responses"
```

---

### OpenCode / Aider / Cursor

For agents that communicate via standard OpenAI Chat Completions:

```bash
# OpenCode / CommandCode
export OPENAI_BASE_URL="http://127.0.0.1:8082/v1"
export OPENAI_API_KEY="sk-claudegate-local"

# Aider
aider --openai-api-base "http://127.0.0.1:8082/v1" --openai-api-key "sk-claudegate-local"
```

---

## Live Console Dashboard

Open your browser to:
```text
http://127.0.0.1:8082/dashboard
```

The console provides:
- **Zero-Emoji SVG Interface**: Clean, minimal, high-contrast dark theme designed for engineers.
- **Flight Records**: Every request logged with timestamp, inbound model, routed upstream model, duration, token usage, and status.
- **Real-Time Telemetry**: Auto-refreshes every 3 seconds via `/api/flight-recorder`.
- **Filter Controls**: Filter by all requests, Claude models, Codex models, OpenAI, or error logs.
- **Ping Probe**: Authenticated test probe button to measure real-time latency against your active upstream backend.

---

## Supported Providers & Presets

ClaudeGate includes 28+ tested presets in the `presets/` directory:

| Category | Provider | Preset | Description |
|---|---|---|---|
| **Local & Private** | **Ollama** | `ollama` | 100% offline, zero data leaves your computer |
| | **LM Studio** | `lmstudio` | Local desktop inference with GUI |
| | **vLLM** | `vllm` | High-throughput self-hosted GPU cluster |
| **High Performance** | **DeepSeek** | `deepseek` | DeepSeek V3 / R1 reasoning and code generation |
| | **Groq** | `groq` | Near-instant LPU inference speeds |
| | **Google Gemini** | `gemini` | Gemini 2.5/3.0 Pro & Flash with massive context |
| | **OpenRouter** | `openrouter` | Gateway to 100+ open-source and proprietary models |
| | **OpenAI Official** | `openai` | GPT-4o, o1, o3-mini |
| | **Mistral AI** | `mistral` | Codestral and Mistral Large 2 |
| | **Alibaba Qwen** | `qwen` | Qwen 2.5 Coder 32B / DashScope |
| | **Together / Fireworks** | `together` / `fireworks` | High-throughput serverless open-source models |
| **Enterprise** | **Azure OpenAI** | `azure` | Enterprise Azure OpenAI deployments |
| | **Amazon Q Bridge** | `kiro` | Amazon Q Developer bridge |

Switch presets anytime without restarting your coding agent:
```bash
python start_proxy.py --preset deepseek
```

---

## Configuration Reference

Key variables configurable in `.env`:

| Variable | Default | Description |
|---|---|---|
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Primary upstream API base URL |
| `OPENAI_API_KEY` | *(required)* | Primary upstream API key |
| `BIG_MODEL` | `deepseek-v4-flash` | Model used for Claude Opus requests |
| `MIDDLE_MODEL` | `deepseek-v4-flash` | Model used for Claude Sonnet requests |
| `SMALL_MODEL` | `deepseek-v4-flash` | Model used for Claude Haiku requests |
| `HOST` | `127.0.0.1` | Local listen interface |
| `PORT` | `8082` | Local listen port |
| `CIRCUIT_BREAKER_ENABLED` | `true` | Enable zero-latency circuit breaker |
| `CIRCUIT_BREAKER_THRESHOLD`| `3` | Consecutive failures before tripping |
| `FALLBACK_BASE_URL` | *(optional)* | Upstream URL for failover |
| `FALLBACK_MODEL` | *(optional)* | Target model for failover |
| `SANITIZE_SECRETS` | `true` | Scrub API keys and tokens from prompts |
| `RATE_LIMIT_PER_MINUTE` | `120` | Rate limit per client IP |

---

## CLI Command Cheat Sheet

| Command | Description |
|---|---|
| `python start_proxy.py` | Start the ClaudeGate proxy server |
| `python start_proxy.py --setup` | Launch interactive configuration wizard |
| `python start_proxy.py --doctor` | Run comprehensive system, local model, and tool capability diagnostics |
| `python start_proxy.py --test` | Test connectivity with configured upstream backend |
| `python start_proxy.py --preset <name>` | Load a provider preset into `.env` (e.g. `ollama`, `groq`, `deepseek`) |
| `python start_proxy.py --version` | Display version information |
| `python start_proxy.py --help` | Display command-line options |
| `curl http://127.0.0.1:8082/health` | Query gateway health status |
| `curl http://127.0.0.1:8082/api/flight-recorder` | Query telemetry and recent request history |

---

## Running with Docker

```bash
# Build and start in background
docker compose up -d --build

# Follow live proxy logs
docker compose logs -f

# Stop container
docker compose down
```

---

## Verification & Testing

ClaudeGate maintains a full automated test suite covering all conversion mechanics, schema sanitization, streaming edge cases, and failover:

```bash
# Run pytest test suite (39 tests)
python3 -m pytest

# Run type checker
npx --yes pyright
```

---

## Contributing

Contributions, issues, and feature requests are welcome!
- Check out [CONTRIBUTING.md](CONTRIBUTING.md) to get started.
- Review our [Code of Conduct](CODE_OF_CONDUCT.md).
- To add a new provider preset, add `presets/<provider_name>.env` and submit a PR!

---

## License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for details.
