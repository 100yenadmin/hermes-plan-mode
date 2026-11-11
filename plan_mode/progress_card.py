"""Best-effort progress messages; only the first post needs the hook's context."""

from collections import OrderedDict
from dataclasses import dataclass
import logging
import threading
import time
from typing import Any


log = logging.getLogger(__name__)


@dataclass
class _Card:
    last_text: str
    last_seq: int
    handle: Any = None
    pending: str | None = None
    posting: bool = True
    failed: bool = False


class ProgressCards:
    """Keep a bounded set of handles and coalesce edits on one daemon worker."""

    def __init__(self):
        self._cards = OrderedDict()
        self._condition = threading.Condition()
        self._worker = None
        self._editing = 0

    def update(self, card_key, text, post, seq):
        try:
            if not text:
                return
            with self._condition:
                card = self._cards.get(card_key)
                if card is not None:
                    if seq <= card.last_seq:
                        return
                    card.last_seq = seq
                    if card.failed or card.last_text == text:
                        return
                    card.last_text = card.pending = text
                    if not card.posting:
                        self._start_worker()
                    self._condition.notify_all()
                    return
                card = _Card(text, seq)
                self._cards[card_key] = card
                while len(self._cards) > 256:
                    self._cards.popitem(last=False)

            # The host binds the destination to the calling hook, not the worker.
            try:
                handle = post(text)
                ok = bool(getattr(handle, "ok", False))
            except Exception as exc:
                log.debug("Progress card post failed (%s)", type(exc).__name__)
                handle, ok = None, False
            with self._condition:
                card.posting = False
                card.handle, card.failed = handle if ok else None, not ok
                if not ok:
                    card.pending = None
                    log.debug("Progress card post was not successful")
                elif card.pending is not None and self._cards.get(card_key) is card:
                    self._start_worker()
                self._condition.notify_all()
        except Exception as exc:
            log.debug("Progress card update failed (%s)", type(exc).__name__)

    def _start_worker(self):
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run, name="plan-mode-progress-card", daemon=True)
            self._worker.start()

    def _run(self):
        while True:
            with self._condition:
                card = next((card for card in self._cards.values()
                             if not card.posting and not card.failed and card.pending is not None), None)
                if card is None:
                    self._condition.wait()
                    continue
                text, card.pending = card.pending, None
                self._editing += 1
            try:
                result = card.handle.edit(text)
                if not isinstance(result, dict) or not result.get("ok"):
                    log.debug("Progress card edit was not successful")
            except Exception as exc:
                log.debug("Progress card edit failed (%s)", type(exc).__name__)
            finally:
                with self._condition:
                    self._editing -= 1
                    self._condition.notify_all()

    def drain(self, timeout):
        """Wait for posts, pending edits and in-flight edits; return False on timeout."""
        try:
            deadline = time.monotonic() + timeout
            with self._condition:
                while self._editing or any(card.posting or card.pending is not None for card in self._cards.values()):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._condition.wait(remaining)
                return True
        except Exception as exc:
            log.debug("Progress card drain failed (%s)", type(exc).__name__)
            return False
