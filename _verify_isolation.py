# -*- coding: utf-8 -*-
"""临时验证脚本：多用户隔离是否生效（测完删除）。"""
import os
import sys
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import user_ctx
import memory
import state_recorder
import tracer
import app as appmod

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
USERS = ("verify_A", "verify_B")


def reset_users():
    for u in USERS:
        d = os.path.join(ROOT, "users", u)
        if os.path.isdir(d):
            shutil.rmtree(d)


reset_users()

# 1. user_ctx 分目录
user_ctx.set_user_id("verify_A")
dA = user_ctx.user_data_dir()
user_ctx.set_user_id("verify_B")
dB = user_ctx.user_data_dir()
assert dA != dB, "dirs must differ"
assert dA.endswith("users/verify_A"), dA
assert dB.endswith("users/verify_B"), dB
print("[1] user_data_dir 分目录 OK")

# 2. memory 隔离
user_ctx.set_user_id("verify_A")
memory.put("A题", "A答案")
user_ctx.set_user_id("verify_B")
memory.put("B题", "B答案")
user_ctx.set_user_id("verify_A")
allA = memory.get_all()
assert "A题" in allA and "B题" not in allA, list(allA.keys())
user_ctx.set_user_id("verify_B")
allB = memory.get_all()
assert "B题" in allB and "A题" not in allB, list(allB.keys())
print("[2] memory 隔离 OK")

# 3. state_recorder 隔离
user_ctx.set_user_id("verify_A")
sr = state_recorder.StateRecorder()
sr.start("resumeA", "jdA")
task_id_A = sr.task_id
assert os.path.exists(sr.path) and "verify_A" in sr.path
user_ctx.set_user_id("verify_B")
tasksB = state_recorder.get_unfinished_tasks()
assert not any(t["task_id"] == task_id_A for t in tasksB), "B 不应看到 A 的任务"
srB = state_recorder.StateRecorder()
srB.start("resumeB", "jdB")
tasksB2 = state_recorder.get_unfinished_tasks()
assert any(t["task_id"] == srB.task_id for t in tasksB2)
print("[3] state_recorder 隔离 OK")

# 4. tracer 隔离
user_ctx.set_user_id("verify_A")
trA = tracer.Tracer("verify_test")
trA.step("opt", "in", "out")
assert os.path.exists(trA.path) and "verify_A" in trA.path
user_ctx.set_user_id("verify_B")
assert tracer.read_trace(trA.run_id) is None, "B 不应读到 A 的 trace"
print("[4] tracer 隔离 OK")

# 5. question_timestamps 隔离（app 内）
user_ctx.set_user_id("verify_A")
appmod._record_question("generate", "A的问题")
user_ctx.set_user_id("verify_B")
appmod._record_question("generate", "B的问题")
user_ctx.set_user_id("verify_A")
tsA = appmod._question_ts_path()
user_ctx.set_user_id("verify_B")
tsB = appmod._question_ts_path()
assert tsA != tsB
with open(tsA, "r", encoding="utf-8") as f:
    assert any("A的问题" in l for l in f.readlines())
with open(tsB, "r", encoding="utf-8") as f:
    assert any("B的问题" in l for l in f.readlines())
print("[5] question_timestamps 隔离 OK")

reset_users()
print("\nALL ISOLATION CHECKS PASSED")
