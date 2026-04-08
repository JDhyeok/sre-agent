"""Delivery module — Teams Incoming Webhook integration.

Sends incident notifications to Microsoft Teams channels via Incoming Webhooks.
Uses MessageCard format for broad compatibility.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_client = httpx.Client(timeout=15.0)


def _post_card(webhook_url: str, card: dict[str, Any]) -> bool:
    """Send a MessageCard to Teams. Returns True on success."""
    if not webhook_url:
        logger.warning("Teams webhook URL not configured — skipping notification")
        return False
    try:
        resp = _client.post(webhook_url, json=card)
        if resp.status_code == 200:
            logger.info("Teams notification sent successfully")
            return True
        logger.error("Teams webhook returned %d: %s", resp.status_code, resp.text[:200])
        return False
    except httpx.HTTPError as e:
        logger.exception("Failed to send Teams notification: %s", e)
        return False


def send_alert_received(webhook_url: str, incident_id: str, alert_summary: str) -> bool:
    """Send a 'analysis started' notification to Teams."""
    card = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "FFA500",
        "summary": f"SRE Agent: {incident_id}",
        "sections": [
            {
                "activityTitle": f"🔔 알림 수신 — {incident_id}",
                "activitySubtitle": "SRE Agent 자동 분석 시작",
                "facts": [
                    {"name": "알림", "value": alert_summary},
                    {"name": "상태", "value": "⏳ 분석 중..."},
                ],
                "markdown": True,
            }
        ],
    }
    return _post_card(webhook_url, card)


def send_progress(webhook_url: str, incident_id: str, stage: str, elapsed: float) -> bool:
    """Send a progress update to Teams."""
    card = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "0078D7",
        "summary": f"SRE Agent: {incident_id} — {stage}",
        "sections": [
            {
                "activityTitle": f"⏳ {incident_id} — {stage}",
                "activitySubtitle": f"경과 시간: {elapsed:.1f}s",
                "markdown": True,
            }
        ],
    }
    return _post_card(webhook_url, card)


def send_report(
    webhook_url: str,
    incident_id: str,
    report: str,
    elapsed: float,
    has_action: bool = False,
    server_base_url: str = "",
) -> bool:
    """Send the final analysis report to Teams."""
    if len(report) > 5000:
        report_truncated = report[:4800] + "\n\n... (리포트가 잘림. 전체 내용은 웹 UI에서 확인)"
    else:
        report_truncated = report

    theme = "FF0000" if "critical" in report.lower() else "00CC00"

    card: dict[str, Any] = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": theme,
        "summary": f"SRE Agent 분석 완료 — {incident_id}",
        "sections": [
            {
                "activityTitle": f"📋 분석 완료 — {incident_id}",
                "activitySubtitle": f"소요 시간: {elapsed:.1f}s",
                "text": report_truncated,
                "markdown": True,
            }
        ],
    }

    actions: list[dict[str, Any]] = []

    if has_action and server_base_url:
        actions.append({
            "@type": "OpenUri",
            "name": "✅ 조치 승인",
            "targets": [{"os": "default", "uri": f"{server_base_url}/approve/{incident_id}"}],
        })

    if server_base_url:
        actions.append({
            "@type": "OpenUri",
            "name": "📄 상세 보기",
            "targets": [{"os": "default", "uri": f"{server_base_url}/incidents/{incident_id}"}],
        })

    if actions:
        card["potentialAction"] = actions

    return _post_card(webhook_url, card)


def send_action_result(
    webhook_url: str,
    incident_id: str,
    success: bool,
    message: str,
) -> bool:
    """Send action execution result to Teams."""
    if success:
        theme = "00CC00"
        icon = "✅"
        title = "조치 완료"
    else:
        theme = "FF0000"
        icon = "🚨"
        title = "조치 실패"

    card = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": theme,
        "summary": f"SRE Agent {title} — {incident_id}",
        "sections": [
            {
                "activityTitle": f"{icon} {title} — {incident_id}",
                "text": message,
                "markdown": True,
            }
        ],
    }
    return _post_card(webhook_url, card)
