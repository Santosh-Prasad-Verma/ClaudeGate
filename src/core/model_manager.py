from src.core.config import config

class ModelManager:
    def __init__(self, config):
        self.config = config
    
    def map_claude_model_to_openai(self, claude_model: str) -> str:
        """Map Claude model names to OpenAI model names based on BIG/MIDDLE/SMALL pattern, or pass through direct IDs."""
        if not claude_model:
            return self.config.big_model

        # Direct pass-through if model name looks like a specific OpenAI/OpenRouter/Groq/Ollama/Kiro slug
        if "/" in claude_model or claude_model.startswith((
            "gpt-", "o1-", "o3-", "llama", "qwen", "mistral", "deepseek", "gemini",
            "glm", "minimax", "claude-sonnet-4", "claude-haiku-4", "claude-opus-4"
        )):
            return claude_model
        
        # Map based on Claude model family tiers
        model_lower = claude_model.lower()
        if "haiku" in model_lower:
            return self.config.small_model
        elif "sonnet" in model_lower:
            return self.config.middle_model
        elif "opus" in model_lower:
            return self.config.big_model
        else:
            # Default to big model for standard/unrecognized Claude models
            return self.config.big_model

model_manager = ModelManager(config)