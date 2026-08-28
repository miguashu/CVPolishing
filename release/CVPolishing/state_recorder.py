# -*- coding: utf-8 -*-
"""状态记录器：记录智能迭代优化的每一步状态，支持断点续传。

按用户隔离：每个用户的任务落在 data/users/<uid>/state_recorder/（default 用户落在 data/state_recorder）。

设计：
- 每次 auto_optimize 启动生成唯一 task_id，保存到对应用户的 state_recorder 目录
- 每完成一个阶段（search/optimize/score/think/supervise）记录步骤输入输出与耗时
- 每完成一轮迭代后保存完整 checkpoint（current、iterations、prev_total、no_improve 等）
- 多次失败的同一步骤自动标记，供监督 Agent 判定是否该终止
- 任务重启时读取最近 checkpoint，从断点继续执行
"""
import json
import os
import time
import uuid

import user_ctx
from config import BASE_DIR

MAX_ERROR_RETRY = 3  # 同一步骤连续失败上限，超限后标记需终止


def _now_sec():
    return int(time.time())


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def state_dir():
    """当前用户的任务目录。"""
    return os.path.join(user_ctx.user_data_dir(), "state_recorder")


class StateRecorder:
    """一次自动优化任务的状态记录器。

    用法：
        sr = StateRecorder(task_id=None)  # 新建任务
        sr = StateRecorder(task_id="xxx") # 加载已有任务

        sr.start(resume, jd, version, enable_search, target_score, max_iter)
        sr.checkpoint(iteration, phase="optimize", current=resume_text, ...)
        sr.step(iteration, name, input_text, output_text, error=None)
        completed = sr.get_completed_state()
    """

    def __init__(self, task_id=None):
        os.makedirs(state_dir(), exist_ok=True)
        if task_id:
            self.task_id = task_id
            self._load()
        else:
            self.task_id = uuid.uuid4().hex[:12]
            self.data = self._empty()

    @property
    def path(self):
        return os.path.join(state_dir(), f"task_{self.task_id}.json")

    def _empty(self):
        return {
            "task_id": self.task_id,
            "status": "created",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "params": {},
            "checkpoint": None,
            "steps": [],
            "errors": [],
        }

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except Exception:
            self.data = self._empty()

    def _save(self):
        self.data["updated_at"] = _now_iso()
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def start(self, resume, jd, version="标准优化", enable_search=None,
              target_score=90, max_iter=5):
        """记录任务启动参数。"""
        self.data["status"] = "running"
        self.data["params"] = {
            "resume": resume,
            "jd": jd,
            "version": version,
            "enable_search": enable_search,
            "target_score": target_score,
            "max_iter": max_iter,
        }
        self.data["checkpoint"] = {
            "iteration": 0,
            "phase": "init",
            "current": resume,
            "iterations": [],
            "prev_total": -1,
            "no_improve": 0,
        }
        self._save()

    def checkpoint(self, iteration, phase="optimize", current="",
                   iterations=None, prev_total=-1, no_improve=0,
                   references="", ref_titles=None, job_title=""):
        """保存断点：调用方传当前完整运行状态，用于重启后从该断点继续。"""
        cp = {
            "iteration": iteration,
            "phase": phase,
            "current": current,
            "iterations": iterations or [],
            "prev_total": prev_total,
            "no_improve": no_improve,
            "references": references,
            "ref_titles": ref_titles or [],
            "job_title": job_title,
        }
        self.data["checkpoint"] = cp
        self._save()

    def step(self, iteration, name, input_text="", output_text="",
             status="ok", error=None, duration_ms=0, tokens=None):
        """记录单个步骤的执行详情（输入输出摘要、耗时、token、状态）。

        tokens: {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}
        duration_ms: 该步骤耗时（毫秒）
        """
        ts = _now_sec()
        rec = {
            "iteration": iteration,
            "name": name,
            "input_len": len(input_text or ""),
            "output_len": len(output_text or ""),
            "input_summary": (input_text or "")[:300],
            "output_summary": (output_text or "")[:300],
            "status": status,
            "ts": ts,
            "time": _now_iso(),
            "duration_ms": duration_ms,
            "tokens": tokens or {},
        }
        if error:
            rec["error"] = str(error)[:500]
        self.data["steps"].append(rec)
        self._save()

        # 检测是否同一步骤多次失败
        if status == "error":
            self._track_error(iteration, name, error)
        return rec

    def _track_error(self, iteration, name, error):
        """记录失败，统计同一步骤连续失败次数。"""
        err = {
            "iteration": iteration,
            "name": name,
            "error": str(error)[:500],
            "time": _now_iso(),
        }
        # 检查是否与上一条是同一个步骤
        prev = self.data["errors"][-1] if self.data["errors"] else None
        if prev and prev["name"] == name:
            err["consecutive"] = prev.get("consecutive", 1) + 1
        else:
            err["consecutive"] = 1
        self.data["errors"].append(err)
        self._save()

        if err["consecutive"] >= MAX_ERROR_RETRY:
            self.data["status"] = "error"
            self._save()

    @property
    def should_terminate(self):
        """同一步骤连续失败超过上限时返回 True。"""
        if self.data["errors"]:
            return self.data["errors"][-1].get("consecutive", 0) >= MAX_ERROR_RETRY
        return False

    @property
    def last_error_info(self):
        """最近一次失败信息（步骤名、错误信息、连续失败次数）。"""
        if self.data["errors"]:
            return self.data["errors"][-1]
        return None

    def get_completed_state(self):
        """返回已完成的运行状态，供 auto_optimize_stream 跳过多余步骤。

        返回 dict:
          - iteration: 已完成的迭代轮次（该轮全部步骤都完成）
          - phase: 最后完成的阶段名
          - current: 当前最优简历文本
          - iterations: 已完成的完整迭代数据列表
          - prev_total / no_improve: 防死循环状态
          - references / ref_titles / job_title: 搜索阶段产出
          - 若 task 是 completed 或 error 状态，额外标记
        """
        cp = self.data.get("checkpoint") or {}
        return {
            "checkpoint": cp,
            "status": self.data["status"],
            "steps_count": len(self.data.get("steps", [])),
            "errors_count": len(self.data.get("errors", [])),
        }

    def finish(self, status="completed"):
        """标记任务结束。"""
        self.data["status"] = status
        self._save()

    def to_dict(self):
        return self.data


