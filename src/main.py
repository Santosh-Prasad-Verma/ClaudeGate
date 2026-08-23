from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api.endpoints import router as api_router
import uvicorn
import sys
from src.core.config import config
from src.cli import BANNER

app = FastAPI(
    title="ClaudeGate",
    description="High-performance bridge between Claude Code / Anthropic SDK and any OpenAI-compatible LLM provider.",
    version="1.0.0"
)

# Secure CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.allowed_origins if config.allowed_origins else ["http://localhost:8082", "http://127.0.0.1:8082"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "HEAD", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "x-api-key", "anthropic-version", "anthropic-beta", "User-Agent"],
)

app.include_router(api_router)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print(BANNER)
        print("Usage: claudegate [OPTIONS]")
        print("       python start_proxy.py [OPTIONS]")
        print("")
        print("Options:")
        print("  --setup              Launch interactive configuration wizard")
        print("  --test               Test connectivity with upstream LLM")
        print("  --preset <NAME>      Quick-load provider preset (openrouter, groq, ollama, deepseek, openai, azure)")
        print("  --version            Show version")
        print("  --help               Show this help message")
        print("")
        print("Configuration (.env):")
        print(f"  Provider URL:        {config.openai_base_url}")
        print(f"  Big Model (opus):    {config.big_model}")
        print(f"  Middle Model(sonnet):{config.middle_model}")
        print(f"  Small Model (haiku): {config.small_model}")
        print(f"  Listen Address:      {config.host}:{config.port}")
        sys.exit(0)

    # Configuration summary
    print("\033[96m" + BANNER + "\033[0m")
    print(f"  \033[92m●\033[0m Upstream Base URL:  {config.openai_base_url}")
    print(f"  \033[92m●\033[0m Big Model (opus):    {config.big_model}")
    print(f"  \033[92m●\033[0m Mid Model (sonnet):  {config.middle_model}")
    print(f"  \033[92m●\033[0m Small Model (haiku): {config.small_model}")
    print(f"  \033[92m●\033[0m Max Tokens:          {config.max_tokens_limit}")
    print(f"  \033[92m●\033[0m Gateway Server:      http://{config.host}:{config.port}")
    print(f"  \033[92m●\033[0m Client Key Auth:     {'Enabled' if config.anthropic_api_key else 'Disabled'}")
    print("")

    # Parse log level
    log_level = config.log_level.split()[0].lower()
    valid_levels = ['debug', 'info', 'warning', 'error', 'critical']
    if log_level not in valid_levels:
        log_level = 'info'

    # Start server with 10-minute keep-alive timeout to prevent Node.js ECONNRESET
    uvicorn.run(
        "src.main:app",
        host=config.host,
        port=config.port,
        log_level=log_level,
        timeout_keep_alive=600,
        reload=False,
    )


if __name__ == "__main__":
    main()
