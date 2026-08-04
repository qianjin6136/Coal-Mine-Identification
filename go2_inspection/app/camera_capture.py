import cv2
from pathlib import Path
from datetime import datetime


# =========================
# 可修改参数
# =========================
CAMERA_INDEX = 0          # 当前USB相机编号；打不开时尝试改成0或2
WIDTH = 1280
HEIGHT = 720
FPS = 30
SAVE_DIR = Path("dataset")


def main() -> None:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)

    # Windows下优先使用DirectShow，通常对普通USB相机兼容较好
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)

    # 尝试使用MJPG，降低USB传输压力
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, FPS)

    if not cap.isOpened():
        raise RuntimeError(
            f"无法打开编号为 {CAMERA_INDEX} 的相机。"
            "请先关闭OBS、腾讯会议等占用相机的软件，"
            "然后尝试把 CAMERA_INDEX 改为0或2。"
        )

    actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    actual_fps = cap.get(cv2.CAP_PROP_FPS)

    print(f"相机已打开：{actual_width} × {actual_height}，FPS={actual_fps:.1f}")
    print("操作：空格键拍照，Q键退出")
    print(f"照片保存目录：{SAVE_DIR.resolve()}")

    photo_count = 0

    try:
        while True:
            ret, frame = cap.read()

            if not ret or frame is None:
                print("读取画面失败。")
                break

            # 只在预览画面上显示提示，不会写入保存的训练图片
            preview = frame.copy()
            cv2.putText(
                preview,
                "SPACE: Capture   Q: Quit",
                (20, 35),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.putText(
                preview,
                f"Captured: {photo_count}",
                (20, 70),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )

            cv2.imshow("USB Camera Capture", preview)

            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                break

            if key == 32:  # 空格键
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                image_path = SAVE_DIR / f"image_{timestamp}.jpg"

                success = cv2.imwrite(
                    str(image_path),
                    frame,
                    [cv2.IMWRITE_JPEG_QUALITY, 95],
                )

                if success:
                    photo_count += 1
                    print(f"已保存：{image_path}")
                else:
                    print(f"保存失败：{image_path}")

    finally:
        cap.release()
        cv2.destroyAllWindows()
        print(f"程序结束，共拍摄 {photo_count} 张照片。")


if __name__ == "__main__":
    main()
