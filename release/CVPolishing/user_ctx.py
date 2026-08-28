# -*- coding: utf-8 -*-
"""用户上下文：把当前登录用户名放入线程本地变量，供各存储模块按用户隔离。

用户名由 app.before_request 从服务端会话（Flask session）取出后写入；
- 长期记忆、简历：存 MySQL，按 user_id 列隔离（见 db.py / memory.py）；
- 文件型数据（迭代任务 state_recorder、链路追踪 traces）：按 user_data_dir 分目录隔离。

全局配置（skills.json、prompts.json）保持共享，不做用户隔离。
"""
import os
import threading

from config import BASE_DIR

_DATA_DIR = os.path.join(BASE_DIR, "data")
DEFAULT_USER = "default"

_local = threading.local()


def set_user_id(uid):
    """在当前请求线程中设置用户标识（由 app.before_request 调用）。"""
    _local.uid = uid or DEFAULT_USER


def get_user_id():
    """取当前请求线程的用户标识，未设置时回退 default。"""
    return getattr(_local, "uid", DEFAULT_USER)


def user_data_dir(uid=None):
    """返回当前用户的数据根目录。

    default / 空 -> data/（根，兼容老数据）
    其他        -> data/users/<uid>/（自动创建）
    """
    if uid is None:
        uid = get_user_id()
    if not uid or uid == DEFAULT_USER:
        return _DATA_DIR
    d = os.path.join(_DATA_DIR, "users", uid)
    os.makedirs(d, exist_ok=True)
    return d
