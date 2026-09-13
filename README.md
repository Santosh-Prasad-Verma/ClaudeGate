<p align="center">
  <img src="./assets/ClaudeGate.png" alt="ClaudeGate Logo" width="300" style="border-radius: 16px;">
</p>

<h1 align="center">ClaudeGate</h1>

<p align="center">
  <strong>Use Claude Code CLI, OpenAI Codex CLI, and OpenCode with ANY AI Model.</strong><br>
  <em>A fast, lightweight local gateway that connects your favorite coding agents to DeepSeek, Ollama, OpenAI, Gemini, Groq, OpenRouter, and more — 100% free, local, or cloud.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-3776AB?style=flat&logo=python&logoColor=white" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/Tests-39%20Passed-22c55e?style=flat&logo=pytest&logoColor=white" alt="39 Tests Passed">
  <img src="https://img.shields.io/badge/License-MIT-blue.svg?style=flat" alt="MIT License">
  <img src="https://img.shields.io/badge/Docker-Ready-2496ED?style=flat&logo=docker&logoColor=white" alt="Docker Ready">
</p>

---

## What is ClaudeGate?

CLI coding assistants like [Claude Code CLI](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview) and **OpenAI Codex CLI** can read your codebase, write features, run terminal commands, and fix bugs autonomously. 

Normally, these tools are locked into expensive proprietary subscriptions. 

**ClaudeGate is a local bridge for your machine.** It sits quietly in the background on port `8082`. When your coding agent sends a request, ClaudeGate translates it in real time into standard OpenAI format, sends it to **any AI provider of your choice**, and streams the response right back.

```text
┌─────────────────────────────────┐
│       Your Coding Agent         │
│  • Claude Code CLI              │
│  • OpenAI Codex CLI             │
│  • OpenCode / Aider             │
└────────────────┬────────────────┘
                 │ HTTP (localhost:8082)
                 ▼
┌─────────────────────────────────┐       ┌─────────────────────────────────────┐
│           ClaudeGate            │       │         Any AI Backend              │
│   Fast Local Gateway Proxy      │ ────► │  • 100% Free / Local: Ollama, vLLM  │
│  • Real-time Tool Streaming     │ ◄──── │  • Ultra-Fast: DeepSeek, Groq       │
│  • Automatic Failover           │       │  • Frontier: OpenAI, Gemini, Claude │
│  • Live Web Console Dashboard   │       │  • Multi-Model Tier Routing         │
└─────────────────────────────────┘       └─────────────────────────────────────┘
```

You get all the superpowers of elite coding assistants, with total freedom over which AI model powers them.

---

## Why Use ClaudeGate?

| Without ClaudeGate | With ClaudeGate |
|---|---|
| Locked to expensive official API subscriptions | Use **free tiers**, cheap providers, or your existing API keys |
| All your code is sent to closed cloud servers | Run **100% offline & private** on your computer with **Ollama** |
| Session crashes if an API rate-limit hits | **Automatic Failover**: switches to a backup model seamlessly |
| Works with only one specific CLI tool | Works with **Claude Code**, **Codex CLI**, **OpenCode**, and **Aider** |
| Risk of leaking API keys or secrets | **Secret Redaction**: automatically scrubs passwords & tokens |
| No visibility into what the proxy does | **Live Dashboard**: view latency, tokens, and logs in real time |

---

## See It In Action

ClaudeGate translating tool calls, bash commands, and streaming responses live:

<div align="center">
  <table>
    <tr>
      <td align="center" width="50%">
        <strong>1. ClaudeGate Running in Terminal 1</strong><br>
        <img src="./assets/proxy_terminal.png" alt="ClaudeGate Proxy Terminal" width="100%">
      </td>
      <td align="center" width="50%">
        <strong>2. Coding Agent Working in Terminal 2</strong><br>
        <img src="./assets/claude_terminal_ss.png" alt="Claude Code CLI Terminal" width="100%">
      </td>
    </tr>
  </table>
</div>

---

## Quick Start (3 Easy Steps)

### Step 1: Install ClaudeGate

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

---

### Step 2: Choose Your AI Provider

Pick your favorite provider in **one command**:

