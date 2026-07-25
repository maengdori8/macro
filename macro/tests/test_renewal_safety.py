from __future__ import annotations

import base64
import os
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

import cv2
import numpy as np

from macroapp import renewal
from macroapp.capture import CapturedFrame, WGCCaptureEngine


_REAL_SAME_ILLUMINATION_BASELINE = "iVBORw0KGgoAAAANSUhEUgAAAJUAAAAoCAAAAADSnec4AAAF20lEQVRYCc3BD4zWdQHH8ffn+9xxnHfcceAw+SNgKAiujXUKKJVoi9EfU4aVFOaMSzZrBXNtRI3ZakulzS0wPSlg8wiipghiaOG4IkQOmYiOzAKH/FEgfg9/7q7jue+n3/M8J2ryHGs9bbxeSihJsoGALck25xWwgUAMilaIphcBGwjYkhwpkKyEUiRskGyEMGB6JdmAZAciBCKlSTYg2QgRyZOwEkoJigYkGyQMpncBGwgYESFgU1LABgI2SETyArYSSpCIgEQEJGzOQ8ERkGyCIgTblKLgCEg2IBFJSUSUUIJkA5INSNich0I0INlI5NmUEhQNSDYgEUkpRKOEEgIRkIikJGx6JxEBiQgIEDYlSERAIpKSiIBEBCWcm0QEJGNAwqZ3EhGQbIoCNiVIRECySUlEQCKCEs5NIRqQjAEJm94pRAOSDZItESlFIRqQInkSEUSIBiWcm2QDAZuUhE3vAjYQjEHBFjalBGyQbPIkbIFsUMK5BWyQbPIkbHoXiIBkk1LANqVI2CDZ5EkYEDYooTeSzX8nECkQ5rwCkQJRZFJKuDCIIpNSwoVBFJmUEi4M4iyjhDKKvCeQ1x2tkKHIsRtlAgXmAwSiyCihfPzng7yr+nMZ6Hr15bc6akZMGJEhdWr3K4dydSMnDhaQ29bBB1VPpsgooXy8eDdnLamg6+nWdlK1X5wc4OQT27tIDZk+TtDxwEH+QzNFRgnl4yWvcNaSCrat7AQZGr55OTzzJCBD/beHQceDB8kzIPL6/Jwio4Ty8caj5MWd7VT/LNO+bBe1E0e1vRT5xMzQsTBL3fVDd+zKacptgfiPLlJHNh3mI9MrSYWrKDJKKL+DD2WZOp23f9quT87o0/n4dgb+OOxohpunVnSu2sqwexrosa/5GDX39QNxllFC2fmJZ10/v4Gdj9C36WrYvrxq5Ozq1Zvg/v6w9df/6jN/MEXe8BTwhc+DOMsooewONx/Qp2+pYMNaaucNgc5TDRlY9gJ6OMCuZe3MHUOBX3/spEJ35t4RQbyPEspu45Ox4RtXwJo/0G9BAz1WPQ+LK+GlFZ3MGU9ebueaLNdouwfOHJvhfZRQbl5wjHHfCrByM3U/yD7zz6um1AObVsO9V8DGtd00NZLqWrv5DJfOP928l8xNNw7gPUootxd/CXdNAFpaqb1t3VGonP2xwIn5OcbeXnNw9X5oauTtw3/fcgpGfGmU9684EKm65qOXDc6QslBCmXU9sJ/6+6qBllYydSdqTpqGO8fAylZnhvQ7cjxnmhpZ//szUDX+M0MExze0nQbGzamiSAlltvsXOabdQqqlFQbdcem2dR1MuDNwbNnfgLpr/9JOUyNHftTFlbcOr9hzlMuGxmPrX+7k7o+TskAJ5eU1m1yxcBCpllYqZ9xAblkbNfdXwvH1e7sv+mxm6WmaGsk913fsoADNbUyalYGuPUdvFCkLlFBeJx/7K8PnVpNauZnaeUPgTy3mJxeTOn7motpXl7YzZzyIguY2Js3KAKLICCWU175HjjNlRgWp3z1Lvx/Ww+7FZt5oeuxc0cHcMSAKmtuYNCsDiAKDUEJ5bV1OxVevI++Pv6H2e5fAi78y3x9+eu+hEzdXwpZVXRXzh4IoaG5j0qwMIPIMCGUpr2VbaWgaRd4bD7rq643EVZudWVTz+iKYN4bcb5/3Jd8dcKCToqf2MG5aoKB2UKBAWcpr4SGG3TOAvOxDBxh9V//XViQMWxBy8zoZPbt+9/ITXPu1zMNvUtSZo7KKoqu/XEOBspRV+1wz5jsZ8rrXbzCVA9+JZKbfFGhpNVVVJ6DvzIldi/bxYVfe3Y8CZSmrHY/ClNspOvr4axSM/0oDHFz6FgU33Frd/dxJPuzi66ooUJayWrEF7phMj6NPv9ANlZ+aWifwvnW7gbpp1/cFm3MIFCnL/1X2jc5+l9dS5HfezPUfWc15KcsFSFkuQMpSFIj8L4K66SHFQKSHTA+FaFCIpiAQQSGadylEk/o3DYR53NcMX1gAAAAASUVORK5CYII="
_REAL_SAME_ILLUMINATION_CANDIDATE = "iVBORw0KGgoAAAANSUhEUgAAAJUAAAAoCAAAAADSnec4AAAFZklEQVRYCc3Be2yVdx3H8fen5xyhrW1Cb8uGAypYtjHoLsQM3VxYBR1sAlZtNRoSWcSVJbP8gRsxkcUZFkC2EZiapWbU25aReRsDNZaMFnTEbTihcyDXYoBeWEd/ZyCHc77+nqcnuMoejiaPSV8vOd6fCBmeDATGlYmAEZAhPCOKCBgBGQgMRECOaMLwhIHAKEwYnjBhIIwrEIYnDAFGSMgRTRieMEAYhQnDkyEMhHEFwvBkeMIICTkiCcMThieMgoThCUMYCCOaMDxheMIICJMjkjA8YXjCKEgYnjCEgTCiCcMThieMgDA5IskIyAgIoxBheMIAETAiCcMTRkAYnjDkiCIMTxgBYRQiDE8YnjBhRBKGJ4yAMDxhyBFFGJ6MkDAKEYYnI08YkYThyQgJwxOGHFFkBGSEhFGIDE8YIAyEEUmGJ4yQMDwZyBFFhieMkDAKEIYnDE94RiRheMIICQOEgRwRhOHJGCaMAoThyQgIMKIJw5MxTBggDOS4ImH8j4Tx3xPGZeQYheQYheQYheQYheSI0VuHuOT2csj1v77v7VTtzLok3sWTrx5IF3/klgkJvIND/If6BHlyxOj5rVyyahK2f8tRvNI7GlNA52/6DFR5910C1nQzkjaVkCdHjJ7fyiWrJjG07rgRSH7lTji09jyhMS31wJpuRtKmEvLkiNHOVwjYQK8lv30t25+lqG7mQKdjSkvFxe8cI3HjtJN70tSuKIY3h/Byu9+gfG4NgVuS5MkRv/M/2WVTHyjLPXyamhVVud+9cKFs2XXHHs0w4/5i27YlV750GnmZx7sZu/RmRpIjfkfWD6WaGtT7TWPhQjj2VO/Eput3t2V5eCr84/H+RPMc8g6szaBZX2MkOWJnbV2Mb61i7xPQWg8XBio/AH/4WVYbyqB/41HmNxYROrP2pJKZxLL6BO8lR+yOrL7A5+6BXU/DIxPJ63wmq3WV0P9kD3Oaknh24rl91E7fmh3TfFsx7yFH7J7YS8m6EtixGVaX7jx41ccnAn/ZkGXpLDi+xtHQnAJye37Zly277/of/YniGY01/Jsccet5LM0nvwx0tMPKjleMRNPsFOkVaWpWlGd+3gUNzalz7uC2Hihd0JA40/5GDm6dNaEywTA5YpZ78VfZsQ9NAjraoe5QybtZir40O8FLW3KMn9z3VtFFGppTu3+ahuSkT98qGNr+516DylVlDJMjZoM/fJNpy0qAjnZIzZt5pn2AD3+9hgubdxtw5+EeGppTZ1c6Ku65qaL/CKWTk6f+uCPNogXkyRGzv69/V4vuFdDRDjNaZS//ODumpR7ObevMFM392KZDNDSnePnEzDpBV5vVtlSDHdg/byx5csRs+7N8cMnNeDs2wzdugsPf72PxbLzBwdKqtzceZk5TkryuNqttqWYkOWL22N+4urUGb9fT8N3xcGrjCT77GfL6NxxnXmOCvK42q22pZiQ54uUezDJ9ufD++j341hQ4sekkn5+fPXv0yKdK4dT6XjXOF3ldbVbbUs1IShOvjs3QfDeBweVZFi6C19qcltzR80iGJZ+AvRszxYtvO/sOw15/gfFfqCCUrBxDSGni9eRrsHIqAVvTTcX9demn9lv5susyD/VT8+CHBjYepmr5Nb/vYNi5QVLjEoTGfXECIaWJVaZ1CP1gLKHudVmS1X0XYfrSMl7cYiTLB3Nw+332i19zuaqWyYSUJlZHV5/n2kcZ9s/ndmYIVH71Rshs2J/FU+0DlbbnVS5XNvcqQkoTq65nMty1mLzBHb89B4kbFkwRcPqlziykPnrv1ZDL8T4SIqQ0/1eZfaeLp1wjhg3te6fshnEUpDSjkNKMQv8COJQRcmFP78QAAAAASUVORK5CYII="
_REAL_SAME_ILLUMINATION_SHIFTED_CANDIDATE = "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"
_REAL_SAME_PRICE_15_BASELINE = "iVBORw0KGgoAAAANSUhEUgAAANwAAAAwCAAAAABft0ukAAADTklEQVRoBd3BTWgcZRjA8f+z72bzYViaaNomhWgPNvXgV4lUKdsKhWkgHhtGsSqiaJBED9L2WD0olJW2U0QvimCx5GAslYgflAY8+NFDC23XHqRKaNKZGJImIVnXp7uzznazarZCr5n39xPFXqLYSxR7iWIvUewlir1EsZco9hIlDvI3wnRaiJQn/6Rq3XqgUKROs6FGlDj4+lTRGTBEwkNTVGVeBI5eos6BrdSIsubdvDkzcgXHNUTC/bNUZV4C3rtMnYMPUCPKWlc8k5ufCnFcQyQcytPaSKT3GeCba1RMTpC8v52K/i5qRFnr9OOfiTiuIVJ6pcT+LUQkCYRlIuHJcZqee5yKhFAjylqnn5yjBI5riBQG4d0uViu87UPf3iSribLWla5Oz55dxHENkdk34Ug7q5ROjQF3D21mNVFiIPB8HNcQmTgEHzYtNxv+UR4fUWmbY+PBNlYRJQYCz8dxDZFcFg6cnGrc3r+eW8r5H04vJTLOkVk2PLulif8QJQYCz8dxDZFzH5DY4AObX94ElOdyFy6W6Hl+08XPpmnt2dbTZqgRJQYCz8dxDZGznwJSBh4eauD8uL+o8NAL7VL+7SMfUnelBztZIUoMBJ6P4xoiZ74slN3M1PF50q9t5fKxIon2zFMUkKbC6QtzRbreEVaIEgOB5+O4hkh+cqq0W/huJDQDfYTv+13dj3XKtVHa+u8pT/46cf3JHdSIEgOB5+O4hqqywER2iT1ugqV8a7NALkvHcDeg+ZYUNaLEQOD5OK7hX9PHfHbta2BFLkvHcDd1RImBwPNxXAOEy4vz97aCf/QPdu1rYEUuS8dwN3VEiYHA83FcA0ycWM7vzcDvhwvscROsyGXpGO6mjigxEHg+jmuA/OtFOt9qLH/1Ocmnd1+9QtXM97TsSHNL8tGNVIkSA4Hn47iGyPHz8MQjC18UWPfGfWOj3C452EuVKDEQeD6Oa4hcP7wAqWIIfQOJsVFulxzspUqUGAg8H8c1VPx4Ik/F9lcTFJT/0ZSiSpQYuPHtDA/uTFAR/vKTr9K6bWeKOxEldkoLf0m6hTsTxV6i2EsUe4liL1HsJYq9RLGXKPYSxV6i2EsUe4liL1HsJYq9RLGXKPYSxV5/AzgDNCDEKrznAAAAAElFTkSuQmCC"
_REAL_SAME_PRICE_15_CANDIDATE = "iVBORw0KGgoAAAANSUhEUgAAANwAAAAwCAAAAABft0ukAAADL0lEQVRoBd3BX2hWZRzA8e/veTea7tkspplpTKOtP45g5BwYGHQjFQ4rLXaVRST9MUdKIeEuWqSCcgq7eTTKy4HdNIigi6igLk5rs9Zq0LI/upaNtjnn/vh7fU/n8L6H3Mug253n8xHFX6L4SxR/ieIvUfwlir9E8ZcoWRDNRSwTErN5inLLgUKBMpIjJUoW6Olps6eKRM9PFK3eDfT3UmbjFlKiZMFQV8E4S+LoWYo2vAV81E2ZbbtJibLkTX938eMZjLMkOn8mlyNW/wbwaQ+J/CWoriLxwC5Soix5w4eIGWdJHBhhy0ZiNS3A5DiJgW64/2ESK+pIibLkDR8iZpwlVnj5H57fShn3Odz1egULibLkXTjJ6DTGWWJzr0ywfxML/dY1AzXP3ScsIEoWBCHGWWJTr03SeXdecvxn4nRvoepq4Z5n17CAKFkQhBhnif3deYkjZ7+J7nxoFSWXT/Xnb3qsZ8ysf/oOridKFgQhxlli57sum00hsP6F24gVZgbfn6Ji+64fglmq2rbWVAopUbIgCDHOEhs+Ok3RvQcqmf7x/MAveW7c9kglvR/+EVHXtK55LSWiZEEQYpwlNhhcobHt6plRzL7NjL57Dqjf2ZyDwsgnX16D6o4mSkTJgiDEOEtspr9vbO8qvj4Bza8SffBZRe32B3P0QMsa/uwemm/aU0uJKFkQhBhnKYoQdN8EK05UMtLXUL8MaIeOVmDy1+pGUqJkQRBinOU6h7/HHq8l1Q4drZQRJQuCEOMssSiKyAHHe6k+spJUO3S0UkaULAhCjLPA/LdDFzc8egPR/lFq3l5Oqh06WikjShYEIcZZYP7UV9x6cCUXDuapP6wDlByDtkaK1t5CkShZEIQYZ4n1nMmzee/sO4Pw+M7xlyiJQCh5YgdFomRBEGKcJTZy7C+oyl2Bm9+sGX+RRTy5gyJRsiAIMc4Si/pOTpGoe6qFuS9YRMPtFImSBUGIcZZE9Pt75yKof6ZBIGIRQokomXNtZCxavU74X6L4SxR/ieIvUfwlir9E8Zco/hLFX6L4SxR/ieIvUfwlir9E8Zco/hLFX6L4SxSfRBEipP4FnPIVUE7vawwAAAAASUVORK5CYII="
_REAL_SAME_PRICE_39_BASELINE = "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"
_REAL_SAME_PRICE_39_CANDIDATE = "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"


