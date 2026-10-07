import logging
from datetime import datetime

from utils.path_tool import get_abs_path
import os

# 日志保存的根目录

LOG_ROOT = get_abs_path("logs")

# 确保日志目录存在
# exist_ok=False：如果文件夹已存在，Python 会抛出 FileExistsError 异常，程序直接终止。
# exist_ok=True：如果文件夹已存在，什么都不做，直接跳过，继续执行后面的代码；如果不存在，则创建它。
os.makedirs(LOG_ROOT, exist_ok=True)

# 日志格式配置
DEFAULT_LOG_FORMAT = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s"
)

#NOTSET < DEBUG < INFO < WARNING < ERROR < CRITICAL
def get_logger(
        name:str = "agent",# 日志记录器的名字，通常按模块区分
        console_level:int = logging.INFO,# 屏幕输出级别（默认只显示 INFO 及以上）
        file_level:int = logging.DEBUG,# 文件写入级别（默认记录最详细的 DEBUG 级别）
        log_file = None# 自定义日志文件路径（不传则自动生成）
)->logging.Logger:
    logger = logging.getLogger(name)#这是 Python 内置的工厂单例。相同 name 获取的是同一个 Logger 对象，保证了整个项目中对同一模块的日志配置是统一的。
    logger.setLevel(logging.DEBUG)#把“总闸门”调到最低的 DEBUG，这样后续 Handler（处理器）才能根据自己的级别（INFO 或 DEBUG）决定放行哪些日志。

    # 避免重复添加Handler
    if logger.handlers:
        return logger

    # 控制台Handler
    console_handler = logging.StreamHandler()# 控制台输出日志的处理器
    console_handler.setLevel(console_level)
    console_handler.setFormatter(DEFAULT_LOG_FORMAT)

    logger.addHandler(console_handler)

    # 文件Handler
    if not log_file: #配置日志存放路径
        log_file = os.path.join(LOG_ROOT, f"{name}_{datetime.now().strftime('%Y%m%d')}.log")

    file_handler = logging.FileHandler(log_file,encoding="utf-8")
    file_handler.setLevel(file_level)
    file_handler.setFormatter(DEFAULT_LOG_FORMAT)

    logger.addHandler(file_handler)

    return logger

# 快捷获取日志器
logger = get_logger()