```bash
# 100% Free & Local with Ollama (no API key needed):
python start_proxy.py --preset ollama

# DeepSeek Official (Fast & smart coding):
python start_proxy.py --preset deepseek

# Groq (Ultra-fast inference):
python start_proxy.py --preset groq

# Google Gemini:
python start_proxy.py --preset gemini

# OpenRouter (100+ models):
python start_proxy.py --preset openrouter
```

> Or run the interactive setup wizard: `python start_proxy.py --setup`  
> *(If your provider requires an API key, edit `.env` and paste your key).*

---

### Step 3: Connect Your Agent & Start Coding

#### Option A: Claude Code CLI
Add to `~/.claude/settings.json`:
```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8082",
    "ANTHROPIC_API_KEY": "sk-claudegate-local"
  }
}
```
*Or export in your terminal: `export ANTHROPIC_BASE_URL="http://127.0.0.1:8082"`*

#### Option B: OpenAI Codex CLI
```bash
export OPENAI_BASE_URL="http://127.0.0.1:8082/v1"
export OPENAI_API_KEY="sk-claudegate-local"
```

#### Option C: OpenCode / Aider
```bash
export OPENAI_BASE_URL="http://127.0.0.1:8082/v1"
export OPENAI_API_KEY="sk-claudegate-local"
```

Now start ClaudeGate in Terminal 1:
```bash
python start_proxy.py
```
Open Terminal 2 in your coding project, launch your agent (`claude` or `codex`), and start coding!

---

## Live Web Console Dashboard

ClaudeGate includes a built-in real-time monitoring console at **`http://127.0.0.1:8082/dashboard`**.

- **Clean, Minimalist Interface**: Zero emojis, crisp SVG icons, and zero distracting animations.
- **Flight Recorder**: Live log of every request, model routed, duration, tokens, and tools invoked.
- **Real-Time Telemetry**: Tracks requests, success rate, upstream latency, and token cache savings.
- **Ping Test Button**: Instant one-click connection and latency test against your active AI provider.

---

## System & Model Doctor (`--doctor`)

Not sure if your setup is working? Run the diagnostic doctor:

```bash
python start_proxy.py --doctor
```

It performs 4 automated health checks:
1. **Environment Check**: Audits `.env` configuration, keys, and listening port.
2. **Local Engine Discovery**: Detects Ollama (`:11434`), LM Studio (`:1234`), and vLLM (`:8000`).
3. **Upstream Ping**: Tests connection and measures round-trip latency to your provider.
4. **Tool Calling Test**: Validates that your configured model can actually invoke functions and tools.

---

## Supported Providers & Presets

ClaudeGate includes 28+ ready-to-use presets in the `presets/` folder:

| Category | Provider | Preset Name | Best For |
|---|---|---|---|
| **100% Offline & Private** | **Ollama** | `ollama` | Zero data leaves your computer. Completely free. |
| | **LM Studio** | `lmstudio` | Easy desktop GUI for local models. |
| | **vLLM** | `vllm` | High-speed self-hosted GPU servers. |
| **High Performance Cloud** | **DeepSeek** | `deepseek` | Outstanding coding & reasoning at low cost. |
| | **Groq** | `groq` | Blazing-fast inference speeds. |
| | **Google Gemini** | `gemini` | Huge context window & fast speeds. |
| | **OpenRouter** | `openrouter` | Access to 100+ models with one API key. |
| | **OpenAI** | `openai` | GPT-4o, o1, and latest OpenAI models. |
| | **Mistral AI** | `mistral` | Mistral Large and Codestral models. |
| | **Alibaba Qwen** | `qwen` | Top-tier open coding models. |
| | **Together / Fireworks** | `together` / `fireworks` | High-throughput cloud hosting. |
| **Enterprise** | **Azure OpenAI** | `azure` | Corporate Azure cloud deployments. |
| | **Amazon Q** | `kiro` | Amazon Q developer bridge. |

Switch models anytime with `python start_proxy.py --preset <name>` without restarting your coding agent!

---

## Smart Features Under the Hood

