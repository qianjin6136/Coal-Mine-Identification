"""传感器与 USB 可见光相机采集器命令行入口。"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Sequence

from sensor_logger.gas import FourGasReader
from sensor_logger.service import SensorLogger, run_periodic
from sensor_logger.storage import GasCsvWriter
from sensor_logger.thermal import Mlx90640Camera, ThermalImageWriter
from sensor_logger.usbcamera import UsbCamera, VisibleCameraLogger, VisiblePackageWriter

LOGGER = logging.getLogger("sensor_logger")


def positive_interval(value: str) -> float:
    interval = float(value)
    if interval <= 0:
        raise argparse.ArgumentTypeError("采样间隔必须大于 0 秒")
    return interval


def positive_integer(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("数值必须为正整数")
    return result


def jpeg_quality(value: str) -> int:
    result = int(value)
    if not 1 <= result <= 100:
        raise argparse.ArgumentTypeError("JPEG 质量必须在 1 到 100 之间")
    return result


def camera_device(value: str) -> int | str:
    """数字使用 OpenCV 设备编号，其余值按 /dev/video* 路径处理。"""

    stripped = value.strip()
    if stripped.isdecimal():
        return int(stripped)
    if not stripped:
        raise argparse.ArgumentTypeError("相机设备不能为空")
    return stripped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="树莓派气体、红外热像与 USB 可见光三连拍采集器"
    )
    parser.add_argument(
        "--interval",
        type=positive_interval,
        default=5.0,
        help="气体和红外热像采样间隔秒数（默认：5）",
    )
    parser.add_argument(
        "--camera-interval",
        type=positive_interval,
        default=2.0,
        help="USB 相机三连拍间隔秒数（默认：2）",
    )
    parser.add_argument(
        "--serial-port",
        default="/dev/serial0",
        help="4MZ-HH4 串口设备（默认：/dev/serial0）",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="数据根目录（默认：data）",
    )
    parser.add_argument(
        "--camera-device",
        type=camera_device,
        default=0,
        help="OpenCV 相机编号或 /dev/video* 路径（默认：0）",
    )
    parser.add_argument("--camera-width", type=positive_integer, default=1280)
    parser.add_argument("--camera-height", type=positive_integer, default=720)
    parser.add_argument(
        "--camera-fps", type=positive_interval, default=30.0, help="请求帧率（默认：30）"
    )
    parser.add_argument(
        "--camera-quality",
        type=jpeg_quality,
        default=95,
        help="JPEG 质量 1-100（默认：95）",
    )
    parser.add_argument(
        "--camera-id",
        default="raspberry_pi_usb",
        help="写入 metadata.json 的相机编号",
    )
    parser.add_argument(
        "--station-id",
        default=os.environ.get("SENSOR_LOGGER_STATION_ID"),
        help="工位编号；也可通过 SENSOR_LOGGER_STATION_ID 提供（必填）",
    )
    parser.add_argument(
        "--once", action="store_true", help="只采集一组传感器数据和一组三连拍后退出"
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="日志级别（默认：INFO）",
    )
    return parser


def configure_logging(level: str, directory: Path = Path("logs")) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "sensor_logger.log"
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    for handler in LOGGER.handlers:
        handler.close()
    LOGGER.handlers.clear()
    LOGGER.setLevel(getattr(logging, level))
    LOGGER.propagate = False

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    LOGGER.addHandler(console_handler)
    return log_path


def _open_serial(port: str):
    import serial

    return serial.Serial(
        port=port,
        baudrate=9600,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.6,
        write_timeout=0.6,
    )


def _close_resource(resource, name: str) -> None:
    if resource is None:
        return
    try:
        resource.close()
    except Exception:
        LOGGER.exception("关闭%s失败", name)


def _validated_identifiers(
    parser: argparse.ArgumentParser, station_id: str | None, camera_id: str
) -> tuple[str, str]:
    station = (station_id or "").strip()
    camera = camera_id.strip()
    if not station or len(station) > 64:
        parser.error("--station-id 必须提供且长度不能超过 64 个字符")
    if not camera or len(camera) > 64:
        parser.error("--camera-id 不能为空且长度不能超过 64 个字符")
    return station, camera


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    station_id, camera_id = _validated_identifiers(
        parser, args.station_id, args.camera_id
    )
    configure_logging(args.log_level)
    serial_port = None
    thermal_camera = None
    visible_logger = None
    visible_thread: threading.Thread | None = None
    stop_event = threading.Event()
    worker_errors: list[BaseException] = []

    try:
        LOGGER.info("正在打开气体串口 %s", args.serial_port)
        serial_port = _open_serial(args.serial_port)
        LOGGER.info("正在打开 MLX90640（I2C 地址 0x33）")
        thermal_camera = Mlx90640Camera()
        sensor_logger = SensorLogger(
            gas_reader=FourGasReader(serial_port),
            camera=thermal_camera,
            csv_writer=GasCsvWriter(args.data_dir),
            image_writer=ThermalImageWriter(args.data_dir),
        )

        visible_logger = VisibleCameraLogger(
            UsbCamera(
                device=args.camera_device,
                width=args.camera_width,
                height=args.camera_height,
                fps=args.camera_fps,
                quality=args.camera_quality,
            ),
            VisiblePackageWriter(args.data_dir, station_id, camera_id),
        )

        def capture_sensors(sample_id: int, timestamp: datetime):
            result = sensor_logger.capture(sample_id, timestamp)
            if result.errors:
                LOGGER.warning(
                    "第 %06d 组传感器数据已保存，但存在错误：%s",
                    sample_id,
                    "; ".join(result.errors),
                )
            else:
                LOGGER.info(
                    "第 %06d 组传感器数据完成：%s；%s",
                    sample_id,
                    result.csv_path,
                    result.png_path,
                )
            return result

        def capture_visible(_sample_id: int, timestamp: datetime):
            assert visible_logger is not None
            result = visible_logger.capture(timestamp)
            if result.errors:
                LOGGER.warning("%s", "; ".join(result.errors))
            else:
                LOGGER.info(
                    "可见光三连拍完成：%s（3 张）", result.package_path
                )
            return result

        if args.once:
            timestamp = datetime.now().astimezone()
            capture_sensors(1, timestamp)
            capture_visible(1, timestamp)
        else:
            LOGGER.info(
                "开始连续采集：传感器 %.1f 秒，相机三连拍 %.1f 秒；按 Ctrl+C 停止",
                args.interval,
                args.camera_interval,
            )

            def visible_worker() -> None:
                try:
                    run_periodic(
                        capture_visible,
                        interval=args.camera_interval,
                        stop_event=stop_event,
                    )
                except BaseException as error:
                    worker_errors.append(error)
                    LOGGER.exception("可见光采集线程异常退出")
                    stop_event.set()

            visible_thread = threading.Thread(
                target=visible_worker,
                name="visible-camera",
                daemon=True,
            )
            visible_thread.start()
            run_periodic(
                capture_sensors,
                interval=args.interval,
                stop_event=stop_event,
            )
            if worker_errors:
                raise RuntimeError("可见光采集线程异常退出") from worker_errors[0]
        return 0
    except KeyboardInterrupt:
        LOGGER.info("收到停止命令，正在安全关闭")
        return 130
    except Exception:
        LOGGER.exception("采集器启动或运行失败")
        return 1
    finally:
        stop_event.set()
        if visible_thread is not None:
            visible_thread.join(timeout=5.0)
            if visible_thread.is_alive():
                LOGGER.error("可见光采集线程未在 5 秒内退出")
        _close_resource(visible_logger, "USB 相机")
        _close_resource(thermal_camera, "MLX90640")
        _close_resource(serial_port, "气体串口")


if __name__ == "__main__":
    raise SystemExit(main())