_REAL_SAME_PRICE_39_CANDIDATE_V2 = (
    "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"
)


def _price(text: str, width: int, height: int) -> np.ndarray:
    image = np.full((height, width), 238, dtype=np.uint8)
    cv2.putText(
        image,
        text,
        (2, height - 5),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.30,
        30,
        1,
        cv2.LINE_AA,
    )
    return image


def _guard(price_image: np.ndarray) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    guard = np.full((60, 100), 224, dtype=np.uint8)
    cv2.rectangle(guard, (1, 1), (98, 58), 90, 1)
    cv2.putText(
        guard,
        "BP",
        (4, 16),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.35,
        70,
        1,
        cv2.LINE_AA,
    )
    box = (30, 20, 70, 40)
    guard[box[1] : box[3], box[0] : box[2]] = price_image
    return guard, box


class _FakeEngine:
    def __init__(
        self,
        stop_event: threading.Event,
        cycles: list[list[np.ndarray]] | None = None,
        repeat_guard: np.ndarray | None = None,
        stop_after_closes: int | None = None,
    ):
        self.stop_event = stop_event
        self.cycles = cycles or []
        self.repeat_guard = repeat_guard
        self.stop_after_closes = stop_after_closes
        self.closed_event = threading.Event()
        self.capture_region = None
        self.clicks: list[tuple[int, int]] = []
        self.escapes = 0
        self.cycle_index = -1
        self.open_frames: list[np.ndarray] = []
        self.open_index = 0
        self.mode = "closed"
        self.closed_guard = np.zeros((60, 100), dtype=np.uint8)
        self.sequence_id = 0

    def set_capture_region(self, region) -> None:
        self.capture_region = region

    def get_latest_frame(self, timeout: float = 0.0):
        packet = self.get_latest_frame_packet(timeout=timeout)
        return None if packet is None else packet.image

    def get_latest_frame_packet(self, timeout: float = 0.0):
        if timeout <= 0:
            return None
        if self.mode == "closed":
            frame = self.closed_guard.copy()
            if (
                self.stop_after_closes is not None
                and self.escapes >= self.stop_after_closes
            ):
                self.stop_event.set()
        elif self.open_index < len(self.open_frames):
            frame = self.open_frames[self.open_index]
            self.open_index += 1
        elif self.open_frames:
            frame = self.open_frames[-1]
        else:
            return None
        self.sequence_id += 1
        return CapturedFrame(frame.copy(), self.sequence_id, time.perf_counter())

    def on_click(self, _hwnd: int, x: int, y: int) -> None:
        self.clicks.append((x, y))
        if (x, y) == (20, 10):
            self.cycle_index += 1
            self.mode = "open"
            self.open_index = 0
            if self.repeat_guard is not None:
                self.open_frames = [
                    self.repeat_guard,
                    self.repeat_guard,
                ]
            else:
                index = min(self.cycle_index, len(self.cycles) - 1)
                self.open_frames = self.cycles[index]

    def on_escape(self, *_args, **_kwargs) -> None:
        self.escapes += 1
        self.mode = "closed"


