#!/usr/bin/env python3
"""校验一份电力图像标注1.2 JSON。

该校验器故意不要求2.0的候选披露轨迹。它检查1.2真正稳定的训练门槛：
版本、嵌套结构、代码台账、对象编号、缺陷载体和视觉证据。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


SKILL_ROOT = Path(__file__).resolve().parent.parent
TREE_PATH = SKILL_ROOT / "references" / "标签候选树1.2.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def collect_tree_codes(tree: dict[str, Any]) -> set[str]:
    """同时收集对象、缺陷和路由代码，不把中文展示名当稳定主键。"""
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
    """沿设施、设备、部件的嵌套数组遍历，保留权威包含关系。"""
    for node in objects:
        yield node
        for key in ("设备实例", "部件实例"):
            children = node.get(key, [])
            if isinstance(children, list):
                yield from iter_objects(children)


def add_problem(
    problems: list[dict[str, str]], level: str, location: str, message: str
) -> None:
    problems.append({"级别": level, "位置": location, "问题": message})


def validate(data: dict[str, Any], known_codes: set[str]) -> dict[str, Any]:
    problems: list[dict[str, str]] = []
    contains_other = False

    if data.get("标注规范版本") != "电力设备多模态标注-v1.2":
        add_problem(problems, "阻断", "标注规范版本", "必须使用v1.2模板")
    if data.get("标签台账版本") != "1.2":
        add_problem(problems, "阻断", "标签台账版本", "必须使用1.2候选树")

    # 1.2的权威对象必须嵌套在标注对象树，禁止恢复为互相并列的顶层数组。
    for forbidden in ("设施实例", "设备实例", "部件实例", "缺陷实例"):
        if forbidden in data:
            add_problem(problems, "阻断", forbidden, "不允许作为顶层并列数组")

    objects = data.get("标注对象树")
    if not isinstance(objects, list) or not objects:
        add_problem(problems, "阻断", "标注对象树", "缺失或为空")
        objects = []

    # 路由与主要主题同样使用代码台账，避免只校验对象树而漏掉自由造词。
    route = data.get("图像路由", {})
    for field in ("业务域", "设施场景"):
        value = route.get(field, {}) if isinstance(route, dict) else {}
        code = value.get("代码") if isinstance(value, dict) else None
        if code and code not in known_codes:
            add_problem(problems, "阻断", f"图像路由.{field}", f"候选树不存在代码{code}")

    theme_code = data.get("主要对象主题", {}).get("标签代码")
    if theme_code and theme_code not in known_codes:
        add_problem(problems, "阻断", "主要对象主题", f"候选树不存在代码{theme_code}")

    object_ids: set[str] = set()
    defect_ids: set[str] = set()
    for node in iter_objects(objects):
        object_id = node.get("对象编号")
        if not object_id:
            add_problem(problems, "阻断", "标注对象树", "对象缺少对象编号")
        elif object_id in object_ids:
            add_problem(problems, "阻断", object_id, "对象编号重复")
        else:
            object_ids.add(object_id)

        label = node.get("标签", {})
        code = label.get("代码") if isinstance(label, dict) else None
        if not code:
            add_problem(problems, "阻断", object_id or "未知对象", "对象缺少标签代码")
        elif code not in known_codes:
            add_problem(problems, "阻断", object_id or "未知对象", f"候选树不存在代码{code}")
        elif code == "OTHER_REVIEW":
            contains_other = True
            add_problem(problems, "警告", object_id or "未知对象", "使用候选外对象，不能直接进入训练")

        for defect in node.get("缺陷实例", []):
            defect_id = defect.get("缺陷编号")
            if not defect_id:
                add_problem(problems, "阻断", object_id or "未知对象", "缺陷缺少缺陷编号")
            elif defect_id in defect_ids:
                add_problem(problems, "阻断", defect_id, "缺陷编号重复")
            else:
                defect_ids.add(defect_id)

            category = defect.get("缺陷类别", {})
            defect_code = category.get("代码") if isinstance(category, dict) else None
            if not defect_code or defect_code not in known_codes:
                add_problem(problems, "阻断", defect_id or "未知缺陷", f"候选树不存在代码{defect_code}")
            elif defect_code == "OTHER_REVIEW":
                contains_other = True
                add_problem(problems, "警告", defect_id or "未知缺陷", "使用候选外缺陷，不能直接进入训练")

    evidence_targets = {
        item.get("对应对象编号")
        for item in data.get("视觉证据", [])
        if isinstance(item, dict) and item.get("可见事实")
    }
    for defect_id in defect_ids:
        if defect_id not in evidence_targets:
            add_problem(problems, "阻断", defect_id, "缺少非空的直接视觉证据")

    blockers = sum(item["级别"] == "阻断" for item in problems)
    warnings = sum(item["级别"] == "警告" for item in problems)
    return {
        "规则版本": "1.2",
        "总体结论": "通过" if blockers == 0 else "需要修改",
        "阻断问题数量": blockers,
        "警告问题数量": warnings,
        "包含候选外标签": contains_other,
        "允许进入训练前置条件": blockers == 0 and not contains_other,
        "问题列表": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="校验电力图像标注1.2 JSON")
    parser.add_argument("annotation", type=Path, help="待校验JSON路径")
    args = parser.parse_args()

    try:
        tree = load_json(TREE_PATH)
        annotation = load_json(args.annotation)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"总体结论": "无法校验", "错误": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    report = validate(annotation, collect_tree_codes(tree))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["阻断问题数量"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