def get_unfinished_tasks():
    """列出当前用户所有未完成的任务，按更新时间倒序。"""
    tasks = []
    try:
        for f in os.listdir(state_dir()):
            if not f.startswith("task_") or not f.endswith(".json"):
                continue
            path = os.path.join(state_dir(), f)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                continue
            if data.get("status") in ("running", "created"):
                tasks.append({
                    "task_id": data.get("task_id"),
                    "status": data.get("status"),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "params": data.get("params", {}),
                    "checkpoint": data.get("checkpoint"),
                    "errors": data.get("errors", []),
                })
    except Exception:
        pass
    tasks.sort(key=lambda t: t.get("updated_at", ""), reverse=True)
    return tasks


def get_task(task_id):
    """读取指定任务状态（仅当前用户）。"""
    path = os.path.join(state_dir(), f"task_{task_id}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def cleanup_old_tasks(keep_days=7):
    """清理 N 天前的已完成任务，避免磁盘堆积（仅当前用户）。"""
    cutoff = _now_sec() - keep_days * 86400
    try:
        for f in os.listdir(state_dir()):
            if not f.startswith("task_") or not f.endswith(".json"):
                continue
            path = os.path.join(state_dir(), f)
            try:
                mtime = os.path.getmtime(path)
                if mtime < cutoff:
                    os.remove(path)
            except Exception:
                pass
    except Exception:
        pass
