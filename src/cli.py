"""ClaudeGate CLI & Interactive Setup Tool."""

import os
import sys
import shutil
import argparse
import asyncio
import time
import re
import httpx
from src.core.constants import BANNER


async def run_doctor() -> None:
    """Run comprehensive environment, local provider, and upstream capability diagnostics."""
    print_banner()
    print("\033[1mClaudeGate System & Model Doctor\033[0m\n")
    from src.core.config import get_config
    config = get_config()

    # 1. Environment & Configuration
    print("\033[94m[1/4] Environment & Configuration\033[0m")
    print(f"   • Python Version:    {sys.version.split()[0]}")
    print(f"   • Listen Interface:  http://{config.host}:{config.port}")
    print(f"   • Upstream URL:      {config.openai_base_url}")
    print(f"   • Upstream API Key:  {'[CONFIGURED]' if config.openai_api_key else '[MISSING]'}")
    print(f"   • Client API Key:    {'[CONFIGURED]' if config.anthropic_api_key else '[DISABLED/NONE]'}")
    print(f"   • Tier Models:       Big={config.big_model} | Mid={config.middle_model} | Small={config.small_model}")
    print(f"   • Fallback Endpoint: {config.fallback_base_url or 'None'} (Model: {config.fallback_model or 'None'})")
    print(f"   • Circuit Breaker:   {'Enabled (Threshold: ' + str(config.circuit_breaker_threshold) + ')' if config.circuit_breaker_enabled else 'Disabled'}")

    # 2. Local AI Engine Discovery
    print("\n\033[94m[2/4] Scanning Local AI Engines\033[0m")
    local_ports = [
        ("Ollama", "http://127.0.0.1:11434/api/tags"),
        ("LM Studio", "http://127.0.0.1:1234/v1/models"),
        ("vLLM / LocalAI", "http://127.0.0.1:8000/v1/models"),
    ]
    async with httpx.AsyncClient(timeout=1.5) as client:
        for engine, url in local_ports:
            try:
                res = await client.get(url)
                if res.status_code in (200, 401):
                    print(f"   \033[92m●\033[0m {engine}: ONLINE at {url.split('/')[2]}")
                else:
                    print(f"   \033[90m○\033[0m {engine}: Offline")
            except Exception:
                print(f"   \033[90m○\033[0m {engine}: Offline")

    # 3. Upstream Ping & Latency
    print("\n\033[94m[3/4] Testing Upstream Connection & Latency\033[0m")
    ping_url = f"{config.openai_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.openai_api_key}",
        "Content-Type": "application/json",
    }
    ping_payload = {
        "model": config.small_model,
        "messages": [{"role": "user", "content": "Ping"}],
        "max_tokens": 5,
    }
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(ping_url, headers=headers, json=ping_payload)
            lat = round((time.time() - t0) * 1000, 1)
            if resp.status_code == 200:
                print(f"   \033[92m[OK] Connection OK\033[0m — Latency: {lat} ms")
            else:
                print(f"   \033[91m[FAIL] Upstream returned HTTP {resp.status_code}\033[0m: {resp.text[:200]}")
    except Exception as e:
        print(f"   \033[91m[FAIL] Failed to reach upstream: {e}\033[0m")

    # 4. Tool Calling Capability Validation
    print("\n\033[94m[4/4] Validating Function/Tool Calling Capability\033[0m")
    tool_payload = {
        "model": config.middle_model,
        "messages": [{"role": "user", "content": "What is 42 plus 58? Call the add tool."}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "add",
                    "description": "Add two numbers",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "a": {"type": "number"},
                            "b": {"type": "number"},
                        },
                        "required": ["a", "b"],
                    },
                },
            }
        ],
        "max_tokens": 150,
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            tool_resp = await client.post(ping_url, headers=headers, json=tool_payload)
            if tool_resp.status_code == 200:
                data = tool_resp.json()
                msg = data.get("choices", [{}])[0].get("message", {})
                if msg.get("tool_calls"):
                    fn_name = msg["tool_calls"][0].get("function", {}).get("name")
                    fn_args = msg["tool_calls"][0].get("function", {}).get("arguments")
                    print(f"   \033[92m[OK] Tool Calling Supported!\033[0m (Invoked: {fn_name} with {fn_args})")
                else:
                    print("   \033[93m[WARN] Model returned text instead of calling tool. Verify function calling support for this model.\033[0m")
            else:
                print(f"   \033[91m[FAIL] Upstream tool probe returned HTTP {tool_resp.status_code}\033[0m: {tool_resp.text[:150]}")
    except Exception as e:
        print(f"   \033[91m[FAIL] Tool probe failed: {e}\033[0m")

    print("\nDiagnostic complete.\n")


