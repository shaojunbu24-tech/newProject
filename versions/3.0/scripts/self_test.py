#!/usr/bin/env python3
"""回归测试3.0双模式、双输出和关键反例。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Optional

from export_content_view import export_content_view
from validate_annotation import collect_codes, load_json, validate_audit, validate_content


ROOT = Path(__file__).resolve().parents[1]
TREE = ROOT / "标签候选树3.0.json"
if not TREE.exists():
    TREE = ROOT / "references" / "标签候选树3.0.json"
EXAMPLES = ROOT / "例子"
if not EXAMPLES.exists():
    EXAMPLES = ROOT / "assets" / "examples"


def find_first_defect(data: dict) -> dict:
    def visit(objects: list[dict]) -> Optional[dict]:
        for node in objects:
            defects = node.get("缺陷实例", [])
            if defects:
                return defects[0]
            for key in ("设备实例", "部件实例"):
                found = visit(node.get(key, []))
                if found:
                    return found
        return None

    defect = visit(data["标注对象树"])
    assert defect is not None
    return defect


def main() -> int:
    tree = load_json(TREE)
    audit_template_path = ROOT / "标注规则3.0_审计版.json"
    content_template_path = ROOT / "标注规则3.0_内容版.json"
    if not audit_template_path.exists():
        audit_template_path = ROOT / "assets" / "标注规则3.0_审计版.json"
        content_template_path = ROOT / "assets" / "标注规则3.0_内容版.json"
    audit_template = load_json(audit_template_path)
    content_template = load_json(content_template_path)
    assert audit_template["标注规范版本"] == "电力设备多模态标注-v3.0"
    assert content_template["标注规范版本"] == "电力设备多模态标注-v3.0-content"
    assert set(tree.get("3.0双模式配置", {})) == {"STRICT_PACKAGE", "REFERENCE_PACKAGE"}
    assert len(list(EXAMPLES.glob("*.json"))) == 4

    known_codes = collect_codes(tree)
    strict = load_json(EXAMPLES / "绝缘子釉表面灼伤-严格标签包.audit.json")
    reference = load_json(EXAMPLES / "绝缘子釉面局部复合异常-参考标签包.audit.json")

    strict_report = validate_audit(strict, known_codes)
    assert strict_report["阻断问题数量"] == 0, strict_report
    assert strict_report["允许进入受控训练"] is True

    reference_report = validate_audit(reference, known_codes)
    assert reference_report["阻断问题数量"] == 0, reference_report
    assert reference_report["包含开放标签"] is True
    assert reference_report["允许进入受控训练"] is False
    assert reference_report["允许进入开放词汇候选池"] is True

    # 严格模式不得接受一个临时编造的正式代码。
    bad_strict = copy.deepcopy(strict)
    category = find_first_defect(bad_strict)["缺陷类别"]
    category["代码"] = "MODEL_INVENTED_DEFECT"
    category["中文名称"] = "模型临时缺陷"
    bad_report = validate_audit(bad_strict, known_codes)
    assert bad_report["阻断问题数量"] > 0

    # 参考模式虽可保留开放名称，但缺少候选映射时必须阻断。
    bad_reference = copy.deepcopy(reference)
    find_first_defect(bad_reference)["缺陷类别"].pop("候选树映射", None)
    bad_reference_report = validate_audit(bad_reference, known_codes)
    assert bad_reference_report["阻断问题数量"] > 0

    # 两种模式都能导出内容版，且不泄漏审计过程字段。
    forbidden = {
        "标注模式", "输出视图", "标注流程状态", "自动校验结果",
        "候选披露轨迹编号", "候选代码", "候选快照摘要", "候选树映射",
    }
    for audit in (strict, reference):
        content = export_content_view(audit)
        content_report = validate_content(content)
        assert content_report["阻断问题数量"] == 0, content_report
        serialized = json.dumps(content, ensure_ascii=False)
        for key in forbidden:
            assert f'"{key}"' not in serialized, key
        assert content.get("标注对象树")

    print("3.0自检通过：严格/参考双模式、审计/内容双输出及关键反例均符合预期。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