class _FakeManager:
    def __init__(self, engine: _FakeEngine):
        self.engine = engine
        self.capture_engine = engine
        self.hwnd = 1

    def find_window(self) -> bool:
        return True

    def capture_client_area(self, *, window_validated: bool = False):
        return np.full((100, 200), 128, dtype=np.uint8)

    def wgc_to_client(self, x: int, y: int):
        return x, y


class _DuplicateSequenceEngine(_FakeEngine):
    """Supplies images but never advances the WGC sequence number."""

    def __init__(self, stop_event: threading.Event, guard: np.ndarray):
        super().__init__(stop_event, repeat_guard=guard)
        self.packet_calls = 0

    def get_latest_frame_packet(self, timeout: float = 0.0):
        packet = super().get_latest_frame_packet(timeout=timeout)
        if packet is None:
            return None
        self.packet_calls += 1
        if self.packet_calls >= 12:
            self.stop_event.set()
        return CapturedFrame(packet.image, 1, packet.captured_at)


def _profile(baseline: np.ndarray, guard: np.ndarray) -> renewal.RenewalProfile:
    side = renewal.RenewalSideProfile(
        action_point=renewal.NormalizedPoint(20 / 199, 10 / 99),
        confirm_point=renewal.NormalizedPoint(180 / 199, 80 / 99),
        price_rect=renewal.NormalizedRect(0.40, 0.40, 0.60, 0.60),
        guard_rect=renewal.NormalizedRect(0.25, 0.20, 0.75, 0.80),
        limit_point=renewal.NormalizedPoint(140 / 199, 70 / 99),
        baseline_png=renewal.encode_gray_png(baseline),
        baseline_variants_png=[renewal.encode_gray_png(baseline)],
        guard_png=renewal.encode_gray_png(guard),
        closed_guard_png=renewal.encode_gray_png(np.zeros_like(guard)),
        unchanged_limit=0.035,
        stability_limit=0.015,
        registration_shift_limit=4,
        calibration_openings=renewal.RENEWAL_CALIBRATION_OPENINGS,
        calibration_version=renewal.RENEWAL_CALIBRATION_VERSION,
        calibrated_frame_width=200,
        calibrated_frame_height=100,
    )
    profile = renewal.RenewalProfile(buy=side)
    profile.apply_speed_level(10)
    return profile


def _run(
    profile: renewal.RenewalProfile,
    engine: _FakeEngine,
    side: str = "buy",
    monitor_only: bool = False,
    diagnostic_sink=None,
) -> bool:
    manager = _FakeManager(engine)
    with ExitStack() as stack:
        # 회귀 테스트가 사용자의 실제 진단 보관함을 채우거나 오래된 자료를
        # 최근 50건 정리 정책으로 삭제하지 않도록 저장 I/O를 차단합니다.
        stack.enter_context(
            patch.object(
                renewal,
                "save_renewal_diagnostic",
                (
                    diagnostic_sink
                    if diagnostic_sink is not None
                    else lambda *_args, **_kwargs: None
                ),
            )
        )
        stack.enter_context(
            patch.object(
                renewal.input_message,
                "prepare_mouse_click",
                lambda hwnd, x, y: SimpleNamespace(
                    hwnd=hwnd,
                    x=x,
                    y=y,
                ),
            )
        )
        stack.enter_context(
            patch.object(
                renewal.input_message,
                "post_prepared_mouse_click",
                side_effect=lambda click: engine.on_click(
                    click.hwnd,
                    click.x,
                    click.y,
                ),
            )
        )
        stack.enter_context(
            patch.object(
                renewal.input_message,
                "send_key_to_window",
                side_effect=engine.on_escape,
            )
        )
        return renewal.FastRenewalRunner(
            manager=manager,
            profile=profile,
            side=side,
            stop_event=engine.stop_event,
            logger=lambda _message: None,
            status=lambda _message: None,
            monitor_only=monitor_only,
        ).run()


