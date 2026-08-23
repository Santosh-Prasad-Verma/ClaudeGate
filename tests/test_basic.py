import os
import pytest
from fastapi.testclient import TestClient

# Ensure test environment variables before imports
os.environ["OPENAI_API_KEY"] = "sk-test-openai-key-for-unit-tests-12345"
os.environ["ALLOW_ANONYMOUS_ACCESS"] = "true"

from src.main import app
from src.security.sanitizer import SecretSanitizer
from src.models.claude import ClaudeMessagesRequest, ClaudeMessage
from src.conversion.request_converter import convert_claude_to_openai
from src.core.model_manager import model_manager


@pytest.fixture
def client():
    return TestClient(app)


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "healthy"
    assert data.get("service") == "claudegate"


def test_hello_endpoint(client):
    response = client.get("/api/hello")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "ok"
    assert "ClaudeGate is online" in data.get("message", "")


def test_root_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data.get("service") == "ClaudeGate"
    assert data.get("version") == "1.0.0"
    assert "endpoints" in data


def test_secret_sanitizer():
    sanitizer = SecretSanitizer(enabled=True)
    sample_text = "Here is my secret token: ghp_123456789012345678901234567890123456"
    sanitized = sanitizer.sanitize_text(sample_text)
    assert "[REDACTED_GITHUB_TOKEN]" in sanitized
    assert "ghp_123456789012345678901234567890123456" not in sanitized


def test_claude_to_openai_conversion():
    claude_req = ClaudeMessagesRequest(
        model="claude-3-5-sonnet-20241022",
        system="You are a helpful coding assistant.",
        messages=[
            ClaudeMessage(role="user", content="Hello, ClaudeGate!")
        ],
        max_tokens=1000,
        temperature=0.7,
        stream=False,
    )
    openai_req = convert_claude_to_openai(claude_req, model_manager)
    assert "messages" in openai_req
    assert len(openai_req["messages"]) >= 2  # system + user
    assert openai_req["messages"][0]["role"] == "system"
    assert openai_req["messages"][0]["content"] == "You are a helpful coding assistant."
    assert openai_req["messages"][1]["role"] == "user"
    assert openai_req["messages"][1]["content"] == "Hello, ClaudeGate!"
    assert openai_req["max_tokens"] == 1000


def test_model_manager_tiers():
    # Direct pass-throughs for Frontier Flagship Models
    assert model_manager.map_claude_model_to_openai("gemini-3.7-flash") == "gemini-3.7-flash"
    assert model_manager.map_claude_model_to_openai("gemini-3.1-pro") == "gemini-3.1-pro"
    assert model_manager.map_claude_model_to_openai("gemini-3.5-flash-lite") == "gemini-3.5-flash-lite"
    assert model_manager.map_claude_model_to_openai("qwen3.8-max") == "qwen3.8-max"
    assert model_manager.map_claude_model_to_openai("qwen3.7-plus") == "qwen3.7-plus"

    assert model_manager.map_claude_model_to_openai("deepseek-v4-pro") == "deepseek-v4-pro"
    assert model_manager.map_claude_model_to_openai("deepseek-v4-flash") == "deepseek-v4-flash"
    assert model_manager.map_claude_model_to_openai("gpt-5.6-sol") == "gpt-5.6-sol"
    assert model_manager.map_claude_model_to_openai("gpt-5.6-terra") == "gpt-5.6-terra"
    assert model_manager.map_claude_model_to_openai("gpt-5.6-luna") == "gpt-5.6-luna"
    assert model_manager.map_claude_model_to_openai("minimax-m3") == "minimax-m3"
    assert model_manager.map_claude_model_to_openai("kimi-k3") == "kimi-k3"
    assert model_manager.map_claude_model_to_openai("kimi-k2.7-code") == "kimi-k2.7-code"
    assert model_manager.map_claude_model_to_openai("muse-spark-1.2") == "muse-spark-1.2"
    assert model_manager.map_claude_model_to_openai("muse-glimmer") == "muse-glimmer"
    assert model_manager.map_claude_model_to_openai("llama-4-maverick") == "llama-4-maverick"
    assert model_manager.map_claude_model_to_openai("glm-5.3") == "glm-5.3"
    assert model_manager.map_claude_model_to_openai("sonar-reasoning-pro") == "sonar-reasoning-pro"
    assert model_manager.map_claude_model_to_openai("command-a-plus") == "command-a-plus"
    assert model_manager.map_claude_model_to_openai("ox-alpha") == "ox-alpha"

    assert model_manager.map_claude_model_to_openai("deepseek/deepseek-r1") == "deepseek/deepseek-r1"
    assert model_manager.map_claude_model_to_openai("qwen2.5-coder-32b") == "qwen2.5-coder-32b"

    # Claude 5.x / 4.8 / 4.5 / Mythos Tier classifications
    assert model_manager.map_claude_model_to_openai("claude-opus-5") == model_manager.config.big_model
    assert model_manager.map_claude_model_to_openai("claude-5-opus") == model_manager.config.big_model
    assert model_manager.map_claude_model_to_openai("claude-opus-4.8") == model_manager.config.big_model
    assert model_manager.map_claude_model_to_openai("claude-opus-4") == model_manager.config.big_model
    assert model_manager.map_claude_model_to_openai("claude-4.5-opus") == model_manager.config.big_model
    assert model_manager.map_claude_model_to_openai("claude-mythos-5") == model_manager.config.big_model
    assert model_manager.map_claude_model_to_openai("claude-fable-5") == model_manager.config.big_model

    assert model_manager.map_claude_model_to_openai("claude-sonnet-5") == model_manager.config.middle_model
    assert model_manager.map_claude_model_to_openai("claude-5-sonnet") == model_manager.config.middle_model
    assert model_manager.map_claude_model_to_openai("claude-sonnet-4") == model_manager.config.middle_model
    assert model_manager.map_claude_model_to_openai("claude-4.5-sonnet") == model_manager.config.middle_model
    assert model_manager.map_claude_model_to_openai("claude-3-7-sonnet-20250219") == model_manager.config.middle_model

    assert model_manager.map_claude_model_to_openai("claude-haiku-4.5") == model_manager.config.small_model
    assert model_manager.map_claude_model_to_openai("claude-4.5-haiku") == model_manager.config.small_model
    assert model_manager.map_claude_model_to_openai("claude-haiku-4") == model_manager.config.small_model
    assert model_manager.map_claude_model_to_openai("claude-3-5-haiku-20241022") == model_manager.config.small_model

