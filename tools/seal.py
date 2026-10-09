# -*- coding: utf-8 -*-
"""공고 데이터 잠그기/풀기 — '링크를 받은 사람만' 볼 수 있게 (2026-10-08).
형식: OpenSSL `enc -aes-256-cbc -pbkdf2 -iter 100000 -md sha256` (머리 'Salted__'+salt 8바이트). 브라우저는 WebCrypto(PBKDF2+AES-CBC)로 같은 방식으로 푼다.
열쇠: 환경변수 RADAR_KEY (GitHub Actions 비밀값). 링크의 # 뒤(k=…)에도 같은 값이 들어가며, # 뒤는 서버로 전송되지 않는다.
사용: python tools/seal.py enc <평문> <암호문>   /   python tools/seal.py dec <암호문> <평문>"""
import os
import shutil
import subprocess
import sys

ITER = "100000"


def run(mode, src, dst):
    key = os.environ.get("RADAR_KEY", "")
    if not key:
        sys.exit("RADAR_KEY 가 없습니다")
    exe = shutil.which("openssl") or sys.exit("openssl 이 없습니다")
    args = [exe, "enc", "-aes-256-cbc", "-pbkdf2", "-iter", ITER, "-md", "sha256", "-salt", "-pass", "env:RADAR_KEY", "-in", src, "-out", dst]
    if mode == "dec":
        args.insert(2, "-d")
    subprocess.run(args, check=True)


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2], sys.argv[3])
