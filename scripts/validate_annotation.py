#!/usr/bin/env python3
"""对单张2.0标注执行阶段四A确定性规则校验。

校验器只报告可确定计算的错误，不判断图像中的设备和缺陷是否真的存在。只有本脚本
不存在“阻断”问题时，Skill才应继续执行大模型语义复核。默认以新版渐进式模板为
准；``--allow-legacy``仅用于读取没有候选轨迹的存量示例，会把新增字段缺失降级为
警告，绝不能用来放宽新训练数据的入口门禁。

脚本不会修改输入标注。结果以结构化JSON写到标准输出，或通过``--report``写入独立
报告文件，避免静默改变权威事实源。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

# 复用候选选择器中的确定性映射逻辑，避免“标注时允许、审核时禁止”的规则漂移。
from select_candidates import (
    DEFAULT_LEDGER,
    build_definition_index,
    defect_candidates,
    find_scene,
    load_ledger,
    part_candidates,
)


RULE_CHECKS = [
    "JSON结构校验",
    "标签台账校验",
    "渐进披露轨迹校验",
    "候选树路径校验",
    "对象树关系校验",
    "载体缺陷兼容校验",
    "静态属性约束校验",
    "来源和证据形式校验",
    "任务完整性校验",
]
STAGE_ORDER = ["ROUTE", "SUBJECT", "DEFECT", "VALIDATE", "DERIVE"]


class Validator:
    """保存校验上下文和问题列表，提供统一的结构化问题输出。"""

    def __init__(
        self,
        annotation: dict[str, Any],
        ledger: dict[str, Any],
        allow_legacy: bool,
    ) -> None:
        self.annotation = annotation
        self.ledger = ledger
        self.allow_legacy = allow_legacy
        self.issues: list[dict[str, Any]] = []
        self.index: dict[str, dict[str, Any]] = {}
        build_definition_index(ledger, self.index)
        self.defect_codes = {item["代码"] for item in ledger["缺陷代码表"]}
        self.object_ids: set[str] = set()
        self.defect_ids: set[str] = set()
        self.traces_by_id: dict[str, dict[str, Any]] = {}

    def add_issue(
        self,
        category: str,
        level: str,
        location: str,
        message: str,
        *,
        rule_id: str | None = None,
        actual: Any = None,
        expected: Any = None,
        suggestion: str | None = None,
        return_stage: str | None = None,
    ) -> None:
        """追加一个固定字段的问题对象，便于Skill直接写入规则校验报告。"""

        issue = {
            "规则编号": rule_id or f"AUTO-{category}",
            "校验类别": category,
            "问题级别": level,
            "问题位置": location,
            "问题说明": message,
            "实际内容": actual,
            "期望要求": expected,
            "处理建议": suggestion,
            "退回阶段": return_stage,
        }
        self.issues.append(issue)

    def legacy_level(self, strict_level: str = "阻断") -> str:
        """只对新增元数据缺失降级；标签、路径和载体错误永远保持阻断。"""

        return "警告" if self.allow_legacy else strict_level

    @staticmethod
    def attribute_dict(raw: Any) -> dict[str, Any]:
        """兼容字典和对象数组两种属性表示，返回供约束匹配的简洁键值表。"""

        if isinstance(raw, dict):
            return dict(raw)
        result: dict[str, Any] = {}
        if not isinstance(raw, list):
            return result
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = item.get("属性名称") or item.get("名称") or item.get("属性")
            value = item.get("代码", item.get("值"))
            if isinstance(value, dict):
                value = value.get("代码", value.get("中文名称"))
            if isinstance(key, str) and value is not None:
                result[key] = value
        return result

    @staticmethod
    def iter_nodes(
        nodes: Iterable[dict[str, Any]],
        *,
        parent: dict[str, Any] | None = None,
        relation: str | None = None,
        inherited_attributes: dict[str, Any] | None = None,
        path: str = "标注对象树",
    ) -> Iterable[
        tuple[
            dict[str, Any],
            dict[str, Any] | None,
            str | None,
            dict[str, Any],
            str,
        ]
    ]:
        """深度遍历对象树，同时携带父关系、继承属性和可读JSON路径。"""

        inherited = dict(inherited_attributes or {})
        for position, node in enumerate(nodes):
            node_path = f"{path}/{position}"
            attributes = dict(inherited)
            attributes.update(Validator.attribute_dict(node.get("属性", [])))
            yield node, parent, relation, attributes, node_path
            yield from Validator.iter_nodes(
                node.get("设备实例", []),
                parent=node,
                relation="设备实例",
                inherited_attributes=attributes,
                path=f"{node_path}/设备实例",
            )
            yield from Validator.iter_nodes(
                node.get("部件实例", []),
                parent=node,
                relation="部件实例",
                inherited_attributes=attributes,
                path=f"{node_path}/部件实例",
            )

    def validate_structure(self) -> None:
        """检查顶层结构、路由字段、坐标和基础对象字段。"""

        required_top = {
            "图像基本信息",
            "图像路由",
            "主要对象主题",
            "标注对象树",
            "视觉证据",
        }
        if not self.allow_legacy:
            required_top.add("标注流程状态")
        for key in sorted(required_top - set(self.annotation)):
            self.add_issue(
                "JSON结构校验",
                self.legacy_level(),
                "/",
                f"缺少顶层字段{key}",
                expected=key,
                return_stage="ROUTE",
            )

        route = self.annotation.get("图像路由", {})
        for key in ("业务域", "设施场景", "拍摄任务", "拍摄范围"):
            if key not in route:
                self.add_issue(
                    "JSON结构校验",
                    "阻断",
                    "图像路由",
                    f"缺少路由字段{key}",
                    return_stage="ROUTE",
                )

        for node, _, _, _, path in self.iter_nodes(
            self.annotation.get("标注对象树", [])
        ):
            for key in ("对象类型", "对象编号", "标签", "候选树路径"):
                if key not in node:
                    self.add_issue(
                        "JSON结构校验",
                        self.legacy_level() if key == "候选树路径" else "阻断",
                        path,
                        f"对象缺少字段{key}",
                        return_stage="SUBJECT",
                    )
            bbox = node.get("边界框_归一化坐标")
            if bbox is not None and (
                not isinstance(bbox, list)
                or len(bbox) != 4
                or any(not isinstance(value, (int, float)) or not 0 <= value <= 1000 for value in bbox)
            ):
                self.add_issue(
                    "JSON结构校验",
                    "阻断",
                    f"{path}/边界框_归一化坐标",
                    "归一化边界框必须是0到1000范围内的四个数值",
                    actual=bbox,
                    return_stage="SUBJECT",
                )

    def validate_ledger_labels(self) -> None:
        """检查对象和缺陷代码存在、名称匹配及台账版本一致。"""

        expected_version = self.ledger["标签台账版本"]
        for node, _, _, _, path in self.iter_nodes(
            self.annotation.get("标注对象树", [])
        ):
            label = node.get("标签", {})
            code = label.get("代码")
            definition = self.index.get(code)
            if not definition:
                self.add_issue(
                    "标签台账校验",
                    "阻断",
                    f"{path}/标签",
                    "对象代码不在当前标签台账",
                    actual=code,
                    suggestion="使用本轮候选或OTHER_REVIEW",
                    return_stage="SUBJECT",
                )
            elif label.get("中文名称") != definition.get("中文名称"):
                self.add_issue(
                    "标签台账校验",
                    "阻断",
                    f"{path}/标签/中文名称",
                    "代码与中文名称不匹配",
                    actual=label.get("中文名称"),
                    expected=definition.get("中文名称"),
                    return_stage="SUBJECT",
                )
            if label.get("台账版本") not in (None, expected_version):
                self.add_issue(
                    "标签台账校验",
                    "阻断",
                    f"{path}/标签/台账版本",
                    "对象使用了不同版本的标签台账",
                    actual=label.get("台账版本"),
                    expected=expected_version,
                    return_stage="SUBJECT",
                )

            for defect_position, defect in enumerate(node.get("缺陷实例", [])):
                defect_path = f"{path}/缺陷实例/{defect_position}"
                defect_label = defect.get("缺陷类别", {})
                defect_code = defect_label.get("代码")
                definition = self.index.get(defect_code)
                if defect_code not in self.defect_codes or not definition:
                    self.add_issue(
                        "标签台账校验",
                        "阻断",
                        f"{defect_path}/缺陷类别",
                        "缺陷代码不在当前缺陷台账",
                        actual=defect_code,
                        return_stage="DEFECT",
                    )
                elif defect_label.get("中文名称") != definition.get("中文名称"):
                    self.add_issue(
                        "标签台账校验",
                        "阻断",
                        f"{defect_path}/缺陷类别/中文名称",
                        "缺陷代码与中文名称不匹配",
                        actual=defect_label.get("中文名称"),
                        expected=definition.get("中文名称"),
                        return_stage="DEFECT",
                    )

    def validate_traces(self) -> set[str]:
        """校验候选快照摘要和“选择必须属于候选”约束。"""

        flow = self.annotation.get("标注流程状态", {})
        traces = flow.get("候选披露轨迹", [])
        selected_codes: set[str] = set()
        if not traces:
            self.add_issue(
                "渐进披露轨迹校验",
                self.legacy_level(),
                "标注流程状态/候选披露轨迹",
                "缺少候选披露轨迹",
                suggestion="新标注必须保存每轮候选、选择和快照摘要",
                return_stage="ROUTE",
            )
            return selected_codes

        seen_trace_ids: set[str] = set()
        for position, trace in enumerate(traces):
            path = f"标注流程状态/候选披露轨迹/{position}"
            trace_id = trace.get("轨迹编号")
            if trace_id in seen_trace_ids:
                self.add_issue(
                    "渐进披露轨迹校验",
                    "阻断",
                    path,
                    "候选轨迹编号重复",
                    actual=trace_id,
                    return_stage=trace.get("阶段代码"),
                )
            if trace_id:
                seen_trace_ids.add(trace_id)
                self.traces_by_id[trace_id] = trace

            candidates = trace.get("候选代码", [])
            choices = trace.get("选择代码", [])
            if not set(choices).issubset(set(candidates)):
                self.add_issue(
                    "渐进披露轨迹校验",
                    "阻断",
                    path,
                    "选择代码包含本轮未披露的标签",
                    actual=sorted(set(choices) - set(candidates)),
                    expected="选择代码必须是候选代码子集",
                    return_stage=trace.get("阶段代码"),
                )
            selected_codes.update(code for code in choices if isinstance(code, str))

            canonical = json.dumps(candidates, ensure_ascii=False, separators=(",", ":"))
            expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if trace.get("候选快照摘要") != expected_hash:
                self.add_issue(
                    "渐进披露轨迹校验",
                    self.legacy_level(),
                    f"{path}/候选快照摘要",
                    "候选快照摘要与候选代码不一致",
                    actual=trace.get("候选快照摘要"),
                    expected=expected_hash,
                    return_stage=trace.get("阶段代码"),
                )
        return selected_codes

    def validate_tree_and_paths(self, selected_codes: set[str]) -> None:
        """校验场景设施/设备候选、父子部件关系、编号和主要对象引用。"""

        route = self.annotation.get("图像路由", {})
        domain_code = route.get("业务域", {}).get("代码")
        scene_code = route.get("设施场景", {}).get("代码")
        try:
            scene = find_scene(self.ledger, domain_code, scene_code)
        except SystemExit as exc:
            self.add_issue(
                "候选树路径校验",
                "阻断",
                "图像路由",
                str(exc),
                return_stage="ROUTE",
            )
            return

        facility_codes = {item["代码"] for item in scene.get("设施候选", [])}
        device_codes = {item["代码"] for item in scene.get("设备候选", [])}
        for node, parent, relation, _, path in self.iter_nodes(
            self.annotation.get("标注对象树", [])
        ):
            object_id = node.get("对象编号")
            if object_id in self.object_ids:
                self.add_issue(
                    "对象树关系校验",
                    "阻断",
                    f"{path}/对象编号",
                    "对象编号全树重复",
                    actual=object_id,
                    return_stage="SUBJECT",
                )
            if object_id:
                self.object_ids.add(object_id)

            code = node.get("标签", {}).get("代码")
            if selected_codes and code not in selected_codes:
                self.add_issue(
                    "渐进披露轨迹校验",
                    self.legacy_level(),
                    f"{path}/标签/代码",
                    "对象代码没有出现在任何选择轨迹中",
                    actual=code,
                    return_stage="SUBJECT",
                )
            trace_id = node.get("候选披露轨迹编号")
            if trace_id is None:
                self.add_issue(
                    "渐进披露轨迹校验",
                    self.legacy_level(),
                    f"{path}/候选披露轨迹编号",
                    "对象没有引用产生该选择的候选轨迹",
                    actual=code,
                    return_stage="SUBJECT",
                )
            elif trace_id not in self.traces_by_id:
                self.add_issue(
                    "渐进披露轨迹校验",
                    "阻断",
                    f"{path}/候选披露轨迹编号",
                    "对象引用了不存在的候选轨迹",
                    actual=trace_id,
                    return_stage="SUBJECT",
                )
            elif code not in self.traces_by_id[trace_id].get("选择代码", []):
                self.add_issue(
                    "渐进披露轨迹校验",
                    "阻断",
                    f"{path}/候选披露轨迹编号",
                    "对象代码不是所引用轨迹的实际选择",
                    actual={"对象代码": code, "轨迹编号": trace_id},
                    return_stage="SUBJECT",
                )

            if parent is None and code not in facility_codes:
                # 部件特写可以直接挂在设施节点，但根节点本身仍应是当前场景设施；
                # 变电站旧示例没有显式站级设施候选时，只降为警告供人工确认。
                # 早期示例曾把设施场景代码本身作为根设施节点。兼容模式允许读取，
                # 但必须报告警告；新标注仍应使用正式设施代码。
                legacy_scene_root = self.allow_legacy and code == scene_code
                level = "警告" if not facility_codes or legacy_scene_root else "阻断"
                self.add_issue(
                    "候选树路径校验",
                    level,
                    path,
                    "根设施不在当前场景设施候选中",
                    actual=code,
                    return_stage="SUBJECT",
                )
            elif relation == "设备实例" and code not in device_codes:
                self.add_issue(
                    "候选树路径校验",
                    "阻断",
                    path,
                    "设备不属于当前设施场景",
                    actual=code,
                    return_stage="SUBJECT",
                )
            elif relation == "部件实例" and parent is not None:
                parent_code = parent.get("标签", {}).get("代码")
                allowed_parts, _ = part_candidates(
                    self.ledger,
                    scene,
                    parent_code,
                    self.index,
                    include_nontrainable=True,
                )
                allowed_codes = {item["代码"] for item in allowed_parts}
                if code not in allowed_codes:
                    self.add_issue(
                        "候选树路径校验",
                        "阻断",
                        path,
                        "部件或子部件不属于当前父对象",
                        actual={"父对象": parent_code, "子对象": code},
                        expected=sorted(allowed_codes),
                        return_stage="SUBJECT",
                    )

        main_id = self.annotation.get("主要对象主题", {}).get("对象编号")
        if main_id not in self.object_ids:
            self.add_issue(
                "对象树关系校验",
                "阻断",
                "主要对象主题/对象编号",
                "主要对象编号在对象树中不存在",
                actual=main_id,
                return_stage="SUBJECT",
            )

    def validate_defects_and_evidence(self, selected_codes: set[str]) -> None:
        """校验缺陷载体兼容、材质过滤、编号、证据引用和检查完整性。"""

        route = self.annotation.get("图像路由", {})
        domain_code = route.get("业务域", {}).get("代码")
        scene_code = route.get("设施场景", {}).get("代码")
        task = route.get("拍摄任务")
        try:
            find_scene(self.ledger, domain_code, scene_code)
        except SystemExit:
            return

        evidence_ids = {
            item.get("证据编号")
            for item in self.annotation.get("视觉证据", [])
            if item.get("证据编号")
        }
        valid_check_states = {
            item["代码"] for item in self.ledger["缺陷检查结论候选"]
        } | {
            item["中文名称"] for item in self.ledger["缺陷检查结论候选"]
        }

        for node, parent, _, attributes, path in self.iter_nodes(
            self.annotation.get("标注对象树", [])
        ):
            carrier_code = node.get("标签", {}).get("代码")
            parent_code = parent.get("标签", {}).get("代码") if parent else None
            allowed, filter_audit, _ = defect_candidates(
                self.ledger,
                carrier_code,
                parent_code,
                attributes,
                task,
                self.index,
            )
            allowed_codes = {item["代码"] for item in allowed}

            check = node.get("缺陷检查")
            if check is None:
                self.add_issue(
                    "任务完整性校验",
                    self.legacy_level(),
                    f"{path}/缺陷检查",
                    "载体缺少缺陷检查状态",
                    return_stage="DEFECT",
                )
            else:
                state = check.get("状态")
                if state not in valid_check_states:
                    self.add_issue(
                        "任务完整性校验",
                        "阻断",
                        f"{path}/缺陷检查/状态",
                        "缺陷检查状态不在固定候选中",
                        actual=state,
                        return_stage="DEFECT",
                    )
                check_trace_id = check.get("候选披露轨迹编号")
                if check_trace_id is None:
                    self.add_issue(
                        "渐进披露轨迹校验",
                        self.legacy_level(),
                        f"{path}/缺陷检查/候选披露轨迹编号",
                        "缺陷检查结论没有引用候选轨迹",
                        actual=state,
                        return_stage="DEFECT",
                    )
                elif check_trace_id not in self.traces_by_id:
                    self.add_issue(
                        "渐进披露轨迹校验",
                        "阻断",
                        f"{path}/缺陷检查/候选披露轨迹编号",
                        "缺陷检查结论引用了不存在的候选轨迹",
                        actual=check_trace_id,
                        return_stage="DEFECT",
                    )
                elif state not in self.traces_by_id[check_trace_id].get("选择代码", []):
                    self.add_issue(
                        "渐进披露轨迹校验",
                        "阻断",
                        f"{path}/缺陷检查/候选披露轨迹编号",
                        "缺陷检查状态不是所引用轨迹的实际选择",
                        actual={"检查状态": state, "轨迹编号": check_trace_id},
                        return_stage="DEFECT",
                    )
                if state in {"未处理", "CHECK_UNPROCESSED"}:
                    self.add_issue(
                        "任务完整性校验",
                        "阻断",
                        f"{path}/缺陷检查/状态",
                        "存在未处理载体",
                        return_stage="DEFECT",
                    )

            for defect_position, defect in enumerate(node.get("缺陷实例", [])):
                defect_path = f"{path}/缺陷实例/{defect_position}"
                defect_id = defect.get("缺陷编号")
                if defect_id in self.defect_ids:
                    self.add_issue(
                        "对象树关系校验",
                        "阻断",
                        f"{defect_path}/缺陷编号",
                        "缺陷编号全树重复",
                        actual=defect_id,
                        return_stage="DEFECT",
                    )
                if defect_id:
                    self.defect_ids.add(defect_id)

                defect_code = defect.get("缺陷类别", {}).get("代码")
                if defect_code == "NO_VISIBLE_ANOMALY":
                    self.add_issue(
                        "载体缺陷兼容校验",
                        "阻断",
                        defect_path,
                        "无明显异常是检查结论，不能创建缺陷实例",
                        return_stage="DEFECT",
                    )

                defect_trace_id = defect.get("候选披露轨迹编号")
                if defect_trace_id is None:
                    self.add_issue(
                        "渐进披露轨迹校验",
                        self.legacy_level(),
                        f"{defect_path}/候选披露轨迹编号",
                        "缺陷没有引用产生该选择的候选轨迹",
                        actual=defect_code,
                        return_stage="DEFECT",
                    )
                elif defect_trace_id not in self.traces_by_id:
                    self.add_issue(
                        "渐进披露轨迹校验",
                        "阻断",
                        f"{defect_path}/候选披露轨迹编号",
                        "缺陷引用了不存在的候选轨迹",
                        actual=defect_trace_id,
                        return_stage="DEFECT",
                    )
                elif defect_code not in self.traces_by_id[defect_trace_id].get("选择代码", []):
                    self.add_issue(
                        "渐进披露轨迹校验",
                        "阻断",
                        f"{defect_path}/候选披露轨迹编号",
                        "缺陷代码不是所引用轨迹的实际选择",
                        actual={"缺陷代码": defect_code, "轨迹编号": defect_trace_id},
                        return_stage="DEFECT",
                    )
                if defect_code not in allowed_codes:
                    matching_filter = next(
                        (
                            item
                            for item in filter_audit
                            if item["缺陷代码"] == defect_code
                        ),
                        None,
                    )
                    self.add_issue(
                        "静态属性约束校验" if matching_filter else "载体缺陷兼容校验",
                        "阻断",
                        f"{defect_path}/缺陷类别/代码",
                        "缺陷不属于当前载体过滤后的候选",
                        rule_id=matching_filter.get("规则编号") if matching_filter else None,
                        actual=defect_code,
                        expected=sorted(allowed_codes),
                        suggestion=matching_filter.get("过滤原因") if matching_filter else "重新加载当前载体缺陷候选",
                        return_stage="DEFECT",
                    )

                if selected_codes and defect_code not in selected_codes:
                    self.add_issue(
                        "渐进披露轨迹校验",
                        self.legacy_level(),
                        f"{defect_path}/缺陷类别/代码",
                        "缺陷代码没有出现在任何选择轨迹中",
                        actual=defect_code,
                        return_stage="DEFECT",
                    )

                carrier_id = defect.get("载体对象编号")
                if carrier_id is None and self.allow_legacy:
                    pass
                elif carrier_id != node.get("对象编号"):
                    self.add_issue(
                        "对象树关系校验",
                        "阻断",
                        f"{defect_path}/载体对象编号",
                        "缺陷载体编号与嵌套节点不一致",
                        actual=carrier_id,
                        expected=node.get("对象编号"),
                        return_stage="DEFECT",
                    )

                evidence_refs = defect.get("直接可见证据编号", [])
                if not evidence_refs:
                    self.add_issue(
                        "来源和证据形式校验",
                        self.legacy_level(),
                        f"{defect_path}/直接可见证据编号",
                        "缺陷没有引用直接可见证据",
                        return_stage="DEFECT",
                    )
                missing_evidence = sorted(set(evidence_refs) - evidence_ids)
                if missing_evidence:
                    self.add_issue(
                        "来源和证据形式校验",
                        "阻断",
                        f"{defect_path}/直接可见证据编号",
                        "缺陷引用了不存在的视觉证据",
                        actual=missing_evidence,
                        return_stage="DEFECT",
                    )

    def validate_stage_completeness(self) -> None:
        """校验阶段门禁；存量示例缺少新流程状态时只报告警告。"""

        flow = self.annotation.get("标注流程状态")
        if not flow:
            return
        stage_states = flow.get("阶段状态", {})
        if list(stage_states) != STAGE_ORDER:
            self.add_issue(
                "任务完整性校验",
                "阻断",
                "标注流程状态/阶段状态",
                "阶段代码或顺序与2.0固定流程不一致",
                actual=list(stage_states),
                expected=STAGE_ORDER,
                return_stage="ROUTE",
            )
        for stage in ("ROUTE", "SUBJECT", "DEFECT"):
            if stage_states.get(stage) != "已完成":
                self.add_issue(
                    "任务完整性校验",
                    "阻断",
                    f"标注流程状态/阶段状态/{stage}",
                    "进入阶段四规则校验前，前三阶段必须已完成",
                    actual=stage_states.get(stage),
                    expected="已完成",
                    return_stage=stage,
                )

    def run(self) -> dict[str, Any]:
        """按规范固定顺序执行检查并生成阶段四A报告。"""

        self.validate_structure()
        self.validate_ledger_labels()
        selected_codes = self.validate_traces()
        self.validate_tree_and_paths(selected_codes)
        self.validate_defects_and_evidence(selected_codes)
        self.validate_stage_completeness()

        blocking = sum(1 for issue in self.issues if issue["问题级别"] == "阻断")
        warnings = sum(1 for issue in self.issues if issue["问题级别"] == "警告")
        conclusion = (
            "需要修改" if blocking else "通过但有警告" if warnings else "通过"
        )
        check_results = {
            name: (
                "失败"
                if any(
                    issue["校验类别"] == name and issue["问题级别"] == "阻断"
                    for issue in self.issues
                )
                else "警告"
                if any(
                    issue["校验类别"] == name and issue["问题级别"] == "警告"
                    for issue in self.issues
                )
                else "通过"
            )
            for name in RULE_CHECKS
        }
        return {
            "规则校验": {
                "执行状态": "已完成",
                "规则集版本": self.ledger["标签台账版本"],
                "总体结论": conclusion,
                "阻断问题数量": blocking,
                "警告问题数量": warnings,
                "校验项": check_results,
                "问题列表": self.issues,
                "允许进入大模型语义复核": blocking == 0,
            }
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("annotation", type=Path, help="待校验的单张标注JSON")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--report", type=Path, help="可选的独立校验报告输出路径")
    parser.add_argument(
        "--allow-legacy",
        action="store_true",
        help="仅兼容读取旧示例；新训练数据禁止使用",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        annotation = json.loads(args.annotation.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取标注JSON {args.annotation}: {exc}") from exc
    ledger = load_ledger(args.ledger)
    report = Validator(annotation, ledger, args.allow_legacy).run()
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
