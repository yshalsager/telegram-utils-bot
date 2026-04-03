from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

SCRIPT_BLOCK_RE = re.compile(r'(?ms)^#\s*///\s*script\s*\n(?P<body>.*?)^#\s*///\s*$')


def parse_pep723_dependencies(path: Path) -> list[str]:
    text = path.read_text(encoding='utf-8')
    match = SCRIPT_BLOCK_RE.search(text)
    if not match:
        if '# /// script' in text:
            raise ValueError(f'{path}: malformed PEP 723 script block')
        return []

    body = (
        '\n'.join(
            line[1:].lstrip() if line.startswith('#') else line
            for line in match.group('body').splitlines()
        )
        + '\n'
    )

    try:
        data = tomllib.loads(body)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f'{path}: invalid PEP 723 TOML: {e}') from e

    dependencies = data.get('dependencies', [])
    if dependencies is None:
        return []
    if not isinstance(dependencies, list) or not all(
        isinstance(item, str) for item in dependencies
    ):
        raise ValueError(f'{path}: dependencies must be list[str]')
    return [item.strip() for item in dependencies if item.strip()]


def collect_dependencies(plugins_dir: Path) -> list[str]:
    dependencies: list[str] = []
    for plugin_file in sorted(plugins_dir.glob('*.py')):
        if plugin_file.name.startswith(('.', '_')) or plugin_file.name == '__init__.py':
            continue
        dependencies.extend(parse_pep723_dependencies(plugin_file))

    seen: set[str] = set()
    deduplicated: list[str] = []
    for dependency in dependencies:
        if dependency not in seen:
            seen.add(dependency)
            deduplicated.append(dependency)
    return deduplicated


def resolve_python(python_bin: str | None) -> str:
    if python_bin:
        return python_bin
    venv_python = Path('.venv/bin/python')
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def install_dependencies(dependencies: list[str], python_bin: str, *, dry_run: bool = False) -> int:
    if not dependencies:
        sys.stdout.write('No plugin dependencies found.\n')
        return 0

    command = ['uv', 'pip', 'install', '--python', python_bin, *dependencies]
    sys.stdout.write('Plugin dependencies:\n')
    for dependency in dependencies:
        sys.stdout.write(f'- {dependency}\n')
    sys.stdout.write(f'Command: {" ".join(command)}\n')

    if dry_run:
        return 0
    result = subprocess.run(command, check=False)  # noqa: S603
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Install dependencies declared in PEP 723 blocks for state plugins.'
    )
    parser.add_argument('--plugins-dir', default='state/plugins')
    parser.add_argument('--python', default=None)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)

    plugins_dir = Path(args.plugins_dir)
    if not plugins_dir.exists():
        sys.stdout.write(f'Plugins directory not found: {plugins_dir}\n')
        return 0

    try:
        dependencies = collect_dependencies(plugins_dir)
    except ValueError as e:
        sys.stderr.write(f'Error: {e}\n')
        return 1

    python_bin = resolve_python(args.python)
    return install_dependencies(dependencies, python_bin, dry_run=args.dry_run)


if __name__ == '__main__':
    raise SystemExit(main())