def get_server_dir() -> str:
    """Return the repository directory containing .env and presets."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def print_banner() -> None:
    """Print the ASCII startup banner."""
    print("\033[96m" + BANNER + "\033[0m")

async def test_upstream_connection() -> bool:
    """Test connectivity to the configured upstream provider."""
    from src.core.config import get_config
    config = get_config()
    print("\nTesting upstream connection...")
    print(f"   Provider Base URL: {config.openai_base_url}")
    print(f"   Test Model:        {config.small_model}")
    
    headers = {
        "Authorization": f"Bearer {config.openai_api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": config.small_model,
        "messages": [{"role": "user", "content": "Ping"}],
        "max_tokens": 5
    }
    
    url = f"{config.openai_base_url.rstrip('/')}/chat/completions"
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code == 200:
                print("\033[92m[OK] Connection Successful! Model is active and responsive.\033[0m")
                print(f"   Status: {response.status_code} OK")
                return True
            else:
                print(f"\033[91m[FAIL] Upstream returned HTTP {response.status_code}:\033[0m")
                print(f"   {response.text[:300]}")
                return False
    except Exception as e:
        print(f"\033[91m[FAIL] Failed to connect to upstream: {e}\033[0m")
        return False

def _write_secure_env_file(path: str, data: str) -> None:
    """Atomically write file with strictly restricted owner-only (0600) permissions."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    mode = 0o600
    try:
        fd = os.open(path, flags, mode)
        with open(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.chmod(path, mode)
    except OSError:
        # Fallback if os.open flags fail on non-POSIX filesystems
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
        try:
            os.chmod(path, mode)
        except OSError:
            pass


def sanitize_env_value(val: str) -> str:
    """Sanitize user input to prevent environment variable and INI file injection."""
    if not val:
        return ""
    # Strip carriage returns, newlines, double quotes, and control chars
    return re.sub(r'[\r\n"\x00-\x1f\x7f-\x9f]', '', str(val)).strip()

def run_interactive_setup() -> None:
    """Interactive wizard to configure ClaudeGate."""
    print_banner()
    print("ClaudeGate Universal Setup Wizard\n")
    
    presets = {
        "1":  ("OpenRouter (Claude Opus 5, Sonnet 5, Haiku 4.5 / DeepSeek V4)", "openrouter.env"),
        "2":  ("Groq (DeepSeek V4 Pro, Llama 4 Maverick, Muse Glimmer)", "groq.env"),
        "3":  ("Ollama (100% Local - DeepSeek V4-Pro, Qwen3.6-35B, Muse Glimmer)", "ollama.env"),
        "4":  ("DeepSeek Official (DeepSeek V4-Pro & V4-Flash)", "deepseek.env"),
        "5":  ("Google Gemini (Gemini 3.1 Pro, 3.7 Flash & 3.5 Flash-Lite)", "gemini.env"),
        "6":  ("OpenAI Official (GPT-5.6 Sol, GPT-5.6 Terra, GPT-5.6 Luna)", "openai.env"),
        "7":  ("Moonshot Kimi (Kimi K3 2.8T Reasoning & K2.7 Code)", "kimi.env"),
        "8":  ("Alibaba Qwen / DashScope (Qwen3.8-Max 2.4T, Qwen3.7-Plus, Qwen3.8-27B)", "qwen.env"),
        "9":  ("Mistral AI (Mistral Large 3, Mistral Medium 3.5, Mistral Small 4)", "mistral.env"),
        "10": ("Perplexity AI (Sonar Reasoning Pro, Sonar Pro & Sonar)", "perplexity.env"),
        "11": ("Cohere AI (Command A+, Command A & Command R7B)", "cohere.env"),
        "12": ("MiniMax (MiniMax M3 Frontier & MiniMax M2.7)", "minimax.env"),
        "13": ("Meta AI (Muse Spark 1.2, Llama 4 Maverick, Muse Glimmer)", "meta.env"),
        "14": ("Z.ai / Zhipu GLM (GLM-5.3 Flagship, GLM-5-Turbo, GLM-4.7-Flash)", "zai.env"),
        "15": ("Together AI (DeepSeek V4-Pro, DeepSeek V4-Flash, Qwen3.8-27B)", "together.env"),
        "16": ("Fireworks AI (DeepSeek V4-Pro, DeepSeek V4-Flash, Qwen3.8-27B)", "fireworks.env"),
        "17": ("Cerebras AI (DeepSeek V4-Pro, Llama 4 Maverick, Muse Glimmer)", "cerebras.env"),
        "18": ("SambaNova Cloud (DeepSeek V4-Pro, Llama 4 Maverick, Qwen3.8-27B)", "sambanova.env"),
        "19": ("SiliconFlow (DeepSeek V4-Pro, DeepSeek V4-Flash, Qwen3.8-27B)", "siliconflow.env"),
        "20": ("LM Studio (Local Desktop - DeepSeek V4-Pro & Muse Glimmer)", "lmstudio.env"),
        "21": ("vLLM (Self-Hosted GPU Server - DeepSeek V4-Pro & Qwen3.6-35B)", "vllm.env"),
        "22": ("Azure OpenAI Service (o1 & GPT-5.6 Flagship Deployments)", "azure.env"),
        "23": ("Kiro Gateway (Amazon Q Developer / Claude Opus 5 Bridge)", "kiro.env"),
        "24": ("Ox Alpha (Next-Gen Autonomous Agent & Frontier Model)", "ox.env"),
    }
    
    print("Select an AI Provider Preset:")
    for k, (label, _) in presets.items():
        print(f"  [{k.rjust(2)}] {label}")
    print("  [25] Custom / Manual Configuration\n")
    
    choice = input("Enter choice (1-25) [1]: ").strip() or "1"
    server_dir = get_server_dir()
    target_env = os.path.join(server_dir, ".env")
    
    if choice in presets:
        _, preset_file = presets[choice]
        presets_dir = os.path.realpath(os.path.join(server_dir, "presets"))
        preset_path = os.path.realpath(os.path.join(presets_dir, preset_file))
        if preset_path.startswith(presets_dir) and os.path.exists(preset_path):
            shutil.copy(preset_path, target_env)
            try:
                os.chmod(target_env, 0o600)
            except OSError:
                pass  # Best-effort file permission hardening; unsupported on some filesystems
            print(f"\n[OK] Applied preset: {preset_file}")
        else:
            print(f"[ERROR] Preset file {preset_file} not found.")
            return
            
        # If not purely local, prompt for API key
        if choice not in ("3", "20", "21", "23"): # Local or bridge
            raw_key = input("\nEnter your API key for this provider: ").strip()
            api_key = sanitize_env_value(raw_key)
            if api_key:
                with open(target_env, "r", encoding="utf-8") as f:
                    content = f.read()
                
                # Replace common placeholders
                for placeholder in [
                    "sk-or-v1-YOUR_OPENROUTER_KEY", "gsk_YOUR_GROQ_API_KEY", "sk-YOUR_DEEPSEEK_KEY",
                    "AIzaSy_YOUR_GEMINI_KEY", "YOUR_MISTRAL_API_KEY", "sk-proj-YOUR_OPENAI_KEY",
                    "YOUR_TOGETHER_API_KEY", "YOUR_FIREWORKS_API_KEY", "csk-YOUR_CEREBRAS_API_KEY",
                    "YOUR_SAMBANOVA_API_KEY", "pplx-YOUR_PERPLEXITY_KEY", "YOUR_COHERE_API_KEY",
                    "sk-YOUR_SILICONFLOW_KEY", "YOUR_AZURE_OPENAI_KEY", "sk-YOUR_DASHSCOPE_KEY",
                    "sk-YOUR_MOONSHOT_KEY", "YOUR_MINIMAX_KEY", "sk-YOUR_OX_API_KEY",
                    "YOUR_META_API_KEY", "YOUR_ZHIPU_API_KEY"
                ]:
                    content = content.replace(placeholder, api_key)
                content = content.replace('OPENAI_API_KEY="your-api-key-here"', f'OPENAI_API_KEY="{api_key}"')
                _write_secure_env_file(target_env, content)
                print("[OK] API key successfully configured in .env.")
    else:
        # Manual flow with input sanitization
        api_key = sanitize_env_value(input("Upstream API Key: ").strip())
        raw_base_url = input("Upstream Base URL [https://openrouter.ai/api/v1]: ").strip() or "https://openrouter.ai/api/v1"
        base_url = sanitize_env_value(raw_base_url)
        big_model = sanitize_env_value(input("Big Model ID (Opus requests): ").strip())
        middle_model = sanitize_env_value(input("Middle Model ID (Sonnet requests): ").strip()) or big_model
        small_model = sanitize_env_value(input("Small Model ID (Haiku requests): ").strip()) or middle_model
        
        env_content = f"""OPENAI_API_KEY="{api_key}"
OPENAI_BASE_URL="{base_url}"
BIG_MODEL="{big_model}"
MIDDLE_MODEL="{middle_model}"
SMALL_MODEL="{small_model}"
HOST="127.0.0.1"
PORT="8082"
LOG_LEVEL="INFO"
ANTHROPIC_API_KEY="sk-claudegate-local"
REQUEST_TIMEOUT="120"
MAX_TOKENS_LIMIT="8192"
RATE_LIMIT_PER_MINUTE="120"
MAX_CONCURRENT_REQUESTS="30"
ALLOW_ANONYMOUS_ACCESS="false"
"""
        _write_secure_env_file(target_env, env_content)
        print("[OK] Custom configuration saved to .env.")

    print("\nSetup complete! You can test your connection with:\n   python start_proxy.py --test\n")

def apply_preset(preset_name: str) -> None:
    """Apply a preset by name with path traversal protection."""
    server_dir = get_server_dir()
    # Sanitize preset name to strictly alphanumeric, dashes, and underscores
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '', preset_name).lower()
    preset_file = f"{safe_name}.env"
    presets_dir = os.path.realpath(os.path.join(server_dir, "presets"))
    preset_path = os.path.realpath(os.path.join(presets_dir, preset_file))
    target_env = os.path.join(server_dir, ".env")
    
    if preset_path.startswith(presets_dir) and os.path.exists(preset_path):
        shutil.copy(preset_path, target_env)
        try:
            os.chmod(target_env, 0o600)
        except OSError:
            pass  # Best-effort file permission hardening; unsupported on some filesystems
        print(f"[OK] Loaded preset '{safe_name}' into .env.")
        print("Note: Remember to edit .env and insert your API key if required.")
    else:
        print(f"[ERROR] Preset '{safe_name}' not found. Available presets:")
        if os.path.exists(presets_dir):
            for p in sorted(os.listdir(presets_dir)):
                if p.endswith(".env"):
                    print(f"   • {p[:-4]}")

def cli_main() -> None:
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="claudegate",
        description="ClaudeGate — High-performance bridge between Claude Code and any OpenAI-compatible LLM."
    )
    parser.add_argument("--setup", action="store_true", help="Launch interactive configuration wizard")
    parser.add_argument("--test", action="store_true", help="Test connectivity to upstream LLM")
    parser.add_argument("--doctor", action="store_true", help="Run comprehensive system & model capability diagnostics")
    parser.add_argument("--preset", type=str, help="Apply a predefined preset (e.g., groq, gemini, ollama, deepseek, etc.)")
    parser.add_argument("--version", action="version", version="ClaudeGate v1.0.0")

    args = parser.parse_args()

    if args.setup:
        run_interactive_setup()
        sys.exit(0)
    elif args.test:
        sys.exit(0 if asyncio.run(test_upstream_connection()) else 1)
    elif args.doctor:
        asyncio.run(run_doctor())
        sys.exit(0)
    elif args.preset:
        apply_preset(args.preset)
        sys.exit(0)
        
    # Default: Run the server
    from src.main import main
    main()

if __name__ == "__main__":
    cli_main()
