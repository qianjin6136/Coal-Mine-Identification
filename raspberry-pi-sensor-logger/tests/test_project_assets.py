import json
from pathlib import Path


def test_usb_export_safely_replaces_three_folders_at_mount_root() -> None:
    text = Path("scripts/export_to_usb.sh").read_text(encoding="utf-8")

    assert "mountpoint -q" in text
    assert 'DESTINATION="$(realpath "$1")"' in text
    assert '[[ "$DESTINATION" == "/" ]]' in text
    assert 'mktemp -d "$DESTINATION/.sensor-export.XXXXXX"' in text
    assert "for directory in gas thermal visible" in text
    assert 'cp -a "$PROJECT_ROOT/data/$directory" "$STAGING/"' in text
    assert 'rm -rf -- "$DESTINATION/$directory"' in text
    assert 'mv "$STAGING/$directory" "$DESTINATION/$directory"' in text
    assert "inspection-export-" not in text
    assert "sync" in text


def test_setup_supports_pi_os_and_ubuntu_and_never_reboots_automatically() -> None:
    text = Path("scripts/setup_raspberry_pi.sh").read_text(encoding="utf-8")

    assert "/boot/firmware/config.txt" in text
    assert "/boot/config.txt" in text
    assert "dtparam=i2c_arm=on,i2c_arm_baudrate=400000" in text
    assert "enable_uart=1" in text
    assert "dialout" in text
    assert "video" in text
    assert "--system-site-packages" in text
    assert "python3-opencv" in text
    assert "v4l-utils" in text
    assert "\nsudo reboot\n" not in text

    compatibility_wrapper = Path("scripts/setup_ubuntu.sh").read_text(
        encoding="utf-8"
    )
    assert "setup_raspberry_pi.sh" in compatibility_wrapper


def test_systemd_service_uses_install_time_user_path_and_interval() -> None:
    text = Path("deploy/sensor-logger.service").read_text(encoding="utf-8")

    assert "User=@@SENSOR_LOGGER_USER@@" in text
    assert 'WorkingDirectory="@@PROJECT_ROOT@@"' in text
    assert 'ExecStart="@@VENV_PYTHON@@"' in text
    assert "--interval 5" in text
    assert "--camera-interval 2" in text
    assert "EnvironmentFile=-/etc/default/sensor-logger" in text
    assert "Restart=on-failure" in text

    installer = Path("scripts/install_service.sh").read_text(encoding="utf-8")
    assert "EXPECTED_ROOT" not in installer
    assert "$(id -un)" in installer
    assert "@@SENSOR_LOGGER_USER@@" in installer


def test_hardware_diagnostics_cover_all_three_devices() -> None:
    text = Path("scripts/diagnose_hardware.sh").read_text(encoding="utf-8")

    for required in ("/dev/i2c-1", "0x33", "/dev/serial0", "/dev/video0"):
        assert required in text
    assert "i2cdetect" in text
    assert "v4l2-ctl" in text


def test_readme_contains_exact_wiring_and_download_commands() -> None:
    text = Path("../README.md").read_text(encoding="utf-8")

    for required in (
        "BCM2",
        "BCM3",
        "BCM14",
        "BCM15",
        "/dev/serial0",
        "0x33",
        "CH4",
        "O2",
        "CO",
        "H2S",
        "scp -r",
        "每 5 秒",
        "每 2 秒",
        "USB",
        "visible",
        "export_to_usb.sh",
        "setup_raspberry_pi.sh",
        "diagnose_hardware.sh",
        "require-all-hardware",
    ):
        assert required in text

    detailed = Path("README.md").read_text(encoding="utf-8")
    for required in (
        "Raspberry Pi OS Bookworm",
        "Ubuntu 22.04/24.04",
        "externally-managed-environment",
        "systemctl",
        "unknown encoding",
    ):
        assert required in detailed


def test_vscode_json_files_are_valid_and_have_chinese_commands() -> None:
    launch = json.loads(Path(".vscode/launch.json").read_text(encoding="utf-8"))
    tasks = json.loads(Path(".vscode/tasks.json").read_text(encoding="utf-8"))

    assert {item["name"] for item in launch["configurations"]} == {
        "单次硬件测试",
        "连续采集（每5秒）",
    }
    assert "运行全部测试" in {item["label"] for item in tasks["tasks"]}
