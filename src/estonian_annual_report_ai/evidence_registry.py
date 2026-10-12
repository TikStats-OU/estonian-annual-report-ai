"""Persistent, local-only registry for source documents and accounting evidence."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class EvidenceRegistry:
    """Small JSON registry that remembers evidence state and avoids repeat requests."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("Evidence registry is unreadable; preserve and inspect it before replacing.") from exc
        else:
            self.data: dict[str, Any] = {
                "schema_version": 1,
                "documents": {},
                "facts": {},
                "issues": {},
                "decisions": {},
            }
        for section in ("documents", "facts", "issues", "decisions"):
            self.data.setdefault(section, {})

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def record_document(
        self,
        *,
        document_id: str,
        filename: str,
        document_type: str,
        period: str | None,
        status: str,
        sha256: str | None = None,
        transaction_count: int | None = None,
        processing_status: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = self.data["documents"].get(document_id, {})
        item = {
            **current,
            "filename": filename,
            "document_type": document_type,
            "period": period,
            "status": status,
            "sha256": sha256,
            "transaction_count": transaction_count,
            "processing_status": processing_status,
            "details": details or current.get("details", {}),
            "updated_at": self._now(),
        }
        self.data["documents"][document_id] = item
        self.save()
        return item

    def record_fact(
        self,
        fact_id: str,
        value: Any,
        *,
        status: str,
        source: str,
        detail: str | None = None,
    ) -> dict[str, Any]:
        item = {
            "value": value,
            "status": status,
            "source": source,
            "detail": detail,
            "updated_at": self._now(),
        }
        self.data["facts"][fact_id] = item
        self.save()
        return item

    def mark_unavailable(self, evidence_id: str, *, description: str, alternative: str) -> dict[str, Any]:
        return self.record_document(
            document_id=evidence_id,
            filename=description,
            document_type="unavailable_export",
            period=None,
            status="export_unavailable",
            details={"alternative_evidence": alternative},
        )

    def record_issue(
        self,
        issue_id: str,
        *,
        description: str,
        status: str = "open",
        material: bool = True,
        required_evidence: str | None = None,
        alternative: str | None = None,
        question: str | None = None,
    ) -> dict[str, Any]:
        previous = self.data["issues"].get(issue_id, {})
        item = {
            **previous,
            "description": description,
            "status": status,
            "material": material,
            "required_evidence": required_evidence,
            "alternative": alternative,
            "question": question,
            "updated_at": self._now(),
        }
        if status != "resolved":
            item.pop("resolution", None)
            item.pop("resolved_at", None)
        self.data["issues"][issue_id] = item
        self.save()
        return item

    def mark_question_asked(self, issue_id: str) -> None:
        issue = self.data["issues"].get(issue_id)
        if issue is None:
            raise KeyError(issue_id)
        issue["last_asked_at"] = self._now()
        self.save()

    def resolve_issue(self, issue_id: str, *, resolution: str) -> dict[str, Any]:
        issue = self.data["issues"].get(issue_id)
        if issue is None:
            raise KeyError(issue_id)
        issue.update(status="resolved", resolution=resolution, resolved_at=self._now())
        self.save()
        return issue

    def record_decision(self, issue_id: str, recommendation: dict[str, Any]) -> dict[str, Any]:
        """Persist a recommendation while keeping approval separate from posting."""
        current = self.data["decisions"].get(issue_id, {})
        unchanged = current.get("recommendation") == recommendation
        history = list(current.get("history", []))
        if current.get("recommendation") is not None and not unchanged:
            history.append({
                "recommendation": current["recommendation"],
                "status": current.get("status", "pending"),
                "updated_at": current.get("updated_at"),
            })
        item = {
            **current,
            "recommendation": recommendation,
            "status": current.get("status", "pending") if unchanged else "pending",
            "history": history,
            "updated_at": self._now(),
        }
        if not unchanged:
            item.pop("approved_by", None)
            item.pop("approved_at", None)
        self.data["decisions"][issue_id] = item
        self.save()
        return item

    def approve_decision(self, issue_id: str, *, approved_by: str) -> dict[str, Any]:
        decision = self.data["decisions"].get(issue_id)
        if decision is None:
            raise KeyError(issue_id)
        if not decision.get("recommendation", {}).get("approval_required"):
            raise ValueError("This recommendation does not require explicit approval.")
        decision.update(status="approved", approved_by=approved_by, approved_at=self._now())
        self.save()
        return decision

    def pending_questions(self) -> list[dict[str, Any]]:
        return [
            {"issue_id": key, **issue}
            for key, issue in self.data["issues"].items()
            if issue.get("status") == "open" and issue.get("question") and not issue.get("last_asked_at")
        ]

    def snapshot(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.data))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as target:
                json.dump(self.data, target, ensure_ascii=False, indent=2)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            temporary.unlink(missing_ok=True)
