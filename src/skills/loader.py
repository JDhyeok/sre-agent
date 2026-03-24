"""
SKILL.md Loader — Markdown + YAML frontmatter 파싱.

skills/ 디렉토리에서 .md 파일을 읽어 SkillMeta 모델로 변환한다.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from src.config import settings
from src.models.schemas import Risk, SkillMeta, SkillStep

logger = logging.getLogger(__name__)


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """YAML frontmatter와 본문을 분리한다."""
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    frontmatter = yaml.safe_load(parts[1]) or {}
    body = parts[2].strip()
    return frontmatter, body


def _parse_steps(body: str, section: str = "## steps") -> list[SkillStep]:
    """본문에서 steps 섹션을 파싱한다."""
    steps: list[SkillStep] = []
    in_section = False
    current_step: dict | None = None
    current_command_lines: list[str] = []
    in_command = False

    for line in body.split("\n"):
        stripped = line.strip()

        if stripped.lower().startswith("## "):
            if in_section and current_step:
                current_step["command"] = "\n".join(current_command_lines)
                steps.append(SkillStep(**current_step))
                current_step = None
                current_command_lines = []
            in_section = stripped.lower() == section
            in_command = False
            continue

        if not in_section:
            continue

        # 새 step 시작: "1. step_name:" 패턴
        if stripped and stripped[0].isdigit() and "." in stripped.split()[0]:
            if current_step:
                current_step["command"] = "\n".join(current_command_lines)
                steps.append(SkillStep(**current_step))

            parts = stripped.split(".", 1)
            order = int(parts[0].strip())
            name = parts[1].strip().rstrip(":")
            current_step = {
                "order": order,
                "name": name,
                "description": "",
                "command": "",
            }
            current_command_lines = []
            in_command = False
            continue

        if current_step:
            if stripped.startswith("description:"):
                current_step["description"] = stripped.split(":", 1)[1].strip().strip('"')
            elif stripped.startswith("command:"):
                in_command = True
            elif stripped.startswith("timeout:"):
                current_step["timeout"] = stripped.split(":", 1)[1].strip()
                in_command = False
            elif stripped.startswith("rollback_on_fail:"):
                val = stripped.split(":", 1)[1].strip().lower()
                current_step["rollback_on_fail"] = val == "true"
                in_command = False
            elif in_command and (line.startswith("    ") or line.startswith("\t") or not stripped):
                current_command_lines.append(line.rstrip())

    # 마지막 step 저장
    if current_step:
        current_step["command"] = "\n".join(current_command_lines)
        steps.append(SkillStep(**current_step))

    return steps


def load_skill(filepath: Path) -> SkillMeta:
    """단일 SKILL.md 파일을 로드한다."""
    content = filepath.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(content)

    steps = _parse_steps(body, "## steps")
    rollback_steps = _parse_steps(body, "## rollback")

    return SkillMeta(
        id=meta.get("id", filepath.stem),
        name=meta.get("name", filepath.stem),
        trigger=meta.get("trigger", ""),
        scope=meta.get("scope", {}),
        risk=Risk(meta.get("risk", "medium")),
        approval=meta.get("approval", "required"),
        tags=meta.get("tags", []),
        preconditions=meta.get("preconditions", []),
        steps=steps,
        rollback_steps=rollback_steps,
        requires=meta.get("requires", []),
        chains=meta.get("chains", []),
    )


def load_all_skills(directory: str | None = None) -> dict[str, SkillMeta]:
    """skills/ 디렉토리의 모든 SKILL.md를 로드한다."""
    skills_dir = Path(directory or settings.skills_directory)
    skills: dict[str, SkillMeta] = {}

    if not skills_dir.exists():
        logger.warning("skills_directory_not_found", extra={"path": str(skills_dir)})
        return skills

    for filepath in skills_dir.glob("*.md"):
        try:
            skill = load_skill(filepath)
            skills[skill.id] = skill
            logger.info("skill_loaded", extra={"id": skill.id, "name": skill.name})
        except Exception as e:
            logger.error(
                "skill_load_failed",
                extra={"file": str(filepath), "error": str(e)},
            )

    logger.info("all_skills_loaded", extra={"count": len(skills)})
    return skills
