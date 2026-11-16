"""Incident monitoring with thresholds, single-alert-per-window gating,
and safe notification dispatch with retry and audit trails."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from openjarvis.routines.quiet_hours import QuietHoursManager

logger = logging.getLogger(__name__)


class ComparisonOperator(str, Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"


@dataclass
class ThresholdRule:
    """Rule defining when a metric breach constitutes an incident."""

    name: str
    metric_name: str
    operator: ComparisonOperator
    threshold: float
    incident_window_seconds: float = 300.0  # 5 minute cooldown window by default
    severity: str = "warning"
    description: str = ""

    def evaluate(self, value: float) -> bool:
        """Return True if value breaches the threshold."""
        op = self.operator
        thresh = self.threshold
        if op == ComparisonOperator.GT:
            return value > thresh
        elif op == ComparisonOperator.GTE:
            return value >= thresh
        elif op == ComparisonOperator.LT:
            return value < thresh
        elif op == ComparisonOperator.LTE:
            return value <= thresh
        elif op == ComparisonOperator.EQ:
            return abs(value - thresh) < 1e-6
        return False


@dataclass
class Incident:
    """An active or resolved threshold incident."""

    id: str
    rule_name: str
    metric_name: str
    trigger_value: float
    threshold: float
    severity: str
    triggered_at: float
    window_expires_at: float
    is_active: bool = True
    resolved_at: Optional[float] = None
    alert_count: int = 1


class ThresholdMonitor:
    """Monitors metric streams and ensures threshold triggers fire exactly once per incident window."""

    def __init__(self, rules: Optional[List[ThresholdRule]] = None) -> None:
        self._rules: Dict[str, ThresholdRule] = {r.name: r for r in (rules or [])}
        self._incidents: Dict[str, Incident] = {}
        self._lock = threading.RLock()

    def add_rule(self, rule: ThresholdRule) -> None:
        with self._lock:
            self._rules[rule.name] = rule

    def get_rule(self, name: str) -> Optional[ThresholdRule]:
        with self._lock:
            return self._rules.get(name)

    def evaluate_metric(
        self,
        rule_name: str,
        value: float,
        timestamp: Optional[float] = None,
    ) -> Optional[Incident]:
        """Evaluate a metric against a rule.

        Returns an Incident if a NEW alert should be triggered.
        Returns None if no breach, or if the incident is already active within its incident window.
        """
        now = timestamp if timestamp is not None else time.time()

        with self._lock:
            rule = self._rules.get(rule_name)
            if not rule:
                raise KeyError(f"Rule not found: {rule_name}")

            is_breach = rule.evaluate(value)
            existing = self._incidents.get(rule_name)

            if is_breach:
                if existing and existing.is_active:
                    # Still within the incident window: suppress duplicate alert
                    if now < existing.window_expires_at:
                        existing.alert_count += 1
                        logger.debug(
                            "Incident for %s active until %.1f; suppressing duplicate alert (count: %d)",
                            rule_name,
                            existing.window_expires_at,
                            existing.alert_count,
                        )
                        return None
                    else:
                        # Prior window expired, open a fresh window
                        existing.is_active = False

                # Trigger new incident
                incident_id = f"inc-{uuid.uuid4().hex[:10]}"
                incident = Incident(
                    id=incident_id,
                    rule_name=rule.name,
                    metric_name=rule.metric_name,
                    trigger_value=value,
                    threshold=rule.threshold,
                    severity=rule.severity,
                    triggered_at=now,
                    window_expires_at=now + rule.incident_window_seconds,
                    is_active=True,
                    alert_count=1,
                )
                self._incidents[rule_name] = incident
                logger.info(
                    "Threshold breached for rule '%s' (value=%.2f, thresh=%.2f); firing alert %s",
                    rule_name,
                    value,
                    rule.threshold,
                    incident_id,
                )
                return incident
            else:
                # Value returned to normal: resolve active incident
                if existing and existing.is_active:
                    existing.is_active = False
                    existing.resolved_at = now
                    logger.info("Incident '%s' for rule '%s' resolved", existing.id, rule_name)
                return None

    def get_active_incident(self, rule_name: str) -> Optional[Incident]:
        with self._lock:
            inc = self._incidents.get(rule_name)
            return inc if (inc and inc.is_active) else None


class NotificationStatus(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    RETRIED = "retried"
    QUEUED_QUIET_HOURS = "queued_quiet_hours"
    PERMISSION_DENIED = "permission_denied"


@dataclass
class NotificationMessage:
    """A notification dispatched to user or external systems."""

    id: str
    channel: str  # "voice", "system", "webhook", "email"
    title: str
    content: str
    recipient: str = "user"
    status: NotificationStatus = NotificationStatus.PENDING
    attempts: int = 0
    max_retries: int = 3
    created_at: float = field(default_factory=time.time)
    delivered_at: Optional[float] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class ExternalConnector:
    """Enforces scopes and least-privilege permissions for external connectors."""

    def __init__(
        self,
        name: str,
        allowed_scopes: Set[str],
        base_url: str = "",
    ) -> None:
        self.name = name
        self.allowed_scopes = set(allowed_scopes)
        self.base_url = base_url

    def check_permission(self, required_scope: str) -> bool:
        """Verify whether this connector possesses the required scope."""
        return required_scope in self.allowed_scopes


class NotificationDispatcher:
    """Dispatches notifications respecting quiet hours, retrying failures safely,

    and recording an audit trail of every external action.
    """

    def __init__(
        self,
        quiet_hours: Optional[QuietHoursManager] = None,
        max_retries: int = 3,
    ) -> None:
        self.quiet_hours = quiet_hours or QuietHoursManager()
        self.max_retries = max_retries
        self._audit_trail: List[Dict[str, Any]] = []
        self._lock = threading.RLock()

    def send(
        self,
        message: NotificationMessage,
        sender_fn: Optional[Callable[[NotificationMessage], bool]] = None,
        connector: Optional[ExternalConnector] = None,
        required_scope: Optional[str] = None,
        now_ts: Optional[float] = None,
    ) -> NotificationMessage:
        """Send a notification safely with quiet hours check and retry logic."""
        now = now_ts if now_ts is not None else time.time()

        with self._lock:
            # 1. Permission check
            if connector is not None and required_scope is not None:
                if not connector.check_permission(required_scope):
                    message.status = NotificationStatus.PERMISSION_DENIED
                    message.error = f"Permission denied: connector '{connector.name}' lacks scope '{required_scope}'"
                    self._record_audit(message, "permission_denied", message.error)
                    return message

            # 2. Quiet hours check
            if message.channel == "voice" and self.quiet_hours.should_suppress_voice(True, now):
                message.status = NotificationStatus.QUEUED_QUIET_HOURS
                self.quiet_hours.queue_notification(
                    {
                        "notification_id": message.id,
                        "title": message.title,
                        "content": message.content,
                        "channel": message.channel,
                    }
                )
                self._record_audit(message, "queued_quiet_hours", "Suppressed by quiet hours")
                return message

            # 3. Transmission with safe retry
            attempts = 0
            last_err = ""
            while attempts <= message.max_retries:
                attempts += 1
                message.attempts = attempts
                try:
                    if sender_fn:
                        success = sender_fn(message)
                        if not success:
                            raise RuntimeError("Sender function reported delivery failure")
                    # If sender_fn is None, simulate successful delivery
                    message.status = NotificationStatus.DELIVERED
                    message.delivered_at = now
                    message.error = None
                    self._record_audit(message, "delivered", "Success")
                    return message
                except Exception as exc:
                    last_err = str(exc)
                    logger.warning(
                        "Notification %s attempt %d/%d failed: %s",
                        message.id,
                        attempts,
                        message.max_retries + 1,
                        exc,
                    )
                    if attempts <= message.max_retries:
                        message.status = NotificationStatus.RETRIED
                        time.sleep(0.01)  # small backoff in test / async

            message.status = NotificationStatus.FAILED
            message.error = f"Failed after {attempts} attempts: {last_err}"
            self._record_audit(message, "failed", message.error)
            return message

    def _record_audit(
        self,
        message: NotificationMessage,
        action: str,
        detail: str,
    ) -> None:
        self._audit_trail.append(
            {
                "notification_id": message.id,
                "channel": message.channel,
                "title": message.title,
                "recipient": message.recipient,
                "action": action,
                "status": message.status.value,
                "attempts": message.attempts,
                "timestamp": time.time(),
                "detail": detail,
            }
        )

    def get_audit_trail(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._audit_trail)
