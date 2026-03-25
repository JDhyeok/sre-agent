"""대시보드 정적 서빙 스모크 테스트.

모듈 최상단에서 app을 import하면 database가 ./data로 고정되어
graph_store 테스트와 충돌하므로 테스트 함수 안에서 lazy import한다.
"""

from fastapi.testclient import TestClient


def test_dashboard_index_and_static() -> None:
    from src.api.main import app

    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        assert b"SRE Agent" in r.content

        css = client.get("/static/styles.css")
        assert css.status_code == 200
        assert b":root" in css.content

        vis = client.get("/static/vendor/vis-network.min.js")
        assert vis.status_code == 200
        assert len(vis.content) > 10_000
