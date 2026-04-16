"""Tests for the intake pipeline: deduplication, grouping, severity routing."""

import time
import threading

import pytest

from sre_agent.pipeline.intake import (
    AlertDeduplicator,
    AlertGrouper,
    AlertItem,
    AnalysisLevel,
    IncidentRequest,
    IntakeProcessor,
    route_severity,
)
from sre_agent.config import IntakeConfig


# ---------------------------------------------------------------------------
# route_severity
# ---------------------------------------------------------------------------

class TestRouteSeverity:
    def test_critical_maps_to_full_analysis(self):
        routing = {"critical": "full_analysis", "warning": "lightweight"}
        assert route_severity("critical", routing) == AnalysisLevel.FULL_ANALYSIS

    def test_warning_maps_to_lightweight(self):
        routing = {"critical": "full_analysis", "warning": "lightweight"}
        assert route_severity("warning", routing) == AnalysisLevel.LIGHTWEIGHT

    def test_info_maps_to_log_only(self):
        routing = {"info": "log_only"}
        assert route_severity("info", routing) == AnalysisLevel.LOG_ONLY

    def test_resolved_maps_to_summary_only(self):
        routing = {"resolved": "summary_only"}
        assert route_severity("resolved", routing) == AnalysisLevel.SUMMARY_ONLY

    def test_unknown_severity_defaults_to_lightweight(self):
        routing = {"critical": "full_analysis"}
        assert route_severity("unknown_sev", routing) == AnalysisLevel.LIGHTWEIGHT

    def test_empty_routing_defaults_to_lightweight(self):
        assert route_severity("critical", {}) == AnalysisLevel.LIGHTWEIGHT


# ---------------------------------------------------------------------------
# AlertDeduplicator
# ---------------------------------------------------------------------------

