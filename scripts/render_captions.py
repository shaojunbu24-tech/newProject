#!/usr/bin/env python3
"""从审核通过的结构化标注派生中文描述和检索文本。

本脚本严格遵循“结构化事实先于文本”的原则：默认要求
``自动校验结果.最终门禁.允许生成派生输出``为true。脚本不会根据文件名猜测缺失
标签，也不会把不确定信息写成肯定结论。输出是独立JSON，可由调用方审核后写回
``多模态描述``；脚本本身不修改权威标注文件。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


def iter_nodes(
    nodes: Iterable[dict[str, Any]], ancestors: tuple[str, ...] = ()
) -> Iterable[tuple[dict[str, Any], tuple[str, ...]]]:
    """遍历对象树并携带中文祖先路径，用于生成可读的缺陷定位短语。"""

    for node in nodes:
        name = node.get("标签", {}).get("中文名称") or node.get("对象编号", "未知对象")
        path = ancestors + (name,)
        yield node, path
        yield from iter_nodes(node.get("设备实例", []), path)
        yield from iter_nodes(node.get("部件实例", []), path)


def flatten_attribute_values(raw: Any) -> list[str]:
    """从字典或属性对象数组中提取中文可读值，不输出内部代码。"""

    values: list[str] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                value = value.get("中文名称", value.get("代码"))
            values.append(f"{key}为{value}")
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = item.get("属性名称") or item.get("名称")
            value = item.get("中文名称", item.get("值", item.get("代码")))
            if key and value is not None:
                values.append(f"{key}为{value}")
    return values


def collect_external_facts(annotation: dict[str, Any]) -> list[str]:
    """生成带来源限定的业务信息，确保文件名内容不会混入纯视觉描述。"""

    facts: list[str] = []
    external = annotation.get("外部信息", {})
    for source_key in ("文件名解析", "设备台账信息", "工单信息"):
        for item in external.get(source_key, []):
            if not isinstance(item, dict):
                continue
            name = item.get("字段") or item.get("信息类型") or item.get("名称")
            value = item.get("值", item.get("原始值"))
            if name and value is not None:
                facts.append(f"据{source_key}，{name}为{value}")
    return facts


def render(annotation: dict[str, Any]) -> dict[str, Any]:
    """从对象、属性、缺陷和证据生成四类中文描述。"""

    nodes = list(iter_nodes(annotation.get("标注对象树", [])))
    main_id = annotation.get("主要对象主题", {}).get("对象编号")
    main_node = next(
        (node for node, _ in nodes if node.get("对象编号") == main_id),
        None,
    )
    main_name = (
        main_node.get("标签", {}).get("中文名称")
        if main_node
        else annotation.get("主要对象主题", {}).get("中文名称", "电力设备")
    )

    visible_objects: list[str] = []
    attributes: list[str] = []
    defect_phrases: list[str] = []
    uncertain: list[str] = []
    for node, path in nodes:
        name = node.get("标签", {}).get("中文名称")
        if name and name not in visible_objects:
            visible_objects.append(name)
        attributes.extend(flatten_attribute_values(node.get("属性", [])))
        for defect in node.get("缺陷实例", []):
            defect_name = defect.get("缺陷类别", {}).get("中文名称", "待复核异常")
            status = defect.get("判定状态")
            location = "的".join(path)
            if status in {"疑似缺陷", "可见异常但类别待复核", "不可判定"}:
                defect_phrases.append(f"{location}可见疑似{defect_name}")
            else:
                defect_phrases.append(f"{location}存在{defect_name}")
            uncertain.extend(defect.get("不可确认内容", []))

    route = annotation.get("图像路由", {})
    scene_name = route.get("设施场景", {}).get("中文名称")
    range_value = route.get("拍摄范围")
    range_name = (
        range_value.get("中文名称") if isinstance(range_value, dict) else range_value
    )

    subject = f"图像主体为{main_name}。"
    object_sentence = ""
    secondary = [name for name in visible_objects if name != main_name]
    if secondary:
        object_sentence = f"画面还可见{'、'.join(secondary)}。"
    attribute_sentence = f"可见属性包括{'、'.join(dict.fromkeys(attributes))}。" if attributes else ""
    defect_sentence = "；".join(defect_phrases) + "。" if defect_phrases else "未记录确认缺陷。"
    visual_description = subject + object_sentence + attribute_sentence + defect_sentence

    external_facts = collect_external_facts(annotation)
    business_prefix = ""
    if scene_name or range_name:
        business_prefix = f"该图路由为{scene_name or '场景待复核'}，拍摄范围为{range_name or '待复核'}。"
    business_description = business_prefix + visual_description
    if external_facts:
        business_description += "；".join(external_facts) + "。"

    short_retrieval = "-".join(
        part
        for part in (
            main_name,
            secondary[-1] if secondary else None,
            defect_phrases[0].split("存在")[-1] if defect_phrases else "无确认缺陷",
        )
        if part
    )

    return {
        "生成状态": "已生成待写回",
        "主体描述": subject.rstrip("。"),
        "属性描述": attribute_sentence.rstrip("。") if attribute_sentence else "无可可靠派生的静态属性",
        "纯视觉完整描述": visual_description,
        "业务完整描述": business_description,
        "简短检索描述": short_retrieval,
        "可见证据摘要": [
            item.get("可见事实")
            for item in annotation.get("视觉证据", [])
            if item.get("可见事实")
        ],
        "不确定项": list(dict.fromkeys(uncertain)),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("annotation", type=Path)
    parser.add_argument("--output", type=Path, help="可选的独立派生描述输出路径")
    parser.add_argument(
        "--force",
        action="store_true",
        help="仅用于调试；跳过最终门禁检查，生产标注禁止使用",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        annotation = json.loads(args.annotation.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取标注JSON {args.annotation}: {exc}") from exc

    gate = annotation.get("自动校验结果", {}).get("最终门禁", {})
    if not args.force and gate.get("允许生成派生输出") is not True:
        raise SystemExit("最终门禁尚未允许生成派生输出；请先完成规则校验和大模型语义复核")

    rendered = json.dumps(render(annotation), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
