"""Durable approvals stored independently of session state."""

import logging
import os
import secrets

from .approval import plan_digest


APPROVED_PLANS_KEY = "approved-plans"
PLAN_HISTORY_KEY = "plan-history"
_LOG = logging.getLogger(__name__)


def remember(store, state, now, *, digest=None, **changes):
    """Record an approval or update an existing approval for the same revision."""
    try:
        review = state.get("edit_review") or {}
        path = state.get("approved_path") or review.get("path")
        if not path or (not digest and not changes):
            return
        key = os.path.realpath(path)
        value = store.get(APPROVED_PLANS_KEY, {})
        records = dict(value) if isinstance(value, dict) else {}
        approval_id = ""
        if digest:
            # Each approval gets its own id: path, revision and a one-second timestamp can repeat across sessions.
            approval_id = secrets.token_hex(8)
            record = dict(plans_dir=os.path.realpath(state["plans_dir"]), path=key,
                          revision=state.get("approved_revision"), digest=digest,
                          snapshot=state.get("approved_snapshot") or "", approved_at=state["approved_at"],
                          approval_id=approval_id, edit_review=False, status="approved", progress={})
        else:
            old = records.get(key)
            mine = state.get("approval_id") or review.get("approval_id")
            if (not isinstance(old, dict) or old.get("status") != "approved"
                    or not mine or old.get("approval_id") != mine):
                return
            record = {**old, **changes}
        records.pop(key, None)
        records[key] = dict(record, updated_at=now)
        while len(records) > 200:
            records.pop(next(iter(records)))
        # No CAS: a concurrent finish or progress write may be lost. The host locks each call itself.
        store.set(APPROVED_PLANS_KEY, records)
        if approval_id:
            state["approval_id"] = approval_id
    except Exception:
        _LOG.debug("Could not store the durable plan approval", exc_info=True)


def _safe_path(path, root, is_inside):
    return (isinstance(path, str) and is_inside(path, root) and not os.path.islink(path)
            and os.path.realpath(path) == path)


def _matches(paths, name):
    if not name:
        return paths[:1]
    exact = [path for path in paths if os.path.basename(path) == name]
    stem = [path for path in paths if os.path.basename(path) == name + ".md"]
    return exact or stem or [path for path in paths if os.path.basename(path).startswith(name)]


def _valid_record(record, path):
    revision = record.get("revision")
    progress = record.get("progress")
    return (record.get("path") == path and "revision" in record
            and (revision is None or type(revision) is int and revision >= 0)
            and isinstance(record.get("edit_review"), bool)
            and record.get("status") in ("approved", "finished")
            and isinstance(record.get("snapshot"), str) and isinstance(record.get("approval_id"), str) and record["approval_id"]
            and isinstance(record.get("approved_at"), str) and record["approved_at"]
            and isinstance(record.get("updated_at"), str)
            and (progress == {} or isinstance(progress, dict)
                 and type(progress.get("done")) is int and type(progress.get("total")) is int
                 and 0 <= progress["done"] <= progress["total"] and isinstance(progress.get("label"), str)))


def find(store, plans_dir, name, is_inside):
    """Return a verified unfinished approval or a user-facing refusal."""
    missing = f"No approved plan to resume in {plans_dir}. /planmode list shows the plans here."
    if os.path.isabs(name) or any(part in name for part in ("/", "\\")) or name in (".", ".."):
        return None, missing
    try:
        records = store.get(APPROVED_PLANS_KEY, {})
        if not isinstance(records, dict):
            return None, missing
        paths = [path for path, record in reversed(records.items())
                 if isinstance(record, dict) and record.get("plans_dir") == plans_dir
                 and _safe_path(path, plans_dir, is_inside)
                 and (name or (record.get("status") == "approved" and os.path.isfile(path)
                               and _valid_record(record, path)))]
        matches = _matches(paths, name)
        if len(matches) > 1:
            return None, "Plan name is ambiguous: " + ", ".join(os.path.basename(path) for path in matches[:5])
        if matches:
            path = matches[0]
            record = records[path]
            if not _valid_record(record, path):
                return None, missing
            if record["status"] == "finished":
                return None, f"{plan_title(path, record['revision'])} is finished (/planmode list)."
        else:
            history = store.get(PLAN_HISTORY_KEY, {})
            legacy = [path for path, item in reversed(history.items()) if path not in records
                      and isinstance(item, dict) and item.get("status") == "approved"
                      and _safe_path(path, plans_dir, is_inside)
                      and (name or os.path.isfile(path))] if isinstance(history, dict) else []
            matches = _matches(legacy, name)
            if len(matches) != 1:
                return None, missing
            path, record = matches[0], {}

        snapshot = record.get("snapshot")
        approved_dir = os.path.join(plans_dir, "approved")
        changed = (f"{os.path.basename(path)} changed after it was approved (or was approved before resume existed), "
                   "so it needs a new approval: start plan mode and submit it again.")
        if record.get("digest") and (not snapshot or (not os.path.islink(approved_dir)
                                                     and _safe_path(snapshot, approved_dir, is_inside))):
            approved_text = snapshot or path
            try:
                if os.path.isfile(approved_text) and plan_digest(approved_text)[0] == record["digest"]:
                    return record, ""
            except (OSError, ValueError):
                pass
        return None, changed
    except Exception:
        _LOG.debug("Could not read the durable plan approval", exc_info=True)
        return None, missing


def plan_title(path, revision):
    return f"{os.path.basename(path or 'plan')} rev {revision if revision is not None else '?'}"
