import yaml
from utils.path_tool import get_abs_path

# 默认参数使用正斜杠，跨平台兼容（Python 在 Windows 上也接受正斜杠）
def load_rag_config(config_path: str = get_abs_path("config/rag.yml"), encoding: str = "utf-8"):
    with open(config_path, 'r', encoding=encoding) as f:
        return yaml.safe_load(f)

def load_agent_config(config_path: str = get_abs_path("config/agent.yml"), encoding: str = "utf-8"):
    with open(config_path, 'r', encoding=encoding) as f:
        return yaml.safe_load(f)

def load_chroma_config(config_path: str = get_abs_path("config/chroma.yml"), encoding: str = "utf-8"):
    with open(config_path, 'r', encoding=encoding) as f:
        return yaml.safe_load(f)

def load_prompts_config(config_path: str = get_abs_path("config/prompts.yml"), encoding: str = "utf-8"):
    with open(config_path, 'r', encoding=encoding) as f:
        return yaml.safe_load(f)

rag_conf = load_rag_config()
chroma_conf = load_chroma_config()
prompt_conf = load_prompts_config()
agent_conf = load_agent_config()
