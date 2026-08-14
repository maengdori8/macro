# Strict inactive skip hypothesis journal

## 2026-08-10 ESC envelope miss follow-up

- Evidence: `spoof_start_envelope2_escape_block` produced three tightly clustered
  successes (0.620-0.643 s from action start) and then one controlled ESC miss.
  The prompt eventually exited at 1.553 s, outside the strict 1.5 s attribution
  window, while foreground and guard invariants remained intact.
- New hypothesis `spoof_start_preactivate80_burst3`: target-local activation
  messages may reach FC's game loop one render tick after the first controller
  edge. Wait 80 ms inside the fake activation envelope, then emit three 80 ms
  START samples with 40 ms gaps. Reject on any failed controlled attempt in the
  first three trials.
- New hypothesis `spoof_start_envelope4_fast`: the handler may poll too sparsely
  for two transitions. Emit four 80 ms START samples with 40 ms gaps while
  keeping total action time bounded. Reject on any failed controlled attempt in
  the first three trials.
- Scheduler rule: once a concrete candidate has earned the three-success
  confirmation lock, any later failure quarantines that exact delivery/timing
  shape. Log replay rebuilds the quarantine after restart, preventing an
  intermittent 3/4 route from repeatedly re-entering confirmation.
- The live classifier later captured a distinct `[S] SKIP` prompt. Target-only
  click and `WM_CHAR S` each failed one controlled attempt. The same safe
  virtual-START activation-envelope families are therefore included in the S
  tracker; they remain non-keyboard and retain independent S evidence.
- The first S trials of the two-pulse START envelope, post-release settle,
  preactivated three-pulse START, and dense four-pulse START all remained
  visible. Because `KEY_TO_GAMEPAD["s"]` maps to controller A, add a continuous
  two-pulse A envelope and an 80 ms preactivated three-A burst. Reject either
  concrete shape on any failed attempt after it reaches confirmation, and
  require at least three attributable trials before promotion.

This journal records safe hypotheses that are rejected before becoming a
runtime candidate. Per-candidate hypotheses and rejection rules remain in
`macroapp/skip_candidates.py` and are copied into every JSONL result.

## 2026-08-10 highlight-context split and controller rescan

- Read-only captures showed that the same on-screen skip label occurs in two
  different states. The central ROI dark-pixel ratio (`gray < 45`) measured
  0.003 during gameplay, 0.156 on the tactics screen, and 0.882 on the
  post-match highlight summary. Generation 6 therefore uses a conservative
  0.70 threshold and keeps a separate `escape_highlight` evidence tracker.
  No earlier in-match success is restored into this new tracker.
- Read-only XInput enumeration found a physical compatible controller in slot
  0 and the ViGEm virtual Xbox controller in slot 1. FC may cache or poll only
  the first device while inactive.
- New hypotheses `process_device_spoof_start_envelope2` and
  `process_device_spoof_a_envelope2`: ensure the existing virtual Xbox pad,
  notify only FC-owned windows that the device tree changed, wait 120 ms, then
  emit two 180 ms START or A pulses inside one target-local activation-message
  envelope. Reject a concrete route on any controlled miss after confirmation;
  never disable/hide the physical controller and never focus FC.
- The highlight tracker starts with a sham, the process-rescan START envelope,
  and the two one-off target-window message routes observed on the stuck
  summary. A single prior success is not promotion: every safe family still
  needs three attributable trials and the final route needs 30 consecutive
  successes with zero focus violations.
- Generation-6 live result: the settled rescan envelope missed at 1.675 s.
  Add `process_device_spoof_start_fast3`, which removes the 120 ms settle and
  front-loads three 60 ms START samples with 20 ms gaps. Reject on a controlled
  miss; a sham later exited naturally at 0.308 s, so apparent fast successes
  must also demonstrate lift over at least three sham attempts.
- Scheduler correction: after at least three attributable trials, an exact
  route with both successes and failures is quarantined as intermittent. This
  prevents a lucky 2/2 start followed by failures from monopolizing exploit
  allocation; materially different delivery/timing hypotheses remain active.

## 2026-08-10 any-key halftime confirmation

- Read-only evidence capture showed the distinct halftime prompt
  `SKIP 하려면 아무키나 누르세요. (Enter 키 제외)`, so this is tracked
  independently from the small in-play/highlight `ESC SKIP` label.
- Generation-7 result: `spoof_start_envelope2` survived the fixed three-second
  no-input control and removed the any-key prompt in 0.623 s, then reproduced
  after a local reinstall in 0.624 s and 0.637 s. The exact route is therefore
  3/3 in the same classifier generation with no foreground, focus, or scope
  violation and is now locked for the 30-consecutive confirmation run.
- The route sends two 180 ms virtual-controller START pulses inside one
  target-window-only activation-message envelope. It does not emit a global
  Escape key or focus FC.
- Keep it under confirmation rather than final promotion. Any later controlled
  failure resets the streak and quarantines the exact route; only 30
  consecutive successes can validate it. The any-key tracker still needs its
  own sham observations before this route can satisfy the final goal.
- The first post-component local install supplied a fourth independent
  any-key reproduction at 0.592 s after input. The route is now 4/4 across
  multiple matches; foreground, focus, and input-scope violations remain zero.
- A later match supplied a fifth reproduction at 0.651 s. The any-key route is
  now 5/5 and remains independently locked for the 30-run confirmation.

## 2026-08-10 highlight post-release activation sweep

- Generation-7 highlight evidence: the ordinary two-pulse START envelope and
  its input-free timing control both remained visible at about 1.563-1.564 s,
  so the ordinary envelope showed no measurable lift over the control.
- `spoof_start_envelope2_settle250` then survived the fixed three-second
  control and removed an `ESC SKIP` highlight prompt in 0.845 s. Its two START
  pulses and 100 ms gap consume about 460 ms, followed by 250 ms of target-only
  activation-envelope persistence; the early exit occurred just after that
  persistence window and is temporally distinct from the no-settle misses.
- New hypotheses `spoof_start_envelope2_settle150` and
  `spoof_start_envelope2_settle350` bracket the observed window without global
  input or focus. Reject each exact timing shape on mixed evidence after three
  controlled attempts. The 250 ms route also remains provisional until it
  reproduces three times and then completes the 30-consecutive run.
- Reproduction result: the 250 ms route's second controlled attempt remained
  visible at 1.564 s. Its evidence is now mixed at 1/2, so the first 0.845 s
  exit is not accepted as a skip effect. Keep one bounded third trial so the
  intermittent-retirement rule can quarantine it with the minimum three
  samples, then move to the 150/350 ms sweep.
- The 150 ms route's first post-install attempt exited at 0.759 s and visibly
  entered the next match, but its second controlled attempt did not exit until
  1.721 s. This exceeds the strict 1.5 s limit and makes the route mixed at
  1/2; do not attribute the first exit until a third sample resolves and, on
  mixed evidence, quarantines the exact timing shape.

## 2026-08-10 highlight control-phase decorrelation

- Evidence: several unrelated target-only Space-message shapes appeared to
  succeed inside one highlight carousel, including two `pm_space` exits at
  0.290 s. Earlier no-input exits occurred at 0.287-0.308 s, so this cluster is
  consistent with replay auto-advance rather than three independent input
  mechanisms suddenly working.
- New control-policy hypothesis: keeping every input onset at exactly 3.000 s
  can phase-lock candidate selection to the replay cadence. For the highlight
  tracker only, cycle extra non-negative control offsets of 0, 170, 410, and
  730 ms after the mandatory three seconds. An actual input route should keep
  its post-input latency and success rate across offsets; a natural exit should
  move into the extended control window or lose timing consistency.
- The offsets also apply to highlight shams and never shorten the three-second
  requirement. Ordinary any-key evidence remains on the unchanged fixed
  three-second control, preserving its current 3/3 confirmation record.
- The jittered install immediately moved dozens of replay exits into the
  extended control window and exposed the earlier 2/2 `char_space` signal as a
  third-attempt failure. Replay restored and quarantined `char_space`,
  `pm_space`, and `sync_pm_space` as mixed routes.
- `pm_space_hold_1250` produced exits at 1.303 s after a 3.00 s control and
  1.313 s after a 3.73 s control with different prompt hashes, but its third
  controlled attempt remained visible at 1.913 s. It is therefore quarantined
  at 2/3 rather than promoted. New target-only 1.15 s and 1.35 s release
  variants bracket that partial key-up window; reject either on mixed evidence
  after the minimum three attempts.

## 2026-08-10 — Windows UI Automation invoke path