class TestAlertDeduplicator:
    def test_first_alert_is_not_duplicate(self):
        dedup = AlertDeduplicator(window_minutes=5)
        alert = AlertItem(alertname="HighCPU", severity="critical", status="firing",
                          labels={"service": "api", "instance": "host1"}, annotations={})
        assert dedup.is_duplicate(alert) is False

    def test_same_alert_is_duplicate(self):
        dedup = AlertDeduplicator(window_minutes=5)
        alert = AlertItem(alertname="HighCPU", severity="critical", status="firing",
                          labels={"service": "api", "instance": "host1"}, annotations={})
        dedup.is_duplicate(alert)
        assert dedup.is_duplicate(alert) is True

    def test_different_alert_is_not_duplicate(self):
        dedup = AlertDeduplicator(window_minutes=5)
        alert1 = AlertItem(alertname="HighCPU", severity="critical", status="firing",
                           labels={"service": "api", "instance": "host1"}, annotations={})
        alert2 = AlertItem(alertname="HighMemory", severity="warning", status="firing",
                           labels={"service": "api", "instance": "host1"}, annotations={})
        dedup.is_duplicate(alert1)
        assert dedup.is_duplicate(alert2) is False

    def test_different_instance_is_not_duplicate(self):
        dedup = AlertDeduplicator(window_minutes=5)
        alert1 = AlertItem(alertname="HighCPU", severity="critical", status="firing",
                           labels={"service": "api", "instance": "host1"}, annotations={})
        alert2 = AlertItem(alertname="HighCPU", severity="critical", status="firing",
                           labels={"service": "api", "instance": "host2"}, annotations={})
        dedup.is_duplicate(alert1)
        assert dedup.is_duplicate(alert2) is False

    def test_thread_safety(self):
        dedup = AlertDeduplicator(window_minutes=5)
        results = []

        def check():
            alert = AlertItem(alertname="HighCPU", severity="critical", status="firing",
                              labels={"service": "api", "instance": "host1"}, annotations={})
            results.append(dedup.is_duplicate(alert))

        threads = [threading.Thread(target=check) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one should be non-duplicate (first), rest duplicates
        assert results.count(False) == 1
        assert results.count(True) == 9


# ---------------------------------------------------------------------------
# AlertGrouper
# ---------------------------------------------------------------------------

class TestAlertGrouper:
    def test_add_returns_group_key(self):
        grouper = AlertGrouper(window_seconds=60)
        alert = AlertItem(alertname="HighCPU", severity="critical", status="firing",
                          labels={"service": "web-app"}, annotations={})
        key = grouper.add(alert)
        assert key.startswith("grp-")
        assert "web-app" in key

    def test_same_service_groups_together(self):
        grouper = AlertGrouper(window_seconds=60)
        alert1 = AlertItem(alertname="HighCPU", severity="critical", status="firing",
                           labels={"service": "web-app"}, annotations={})
        alert2 = AlertItem(alertname="HighMemory", severity="warning", status="firing",
                           labels={"service": "web-app"}, annotations={})
        key1 = grouper.add(alert1)
        key2 = grouper.add(alert2)
        assert key1 == key2

    def test_different_services_separate_groups(self):
        grouper = AlertGrouper(window_seconds=60)
        alert1 = AlertItem(alertname="HighCPU", severity="critical", status="firing",
                           labels={"service": "web-app"}, annotations={})
        alert2 = AlertItem(alertname="HighCPU", severity="critical", status="firing",
                           labels={"service": "db-server"}, annotations={})
        key1 = grouper.add(alert1)
        key2 = grouper.add(alert2)
        assert key1 != key2

    def test_flush_all_returns_all_groups(self):
        grouper = AlertGrouper(window_seconds=60)
        alert1 = AlertItem(alertname="HighCPU", severity="critical", status="firing",
                           labels={"service": "web-app"}, annotations={})
        alert2 = AlertItem(alertname="HighMemory", severity="critical", status="firing",
                           labels={"service": "db-server"}, annotations={})
        grouper.add(alert1)
        grouper.add(alert2)
        groups = grouper.flush_all()
        assert len(groups) == 2


# ---------------------------------------------------------------------------
# IncidentRequest
# ---------------------------------------------------------------------------

class TestIncidentRequest:
    def test_primary_alertname(self):
        alert = AlertItem(alertname="HighCPU", severity="critical", status="firing", labels={}, annotations={})
        req = IncidentRequest(
            incident_id="INC-001",
            alerts=[alert],
            analysis_level=AnalysisLevel.FULL_ANALYSIS,
            group_key="grp-test",
        )
        assert req.primary_alertname == "HighCPU"

    def test_primary_severity(self):
        alert = AlertItem(alertname="HighCPU", severity="critical", status="firing", labels={}, annotations={})
        req = IncidentRequest(
            incident_id="INC-001",
            alerts=[alert],
            analysis_level=AnalysisLevel.FULL_ANALYSIS,
            group_key="grp-test",
        )
        assert req.primary_severity == "critical"

    def test_format_context_includes_alert_info(self):
        alert = AlertItem(
            alertname="HighCPU", severity="critical", status="firing",
            labels={"service": "web-app", "instance": "host1"},
            annotations={"summary": "CPU is high"},
        )
        req = IncidentRequest(
            incident_id="INC-001",
            alerts=[alert],
            analysis_level=AnalysisLevel.FULL_ANALYSIS,
            group_key="grp-test",
        )
        ctx = req.format_context()
        assert "HighCPU" in ctx
        assert "critical" in ctx
        assert "INC-001" in ctx


# ---------------------------------------------------------------------------
# IntakeProcessor
# ---------------------------------------------------------------------------

class TestIntakeProcessor:
    def test_process_alertmanager_payload(self, sample_alertmanager_payload):
        config = IntakeConfig()
        processor = IntakeProcessor(config)
        requests = processor.process_alertmanager_payload(sample_alertmanager_payload)
        assert len(requests) >= 1
        req = requests[0]
        assert req.primary_alertname == "HighMemoryUsage"
        assert req.analysis_level == AnalysisLevel.FULL_ANALYSIS

    def test_duplicate_payload_returns_empty(self, sample_alertmanager_payload):
        config = IntakeConfig()
        processor = IntakeProcessor(config)
        processor.process_alertmanager_payload(sample_alertmanager_payload)
        requests = processor.process_alertmanager_payload(sample_alertmanager_payload)
        assert len(requests) == 0

    def test_process_generic_payload(self):
        config = IntakeConfig()
        processor = IntakeProcessor(config)
        payload = {
            "alertname": "CustomAlert",
            "severity": "warning",
            "status": "firing",
            "message": "Something is wrong",
            "labels": {"service": "test-svc"},
        }
        requests = processor.process_generic_payload(payload)
        assert len(requests) >= 1
