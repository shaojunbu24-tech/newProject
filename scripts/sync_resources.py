#!/usr/bin/env python3
"""把项目中的权威2.0规则同步为Skill运行时资源。

设计原则
--------
项目目录中的候选树和标注规则是唯一人工维护事实源。Skill目录中的JSON和Markdown
只是带版本摘要的运行时快照，不能人工双份修改。本脚本执行机械同步并生成SHA-256
清单，便于发现Skill快照落后于项目源文件的情况。

调用者必须通过``--source-dir``显式指定2.0规则目录。除三份参考资料和一份JSON
模板外，脚本还同步配电、输电绝缘子严格结构示例。这样Skill仓库不绑定维护者的
本机路径，也不会在资源清单中泄露用户目录。脚本只覆盖Skill自己的
references/assets资源，不修改项目源文件。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]

# 参考文档供代理按需读取；JSON模板作为新标注任务的输出骨架放在assets中。
REFERENCE_FILES = (
    "标注规则2.0.md",
    "标签候选树2.0.json",
    "候选标签覆盖审计2.0.md",
)
ASSET_FILES = ("标注规则2.0.json",)

# 只同步经过严格校验、明确标注为“结构回归且不进入训练”的示例。旧版示例缺少
# 候选披露轨迹和载体检查状态，继续放入Skill会诱导其他代理复制兼容格式。
STRICT_EXAMPLE_FILES = (
    "绝缘子釉表面灼伤-配电-严格结构示例.json",
    "绝缘子釉表面灼伤-输电-严格结构示例.json",
)


def sha256_file(path: Path) -> str:
    """流式计算文件摘要，避免将较大的候选树一次读入内存。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_resource(source: Path, target: Path) -> dict[str, object]:
    """复制单个资源并返回可写入清单的稳定元数据。"""

    if not source.is_file():
        raise FileNotFoundError(f"缺少权威源文件：{source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {
        # 清单只记录文件名，不保存调用者机器上的绝对源路径。资源内容完整性由
        # SHA-256保证，维护者仍可在自己的项目中任意放置权威源目录。
        "源文件": source.name,
        "Skill资源": str(target.relative_to(SKILL_ROOT)),
        "字节数": target.stat().st_size,
        "SHA256": sha256_file(target),
    }


def parse_args() -> argparse.Namespace:
    """解析命令行；保持接口简单，便于Skill和维护者共同使用。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        required=True,
        help="包含标注规则2.0文件的项目目录",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_dir = args.source_dir.expanduser().resolve()
    manifest_entries: list[dict[str, object]] = []

    for name in REFERENCE_FILES:
        manifest_entries.append(
            copy_resource(source_dir / name, SKILL_ROOT / "references" / name)
        )
    for name in ASSET_FILES:
        manifest_entries.append(
            copy_resource(source_dir / name, SKILL_ROOT / "assets" / name)
        )
    for name in STRICT_EXAMPLE_FILES:
        manifest_entries.append(
            copy_resource(
                source_dir / "例子" / name,
                SKILL_ROOT / "assets" / "examples" / name,
            )
        )

    manifest = {
        "_说明": "该文件由sync_resources.py生成，不应手工编辑。",
        "同步时间_UTC": datetime.now(timezone.utc).isoformat(),
        "权威源目录": "运行时通过--source-dir指定；为保护本机路径不写入清单",
        "资源": manifest_entries,
    }
    manifest_path = SKILL_ROOT / "references" / "资源清单.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"已同步{len(manifest_entries)}个资源。")
    print(f"资源清单：{manifest_path}")


if __name__ == "__main__":
    main()
