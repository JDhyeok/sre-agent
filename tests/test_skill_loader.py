"""SKILL.md Loader 테스트 — 파싱, 검증, 디렉토리 로드."""

from pathlib import Path
from textwrap import dedent

import pytest

from src.models.schemas import Risk
from src.skills.loader import load_all_skills, load_skill


@pytest.fixture
def sample_skill_file(tmp_path: Path) -> Path:
    content = dedent("""\
    ---
    id: test-restart
    name: 서비스 재시작
    trigger: service_down
    scope:
      os: [ubuntu]
    risk: medium
    approval: required
    tags: [restart]
    requires: []
    chains: []
    ---

    ## preconditions
    - service.state == "failed"

    ## steps
    1. check_logs:
       description: "최근 로그 확인"
       command: |
         journalctl -u ${SERVICE_NAME} --since "5 min ago" --no-pager
       timeout: 10s
       rollback_on_fail: false

    2. restart_service:
       description: "서비스 재시작"
       command: |
         systemctl restart ${SERVICE_NAME}
         sleep 3
         systemctl is-active ${SERVICE_NAME}
       timeout: 30s
       rollback_on_fail: true

    ## rollback
    1. manual_check:
       description: "수동 확인 필요"
       command: |
         echo "Manual intervention required"
       timeout: 10s

    ## history
    - 2024-12-01: web-042 nginx 재시작 성공
    """)
    filepath = tmp_path / "test-restart.md"
    filepath.write_text(content)
    return filepath


def test_load_skill(sample_skill_file: Path):
    skill = load_skill(sample_skill_file)

    assert skill.id == "test-restart"
    assert skill.name == "서비스 재시작"
    assert skill.trigger == "service_down"
    assert skill.risk == Risk.MEDIUM
    assert skill.approval == "required"
    assert "ubuntu" in skill.scope["os"]
    assert len(skill.steps) == 2
    assert skill.steps[0].name == "check_logs"
    assert skill.steps[1].name == "restart_service"
    assert skill.steps[1].rollback_on_fail is True
    assert len(skill.rollback_steps) == 1


def test_load_all_skills(tmp_path: Path):
    # 2개의 skill 파일 생성
    for name in ["skill-a", "skill-b"]:
        content = dedent(f"""\
        ---
        id: {name}
        name: Skill {name}
        trigger: manual
        scope: {{}}
        risk: low
        approval: auto
        ---

        ## steps
        1. do_thing:
           description: "do it"
           command: |
             echo hello
        """)
        (tmp_path / f"{name}.md").write_text(content)

    skills = load_all_skills(str(tmp_path))
    assert len(skills) == 2
    assert "skill-a" in skills
    assert "skill-b" in skills


def test_load_empty_directory(tmp_path: Path):
    skills = load_all_skills(str(tmp_path))
    assert len(skills) == 0