class RenewalSafetyTests(unittest.TestCase):
    def test_popup_open_deadline_covers_measured_fc_transition(self) -> None:
        self.assertGreaterEqual(
            renewal.RENEWAL_POPUP_OPEN_TIMEOUT_SECONDS,
            0.80,
        )

    def setUp(self) -> None:
        self.base_price = _price("82400", 40, 20)
        # At this deliberately tiny synthetic scale, the 0 -> 1 terminal
        # stroke collapses inside the classifier's 3x3 anti-aliasing
        # tolerance and is not a valid stand-in for the much larger FC price
        # glyph.  Use a one-digit topology change that survives the tiny
        # raster; recorded FC last-digit holdouts cover the real requirement.
        self.changed_price = _price("82430", 40, 20)
        self.guard, self.price_box = _guard(self.base_price)
        self.changed_guard, _ = _guard(self.changed_price)
        self.profile = _profile(self.base_price, self.guard)

    def test_v8_profile_requires_v9_right_limit_price_recalibration(self) -> None:
        legacy = self.profile.to_dict()
        legacy["version"] = 8
        legacy["buy"]["calibration_version"] = 4
        legacy["buy"].pop("calibrated_frame_width")
        legacy["buy"].pop("calibrated_frame_height")
        loaded = renewal.RenewalProfile.from_dict(legacy)
        self.assertIn("v9 우측 상한가/하한가 재설정", loaded.missing("buy"))

    def test_v9_profile_round_trip_keeps_alignment_and_frame_size(self) -> None:
        self.profile.buy.noise_global = 0.004
        self.profile.buy.noise_slice = 0.018
        self.profile.buy.unchanged_limit = 0.038
        loaded = renewal.RenewalProfile.from_dict(self.profile.to_dict())
        self.assertEqual(loaded.to_dict()["version"], 9)
        self.assertTrue(loaded.buy.complete())
        self.assertAlmostEqual(loaded.buy.noise_global, 0.004)
        self.assertAlmostEqual(loaded.buy.unchanged_limit, 0.038)
        self.assertEqual(len(loaded.buy.baseline_variants_png), 1)
        self.assertEqual(loaded.buy.calibrated_frame_size(), (200, 100))

    def test_v9_accepts_only_expected_right_limit_price_row(self) -> None:
        buy_rect = renewal.NormalizedRect(0.6810, 0.4113, 0.7951, 0.4571)
        sell_rect = renewal.NormalizedRect(0.6810, 0.4113, 0.7951, 0.4571)
        self.assertTrue(
            renewal.validate_limit_price_selection(
                self.base_price,
                buy_rect,
                "buy",
                1928,
                1048,
            ).valid
        )
        self.assertTrue(
            renewal.validate_limit_price_selection(
                self.base_price,
                sell_rect,
                "sell",
                1928,
                1048,
            ).valid
        )
        self.assertTrue(
            renewal.validate_limit_price_selection(
                self.base_price,
                buy_rect,
                "buy",
                1920,
                1040,
            ).valid
        )

        amount_input = renewal.NormalizedRect(0.610, 0.485, 0.765, 0.555)
        wrong_buy_row = renewal.NormalizedRect(
            0.6810,
            0.3720,
            0.7951,
            0.4178,
        )
        self.assertFalse(
            renewal.validate_limit_price_selection(
                self.base_price,
                amount_input,
                "buy",
                1928,
                1048,
            ).valid
        )
        self.assertFalse(
            renewal.validate_limit_price_selection(
                self.base_price,
                wrong_buy_row,
                "buy",
                1928,
                1048,
            ).valid
        )
        self.assertFalse(
            renewal.validate_limit_price_selection(
                self.base_price,
                wrong_buy_row,
                "sell",
                1928,
                1048,
            ).valid
        )
        self.assertFalse(
            renewal.validate_limit_price_selection(
                self.base_price,
                buy_rect,
                "buy",
                2568,
                1408,
            ).valid
        )

    def test_two_line_price_region_is_rejected(self) -> None:
        two_lines = np.full((70, 120), 238, dtype=np.uint8)
        cv2.putText(
            two_lines,
            "0",
            (8, 23),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            20,
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            two_lines,
            "82400",
            (8, 58),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            20,
            2,
            cv2.LINE_AA,
        )
        self.assertFalse(renewal.validate_price_region(two_lines).valid)
        self.assertTrue(renewal.validate_price_region(self.base_price).valid)

    def test_wgc_single_slot_always_replaces_with_newest_frame(self) -> None:
        engine = WGCCaptureEngine(1, logger=lambda _message: None)
        first = np.zeros((4, 5, 4), dtype=np.uint8)
        second = np.full((4, 5, 4), 200, dtype=np.uint8)
        engine.on_frame_arrived(
            SimpleNamespace(frame_buffer=first, width=5, height=4)
        )
        engine.on_frame_arrived(
            SimpleNamespace(frame_buffer=second, width=5, height=4)
        )
        packet = engine.get_latest_frame_packet()
        self.assertIsNotNone(packet)
        self.assertEqual(packet.sequence_id, 2)
        self.assertEqual(int(packet.image[0, 0]), 200)
        self.assertEqual(engine.get_replaced_frame_count(), 1)

    def test_guard_and_price_alignment_accepts_same_and_real_change(self) -> None:
        guard_detector = renewal.RenewalModalGuard(
            self.guard,
            self.price_box,
            shift_limit=4,
        )
        classifier = renewal.RenewalPriceClassifier(
            self.base_price,
            unchanged_limit=0.035,
            stability_limit=0.015,
        )
        for shift_x in range(-4, 5):
            with self.subTest(shift_x=shift_x, kind="same"):
                registration = guard_detector.register(
                    renewal._translate_image(self.guard, shift_x, 0),
                    0.0,
                    0.0,
                )
                self.assertTrue(registration.valid)
                self.assertEqual(registration.shift_x, -shift_x)
                current = renewal.crop_price_from_guard(
                    registration.aligned,
                    self.price_box,
                )
                self.assertIs(
                    classifier.classify(current).state,
                    renewal.PriceState.UNCHANGED,
                )
            with self.subTest(shift_x=shift_x, kind="changed"):
                registration = guard_detector.register(
                    renewal._translate_image(self.changed_guard, shift_x, 0),
                    0.0,
                    0.0,
                )
                self.assertTrue(registration.valid)
                self.assertEqual(registration.shift_x, -shift_x)
                current = renewal.crop_price_from_guard(
                    registration.aligned,
                    self.price_box,
                )
                self.assertIs(
                    classifier.classify(current).state,
                    renewal.PriceState.CHANGED,
                )

    def test_guard_registration_ignores_both_live_limit_price_rows(self) -> None:
        target = _price("769", 150, 40)
        sibling_before = _price("629", 150, 40)
        sibling_after = _price("630", 150, 40)
        price_box = (96, 40, 246, 80)
        baseline_guard = np.full((120, 341), 251, dtype=np.uint8)
        baseline_guard[0:40, 96:246] = sibling_before
        baseline_guard[40:80, 96:246] = target
        cv2.line(baseline_guard, (20, 100), (300, 100), 190, 1)
        changed_sibling_guard = baseline_guard.copy()
        changed_sibling_guard[0:40, 96:246] = sibling_after
        dynamic_boxes = renewal.dynamic_limit_price_boxes(
            price_box,
            "buy",
            1040,
        )
        guard_detector = renewal.RenewalModalGuard(
            baseline_guard,
            price_box,
            shift_limit=4,
            dynamic_boxes=dynamic_boxes,
        )
        for shift_x, shift_y in ((0, 0), (-4, -4), (4, 4)):
            with self.subTest(shift_x=shift_x, shift_y=shift_y):
                registration = guard_detector.register(
                    renewal._translate_image(
                        changed_sibling_guard,
                        shift_x,
                        shift_y,
                    ),
                    0.0,
                    0.0,
                )
                self.assertTrue(registration.valid)
                current = renewal.crop_price_from_guard(
                    registration.aligned,
                    price_box,
                )
                self.assertLess(
                    float(cv2.mean(cv2.absdiff(current, target))[0]),
                    0.01,
                )

    def test_guard_alignment_noise_never_hides_whole_popup_shift(self) -> None:
        for brightness in (0, 12):
            guard_detector = renewal.RenewalModalGuard(
                self.guard,
                self.price_box,
                shift_limit=4,
            )
            for shift_y in range(-4, 5):
                for shift_x in range(-4, 5):
                    with self.subTest(
                        brightness=brightness,
                        shift_x=shift_x,
                        shift_y=shift_y,
                    ):
                        shifted = renewal._translate_image(
                            self.guard,
                            shift_x,
                            shift_y,
                        )
                        shifted = np.clip(
                            shifted.astype(np.int16) + brightness,
                            0,
                            255,
                        ).astype(np.uint8)
                        registration = guard_detector.register(
                            shifted,
                            10.0,
                            0.001,
                        )
                        self.assertTrue(registration.valid)
                        self.assertEqual(
                            (
                                registration.shift_x,
                                registration.shift_y,
                            ),
                            (-shift_x, -shift_y),
                        )

    def test_guard_rejects_closed_screen_without_exhaustive_shift_sweep(
        self,
    ) -> None:
        guard_detector = renewal.RenewalModalGuard(
            self.guard,
            self.price_box,
            shift_limit=4,
        )
        closed_screen = np.zeros_like(self.guard)
        with patch.object(
            renewal,
            "_translate_image",
            wraps=renewal._translate_image,
        ) as translate:
            registration = guard_detector.register(
                closed_screen,
                0.0,
                0.0,
            )
        self.assertFalse(registration.valid)
        # Neither template alignment nor the 9x9 fallback may run for a
        # screen whose luminance is plainly not this popup.
        self.assertEqual(translate.call_count, 0)

    def test_guard_rejects_incomplete_template_match_without_shift_sweep(
        self,
    ) -> None:
        guard_detector = renewal.RenewalModalGuard(
            self.guard,
            self.price_box,
            shift_limit=4,
        )
        # Close enough to bypass the coarse closed-screen luma rejection, but
        # structurally incomplete and therefore never valid popup evidence.
        incomplete = np.clip(
            self.guard.astype(np.float32) * 0.65,
            0,
            255,
        ).astype(np.uint8)
        with (
            patch.object(
                guard_detector,
                "_template_shift",
                return_value=(0, 0),
            ),
            patch.object(
                renewal,
                "_translate_image",
                wraps=renewal._translate_image,
            ) as translate,
            patch.object(
                guard_detector,
                "_edge_delta",
                wraps=guard_detector._edge_delta,
            ) as edge_delta,
        ):
            registration = guard_detector.register(
                incomplete,
                0.0,
                0.0,
            )
        self.assertFalse(registration.valid)
        # One translation validates the template's proposed location.  The
        # old 9x9 fallback would add 81 more translations on every transition
        # frame even though no alignment could make it a completed popup.
        self.assertEqual(translate.call_count, 1)
        # Luma and local structure already prove this popup is incomplete, so
        # expensive Canny edge extraction must not run at all.
        self.assertEqual(edge_delta.call_count, 0)

    def test_guard_reuses_exact_fresh_frame_pixels_without_reprocessing(
        self,
    ) -> None:
        guard_detector = renewal.RenewalModalGuard(
            self.guard,
            self.price_box,
            shift_limit=4,
        )
        shifted = renewal._translate_image(self.guard, 3, -2)
        with patch.object(
            guard_detector,
            "_edge_delta",
            wraps=guard_detector._edge_delta,
        ) as edge_delta:
            first = guard_detector.register(shifted, 0.0, 0.0)
            calls_after_first = edge_delta.call_count
            second = guard_detector.register(
                shifted.copy(),
                0.0,
                0.0,
            )
        self.assertTrue(first.valid)
        self.assertIs(first, second)
        self.assertGreater(calls_after_first, 0)
        self.assertEqual(edge_delta.call_count, calls_after_first)

    def test_guard_accepts_only_structure_matched_smooth_illumination_drift(
        self,
    ) -> None:
        guard_detector = renewal.RenewalModalGuard(
            self.guard,
            self.price_box,
            shift_limit=4,
        )
        horizontal_drift = np.linspace(
            2.0,
            8.0,
            self.guard.shape[1],
            dtype=np.float32,
        )
        illuminated = np.clip(
            self.guard.astype(np.float32) + horizontal_drift[None, :],
            0,
            255,
        ).astype(np.uint8)
        shifted = renewal._translate_image(illuminated, -2, 1)
        registration = guard_detector.register(shifted, 0.0, 0.0)
        self.assertTrue(registration.valid)
        self.assertEqual(
            (registration.shift_x, registration.shift_y),
            (2, -1),
        )

    def test_guard_keeps_measured_antialias_headroom_for_sparse_popup(
        self,
    ) -> None:
        sparse_guard = np.full((144, 412), 251, dtype=np.uint8)
        cv2.putText(
            sparse_guard,
            "4",
            (290, 75),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            70,
            2,
            cv2.LINE_AA,
        )
        price_box = (96, 48, 316, 96)
        guard_detector = renewal.RenewalModalGuard(
            sparse_guard,
            price_box,
            shift_limit=4,
            dynamic_boxes=renewal.dynamic_limit_price_boxes(
                price_box,
                "sell",
                1048,
            ),
        )

        self.assertGreaterEqual(
            guard_detector._local_spatial_limit,
            0.60,
        )
        self.assertFalse(
            guard_detector.register(
                np.zeros_like(sparse_guard),
                0.0,
                0.0,
            ).valid
        )

    def test_incomplete_brightness_and_wrong_popup_never_become_changes(self) -> None:
        classifier = renewal.RenewalPriceClassifier(
            self.base_price,
            unchanged_limit=0.035,
            stability_limit=0.015,
        )
        bright = np.clip(
            self.base_price.astype(np.int16) + 12,
            0,
            255,
        ).astype(np.uint8)
        clipped = renewal._translate_image(self.base_price, -7, 0)
        blank = np.full_like(self.base_price, 238)
        self.assertIs(
            classifier.classify(bright).state,
            renewal.PriceState.UNCHANGED,
        )
        self.assertIs(
            classifier.classify(clipped).state,
            renewal.PriceState.AMBIGUOUS,
        )
        self.assertIs(
            classifier.classify(blank).state,
            renewal.PriceState.AMBIGUOUS,
        )
        wrong_popup = np.full_like(self.guard, 50)
        guard_detector = renewal.RenewalModalGuard(
            self.guard,
            self.price_box,
            shift_limit=4,
        )
        self.assertFalse(
            guard_detector.register(wrong_popup, 0.0, 0.0).valid
        )

    def test_exact_baseline_and_repeated_candidate_use_safe_cache(self) -> None:
        classifier = renewal.RenewalPriceClassifier(
            self.base_price,
            unchanged_limit=0.035,
            stability_limit=0.015,
        )
        with patch.object(
            classifier.detector,
            "prepare_pair_reusable",
            wraps=classifier.detector.prepare_pair_reusable,
        ) as prepare_pair:
            first_baseline = classifier.classify(self.base_price.copy())
            second_baseline = classifier.classify(self.base_price.copy())
            self.assertIs(first_baseline, second_baseline)
            self.assertEqual(prepare_pair.call_count, 0)

            first_changed = classifier.classify(self.changed_price.copy())
            calls_after_first = prepare_pair.call_count
            second_changed = classifier.classify(self.changed_price.copy())
            self.assertIs(first_changed, second_changed)
            self.assertGreater(calls_after_first, 0)
            self.assertEqual(prepare_pair.call_count, calls_after_first)

    def test_real_same_glyph_illumination_holdout_is_unchanged(self) -> None:
        def decode(encoded: str) -> np.ndarray:
            raw = np.frombuffer(base64.b64decode(encoded), dtype=np.uint8)
            image = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
            self.assertIsNotNone(image)
            return image

        baseline = decode(_REAL_SAME_ILLUMINATION_BASELINE)
        candidate = decode(_REAL_SAME_ILLUMINATION_CANDIDATE)
        classifier = renewal.RenewalPriceClassifier(
            baseline,
            unchanged_limit=0.040,
            stability_limit=0.030,
        )
        first = classifier.classify(candidate)
        second = classifier.classify(candidate.copy())
        self.assertIs(first.state, renewal.PriceState.UNCHANGED)
        self.assertEqual(
            first.reason,
            "illumination_normalized_same_glyph",
        )
        self.assertTrue(classifier.same_candidate(first, second))

        changed_image = baseline.copy()
        changed_image[:, 99:111] = np.fliplr(
            changed_image[:, 99:111]
        )
        changed = classifier.classify(changed_image)
        self.assertIsNot(
            changed.state,
            renewal.PriceState.UNCHANGED,
        )

    def test_recorded_same_price_15_reopen_is_unchanged(self) -> None:
        def decode(encoded: str) -> np.ndarray:
            raw = np.frombuffer(base64.b64decode(encoded), dtype=np.uint8)
            image = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
            self.assertIsNotNone(image)
            return image

        baseline = decode(_REAL_SAME_PRICE_15_BASELINE)
        candidate = decode(_REAL_SAME_PRICE_15_CANDIDATE)
        classifier = renewal.RenewalPriceClassifier(
            baseline,
            unchanged_limit=0.035,
            stability_limit=0.015,
        )
        first = classifier.classify(candidate)
        second = classifier.classify(candidate.copy())

        self.assertIs(first.state, renewal.PriceState.UNCHANGED)
        self.assertEqual(
            first.reason,
            "illumination_normalized_same_glyph",
        )
        self.assertTrue(classifier.same_candidate(first, second))

    def test_recorded_same_price_39_global_resampling_never_orders(self) -> None:
        def decode(encoded: str) -> np.ndarray:
            raw = np.frombuffer(base64.b64decode(encoded), dtype=np.uint8)
            image = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
            self.assertIsNotNone(image)
            return image

        baseline = decode(_REAL_SAME_PRICE_39_BASELINE)
        candidate = decode(_REAL_SAME_PRICE_39_CANDIDATE_V2)
        classifier = renewal.RenewalPriceClassifier(
            baseline,
            unchanged_limit=0.035,
            stability_limit=0.015,
        )
        first = classifier.classify(candidate)
        second = classifier.classify(candidate.copy())

        self.assertIs(first.state, renewal.PriceState.AMBIGUOUS)
        self.assertEqual(first.reason, "global_glyph_resampling")
        self.assertFalse(classifier.same_candidate(first, second))
        self.assertIsNot(first.state, renewal.PriceState.CHANGED)

    def test_calibration_observed_render_variant_resolves_only_gray_zone(
        self,
    ) -> None:
        def decode(encoded: str) -> np.ndarray:
            raw = np.frombuffer(base64.b64decode(encoded), dtype=np.uint8)
            image = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
            self.assertIsNotNone(image)
            return image

        baseline = decode(_REAL_SAME_PRICE_39_BASELINE)
        render_variant = decode(_REAL_SAME_PRICE_39_CANDIDATE_V2)
        classifier = renewal.RenewalCalibratedPriceClassifier(
            baseline,
            [render_variant],
            unchanged_limit=0.035,
            stability_limit=0.015,
        )

        first = classifier.classify(render_variant)
        second = classifier.classify(render_variant.copy())
        self.assertIs(first.state, renewal.PriceState.UNCHANGED)
        self.assertEqual(first.reason, "calibrated_render_variant")
        self.assertTrue(classifier.same_candidate(first, second))

        changed = baseline.copy()
        changed[:, 80:100] = np.fliplr(changed[:, 80:100])
        changed_first = classifier.classify(changed)
        changed_second = classifier.classify(changed.copy())
        self.assertIs(changed_first.state, renewal.PriceState.CHANGED)
        self.assertTrue(
            classifier.same_candidate(changed_first, changed_second)
        )

    def test_subthreshold_native_glyph_change_is_not_hidden_as_same(self) -> None:
        raw = np.frombuffer(
            base64.b64decode(_REAL_SAME_ILLUMINATION_BASELINE),
            dtype=np.uint8,
        )
        baseline = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
        self.assertIsNotNone(baseline)
        candidate = baseline.copy()
        candidate[:, 90:92] = np.fliplr(candidate[:, 90:92])

        classifier = renewal.RenewalPriceClassifier(
            baseline,
            unchanged_limit=0.040,
            stability_limit=0.030,
        )
        first = classifier.classify(candidate)
        second = classifier.classify(candidate.copy())

        # This change is deliberately below the legacy 0.040 unchanged
        # threshold. Native-resolution glyph evidence must still promote it.
        self.assertLess(first.global_score, 0.040)
        self.assertIs(first.state, renewal.PriceState.CHANGED)
        self.assertEqual(first.reason, "native_glyph_structure_change")
        self.assertTrue(classifier.same_candidate(first, second))

    def test_real_same_glyph_raw_alignment_holdout_is_unchanged(self) -> None:
        def decode(encoded: str) -> np.ndarray:
            raw = np.frombuffer(base64.b64decode(encoded), dtype=np.uint8)
            image = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
            self.assertIsNotNone(image)
            return image

        baseline = decode(_REAL_SAME_ILLUMINATION_BASELINE)
        candidate = decode(_REAL_SAME_ILLUMINATION_SHIFTED_CANDIDATE)
        classifier = renewal.RenewalPriceClassifier(
            baseline,
            unchanged_limit=0.035,
            stability_limit=0.015,
        )
        first = classifier.classify(candidate)
        second = classifier.classify(candidate.copy())
        self.assertIs(first.state, renewal.PriceState.UNCHANGED)
        self.assertEqual(
            first.reason,
            "illumination_normalized_same_glyph",
        )
        self.assertTrue(classifier.same_candidate(first, second))

    def test_stable_partial_price_clipping_never_authorizes_change(self) -> None:
        classifier = renewal.RenewalPriceClassifier(
            self.base_price,
            unchanged_limit=0.035,
            stability_limit=0.015,
        )
        background = int(np.median(self.base_price[:, 0]))
        for edge, clipping_widths in (
            ("left", (6, 10, 15)),
            # A 10 px cut of this tiny synthetic font is pixel-identical in
            # density and bounds to a legitimate final digit.  Keep only
            # structurally incomplete right cuts here; the embedded real FC
            # corpus separately covers its 2..20 px right-edge transitions.
            ("right", (15, 20)),
        ):
            for pixels in clipping_widths:
                with self.subTest(edge=edge, pixels=pixels):
                    clipped = self.base_price.copy()
                    if edge == "left":
                        clipped[:, :pixels] = background
                    else:
                        clipped[:, -pixels:] = background
                    first = classifier.classify(clipped)
                    second = classifier.classify(clipped.copy())
                    self.assertFalse(
                        first.state is renewal.PriceState.CHANGED
                        and classifier.same_candidate(first, second)
                    )
        for edge in ("top", "bottom"):
            for pixels in (12, 16, 20):
                with self.subTest(edge=edge, pixels=pixels):
                    clipped = self.base_price.copy()
                    if edge == "top":
                        clipped[:pixels, :] = background
                    else:
                        clipped[-pixels:, :] = background
                    first = classifier.classify(clipped)
                    second = classifier.classify(clipped.copy())
                    self.assertFalse(
                        first.state is renewal.PriceState.CHANGED
                        and classifier.same_candidate(first, second)
                    )
        for top, bottom in ((6, 10), (8, 12)):
            with self.subTest(gap="horizontal", top=top, bottom=bottom):
                occluded = self.base_price.copy()
                occluded[top:bottom, :] = background
                first = classifier.classify(occluded)
                second = classifier.classify(occluded.copy())
                self.assertFalse(
                    first.state is renewal.PriceState.CHANGED
                    and classifier.same_candidate(first, second)
                )
        for left, right in ((8, 13), (14, 19), (20, 25)):
            with self.subTest(gap="vertical", left=left, right=right):
                occluded = self.base_price.copy()
                occluded[:, left:right] = background
                first = classifier.classify(occluded)
                second = classifier.classify(occluded.copy())
                self.assertFalse(
                    first.state is renewal.PriceState.CHANGED
                    and classifier.same_candidate(first, second)
                )

    def test_five_independent_openings_build_v9_calibration(self) -> None:
        sessions = [
            [
                renewal._translate_image(self.guard, shift_x, 0)
                for _ in range(renewal.RENEWAL_CALIBRATION_FRAMES_PER_OPENING)
            ]
            for shift_x in (0, 1, -1, 2, -2)
        ]
        closed_samples = [
            np.zeros_like(self.guard)
            for _ in range(4)
        ]
        result = renewal.build_calibration_result(
            sessions,
            self.price_box,
            closed_samples,
        )
        self.assertEqual(
            result.calibration_openings,
            renewal.RENEWAL_CALIBRATION_OPENINGS,
        )
        self.assertEqual(result.registration_shift_limit, 4)
        self.assertEqual(result.closed_guard.shape, self.guard.shape)
        self.assertGreaterEqual(len(result.baseline_variants), 1)
        self.assertLessEqual(
            len(result.baseline_variants),
            renewal.RENEWAL_MAX_BASELINE_VARIANTS,
        )
        self.assertGreaterEqual(result.unchanged_limit, 0.035)
        self.assertLessEqual(result.unchanged_limit, 0.040)

    def test_v9_calibration_still_rejects_a_real_price_change(self) -> None:
        sessions = [
            [self.guard.copy() for _ in range(4)]
            for _ in range(renewal.RENEWAL_CALIBRATION_OPENINGS)
        ]
        sessions[-1] = [self.changed_guard.copy() for _ in range(4)]
        closed_samples = [
            np.zeros_like(self.guard)
            for _ in range(4)
        ]
        with self.assertRaisesRegex(
            ValueError,
            "숫자 구조가 달라졌습니다|기준과 일치하지 않습니다",
        ):
            renewal.build_calibration_result(
                sessions,
                self.price_box,
                closed_samples,
            )

    def test_calibration_never_persists_an_ambiguous_render_as_same(
        self,
    ) -> None:
        def decode(encoded: str) -> np.ndarray:
            raw = np.frombuffer(base64.b64decode(encoded), dtype=np.uint8)
            image = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
            self.assertIsNotNone(image)
            return image

        baseline = decode(_REAL_SAME_PRICE_39_BASELINE)
        ambiguous = decode(_REAL_SAME_PRICE_39_CANDIDATE_V2)
        price_box = (30, 20, 250, 68)

        def make_guard(price: np.ndarray) -> np.ndarray:
            guard = np.full((88, 280), 224, dtype=np.uint8)
            cv2.rectangle(guard, (1, 1), (278, 86), 90, 1)
            guard[20:68, 30:250] = price
            return guard

        baseline_guard = make_guard(baseline)
        ambiguous_guard = make_guard(ambiguous)
        sessions = [
            [baseline_guard.copy() for _ in range(4)]
            for _ in range(renewal.RENEWAL_CALIBRATION_OPENINGS)
        ]
        sessions[-1] = [ambiguous_guard.copy() for _ in range(4)]

        with self.assertRaises(ValueError):
            renewal.build_calibration_result(
                sessions,
                price_box,
                [np.zeros_like(baseline_guard) for _ in range(4)],
            )

    def test_v9_calibration_rejects_indistinguishable_closed_screen(self) -> None:
        sessions = [
            [self.guard.copy() for _ in range(4)]
            for _ in range(renewal.RENEWAL_CALIBRATION_OPENINGS)
        ]
        with self.assertRaisesRegex(ValueError, "구분되지 않습니다"):
            renewal.build_calibration_result(
                sessions,
                self.price_box,
                [self.guard.copy() for _ in range(4)],
            )

    def test_duplicate_wgc_sequence_ids_never_order(self) -> None:
        stop_event = threading.Event()
        engine = _DuplicateSequenceEngine(stop_event, self.guard)
        completed = _run(self.profile, engine)
        self.assertFalse(completed)
        self.assertEqual(
            [point for point in engine.clicks if point != (20, 10)],
            [],
        )

    def test_three_incomplete_popup_attempts_stop_without_order(self) -> None:
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            cycles=[[]],
        )
        completed = _run(
            self.profile,
            engine,
            monitor_only=True,
        )
        self.assertFalse(completed)
        self.assertEqual(engine.clicks, [(20, 10)] * 3)
        self.assertEqual(engine.escapes, 3)
        self.assertEqual(engine.mode, "closed")

    def test_recorded_false_orders_replay_as_unchanged(self) -> None:
        diagnostic_root = (
            Path.home()
            / "AppData"
            / "Local"
            / "mAuto"
            / "renewal_diagnostics"
        )
        event_names = (
            "20260724_045646_675835700_buy_initial_mismatch",
            "20260724_045707_476789900_buy_initial_mismatch",
            "20260724_045715_328935100_buy_unstable_candidate",
            "20260724_045715_879773500_buy_initial_mismatch",
            "20260724_045722_348406900_buy_initial_mismatch",
            "20260724_045730_899395200_buy_unstable_candidate",
            "20260724_045731_949375200_buy_initial_mismatch",
            "20260724_045737_023254800_buy_unstable_candidate",
        )
        event_dirs = [
            diagnostic_root / name
            for name in event_names
            if (diagnostic_root / name).is_dir()
        ]
        if not event_dirs:
            self.skipTest("local recorded false-order fixtures are unavailable")
        replayed = 0
        for event_dir in event_dirs:
            baseline = cv2.imread(
                str(event_dir / "baseline.png"),
                cv2.IMREAD_GRAYSCALE,
            )
            self.assertIsNotNone(baseline)
            classifier = renewal.RenewalPriceClassifier(
                baseline,
                unchanged_limit=0.035,
                stability_limit=0.015,
            )
            for candidate_path in sorted(event_dir.glob("candidate_*.png")):
                candidate = cv2.imread(
                    str(candidate_path),
                    cv2.IMREAD_GRAYSCALE,
                )
                result = classifier.classify(candidate)
                self.assertIs(
                    result.state,
                    renewal.PriceState.UNCHANGED,
                    str(candidate_path),
                )
                replayed += 1
        self.assertGreaterEqual(replayed, 1)

    def test_initial_mismatch_never_orders(self) -> None:
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            cycles=[[self.changed_guard, self.changed_guard]],
        )
        with TemporaryDirectory() as temp_dir:
            with patch.dict("os.environ", {"LOCALAPPDATA": temp_dir}):
                completed = _run(self.profile, engine)
        self.assertFalse(completed)
        self.assertEqual(engine.clicks, [(20, 10)])

    def test_existing_open_popup_is_closed_before_any_action_click(self) -> None:
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            cycles=[[self.guard, self.guard]],
            stop_after_closes=1,
        )
        engine.mode = "open"
        engine.open_frames = [self.guard, self.guard]
        completed = _run(self.profile, engine, monitor_only=True)
        self.assertFalse(completed)
        self.assertEqual(engine.escapes, 1)
        self.assertEqual(engine.clicks, [])

    def test_monitor_stop_during_popup_confirms_closed_state(self) -> None:
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            cycles=[[self.guard, self.guard]],
        )
        original_packet = engine.get_latest_frame_packet

        def stop_after_first_open_frame(timeout: float = 0.0):
            packet = original_packet(timeout)
            if engine.mode == "open" and engine.open_index >= 1:
                stop_event.set()
            return packet

        engine.get_latest_frame_packet = stop_after_first_open_frame
        completed = _run(self.profile, engine, monitor_only=True)
        self.assertFalse(completed)
        self.assertEqual(engine.clicks, [(20, 10)])
        self.assertEqual(engine.escapes, 1)
        self.assertEqual(engine.mode, "closed")

    def test_timer_firing_on_escape_still_confirms_closed_state(self) -> None:
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            cycles=[[self.guard, self.guard]],
        )
        original_escape = engine.on_escape

        def close_then_stop(*args, **kwargs) -> None:
            original_escape(*args, **kwargs)
            stop_event.set()

        engine.on_escape = close_then_stop
        completed = _run(self.profile, engine, monitor_only=True)
        self.assertFalse(completed)
        self.assertEqual(engine.clicks, [(20, 10)])
        self.assertEqual(engine.escapes, 1)
        self.assertEqual(engine.mode, "closed")

    def test_delayed_popup_after_stop_is_closed_before_return(self) -> None:
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            cycles=[[self.guard, self.guard]],
        )
        original_packet = engine.get_latest_frame_packet
        original_click = engine.on_click
        pending_packets = 0

        def delayed_click(hwnd: int, x: int, y: int) -> None:
            nonlocal pending_packets
            original_click(hwnd, x, y)
            if (x, y) == (20, 10):
                engine.mode = "closed"
                pending_packets = 4
                stop_event.set()

        def delayed_packet(timeout: float = 0.0):
            nonlocal pending_packets
            if pending_packets > 0:
                pending_packets -= 1
                if pending_packets == 0:
                    engine.mode = "open"
            return original_packet(timeout)

        def escape_only_if_open(*args, **kwargs) -> None:
            engine.escapes += 1
            if engine.mode == "open":
                engine.mode = "closed"

        engine.on_click = delayed_click
        engine.get_latest_frame_packet = delayed_packet
        engine.on_escape = escape_only_if_open
        with patch.object(
            renewal,
            "RENEWAL_POPUP_CLEANUP_GRACE_SECONDS",
            0.05,
        ):
            completed = _run(self.profile, engine, monitor_only=True)

        self.assertFalse(completed)
        self.assertEqual(engine.clicks, [(20, 10)])
        self.assertGreaterEqual(engine.escapes, 2)
        self.assertEqual(engine.mode, "closed")

    def test_transition_and_single_frame_glitch_never_order(self) -> None:
        stop_event = threading.Event()
        wrong_popup = np.full((60, 100), 50, dtype=np.uint8)
        engine = _FakeEngine(
            stop_event,
            cycles=[
                [self.guard, self.guard],
                [self.guard, self.guard],
                [
                    wrong_popup,
                    self.changed_guard,
                    self.guard,
                    self.guard,
                ],
            ],
            stop_after_closes=3,
        )
        completed = _run(self.profile, engine)
        self.assertFalse(completed)
        self.assertEqual(
            [point for point in engine.clicks if point != (20, 10)],
            [],
        )

    def test_faded_price_render_is_never_a_confirmed_change(self) -> None:
        baseline = renewal.decode_gray_png(self.profile.buy.baseline_png)
        self.assertGreater(baseline.size, 0)
        background = np.full_like(baseline, int(np.median(baseline[0])))
        classifier = renewal.RenewalPriceClassifier(
            baseline,
            unchanged_limit=0.035,
            stability_limit=0.015,
        )
        for alpha in (0.20, 0.35, 0.50, 0.65):
            faded = cv2.addWeighted(
                baseline,
                alpha,
                background,
                1.0 - alpha,
                0.0,
            )
            result = classifier.classify(faded)
            self.assertIsNot(
                result.state,
                renewal.PriceState.CHANGED,
                f"partial opacity {alpha:.2f} became an order signal",
            )

    def test_frame_size_mismatch_stops_before_first_click(self) -> None:
        stop_event = threading.Event()
        engine = _FakeEngine(stop_event, cycles=[[self.guard, self.guard]])
        self.profile.buy.calibrated_frame_width = 1928
        self.profile.buy.calibrated_frame_height = 1048
        with self.assertRaisesRegex(RuntimeError, "게임 창 크기"):
            _run(self.profile, engine)
        self.assertEqual(engine.clicks, [])

    def test_next_open_is_blocked_until_exact_ready_guard_returns(self) -> None:
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            cycles=[[self.guard, self.guard]],
            stop_after_closes=1,
        )
        engine.closed_guard = np.full_like(self.guard, 77)
        # Initial state must be ready; replace it with a wrong closed screen only
        # after the first open click.
        original_click = engine.on_click

        def click_then_break_ready(hwnd: int, x: int, y: int) -> None:
            original_click(hwnd, x, y)
            if (x, y) == (20, 10):
                engine.closed_guard = np.full_like(self.guard, 77)

        initial_ready = np.zeros_like(self.guard)
        engine.closed_guard = initial_ready
        engine.on_click = click_then_break_ready
        completed = _run(self.profile, engine)
        self.assertFalse(completed)
        self.assertEqual(engine.clicks, [(20, 10)])

    def test_unchanged_10000_cycles_has_zero_orders(self) -> None:
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            repeat_guard=self.guard,
            stop_after_closes=10_000,
        )
        completed = _run(self.profile, engine)
        self.assertFalse(completed)
        self.assertEqual(engine.escapes, 10_000)
        self.assertEqual(
            [point for point in engine.clicks if point != (20, 10)],
            [],
        )

    def test_stable_change_orders_once_in_correct_sequence_and_stops(self) -> None:
        stop_event = threading.Event()
        ordered_metrics: list[dict[str, object]] = []

        def collect_diagnostic(
            _side,
            reason,
            _baseline,
            _candidates,
            metadata,
            **_kwargs,
        ) -> None:
            if reason == "ordered":
                ordered_metrics.append(metadata)

        engine = _FakeEngine(
            stop_event,
            cycles=[
                [self.guard, self.guard],
                [self.guard, self.guard],
                [self.changed_guard, self.changed_guard],
            ],
        )
        completed = _run(
            self.profile,
            engine,
            diagnostic_sink=collect_diagnostic,
        )
        self.assertTrue(completed)
        self.assertEqual(
            engine.clicks,
            [
                (20, 10),
                (20, 10),
                (20, 10),
                (140, 70),
                (180, 80),
            ],
        )
        self.assertEqual(engine.escapes, 2)
        self.assertEqual(len(ordered_metrics), 1)
        self.assertLess(
            float(ordered_metrics[0]["second_frame_to_input_ms"]),
            4.0,
        )
        self.assertLess(
            float(ordered_metrics[0]["first_frame_to_input_ms"]),
            20.0,
        )

    def test_monitor_only_detects_change_without_any_order_click(self) -> None:
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            cycles=[
                [self.guard, self.guard],
                [self.guard, self.guard],
                [self.changed_guard, self.changed_guard],
            ],
        )
        with TemporaryDirectory() as temp_dir:
            with patch.dict("os.environ", {"LOCALAPPDATA": temp_dir}):
                completed = _run(
                    self.profile,
                    engine,
                    monitor_only=True,
                )
        self.assertFalse(completed)
        self.assertEqual(
            engine.clicks,
            [(20, 10), (20, 10), (20, 10)],
        )

    def test_sell_uses_its_own_open_limit_and_final_coordinates(self) -> None:
        sell = renewal.RenewalSideProfile.from_dict(self.profile.buy.to_dict())
        sell.action_point = renewal.NormalizedPoint(30 / 199, 12 / 99)
        sell.limit_point = renewal.NormalizedPoint(130 / 199, 72 / 99)
        sell.confirm_point = renewal.NormalizedPoint(170 / 199, 82 / 99)
        self.profile.sell = sell
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            cycles=[
                [self.guard, self.guard],
                [self.guard, self.guard],
                [self.changed_guard, self.changed_guard],
            ],
        )

        original_on_click = engine.on_click

        def sell_click(hwnd: int, x: int, y: int) -> None:
            if (x, y) == (30, 12):
                engine.clicks.append((x, y))
                engine.cycle_index += 1
                engine.mode = "open"
                engine.open_index = 0
                index = min(engine.cycle_index, len(engine.cycles) - 1)
                engine.open_frames = engine.cycles[index]
                return
            original_on_click(hwnd, x, y)

        engine.on_click = sell_click
        completed = _run(self.profile, engine, side="sell")
        self.assertTrue(completed)
        self.assertEqual(
            engine.clicks,
            [
                (30, 12),
                (30, 12),
                (30, 12),
                (130, 72),
                (170, 82),
            ],
        )


if __name__ == "__main__":
    unittest.main()
