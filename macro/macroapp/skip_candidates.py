"""Strictly-inactive SKIP candidate catalogue.

Every entry documents the hypothesis it tests and the result that rejects it.
The catalogue contains only target-window messages, virtual gamepad state and
no-op controls.  Global SendInput, top-level SetFocus and foreground switching
are deliberately absent because they can disturb the user's active desktop.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class SkipCandidateSpec:
    name: str
    action: str
    family: str
    hypothesis: str
    reject_condition: str
    input_scope: str
    hold_seconds: float = 0.15
    pulses: int = 1
    pulse_gap_seconds: float = 0.08

    def event_metadata(self) -> dict[str, object]:
        return asdict(self)


def _spec(
    name: str,
    action: str,
    family: str,
    hypothesis: str,
    *,
    hold: float = 0.15,
    pulses: int = 1,
    gap: float = 0.08,
    input_scope: str = "target_window",
) -> SkipCandidateSpec:
    reject = (
        "입력 전 대조 구간을 통과했는데 입력 후 1.5초 안에 프롬프트가 "
        "0.4초 이상 사라지지 않거나 전면창 불변 조건을 위반하면 폐기"
    )
    return SkipCandidateSpec(
        name=name,
        action=action,
        family=family,
        hypothesis=hypothesis,
        reject_condition=reject,
        input_scope=input_scope,
        hold_seconds=hold,
        pulses=max(1, int(pulses)),
        pulse_gap_seconds=max(0.0, float(gap)),
    )


_SPECS = (
    _spec(
        "control_noop", "control_noop", "sham",
        "같은 관찰 창에서 아무 입력도 보내지 않아 상대 스킵과 자연 종료 기준선을 측정",
        hold=0.0, input_scope="none",
    ),
    _spec(
        "click_prompt", "click_prompt", "mouse_message",
        "스킵 표시가 비활성 WM_MOUSE 메시지 클릭을 직접 처리할 가능성",
    ),

    _spec(
        "click_prompt_sync", "click_prompt_sync", "mouse_message_sync",
        "The render window may consume synchronous mouse messages instead of queued clicks",
    ),
    _spec(
        "click_prompt_noactivate", "click_prompt_noactivate",
        "mouse_message_activation",
        "A no-activate WM_MOUSEACTIVATE probe may initialize the render-window click path",
    ),

    # XInput/DirectInput values can be visible to a background game.  Test the
    # threshold and repeat axes separately instead of assuming one 1s hold.
    # 2026-08-22 리서치 H1: 게이트가 전역 전면창이 아니라 스레드 큐 로컬 활성 값이라면,
    # 게임 큐에 붙어(AttachThreadInput) 있는 동안의 A 홀드(+SetActiveWindow)가 통한다.
    # attach_hold_a 는 대조(큐 공유만), attach_active_hold_a 가 진짜 가설. 둘 다 전면 불변.
    _spec("attach_hold_a", "attach_a", "gamepad_attach", "게임 큐에 붙은 채 A 장홀드(대조 — 큐 공유만으로는 변화 없을 것)", hold=1.25, input_scope="virtual_gamepad"),
    _spec("attach_active_hold_a", "attach_active_a", "gamepad_attach_activate", "붙은 큐에서 SetActiveWindow 로 스레드 로컬 활성 창을 세운 뒤 A 장홀드(게이트가 GetActiveWindow/GetFocus 면 통과)", hold=1.25, input_scope="virtual_gamepad"),
    _spec("a", "a", "gamepad", "게임패드 A 탭 경로", input_scope="virtual_gamepad"),
    _spec("a_hold_500", "a", "gamepad", "A 홀드 임계값이 0.5초 부근일 가능성", hold=0.50, input_scope="virtual_gamepad"),
    _spec("a_hold", "a", "gamepad", "기존 1.0초 A 홀드 경로", hold=1.00, input_scope="virtual_gamepad"),
    _spec("a_hold_1250", "a", "gamepad", "1초 경계 누락을 피한 A 장홀드", hold=1.25, input_scope="virtual_gamepad"),
    _spec("a_pulse2", "a", "gamepad", "A 탭을 두 번 요구하는 상태 전환 가능성", hold=0.18, pulses=2, gap=0.10, input_scope="virtual_gamepad"),
    _spec("start", "start", "gamepad", "일반 SKIP이 가상 START 탭을 받을 가능성", input_scope="virtual_gamepad"),
    _spec("start_hold_500", "start", "gamepad", "START 0.5초 홀드 임계값", hold=0.50, input_scope="virtual_gamepad"),
    _spec("start_hold", "start", "gamepad", "기존 1.0초 START 홀드 경로", hold=1.00, input_scope="virtual_gamepad"),
    _spec("start_hold_1250", "start", "gamepad", "START 장홀드 임계값", hold=1.25, input_scope="virtual_gamepad"),
    _spec("start_pulse2", "start", "gamepad", "START 이중 탭 경로", hold=0.18, pulses=2, gap=0.10, input_scope="virtual_gamepad"),

    _spec("spoof_a_hold_500", "spoof_a", "gamepad_spoof", "활성 메시지 플래그와 A 0.5초 홀드 조합", hold=0.50, input_scope="virtual_gamepad"),
    _spec("spoof_a_hold", "spoof_a", "gamepad_spoof", "활성 메시지 플래그와 A 1초 홀드 조합", hold=1.00, input_scope="virtual_gamepad"),
    _spec("spoof_a_hold_1250", "spoof_a", "gamepad_spoof", "활성 메시지 플래그와 A 장홀드 조합", hold=1.25, input_scope="virtual_gamepad"),
    _spec("spoof_a_pulse2", "spoof_a", "gamepad_spoof", "활성 메시지 플래그 안에서 A 이중 탭", hold=0.18, pulses=2, gap=0.10, input_scope="virtual_gamepad"),
    _spec("spoof_start", "spoof_start", "gamepad_spoof", "활성 메시지 플래그와 START 탭 조합", input_scope="virtual_gamepad"),
    _spec("spoof_start_hold_500", "spoof_start", "gamepad_spoof", "활성 메시지 플래그와 START 0.5초 홀드", hold=0.50, input_scope="virtual_gamepad"),
    _spec("spoof_start_hold", "spoof_start", "gamepad_spoof", "활성 메시지 플래그와 START 1초 홀드", hold=1.00, input_scope="virtual_gamepad"),
    _spec("spoof_start_hold_1250", "spoof_start", "gamepad_spoof", "활성 메시지 플래그와 START 장홀드", hold=1.25, input_scope="virtual_gamepad"),
    _spec("spoof_start_pulse2", "spoof_start", "gamepad_spoof", "활성 메시지 플래그 안에서 START 이중 탭", hold=0.18, pulses=2, gap=0.10, input_scope="virtual_gamepad"),
    _spec("spoof_a_envelope2", "spoof_a_envelope2", "gamepad_s_spoof_sequence", "The captured S SKIP prompt may use controller confirm/A; keep one target-local activation envelope across two A pulses instead of dropping activation between pulses", hold=0.18, input_scope="virtual_gamepad"),
    _spec("spoof_a_preactivate80_burst3", "spoof_a_preactivate80_burst3", "gamepad_s_spoof_preactivation", "Let target-local activation age for 80 ms, then cover three controller-A polling samples for the explicit S SKIP prompt", hold=0.08, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_a_envelope2", "process_device_spoof_a_envelope2", "gamepad_s_process_rescan_spoof_sequence", "FC may keep polling physical XInput slot 0 while the virtual pad is slot 1; notify every FC-owned window of controller re-enumeration, settle 120 ms, then send two A pulses inside one target-local activation envelope", hold=0.18, input_scope="virtual_gamepad"),
    _spec("spoof_start_envelope2", "spoof_start_envelope2", "gamepad_spoof_sequence", "Two pulse2 successes ended 45-50 ms after delivery; keep one activation envelope across both START pulses to remove the inactive gap", hold=0.18, input_scope="virtual_gamepad"),
    _spec("spoof_envelope2_control", "spoof_envelope2_control", "window_anykey_spoof_control", "Replay the same target-local activation envelope and timing without any START state, separating activation-only or natural exits from the two-pulse input effect; reject as an explanatory control if three prompts do not exit", hold=0.18, input_scope="target_window"),
    _spec("spoof_start_envelope3", "spoof_start_envelope3", "gamepad_spoof_sequence", "Cover a third input sample while keeping FC's target-local activation flag continuously set", hold=0.12, input_scope="virtual_gamepad"),
    _spec("spoof_start_envelope2_settle150", "spoof_start_envelope2_settle150", "gamepad_spoof_sequence_settle_150", "The zero-settle highlight route missed while 250 ms produced a first 0.845 s exit; keep the activation envelope 150 ms after the second release to locate the earliest inactive polling window", hold=0.18, input_scope="virtual_gamepad"),
    _spec("spoof_start_envelope2_settle250", "spoof_start_envelope2_settle250", "gamepad_spoof_sequence_settle", "Keep the activation envelope for 250 ms after the second release in case FC consumes START on the next render tick", hold=0.18, input_scope="virtual_gamepad"),
    _spec("spoof_start_envelope2_settle350", "spoof_start_envelope2_settle350", "gamepad_spoof_sequence_settle_350", "If the 250 ms highlight exit is activation-window dependent, extending the target-local envelope to 350 ms should reproduce it while remaining inside the strict 1.5 s result budget", hold=0.18, input_scope="virtual_gamepad"),
    _spec("focusmsg_start_envelope2", "focusmsg_start_envelope2", "gamepad_focus_component_sequence", "The full activation envelope may contain counteracting flags; expose only FC's target-local focus flag while emitting the proven two-pulse virtual START sequence", hold=0.18, input_scope="virtual_gamepad"),
    _spec("appmsg_start_envelope2", "appmsg_start_envelope2", "gamepad_app_component_sequence", "Expose only FC's target-local app-active flag while emitting two virtual START pulses, separating controller polling from window-active state", hold=0.18, input_scope="virtual_gamepad"),
    _spec("windowmsg_start_envelope2", "windowmsg_start_envelope2", "gamepad_window_component_sequence", "Expose only FC's target-local window-active flag while emitting two virtual START pulses so focus and app messages cannot cancel the polling gate", hold=0.18, input_scope="virtual_gamepad"),
    _spec("focuswindow_start_envelope2", "focuswindow_start_envelope2", "gamepad_focus_window_component_sequence", "Focus-only cleared two highlights quickly and window-only cleared one, while app-active missed; expose the two promising target-local components together, exclude app-active, and emit two virtual START pulses", hold=0.18, input_scope="virtual_gamepad"),
    _spec("focusmsg_start_compact", "focusmsg_start_compact", "gamepad_focus_component_compact_sequence", "Focus-only registered with two 180 ms pulses but later missed the deadline by 41 ms; retain two controller edges while shortening each proven-capable hold to 150 ms and the gap to 50 ms", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("windowmsg_start_compact", "windowmsg_start_compact", "gamepad_window_component_compact_sequence", "Window-only registered quickly once; test two 150 ms START samples with a 50 ms gap to reduce synchronous delivery by 110 ms without adding another activation component", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("windowmsg_start_refresh2", "windowmsg_start_refresh2", "gamepad_window_component_refreshed_pair", "Window-only compact reached 4/5 and the independent refreshed pair passed once; combine the two signals by re-publishing each 150 ms held report every 25 ms under only the target-local window-active component", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("windowmsg_start_edge2", "windowmsg_start_edge2", "gamepad_window_component_per_edge_sequence", "The compact window envelope is intermittent and both post-release settles missed; align a separate target-local window-active transition with each of the two proven START rising edges instead of holding the component across both pulses", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("windowmsg_start_compact_settle100", "windowmsg_start_compact_settle100", "gamepad_window_component_compact_settle100", "The 4/5 window-only route eventually exited late once; preserve both proven edges and keep only the target-local window-active flag for 100 ms after the second release so the next render poll can consume it", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("windowmsg_start_compact_settle200", "windowmsg_start_compact_settle200", "gamepad_window_component_compact_settle200", "If one inactive render poll occurs beyond 100 ms, retain the exact window-only compact input and extend only its post-release target-local window-active window to 200 ms", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("focuswindow_start_compact", "focuswindow_start_compact", "gamepad_focus_window_component_compact_sequence", "The 180 ms focus+window route physically transitioned but crossed the strict deadline at 1.574 s; preserve both target-local components and two START edges while removing 110 ms of delivery time", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("focusmsg_start_spread650", "focusmsg_start_spread650", "gamepad_focus_component_spread_sequence", "Clustered START edges sometimes register immediately and sometimes only after the strict deadline; keep focus target-local and separate two 180 ms rises by 650 ms to cover distinct controller polling intervals", hold=0.18, gap=0.65, input_scope="virtual_gamepad"),
    _spec("windowmsg_start_spread650", "windowmsg_start_spread650", "gamepad_window_component_spread_sequence", "Test the same 650 ms-separated START rises with only the window-active component, preserving roughly 400 ms for post-action observation inside the strict deadline", hold=0.18, gap=0.65, input_scope="virtual_gamepad"),
    _spec("focuswindow_start_spread650", "focuswindow_start_spread650", "gamepad_focus_window_component_spread_sequence", "Combine the two promising target-local components while spreading two 180 ms START rises across separate polling intervals and still returning before the 1.5 s deadline", hold=0.18, gap=0.65, input_scope="virtual_gamepad"),
    _spec("spoof_start_preactivate80_burst3", "spoof_start_preactivate80_burst3", "gamepad_spoof_preactivation_timing", "The two-pulse envelope reproduced 3/3 then missed once on ESC; let target-local activation messages age for 80 ms before three fast START samples so the game loop sees the active flag before the first edge", hold=0.08, input_scope="virtual_gamepad"),
    _spec("spoof_start_envelope4_fast", "spoof_start_envelope4_fast", "gamepad_spoof_dense_sequence", "The proven two-pulse route can miss an ESC polling window; fit four 80 ms START samples into a similar total envelope to cover more game-loop ticks", hold=0.08, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_envelope2", "process_device_spoof_start_envelope2", "gamepad_process_rescan_spoof_sequence", "The macro pad is XInput slot 1 while a physical pad occupies slot 0; notify every FC-owned window of controller re-enumeration, settle 120 ms, then send two START pulses inside one target-local activation envelope", hold=0.18, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_envelope2_settle0", "process_device_spoof_start_envelope2_settle0", "gamepad_process_rescan_spoof_sequence_settle0", "The strong highlight route physically skipped 3/3 but once crossed the strict deadline at 1.705 s; preserve both 180 ms START pulses and remove only the 120 ms post-rescan wait", hold=0.18, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_envelope2_gap50", "process_device_spoof_start_envelope2_gap50", "gamepad_process_rescan_spoof_sequence_gap50", "Preserve the two proven 180 ms START samples while removing the post-rescan wait and halving only the inter-pulse gap to recover 170 ms of latency", hold=0.18, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_envelope2_compact", "process_device_spoof_start_envelope2_compact", "gamepad_process_rescan_spoof_sequence_compact", "If the strict miss is delivery duration rather than recognition delay, use two still-long 150 ms START samples with a 50 ms gap and no post-rescan wait, reducing the proven route by 230 ms", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_pair_rehandshake2", "process_device_spoof_start_pair_rehandshake2", "gamepad_process_rescan_spoof_pair_rehandshake_sequence", "The compact process-rescan START pair reached seven strict successes in eight trials while adding more edges inside one activation envelope did not improve reliability; repeat the complete target-local rescan, activation, and two-edge handshake after a 50 ms neutral interval so a missed device-enumeration or activation transition gets one independent retry", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_rescan2_pair", "process_device_spoof_start_rescan2_pair", "gamepad_process_double_rescan_spoof_pair_sequence", "The full repeated handshake exited 45 ms late with a 992 ms synchronous guard, so isolate device enumeration without adding START edges or a second activation envelope: notify FC-owned windows of controller rescan twice 50 ms apart, then run the proven two-edge compact START pair once", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_activation_rearm_pair", "process_device_spoof_start_activation_rearm_pair", "gamepad_process_rescan_spoof_activation_rearm_pair", "The full repeated handshake could not distinguish enumeration from activation and spent 992 ms inside the guard; rescan once, then align each of the two proven 150 ms START edges with its own target-local activation transition, retaining only a 50 ms neutral interval", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_compact_control80", "process_device_spoof_start_envelope2_compact", "gamepad_process_rescan_spoof_compact_control80_sequence", "Revalidate the strongest exact 7/8 compact START delivery under the control-only 80 ms OCR cadence, separating input-shape reliability from the prior 300 ms quantization of the nominal three-second boundary", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_preactivate150_pair", "process_device_spoof_start_preactivate150_pair", "gamepad_process_rescan_spoof_neutral_active_sample", "The compact route is bimodal, usually exiting near 0.52 s or not being consumed at all; after process rescan, expose FC's target-local active state for 150 ms while the virtual pad is neutral so the inactive controller loop can sample neutral before the first of the same two START rising edges", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_reset_start_compact", "process_device_spoof_reset_start_compact", "gamepad_neutral_reset_process_rescan_spoof_pair", "Intermittent consumption may reflect a stale shared virtual-pad report left by another macro action rather than START timing; publish one explicit neutral reset under the pad lock, settle 50 ms, then run the unchanged process-rescan compact START pair", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_sync_spoof_start_compact", "process_device_sync_spoof_start_compact", "gamepad_sync_relevant_device_rescan_spoof_compact_pair", "The 7/8 compact route queues device rescan asynchronously and can emit START before FC processes it; synchronously refresh only the FC render and DIEmWin windows with an 80 ms timeout, then run the unchanged target-local compact pair", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_raw_spoof_start_compact", "process_raw_spoof_start_compact", "gamepad_raw_arrival_spoof_compact_pair", "The earlier Raw Input arrival trial used one unactivated START edge; isolate Raw Input discovery by posting valid gamepad-arrival handles only to FC-owned windows, then run the proven target-local two-edge compact START envelope without a generic device rescan", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_raw_spoof_start_compact", "process_device_raw_spoof_start_compact", "gamepad_process_rescan_raw_arrival_spoof_compact_pair", "If FC needs both its generic controller registry and Raw Input handle cache refreshed, send both target-process-only discovery notifications before the unchanged target-local two-edge compact START envelope; reject on the first attributable miss", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_spoof_device_start_compact", "process_spoof_device_start_compact", "gamepad_active_first_process_rescan_compact_pair", "FC may ignore a queued device-change while its internal app state is inactive; establish only the target-local activation envelope first, queue the process rescan while that state is active, settle 50 ms, then emit the unchanged compact START pair", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_spoof_device_sync_start_compact", "process_spoof_device_sync_start_compact", "gamepad_active_first_sync_rescan_compact_pair", "Factor device-change ordering from queue delivery: establish the target-local activation envelope first, synchronously refresh only the FC render and DIEmWin windows, then emit the unchanged compact START pair", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_spoof_device_raw_sync_start_compact", "process_spoof_device_raw_sync_start_compact", "gamepad_active_first_sync_rescan_raw_arrival_compact_pair", "The active-first synchronous device-rescan route cleared its first live highlight in 0.528 s while the generic-rescan plus Raw route reached two of three; inside one target-local active envelope, synchronously refresh FC's render and DirectInput device state and valid Raw Input gamepad handles before the unchanged compact START pair", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_spoof_device_raw_parallel_start_compact", "process_spoof_device_raw_parallel_start_compact", "gamepad_active_first_parallel_sync_rescan_raw_arrival_compact_pair", "The sequential active-first device-plus-Raw route clustered at the 1.5 s deadline; overlap only the two independent bounded target-process discovery calls inside one target-local active envelope, require both to finish successfully, then emit the unchanged compact START pair", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_spoof_diapp_device_raw_parallel_start_compact", "process_spoof_diapp_device_raw_parallel_start_compact", "gamepad_render_diapp_active_parallel_sync_rescan_raw_arrival_compact_pair", "The parallel discovery route reached six strict highlight successes before one complete miss; additionally mirror app-active state only to FC's process-owned DirectInput window before the same parallel discovery and compact START pair so both render and controller threads accept the virtual pad without changing real focus", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_spoof_device_raw_parallel_start_compact3", "process_spoof_device_raw_parallel_start_compact3", "gamepad_active_first_parallel_sync_rescan_raw_arrival_compact_triple", "The two-edge parallel discovery route passed six of seven strict highlights but one prompt consumed neither edge; preserve its exact target-local discovery and 150 ms edge shape while adding one third 150 ms START rise 50 ms later to cover one additional inactive controller poll within the 1.5-second budget", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_spoof_device_raw_parallel_start_rehandshake2", "process_spoof_device_raw_parallel_start_rehandshake2", "gamepad_active_first_parallel_sync_discovery_rehandshake_compact_pairs", "Adding a third START edge still missed its first highlight, so the failure is not edge count alone; repeat the target-local parallel device/Raw discovery once after the first compact START pair, then issue a second identical pair inside the same inactive activation envelope as an independent discovery-and-input retry", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_spoof_device_raw_stagger30_start_compact", "process_spoof_device_raw_stagger30_start_compact", "gamepad_active_first_staggered_parallel_sync_rescan_raw_arrival_compact_pair", "Simultaneous device and Raw notifications can interleave differently on FC's render and DirectInput threads; begin bounded device rescan 30 ms before bounded Raw arrival while retaining overlap, target scope, and the original compact START pair", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_spoof_reset_device_raw_parallel_start_compact", "process_spoof_reset_device_raw_parallel_start_compact", "gamepad_neutral_reset_active_first_parallel_sync_discovery_compact_pair", "Repeated and ordered discovery both missed their first trials; publish one explicit neutral Xbox report before the same target-only parallel discovery so FC enumerates a clean baseline report, then retain the original two 150 ms START rises", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_spoof_device_raw_parallel_start_refresh2", "process_spoof_device_raw_parallel_start_refresh2", "gamepad_active_first_parallel_sync_discovery_refreshed_compact_pair", "If FC's inactive controller loop samples held reports rather than edges, retain the strongest target-only parallel discovery and two 150 ms START holds while re-publishing each held state every 25 ms under the same virtual-pad lock", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_wait300_a", "process_device_spoof_start_wait300_a", "gamepad_process_rescan_spoof_start_mode_settle_a", "START-then-A produced one 0.764 s strict exit but the 50 ms forms did not reproduce; allow 300 ms of neutral controller-mode/UI settling after one START edge before one A confirmation edge", hold=0.15, gap=0.30, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_diapp_start_compact", "process_device_spoof_diapp_start_compact", "gamepad_process_rescan_render_directinput_active_compact_pair", "FC's render and DIEmWin windows are on different UI threads; mirror WM_ACTIVATEAPP only to the process-owned DirectInput window while retaining the normal target-local render envelope and unchanged compact START pair", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_raw_sync_spoof_start_compact", "process_raw_sync_spoof_start_compact", "gamepad_sync_raw_arrival_spoof_compact_pair", "The Raw Input discovery factorial otherwise queues arrival asynchronously; synchronously deliver valid gamepad handles only to FC's render and DIEmWin windows with bounded timeouts, then run the unchanged compact START pair", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_compact_fixed3", "process_device_spoof_start_envelope2_compact", "gamepad_process_rescan_spoof_compact_fixed3_sequence", "The compact START route passed all seven highlight trials whose no-input control ended between 3.00 and 3.41 seconds; its only miss was the deliberately delayed 3.73-second trial, so revalidate the identical target-local delivery at the production-representative fixed 3.00-second boundary", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_compact4", "process_device_spoof_start_compact4", "gamepad_process_rescan_spoof_compact4_sequence", "The two-pulse compact process-rescan route achieved seven strict highlight successes before one miss; keep its proven 150 ms edges and 50 ms gaps but cover four polling opportunities inside a roughly 0.91 s target-local envelope", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_compact4_gap10", "process_device_spoof_start_compact4_gap10", "gamepad_process_rescan_spoof_compact4_gap10_sequence", "Compact4 physically exited only 77 ms late with a measured 0.916 s guard; preserve four proven 150 ms START holds and reduce only the three idle gaps from 50 ms to 10 ms, targeting a 0.796 s guard", hold=0.15, gap=0.01, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_compact3", "process_device_spoof_start_compact3", "gamepad_process_rescan_spoof_compact3_sequence", "The proven two-pulse 150/50 route was 7/8 while four pulses returned too late; add exactly one proven-length START rise for three distinct polling opportunities in an estimated 0.69 s guard", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_compact4_hold130", "process_device_spoof_start_compact4_hold130", "gamepad_process_rescan_spoof_compact4_hold130_sequence", "The 10 ms neutral gaps likely merged edges; retain four distinct 50 ms gaps and shorten only each START hold from 150 ms to 130 ms, targeting a roughly 0.84 s guard", hold=0.13, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_b_combo2", "process_device_spoof_start_b_combo2", "gamepad_process_rescan_spoof_start_b_combo_sequence", "The strongest START-only delivery still missed once and further timing changes did not make highlight exits deterministic; emit START+B in the same two 150 ms virtual-pad reports after process rescan so START can select controller input while B supplies the controller cancel/skip mapping", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_back_combo2", "process_device_spoof_start_back_combo2", "gamepad_process_rescan_spoof_start_back_combo_sequence", "The highlight explicitly advertises an escape action but START-only is intermittent; emit START+BACK in the same two 150 ms reports after process rescan to cover games that map menu/cancel semantics to the controller back button while retaining the proven START mode-selection edge", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_then_b_pair", "process_device_spoof_start_then_b_pair", "gamepad_process_rescan_spoof_start_then_b_sequence", "START+B in the same report missed the strict deadline and may be treated as an unsupported chord; publish one proven 150 ms START edge first, then two distinct 150 ms B cancel edges with 50 ms neutral gaps while the target-local activation envelope remains open", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_then_back_pair", "process_device_spoof_start_then_back_pair", "gamepad_process_rescan_spoof_start_then_back_sequence", "Separate controller-mode selection from the alternate back/menu mapping: publish one 150 ms START report, then two 150 ms BACK reports under one process-rescan and target-local activation envelope, rejecting the family if the sequential mapping still misses once", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_then_a_pair", "process_device_spoof_start_then_a_pair", "gamepad_process_rescan_spoof_start_then_a_sequence", "A is the established controller equivalent of the observed S/confirm prompt and has not been isolated on the highlight form; publish one proven 150 ms START mode-selection edge followed by two distinct 150 ms A confirm edges under one process-rescan activation envelope", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_a_combo2", "process_device_spoof_start_a_combo2", "gamepad_process_rescan_spoof_start_a_combo_sequence", "START-then-A cleared one highlight after a sham and an isolated A failure, but the game may consume mode selection and confirmation in the same controller poll; emit two simultaneous 150 ms START+A reports and reject this family on its first attributable miss", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_a_then_start_then_a", "process_device_spoof_a_then_start_then_a", "gamepad_process_rescan_spoof_a_start_a_sequence", "The first A may establish controller/confirm state before START changes the prompt mode; publish A, then START, then A as three separate 150 ms reports, and reject this order on its first attributable miss", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_then_a_single", "process_device_spoof_start_then_a_single", "gamepad_process_rescan_spoof_start_then_a_single_sequence", "START-then-A-pair physically exited both trials but the second was observed 193 ms late with a 694 ms synchronous guard; retain its first START and A edges while removing only the redundant second A to recover about 220 ms, rejecting on the first attributable miss", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_then_a_single_gap0", "process_device_spoof_start_then_a_single_gap0", "gamepad_process_rescan_spoof_start_then_a_single_gap0_sequence", "The two-edge START-then-A route missed by only 42 ms with a 473 ms guard; preserve both 150 ms holds and their distinct neutral releases while removing only the extra 50 ms idle gap, rejecting if the first attributable exit still exceeds 1.5 seconds", hold=0.15, gap=0.0, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_ds4_options_then_cross", "process_device_spoof_ds4_options_then_cross", "gamepad_ds4_process_rescan_spoof_options_cross_sequence", "Xbox START-then-A produced one strict exit while simultaneous START+A and isolated A missed; test the protocol-independent DS4 equivalent as separate 150 ms OPTIONS then CROSS edges after process-wide rescan and target-local activation, rejecting on the first attributable miss", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_ds4_options_then_cross_gap0", "process_device_spoof_ds4_options_then_cross_gap0", "gamepad_ds4_process_rescan_spoof_options_cross_gap0_sequence", "DS4 OPTIONS-then-CROSS remained visible only 8 ms beyond the strict deadline with a 474 ms guard; preserve both 150 ms DS4 edges and neutral releases while removing only the extra 50 ms idle gap, rejecting on the first attributable miss", hold=0.15, gap=0.0, input_scope="virtual_gamepad"),
    _spec("process_appcommand_browser_back", "process_appcommand_browser_back", "window_process_appcommand_browser_back", "All direct ESC key-message shapes are intermittent or failed, but FC overlays may route semantic navigation through CEF/DefWindowProc; post WM_APPCOMMAND Browser Back only to FC-owned HWNDs and reject on the first attributable miss", input_scope="target_window"),
    _spec("process_command_idcancel", "process_command_idcancel", "window_process_command_idcancel", "The highlight summary can behave like a modal overlay even when keyboard state is gated; post WM_COMMAND IDCANCEL only to FC-owned HWNDs and reject on the first attributable miss", input_scope="target_window"),
    _spec("process_notify_appcommand_browser_back", "process_notify_appcommand_browser_back", "window_process_notify_appcommand_browser_back", "PostMessage delivery did not reach the highlight, but cross-process SendNotifyMessage enters the target window procedure through a distinct sent-message path; send Browser Back only to FC-owned HWNDs and reject on the first attributable miss", input_scope="target_window"),
    _spec("process_notify_command_idcancel", "process_notify_command_idcancel", "window_process_notify_command_idcancel", "Separate IDCANCEL semantics from queued delivery by using target-only SendNotifyMessage across FC-owned HWNDs; reject on the first attributable miss", input_scope="target_window"),
    _spec("process_cancelmode", "process_cancelmode", "window_process_cancelmode", "The overlay may keep an internal modal or capture mode even though navigation commands are ignored; post WM_CANCELMODE only to FC-owned HWNDs and reject on the first attributable miss", input_scope="target_window"),
    _spec("process_device_spoof_start_refresh2", "process_device_spoof_start_refresh2", "gamepad_process_rescan_spoof_refreshed_pair", "The compact START pair reached 7/8 but occasionally missed a sparse inactive polling tick; preserve its two 150 ms holds and 50 ms neutral gap while re-publishing the held report every 25 ms", hold=0.15, gap=0.05, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_refresh650", "process_device_spoof_start_refresh650", "gamepad_process_rescan_spoof_refreshed_hold650", "Test whether the inactive loop needs a longer continuous polling window rather than more rising edges: hold START for 650 ms while refreshing its report every 25 ms, leaving observation time inside the 1.5 s deadline", hold=0.65, gap=0.0, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_single150", "process_device_spoof_start_single150", "gamepad_process_rescan_spoof_single150", "The 60 ms and 40 ms pairs were not consumed while compact's 150 ms holds were; keep one proven-length 150 ms START edge but remove the second pulse so capture resumes about 220 ms earlier", hold=0.15, gap=0.0, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_single180", "process_device_spoof_start_single180", "gamepad_process_rescan_spoof_single180", "If 150 ms is marginal, a single 180 ms START edge preserves the original proven hold threshold while still returning capture about 190 ms earlier than compact's two-pulse envelope", hold=0.18, gap=0.0, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_wake40_finish150", "process_device_spoof_start_wake40_finish150", "gamepad_process_rescan_spoof_wake40_finish150", "Single 150/180 ms START and two uniformly short pulses were not consumed, while two long pulses were; use a 40 ms controller-wake edge followed by one proven 150 ms consuming edge to retain two rises and shorten delivery by about 150 ms", hold=0.15, gap=0.01, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_wake60_finish150", "process_device_spoof_start_wake60_finish150", "gamepad_process_rescan_spoof_wake60_finish150", "If a 40 ms wake edge is below the re-enumerated controller polling threshold, extend only the wake edge to 60 ms and retain the proven 150 ms consuming edge", hold=0.15, gap=0.02, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_edge60_pair", "process_device_spoof_start_edge60_pair", "gamepad_process_rescan_spoof_edge60_pair", "The confirmed compact route eventually crossed 1.5 s while its synchronous 390 ms delivery blocked prompt observation; preserve two controller edges but shorten the envelope to two 60 ms holds with a 20 ms gap so capture resumes about 210 ms earlier", hold=0.06, gap=0.02, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_edge40_pair", "process_device_spoof_start_edge40_pair", "gamepad_process_rescan_spoof_edge40_pair", "If two 60 ms edges still lose the strict observation window, two 40 ms holds cover multiple render ticks while returning capture about 260 ms earlier than the confirmed compact route", hold=0.04, gap=0.01, input_scope="virtual_gamepad"),
    _spec("process_device_spoof_start_fast3", "process_device_spoof_start_fast3", "gamepad_process_rescan_spoof_fast_sequence", "The 120 ms settle plus two long START pulses missed the strict highlight deadline at 1.675 s; notify FC-owned windows and immediately front-load three 60 ms START samples with 20 ms gaps so the first controller edge reaches the earliest polling tick", hold=0.06, gap=0.02, input_scope="virtual_gamepad"),

    # Deep window-message paths.  All keyboard messages are addressed directly
    # to the game HWND tree and never use the global keyboard input stream.
    _spec("char_s", "char_s", "window_char", "CEF/메시지 UI가 WM_CHAR S 탭을 처리할 가능성"),
    _spec("char_s_hold_350", "char_s", "window_char", "WM_CHAR S의 짧은 홀드 임계값", hold=0.35),
    _spec("char_s_hold", "char_s", "window_char", "과거 실측에서 유망했던 WM_CHAR S 1초 홀드", hold=1.00),
    _spec("char_s_hold_1250", "char_s", "window_char", "WM_CHAR S 장홀드 경계", hold=1.25),
    _spec("char_s_pulse2", "char_s", "window_char", "WM_CHAR S 이중 펄스", hold=0.18, pulses=2, gap=0.10),
    _spec("pm_s", "pm_s", "window_char", "렌더 자식 트리에 WM_KEYDOWN S 탭 전달"),
    _spec("pm_s_hold_350", "pm_s", "window_char", "WM_KEYDOWN S 0.35초 홀드", hold=0.35),
    _spec("pm_s_hold", "pm_s", "window_char", "WM_KEYDOWN S 1초 홀드", hold=1.00),
    _spec("pm_s_hold_1250", "pm_s", "window_char", "WM_KEYDOWN S 장홀드", hold=1.25),
    _spec("pm_s_pulse2", "pm_s", "window_char", "WM_KEYDOWN S 이중 펄스", hold=0.18, pulses=2, gap=0.10),

    _spec("sync_char_s", "sync_char_s", "window_sync", "동기 SendMessage WM_CHAR 경로"),
    _spec("sync_char_s_hold", "sync_char_s", "window_sync", "동기 WM_CHAR S 1초 홀드", hold=1.00),
    _spec("sync_char_s_pulse2", "sync_char_s", "window_sync", "동기 WM_CHAR S 이중 펄스", hold=0.18, pulses=2, gap=0.10),
    _spec("sync_pm_s", "sync_pm_s", "window_sync", "동기 SendMessage WM_KEYDOWN 경로"),
    _spec("sync_pm_s_hold", "sync_pm_s", "window_sync", "동기 WM_KEYDOWN S 1초 홀드", hold=1.00),
    _spec("sync_pm_s_pulse2", "sync_pm_s", "window_sync", "동기 WM_KEYDOWN S 이중 펄스", hold=0.18, pulses=2, gap=0.10),

    _spec("spoof_char_s", "spoof_char_s", "window_spoof", "활성 메시지 플래그와 WM_CHAR S 탭"),
    _spec("spoof_char_s_hold", "spoof_char_s", "window_spoof", "활성 메시지 플래그와 WM_CHAR S 1초 홀드", hold=1.00),
    _spec("spoof_char_s_pulse2", "spoof_char_s", "window_spoof", "활성 메시지 플래그 안에서 WM_CHAR S 이중 펄스", hold=0.18, pulses=2, gap=0.10),
    _spec("spoof_pm_s", "spoof_pm_s", "window_spoof", "활성 메시지 플래그와 WM_KEYDOWN S 탭"),
    _spec("spoof_pm_s_hold", "spoof_pm_s", "window_spoof", "활성 메시지 플래그와 WM_KEYDOWN S 1초 홀드", hold=1.00),
    _spec("spoof_pm_s_pulse2", "spoof_pm_s", "window_spoof", "활성 메시지 플래그 안에서 WM_KEYDOWN S 이중 펄스", hold=0.18, pulses=2, gap=0.10),

    _spec("attach_state_s", "attach_state_s", "keyboard_state", "AttachThreadInput+키 상태 S 탭"),
    _spec("attach_state_s_hold_500", "attach_state_s", "keyboard_state", "공유 키 상태 S 0.5초 홀드", hold=0.50),
    _spec("attach_state_s_hold", "attach_state_s", "keyboard_state", "공유 키 상태 S 1초 홀드", hold=1.00),
    _spec("attach_state_s_hold_1250", "attach_state_s", "keyboard_state", "공유 키 상태 S 장홀드", hold=1.25),
    _spec("attach_post_s", "attach_post_s", "keyboard_state", "AttachThreadInput 큐 공유+S 탭"),
    _spec("attach_post_s_hold_500", "attach_post_s", "keyboard_state", "공유 큐 S 0.5초 홀드", hold=0.50),
    _spec("attach_post_s_hold", "attach_post_s", "keyboard_state", "공유 큐 S 1초 홀드", hold=1.00),
    _spec("attach_post_s_hold_1250", "attach_post_s", "keyboard_state", "공유 큐 S 장홀드", hold=1.25),

    # A captured generic prompt explicitly displayed "ESC SKIP".  These are
    # target-window-only Escape paths; none use the global keyboard stream.
    _spec("pm_esc", "pm_esc", "window_escape", "Deliver the displayed ESC to the target HWND tree with WM_KEYDOWN/UP"),
    _spec("pm_esc_hold_350", "pm_esc", "window_escape", "Test a 0.35 second ESC message threshold", hold=0.35),
    _spec("pm_esc_hold", "pm_esc", "window_escape", "Test a one second ESC message threshold", hold=1.00),
    _spec("pm_esc_pulse2", "pm_esc", "window_escape", "Test whether the prompt requires two ESC transitions", hold=0.18, pulses=2, gap=0.10),
    _spec("sync_pm_esc", "sync_pm_esc", "window_escape_sync", "Synchronously deliver ESC to window procedures that ignore queued keys"),
    _spec("sync_pm_esc_hold", "sync_pm_esc", "window_escape_sync", "Synchronously hold ESC for one second", hold=1.00),
    _spec("sync_pm_esc_pulse2", "sync_pm_esc", "window_escape_sync", "Synchronously deliver two ESC transitions", hold=0.18, pulses=2, gap=0.10),
    _spec("sync_pm_esc_pulse3", "sync_pm_esc", "window_escape_sync_timing", "The partially successful synchronous route may require three frame-spaced transitions", hold=0.10, pulses=3, gap=0.05),
    _spec("sync_pm_esc_burst5", "sync_pm_esc", "window_escape_sync_timing", "Cover a wider render-tick window with five bounded synchronous transitions", hold=0.05, pulses=5, gap=0.03),
    _spec("sync_pm_esc_delay50", "sync_pm_esc_delay50", "window_escape_sync_timing", "Shift synchronous ESC by 50 ms to test render-tick alignment"),
    _spec("sync_pm_esc_delay150", "sync_pm_esc_delay150", "window_escape_sync_timing", "Shift synchronous ESC by 150 ms to test render-tick alignment"),
    _spec("sys_pm_esc", "sys_pm_esc", "window_escape_system", "Deliver ESC through the target-only WM_SYSKEYDOWN branch"),
    _spec("sys_pm_esc_pulse2", "sys_pm_esc", "window_escape_system", "Deliver two target-only WM_SYSKEYDOWN ESC transitions", hold=0.18, pulses=2, gap=0.10),
    _spec("spoof_pm_esc", "spoof_pm_esc", "window_escape_spoof", "Announce inactive activation messages before delivering ESC"),
    _spec("focusmsg_pm_esc", "focusmsg_pm_esc", "window_escape_focus_component", "The full activation envelope may cancel an internal focus gate; send only target-local WM_SETFOCUS, deliver ESC, then WM_KILLFOCUS while continuously auditing the real foreground"),
    _spec("appmsg_pm_esc", "appmsg_pm_esc", "window_escape_app_component", "Test whether only FC's WM_ACTIVATEAPP flag gates ESC without sending window/focus activation messages or changing OS focus"),
    _spec("windowmsg_pm_esc", "windowmsg_pm_esc", "window_escape_window_component", "Test only WM_NCACTIVATE plus WM_ACTIVATE around target-window ESC so app and focus flags cannot counteract the window-active gate"),
    _spec("spoof_pm_esc_hold", "spoof_pm_esc", "window_escape_spoof", "Combine inactive activation messages with a one second ESC hold", hold=1.00),
    _spec("spoof_pm_esc_pulse2", "spoof_pm_esc", "window_escape_spoof", "Deliver two ESC transitions inside the inactive activation envelope", hold=0.18, pulses=2, gap=0.10),
    _spec("spoof_pm_esc_pulse3", "spoof_pm_esc", "window_escape_spoof", "Two-pulse results were intermittent; test three bounded ESC transitions", hold=0.14, pulses=3, gap=0.08),
    _spec("spoof_pm_esc_envelope2", "spoof_pm_esc_envelope2", "window_escape_spoof_sequence", "Keep one target-local activation envelope across both ESC transitions; the equivalent continuous envelope made START reproducible while per-pulse spoofing remained intermittent", hold=0.18),
    _spec("spoof_start_envelope2_escape_block", "spoof_start_envelope2", "window_escape_gamepad_spoof_sequence", "A generation-5 ESC prompt disappeared 46 ms after the same START envelope that is 3/3 on any-key; run a fresh ESC-only reproduction block and reject on any non-reproduced controlled attempt", hold=0.18, input_scope="virtual_gamepad"),
    _spec("spoof_pm_esc_settle150", "spoof_pm_esc_settle150", "window_escape_spoof_timing", "Allow one render frame after inactive activation messages before ESC"),
    _spec("spoof_sync_pm_esc", "spoof_sync_pm_esc", "window_escape_spoof_sync", "Combine inactive activation state with synchronous target-window ESC"),
    _spec("attach_state_esc", "attach_state_esc", "window_escape_state", "Expose ESC down through the attached target thread keyboard state"),
    _spec("attach_state_esc_hold", "attach_state_esc", "window_escape_state", "Expose ESC down in attached keyboard state for one second", hold=1.00),
    _spec("attach_post_esc", "attach_post_esc", "window_escape_state", "Post ESC to the target thread's own focused HWND"),
    _spec("attach_post_esc_hold", "attach_post_esc", "window_escape_state", "Hold ESC on the target thread's focused HWND for one second", hold=1.00),
    _spec("char_esc", "char_esc", "window_escape_char", "Deliver ESC as WM_KEYDOWN plus WM_CHAR 0x1B to the FC window"),
    _spec("char_esc_hold", "char_esc", "window_escape_char", "Hold the WM_CHAR 0x1B ESC route for one second", hold=1.00),
    _spec("char_esc_pulse2", "char_esc", "window_escape_char", "Deliver two WM_CHAR 0x1B ESC transitions", hold=0.18, pulses=2, gap=0.10),
    _spec("sync_char_esc", "sync_char_esc", "window_escape_sync_char", "Synchronously deliver ESC with WM_CHAR 0x1B"),
    _spec("sync_char_esc_hold", "sync_char_esc", "window_escape_sync_char", "Synchronously hold the ESC plus WM_CHAR route for one second", hold=1.00),
    _spec("spoof_char_esc", "spoof_char_esc", "window_escape_spoof_char", "Deliver ESC plus WM_CHAR 0x1B inside an inactive activation envelope"),
    _spec("spoof_char_esc_hold", "spoof_char_esc", "window_escape_spoof_char", "Hold inactive-spoofed ESC plus WM_CHAR for one second", hold=1.00),
    _spec("thread_pm_esc", "thread_pm_esc", "window_escape_thread", "Post ESC directly to the FC UI thread queue"),
    _spec("thread_pm_esc_hold", "thread_pm_esc", "window_escape_thread", "Hold the FC UI thread-queue ESC route for one second", hold=1.00),
    _spec("thread_pm_esc_pulse2", "thread_pm_esc", "window_escape_thread", "Post two ESC transitions to the FC UI thread queue", hold=0.18, pulses=2, gap=0.10),
    _spec("thread_char_esc", "thread_char_esc", "window_escape_thread", "Post ESC plus WM_CHAR 0x1B to the FC UI thread queue"),
    _spec("thread_char_esc_hold", "thread_char_esc", "window_escape_thread", "Hold the thread-queue ESC plus WM_CHAR route for one second", hold=1.00),
    _spec("thread_sys_esc", "thread_sys_esc", "window_escape_thread_system", "Post WM_SYSKEY ESC directly to the FC UI thread queue"),
    _spec("thread_sys_esc_pulse2", "thread_sys_esc", "window_escape_thread_system", "Post two WM_SYSKEY ESC transitions to the FC UI thread queue", hold=0.12, pulses=2, gap=0.06),
    _spec("thread_sys_esc_pulse3", "thread_sys_esc", "window_escape_thread_system_timing", "Two thread SYSKEY pulses were intermittent; cover three adjacent render ticks", hold=0.08, pulses=3, gap=0.04),
    _spec("thread_sys_esc_burst5", "thread_sys_esc", "window_escape_thread_system_timing", "Cover a wider interval with five short target-thread SYSKEY transitions", hold=0.04, pulses=5, gap=0.025),
    _spec("spoof_thread_sys_esc", "spoof_thread_sys_esc", "window_escape_thread_system_spoof", "Combine inactive activation state with the partially successful target-thread SYSKEY route"),
    _spec("spoof_thread_sys_esc_pulse2", "spoof_thread_sys_esc", "window_escape_thread_system_spoof", "Send two target-thread SYSKEY transitions inside the inactive activation envelope", hold=0.12, pulses=2, gap=0.06),
    _spec("notify_pm_esc", "notify_pm_esc", "window_escape_notify", "Use target-only SendNotifyMessage for ESC to separate notify delivery from Post/SendMessage"),
    _spec("callback_pm_esc", "callback_pm_esc", "window_escape_callback", "Use target-only SendMessageCallback for ESC; reject if three controlled prompts do not exit within 1.5 seconds"),
    _spec("down_pm_esc", "down_pm_esc", "window_escape_transition", "Send only the ESC down edge to test frame-sampled transition handling", hold=0.0),
    _spec("up_pm_esc", "up_pm_esc", "window_escape_transition", "Send only the ESC release edge to test whether release drives the prompt", hold=0.0),
    _spec("process_pm_esc", "process_pm_esc", "window_escape_process", "FC owns hidden sibling HWNDs such as DIEmWin; post ESC to every HWND in FC's PID instead of only the render tree"),
    _spec("process_sys_esc", "process_sys_esc", "window_escape_process_system", "Test the WM_SYSKEY ESC branch across FC's hidden sibling top-level windows and their threads"),
    _spec("process_thread_pm_esc", "process_thread_pm_esc", "window_escape_process_thread", "Post ESC to every FC-owned GUI thread queue, including the hidden DirectInput and Chrome threads"),
    _spec("process_thread_sys_esc", "process_thread_sys_esc", "window_escape_process_thread_system", "Post WM_SYSKEY ESC to all FC GUI thread queues rather than only the visible render thread"),
    _spec("spoof_process_thread_sys_esc", "spoof_process_thread_sys_esc", "window_escape_process_thread_spoof", "Combine FC-local activation flags with all-process-thread WM_SYSKEY delivery"),
    _spec("spoof_process_thread_sys_esc_envelope2", "spoof_process_thread_sys_esc_envelope2", "window_escape_process_thread_spoof_sequence", "Keep one FC-local activation envelope across two all-process-thread SYSKEY transitions so hidden DirectInput and render queues see a continuous active interval", hold=0.12),
    # The other captured generic form says "press any key (except Enter)".
    # Space exercises a different window-message path than ESC while remaining
    # target-window-only, so it is a useful follow-up after queued ESC fails.
    _spec("pm_space", "pm_space", "window_anykey", "Deliver Space WM_KEYDOWN/UP to the target tree for the explicit any-key prompt"),
    _spec("pm_space_hold", "pm_space", "window_anykey", "Hold target-window Space for one second on the any-key prompt", hold=1.00),
    _spec("pm_space_hold_1150", "pm_space", "window_anykey_timing_1150", "The one-second highlight hold missed while 1.25 seconds exited twice before an intermittent miss; release at 1.15 seconds to test the lower edge of the apparent key-up window", hold=1.15),
    _spec("pm_space_hold_1250", "pm_space", "window_anykey_timing", "The partial one-second Space result may fire on key release; move release to 1.25 seconds", hold=1.25),
    _spec("pm_space_hold_1350", "pm_space", "window_anykey_timing_1350", "Release target-only Space at 1.35 seconds to test the upper edge of the apparent key-up window while leaving 150 ms inside the strict attribution limit", hold=1.35),
    _spec("pm_space_pulse2", "pm_space", "window_anykey_timing", "Test whether two target-only Space transitions are more reliable than one long hold", hold=0.40, pulses=2, gap=0.10),
    _spec("char_space", "char_space", "window_anykey_char", "Deliver WM_CHAR Space to UI code that ignores queued ESC key messages"),
    _spec("char_space_hold", "char_space", "window_anykey_char", "Hold the target-window WM_CHAR Space path for one second", hold=1.00),
    _spec("sync_pm_space", "sync_pm_space", "window_anykey_sync", "Synchronously deliver Space to window procedures that intermittently miss queued Space", hold=1.00),
    _spec("sync_pm_space_hold_1250", "sync_pm_space", "window_anykey_sync", "Synchronously release Space at 1.25 seconds to test the observed release-time cluster", hold=1.25),
    _spec("thread_pm_space", "thread_pm_space", "window_anykey_thread", "Post Space directly to the FC UI thread queue while the real foreground stays unchanged", hold=1.00),
    _spec("spoof_pm_space", "spoof_pm_space", "window_anykey_spoof", "Combine inactive activation messages with the partially successful one-second Space route", hold=1.00),
    _spec("notify_pm_space", "notify_pm_space", "window_anykey_notify", "Use target-only SendNotifyMessage to test a third Space delivery API", hold=1.00),
    _spec("callback_pm_space", "callback_pm_space", "window_anykey_callback", "Use target-only SendMessageCallback for Space; reject if three controlled prompts do not exit within 1.5 seconds"),
    _spec("down_pm_space", "down_pm_space", "window_anykey_transition", "Send only the Space down edge to test delayed frame sampling", hold=0.0),
    _spec("up_pm_space", "up_pm_space", "window_anykey_transition", "Send only the Space release edge suggested by the one-second success cluster", hold=0.0),
    _spec("process_pm_space", "process_pm_space", "window_anykey_process", "The any-key handler may live in FC's hidden sibling HWND rather than the visible render window"),
    _spec("process_thread_pm_space", "process_thread_pm_space", "window_anykey_process_thread", "Post Space to every FC-owned GUI thread queue for the explicit any-key prompt"),
    _spec("attach_state_space", "attach_state_space", "window_anykey_state", "Expose Space through the attached target thread keyboard state"),
    _spec("attach_post_space", "attach_post_space", "window_anykey_state", "Post Space to the target thread's own focused HWND"),
    _spec("b", "b", "gamepad", "Test whether the generic prompt maps to controller cancel (B)", input_scope="virtual_gamepad"),
    _spec("b_hold", "b", "gamepad", "Test a one second controller B hold", hold=1.00, input_scope="virtual_gamepad"),
    _spec("back", "back", "gamepad", "Test whether the generic prompt maps to controller BACK", input_scope="virtual_gamepad"),
    _spec("back_hold", "back", "gamepad", "Test a one second controller BACK hold", hold=1.00, input_scope="virtual_gamepad"),
    _spec("spoof_b_envelope2", "spoof_b_envelope2", "gamepad_escape_spoof_sequence", "Transfer the proven inactive activation-envelope mechanism to the controller cancel mapping with two B pulses; reject after three controlled ESC prompts remain visible", hold=0.18, input_scope="virtual_gamepad"),
    _spec("spoof_back_envelope2", "spoof_back_envelope2", "gamepad_escape_spoof_sequence", "Transfer the proven inactive activation-envelope mechanism to the controller Back/Select mapping with two BACK pulses; reject after three controlled ESC prompts remain visible", hold=0.18, input_scope="virtual_gamepad"),
    _spec("device_start", "device_start", "gamepad_rescan", "Notify FC of the secondary XInput slot before a START transition", input_scope="virtual_gamepad"),
    _spec("device_a", "device_a", "gamepad_rescan", "Notify FC of the secondary XInput slot before an A transition", input_scope="virtual_gamepad"),
    _spec("device_start_hold500", "device_start", "gamepad_rescan", "Re-enumerate the secondary XInput slot before a 500 ms START hold", hold=0.50, input_scope="virtual_gamepad"),
    _spec("device_start_pulse2", "device_start", "gamepad_rescan", "Re-enumerate before two START transitions", hold=0.12, pulses=2, gap=0.06, input_scope="virtual_gamepad"),
    _spec("device_a_pulse2", "device_a", "gamepad_rescan", "Re-enumerate before two A transitions", hold=0.12, pulses=2, gap=0.06, input_scope="virtual_gamepad"),
    _spec("ds4_cross", "ds4_cross", "gamepad_ds4", "FC may consume the virtual DualShock DirectInput path while inactive even when secondary XInput is ignored", input_scope="virtual_gamepad"),
    _spec("ds4_cross_hold500", "ds4_cross", "gamepad_ds4", "Test a 500 ms DS4 Cross threshold on the independent controller path", hold=0.50, input_scope="virtual_gamepad"),
    _spec("ds4_options", "ds4_options", "gamepad_ds4", "Test the DS4 Options mapping for START-style skip prompts", input_scope="virtual_gamepad"),
    _spec("ds4_options_hold500", "ds4_options", "gamepad_ds4", "Test a 500 ms DS4 Options threshold", hold=0.50, input_scope="virtual_gamepad"),
    _spec("ds4_circle", "ds4_circle", "gamepad_ds4_cancel", "The displayed ESC action may map to controller cancel; test DS4 Circle through the independent DirectInput-compatible virtual pad", input_scope="virtual_gamepad"),
    _spec("ds4_share", "ds4_share", "gamepad_ds4_back", "The displayed ESC action may map to the controller Back/Select path rather than Circle; test DS4 Share through the independent virtual pad", input_scope="virtual_gamepad"),
    _spec("ds4_touchpad", "ds4_touchpad", "gamepad_ds4_touchpad", "FC may map an inactive DirectInput touchpad-click bit to its menu/any-key action even when standard DS4 buttons are gated; reject after three controlled prompts remain visible", input_scope="virtual_gamepad"),
    _spec("spoof_ds4_cross", "spoof_ds4_cross", "gamepad_ds4_spoof", "The game may poll DS4 only while its target-local activation flag is set; wrap Cross in an inactive activation envelope", input_scope="virtual_gamepad"),
    _spec("spoof_ds4_options", "spoof_ds4_options", "gamepad_ds4_spoof", "The game may poll DS4 only while its target-local activation flag is set; wrap Options in an inactive activation envelope", input_scope="virtual_gamepad"),
    _spec("spoof_ds4_circle", "spoof_ds4_circle", "gamepad_ds4_cancel_spoof", "Combine target-local activation messages with DS4 Circle in case FC gates the controller cancel mapping while inactive", input_scope="virtual_gamepad"),
    _spec("spoof_ds4_share", "spoof_ds4_share", "gamepad_ds4_back_spoof", "Combine target-local activation messages with DS4 Share in case FC polls the Back/Select mapping only while its local active flag is set", input_scope="virtual_gamepad"),
    _spec("device_ds4_cross", "device_ds4_cross", "gamepad_ds4_rescan", "The background game may need a target-scoped device-change notification before polling the virtual DS4 Cross state", input_scope="virtual_gamepad"),
    _spec("device_ds4_options", "device_ds4_options", "gamepad_ds4_rescan", "The background game may need a target-scoped device-change notification before polling the virtual DS4 Options state", input_scope="virtual_gamepad"),
    _spec("device_ds4_circle", "device_ds4_circle", "gamepad_ds4_cancel_rescan", "Notify only the FC render window of the virtual DS4 before testing the cancel mapping", input_scope="virtual_gamepad"),
    _spec("device_ds4_share", "device_ds4_share", "gamepad_ds4_back_rescan", "Notify only the FC render window of the virtual DS4 before testing the Back/Select mapping", input_scope="virtual_gamepad"),
    _spec("device_ds4_touchpad", "device_ds4_touchpad", "gamepad_ds4_touchpad_rescan", "Notify only FC of the virtual DS4 before the touchpad-click transition in case the inactive DirectInput device slot is stale; reject after three controlled prompts remain visible", input_scope="virtual_gamepad"),
    _spec("device_ds4_rescan_control", "device_ds4_rescan_control", "gamepad_ds4_rescan_control", "Prepare the same virtual DS4 and notify only the FC render window, but send no button, to separate rescan effects and natural exits from Share", input_scope="target_window"),
    _spec("device_ds4_share_hold500", "device_ds4_share", "gamepad_ds4_back_rescan_timing", "The first rescan-plus-Share exit arrived near one second; test whether a 500 ms Share state is required after the same target-only rescan", hold=0.50, input_scope="virtual_gamepad"),
    _spec("process_device_start", "process_device_start", "gamepad_process_rescan", "Send device-tree change to FC's hidden process-owned message windows before a virtual START transition", input_scope="virtual_gamepad"),
    _spec("raw_device_start", "raw_device_start", "gamepad_raw_arrival", "Post valid Raw Input gamepad arrival handles to FC-owned windows before virtual Xbox START", input_scope="virtual_gamepad"),
    _spec("raw_device_ds4_options", "raw_device_ds4_options", "gamepad_raw_arrival_ds4", "Create the neutral DS4 first, then post Raw Input gamepad arrivals to FC before Options", input_scope="virtual_gamepad"),
    _spec("raw_device_ds4_circle", "raw_device_ds4_circle", "gamepad_raw_arrival_ds4_cancel", "Create the neutral DS4, post only valid gamepad Raw Input arrival handles to FC, then test Circle as controller cancel", input_scope="virtual_gamepad"),
    _spec("raw_device_ds4_share", "raw_device_ds4_share", "gamepad_raw_arrival_ds4_back", "Create the neutral DS4, post only valid gamepad Raw Input arrival handles to FC, then test Share as controller Back/Select", input_scope="virtual_gamepad"),
)


SKIP_CANDIDATE_SPECS = {spec.name: spec for spec in _SPECS}


def get_skip_candidate_spec(name: str) -> Optional[SkipCandidateSpec]:
    return SKIP_CANDIDATE_SPECS.get(str(name).strip().lower())


def skip_candidate_families(names) -> dict[str, str]:
    result = {}
    for name in names:
        spec = get_skip_candidate_spec(name)
        if spec is not None:
            result[spec.name] = spec.family
    return result


# A prompt starts with gamepad/message-spoof coverage.  S starts with the
# keyboard-message families.  The scheduler still balances by family, so order
# only resolves equal evidence rather than starving later hypotheses.
_COMMON = tuple(
    spec.name for spec in _SPECS
    if spec.family != "sham"
    and not spec.family.startswith("window_escape")
    and spec.name not in {"b", "b_hold", "back", "back_hold"}
)
SKIP_A_CANDIDATES = ("control_noop",) + _COMMON
_S_START_ENVELOPE_PRIORITY = (
    "process_device_spoof_a_envelope2",
    "spoof_a_envelope2",
    "spoof_a_preactivate80_burst3",
    "spoof_start_preactivate80_burst3",
    "spoof_start_envelope4_fast",
    "spoof_start_envelope2",
    "spoof_start_envelope3",
    "spoof_start_envelope2_settle150",
    "spoof_start_envelope2_settle250",
    "spoof_start_envelope2_settle350",
)
_S_LEGACY_CANDIDATES = tuple(
    name for name in _COMMON
    if SKIP_CANDIDATE_SPECS[name].family in {
        "window_char", "window_sync", "window_spoof", "keyboard_state",
        "child_focus", "mouse_message",
    }
) + tuple(
    name for name in _COMMON
    if SKIP_CANDIDATE_SPECS[name].family in {
        "gamepad",
        "gamepad_spoof",
        "gamepad_spoof_sequence",
        "gamepad_spoof_sequence_settle",
        "gamepad_spoof_preactivation_timing",
        "gamepad_spoof_dense_sequence",
        "gamepad_s_spoof_sequence",
        "gamepad_s_spoof_preactivation",
        "gamepad_s_process_rescan_spoof_sequence",
    }
)
SKIP_S_CANDIDATES = tuple(dict.fromkeys(
    ("control_noop",) + _S_START_ENVELOPE_PRIORITY + _S_LEGACY_CANDIDATES
))

# Generic OCR prompts currently include a captured "ESC SKIP" presentation.
# Escape message families lead, while START/B/BACK remain independent mapping
# hypotheses for controller-mode presentations of the same prompt.
_GENERIC_FAMILIES = {
    "mouse_message", "mouse_message_sync", "mouse_message_activation",
    "window_escape", "window_escape_sync", "window_escape_spoof",
    "window_escape_focus_component", "window_escape_app_component",
    "window_escape_window_component",
    "window_escape_state", "window_escape_char", "window_escape_sync_char",
    "window_escape_spoof_char", "window_escape_thread",
    "window_escape_system", "window_escape_spoof_timing",
    "window_escape_spoof_sync", "window_escape_sync_timing",
    "window_escape_thread_system", "window_escape_thread_system_timing",
    "window_escape_thread_system_spoof", "gamepad_rescan",
    "window_escape_notify", "window_escape_callback",
    "window_escape_transition", "gamepad_ds4",
    "gamepad_ds4_spoof", "gamepad_ds4_rescan",
    "window_escape_process", "window_escape_process_system",
    "window_escape_process_thread", "window_escape_process_thread_system",
    "window_escape_process_thread_spoof",
    "window_escape_process_thread_spoof_sequence",
    "window_escape_spoof_sequence",
    "window_escape_gamepad_spoof_sequence",
    "window_anykey", "window_anykey_timing",
    "window_anykey_timing_1150", "window_anykey_timing_1350",
    "window_anykey_char",
    "window_anykey_sync", "window_anykey_thread", "window_anykey_spoof",
    "window_anykey_notify", "window_anykey_callback",
    "window_anykey_transition", "window_anykey_state",
    "window_anykey_process", "gamepad_process_rescan",
    "window_anykey_process_thread",
    "gamepad_raw_arrival", "gamepad_raw_arrival_ds4",
    "gamepad_ds4_cancel", "gamepad_ds4_cancel_spoof",
    "gamepad_ds4_cancel_rescan", "gamepad_raw_arrival_ds4_cancel",
    "gamepad_ds4_back", "gamepad_ds4_back_spoof",
    "gamepad_ds4_back_rescan", "gamepad_raw_arrival_ds4_back",
    "gamepad_ds4_rescan_control", "gamepad_ds4_back_rescan_timing",
    "gamepad_ds4_touchpad", "gamepad_ds4_touchpad_rescan",
    "window_anykey_spoof_control",
    "gamepad_escape_spoof_sequence",
}
_GENERIC_GAMEPAD_SPOOF = (
    # Four legacy-generation successes were visually verified as the any-key
    # generic prompt, not A.  Do not restore those successes, but prioritize
    # the same safe delivery family for clean re-validation.
    "spoof_start_hold", "spoof_start_hold_500", "spoof_start",
    "spoof_start_hold_1250", "spoof_start_pulse2",
    "spoof_start_envelope2", "spoof_envelope2_control",
    "process_device_spoof_start_envelope2",
    "process_device_spoof_start_envelope2_settle0",
    "process_device_spoof_start_envelope2_gap50",
    "process_device_spoof_start_envelope2_compact",
    "process_device_spoof_start_pair_rehandshake2",
    "process_device_spoof_start_rescan2_pair",
    "process_device_spoof_start_activation_rearm_pair",
    "process_device_spoof_start_compact_control80",
    "process_device_spoof_start_preactivate150_pair",
    "process_device_spoof_reset_start_compact",
    "process_device_sync_spoof_start_compact",
    "process_raw_spoof_start_compact",
    "process_device_raw_spoof_start_compact",
    "process_spoof_device_start_compact",
    "process_spoof_device_sync_start_compact",
    "process_spoof_device_raw_sync_start_compact",
    "process_spoof_device_raw_parallel_start_compact",
    "process_spoof_diapp_device_raw_parallel_start_compact",
    "process_spoof_device_raw_parallel_start_compact3",
    "process_spoof_device_raw_parallel_start_rehandshake2",
    "process_spoof_device_raw_stagger30_start_compact",
    "process_spoof_reset_device_raw_parallel_start_compact",
    "process_spoof_device_raw_parallel_start_refresh2",
    "process_device_spoof_start_wait300_a",
    "process_device_spoof_diapp_start_compact",
    "process_raw_sync_spoof_start_compact",
    "process_device_spoof_start_compact_fixed3",
    "process_device_spoof_start_compact4",
    "process_device_spoof_start_compact4_gap10",
    "process_device_spoof_start_compact3",
    "process_device_spoof_start_compact4_hold130",
    "process_device_spoof_start_b_combo2",
    "process_device_spoof_start_back_combo2",
    "process_device_spoof_start_then_b_pair",
    "process_device_spoof_start_then_back_pair",
    "process_device_spoof_start_then_a_pair",
    "process_device_spoof_start_a_combo2",
    "process_device_spoof_a_then_start_then_a",
    "process_device_spoof_start_then_a_single",
    "process_device_spoof_start_then_a_single_gap0",
    "process_device_spoof_ds4_options_then_cross",
    "process_device_spoof_ds4_options_then_cross_gap0",
    "process_appcommand_browser_back",
    "process_command_idcancel",
    "process_notify_appcommand_browser_back",
    "process_notify_command_idcancel",
    "process_cancelmode",
    "process_device_spoof_start_refresh2",
    "process_device_spoof_start_refresh650",
    "process_device_spoof_start_single150",
    "process_device_spoof_start_single180",
    "process_device_spoof_start_wake40_finish150",
    "process_device_spoof_start_wake60_finish150",
    "process_device_spoof_start_edge60_pair",
    "process_device_spoof_start_edge40_pair",
    "process_device_spoof_start_fast3",
        "spoof_start_preactivate80_burst3", "spoof_start_envelope4_fast",
        "spoof_start_envelope3",
        "spoof_start_envelope2_settle150",
        "spoof_start_envelope2_settle250",
        "spoof_start_envelope2_settle350",
        "focusmsg_start_envelope2",
        "appmsg_start_envelope2",
        "windowmsg_start_envelope2",
        "focuswindow_start_envelope2",
        "focusmsg_start_compact",
        "windowmsg_start_compact",
        "windowmsg_start_refresh2",
        "windowmsg_start_edge2",
        "windowmsg_start_compact_settle100",
        "windowmsg_start_compact_settle200",
        "focuswindow_start_compact",
        "focusmsg_start_spread650",
        "windowmsg_start_spread650",
        "focuswindow_start_spread650",
    "spoof_a_hold", "spoof_a_hold_500", "spoof_a_hold_1250",
    "spoof_a_pulse2",
)
_GENERIC_ESCAPE_ROUTES = tuple(
    spec.name for spec in _SPECS
    if spec.family in _GENERIC_FAMILIES
    and not spec.family.startswith("mouse_message")
)


def _is_direct_escape_delivery(name: str) -> bool:
    """Return whether a candidate delivers an ESC keyboard/window message."""

    family = SKIP_CANDIDATE_SPECS[name].family
    return (
        family.startswith("window_escape")
        and family != "window_escape_gamepad_spoof_sequence"
    )


# Prompt names such as ``escape``/``escape_highlight`` describe what OCR saw;
# they must not authorize sending ESC. Automatic skip catalogues use controller
# and other non-keyboard routes only.
_GENERIC_NON_ESC_ROUTES = tuple(
    name for name in _GENERIC_ESCAPE_ROUTES
    if not _is_direct_escape_delivery(name)
)
_GENERIC_MOUSE_ROUTES = tuple(
    spec.name for spec in _SPECS
    if spec.family.startswith("mouse_message")
)
_GENERIC_REGULAR_GAMEPAD = tuple(
    name for name in (
        "a", "a_hold_500", "a_hold", "a_hold_1250", "a_pulse2",
        "start", "start_hold_500", "start_hold", "start_hold_1250",
        "start_pulse2", "b", "b_hold", "back", "back_hold",
    )
    if name in SKIP_CANDIDATE_SPECS
)

# Keep independent search histories for the two observed generic forms.  A
# verified START-spoof succeeds on ``any_key`` but failed on ``escape``; sharing
# one tracker therefore makes confirmation oscillate between incompatible
# prompts.  Both catalogues still retain all safe routes, only the evidence-
# driven priority differs.
SKIP_GENERIC_ANY_KEY_CANDIDATES = tuple(dict.fromkeys(
    ("control_noop",)
    + _GENERIC_GAMEPAD_SPOOF
    + tuple(
        name for name in _GENERIC_NON_ESC_ROUTES
        if SKIP_CANDIDATE_SPECS[name].family.startswith("window_anykey")
    )
    + _GENERIC_MOUSE_ROUTES
    + _GENERIC_REGULAR_GAMEPAD
    + tuple(
        name for name in _GENERIC_NON_ESC_ROUTES
        if not SKIP_CANDIDATE_SPECS[name].family.startswith("window_anykey")
    )
))
SKIP_GENERIC_ESCAPE_CANDIDATES = tuple(dict.fromkeys(
    ("control_noop",)
    + tuple(
        name for name in _GENERIC_NON_ESC_ROUTES
        if not SKIP_CANDIDATE_SPECS[name].family.startswith("window_anykey")
    )
    + _GENERIC_MOUSE_ROUTES
    + tuple(
        name for name in ("b", "b_hold", "back", "back_hold")
        if name in SKIP_CANDIDATE_SPECS
    )
    + _GENERIC_GAMEPAD_SPOOF
    + _GENERIC_REGULAR_GAMEPAD
    + tuple(
        name for name in _GENERIC_NON_ESC_ROUTES
        if SKIP_CANDIDATE_SPECS[name].family.startswith("window_anykey")
    )
))
SKIP_GENERIC_HIGHLIGHT_CANDIDATES = tuple(dict.fromkeys(
    (
        "control_noop",
        "process_device_spoof_start_preactivate150_pair",
        "process_device_spoof_reset_start_compact",
        "process_device_sync_spoof_start_compact",
        "process_raw_spoof_start_compact",
        "process_device_raw_spoof_start_compact",
        "process_spoof_device_start_compact",
        "process_spoof_device_sync_start_compact",
        "process_spoof_device_raw_sync_start_compact",
        "process_spoof_device_raw_parallel_start_compact",
        "process_spoof_diapp_device_raw_parallel_start_compact",
        "process_spoof_device_raw_parallel_start_compact3",
        "process_spoof_device_raw_parallel_start_rehandshake2",
        "process_spoof_device_raw_stagger30_start_compact",
        "process_spoof_reset_device_raw_parallel_start_compact",
        "process_spoof_device_raw_parallel_start_refresh2",
        "process_device_spoof_start_wait300_a",
        "process_device_spoof_diapp_start_compact",
        "process_raw_sync_spoof_start_compact",
        "process_device_spoof_start_rescan2_pair",
        "process_device_spoof_start_activation_rearm_pair",
        "process_device_spoof_start_compact_control80",
        "process_device_spoof_start_pair_rehandshake2",
        "process_device_spoof_start_b_combo2",
        "process_device_spoof_start_back_combo2",
        "process_device_spoof_start_then_b_pair",
        "process_device_spoof_start_then_back_pair",
        "process_device_spoof_a_envelope2",
        "process_device_spoof_start_then_a_pair",
        "process_device_spoof_start_a_combo2",
        "process_device_spoof_a_then_start_then_a",
        "process_device_spoof_start_then_a_single",
        "process_device_spoof_ds4_options_then_cross_gap0",
        "process_appcommand_browser_back",
        "process_command_idcancel",
        "process_notify_appcommand_browser_back",
        "process_notify_command_idcancel",
        "process_cancelmode",
        "process_device_spoof_start_refresh2",
        "process_device_spoof_start_refresh650",
        "windowmsg_start_refresh2",
        "windowmsg_start_edge2",
        "windowmsg_start_compact_settle100",
        "windowmsg_start_compact_settle200",
        "process_device_spoof_start_then_a_single_gap0",
        "process_device_spoof_ds4_options_then_cross",
        "process_device_spoof_start_compact_fixed3",
        "process_device_spoof_start_compact3",
        "process_device_spoof_start_compact4_hold130",
        "process_device_spoof_start_compact4_gap10",
        "process_device_spoof_start_compact4",
        "focusmsg_start_spread650",
        "windowmsg_start_spread650",
        "focuswindow_start_spread650",
        "focusmsg_start_compact",
        "windowmsg_start_compact",
        "focuswindow_start_compact",
        "focuswindow_start_envelope2",
        "process_device_spoof_start_single150",
        "process_device_spoof_start_single180",
        "process_device_spoof_start_wake40_finish150",
        "process_device_spoof_start_wake60_finish150",
        "process_device_spoof_start_edge60_pair",
        "process_device_spoof_start_edge40_pair",
        "process_device_spoof_start_envelope2_gap50",
        "process_device_spoof_start_envelope2_settle0",
        "process_device_spoof_start_envelope2_compact",
        "process_device_spoof_start_envelope2",
        "process_device_spoof_start_fast3",
        "spoof_start_envelope2_settle150",
        "spoof_start_envelope2_settle350",
        "focusmsg_start_envelope2",
        "appmsg_start_envelope2",
        "windowmsg_start_envelope2",
    )
    + SKIP_GENERIC_ESCAPE_CANDIDATES
))
SKIP_GENERIC_CANDIDATES = tuple(dict.fromkeys(
    SKIP_GENERIC_ANY_KEY_CANDIDATES
    + SKIP_GENERIC_ESCAPE_CANDIDATES
    + SKIP_GENERIC_HIGHLIGHT_CANDIDATES
))
