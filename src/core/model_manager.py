from src.core.config import config

class ModelManager:
    def __init__(self, config):
        self.config = config
    
    def map_claude_model_to_openai(self, claude_model: str) -> str:
        """Map Claude model names to target upstream model names based on BIG/MIDDLE/SMALL tiers, or pass through direct slugs."""
        if not claude_model:
            return self.config.big_model

        model_clean = claude_model.strip()
        model_lower = model_clean.lower()

        # Direct pass-through if model name is an explicit provider/open-source slug (e.g. gemini-3.7-flash, qwen3.8-max, deepseek-v4-pro, minimax-m3, kimi-k3, gpt-5.6-sol, gpt-5.6-terra, gpt-5.6-luna, muse-spark, muse-glimmer, glm-5.3, sonar, command-a, ox-alpha)
        if "/" in model_clean or model_lower.startswith((
            "gpt-", "o1", "o3", "o4", "chatgpt-", "llama", "muse", "qwen", "mistral", "codestral",
            "deepseek", "gemini", "glm", "minimax", "moonshot", "kimi", "ox", "0x",
            "command", "sonar", "phi-", "yi-", "gemma", "nemotron", "luna", "terra", "sol"
        )):
            return model_clean

        # Next-Gen & Modern Claude Tier Classification
        # 1. Haiku & Fast Speed tier (Claude Haiku 4.5, Claude 3.5 Haiku, Claude Haiku 4) -> SMALL_MODEL
        if any(keyword in model_lower for keyword in ("haiku", "claude-haiku", "claude-4.5-haiku", "claude-haiku-4.5", "claude-4-haiku", "claude-haiku-4", "claude-3-5-haiku", "claude-3.5-haiku")):
            return self.config.small_model

        # 2. Sonnet & Balanced Reasoning tier (Claude Sonnet 5, Claude Sonnet 4.x, Claude 3.7 Sonnet, Claude 3.5 Sonnet) -> MIDDLE_MODEL
        if any(keyword in model_lower for keyword in ("sonnet", "claude-sonnet", "claude-5-sonnet", "claude-sonnet-5", "claude-4-sonnet", "claude-sonnet-4", "claude-3-7", "claude-3.7", "claude-3-5", "claude-3.5")):
            return self.config.middle_model

        # 3. Opus & Frontier Flagship / Mythos tier (Claude Opus 5, Opus 4.8, Opus 4.x, Claude Fable 5, Claude Mythos 5) -> BIG_MODEL
        if any(keyword in model_lower for keyword in ("opus", "fable", "mythos", "claude-opus", "claude-5-opus", "claude-opus-5", "claude-4.8-opus", "claude-opus-4.8", "claude-4-opus", "claude-opus-4", "claude-4")):
            return self.config.big_model

        # Default fallback to BIG_MODEL
        return self.config.big_model



model_manager = ModelManager(config)