- Why it could work: an exposed DirectX overlay control with InvokePattern
  would permit a target-only inactive action without keyboard, mouse, focus,
  injection, or process memory access.
- Read-only observation: `AutomationElement.FromHandle(22939396)` exposed the
  FC `FIFAKC` window and only one descendant, the macro's
  `MautoWGCBorderMask_*` pane. No FC prompt text, button, or invokable control
  was present. Foreground HWND stayed `46794896` throughout the query.
- Rejection rule and result: reject when no FC-owned descendant supports an
  actionable UI Automation pattern. Rejected; there is no target to invoke.

## 2026-08-10 highlight activation-component split

- The full target-local activation envelope announces app activation, window
  activation, and focus together. FC may gate highlight-skip handling on only
  one internal flag, while another message in the same envelope may reset or
  counteract it before the bounded target-window input is consumed.
- Add three independent highlight-skip hypotheses: `focusmsg_pm_esc` sends
  only `WM_SETFOCUS`/`WM_KILLFOCUS`, `appmsg_pm_esc` sends only
  `WM_ACTIVATEAPP`, and `windowmsg_pm_esc` sends only
  `WM_NCACTIVATE`/`WM_ACTIVATE`. Each wraps a target-window-only delivery of
  the skip key displayed on the highlight screen. None calls
  `SetForegroundWindow`, `SetFocus`, or a global input API.
- Every individual message checks that the real foreground HWND is unchanged.
  Reject a component immediately on any foreground change or scope violation;
  otherwise reject it as ineffective after three attributable misses or as
  intermittent after mixed evidence.
- Cross the same three isolated activation components with two 180 ms virtual
  START pulses (`focusmsg_start_envelope2`, `appmsg_start_envelope2`, and
  `windowmsg_start_envelope2`). This prevents the search from overfitting to
  one displayed keyboard label and reuses the button family that already
  reproduced on the separate any-key prompt. Reject each cross-product route
  under the same three-attempt and zero-focus-violation rules.
- The running pre-component build supplied an additional decorrelation check:
  `notify_pm_space` first appeared to exit at 1.028 s but later missed at
  2.010 s, `down_pm_space` exited at 0.291 s then missed at 1.574 s, and a sham
  auto-advanced at 0.308 s. These are natural highlight-carousel timing, not
  accepted skip effects. Foreground, contamination, and unsafe-scope counters
  remained zero.

## 2026-08-10 highlight destination-persistence gate

- Live logs showed dozens of highlight-label disappearances during the
  no-input control, often only 0.29-0.65 s after a clip boundary. A sham also
  disappeared at 0.644 s, and unrelated input routes alternated between fast
  exits and misses. Therefore 0.4 s of label absence proves only that the
  current highlight clip changed, not that FC left the highlight sequence.
- Keep the strict response deadline unchanged: the first absent frame must
  still occur within 1.5 s of actual input onset. For `escape_highlight` only,
  require that absence to persist for 5.0 s before recording a final outcome.
  Ordinary any-key/A/S prompts keep the goal's 0.4 s minimum.
- Every event now records `exit_confirm_seconds`. On local restart, legacy
  highlight outcomes without the 5.0 s gate are deliberately not restored,
  while the separately valid any-key history is preserved. This rebuilds
  highlight evidence without discarding unrelated progress.
- First strong-gate live result: `process_device_spoof_start_fast3` lost the
  label briefly, but the label returned and the candidate finalized as a miss
  rather than a success. This directly confirms that the longer gate rejects
  a clip transition that the old rule could misattribute.
- Strong-gate candidate result: `process_device_spoof_start_envelope2` removed
  the highlight prompt within 0.720 s and kept it absent beyond the full 5 s
  destination-persistence gate, then the next match began. After installing
  persistent-summary retry, the same route reproduced on a different match in
  0.746 s with the same 5 s non-return. It is now 2/2 under the corrected gate;
  one more clean reproduction is required before confirmation lock.
- The same screen then remained indefinitely after that miss. Add a bounded
  persistent-summary rollover: eight seconds after the prior action starts,
  reset only the episode and require a fresh full three-second no-input
  control before trying the next candidate. This places consecutive inputs at
  least eleven seconds apart, preserves causal separation, and prevents the
  one-input-per-episode rule from freezing on a screen that cannot end without
  a successful skip.

## 2026-08-10 highlight START latency reduction

- A third strong-gate run of `process_device_spoof_start_envelope2` also left
  the highlight sequence and entered a new match, so the physical transition
  reproduced 3/3. Its first durable label absence was measured at 1.705 s,
  however, 205 ms beyond the goal's strict 1.5 s response deadline. The route
  is therefore mixed at 2/3 for goal accounting and is quarantined despite the
  eventual screen transition.
- Preserve the successful mechanism (process-local controller rescan, one
  target-local activation envelope, and two long virtual START pulses) while
  varying only accumulated delivery latency. Add a zero-wait 180/100/180 ms
  form, a zero-wait 180/50/180 ms form, and a zero-wait 150/50/150 ms compact
  form. Relative to the original these remove 120, 170, and 230 ms.
- These are separate families so one lucky exit cannot confirm all three.
  Reject any form on foreground change, scope contamination, a prompt return
  inside the five-second persistence gate, or mixed attributable results after
  its minimum three trials. Promotion still requires the full Goal streak;
  merely reaching the next match is not enough when first absence exceeds
  1.5 s.
- The independent any-key halftime route
  `spoof_start_envelope2` has now reproduced 11/11 attributable exits with no
  focus or contamination event. It remains evidence, not completion, until
  the required 30-run streak and sham quotas are met.
- First optimized live result: after a sham held the same permanent summary,
  `process_device_spoof_start_envelope2_gap50` survived a 3.17 s jittered
  no-input control, removed the prompt in 0.591 s, remained absent for the full
  five-second destination gate, and entered the next match. Guard delivery
  took 0.534 s; foreground stayed on HWND 12655032 and every focus,
  contamination, FC-foreground, and unsafe-scope counter remained zero. This
  is 1/1 evidence only and requires two more attributable reproductions before
  a candidate confirmation lock, then the full 30-run Goal streak.
- First zero-wait/100 ms-gap result: on a later match,
  `process_device_spoof_start_envelope2_settle0` survived a 3.41 s control but
  still had the prompt visible at 1.595 s. It is a strict failure and resets
  the global streak, although the screen disappeared later without an active
  candidate and cannot be attributed. Keep it available until the required
  three family trials; do not generalize this single miss to the 50 ms-gap
  family, whose first trial passed.
- The next-match halftime any-key prompt supplied another clean
  `spoof_start_envelope2` result at 0.678 s after its full three-second
  control, raising that independent route to 8/8. The prior highlight failure
  still resets the global Goal streak; prompt-specific evidence is retained
  without hiding that reset.
- Second 50 ms-gap trial: `process_device_spoof_start_envelope2_gap50`
  remained visible at 1.529 s, missing the strict deadline by 29 ms. Its live
  evidence is now mixed at 1/2 and cannot be promoted. The already-installed
  compact form removes another 60 ms by shortening each long pulse from 180
  to 150 ms; under the same observed tail that would move the boundary to
  roughly 1.469 s. Test that family independently rather than silently
  relaxing the 1.5 s criterion.
- Third 50 ms-gap trial remained visible at 1.565 s. The family's completed
  minimum sample is therefore 1 success / 2 failures, and the scheduler has
  quarantined it as intermittent. In both failures the prompt disappeared
  only later with no pending action (5.0+ s), so those exits remain
  unattributed. Advance to the independently named 150/50/150 ms compact
  family; do not count the one 0.591 s result as confirmation.
- First compact live trial produced the strongest causal contrast so far on a
  single persistent summary. A no-input sham survived its 3.17 s control and
  was still visible at 1.570 s. After the eight-second washout and a fresh
  3.41 s control on the identical prompt hash, the 150/50/150 ms compact route
  removed it in 0.527 s and kept it absent for the full five-second gate.
  Guard delivery itself took 0.471 s. Foreground remained HWND 12655032 and
  all safety counters stayed zero. This is 1/1 and needs two more independent
  attributable trials before confirmation lock.
- The following match's halftime any-key prompt reproduced
  `spoof_start_envelope2` again at 0.630 s after a full three-second control,
  taking that prompt-specific route to 9/9. Together with the preceding
  compact highlight success, the post-failure global streak is now two.
