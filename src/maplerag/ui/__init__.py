"""챗 UI. 실행: uv run maplerag-ui"""
import subprocess
import sys
from pathlib import Path


def main() -> None:
    """streamlit 앱을 띄운다. 추가 인자는 streamlit 에 그대로 전달(예: --server.port 8600)."""
    app = Path(__file__).with_name("app.py")
    sys.exit(subprocess.call([sys.executable, "-m", "streamlit", "run", str(app), *sys.argv[1:]]))