- **Real-Time Tool Streaming**: Zero latency on tool calls — arguments stream directly to your CLI as they are generated.
- **Automatic Failover**: If your main provider hits rate limits (`429`) or errors (`503`), ClaudeGate instantly retries with your backup provider.
- **Extended Thinking & Reasoning**: Supports DeepSeek R1 and Groq reasoning blocks, while automatically stripping `<think>` tags so follow-up turns stay clean.
- **Anti-Timeout Heartbeats**: Emits 15-second keep-alive pings during long reasoning pauses so your terminal connection never drops with `ECONNRESET`.
- **Secret Redaction**: Protects AWS keys, GitHub tokens, and private SSH keys from being accidentally sent in prompts.
- **Context Overflow Guard**: Automatically compacts ancient tool outputs when conversations get very long, preventing `Context Length Exceeded` errors.

---

## Command Cheat Sheet

| Command | What it does |
|---|---|
| `python start_proxy.py` | Starts the ClaudeGate proxy server |
| `python start_proxy.py --preset <name>` | Loads a provider preset (e.g. `deepseek`, `ollama`, `groq`) |
| `python start_proxy.py --doctor` | Runs system, local engine, and model capability diagnostics |
| `python start_proxy.py --test` | Tests if your upstream provider is connected and responding |
| `python start_proxy.py --setup` | Interactive configuration wizard |
| `python start_proxy.py --version` | Shows current version |
| `curl http://127.0.0.1:8082/dashboard` | Opens the web console dashboard |
| `curl http://127.0.0.1:8082/health` | Checks proxy health status |

---

## Running with Docker (Optional)

```bash
# Start container in background
docker compose up -d --build

# View live logs
docker compose logs -f

# Stop container
docker compose down
```

---

## FAQ

<details>
<summary><strong>Do I need a paid Anthropic subscription or API key?</strong></summary>

**No!** You do not need an Anthropic subscription. ClaudeGate lets you power Claude Code CLI completely with other providers (like DeepSeek, Gemini, OpenAI, or 100% free local models like Ollama).
</details>

<details>
<summary><strong>Can my agent still edit files, run bash commands, and run tests?</strong></summary>

**Yes!** ClaudeGate translates all tool calls and bash commands seamlessly between your CLI agent and the backend model. File edits, terminal commands, and searches work just like native Claude.
</details>

<details>
<summary><strong>How do I run 100% free and offline?</strong></summary>

Install [Ollama](https://ollama.com), pull a model (e.g., `ollama pull qwen2.5-coder:32b` or `deepseek-r1`), and run:
```bash
python start_proxy.py --preset ollama
python start_proxy.py
```
Zero API keys, zero dollars spent, and zero data leaves your machine.
</details>

<details>
<summary><strong>How do I switch to a different model?</strong></summary>

Run `python start_proxy.py --preset <name>` (e.g. `deepseek`, `gemini`, `groq`), then restart `python start_proxy.py`. Your agent in Terminal 2 will automatically use the new model.
</details>

---

## License

This project is licensed under the **[MIT License](LICENSE)** — open and free for both personal and commercial use.

<br>

<div align="center">
  <img src="./assets/ClaudeGate.png" alt="ClaudeGate" width="110" style="border-radius: 14px;">
  
  <p style="margin-top: 12px; font-size: 15px;">
    <strong>Built for developers who love CLI coding agents and demand complete freedom of choice.</strong>
  </p>

  <p>
    <sub>If ClaudeGate helps your coding workflow, consider starring the repo on GitHub!</sub>
  </p>

  <br>

  <a href="https://github.com/Santosh-Prasad-Verma/ClaudeGate">
    <img src="https://img.shields.io/github/stars/Santosh-Prasad-Verma/ClaudeGate?style=social" alt="GitHub Stars">
  </a>
  &nbsp;
  <a href="https://github.com/Santosh-Prasad-Verma/ClaudeGate/fork">
    <img src="https://img.shields.io/github/forks/Santosh-Prasad-Verma/ClaudeGate?style=social" alt="GitHub Forks">
  </a>
  &nbsp;
  <a href="https://github.com/Santosh-Prasad-Verma/ClaudeGate/issues">
    <img src="https://img.shields.io/github/issues/Santosh-Prasad-Verma/ClaudeGate?style=flat-square" alt="Issues">
  </a>
  &nbsp;
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-3b82f6.svg?style=flat-square" alt="MIT License">
  </a>
</div>