- On the following match-end sequence, a target-window-only displayed-key
  route (`spoof_pm_esc_settle150`) cleared one independently hashed highlight
  prompt in 1.388 s and held the five-second gate. A second, differently
  hashed highlight prompt then appeared; compact START cleared that prompt in
  0.510 s after its own three-second control and five-second persistence gate.
  Compact is now 2/2 across separate matches, with guard delivery at about
  0.47 s both times. No global keyboard action, focus change, contamination,
  FC foreground sample, or unsafe scope was recorded. One more compact pass is
  required for its three-attempt confirmation lock.
- The next halftime any-key prompt cleared in 0.636 s, raising
  `spoof_start_envelope2` to 10/10 attributable successes and satisfying the
  ten-sample minimum for that observed prompt family. The combined post-reset
  Goal streak is now five; the 30-run and sham-10 termination requirements
  remain unchanged.
- The next match-end persistent summary exercised the exploration allocation
  rather than compact confirmation. A sham held at 1.583 s; after washout,
  `notify_pm_esc` remained visible at 1.608 s and reset the global streak.
  After another washout, the target-local virtual-START route
  `spoof_start_envelope2_settle150` cleared a subsequent prompt in 0.789 s and
  held the five-second gate. Compact evidence remains 2/2; these independent
  families are not merged, and all guards again recorded zero focus or scope
  violations.
- The next halftime any-key prompt cleared in 0.627 s, making that confirmed
  route 11/11. The global streak after the last exploratory failure is two;
  compact highlight evidence remains 2/2 pending its third trial.
- Compact's third independent match-end trial cleared in 0.531 s after a
  3.17 s jittered control and remained absent through the five-second gate.
  The tracker now records 3/3, candidate streak three, and explicitly locks
  `process_device_spoof_start_envelope2_compact` as the highlight confirmation
  candidate. Future real highlight episodes use this route; scheduled shams
  remain separate. Its three response latencies are 0.527, 0.510, and 0.531 s
  with zero focus, contamination, FC-foreground, or unsafe-scope events.
- Its first post-lock confirmation trial cleared another match-end highlight
  in 0.518 s, bringing compact to 4/4 and proving that the scheduler actually
  remains on the locked route. The global validation streak is four, with
  five held shams and all safety counters still zero.
- A later post-lock highlight first held under sham, then compact cleared it in
  1.464 s after washout. This is a valid fifth compact success but only 36 ms
  inside the strict deadline, unlike the prior 0.51-0.53 s cluster. Preserve
  the locked route while it remains perfect; if any strict failure occurs,
  reset the Goal streak and test a further shortened pulse/gap derivative
  rather than relaxing the criterion. Compact is 5/5, global streak six, sham
  count six, and all safety counters remain zero.
- The compact confirmation route later produced a strict failure at 1.767 s
  after seven attributable highlight successes. The screen did transition, but
  the Goal requires disappearance within 1.5 s, so the global streak resets to
  zero and compact is no longer treated as perfect. Its action is synchronous:
  two 150 ms holds, two 20 ms release settles, and a 50 ms gap block capture for
  roughly 390 ms. Add two new, independently named target-local START families:
  `process_device_spoof_start_edge60_pair` (60/20/60 ms plus release settles)
  and `process_device_spoof_start_edge40_pair` (40/10/40 ms plus settles).
  They retain process-wide device rescan and two rising edges while returning
  the capture loop roughly 210 ms and 260 ms earlier than compact. Reject each
  family if it cannot achieve three attributable strict successes with zero
  safety violations; do not relax the 1.5 s deadline or merge their evidence.
- On their first persistent highlight, a sham held at 1.632 s, then the new
  60/20/60 ms and 40/10/40 ms pairs both remained visible at their strict
  deadlines (1.683 s and 1.622 s). This rejects the assumption that merely
  returning capture earlier is sufficient: those short holds were apparently
  not consumed. A target-local displayed-key route also remained visible, and
  a longer START envelope transitioned only at 1.602 s, confirming that the
  deadline must remain strict. Add single-edge 150 ms and 180 ms process-rescan
  START families. They preserve a hold duration already observed to register,
  but remove compact's second pulse and resume capture roughly 220 ms or 190 ms
  earlier. Any strict failure rejects that route for the zero-failure Goal;
  successes remain provisional until three attributable reproductions.
- On the next persistent highlight, a sham held at 1.625 s. A single 150 ms
  process-rescan START remained visible for 3.473 s, and a single 180 ms START
  remained visible at 1.640 s. Target-local focus/app ESC component probes also
  failed. Together with the uniformly short-pair failures and compact's former
  success cluster, this supports a controller-wake/consume model: one rising
  START edge initializes the re-enumerated path and a second sufficiently long
  edge performs the skip. Add asymmetric 40/150 ms and 60/150 ms two-edge
  families. They keep two rises and the proven-length consuming hold while
  removing 120-150 ms from compact. A strict miss rejects each route; three
  attributable passes are required before confirmation.
- Both asymmetric process-rescan routes failed on their first live highlight:
  40/150 ms remained visible at 1.686 s and 60/150 ms at 1.697 s. A subsequent
  target-window-only ESC component route also failed. On the same persistent
  highlight, `focusmsg_start_envelope2` then cleared the prompt in 0.554 s and
  held the five-second destination gate. This route sends two 180 ms virtual
  START pulses while exposing only FC's target-local focus-message component;
  it does not call SetFocus, foreground the game, or use global input. Treat it
  as 1/3 provisional evidence, retain zero safety counters, and require two
  additional attributable passes before confirmation lock.
- A later highlight first held under sham at 1.602 s. The app-component START
  route then missed by 15 ms (1.515 s) and was correctly counted as a strict
  failure; the subsequent no-pending disappearance was excluded. On the next
  independent match-end, `windowmsg_start_envelope2` cleared in 0.576 s and
  held the five-second gate. Focus-only and window-only activation components
  are therefore each 1/1, while app-only is 0/1. Continue independent replay;
  if both good components reproduce, test a focus+window envelope that excludes
  the apparently counterproductive app component. No route is locked yet.
- `focusmsg_start_envelope2` reproduced on a second independent match-end in
  0.567 s (first was 0.554 s), with the prompt absent for the full five-second
  gate and all safety counters still zero. Focus-only is now 2/2 and needs one
  more attributable pass for confirmation lock; window-only remains 1/1.
- The third independent `focusmsg_start_envelope2` trial remained visible until
  1.541 s, missing the strict 1.5 s deadline by 41 ms. This resets the global
  streak and rejects focus-only as a zero-failure route despite its two fast
  passes. Add `focuswindow_start_envelope2`: activate the target-local focus
  and window components together, deliberately omit the app component that
  missed at 1.515 s, and emit the same two 180 ms virtual START pulses. The
  candidate is rejected on its first strict miss or any real foreground/focus
  change, and needs three attributable passes before confirmation.
- `focuswindow_start_envelope2` physically transitioned but first disappeared
  only at 1.574 s, so the strict Goal rejects it immediately. The repeated
  1.54-1.57 s misses cluster close enough to the deadline that synchronous
  delivery duration remains a distinct variable, while the failed 40/60 ms
  process-rescan trials show that holds cannot simply be minimized. Add three
  independent 150/50/150 ms routes: focus-only, window-only, and focus+window.
  Each saves 110 ms versus the 180/100/180 ms envelope, keeps two rising START
  edges, and uses a hold length that previously produced attributable compact
  successes. Reject each on its first strict miss or any safety violation;
  require three clean reproductions before a confirmation lock.
- A sham held for 1.598 s, then `focusmsg_start_compact` exited only at 1.746 s;
  the compact focus route is therefore rejected. The mix of earlier immediate
  0.55 s exits and repeated 1.54-1.75 s late exits suggests clustered edges can
  miss a sparse controller polling interval rather than only taking too long
  to deliver. Add focus-only, window-only, and focus+window spread families:
  two 180 ms START rises separated by 650 ms. Their action envelope is about
  1.08 s, covering two distant polls while retaining roughly 0.4 s for prompt
  observation before the strict deadline. Reject on the first strict miss or
  safety violation and require three attributable reproductions independently.
- `windowmsg_start_envelope2`, previously 1/1 at 0.576 s, failed its second
  independent match-end with the prompt still visible at 1.582 s. Window-only
  clustered delivery is therefore intermittent and rejected for the zero-
  failure Goal; the later no-pending disappearance is excluded as unattributed.
  Keep the new spread families independent rather than restoring the one-off
  window success.
- The first `focusmsg_start_spread650` trial still had the prompt visible at
  1.802 s. Spreading the two focus-only START rises across controller polling
  intervals did not rescue the route, so it is rejected; the later unassigned
  disappearance remains excluded. Continue the window-only and focus+window
  spread families independently rather than generalizing this failure across
  activation components.
