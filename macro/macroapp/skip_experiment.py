"""Strictly-inactive SKIP experiment scheduling and foreground invariants.

This module deliberately has no GUI or Windows imports so its decision logic can
be regression-tested on any platform.  The caller supplies the foreground HWND
reader and the concrete input action.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import threading
import time
from typing import Callable, Optional, Sequence


_LEARNING_FILE_LOCK = threading.Lock()
LEARNING_SCHEMA_VERSION = 4


def load_device_learning(path: Path, device_id: str) -> dict[str, str]:
    """Load validated A/S candidates learned for one device.

    Corrupt, missing, or older files are treated as empty so SKIP handling can
    always fall back to the safe candidate sweep.
    """

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("version") != LEARNING_SCHEMA_VERSION:
            return {}
        devices = payload.get("devices", {})
        device = devices.get(str(device_id), {})
        if not isinstance(device, dict):
            return {}
        result = {}
        for variant in ("a", "s"):
            candidate = device.get(variant)
            if isinstance(candidate, str) and candidate.strip():
                result[variant] = candidate.strip().lower()
        return result
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def save_device_learning(
    path: Path,
    device_id: str,
    variant: str,
    candidate: str,
) -> bool:
    """Atomically persist one confirmed candidate for one device."""

    variant = str(variant).strip().lower()
    candidate = str(candidate).strip().lower()
    device_id = str(device_id).strip()
    if variant not in {"a", "s"} or not candidate or not device_id:
        return False

    path = Path(path)
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        with _LEARNING_FILE_LOCK:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if (
                    not isinstance(payload, dict)
                    or payload.get("version") != LEARNING_SCHEMA_VERSION
                ):
                    payload = {}
            except (OSError, ValueError, TypeError):
                payload = {}
            devices = payload.get("devices")
            if not isinstance(devices, dict):
                devices = {}
                payload["devices"] = devices
            device = devices.get(device_id)
            if not isinstance(device, dict):
                device = {}
                devices[device_id] = device
            payload["version"] = LEARNING_SCHEMA_VERSION
            device[variant] = candidate
            device["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        return True
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def remove_device_learning(
    path: Path,
    device_id: str,
    variant: str,
) -> bool:
    """Atomically forget one invalidated candidate, if it is present."""

    variant = str(variant).strip().lower()
    device_id = str(device_id).strip()
    if variant not in {"a", "s"} or not device_id:
        return False

    path = Path(path)
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        with _LEARNING_FILE_LOCK:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                return True
            if (
                not isinstance(payload, dict)
                or payload.get("version") != LEARNING_SCHEMA_VERSION
            ):
                return True
            devices = payload.get("devices")
            if not isinstance(devices, dict):
                return True
            device = devices.get(device_id)
            if not isinstance(device, dict) or variant not in device:
                return True
            device.pop(variant, None)
            device["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        return True
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False


@dataclass(frozen=True)
class GuardedActionResult:
    """Result of one input while continuously watching the foreground window."""

    attempted: bool
    action_ok: bool
    foreground_before: int
    foreground_after: int
    foreground_samples: tuple[int, ...]
    invariant_ok: bool
    reason: str
    elapsed_seconds: float


@dataclass(frozen=True)
class SkipAttempt:
    candidate: str
    attempted_at: float
    guard: GuardedActionResult


@dataclass(frozen=True)
class SkipOutcome:
    status: str
    candidate: Optional[str]
    latency_seconds: Optional[float] = None
    learned: Optional[str] = None
    detail: str = ""


def run_guarded_inactive_action(
    action: Callable[[], bool],
    foreground_reader: Callable[[], int],
    target_hwnd: int,
    *,
    poll_seconds: float = 0.005,
) -> GuardedActionResult:
    """Run ``action`` only while the target is inactive and watch the invariant.

    A background experiment is valid when the game HWND is never foreground
    before, throughout or after the input.  The user may switch between other
    applications while an attempt is running; that does not activate the game
    and must not quarantine an otherwise safe candidate.  Sampling runs on a
    separate thread because most useful candidates hold a key for about a
    second and therefore block the caller.
    """

    started = time.monotonic()
    target_hwnd = int(target_hwnd or 0)
    before = int(foreground_reader() or 0)
    if not target_hwnd:
        return GuardedActionResult(
            False, False, before, before, (before,), False,
            "missing_target", time.monotonic() - started,
        )
    if not before:
        return GuardedActionResult(
            False, False, before, before, (before,), False,
            "missing_foreground", time.monotonic() - started,
        )
    if before == target_hwnd:
        return GuardedActionResult(
            False, False, before, before, (before,), False,
            "target_already_foreground", time.monotonic() - started,
        )

    stop = threading.Event()
    ready = threading.Event()
    samples = [before]

    def sample_foreground() -> int:
        value = int(foreground_reader() or 0)
        # The invariant only needs transitions.  Keeping every 5 ms sample would
        # make a one-second hold produce hundreds of duplicate JSON values.
        if value != samples[-1]:
            samples.append(value)
        return value

    def monitor() -> None:
        # Signal only after the watcher thread is alive, so the action cannot
        # race ahead of foreground monitoring on a busy machine.
        ready.set()
        while not stop.wait(max(0.001, poll_seconds)):
            sample_foreground()

    watcher = threading.Thread(
        target=monitor,
        name="skip-foreground-guard",
        daemon=True,
    )
    watcher.start()
    if not ready.wait(timeout=max(0.05, poll_seconds * 10)):
        stop.set()
        watcher.join(timeout=0.05)
        return GuardedActionResult(
            False,
            False,
            before,
            int(foreground_reader() or 0),
            tuple(samples),
            False,
            "foreground_monitor_not_ready",
            time.monotonic() - started,
        )
    action_ok = False
    reason = "action_failed"
    try:
        action_ok = bool(action())
        reason = "ok" if action_ok else "action_failed"
    except Exception as exc:  # noqa: BLE001 - result must preserve the experiment loop
        reason = f"action_error:{type(exc).__name__}"
    finally:
        sample_foreground()
        stop.set()
        watcher.join(timeout=max(0.05, poll_seconds * 4))
        after = int(foreground_reader() or 0)
        if after != samples[-1]:
            samples.append(after)

    invariant_ok = target_hwnd not in samples
    if not invariant_ok:
        reason = "target_became_foreground"

    return GuardedActionResult(
        attempted=True,
        action_ok=action_ok,
        foreground_before=before,
        foreground_after=after,
        foreground_samples=tuple(samples),
        invariant_ok=invariant_ok,
        reason=reason,
        elapsed_seconds=time.monotonic() - started,
    )


class SkipExperimentTracker:
    """Cycle safe candidates, attribute prompt disappearance, and learn winners."""

    def __init__(
        self,
        candidates: Sequence[str],
        *,
        result_window_seconds: float,
        attempt_gap_seconds: float,
        confirm_successes: int,
        learned: Optional[str] = None,
        control_seconds: float = 0.0,
        exit_confirm_seconds: float = 0.0,
        progressive_control: bool = False,
        control_ramp_successes: Optional[int] = None,
    ) -> None:
        normalized = tuple(dict.fromkeys(
            str(name).strip().lower() for name in candidates if str(name).strip()
        ))
        if not normalized:
            raise ValueError("at least one SKIP candidate is required")
        self.candidates = normalized
        self.result_window_seconds = max(0.05, float(result_window_seconds))
        self.attempt_gap_seconds = max(0.0, float(attempt_gap_seconds))
        self.confirm_successes = max(1, int(confirm_successes))
        self.control_seconds = max(0.0, float(control_seconds))
        self.exit_confirm_seconds = max(0.0, float(exit_confirm_seconds))
        self.progressive_control = bool(progressive_control)
        ramp_successes = (
            self.confirm_successes
            if control_ramp_successes is None
            else max(1, int(control_ramp_successes))
        )
        self.control_ramp_successes = min(
            self.confirm_successes,
            ramp_successes,
        )
        learned_name = learned.strip().lower() if learned else None
        # Persisted/manual values may outlive a safety decision. Never restore
        # a method that is no longer present in the explicitly safe candidates.
        self.learned = (
            learned_name if learned_name in self.candidates else None
        )
        self.pending: Optional[SkipAttempt] = None
        self.preferred: Optional[str] = self.learned
        self.quarantined: set[str] = set()
        self.success_counts: dict[str, int] = {}
        self.index = 0
        self.last_attempt_at = float("-inf")
        self.control_started_at: Optional[float] = None
        self.control_passed = self.control_seconds <= 0.0
        self.episode_control_seconds = self.control_seconds
        self.absence_started_at: Optional[float] = None

    def _priorities(self) -> list[str]:
        priorities = []
        for name in (self.learned, self.preferred):
            if name and name not in priorities:
                priorities.append(name)
        priorities.extend(
            self.candidates[(self.index + offset) % len(self.candidates)]
            for offset in range(len(self.candidates))
        )
        return [
            candidate
            for candidate in priorities
            if candidate not in self.quarantined
        ]

    def begin_episode(self, now: float) -> None:
        """Start the no-input control period for one visible prompt."""

        if self.control_started_at is None:
            self.control_started_at = float(now)
            self.episode_control_seconds = self.control_seconds
            if self.progressive_control and self.control_seconds > 0.0:
                priorities = self._priorities()
                candidate = priorities[0] if priorities else None
                # A restored winner must pass the full no-input control after
                # every restart. Starting at the discovery ramp's shortest
                # stage can re-learn an opponent or natural exit.
                if candidate != self.learned:
                    confirmations = (
                        self.success_counts.get(candidate, 0)
                        if candidate is not None else 0
                    )
                    stage = min(
                        self.control_ramp_successes,
                        confirmations + 1,
                    )
                    stage_fraction = stage / self.control_ramp_successes
                    self.episode_control_seconds = (
                        self.control_seconds
                        * stage_fraction
                        * stage_fraction
                        * stage_fraction
                    )
            self.control_passed = self.episode_control_seconds <= 0.0

    def _control_ready(self, now: float) -> bool:
        self.begin_episode(now)
        if self.control_passed:
            return True
        if (
            now - float(self.control_started_at) + 1e-9
            < self.episode_control_seconds
        ):
            return False
        self.control_passed = True
        return True

    def _advance_past(self, candidate: str) -> None:
        try:
            current = self.candidates.index(candidate)
        except ValueError:
            return
        self.index = (current + 1) % len(self.candidates)

    def expire_pending(self, now: float) -> Optional[SkipOutcome]:
        attempt = self.pending
        if attempt is None:
            return None
        if now - attempt.attempted_at <= self.result_window_seconds:
            return None
        self.pending = None
        self.success_counts[attempt.candidate] = 0
        if self.preferred == attempt.candidate and self.learned != attempt.candidate:
            self.preferred = None
        if self.learned == attempt.candidate:
            self.learned = None
        self._advance_past(attempt.candidate)
        return SkipOutcome(
            "failed",
            attempt.candidate,
            latency_seconds=now - attempt.attempted_at,
            detail="prompt_still_visible",
        )

    def choose(self, now: float) -> tuple[Optional[str], Optional[SkipOutcome]]:
        # A single missed template/OCR frame is common while the camera or
        # countdown animates. Seeing the prompt again cancels a tentative exit.
        self.absence_started_at = None
        expired = self.expire_pending(now)
        if self.pending is not None:
            return None, expired
        if not self._control_ready(now):
            return None, expired
        if now - self.last_attempt_at < self.attempt_gap_seconds:
            return None, expired

        for candidate in self._priorities():
            return candidate, expired
        return None, SkipOutcome(
            "blocked", None, detail="all_candidates_quarantined"
        )

    def record_attempt(
        self,
        candidate: str,
        now: float,
        guard: GuardedActionResult,
    ) -> SkipOutcome:
        candidate = candidate.strip().lower()
        self.last_attempt_at = now
        attempt = SkipAttempt(candidate, now, guard)
        if not guard.attempted:
            return SkipOutcome("deferred", candidate, detail=guard.reason)
        if not guard.invariant_ok:
            self.quarantined.add(candidate)
            self.success_counts[candidate] = 0
            if self.learned == candidate:
                self.learned = None
            if self.preferred == candidate:
                self.preferred = None
            self._advance_past(candidate)
            return SkipOutcome("quarantined", candidate, detail=guard.reason)
        if not guard.action_ok:
            self.success_counts[candidate] = 0
            if self.learned == candidate:
                self.learned = None
            if self.preferred == candidate:
                self.preferred = None
            self._advance_past(candidate)
            return SkipOutcome("failed", candidate, detail=guard.reason)
        self.pending = attempt
        return SkipOutcome("pending", candidate, detail="awaiting_prompt_exit")

    def prompt_disappeared(self, now: float) -> Optional[SkipOutcome]:
        """Confirm a prompt exit only after it stays absent for a short window."""

        if self.absence_started_at is None:
            self.absence_started_at = float(now)
        if now - self.absence_started_at < self.exit_confirm_seconds:
            return None

        attempt = self.pending
        self.pending = None
        if attempt is None:
            detail = (
                "opponent_or_natural_exit_during_control"
                if self.control_started_at is not None and not self.control_passed
                else "no_pending_attempt"
            )
            latency = (
                max(
                    0.0,
                    float(self.absence_started_at)
                    - float(self.control_started_at),
                )
                if (
                    self.control_started_at is not None
                    and self.absence_started_at is not None
                )
                else None
            )
            self.reset_episode()
            return SkipOutcome(
                "unattributed",
                None,
                latency_seconds=latency,
                detail=detail,
            )
        latency = max(0.0, now - attempt.attempted_at)
        if latency > self.result_window_seconds:
            self.success_counts[attempt.candidate] = 0
            if self.learned == attempt.candidate:
                self.learned = None
            if self.preferred == attempt.candidate:
                self.preferred = None
            self._advance_past(attempt.candidate)
            outcome = SkipOutcome(
                "failed",
                attempt.candidate,
                latency_seconds=latency,
                detail="late_prompt_exit",
            )
            self.reset_episode()
            return outcome

        count = self.success_counts.get(attempt.candidate, 0) + 1
        self.success_counts[attempt.candidate] = count
        self.preferred = attempt.candidate
        learned = None
        if count >= self.confirm_successes:
            self.learned = attempt.candidate
            learned = attempt.candidate
        outcome = SkipOutcome(
            "success",
            attempt.candidate,
            latency_seconds=latency,
            learned=learned,
            detail=f"confirmation={count}/{self.confirm_successes}",
        )
        self.reset_episode()
        return outcome

    def reset_episode(self) -> None:
        """Drop an unattributed pending result when capture/automation restarts."""

        self.pending = None
        self.control_started_at = None
        self.control_passed = self.control_seconds <= 0.0
        self.episode_control_seconds = self.control_seconds
        self.absence_started_at = None
