import hashlib
import os
from utils.logger_handler import logger


def get_file_md5_hex(filepath):# 计算文件md5值（返回16进制字符串表示）传入的是文件路径
    if not os.path.exists(filepath):#检查你传入的 filepath（字符串路径）在当前的硬盘上是否真实存在（无论是文件还是文件夹，只要存在就返回 True）
        logger.error(f"[md5计算]文件{filepath}不存在")
        return
    if not os.path.isfile(filepath):
        logger.error(f"[md5计算]路径{filepath}不是文件")
        return

    md5_obj = hashlib.md5()# 创建一个 MD5 对象，用于计算文件的 MD5 值
    chunk_size = 4096# 分片，避免文件太大
    try:
        with open(filepath,'rb') as f:# 'rb' 以二进制模式打开文件
            while chunk := f.read(chunk_size):
                md5_obj.update(chunk)
            """
            chunk := f.read(chunk_size)从当前文件指针位置开始，读取指定大小（chunk_size，即 4096 字节）的数据。
            while chunk:
                md5_obj.update(chunk)
                chunk = f.read(chunk_size)
            """
            md5_hex = md5_obj.hexdigest()#在所有数据都通过 update() 喂完后，调用此方法结束计算，返回最终的 MD5 值
            return md5_hex
    except Exception as e:
        logger.error(f"[md5计算]文件{filepath}读取失败:{e}")
        return None


def get_text_md5_hex(text: str) -> str:
    # 计算字符串的 md5 值（返回16进制字符串）
    return hashlib.md5(text.encode("utf-8")).hexdigest()