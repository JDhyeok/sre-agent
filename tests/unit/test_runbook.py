"""Tests for runbook parsing and loading."""

import pytest

from sre_agent.tools.runbook import _parse_frontmatter


class TestParseFrontmatter:
    def test_valid_frontmatter(self, sample_runbook_markdown):
        meta, body = _parse_frontmatter(sample_runbook_markdown)
        assert meta["name"] == "memory-leak-restart"
        assert meta["risk"] == "medium"
        assert meta["script"] == "scripts/restart-app.sh"
        assert meta["target_host_label"] == "service=web-app"
        assert "When to use" in body

    def test_no_frontmatter(self):
        text = "# Just a regular markdown\nNo frontmatter here."
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert "regular markdown" in body

    def test_empty_frontmatter(self):
        text = "---\n---\nBody content"
        meta, body = _parse_frontmatter(text)
        assert meta == {} or meta is None
        assert "Body" in body

    def test_frontmatter_with_trigger(self):
        text = '---\nname: test-runbook\ntrigger: "High CPU usage"\nrisk: low\nscript: scripts/test.sh\n---\nBody'
        meta, body = _parse_frontmatter(text)
        assert meta["name"] == "test-runbook"
        assert meta["trigger"] == "High CPU usage"
        assert meta["risk"] == "low"