- A generation-7 aggregate across all highlight trials shows the strongest
  causal lift in `process_device_spoof_start_envelope2_compact`: 7 strict
  successes and 1 miss, versus 7 natural exits and 30 holds for sham. Its
  pending guard duration is consistently about 0.47 s. Add
  `process_device_spoof_start_compact4`, preserving process-wide controller
  re-enumeration, one target-local activation envelope, 150 ms holds, and
  50 ms gaps while expanding from two to four START rises. Estimated guard
  duration is about 0.91 s, leaving roughly 0.59 s for WGC observation. Reject
  on the first strict miss or safety violation and require three independent
  attributable passes before confirmation.
- Compact4's first live sham held for 1.877 s. Its four-pulse action guard then
  measured 0.916 s and caused a physical transition first observed at 1.577 s,
  only 77 ms outside the strict deadline. Add
  `process_device_spoof_start_compact4_gap10`, changing only the three idle
  inter-pulse gaps from 50 ms to 10 ms. This preserves all four proven-length
  150 ms holds and rising edges while targeting a 0.796 s guard; if the same
  post-delivery reaction delay repeats, observation should occur near 1.457 s.
  Reject on the first strict miss or safety violation and require three passes.
- Gap10's guard matched the prediction at 0.796 s, but its first physical exit
  was slower overall at 1.775 s. Shortening delivery alone is therefore
  rejected, and the 30 ms total neutral period (20 ms release settle plus a
  10 ms explicit gap) may not expose four distinct rising edges to FC. Add two
  orthogonal refinements: `process_device_spoof_start_compact3` keeps the
  proven 150/50 shape and adds only one edge to the former 7/8 route, while
  `process_device_spoof_start_compact4_hold130` retains four 50 ms gaps and
  shortens only holds to 130 ms. Reject either on its first strict miss or any
  safety violation; require three independent passes per family.
- Compact3's guard measured 0.693 s, yet its first disappearance was still late
  at 1.678 s. Adding one proven 150/50 edge to the former 7/8 route therefore
  does not recover deterministic highlight timing and is rejected. The nearly
  one-second post-action delay also weakens the assumption that synchronous
  guard duration alone explains recent misses. Continue the orthogonal 130 ms
  hold trial and keep natural/opponent exits excluded.
- `process_device_spoof_start_compact4_hold130` also missed on its first live
  trial: the prompt was still visible at 1.553 s and disappeared later without
  a pending candidate, so the late transition is excluded. Four timing-only
  refinements have now failed despite the original compact process-rescan route
  leading the aggregate at 7/8. Stop micro-tuning START duration and widen the
  controller mapping instead. Add two process-rescan, target-local activation
  candidates that publish two simultaneous 150 ms reports: START+B and
  START+BACK, separated by one 50 ms neutral gap. START retains the strongest
  known input-mode edge while B/BACK covers controller cancel/menu mappings for
  the visible skip action. Reject either family on its first strict miss or any
  safety violation and require three attributable reproductions before lock.
- The first `process_device_spoof_start_b_combo2` live trial followed a sham
  that held for 1.629 s, but its physical exit was first observed only at
  1.768 s. It is rejected immediately under the strict deadline, with all
  foreground and leakage counters still zero. Simultaneous START+B may also be
  interpreted as an unsupported chord rather than two independent bindings.
  Keep START+BACK as the independent simultaneous mapping test, and add two
  staggered families: one 150 ms START report followed by two 150 ms B reports,
  or by two 150 ms BACK reports, each separated by 50 ms. This preserves the
  strongest controller-mode edge while making the cancel/menu edge individually
  observable. Reject either sequential family on its first strict miss or any
  safety violation and require three attributable reproductions before lock.
- `process_device_spoof_start_back_combo2` also missed immediately: the prompt
  remained visible at 1.570 s and disappeared later with no pending attempt, so
  that later exit is excluded. Both simultaneous START+B and START+BACK chord
  families are rejected, while the sham/control and safety counters remain
  clean. The shared failure supports the chord-interpretation explanation and
  leaves the already-built staggered START-then-B and START-then-BACK families
  as the next independent mapping tests.
- Generation-7 highlight aggregation now has no attributable trial for the
  controller-A process-rescan family, even though A is the established mapping
  for the separately observed S/confirm form. B/BACK alone, spoofed, and in
  simultaneous chords have all missed, so add a genuinely different mapping
  axis rather than another chord: prioritize the existing two-pulse process-
  rescan A envelope, then add one 150 ms START mode-selection report followed
  by two distinct 150 ms A reports with 50 ms neutral gaps. Reject either exact
  route on its first strict miss or safety violation; require three attributable
  reproductions before confirmation.
- The installed `process_device_spoof_a_then_start_then_a` order also failed on
  its first controlled highlight: a sham held the prompt first, then the
  three-edge sequence left it visible at 1.693 s. Its later disappearance had
  no pending input and is excluded. This rejects the reverse ordering and
  strengthens the next edge-count hypothesis: preserve only the responsive
  START-then-A prefix, removing the second A that adds delivery time without
  proving additional recognition. Safety counters stayed at zero.
- `process_device_spoof_start_then_a_single` improved the prior 1.693 s miss
  to 1.542 s and reduced the synchronous guard from 0.694 s to 0.473 s, but the
  prompt was still visible 42 ms beyond the strict deadline. Its later exit is
  unattributed. Add one data-proportional timing isolate,
  `process_device_spoof_start_then_a_single_gap0`: retain both 150 ms holds and
  the neutral release generated by each virtual-pad pulse, but remove only the
  extra 50 ms idle sleep. Reject if its first attributable exit is still over
  1.5 s. Before installing it, let the already-installed DS4 OPTIONS-CROSS
  family receive an independent protocol trial to preserve search diversity.
- The DS4 protocol trial was the closest strict miss so far:
  `process_device_spoof_ds4_options_then_cross` still showed the prompt at
  1.508 s, only 8 ms over the deadline, with a 0.474 s guard. After washout and
  a new control on the same prompt, the independent fixed-3-second START route
  exited late at 1.742 s, so the near-boundary DS4 result is not evidence that
  every input happened to work in that clip. Add and prioritize
  `process_device_spoof_ds4_options_then_cross_gap0`, preserving the two 150 ms
  edges and neutral releases while removing only the 50 ms idle gap. Reject on
  its first attributable miss or safety violation; require three independent
  passes before confirmation.
- The first independent reproduction of `process_device_spoof_start_then_a_pair`
  physically left the highlight but was first observed at 1.693 s, 193 ms past
  the strict deadline, with a 0.694 s synchronous action guard. Its aggregate
  is therefore 1/2 and the family is rejected; the success streak resets.
  Prioritize simultaneous START+A because it preserves both bindings while
  shortening the guard by roughly 300 ms. If that misses, test the orthogonal
  A-START-A order rather than further tuning the rejected sequence. Foreground
  and leakage counters remained zero.
- `process_device_spoof_start_a_combo2` failed its first live controlled
  highlight: the prompt was still present at 1.556 s despite the action guard
  shrinking to 0.470 s, and the later five-second absence was recorded as
  unattributed. This rejects the simultaneous chord rather than supporting a
  pure observation-latency explanation. Keep A-START-A as the installed
  orthogonal order trial. Prepare two later families: START then one A removes
  only the redundant second confirm edge from the partially responsive Xbox
  sequence, while process-rescan/spoof DS4 OPTIONS then CROSS transfers the
  same menu-confirm ordering to an independent controller protocol. The latter
  is not a repeat of the failed DS4 single-button routes. Reject either on its
  first attributable miss or safety violation and require three independent
  reproductions before confirmation.
- The first staggered `process_device_spoof_start_then_b_pair` trial also
  missed: the prompt's first sustained exit was observed only at 1.693 s.
  Together with B alone and START+B, this rejects the B mapping across isolated,
  simultaneous, and sequential delivery; do not spend more trials on B timing.
  The locally installed A build now tests the independent confirm mapping while
  preserving the same process-rescan and target-local activation safeguards.
- A control-timing split in the strongest compact START evidence exposes a
  separate causal variable: it passed all seven trials attempted after
  3.00-3.41 s of no input and missed only the deliberately delayed 3.73 s
  trial. Add `process_device_spoof_start_compact_fixed3`, an independently named
  family that reuses the exact 150/50/150 ms target-local delivery but evaluates
  it only at the production-representative 3.00 s boundary. New highlight sham
  trials use that same fixed boundary. Reject this exact fixed-control family
  on its first strict miss or safety violation and require the full 30-success
  confirmation; the name and family stay separate so the earlier mixed-timing
  7/8 evidence cannot be silently counted toward it.
