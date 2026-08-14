"""테스트가 어느 파일부터 수집되든 레포 루트를 import 경로에 넣습니다.

지금까지는 알파벳순 첫 테스트가 sys.path를 심어주는 우연에 기대고 있어서
`pytest tests/test_session.py` 처럼 단독 실행하면 ModuleNotFoundError가 났습니다.
"""

from pathlib import Path
import sys

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
