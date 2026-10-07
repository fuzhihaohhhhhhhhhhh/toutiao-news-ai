"""
统一的环境变量入口

作用：在任何配置模块读取环境变量之前，先把项目根目录下的 .env 加载进进程环境。

为什么单独抽一个模块：
    db_config / pg_config / cache_config 都需要读环境变量。如果每个文件各自
    load_dotenv 一次，一是重复，二是容易出现"还没加载就读了"的顺序问题。
    这里集中加载一次，其余模块直接 import 本模块的 env() / env_int() 即可。

安全约定：
    .env **不进入版本库**（见项目根目录 .gitignore），仓库里只保留 .env.example
    作为模板。因此本模块所有默认值都是**非敏感**的占位值（主机、端口、库名、
    用户名），密码类一律默认空字符串，真实密码只存在于本地 .env。
"""
import os

from dotenv import load_dotenv

from utils.path_tool import get_abs_path

# override=False（默认）：已存在的真实环境变量优先于 .env 文件，
# 这样容器/CI 里用环境变量注入时不会被 .env 覆盖。
load_dotenv(get_abs_path(".env"))


def env(key: str, default: str = "") -> str:
    """
    读取环境变量。

    :param key: 环境变量名
    :param default: 未配置时返回的默认值（必须是**非敏感**值）
    """
    value = os.getenv(key)
    # 把"未设置"和"设成空串"一视同仁，统一回退到默认值
    return value if value not in (None, "") else default


def env_int(key: str, default: int) -> int:
    """
    读取整型环境变量。值非法时回退默认值，避免因为一个错的端口号导致启动即崩。

    :param key: 环境变量名
    :param default: 未配置或解析失败时返回的默认值
    """
    try:
        return int(env(key, str(default)))
    except (TypeError, ValueError):
        return default