- The first fixed-control A-axis episode finally separated the mappings on one
  persistent highlight. A no-input sham held, and the isolated two-pulse
  process-rescan A route left the prompt visible. After the required washout
  and another three-second control, `process_device_spoof_start_then_a_pair`
  removed it with a 0.764 s first-absence latency and the absence persisted for
  the full five-second destination gate. This is the first attributable success
  for a mapping sequence added after the START-only intermittent result: START
  appears to establish controller input mode while a subsequent A edge confirms
  the skip. Keep the exact 150 ms START, 50 ms neutral, A, 50 ms neutral, A
  shape unchanged; require two more independent attributable reproductions
  before confirmation lock and reset immediately on any miss.
- Two orthogonal A-order fallbacks are prepared without changing the running
  installation while START-then-A is being independently reproduced. First,
  `process_device_spoof_start_a_combo2` publishes START+A together twice; it
  tests whether mode selection and confirmation must coexist in one controller
  poll. Second, `process_device_spoof_a_then_start_then_a` publishes three
  separate A, START, A reports; it tests whether an initial confirm edge must
  establish controller state before the START mode edge. Both retain process
  rescan and target-local activation only, are rejected on their first
  attributable miss or any safety violation, and require three independent
  reproductions before confirmation.
- Removing the 50 ms inter-edge gap did not recover the two closest mapping
  misses. `process_device_spoof_ds4_options_then_cross_gap0` remained visible
  at 1.810 s, and `process_device_spoof_start_then_a_single_gap0` remained
  visible at 1.772 s. Both are materially worse than their 50 ms-gap forms
  (1.508 s and 1.542 s), while foreground, focus-change, and leakage counters
  remained zero. Reject the zero-gap variants and stop spending trials on
  controller gap micro-tuning; the neutral dwell appears useful rather than
  being the source of the observation delay.
- With the button-order and timing axes now sampled across Xbox and DS4, add a
  distinct target-process semantic-message layer. `process_appcommand_browser_back`
  posts `WM_APPCOMMAND/APPCOMMAND_BROWSER_BACKWARD` only to HWNDs owned by the
  FC process, covering CEF/overlay navigation that may ignore synthesized key
  state. `process_command_idcancel` posts `WM_COMMAND/IDCANCEL` to the same
  bounded HWND set, covering modal-overlay cancellation without sending an
  Escape key. Neither route uses global keyboard/mouse input or changes focus.
  Reject either on its first attributable miss or any safety violation and
  require three independent passes before considering confirmation.
- The first live semantic-command comparison rejected both queued commands on
  one persistent highlight. Browser Back left the prompt visible at 1.542 s;
  a no-input sham then held it to 1.578 s; IDCANCEL left it visible at 1.589 s.
  This excludes a natural transition and rejects both exact PostMessage
  routes, with foreground and leakage counters still zero. Test one bounded
  delivery-method factorial next: SendNotifyMessage for Browser Back and
  IDCANCEL, plus target-process WM_CANCELMODE as a distinct internal-mode
  cancellation command. Reject each on its first attributable miss; if all
  three fail, retire this semantic-message layer rather than multiplying
  equivalent message constants.
- `windowmsg_start_spread650` did not reproduce its 1.449 s borderline pass:
  on the next controlled highlight it remained visible at 1.796 s and the
  later transition was unattributed. Its aggregate is now 1/2, so the earlier
  pass is treated as intermittent rather than a confirmation lead. Do not
  prioritize this family without a new causal variable.
- The complete semantic-message factorial failed on one controlled highlight:
  notify Browser Back remained visible at 1.629 s, notify IDCANCEL at 1.599 s,
  a sham held to 1.616 s, and WM_CANCELMODE held to 4.552 s. Retire semantic
  Win32 commands; changing the delivery API did not bypass the game's input
  gate. Safety remained clean.
- Return to the only high-signal controller result without repeating its
  timing micro-tunes. The compact process-rescan START pair reached 7/8, which
  suggests intermittent controller polling rather than the wrong binding.
  Add a report-refresh axis: (1) preserve the exact two 150 ms holds and 50 ms
  neutral gap but re-publish the held ViGEm report every 25 ms; (2) hold one
  START state for 650 ms with the same refresh cadence to widen the inactive
  polling window while retaining roughly 0.8 s for observation. Reject either
  on its first strict miss or safety violation and require three independent
  passes before confirmation.
- The first live `process_device_spoof_start_refresh2` trial passed: after the
  three-second no-input control, the highlight first disappeared at 1.473 s
  and stayed absent for the full five-second destination gate. Foreground,
  focus-change, and leakage counters remained zero. This is only 27 ms inside
  the deadline, so keep the exact refreshed 150/50/150 ms shape unchanged and
  require at least two independent reproductions before giving it confirmation
  priority; reset immediately on any miss.
- The independent long-window branch did not support a simple sparse-polling
  explanation: `process_device_spoof_start_refresh650` remained visible at
  1.793 s, and the following sham held to 1.635 s. Reject the single refreshed
  hold. On the same persistent prompt, `windowmsg_start_compact` then passed at
  1.430 s. Together with the earlier refreshed-pair pass, this favors two
  distinct START rising edges over one long held state, but each exact family
  still has only one current independent pass and must reproduce before lock.
- `windowmsg_start_compact` reproduced on the next independent match at
  0.479 s, bringing that exact family to 2/2 with zero safety violations. Its
  first pass was 1.430 s, so the wide latency range still requires the third
  independent pass mandated by the confirmation lock; do not merge the two
  refreshed-pair results into this family.
- `windowmsg_start_compact` passed its third independent match at 0.487 s.
  The exact family is now 3/3 with zero focus, foreground, contamination, or
  unsafe-scope events and enters confirmation lock. Stop broad exploration
  while this lock remains intact and require 30 consecutive attributable
  successes; any strict miss clears the streak and reopens hypothesis search.
- The first post-lock confirmation also passed at 1.421 s, bringing the exact
  method to 4/4. The wider 0.479-1.430 s latency range remains below the strict
  1.5 s limit but warrants continued zero-tolerance observation; do not change
  the live method while it remains failure-free.
- The next controlled highlight broke the lock. A sham first held to 1.640 s,
  then `windowmsg_start_compact` physically exited but only at 1.821 s, outside
  the strict deadline. Reset the consecutive streak and confirmation lock;
  the exact family is 4/5 and is intermittent rather than solved. Because it
  did eventually exit, investigate delivery/consumption latency around its two
  rising edges instead of changing the binding or treating the miss as no-op.
- Add three evidence-linked variants rather than repeating the 4/5 route.
  `windowmsg_start_refresh2` combines its target-local window-active component
  with the independently successful 25 ms held-report refresh. The two settle
  variants keep the exact compact START pair but retain only that target-local
  component for 100 or 200 ms after release, covering a late render poll. While
  any strict trial is pending, tighten only the SKIP observation cadence from
  300 to 80 ms so first-absence timing is less quantized without increasing
  steady-state capture load. Reject each variant on its first attributable
  miss or any safety violation and require three independent passes before a
  new confirmation lock.
- On the first highlight observed by the 80 ms pending cadence,
  `process_device_spoof_start_refresh2` did not reproduce its earlier 1.473 s
  pass: the prompt was still visible at 1.577 s and disappeared only 5.310 s
  after input, which is unattributed. Its exact aggregate is now 1/2 with a
  reset streak and zero safety violations. Complete the minimum three-trial
  family check once if selected, then retire the mixed route; do not merge it
  with the untested window-component refreshed pair.
- The newly isolated `windowmsg_start_refresh2` route passed its first live
  highlight at 0.488 s after a full three-second no-input control, and the
  prompt stayed absent for the five-second highlight destination gate. This
  combines only target-local window-active state with two refreshed START
  reports; it does not send Escape. Foreground, focus-change, contamination,
  and unsafe-scope counters were all zero. Keep the exact shape unchanged and
  require two more independent attributable passes before confirmation lock.
- `windowmsg_start_compact_settle100` exited on its first live trial only at
  1.578 s, so retaining the target-local window-active component for 100 ms
  after the second START release did not meet the strict deadline. Reset the
  global streak and retire this exact settle duration from winner contention;
  its late exit still supports testing the orthogonal refreshed-report route.
  Safety counters remained zero.
