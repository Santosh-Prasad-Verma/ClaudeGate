import os
from dotenv import load_dotenv

server_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
env_file = os.path.join(server_dir, '.env')
if os.path.exists(env_file):
    load_dotenv(env_file, override=False)

# Configuration
def _int_env(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name} must be true or false")
    return value in {"true", "1", "yes"}


class Config:
    def __init__(self, require_api_key: bool = True):
        self.openai_api_key = os.environ.get("OPENAI_API_KEY")
        if require_api_key and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for runtime commands")

        # Add Anthropic API key for client validation
        self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.anthropic_api_key:
            print("Warning: ANTHROPIC_API_KEY not set. Client API key validation will be disabled.")
        
        self.openai_base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.azure_api_version = os.environ.get("AZURE_API_VERSION")  # For Azure OpenAI
        self.host = os.environ.get("HOST", "127.0.0.1")
        self.port = _int_env("PORT", 8082, 1)
        if self.port > 65535:
            raise ValueError("PORT must be <= 65535")
        self.log_level = os.environ.get("LOG_LEVEL", "INFO")
        self.max_tokens_limit = _int_env("MAX_TOKENS_LIMIT", 4096, 1)
        self.min_tokens_limit = _int_env("MIN_TOKENS_LIMIT", 100, 1)
        if self.min_tokens_limit > self.max_tokens_limit:
            raise ValueError("MIN_TOKENS_LIMIT must be <= MAX_TOKENS_LIMIT")

        # Connection settings
        self.request_timeout = _int_env("REQUEST_TIMEOUT", 90, 1)
        self.max_retries = _int_env("MAX_RETRIES", 2, 0)
        
        # Model settings - BIG and SMALL models
        self.big_model = os.environ.get("BIG_MODEL", "gpt-4o")
        self.middle_model = os.environ.get("MIDDLE_MODEL", self.big_model)
        self.small_model = os.environ.get("SMALL_MODEL", "gpt-4o-mini")

        # Fallback provider configuration (automatic failover on 503/429/timeouts)
        self.fallback_base_url = os.environ.get("FALLBACK_BASE_URL")
        self.fallback_api_key = os.environ.get("FALLBACK_API_KEY", self.openai_api_key)
        self.fallback_model = os.environ.get("FALLBACK_MODEL")

        # Security & Traffic Controls
        self.allow_anonymous_access = _bool_env("ALLOW_ANONYMOUS_ACCESS", False)
        self.rate_limit_per_minute = _int_env("RATE_LIMIT_PER_MINUTE", 120, 0)
        self.max_concurrent_requests = _int_env("MAX_CONCURRENT_REQUESTS", 30, 1)
        raw_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:8082,http://127.0.0.1:8082,http://localhost:3000,http://127.0.0.1:3000")
        self.allowed_origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
        
    def validate_api_key(self):
        """Basic API key validation"""
        if not self.openai_api_key:
            return False
        # Basic format check for OpenAI API keys
        if not self.openai_api_key.startswith('sk-'):
            return False
        return True
        
    def validate_client_api_key(self, client_api_key):
        """Validate client's Anthropic API key using constant-time comparison."""
        import hmac
        if not self.anthropic_api_key:
            return True
        if not client_api_key or not isinstance(client_api_key, str):
            return False
        return hmac.compare_digest(client_api_key, self.anthropic_api_key)
    
    def get_custom_headers(self):
        """Get custom headers from environment variables"""
        custom_headers = {}
        
        # Get all environment variables
        env_vars = dict(os.environ)
        
        # Find CUSTOM_HEADER_* environment variables
        for env_key, env_value in env_vars.items():
            if env_key.startswith('CUSTOM_HEADER_'):
                # Convert CUSTOM_HEADER_KEY to Header-Key
                # Remove 'CUSTOM_HEADER_' prefix and convert to header format
                header_name = env_key[14:]  # Remove 'CUSTOM_HEADER_' prefix
                
                if header_name:  # Make sure it's not empty
                    # Convert underscores to hyphens for HTTP header format
                    header_name = header_name.replace('_', '-')
                    custom_headers[header_name] = env_value
        
        return custom_headers

# Keep imports usable by setup/help commands. Runtime entry points must call
# get_config() before contacting an upstream provider or serving requests.
config = Config(require_api_key=False)


def get_config() -> Config:
    """Load validated runtime configuration on demand."""
    global config
    config = Config(require_api_key=True)
    return config
