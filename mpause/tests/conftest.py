import sys
from pathlib import Path

# 프로젝트 루트를 import 경로에 넣는다(mpauseapp, ed25519_tiny).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
