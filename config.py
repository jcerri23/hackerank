"""Static configuration: paths, output contract enums and canonical orderings."""

from __future__ import annotations

from pathlib import Path

# --------------------------------------------------------------------------- paths
REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "dataset"
EVIDENCE_DIR = DATASET_DIR / "evidence"
OUTPUT_PATH = REPO_ROOT / "output.csv"
CACHE_DIR = REPO_ROOT / ".cache"  # OCR / model caches, never submitted

# --------------------------------------------------------------------------- statuses
COMPLETE = "COMPLETE"
INCOMPLETE = "INCOMPLETE"
NOT_APPLICABLE = "NOT_APPLICABLE"

FINDING, REMEDIATION, VERIFICATION = "FINDING", "REMEDIATION", "VERIFICATION"
PHASE_ORDER = {FINDING: 0, REMEDIATION: 1, VERIFICATION: 2}

# operation -> (phase, state) ; the list order is the canonical in-phase order
OPERATIONS: list[tuple[str, str, str]] = [
    ("REPORT_REMAINING_RECORD", FINDING, "PRESENT"),
    ("REPORT_EXPOSED_PII", FINDING, "PRESENT"),
    ("REPORT_ACTIVE_DATA_FLOW", FINDING, "ACTIVE"),
    ("REPORT_RESTORE_RISK", FINDING, "ACTIVE"),
    ("DISABLE_DATA_FLOW", REMEDIATION, "DISABLED"),
    ("BLOCK_RESTORE", REMEDIATION, "BLOCKED"),
    ("DELETE_RECORD", REMEDIATION, "ABSENT"),
    ("REDACT_RECORD", REMEDIATION, "REDACTED"),
    ("ANONYMIZE_RECORD", REMEDIATION, "ANONYMIZED"),
    ("VERIFY_RECORD_ABSENT", VERIFICATION, "ABSENT"),
    ("VERIFY_RECORD_PRESENT", VERIFICATION, "PRESENT"),
    ("VERIFY_RECORD_REDACTED", VERIFICATION, "REDACTED"),
    ("VERIFY_RECORD_ANONYMIZED", VERIFICATION, "ANONYMIZED"),
    ("VERIFY_DATA_FLOW_DISABLED", VERIFICATION, "DISABLED"),
    ("VERIFY_RESTORE_BLOCKED", VERIFICATION, "BLOCKED"),
]
OP_PHASE = {op: phase for op, phase, _ in OPERATIONS}
OP_STATE = {op: state for op, _, state in OPERATIONS}
OP_ORDER = {op: i for i, (op, _, _) in enumerate(OPERATIONS)}

# --------------------------------------------------------------------------- systems
# resource-id prefix -> owning system.  Longest prefix wins.
RESOURCE_PREFIX_SYSTEM: dict[str, str] = {
    "account_": "account_service",
    "search_": "search_service",
    "analytics_": "analytics_platform",
    "support_": "support_platform",
    "export_": "export_service",
    "data_flow_": "integration_service",
    "backup_": "backup_service",
    "audit_record_": "security_service",
    "tombstone_": "identity_service",
    "legal_hold_": "legal_service",
    "cache_": "cache_service",
    "email_delivery_": "email_delivery_service",
    "marketing_": "marketing_platform",
    "telemetry_": "telemetry_service",
    "object_": "object_storage",
}

# data categories from deletion_rules.csv
USER_PROFILE = "USER_PROFILE"
SECONDARY_IDENTIFIER = "SECONDARY_IDENTIFIER"
SEARCH_INDEX_ENTRY = "SEARCH_INDEX_ENTRY"
CACHE_ENTRY = "CACHE_ENTRY"
SUPPORT_MESSAGE = "SUPPORT_MESSAGE"
SUPPORT_ATTACHMENT = "SUPPORT_ATTACHMENT"
ANALYTICS_PROFILE = "ANALYTICS_PROFILE"
PRODUCT_TELEMETRY = "PRODUCT_TELEMETRY"
USER_EXPORT = "USER_EXPORT"
MARKETING_PROFILE = "MARKETING_PROFILE"
EMAIL_DELIVERY_LOG = "EMAIL_DELIVERY_LOG"
SECURITY_AUDIT_LOG = "SECURITY_AUDIT_LOG"
LEGAL_HOLD_RECORD = "LEGAL_HOLD_RECORD"
BACKUP_ARCHIVE = "BACKUP_ARCHIVE"
ACCOUNT_TOMBSTONE = "ACCOUNT_TOMBSTONE"
DATA_FLOW = "DATA_FLOW"  # not a rules category: a recreation path

# default category per system when the evidence does not say more
SYSTEM_DEFAULT_CATEGORY: dict[str, str] = {
    "account_service": USER_PROFILE,
    "search_service": SEARCH_INDEX_ENTRY,
    "analytics_platform": ANALYTICS_PROFILE,
    "support_platform": SUPPORT_MESSAGE,
    "export_service": USER_EXPORT,
    "integration_service": DATA_FLOW,
    "backup_service": BACKUP_ARCHIVE,
    "security_service": SECURITY_AUDIT_LOG,
    "identity_service": ACCOUNT_TOMBSTONE,
    "legal_service": LEGAL_HOLD_RECORD,
    "cache_service": CACHE_ENTRY,
    "email_delivery_service": EMAIL_DELIVERY_LOG,
    "marketing_platform": MARKETING_PROFILE,
    "telemetry_service": PRODUCT_TELEMETRY,
    "object_storage": SUPPORT_ATTACHMENT,
}


def system_for_resource(resource_id: str) -> str | None:
    best = None
    for prefix, system in RESOURCE_PREFIX_SYSTEM.items():
        if resource_id.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, system)
    return best[1] if best else None
