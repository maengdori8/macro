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
LEARNING_SCHEMA_VERSION = 5


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
        for variant in ("a", "s", "generic"):
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
    if variant not in {"a", "s", "generic"} or not candidate or not device_id:
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
    if variant not in {"a", "s", "generic"} or not device_id:
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
    action_started_at: Optional[float] = None


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
    preserve_foreground: bool = False,
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
    action_started_at = None
    reason = "action_failed"
    try:
        action_started_at = time.monotonic()
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

    target_stayed_inactive = target_hwnd not in samples
    foreground_preserved = all(value == before for value in samples)
    invariant_ok = target_stayed_inactive and (
        foreground_preserved if preserve_foreground else True
    )
    if not target_stayed_inactive:
        reason = "target_became_foreground"
    elif preserve_foreground and not foreground_preserved:
        reason = "foreground_changed"

    return GuardedActionResult(
        attempted=True,
        action_ok=action_ok,
        foreground_before=before,
        foreground_after=after,
        foreground_samples=tuple(samples),
        invariant_ok=invariant_ok,
        reason=reason,
        elapsed_seconds=time.monotonic() - started,
        action_started_at=action_started_at,
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
        control_offsets: Sequence[float] = (),
        exit_confirm_seconds: float = 0.0,
        persistent_retry_seconds: float = 0.0,
        progressive_control: bool = False,
        control_ramp_successes: Optional[int] = None,
        sham_candidate: Optional[str] = None,
        sham_every: int = 0,
        candidate_families: Optional[dict[str, str]] = None,
        min_family_attempts: int = 0,
        confirmation_lock_successes: int = 3,
        real_allocation: Sequence[str] = ("exploit",),
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
        normalized_offsets = tuple(
            max(0.0, float(offset)) for offset in control_offsets
        )
        self.control_offsets = normalized_offsets or (0.0,)
        self.exit_confirm_seconds = max(0.0, float(exit_confirm_seconds))
        self.persistent_retry_seconds = max(
            0.0,
            float(persistent_retry_seconds),
        )
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
        # ── 가짜 입력 대조군(sham) ──
        # 컷신은 상대가 눌러도 양쪽 다 끝나므로, "우리가 안 눌렀을 때의 종료율"을
        # 같은 조건에서 실측하지 않으면 성공률이 상대의 스킵과 구분되지 않는다.
        # sham_every 에피소드마다 아무 입력도 보내지 않는 후보를 넣어 기준선을 측정한다.
        sham = sham_candidate.strip().lower() if sham_candidate else None
        self.sham_candidate = sham if sham in self.candidates else None
        self.sham_every = max(0, int(sham_every))
        self.episode_index = 0
        self._sham_episode = False
        family_map = candidate_families or {}
        self.candidate_families = {
            name: str(family_map.get(name, name)).strip().lower() or name
            for name in self.candidates
        }
        self.min_family_attempts = max(0, int(min_family_attempts))
        self.confirmation_lock_successes = max(
            1, int(confirmation_lock_successes)
        )
        # Discovery keeps the requested explore/exploit mix.  Once one
        # candidate survives enough attributable episodes consecutively, the
        # variant enters a dedicated confirmation phase so a 30-run streak is
        # actually measurable.  Any failure immediately releases the lock.
        self.confirmation_candidate: Optional[str] = self.learned
        allocation = tuple(
            str(mode).strip().lower()
            for mode in real_allocation
            if str(mode).strip().lower() in {"explore", "exploit"}
        )
        self.real_allocation = allocation or ("exploit",)
        self.real_episode_index = 0
        self.selection_mode: Optional[str] = None
        self.selected_candidate: Optional[str] = None
        self.attempted_this_episode = False
        self.attempt_counts: dict[str, int] = {}
        self.success_totals: dict[str, int] = {}
        self.failure_totals: dict[str, int] = {}
        self.latency_totals: dict[str, float] = {}
        self.sham_attempts = 0
        self.sham_successes = 0

    def _real_candidates(self) -> list[str]:
        return [
            name for name in self.candidates
            if name != self.sham_candidate and name not in self.quarantined
        ]

    def _family_attempts(self, family: str) -> int:
        return sum(
            self.attempt_counts.get(name, 0)
            for name in self._real_candidates()
            if self.candidate_families.get(name) == family
        )

    def _family_order(self) -> list[str]:
        return list(dict.fromkeys(
            self.candidate_families[name]
            for name in self._real_candidates()
        ))

    def discovery_complete(self) -> bool:
        families = self._family_order()
        return bool(families) and all(
            self._family_attempts(family) >= self.min_family_attempts
            for family in families
        )

    def _select_explore(self) -> Optional[str]:
        candidates = self._real_candidates()
        if not candidates:
            return None
        families = self._family_order()
        family_rank = {name: rank for rank, name in enumerate(families)}
        # A newly added hypothesis must not inherit an old family's completed
        # sample count and become permanently starved by exploit history. Keep
        # the family round-robin, but first bring every concrete candidate to
        # the same minimum sample floor used for family discovery.
        under_sampled = [
            name for name in candidates
            if self.attempt_counts.get(name, 0) < self.min_family_attempts
        ]
        if under_sampled:
            return min(
                under_sampled,
                key=lambda name: (
                    self._family_attempts(self.candidate_families[name]),
                    self.attempt_counts.get(name, 0),
                    family_rank[self.candidate_families[name]],
                    self.candidates.index(name),
                ),
            )
        family = min(
            families,
            key=lambda name: (
                self._family_attempts(name) >= self.min_family_attempts,
                self._family_attempts(name),
                family_rank[name],
            ),
        )
        family_candidates = [
            name for name in candidates
            if self.candidate_families.get(name) == family
        ]
        return min(
            family_candidates,
            key=lambda name: (
                self.attempt_counts.get(name, 0),
                self.candidates.index(name),
            ),
        )

    def _beats_sham_baseline(self, name: str) -> bool:
        """Return whether a route has reproducible lift over natural exits."""

        # Until a small baseline exists, preserve the ordinary discovery
        # behavior.  Strict installs run a sham every fifth episode, so this
        # grace period is short and avoids starving early discovery.
        if self.sham_candidate is None or self.sham_attempts < 3:
            return True
        attempts = self.attempt_counts.get(name, 0)
        successes = self.success_totals.get(name, 0)
        if attempts < max(3, self.min_family_attempts) or successes < 2:
            return False
        sham_posterior = (
            (self.sham_successes + 1.0) / (self.sham_attempts + 2.0)
        )
        candidate_posterior = (successes + 1.0) / (attempts + 2.0)
        return candidate_posterior >= sham_posterior + 0.20

    def _deserves_limited_retest(self, name: str) -> bool:
        """Give a clean 1/1 or 2/2 signal one reproduction attempt only."""

        attempts = self.attempt_counts.get(name, 0)
        successes = self.success_totals.get(name, 0)
        if attempts not in {1, 2} or successes != attempts:
            return False
        if self.sham_candidate is None or self.sham_attempts < 3:
            return True
        sham_posterior = (
            (self.sham_successes + 1.0) / (self.sham_attempts + 2.0)
        )
        candidate_posterior = (successes + 1.0) / (attempts + 2.0)
        return candidate_posterior >= sham_posterior + 0.20

    def _select_exploit(self) -> Optional[str]:
        # A natural/opponent exit is observable even when the sham sends no
        # input.  Do not spend the exploit share on a single lucky coincidence
        # or on a route whose Bayesian success estimate is indistinguishable
        # from that measured baseline.  Such routes remain available to the
        # explore share until they collect enough independent evidence.
        if self.sham_candidate is None or self.sham_attempts < 3:
            candidates = [
                name for name in self._real_candidates()
                if self.success_totals.get(name, 0) > 0
            ]
        else:
            candidates = [
                name for name in self._real_candidates()
                if (
                    self._beats_sham_baseline(name)
                    or self._deserves_limited_retest(name)
                )
            ]
        if not candidates:
            return self._select_explore()

        def score(name: str) -> tuple[float, int, float, int]:
            attempts = self.attempt_counts.get(name, 0)
            successes = self.success_totals.get(name, 0)
            # Beta(1,1) posterior mean avoids promoting a single lucky success
            # too aggressively while still letting reproducible candidates rise.
            posterior = (successes + 1.0) / (attempts + 2.0)
            average_latency = (
                self.latency_totals.get(name, 0.0) / max(1, successes)
            )
            return (
                posterior,
                successes,
                -average_latency,
                -self.candidates.index(name),
            )

        return max(candidates, key=score)

    def _select_candidate(self) -> Optional[str]:
        if self._sham_episode:
            return self.sham_candidate
        if (
            self.confirmation_candidate in self._real_candidates()
        ):
            return self.confirmation_candidate
        if self.selection_mode == "exploit":
            return self._select_exploit()
        return self._select_explore()

    def _priorities(self) -> list[str]:
        # 대조 에피소드에는 오직 sham만 시도한다. 학습값이 있어도 마찬가지 —
        # 기준선은 실험 내내 계속 측정돼야 비교가 유효하다.
        if self.selected_candidate is None:
            self.selected_candidate = self._select_candidate()
        return [self.selected_candidate] if self.selected_candidate else []

    def begin_episode(self, now: float) -> None:
        """Start the no-input control period for one visible prompt."""

        if self.control_started_at is None:
            self.control_started_at = float(now)
            self.episode_index += 1
            self._sham_episode = bool(
                self.sham_candidate
                and self.sham_every > 0
                and self.episode_index % self.sham_every == 0
                and self.sham_candidate not in self.quarantined
            )
            self.selected_candidate = None
            self.attempted_this_episode = False
            if self._sham_episode:
                self.selection_mode = "sham"
            elif self.confirmation_candidate in self._real_candidates():
                self.selection_mode = "confirm"
            else:
                mode_index = self.real_episode_index % len(self.real_allocation)
                self.selection_mode = self.real_allocation[mode_index]
                self.real_episode_index += 1
            self.episode_control_seconds = self.control_seconds
            if self._sham_episode and self.episode_control_seconds > 0.0:
                offset_index = (self.episode_index - 1) % len(
                    self.control_offsets
                )
                self.episode_control_seconds += self.control_offsets[
                    offset_index
                ]
            if self._sham_episode:
                # 대조군은 항상 전체 대조 시간을 쓴다. 램프가 적용되면 관찰 구간이
                # 짧아져 기준선이 후보 시도와 다른 조건에서 측정된다.
                self.control_passed = self.episode_control_seconds <= 0.0
                return
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
            if not self._sham_episode and self.episode_control_seconds > 0.0:
                # Cycle a deterministic non-negative offset after the
                # mandatory control. Highlight input and sham trials therefore
                # do not always sample the same residual replay lifetime.
                offset_index = (self.episode_index - 1) % len(
                    self.control_offsets
                )
                self.episode_control_seconds += self.control_offsets[
                    offset_index
                ]
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
        # 대조군은 스윕 순서 밖에서 끼어드는 것이므로 인덱스를 옮기면 안 된다.
        # 옮기면 대조 에피소드마다 진짜 후보 하나가 차례를 건너뛴다.
        if candidate == self.sham_candidate:
            return
        try:
            current = self.candidates.index(candidate)
        except ValueError:
            return
        self.index = (current + 1) % len(self.candidates)

    def _retire_observed_intermittent(self, candidate: str) -> bool:
        """Retire an exact route once enough trials prove it intermittent.

        A concrete delivery/timing shape with both attributable successes and
        failures cannot be the zero-failure winner.  Wait for the requested
        minimum of three trials before retiring it, so a single early outcome
        never drives the search.  Materially different timings remain separate
        candidates and continue through discovery.
        """

        if candidate == self.sham_candidate:
            return False
        minimum = max(3, self.min_family_attempts)
        if (
            self.attempt_counts.get(candidate, 0) < minimum
            or self.success_totals.get(candidate, 0) <= 0
            or self.failure_totals.get(candidate, 0) <= 0
        ):
            return False
        self.quarantined.add(candidate)
        if self.confirmation_candidate == candidate:
            self.confirmation_candidate = None
        if self.preferred == candidate:
            self.preferred = None
        if self.learned == candidate:
            self.learned = None
        return True

    def expire_pending(self, now: float) -> Optional[SkipOutcome]:
        attempt = self.pending
        if attempt is None:
            return None
        if now - attempt.attempted_at <= self.result_window_seconds:
            return None
        self.pending = None
        self._record_final(attempt.candidate, success=False, latency=None)
        self.success_counts[attempt.candidate] = 0
        self._retire_observed_intermittent(attempt.candidate)
        if self.confirmation_candidate == attempt.candidate:
            # A route that reached strict confirmation but then missed cannot
            # be the zero-failure winner. Retire this concrete timing shape so
            # discovery advances to a materially different hypothesis.
            self.quarantined.add(attempt.candidate)
            self.confirmation_candidate = None
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
        # One prompt is one independent experiment. Trying a second candidate
        # on the same cutscene would mix both inputs and make attribution false.
        # A highlight summary can remain forever, however. After a deliberately
        # long input-free washout, open a new episode whose own control period
        # keeps the next action at least control+retry seconds from the prior.
        if self.attempted_this_episode:
            can_retry_persistent = bool(
                expired is None
                and self.persistent_retry_seconds > 0.0
                and now - self.last_attempt_at
                >= self.persistent_retry_seconds
            )
            if not can_retry_persistent:
                return None, expired
            self.reset_episode()
            self.begin_episode(now)
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
        self.attempted_this_episode = True
        if not guard.invariant_ok:
            self._record_final(candidate, success=False, latency=None)
            self.quarantined.add(candidate)
            self.success_counts[candidate] = 0
            if self.confirmation_candidate == candidate:
                self.confirmation_candidate = None
            if self.learned == candidate:
                self.learned = None
            if self.preferred == candidate:
                self.preferred = None
            self._advance_past(candidate)
            return SkipOutcome("quarantined", candidate, detail=guard.reason)
        if not guard.action_ok:
            self._record_final(candidate, success=False, latency=None)
            self.success_counts[candidate] = 0
            self._retire_observed_intermittent(candidate)
            if self.confirmation_candidate == candidate:
                self.quarantined.add(candidate)
                self.confirmation_candidate = None
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
        # The result deadline applies to the first frame where the prompt is
        # absent.  ``now`` is deliberately later because the absence must then
        # remain stable for ``exit_confirm_seconds``.  Comparing the deadline
        # with ``now`` incorrectly subtracts the confirmation window from the
        # allowed response time (for example, an exit at 1.34s confirmed at
        # 1.74s was previously rejected against a 1.50s deadline).
        disappeared_at = (
            float(self.absence_started_at)
            if self.absence_started_at is not None
            else float(now)
        )
        latency = max(0.0, disappeared_at - attempt.attempted_at)
        if latency > self.result_window_seconds:
            self._record_final(attempt.candidate, success=False, latency=latency)
            self.success_counts[attempt.candidate] = 0
            self._retire_observed_intermittent(attempt.candidate)
            if self.confirmation_candidate == attempt.candidate:
                self.quarantined.add(attempt.candidate)
                self.confirmation_candidate = None
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
        self._record_final(attempt.candidate, success=True, latency=latency)
        retired_intermittent = self._retire_observed_intermittent(
            attempt.candidate
        )
        learned = None
        if attempt.candidate == self.sham_candidate:
            # 대조군의 '성공'은 상대/자연 종료를 측정한 값이다. 절대 학습하거나
            # 선호 후보로 올리면 안 된다(그러면 매크로가 아무 입력도 안 하게 된다).
            detail = f"sham_baseline={count}"
        else:
            if not retired_intermittent:
                self.preferred = attempt.candidate
                if (
                    count >= self.confirmation_lock_successes
                    and self._beats_sham_baseline(attempt.candidate)
                ):
                    self.confirmation_candidate = attempt.candidate
                if count >= self.confirm_successes:
                    self.learned = attempt.candidate
                    learned = attempt.candidate
            detail = f"confirmation={count}/{self.confirm_successes}"
            if retired_intermittent:
                detail += ";intermittent_quarantined"
        outcome = SkipOutcome(
            "success",
            attempt.candidate,
            latency_seconds=latency,
            learned=learned,
            detail=detail,
        )
        self.reset_episode()
        return outcome

    def _record_final(
        self,
        candidate: str,
        *,
        success: bool,
        latency: Optional[float],
    ) -> None:
        if candidate == self.sham_candidate:
            self.sham_attempts += 1
            if success:
                self.sham_successes += 1
            locked = self.confirmation_candidate
            if locked and not self._beats_sham_baseline(locked):
                self.confirmation_candidate = None
                self.success_counts[locked] = 0
                if self.learned == locked:
                    self.learned = None
            return
        self.attempt_counts[candidate] = self.attempt_counts.get(candidate, 0) + 1
        bucket = self.success_totals if success else self.failure_totals
        bucket[candidate] = bucket.get(candidate, 0) + 1
        if success and latency is not None:
            self.latency_totals[candidate] = (
                self.latency_totals.get(candidate, 0.0) + float(latency)
            )

    def diagnostics(self, candidate: Optional[str] = None) -> dict[str, object]:
        name = candidate or self.selected_candidate
        family = self.candidate_families.get(name) if name else None
        return {
            "selection_mode": self.selection_mode,
            "discovery_complete": self.discovery_complete(),
            "selected_family": family,
            "candidate_attempts": self.attempt_counts.get(name, 0) if name else 0,
            "candidate_successes": self.success_totals.get(name, 0) if name else 0,
            "candidate_failures": self.failure_totals.get(name, 0) if name else 0,
            "candidate_streak": self.success_counts.get(name, 0) if name else 0,
            "confirmation_candidate": self.confirmation_candidate,
            "confirmation_lock_successes": self.confirmation_lock_successes,
            "persistent_retry_seconds": self.persistent_retry_seconds,
            "family_attempts": self._family_attempts(family) if family else 0,
            "minimum_family_attempts": self.min_family_attempts,
            "sham_attempts": self.sham_attempts,
            "sham_successes": self.sham_successes,
            "quarantined": sorted(self.quarantined),
        }

    def restore_final_outcome(
        self,
        candidate: str,
        status: str,
        latency: Optional[float] = None,
    ) -> bool:
        """Replay one prior finalized event into a fresh tracker.

        Monotonic timestamps and pending episodes are intentionally not
        restored.  Only evidence used by scheduling and confirmation survives
        a local restart.
        """

        candidate = str(candidate).strip().lower()
        status = str(status).strip().lower()
        if candidate not in self.candidates:
            return False
        if status == "quarantined":
            self.quarantined.add(candidate)
            if self.confirmation_candidate == candidate:
                self.confirmation_candidate = None
            return True
        if status not in {"success", "failed"}:
            return False

        success = status == "success"
        self._record_final(candidate, success=success, latency=latency)
        self.episode_index += 1
        if candidate != self.sham_candidate:
            self.real_episode_index += 1
        if candidate == self.sham_candidate:
            return True
        if success:
            count = self.success_counts.get(candidate, 0) + 1
            self.success_counts[candidate] = count
            if not self._retire_observed_intermittent(candidate):
                self.preferred = candidate
                if (
                    count >= self.confirmation_lock_successes
                    and self._beats_sham_baseline(candidate)
                ):
                    self.confirmation_candidate = candidate
                if count >= self.confirm_successes:
                    self.learned = candidate
        else:
            self.success_counts[candidate] = 0
            self._retire_observed_intermittent(candidate)
            if self.confirmation_candidate == candidate:
                self.quarantined.add(candidate)
                self.confirmation_candidate = None
            if self.preferred == candidate:
                self.preferred = None
            if self.learned == candidate:
                self.learned = None
            self._advance_past(candidate)
        return True

    def reset_episode(self) -> None:
        """Drop an unattributed pending result when capture/automation restarts."""

        self.pending = None
        self.control_started_at = None
        self.control_passed = self.control_seconds <= 0.0
        self.episode_control_seconds = self.control_seconds
        self.absence_started_at = None
        self.selected_candidate = None
        self.selection_mode = None
        self.attempted_this_episode = False