- A highlight sham then held for 1.566 s, excluding a natural transition, but
  `windowmsg_start_compact_settle200` first disappeared at 1.503 s. Even though
  it missed by only 3 ms, the goal deadline is strict, so count it as a failure
  and reset the streak. Both post-release settle durations have now missed;
  retire the simple settle axis and preserve the independently passing
  refreshed-report hypothesis. Safety counters remained zero.
- Because both settle variants missed, stop extending the same activation
  envelope. Add `windowmsg_start_edge2` as a distinct target-local state-edge
  hypothesis: activate only FC's window component immediately around the first
  START pulse, release it, then repeat the component transition around the
  second START pulse after the same 50 ms neutral gap. This tests whether the
  game samples an active-state transition rather than its duration. Reject on
  the first strict miss or safety violation and require three independent
  passes before confirmation.
- `windowmsg_start_refresh2` did not reproduce: its second independent live
  highlight remained visible at 1.640 s. Its exact aggregate is now 1/2, so
  reset the streak and remove it from winner status rather than combining its
  first 0.488 s pass with another family. Build the per-edge target-local
  transition candidate next; all safety counters on the failed refresh trial
  remained zero.
- `windowmsg_start_edge2` failed its first live trial with the prompt still
  visible at 1.605 s, so separate target-local component transitions around
  each START edge did not recover the polling deadline. On the same persistent
  prompt, after washout and a fresh three-second control, the independent
  `focuswindow_start_compact` route first disappeared at 1.488 s. Count the
  edge route as rejected and the focus+window route only as a narrow 1/1 lead;
  its 12 ms margin requires independent reproduction before any lock. Both
  trials kept foreground, focus-change, contamination, and unsafe-scope at
  zero.
- A later allocation exposed a catalogue-scope defect: the legacy `pm_esc`
  target-window message route was still reachable from the highlight tracker.
  It was not global input and failed at 1.570 s, but it contradicts the
  START-only experiment contract. Filter every direct `window_escape*` family
  out of all automatic generic catalogues, while retaining the distinctly
  named gamepad START control family. Add tests that assert those direct ESC
  routes are disjoint from any-key, escape-labelled, and highlight catalogues.
- On the first live prompt after installing the direct-ESC filter, the legacy-
  named `spoof_start_envelope2_escape_block` candidate passed at 0.651 s and
  remained absent for the five-second destination gate. Despite its context
  suffix, its action is exactly `spoof_start_envelope2`: two target-local
  virtual START pulses and no Escape delivery. Treat it as a fresh exact 1/1
  lead and require two independent reproductions before confirmation. Safety
  counters remained zero.
- On the next independent highlight, the controller-B envelope remained
  visible at 1.670 s. After washout and a fresh control, the previously narrow
  `focuswindow_start_compact` lead also remained visible at 1.541 s. Its exact
  aggregate is now 2/3, so retire it as intermittent and reset the global
  streak. The START-only highlight-block candidate remains a separate 1/1;
  no direct Escape route was present and every safety counter stayed zero.
- Aggregate the exact highlight-generation evidence before adding another
  timing tweak. `process_device_spoof_start_envelope2_compact` remains the
  strongest exact START-only route at 7/8, while denser three- and four-edge
  variants did not improve it. Add `process_device_spoof_start_pair_rehandshake2`
  to repeat the complete FC-process device rescan, target-local activation,
  and two 150 ms START edges after a 50 ms neutral interval. This tests whether
  the intermittent miss is the enumeration/activation handshake itself rather
  than the number of START samples inside one envelope. It sends no Escape or
  global input. Reject it on its first attributable strict miss or any safety
  violation, and require at least three independent passes before retention.
- The first live `process_device_spoof_start_pair_rehandshake2` trial passed a
  three-second sham gate but first disappeared at 1.545 s, missing the strict
  deadline by 45 ms. Its guarded action itself consumed 0.992 s; foreground,
  contamination, and unsafe-scope counters remained zero. Reject the exact
  candidate. Split its causal variables into two shorter candidates instead:
  `process_device_spoof_start_rescan2_pair` repeats only the FC-process device
  rescan before one compact START pair, while
  `process_device_spoof_start_activation_rearm_pair` performs one rescan but
  aligns each of the two existing START edges with a separate target-local
  activation transition. Neither adds keyboard or Escape delivery. Reject
  each on its first attributable strict miss or safety violation and require
  three independent passes before retention.
- Prepare, but do not install before the activation-only factorial trial, a
  measurement-timing correction: once any tracker has begun its mandatory
  control, use the existing 80 ms pending OCR interval until the episode ends.
  The prior 300 ms cadence quantized the nominal fixed three-second boundary
  and could delay candidate delivery by one full scan. This changes no input
  shape and adds no steady-state capture cost; it only tightens the already
  visible prompt's three-second control and result observation. Reject the
  timing hypothesis if the exact compact START route still has one strict miss
  after installation, and require three passes before retention.
- Give that timing-only revalidation its own evidence identity,
  `process_device_spoof_start_compact_control80`, while mapping its action
  exactly to the unchanged 150/50/150 compact process-rescan START delivery.
  This avoids merging its results with the historical 7/8 route or the failed
  fixed-three alias. Install it only after the activation-only factorial has a
  clean live result; reject on the first attributable miss or safety violation.
- `process_device_spoof_start_rescan2_pair` remained visible at 1.590 s with a
  0.522 s guard, and `process_device_spoof_start_activation_rearm_pair`
  remained visible at 1.516 s with a 0.544 s guard. Both had a full
  three-second control and zero foreground, focus, contamination, or unsafe-
  scope violations. Reject both exact candidates and the handshake-repeat
  axis: neither isolating a second enumeration notification nor aligning the
  same two START edges with separate activation transitions removed the miss.
  Proceed with the already-prepared control-cadence measurement hypothesis.
- `process_device_spoof_start_compact_control80` also failed its first live
  trial, disappearing only at 1.571 s after a 0.471 s guard. Reject control
  cadence as the sole cause. The broader data remain bimodal: the compact
  route usually clears near 0.52 s when consumed, while misses stay visible
  near or beyond the deadline. Test controller-state visibility instead of
  another pulse micro-timing: (1) keep FC target-locally active and the pad
  neutral for 150 ms before the unchanged compact pair, and (2) separately
  publish an explicit neutral reset under the virtual-pad lock before the
  unchanged process-rescan compact pair. Record the existing virtual Xbox slot
  index in each experiment event without creating another device. Reject each
  candidate on its first attributable miss or safety violation and require
  three independent passes before retention.
- `process_device_spoof_start_preactivate150_pair` failed its first attributable
  live trial: slot 1 was connected and the foreground invariant held, but the
  prompt remained at 1.598 s and exited naturally at 4.788 s. Reject neutral
  preactivation as ineffective. Keep the explicit-neutral-reset factorial for
  its next live trial.
- Prepare two target-process-only discovery factorials without changing START
  timing: `process_raw_spoof_start_compact` posts valid Raw Input gamepad
  arrival handles before the target-local 150/50/150 compact envelope, while
  `process_device_raw_spoof_start_compact` additionally performs the existing
  generic process device rescan. The historical Raw Input trial used only one
  unactivated START edge, so these combinations remain untested. Reject each
  on its first attributable miss or any safety violation.
- Prepare `process_device_sync_spoof_start_compact` ahead of the Raw Input
  factorials. The strongest 7/8 route posts device-change asynchronously and
  immediately continues into START, so queue ordering can explain its bimodal
  consume-or-miss pattern. This candidate synchronously refreshes only FC's
  render HWND and its `DIEmWin` DirectInput sibling with an 80 ms hang timeout,
  then preserves the exact 150/50/150 compact START envelope. Reject on the
  first attributable miss, delivery timeout, or safety violation.
- `process_device_spoof_reset_start_compact` failed its first live trial by the
  strict boundary: the slot-1 pad reset and action guard completed normally in
  0.524 s, but the prompt remained at 1.506 s and exited only at 4.672 s with
  no pending attempt. Reject stale shared-pad state as the sole cause. Safety
  counters remained zero. Build the synchronous-rescan and Raw Input factorials
  next without relaxing the 1.5 s threshold.
- Prepare an activation-order factorial for a later build without disturbing
  the installed synchronous-rescan trial. `process_spoof_device_start_compact`
  establishes FC's target-local active state before queuing process device
  rescan and settling 50 ms; `process_spoof_device_sync_start_compact` changes
  only that rescan to bounded synchronous render/DIEmWin delivery. Both retain
  the same two START edges and immediately restore the inactive message state.
  Reject each on its first attributable miss or any safety violation.
