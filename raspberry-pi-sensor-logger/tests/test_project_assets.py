import json
from pathlib import Path


def test_usb_export_requires_real_mountpoint_and_copies_all_folders() -> None:
    text = Path("scripts/export_to_usb.sh").read_text(encoding="utf-8")

    assert "mountpoint -q" in text
    assert 'cp -a "$PROJECT_ROOT/data/gas"' in text
    assert 'cp -a "$PROJECT_ROOT/data/thermal"' in text
    assert 'cp -a "$PROJECT_ROOT/data/visible"' in text
    assert "inspection-export-" in text
    assert "sync" in text
    assert "rm -" not in text


def test_setup_targets_ubuntu_firmware_and_never_reboots_automatically() -> None:
    text = Path("scripts/setup_ubuntu.sh").read_text(encoding="utf-8")

    assert "/boot/firmware/config.txt" in text
    assert "dtparam=i2c_arm=on,i2c_arm_baudrate=400000" in text
    assert "enable_uart=1" in text
    assert "dialout" in text
    assert "python3 -m venv .venv" in text
    assert "v4l-utils" in text
    executable_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "echo "))
    ]
    assert "sudo reboot" not in executable_lines


def test_systemd_service_uses_expected_user_path_and_interval() -> None:
    text = Path("deploy/sensor-logger.service").read_text(encoding="utf-8")

    assert "User=aabb942218" in text
    assert "WorkingDirectory=/home/aabb942218/sensor-reader" in text
    assert "--interval 5" in text
    assert "--camera-interval 2" in text
    assert "EnvironmentFile=/etc/default/sensor-logger" in text
    assert "Restart=on-failure" in text


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
    ):
        assert required in text


def test_vscode_json_files_are_valid_and_have_chinese_commands() -> None:
    launch = json.loads(Path(".vscode/launch.json").read_text(encoding="utf-8"))
    tasks = json.loads(Path(".vscode/tasks.json").read_text(encoding="utf-8"))

    assert {item["name"] for item in launch["configurations"]} == {
        "单次硬件测试",
        "连续采集（每5秒）",
    }
    assert "运行全部测试" in {item["label"] for item in tasks["tasks"]}
