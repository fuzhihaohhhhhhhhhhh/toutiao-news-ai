"""
为整个项目添加绝对路径工具
"""
import os

def get_project_tool():
    """
    获取项目根目录
    :return: 项目根目录
    """
    # 获取当前文件所在绝对路径，__file__代表当前被执行脚本文件（.py）所在的路径。
    current_file = os.path.abspath(__file__)
    # 获取当前文件夹所在目录，即上一层目录
    current_dir = os.path.dirname(current_file)
    # 获取项目根目录
    project_root = os.path.dirname(current_dir)
    return project_root

def get_abs_path(relative_path:str):
    """
    传递相对路径，获取绝对路径
    :param relative_path: 相对路径
    :return: 绝对路径
    """
    # 获取项目根目录
    project_root = get_project_tool()
    # 获取绝对路径,os.path.join()函数用于将路径组件连接起来，返回一个绝对路径
    abs_path = os.path.join(project_root, relative_path)
    return abs_path

if __name__ == "__main__":
    print(get_project_tool())
    print(get_abs_path("config_handler.py"))