- `process_device_sync_spoof_start_compact` completed its bounded synchronous
  render/DIEmWin rescan and the unchanged START pair in a 0.474 s guard, with
  slot 1 connected and all safety invariants intact, but the first sustained
  prompt exit was late at 1.543 s. Reject asynchronous device-change queue
  ordering as the sole cause. Continue with the already-installed Raw Input
  discovery factorials before building the activation-order variants.
- Prepare `process_device_spoof_start_wait300_a` as a controller-mode state
  transition test, not another compact-gap tweak. The earlier START-then-A
  sequence achieved one strict 0.764 s exit but its 50 ms forms did not
  reproduce; one START edge may need a rendered controller-mode update before
  A can confirm the visible skip. Keep the pad neutral for 300 ms, send one A
  edge, and reject on the first attributable miss or safety violation.
- FC's visible `FIFAKC` HWND and hidden `DIEmWin` HWND are owned by PID 41980 but
  run on different UI threads (44520 and 36388 in the current process). Prepare
  `process_device_spoof_diapp_start_compact` to retain the normal render-window
  activation envelope while mirroring only `WM_ACTIVATEAPP` to the process-owned
  DirectInput window around the unchanged compact START pair. This tests a
  thread-local controller acquisition gate without changing OS focus or sending
  messages outside FC. Reject on the first attributable miss or safety event.
- Prepare `process_raw_sync_spoof_start_compact` to isolate queue ordering in
  the Raw Input branch. It enumerates only real HID joystick/gamepad handles,
  synchronously delivers their arrival to FC's render and `DIEmWin` HWNDs with
  80 ms timeouts, verifies foreground invariance, and then preserves the exact
  compact START pair. Reject on the first miss, timeout, or safety violation.
- `process_raw_spoof_start_compact` failed its first attributable live trial.
  The slot-1 virtual pad was connected, the three-second control passed, and
  all foreground/focus/contamination counters stayed at zero, but the prompt
  was still visible at 1.500411 s and exited naturally only at 4.860 s. Reject
  asynchronous Raw Input arrival as the sole discovery mechanism under the
  strict 1.5 s boundary. Proceed with the already-installed combined generic
  device-rescan plus Raw Input arrival candidate; do not relax the deadline or
  introduce ESC delivery.
- `process_device_raw_spoof_start_compact` passed its first attributable live
  trial at 1.440563 s after a passed three-second no-input control. It combined
  the generic FC-process device rescan and valid Raw Input gamepad-arrival
  notification before the unchanged target-local compact START pair. The
  foreground, focus, contamination, FC-foreground-sample, and unsafe-scope
  counters all remained zero. Treat this as provisional only: keep the exact
  installed candidate for at least two more independent strict reproductions,
  reject on the first miss, and do not count opponent/natural exits.
- The same combined candidate passed its second independent live trial at
  0.488530 s. A simpler `device_start` attempt had first failed at 1.589155 s
  while the prompt remained visible, after which the combined discovery route
  cleared that still-present prompt. Its exact-method record is now 2/2 with
  zero foreground, focus, contamination, or unsafe-scope events. Preserve the
  installed build for a third strict reproduction before promoting it from a
  provisional discovery result.
- `process_device_raw_spoof_start_compact` failed its third attributable trial
  at 1.512706 s, 12.706 ms beyond the strict result deadline. Its exact record
  therefore ends at 2/3 and its confirmation streak resets to zero despite all
  safety counters remaining zero. Reject the generic-rescan-plus-async-Raw
  ordering as non-deterministic. Build the prepared active-first ordering,
  bounded synchronous variants, DirectInput-thread activation, and settled
  START-to-A state-transition candidates for the next local-only cycle.
- `process_spoof_device_start_compact` failed its first live trial at
  1.530326 s after the same prompt survived the no-input control. Reordering
  target-local activation before the asynchronous process rescan therefore did
  not eliminate the inactive-controller acquisition miss. All safety counters
  remained zero. Reject the async active-first ordering and proceed to its
  bounded synchronous render/DIEmWin counterpart in the same installed build.
- `process_spoof_device_sync_start_compact` passed its first attributable live
  trial at 0.528157 s, with a passed three-second control and zero foreground,
  focus, contamination, FC-foreground-sample, or unsafe-scope events. Unlike
  the failed asynchronous active-first form, it establishes the target-local
  active state before synchronously delivering bounded device-change messages
  to FC's render and DirectInput windows. Keep it provisional until at least
  two more independent strict reproductions; reject on the first miss.
- The same active-first synchronous candidate passed its second independent
  trial at 0.561653 s. Its exact-method record is now 2/2 with zero safety
  events. Keep the installed build unchanged for the third reproduction; if it
  passes, allow the scheduler's three-success confirmation lock to focus the
  candidate for the required 30-run zero-failure streak.
- `process_spoof_device_sync_start_compact` failed its third attributable live
  trial: the prompt remained visible at the strict 1.554212 s observation and
  disappeared naturally only at 4.795535 s. Its exact-method record therefore
  ends at 2/3 and the streak resets to zero. Foreground, focus, contamination,
  FC-foreground-sample, and unsafe-scope counters all remained zero. Reject the
  active-first synchronous-rescan ordering as non-deterministic and continue
  with the installed settled START-to-A controller-state transition trial.
- `process_device_spoof_start_wait300_a` failed its first attributable live
  trial: after a sham held the same prompt through 1.553484 s, the candidate
  still left it visible at 1.607409 s. The later controller-mode confirmation
  edge therefore did not repair the inactive START acquisition miss. All
  safety counters remained zero. Reject this transition sequence and continue
  with the installed render-plus-DirectInput app-state delivery factorial.
- `process_device_spoof_diapp_start_compact` failed its first attributable live
  trial with the prompt still visible at 1.555322 s and a later natural exit.
  Mirroring the target-local app-state message to FC's DirectInput thread did
  not make either compact START edge deterministic. All safety counters stayed
  zero. Reject this factorial and continue with the installed bounded
  synchronous Raw Input discovery route.
- `process_raw_sync_spoof_start_compact` passed its first attributable live
  trial at 0.530404 s after the fixed three-second control and remained absent
  for the full five-second confirmation window. This isolates a useful signal
  in bounded synchronous Raw Input device arrival followed by the unchanged
  target-local compact START pair. Foreground, focus, contamination,
  FC-foreground-sample, and unsafe-scope counters were all zero. Keep it
  provisional for at least two more strict reproductions and reject it on the
  first miss.
- The same synchronous Raw Input candidate passed its second independent live
  trial at 0.529056 s with the identical control and five-second confirmation
  windows. Its exact-method record is now 2/2 with zero safety events. Keep the
  installed build and candidate fixed for a third strict reproduction before
  allowing the confirmation lock to focus it.
- The synchronous Raw Input candidate passed its third attributable live trial
  at 0.530253 s. On the same continuously visible prompt a sham held through
  1.581300 s and the unrelated `ds4_cross` and `ds4_circle` probes also failed;
  only the following Raw-sync compact START route caused the strict exit. Its
  exact record is now 3/3 with zero safety events, so allow the confirmation
  lock to focus it toward the required 30-run zero-failure streak while still
  retaining sham observations. Reject and reset immediately on any miss.
- `process_raw_sync_spoof_start_compact` failed its fourth attributable trial:
  the prompt remained visible at 1.568063 s and disappeared later without a
  pending input. Its exact record therefore ends at 3/4 and the required streak
  resets to zero. All safety counters remained zero. Reject synchronous Raw
  discovery by itself as non-deterministic; its three tightly clustered
  0.529-0.530 s successes still justify combining it with the independently
  useful active-first synchronous device-rescan signal in the prepared
  interaction factorial.
- Prepare `process_spoof_device_raw_sync_start_compact` as a later-build
  interaction factorial while leaving the promising installed candidate
  untouched. Inside one target-local active envelope it synchronously refreshes
  FC's render/DirectInput device state and valid Raw Input gamepad handles, then
  preserves the same two 150 ms START edges. This combines the distinct signal
  from the current 1/1 active-first synchronous route with the earlier 2/3
  generic-rescan-plus-Raw route. Reject on its first attributable miss, timeout,
  or safety event. Dispatch/catalogue tests pass and the full suite is 260/260.
- After the two component routes ultimately ended at 2/3 and 3/4, build and
  install the prepared interaction factorial locally only. Its first live trial
  passed at 1.498734 s after a sham held the same prompt through 1.518242 s,
  with the full five-second disappearance confirmation and zero safety events.
  This sample began on a prompt already present across the local reinstall and
  is only 1.266 ms inside the deadline, so treat it as provisional rather than
  evidence of deterministic operation. Keep the candidate fixed for ordinary
  fresh-match reproductions and reject on the first miss.
