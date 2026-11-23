"""Builds the README.md that goes next to CatR-Tools.exe: the app's README (without its Build section)
and catr's command reference, with `.\\catr\\catr.cmd ARGS` written as `CatR-Tools ARGS`.

    py -3 -I make_readme.py --app <app README.md> --catr <catr README.md> --out <README.md>
"""
import argparse
import re
from pathlib import Path


def app_part(text):
    """Everything before '## Build' (developer notes stay in the repo)."""
    return re.split(r'^## Build\s*$', text, maxsplit=1, flags=re.M)[0].rstrip() + '\n'


def catr_part(text):
    """catr's Commands, Safety rules and Good to know sections (not Development), written for
    CatR-Tools.exe: `.\\catr\\catr.cmd ARGS` becomes `CatR-Tools ARGS`."""
    start = text.index('## Commands')
    end = text.index('## Development')
    body = text[start:end].rstrip()
    body = re.sub(r'^The examples use .*\n', '', body, flags=re.M)     # the hint for repo users
    body = body.replace('## Commands', '## catr commands', 1)
    body = body.replace('.\\catr\\catr.cmd ', 'CatR-Tools ').replace('```powershell', '```')
    return body + '\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', required=True, type=Path)
    parser.add_argument('--catr', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    app = app_part(args.app.read_text('utf-8'))
    # The app's Credits section goes last, after catr's commands.
    app, _, credits = app.partition('## Credits')
    text = app.rstrip() + '\n\n' + catr_part(args.catr.read_text('utf-8'))
    if credits:
        text += '\n## Credits' + credits.rstrip() + '\n'
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding='utf-8')
    print(f'{args.out} ({len(text.splitlines())} lines)')


if __name__ == '__main__':
    main()
