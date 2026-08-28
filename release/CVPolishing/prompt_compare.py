# -*- coding: utf-8 -*-
"""优化对比智能体提示词：对比「优化前简历」与「优化后简历」的差异、提升点与风险点。

系统提示词集中管理在 prompt_config（key=compare_system），支持前端可视化编辑、运行期实时生效。
"""


from prompt_config import load_prompts


def get_system_prompt():
    """运行时读取当前生效的优化对比系统提示词（含前端覆盖）。"""
    return load_prompts()["compare_system"]


def build_user_prompt(jd, before, after):
    """拼装优化对比用户提示词：JD + 优化前 + 优化后。"""
    return f"""# 目标岗位 JD
{jd.strip()}

# 优化前简历
{before.strip()}

# 优化后简历
{after.strip()}

# 任务
请基于以上 JD，对比「优化后简历」相对「优化前简历」的改动，按系统提示词要求输出三段内容：
1. 主要改动与提升；2. 仍待改进 / 风险点；3. 总体结论。"""
