"""把仓库根目录的运行文件 + skill/ 目录组装成 WorkBuddy 技能包 zip。

为什么要这个脚本：代码只保留一份（根目录），技能包所需的
` scripts/ ` 结构在打包时临时生成，避免同一份 run_loop.py 在仓库里存两份而漂移。

用法
  python tools/build_skill.py             # 输出到 dist/douyin-live-interact.zip
  python tools/build_skill.py --out xxx.zip
"""
import argparse
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = 'douyin-live-interact'

# 根目录 -> 技能包内路径
FILE_MAP = {
    'run_loop.py': 'scripts/run_loop.py',
    'cdp_client.py': 'scripts/cdp_client.py',
    'start.ps1': 'scripts/start.ps1',
    'stop.ps1': 'scripts/stop.ps1',
    'diag_welcome.py': 'scripts/diag_welcome.py',
    'config.example.json': 'scripts/config.example.json',
    'panel.html': 'panel.html',
}
SKILL_FILES = ['skill/SKILL.md', 'skill/references/troubleshooting.md', 'skill/assets/icon.png']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(ROOT, 'dist', f'{NAME}.zip'))
    args = ap.parse_args()

    missing = [f for f in list(FILE_MAP) + SKILL_FILES if not os.path.exists(os.path.join(ROOT, f))]
    if missing:
        print('缺少文件，无法打包：')
        for m in missing:
            print('  -', m)
        return 1

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    tmp = os.path.join(ROOT, 'dist', '_tmp_' + NAME)
    shutil.rmtree(tmp, ignore_errors=True)

    for src, dst in FILE_MAP.items():
        target = os.path.join(tmp, dst)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(os.path.join(ROOT, src), target)
    for rel in SKILL_FILES:
        target = os.path.join(tmp, rel[len('skill/'):])
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(os.path.join(ROOT, rel), target)

    if os.path.exists(args.out):
        os.remove(args.out)
    with zipfile.ZipFile(args.out, 'w', zipfile.ZIP_DEFLATED) as z:
        for base, _dirs, files in os.walk(tmp):
            for f in files:
                full = os.path.join(base, f)
                arc = os.path.join(NAME, os.path.relpath(full, tmp)).replace('\\', '/')
                z.write(full, arc)

    shutil.rmtree(tmp, ignore_errors=True)
    print(f'已生成: {args.out}')
    with zipfile.ZipFile(args.out) as z:
        for info in z.infolist():
            print(f'  {info.filename}  {info.file_size} bytes')
    return 0


if __name__ == '__main__':
    sys.exit(main())
