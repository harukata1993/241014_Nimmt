"""Start the local Nimmt server and a temporary Cloudflare Quick Tunnel."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parent


def main() -> int:
    cloudflared = shutil.which("cloudflared")
    if cloudflared is None:
        print(
            "cloudflared が見つかりません。先に Cloudflare の公式手順で "
            "cloudflared をインストールしてください: "
            "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/",
            file=sys.stderr,
        )
        return 1

    server = subprocess.Popen(
        [sys.executable, "-u", str(PROJECT_ROOT / "nimmt_web.py")],
        cwd=PROJECT_ROOT,
    )
    tunnel = None
    exit_code = 0
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if server.poll() is not None:
                raise RuntimeError(
                    "Webサーバーが起動しませんでした。ポート8000が使用中でないか確認してください。"
                )
            try:
                with urlopen("http://127.0.0.1:8000/api/state", timeout=0.5):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Webサーバーの起動確認がタイムアウトしました。")

        tunnel = subprocess.Popen(
            [
                cloudflared,
                "tunnel",
                "--url",
                "http://127.0.0.1:8000",
            ],
            cwd=PROJECT_ROOT,
        )
        while server.poll() is None and tunnel.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n対戦サーバーと一時URLを終了しています。")
    except (OSError, RuntimeError) as error:
        print(f"オンラインサーバーを起動できませんでした: {error}", file=sys.stderr)
        exit_code = 1
    finally:
        if tunnel is not None and tunnel.poll() is None:
            tunnel.terminate()
            tunnel.wait()
        if server.poll() is None:
            server.terminate()
            server.wait()
    if tunnel is not None and tunnel.returncode not in (None, 0):
        exit_code = tunnel.returncode
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
