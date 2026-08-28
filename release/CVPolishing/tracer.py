# -*- coding: utf-8 -*-
"""链路追踪：记录多 Agent 协作流程的每一步（输入/输出摘要、耗时、状态）。

按用户隔离：每个用户的链路日志落在 data/users/<uid>/traces/（default 用户落在 data/traces）。

- 本地：每次运行写入 <用户目录>/traces/<run_id>.jsonl，供前端可视化量化。
- LangSmith：若配置了 LANGSMITH_API_KEY，则把每个步骤同步上报到 LangSmith 项目，
  形成完整的链路调用日志（run tree）。未配置时仅本地落盘，不影响功能。

设计原则：本地 JSONL 是可视化的真实数据源（不是兜底），LangSmith 上报为可选项。
"""
import json
import os
import time
import uuid

import user_ctx
from config import BASE_DIR


def trace_dir():
    """当前用户的链路日志目录。"""
    return os.path.join(user_ctx.user_data_dir(), "traces")


def _now_ms():
    return int(time.time() * 1000)


class Tracer:
    """一次多 Agent 运行的链路记录器。"""

    def __init__(self, name="cvpolish"):
        self.run_id = uuid.uuid4().hex
        self.name = name
        self.start_ms = _now_ms()
        self.steps = []
        os.makedirs(trace_dir(), exist_ok=True)
        self.path = os.path.join(trace_dir(), f"{self.run_id}.jsonl")
        self._ls_client = None
        self.langsmith_enabled = False
        self._init_langsmith()
        self._write({"event": "run_start", "run_id": self.run_id,
                     "name": name, "ts": self.start_ms})

    def _init_langsmith(self):
        try:
            from langsmith import Client
            api_key = os.environ.get("LANGSMITH_API_KEY")
            if api_key:
                self._ls_client = Client(
                    api_key=api_key,
                    api_url=os.environ.get("LANGSMITH_URL",
                                           "https://api.smith.langchain.com"),
                )
                self.langsmith_enabled = True
        except Exception:
            self.langsmith_enabled = False

    def _write(self, obj):
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def step(self, name, input_text="", output_text="", status="ok", extra=None):
        """记录一步 Agent 调用。"""
        rec = {
            "event": "step",
            "name": name,
            "ts": _now_ms(),
            "input_len": len(input_text or ""),
            "output_len": len(output_text or ""),
            "status": status,
        }
        if extra:
            rec["extra"] = extra
        self.steps.append(rec)
        self._write(rec)
        if self.langsmith_enabled:
            self._push_langsmith(name, input_text, output_text, status, extra)
        return rec

    def finish(self, status="ok", extra=None):
        """结束本次运行并记录汇总。"""
        end = _now_ms()
        rec = {"event": "run_end", "run_id": self.run_id, "status": status,
               "ts": end, "duration_ms": end - self.start_ms,
               "steps": len(self.steps)}
        if extra:
            rec["extra"] = extra
        self._write(rec)
        return rec

    def to_dict(self):
        end = _now_ms()
        return {
            "run_id": self.run_id,
            "name": self.name,
            "status": "ok",
            "duration_ms": end - self.start_ms,
            "steps": self.steps,
            "langsmith": self.langsmith_enabled,
        }

    def _push_langsmith(self, name, input_text, output_text, status, extra):
        """尽力把步骤上报到 LangSmith；任何异常都静默忽略，不影响主流程。"""
        try:
            self._ls_client.create_run(
                id=str(uuid.uuid4()),
                name=name,
                run_type="tool",
                project_name=os.environ.get("LANGSMITH_PROJECT", "cvpolish"),
                inputs={"text": (input_text or "")[:4000]},
                outputs={"text": (output_text or "")[:4000], "status": status},
            )
        except Exception:
            pass


def read_trace(run_id):
    """读取某次链路日志（解析 JSONL），返回 {run_id, summary, steps}（仅当前用户）。"""
    path = os.path.join(trace_dir(), f"{run_id}.jsonl")
    if not os.path.exists(path):
        return None
    steps = []
    summary = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("event") == "run_end":
                summary = obj
            else:
                steps.append(obj)
    return {"run_id": run_id, "summary": summary, "steps": steps}
