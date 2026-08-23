# Constants for better maintainability  
class Constants:
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLE_SYSTEM = "system"
    ROLE_TOOL = "tool"
    
    CONTENT_TEXT = "text"
    CONTENT_IMAGE = "image"
    CONTENT_TOOL_USE = "tool_use"
    CONTENT_TOOL_RESULT = "tool_result"
    
    TOOL_FUNCTION = "function"
    
    STOP_END_TURN = "end_turn"
    STOP_MAX_TOKENS = "max_tokens"
    STOP_TOOL_USE = "tool_use"
    STOP_ERROR = "error"
    
    EVENT_MESSAGE_START = "message_start"
    EVENT_MESSAGE_STOP = "message_stop"
    EVENT_MESSAGE_DELTA = "message_delta"
    EVENT_CONTENT_BLOCK_START = "content_block_start"
    EVENT_CONTENT_BLOCK_STOP = "content_block_stop"
    EVENT_CONTENT_BLOCK_DELTA = "content_block_delta"
    EVENT_PING = "ping"
    
    DELTA_TEXT = "text_delta"
    DELTA_INPUT_JSON = "input_json_delta"


BANNER = r"""
   _____ _                 _       _____       _       
  / ____| |               | |     / ____|     | |      
 | |    | | __ _ _   _  __| | ___| |  __  __ _| |_ ___ 
 | |    | |/ _` | | | |/ _` |/ _ \ | |_ |/ _` | __/ _ \
 | |____| | (_| | |_| | (_| |  __/ |__| | (_| | ||  __/
  \_____|_|\__,_|\__,_|\__,_|\___|\_____|\__,_|\__\___|
                                                       
  🔓 Connect Any AI Model to Claude Code CLI / Anthropic SDK
"""