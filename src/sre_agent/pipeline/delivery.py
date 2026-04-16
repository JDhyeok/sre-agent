"""Delivery module — Teams Incoming Webhook integration with log fallback.

Sends incident notifications to Microsoft Teams via Incoming Webhooks
(MessageCard format). When no webhook URL is configured, the same payload
is written to the application logger so the PoC remains testable without
a Teams tenant — set ``delivery.teams_webhook_url`` in settings.yaml to
switch over to real delivery.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_client = httpx.Client(timeout=15.0)


def _log_card(card: dict[str, Any]) -> None:
    """Render a MessageCard to the log when Teams is not configured."""
    summary = card.get("summary", "(no summary)")
    sections = card.get("sections") or []
    parts: list[str] = [f"[teams-disabled] {summary}"]

    for section in sections:
        title = section.get("activityTitle") or ""
        subtitle = section.get("activitySubtitle") or ""
        if title:
            parts.append(f"  {title}")
        if subtitle:
            parts.append(f"    {subtitle}")
        for fact in section.get("facts") or []:
            parts.append(f"    - {fact.get('name', '')}: {fact.get('value', '')}")
        text = section.get("text") or ""
        if text:
            parts.append("    ---")
            for line in text.splitlines():
                parts.append(f"    {line}")

    actions = card.get("potentialAction") or []
    if actions:
        parts.append("  links:")
        for action in actions:
            name = action.get("name", "")
            for target in action.get("targets") or []:
                uri = target.get("uri", "")
                parts.append(f"    - {name}: {uri}")

    logger.info("\n".join(parts))


def _post_card(webhook_url: str, card: dict[str, Any]) -> bool:
    """Send a MessageCard to Teams, or log it if no webhook URL is configured.

    Returns True if the message was delivered (or successfully logged as a
    fallback). Returns False only on a hard delivery error to a configured
    webhook.
    """
    if not webhook_url:
        _log_card(card)
        return True
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
    # Strip visualization JSON blocks before sending to Teams/log
    import re as _re
    clean_report = _re.sub(
        r"###\s*시각화\s*데이터.*?(?=\n###\s|\Z)", "", report, flags=_re.DOTALL,
    )
    for _tag in ("visualization_json", "metrics_json"):
        clean_report = _re.sub(rf"```\s*{_tag}.*?```", "", clean_report, flags=_re.DOTALL)
    clean_report = clean_report.strip()

    if len(clean_report) > 5000:
        report_truncated = clean_report[:4800] + "\n\n... (리포트가 잘림. 전체 내용은 웹 UI에서 확인)"
    else:
        report_truncated = clean_report

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


def send_rca_complete(
    webhook_url: str,
    incident_id: str,
    rca_report: str,
    elapsed: float = 0.0,
    server_base_url: str = "",
) -> bool:
    """Send a notification that RCA analysis has completed."""
    text = rca_report[:4800]
    if len(rca_report) > 4800:
        text += "\n\n> 리포트가 잘림. 전체 내용은 웹 UI에서 확인하세요."

    card = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "0078D7",
        "summary": f"SRE Agent RCA 완료 — {incident_id}",
        "sections": [
            {
                "activityTitle": f"🔍 RCA 분석 완료 — {incident_id}",
                "activitySubtitle": f"소요 시간: {elapsed:.1f}s",
                "text": text,
                "markdown": True,
            }
        ],
    }

    if server_base_url:
        card["potentialAction"] = [
            {
                "@type": "OpenUri",
                "name": "📄 상세 보기",
                "targets": [{"os": "default", "uri": f"{server_base_url}/approve/{incident_id}"}],
            },
        ]

    return _post_card(webhook_url, card)
