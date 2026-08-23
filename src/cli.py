"""ClaudeGate CLI & Interactive Setup Tool."""

import os
import sys
import shutil
import argparse
import asyncio
from typing import Optional
import httpx
from src.core.config import server_dir, config

BANNER = r"""
   _____ _                 _       _____       _       
  / ____| |               | |     / ____|     | |      
 | |    | | __ _ _   _  __| | ___| |  __  __ _| |_ ___ 
 | |    | |/ _` | | | |/ _` |/ _ \ | |_ |/ _` | __/ _ \
 | |____| | (_| | |_| | (_| |  __/ |__| | (_| | ||  __/
  \_____|_|\__,_|\__,_|\__,_|\___|\_____|\__,_|\__\___|
                                                       
  🔓 Connect Any AI Model to Claude Code CLI / Anthropic SDK
"""

def print_banner() -> None:
    """Print the ASCII startup banner."""
    print("\033[96m" + BANNER + "\033[0m")

async def test_upstream_connection() -> bool:
    """Test connectivity to the configured upstream provider."""
    print("\n🔍 Testing upstream connection...")
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
                print("\033[92m✅ Connection Successful! Model is active and responsive.\033[0m")
                print(f"   Status: {response.status_code} OK")
                return True
            else:
                print(f"\033[91m❌ Upstream returned HTTP {response.status_code}:\033[0m")
                print(f"   {response.text[:300]}")
                return False
    except Exception as e:
        print(f"\033[91m❌ Failed to connect to upstream: {e}\033[0m")
        return False

import re

def sanitize_env_value(val: str) -> str:
    """Sanitize user input to prevent environment variable and INI file injection."""
    if not val:
        return ""
    # Strip carriage returns, newlines, double quotes, and control chars
    return re.sub(r'[\r\n"\x00-\x1f\x7f-\x9f]', '', str(val)).strip()

def run_interactive_setup() -> None:
    """Interactive wizard to configure ClaudeGate."""
    print_banner()
    print("🛠️  ClaudeGate Universal Setup Wizard\n")
    
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
    target_env = os.path.join(server_dir, ".env")
    
    if choice in presets:
        _, preset_file = presets[choice]
        presets_dir = os.path.realpath(os.path.join(server_dir, "presets"))
        preset_path = os.path.realpath(os.path.join(presets_dir, preset_file))
        if preset_path.startswith(presets_dir) and os.path.exists(preset_path):
            shutil.copy(preset_path, target_env)
            print(f"\n✅ Applied preset: {preset_file}")
        else:
            print(f"❌ Preset file {preset_file} not found.")
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
                with open(target_env, "w", encoding="utf-8") as f:
                    f.write(content)
                print("✅ API key successfully configured in .env.")
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
        with open(target_env, "w", encoding="utf-8") as f:
            f.write(env_content)
        print("✅ Custom configuration saved to .env.")

    print("\n🎉 Setup complete! You can test your connection with:\n   python start_proxy.py --test\n")

def apply_preset(preset_name: str) -> None:
    """Apply a preset by name with path traversal protection."""
    # Sanitize preset name to strictly alphanumeric, dashes, and underscores
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '', preset_name).lower()
    preset_file = f"{safe_name}.env"
    presets_dir = os.path.realpath(os.path.join(server_dir, "presets"))
    preset_path = os.path.realpath(os.path.join(presets_dir, preset_file))
    target_env = os.path.join(server_dir, ".env")
    
    if preset_path.startswith(presets_dir) and os.path.exists(preset_path):
        shutil.copy(preset_path, target_env)
        print(f"✅ Loaded preset '{safe_name}' into .env.")
        print("👉 Remember to edit .env and insert your API key if required.")
    else:
        print(f"❌ Preset '{safe_name}' not found. Available presets:")
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
    parser.add_argument("--preset", type=str, help="Apply a predefined preset (e.g., groq, gemini, ollama, deepseek, etc.)")
    parser.add_argument("--version", action="version", version="ClaudeGate v1.0.0")

    args = parser.parse_args()

    if args.setup:
        run_interactive_setup()
        sys.exit(0)
    elif args.test:
        asyncio.run(test_upstream_connection())
        sys.exit(0)
    elif args.preset:
        apply_preset(args.preset)
        sys.exit(0)
        
    # Default: Run the server
    from src.main import main
    main()

if __name__ == "__main__":
    cli_main()
