#!/usr/bin/env python3
"""Transfer the Raspberry Pi sensor logger from Ubuntu to a Raspberry Pi.

This program intentionally uses only the Python standard library plus the
system ``ssh`` command. It is designed for a VS Code integrated terminal on
Ubuntu 22.04.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


PROGRAM_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE = PROGRAM_DIR.parent / "raspberry-pi-sensor-logger"
DEFAULT_CONFIG = PROGRAM_DIR / "transfer-config.json"

# Only these files are sent. In particular, data/, logs/, .venv/ and the
# upper-computer application are never part of an upload.
TRANSFER_DIRECTORIES = ("deploy", "scripts", "src")
OPTIONAL_TEST_DIRECTORY = "tests"
TRANSFER_FILES = (
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
)
REQUIRED_SOURCE_FILES = (
    "pyproject.toml",
    "requirements.txt",
    "scripts/setup_raspberry_pi.sh",
    "scripts/diagnose_hardware.sh",
    "src/sensor_logger/__main__.py",
)
IGNORED_NAMES = {"__pycache__", ".pytest_cache"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}

HOST_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
USER_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class TransferError(RuntimeError):
    """A user-facing transfer failure."""


@dataclass(frozen=True)
class TransferSettings:
    source: Path
    host: str
    user: str
    port: int
    remote_path: str
    identity_file: Path | None
    include_tests: bool
    connect_timeout: int


def load_config(path: Path, *, required: bool) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise TransferError(f"配置文件不存在：{path}")
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransferError(f"无法读取配置文件 {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise TransferError(f"配置文件顶层必须是 JSON 对象：{path}")
    return value


def config_or_cli(
    cli_value: Any, config: dict[str, Any], name: str, default: Any = None
) -> Any:
    return cli_value if cli_value is not None else config.get(name, default)


def validate_remote_path(path: str) -> str:
    if not path or "\x00" in path or "\n" in path or "\r" in path:
        raise TransferError("树莓派目标目录不能为空或包含换行符")
    if path in {"~", "~/"}:
        raise TransferError("为了安全，目标目录不能直接使用用户主目录 ~")
    if path.startswith("~/") or path.startswith("/"):
        if path == "/":
            raise TransferError("为了安全，目标目录不能是根目录 /")
        if ".." in Path(path[2:] if path.startswith("~/") else path).parts:
            raise TransferError("为了安全，目标目录不能包含 ..")
        return path.rstrip("/") or "/"
    raise TransferError("树莓派目标目录必须是 ~/目录 或绝对路径")


def remote_path_shell_expression(path: str) -> str:
    """Return a safe POSIX-shell expression with working ~/ expansion."""
    path = validate_remote_path(path)
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


def validate_settings(settings: TransferSettings, *, require_remote: bool) -> None:
    source = settings.source.resolve()
    if not source.is_dir():
        raise TransferError(f"找不到树莓派程序目录：{source}")
    for relative in REQUIRED_SOURCE_FILES:
        if not (source / relative).is_file():
            raise TransferError(f"源目录缺少必要文件：{relative}")

    validate_remote_path(settings.remote_path)
    if not 1 <= settings.port <= 65535:
        raise TransferError("SSH 端口必须在 1 到 65535 之间")
    if settings.connect_timeout < 1:
        raise TransferError("连接超时必须大于 0 秒")

    if settings.identity_file is not None and not settings.identity_file.is_file():
        raise TransferError(f"SSH 私钥不存在：{settings.identity_file}")

    if require_remote:
        if (
            not settings.host
            or settings.host.startswith("-")
            or not HOST_PATTERN.fullmatch(settings.host)
        ):
            raise TransferError("树莓派地址无效；请填写 IP、主机名或 raspberrypi.local")
        if (
            not settings.user
            or settings.user.startswith("-")
            or not USER_PATTERN.fullmatch(settings.user)
        ):
            raise TransferError("树莓派用户名无效")


def should_ignore(path: Path) -> bool:
    return any(part in IGNORED_NAMES for part in path.parts) or path.suffix in IGNORED_SUFFIXES


def iter_directory_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise TransferError(f"为避免传出目录外文件，不允许符号链接：{path}")
        if path.is_file() and not should_ignore(path.relative_to(root)):
            yield path


def collect_transfer_files(source: Path, *, include_tests: bool) -> list[Path]:
    source = source.resolve()
    files: list[Path] = []
    directories = list(TRANSFER_DIRECTORIES)
    if include_tests:
        directories.append(OPTIONAL_TEST_DIRECTORY)

    for name in directories:
        root = source / name
        if not root.is_dir():
            raise TransferError(f"源目录缺少必要目录：{name}")
        files.extend(iter_directory_files(root))

    for name in TRANSFER_FILES:
        path = source / name
        if not path.is_file():
            raise TransferError(f"源目录缺少必要文件：{name}")
        files.append(path)

    return sorted(set(files), key=lambda item: item.relative_to(source).as_posix())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_archive(source: Path, files: Sequence[Path], output_path: Path) -> str:
    source = source.resolve()
    with tarfile.open(output_path, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
        for path in files:
            relative = path.resolve().relative_to(source)
            info = archive.gettarinfo(str(path), arcname=relative.as_posix())
            # Git executable bits can be lost when the source passed through
            # Windows. Ensure all shell scripts are executable on the Pi.
            if relative.suffix == ".sh":
                info.mode = 0o755
            with path.open("rb") as stream:
                archive.addfile(info, stream)
    return sha256_file(output_path)


def format_size(size: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def ssh_base(settings: TransferSettings) -> list[str]:
    command = [
        "ssh",
        "-p",
        str(settings.port),
        "-o",
        f"ConnectTimeout={settings.connect_timeout}",
    ]
    if settings.identity_file is not None:
        command.extend(("-i", str(settings.identity_file)))
    return command


def remote_login(settings: TransferSettings) -> str:
    return f"{settings.user}@{settings.host}"


def ensure_commands_available() -> None:
    if shutil.which("ssh") is None:
        raise TransferError(
            "系统缺少 ssh；请先执行：sudo apt install openssh-client"
        )


def run_checked(
    command: Sequence[str], *, description: str, stdin: Any = None
) -> None:
    try:
        subprocess.run(command, check=True, stdin=stdin)
    except FileNotFoundError as exc:
        raise TransferError(f"找不到命令：{command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise TransferError(f"{description}失败（退出码 {exc.returncode}）") from exc


def transfer_archive(settings: TransferSettings, archive: Path, digest: str) -> None:
    remote_archive = f"/tmp/sensor-reader-{uuid.uuid4().hex}.tar.gz"
    login = remote_login(settings)

    target_expression = remote_path_shell_expression(settings.remote_path)
    archive_quoted = shlex.quote(remote_archive)
    digest_quoted = shlex.quote(digest)
    command = (
        "set -eu; "
        f"archive={archive_quoted}; target={target_expression}; expected={digest_quoted}; "
        "trap 'rm -f -- \"$archive\"' EXIT; "
        "cat > \"$archive\"; "
        "actual=$(sha256sum \"$archive\" | awk '{print $1}'); "
        "if [ \"$actual\" != \"$expected\" ]; then "
        "echo '上传校验失败' >&2; exit 20; fi; "
        "mkdir -p -- \"$target\"; "
        "tar -xzf \"$archive\" -C \"$target\"; "
        "chmod +x \"$target\"/scripts/*.sh; "
        "test -f \"$target\"/pyproject.toml; "
        "test -f \"$target\"/src/sensor_logger/__main__.py; "
        "printf 'REMOTE_PATH=%s\\n' \"$target\""
    )

    print("\n通过 SSH 上传，随后在树莓派校验并解压……", flush=True)
    with archive.open("rb") as stream:
        run_checked(
            [*ssh_base(settings), login, command],
            description="SSH 上传、校验或解压",
            stdin=stream,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从 Ubuntu 22.04/VS Code 安全传输采集程序到树莓派"
    )
    parser.add_argument("--config", type=Path, help="JSON 配置文件")
    parser.add_argument("--host", help="树莓派 IP 或主机名")
    parser.add_argument("--user", help="树莓派 SSH 用户名")
    parser.add_argument("--port", type=int, help="SSH 端口，默认 22")
    parser.add_argument("--remote-path", help="树莓派目标目录，默认 ~/sensor-reader")
    parser.add_argument("--source", type=Path, help="本机树莓派采集程序目录")
    parser.add_argument("--identity-file", type=Path, help="SSH 私钥文件，可不填")
    parser.add_argument(
        "--include-tests",
        action="store_true",
        default=None,
        help="同时传输 tests/，便于在树莓派上运行测试",
    )
    parser.add_argument(
        "--connect-timeout", type=int, help="SSH 连接超时秒数，默认 10"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只检查并显示将传输的内容，不连接树莓派"
    )
    parser.add_argument("--yes", action="store_true", help="跳过传输前确认")
    parser.add_argument("--list-files", action="store_true", help="显示完整文件清单")
    return parser


def make_settings(args: argparse.Namespace) -> TransferSettings:
    config_path = args.config or DEFAULT_CONFIG
    config = load_config(config_path, required=args.config is not None)

    source_value = config_or_cli(args.source, config, "source", DEFAULT_SOURCE)
    identity_value = config_or_cli(args.identity_file, config, "identity_file")
    include_tests = bool(config_or_cli(args.include_tests, config, "include_tests", False))

    return TransferSettings(
        source=Path(source_value).expanduser(),
        host=str(config_or_cli(args.host, config, "host", "")),
        user=str(config_or_cli(args.user, config, "user", "")),
        port=int(config_or_cli(args.port, config, "port", 22)),
        remote_path=str(
            config_or_cli(args.remote_path, config, "remote_path", "~/sensor-reader")
        ),
        identity_file=Path(identity_value).expanduser() if identity_value else None,
        include_tests=include_tests,
        connect_timeout=int(
            config_or_cli(args.connect_timeout, config, "connect_timeout", 10)
        ),
    )


def print_summary(
    settings: TransferSettings,
    files: Sequence[Path],
    archive: Path,
    digest: str,
    *,
    list_files: bool,
    dry_run: bool,
) -> None:
    source = settings.source.resolve()
    print("树莓派程序传输清单")
    print(f"  本机源目录：{source}")
    if not dry_run:
        print(f"  树莓派地址：{settings.user}@{settings.host}:{settings.port}")
        print(f"  目标目录：  {settings.remote_path}")
    print(f"  文件数量：  {len(files)}")
    print(f"  压缩包大小：{format_size(archive.stat().st_size)}")
    print(f"  SHA-256：   {digest}")
    print("  明确不传：  data/、logs/、.venv/、go2_inspection/")

    if list_files:
        print("\n文件清单：")
        for path in files:
            print(f"  {path.relative_to(source).as_posix()}")


def confirm() -> bool:
    try:
        answer = input("\n确认传输到以上树莓派？请输入 y 继续 [y/N]：").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    archive_path: Path | None = None
    try:
        settings = make_settings(args)
        validate_settings(settings, require_remote=not args.dry_run)
        files = collect_transfer_files(
            settings.source, include_tests=settings.include_tests
        )

        with tempfile.TemporaryDirectory(prefix="pi-file-transfer-") as temp_dir:
            archive_path = Path(temp_dir) / "sensor-reader.tar.gz"
            digest = build_archive(settings.source, files, archive_path)
            print_summary(
                settings,
                files,
                archive_path,
                digest,
                list_files=args.list_files,
                dry_run=args.dry_run,
            )

            if args.dry_run:
                print("\n检查完成：未连接树莓派，也未修改任何文件。")
                return 0
            if not args.yes and not confirm():
                print("已取消，未传输任何文件。")
                return 0

            ensure_commands_available()
            transfer_archive(settings, archive_path, digest)

        print("\n传输完成。树莓派原有 data/、logs/ 和 .venv/ 未被修改。")
        print("首次安装请在树莓派执行：")
        print(f"  cd {settings.remote_path}")
        print("  ./scripts/setup_raspberry_pi.sh")
        print("  sudo reboot")
        return 0
    except KeyboardInterrupt:
        print("\n已由用户取消。", file=sys.stderr)
        return 130
    except (TypeError, ValueError) as exc:
        print(f"配置值无效：{exc}", file=sys.stderr)
        return 1
    except TransferError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        location = f"（{archive_path}）" if archive_path else ""
        print(f"文件操作失败{location}：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
