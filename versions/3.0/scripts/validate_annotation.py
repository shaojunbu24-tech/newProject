#!/usr/bin/env python3
"""对3.0审计版或内容版执行模式感知的确定性校验。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
_skill_tree = ROOT / "references" / "标签候选树3.0.json"
_source_tree = ROOT / "标签候选树3.0.json"
TREE_PATH = _skill_tree if _skill_tree.exists() else _source_tree

STRICT = "STRICT_PACKAGE"
REFERENCE = "REFERENCE_PACKAGE"
REFERENCE_SOURCES = {"候选树参考扩展", "人工确认扩展"}
PROCESS_KEYS = {
    "标注模式",
    "输出视图",
    "标注流程状态",
    "知识层任务状态",
    "自动校验结果",
    "人工审核",
    "候选树路径",
    "候选披露轨迹编号",
    "候选代码",
    "选择代码",
    "候选快照摘要",
    "轨迹编号",
    "标签来源",
    "候选树映射",
    "激活标签包",
}


def walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_codes(tree: dict[str, Any]) -> set[str]:
    """收集路由、对象、部件、缺陷和控制代码。"""
    codes: set[str] = set()
    for node in walk(tree):
        if not isinstance(node, dict):
            continue
        for key in ("代码", "业务域代码", "设施场景代码"):
            value = node.get(key)
            if isinstance(value, str) and value:
                codes.add(value)
    fallback = tree.get("标签使用规则", {}).get("候选外处理代码")
    if isinstance(fallback, str):
        codes.add(fallback)
    return codes


def iter_objects(objects: list[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for node in objects:
        yield node
        for key in ("设备实例", "部件实例"):
            children = node.get(key, [])
            if isinstance(children, list):
                yield from iter_objects(children)


def problem(
    problems: list[dict[str, str]], level: str, location: str, message: str
) -> None:
    problems.append({"级别": level, "位置": location, "问题": message})


def trace_index(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    traces = data.get("标注流程状态", {}).get("候选披露轨迹", [])
    return {
        item.get("轨迹编号"): item
        for item in traces
        if isinstance(item, dict) and item.get("轨迹编号")
    }


def check_strict_label(
    *,
    label: dict[str, Any],
    trace_id: Any,
    location: str,
    known_codes: set[str],
    traces: dict[str, dict[str, Any]],
    problems: list[dict[str, str]],
) -> tuple[bool, bool]:
    """返回(是否使用OTHER_REVIEW, 是否为开放标签)。"""
    code = label.get("代码")
    name = label.get("中文名称")
    if not code or not name:
        problem(problems, "阻断", location, "严格模式的标签代码和中文名称均必填")
        return False, False
    if code not in known_codes:
        problem(problems, "阻断", location, f"代码{code}不在3.0标签树")
    if not trace_id or trace_id not in traces:
        problem(problems, "阻断", location, "严格模式缺少有效候选披露轨迹编号")
    elif code not in traces[trace_id].get("选择代码", []):
        problem(problems, "阻断", location, f"代码{code}不在轨迹{trace_id}的选择代码中")
    if code == "OTHER_REVIEW":
        problem(problems, "警告", location, "OTHER_REVIEW不能进入受控标签训练")
        return True, False
    return False, False


def check_reference_label(
    *,
    label: dict[str, Any],
    location: str,
    known_codes: set[str],
    problems: list[dict[str, str]],
) -> tuple[bool, bool]:
    """参考模式允许中文开放标签，但要求映射和复核状态可追溯。"""
    code = label.get("代码")
    name = label.get("中文名称")
    if not name:
        problem(problems, "阻断", location, "参考模式至少需要中文名称")
        return False, False

    if code in known_codes and code != "OTHER_REVIEW":
        return False, False
    if code == "OTHER_REVIEW":
        problem(problems, "警告", location, "参考模式优先保留开放中文标签，不建议以OTHER_REVIEW替代")
        return True, False
    if code:
        problem(problems, "阻断", location, "候选树外标签的代码必须留空，防止生成伪稳定代码")

    source = label.get("标签来源")
    mapping = label.get("候选树映射")
    if source not in REFERENCE_SOURCES:
        problem(problems, "阻断", location, "开放标签必须注明候选树参考扩展或人工确认扩展")
    if not isinstance(mapping, dict):
        problem(problems, "阻断", location, "开放标签缺少候选树映射")
        return False, True

    state = mapping.get("映射状态")
    review = mapping.get("开放标签复核状态")
    if state not in {"近似匹配", "未匹配"}:
        problem(problems, "阻断", location, "开放标签映射状态必须是近似匹配或未匹配")
    if review not in {"待审核", "已批准", "已拒绝"}:
        problem(problems, "阻断", location, "开放标签必须记录复核状态")
    if state == "近似匹配" and not mapping.get("最近候选代码"):
        problem(problems, "阻断", location, "近似匹配必须给出最近候选代码")
    if review != "已批准":
        problem(problems, "警告", location, "开放标签尚未批准，只能进入候选池")
    return False, True


def check_evidence(
    data: dict[str, Any], defect_ids: set[str], problems: list[dict[str, str]]
) -> None:
    evidence_ids = {
        item.get("证据编号")
        for item in data.get("视觉证据", [])
        if isinstance(item, dict) and item.get("证据编号") and item.get("可见事实")
    }
    global_evidence = {
        item.get("对应对象编号")
        for item in data.get("视觉证据", [])
        if isinstance(item, dict) and (item.get("可见事实") or item.get("直接可见证据"))
    }
    inline_evidence: set[str] = set()
    for node in iter_objects(data.get("标注对象树", [])):
        for defect in node.get("缺陷实例", []):
            if defect.get("直接可见证据"):
                inline_evidence.add(defect.get("缺陷编号"))
            referenced = set(defect.get("直接可见证据编号", []))
            if referenced and referenced.issubset(evidence_ids):
                inline_evidence.add(defect.get("缺陷编号"))
    for defect_id in defect_ids:
        if defect_id not in global_evidence and defect_id not in inline_evidence:
            problem(problems, "阻断", defect_id, "缺陷缺少直接可见证据")


def validate_audit(data: dict[str, Any], known_codes: set[str]) -> dict[str, Any]:
    problems: list[dict[str, str]] = []
    mode = data.get("标注模式", {}).get("模式代码")
    contains_other = False
    contains_open = False

    if data.get("标注规范版本") != "电力设备多模态标注-v3.0":
        problem(problems, "阻断", "标注规范版本", "必须使用3.0审计版")
    if data.get("标签台账版本") != "3.0":
        problem(problems, "阻断", "标签台账版本", "必须使用3.0标签树")
    if mode not in {STRICT, REFERENCE}:
        problem(problems, "阻断", "标注模式", "模式代码必须是STRICT_PACKAGE或REFERENCE_PACKAGE")

    traces = trace_index(data)
    if mode == STRICT and not traces:
        problem(problems, "阻断", "标注流程状态.候选披露轨迹", "严格模式轨迹不能为空")

    objects = data.get("标注对象树")
    if not isinstance(objects, list) or not objects:
        problem(problems, "阻断", "标注对象树", "缺失或为空")
        objects = []

    object_ids: set[str] = set()
    defect_ids: set[str] = set()
    for node in iter_objects(objects):
        object_id = node.get("对象编号")
        if not object_id:
            problem(problems, "阻断", "标注对象树", "对象缺少对象编号")
            object_id = "未知对象"
        elif object_id in object_ids:
            problem(problems, "阻断", object_id, "对象编号重复")
        object_ids.add(object_id)

        label = node.get("标签") if isinstance(node.get("标签"), dict) else {}
        trace_id = node.get("候选披露轨迹编号")
        if mode == STRICT:
            other, opened = check_strict_label(
                label=label, trace_id=trace_id, location=object_id,
                known_codes=known_codes, traces=traces, problems=problems,
            )
        else:
            other, opened = check_reference_label(
                label=label, location=object_id,
                known_codes=known_codes, problems=problems,
            )
        contains_other |= other
        contains_open |= opened

        for defect in node.get("缺陷实例", []):
            defect_id = defect.get("缺陷编号") or "未知缺陷"
            if defect_id in defect_ids:
                problem(problems, "阻断", defect_id, "缺陷编号重复")
            defect_ids.add(defect_id)
            category = defect.get("缺陷类别") if isinstance(defect.get("缺陷类别"), dict) else {}
            defect_trace = defect.get("候选披露轨迹编号")
            if mode == STRICT:
                other, opened = check_strict_label(
                    label=category, trace_id=defect_trace, location=defect_id,
                    known_codes=known_codes, traces=traces, problems=problems,
                )
            else:
                other, opened = check_reference_label(
                    label=category, location=defect_id,
                    known_codes=known_codes, problems=problems,
                )
            contains_other |= other
            contains_open |= opened

    check_evidence(data, defect_ids, problems)

    # 严格模式还检查每条轨迹本身的选择确实来自候选数组。
    if mode == STRICT:
        for trace_id, trace in traces.items():
            candidates = set(trace.get("候选代码", []))
            selected = set(trace.get("选择代码", []))
            if not selected.issubset(candidates):
                problem(problems, "阻断", trace_id, "选择代码不完全属于候选代码")

    blockers = sum(item["级别"] == "阻断" for item in problems)
    warnings = sum(item["级别"] == "警告" for item in problems)
    return {
        "视图": "审计版",
        "标注模式": mode,
        "总体结论": "通过" if blockers == 0 else "需要修改",
        "阻断问题数量": blockers,
        "警告问题数量": warnings,
        "包含OTHER_REVIEW": contains_other,
        "包含开放标签": contains_open,
        "允许导出内容版": blockers == 0,
        "允许进入受控训练": blockers == 0 and not contains_other and not contains_open,
        "允许进入开放词汇候选池": blockers == 0 and mode == REFERENCE,
        "问题列表": problems,
    }


def validate_content(data: dict[str, Any]) -> dict[str, Any]:
    problems: list[dict[str, str]] = []
    if data.get("标注规范版本") != "电力设备多模态标注-v3.0-content":
        problem(problems, "阻断", "标注规范版本", "必须使用3.0内容版")
    if not isinstance(data.get("标注对象树"), list) or not data.get("标注对象树"):
        problem(problems, "阻断", "标注对象树", "缺失或为空")
    for node in walk(data):
        if isinstance(node, dict):
            for key in node:
                if key in PROCESS_KEYS:
                    problem(problems, "阻断", key, "内容版包含审计过程字段")
    blockers = sum(item["级别"] == "阻断" for item in problems)
    return {
        "视图": "内容版",
        "总体结论": "通过" if blockers == 0 else "需要修改",
        "阻断问题数量": blockers,
        "警告问题数量": 0,
        "问题列表": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="校验电力图像标注3.0 JSON")
    parser.add_argument("annotation", type=Path)
    args = parser.parse_args()
    try:
        data = load_json(args.annotation)
        tree = load_json(TREE_PATH)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"总体结论": "无法校验", "错误": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    if data.get("标注规范版本") == "电力设备多模态标注-v3.0-content":
        report = validate_content(data)
    else:
        report = validate_audit(data, collect_codes(tree))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["阻断问题数量"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