- `process_spoof_device_raw_sync_start_compact` failed its first ordinary
  fresh-match reproduction at 1.514799 s after an unrelated DS4 Share probe
  also left the prompt visible. Its exact record ends at 1/2 and resets. Both
  outcomes cluster around the 1.5 s deadline, unlike the roughly 0.53 s exits
  of either synchronous component alone, indicating that sequential bounded
  device and Raw notifications consume too much of the result window. All
  safety counters stayed zero. Reject the sequential interaction and prepare a
  parallel bounded version that overlaps only those two independent target-
  process notification calls inside the same target-local active envelope.
- Prepare `process_spoof_device_raw_parallel_start_compact`. It establishes the
  same target-local inactive-window activation envelope, concurrently runs the
  bounded relevant-window device-rescan and valid Raw Input arrival calls,
  requires both results, then preserves the same two 150 ms START edges. The
  concurrency changes only discovery latency and neither broadens HWND scope
  nor introduces global input. Reject on the first attributable miss, timeout,
  or safety event. A real thread-barrier test proves the two discovery calls
  overlap before START; related tests are 132/132 and the full suite is
  261/261 plus 25 subtests.
- The locally installed parallel candidate passed its first attributable live
  trial at 0.569574 s with the fixed three-second control, full five-second
  disappearance confirmation, and zero safety events. This recovers nearly a
  second versus the sequential interaction's roughly 1.5-second observations
  and confirms that the two bounded discovery calls were the latency source.
  Keep it provisional for at least two fresh independent reproductions and
  reject on the first miss.
- The parallel candidate passed its second independent trial at 1.468975 s.
  A sham, DS4 Touchpad, and spoofed DS4 Cross all left the same continuously
  visible prompt in place before the parallel route removed it, strengthening
  attribution. Its exact record is 2/2 and safety counters remain zero, but the
  31 ms deadline margin is small; keep it provisional for a third strict
  reproduction and reject on the first miss.
- The parallel candidate passed its third attributable live trial at 0.505064 s
  with the fixed control and full confirmation window. Its exact-method record
  is now 3/3 with no failures or safety events. Allow the scheduler's
  confirmation lock to focus it toward the required 30-run zero-failure streak,
  retain sham allocation, and reset/reject immediately on any miss.
- Under confirmation lock, the parallel candidate passed its fourth
  attributable live trial at 1.433967 s. A no-input sham immediately before it
  left the same prompt visible through 1.530351 s, while the candidate then
  removed it within the strict 1.5-second window and it remained absent for the
  full five-second confirmation. Its exact-method record is now 4/4, the current
  install has two valid sham holds, and foreground, focus, contamination,
  FC-foreground-sample, and unsafe-scope counters remain zero. Continue the
  locked candidate without changing the build; reject and reset on any miss.
- The same locked candidate passed its fifth attributable live trial at
  0.532060 s after the fixed three-second control and five-second absence
  confirmation. Its exact-method record is now 5/5 with zero safety events.
  Keep the installed binary and timings unchanged while accumulating the
  remaining strict confirmations and sham holds.
- The parallel candidate passed a sixth strict trial at 1.458549 s, then missed
  its seventh at 1.585895 s. The prompt remained visible and exited naturally
  5.297015 s later with no pending attempt, so the miss is not a measurement
  artifact. Its final exact record is 6/7 and the required streak resets to
  zero. Guard time stayed normal at 0.477565 s and every safety counter remained
  zero, indicating intermittent FC controller-thread consumption rather than
  slow discovery or an unsafe focus transition. Reject it as the final route.
- Prepare `process_spoof_diapp_device_raw_parallel_start_compact` as the next
  interaction factorial. It retains the 6/7 route's target-local render
  activation, parallel bounded device/Raw discovery, and unchanged compact
  START pair, while additionally mirroring only `WM_ACTIVATEAPP` to FC's
  process-owned `DIEmWin` before discovery. The real foreground is checked
  before and after each bounded message and no global input or ESC path is
  introduced. Reject on its first attributable miss or safety event. Its order,
  true discovery overlap, cleanup, and catalogue placement are covered by
  tests; the full suite passes 262 tests plus 25 subtests.
- The DirectInput-app activation factorial failed its first attributable live
  trial: guard delivery completed safely in 0.502857 s, but the prompt remained
  visible at 1.520773 s and disappeared naturally 4.682232 s later. Foreground
  and all safety counters stayed unchanged. Reject the added `DIEmWin`
  activation component immediately; it did not fix intermittent controller
  consumption and its extra message time narrows the strict deadline margin.
- Prepare `process_spoof_device_raw_parallel_start_compact3`. It returns to the
  safer 6/7 parallel route unchanged and varies only the sampling axis: after
  the same target-local activation and parallel bounded device/Raw discovery,
  emit three distinct 150 ms START rises with two 50 ms neutral gaps instead of
  two. This covers one additional inactive controller-poll opportunity while
  keeping the expected action guard below the 1.5-second result budget. Reject
  on its first attributable miss or safety event. A real barrier still proves
  discovery overlap and a dedicated dispatch test proves exactly three START
  edges; the full suite passes 263 tests plus 25 subtests.
- The three-edge candidate failed its first attributable live trial after a
  valid sham hold on the same prompt. The prompt was still present at 1.563734 s,
  with no foreground, focus, contamination, or scope event. Reject the simple
  edge-count extension: a third identical START rise does not repair the
  intermittent controller-discovery state.
- Prepare a two-candidate discovery-order bundle for the next local build.
  `process_spoof_device_raw_parallel_start_rehandshake2` repeats the bounded
  target-only parallel device/Raw discovery after the first compact START pair,
  then issues one independent compact pair inside the same activation envelope.
  This tests stale discovery state rather than adding indistinguishable edges.
  `process_spoof_device_raw_stagger30_start_compact` instead begins device rescan
  30 ms before Raw arrival, retaining overlap while making their cross-thread
  order deterministic. Reject either route on its first attributable miss or
  safety event. Barrier tests prove both discovery calls overlap in each round,
  and the full suite passes 265 tests plus 25 subtests.
- `process_spoof_device_raw_parallel_start_rehandshake2` failed its first
  attributable live trial at 1.594736 s. Repeating both target-only discovery
  calls and the compact pair did not clear the prompt, while every safety
  counter remained zero. Reject the repeated-handshake hypothesis immediately
  and retain the already-installed 30 ms device-first ordering candidate for
  the next independent prompt.
- `process_spoof_device_raw_stagger30_start_compact` also failed its first
  attributable live trial, leaving the prompt visible at 1.533622 s with zero
  safety events. Reject both simultaneous-versus-staggered ordering and simple
  discovery repetition as explanations for the intermittent miss.
- Prepare a clean-report bundle. `process_spoof_reset_device_raw_parallel_start_compact`
  publishes one explicit neutral Xbox report before the same target-only
  parallel discovery and compact pair, testing whether FC enumerates a stale
  held report. `process_spoof_device_raw_parallel_start_refresh2` instead keeps
  the discovery route unchanged but re-publishes each 150 ms held START state
  every 25 ms, testing sparse state polling rather than rising-edge count.
  Neither adds ESC, keyboard, mouse, focus, or non-FC HWND delivery. Reject each
  on its first attributable miss or safety event. The full suite passes 267
  tests plus 25 subtests.
- `process_spoof_reset_device_raw_parallel_start_compact` failed its first
  attributable live trial. After the fixed three-second control, the prompt
  remained visible at 1.589734 s; foreground, focus, contamination, FC
  foreground, and unsafe-scope counters all remained zero. Reject the
  pre-discovery neutral-report reset: stale held controller state does not
  explain the intermittent skip miss. Continue with the already-installed
  refreshed-hold candidate, which varies only FC's opportunity to sample the
  START-held state and still emits no ESC, keyboard, or mouse input.
- `process_spoof_device_raw_parallel_start_refresh2` passed its first strict
  live trial at 0.539743 s. On the same continuously visible prompt, an
  immediately preceding no-input control left the prompt present for
  3.387002 s, while the refreshed START-held reports removed it and the full
  five-second absence confirmation passed. Its exact record is 1/1 with one
  valid sham hold and zero foreground, focus, contamination, FC-foreground,
  or scope events. Keep it provisional until at least three independent
  reproductions; reject and reset on the first attributable miss.
