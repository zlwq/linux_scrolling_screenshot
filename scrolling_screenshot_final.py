import os
import queue
import select
import shutil
import sys
import tempfile
import threading
import time
from collections import Counter
from datetime import datetime
import tkinter as tk
from tkinter import filedialog

import cv2
import mss
import numpy as np
from evdev import InputDevice, list_devices, ecodes
from mss.tools import to_png


FPS = 10
TOUCH_TIMEOUT = 0.18
SELECTION_DARKEN_FACTOR = 0.80
MIN_IMAGES_FOR_RECT = 20


# 当前已调好的固定参数，不改。
MASK_DIFF_THRESHOLD = 10
OUTLIER_JOIN_RADIUS = 4
OUTLIER_CONNECTIVITY = 4
RECT_EDGE_BAND = 1
RECT_EDGE_MIN_WHITE_RATIO = 0.3
RECT_EDGE_MAX_TRIM_RATIO = 0.20
RECT_EDGE_DILATE_RADIUS = 1

ORB_NFEATURES = 2500
ORB_SCALE_FACTOR = 1.3
ORB_NLEVELS = 4
ORB_EDGE_THRESHOLD = 31
ORB_FIRST_LEVEL = 0
ORB_WTA_K = 2
ORB_SCORE_TYPE = cv2.ORB_HARRIS_SCORE
ORB_PATCH_SIZE = 31
ORB_FAST_THRESHOLD = 12

MATCH_RATIO = 0.75
MIN_ORB_MATCHES = 8
X_TOLERANCE_RATIO = 0.015
X_TOLERANCE_MIN = 6
MIN_VERTICAL_SHIFT = 2
SHIFT_BIN_SIZE = 2
SHIFT_INLIER_TOLERANCE = 3
MIN_VERTICAL_MATCHES = 6


wheel_pending = 0
wheel_lock = threading.Lock()
touch_states = {}
touch_lock = threading.Lock()


def get_codes(device, event_type):
    caps = device.capabilities()
    result = set()

    for item in caps.get(event_type, []):
        if isinstance(item, tuple):
            result.add(item[0])
        else:
            result.add(item)

    return result


def wait_for_s():
    keyboard_devices = []

    for path in list_devices():
        try:
            device = InputDevice(path)
            key_codes = get_codes(device, ecodes.EV_KEY)

            if ecodes.KEY_S in key_codes:
                keyboard_devices.append(device)
            else:
                device.close()
        except PermissionError:
            pass
        except Exception:
            pass

    if not keyboard_devices:
        raise RuntimeError("没有找到可读取的键盘设备，无法监听 S 键")

    print("桌面保持正常显示。")
    print("按 S 键开始框选截图区域。")

    try:
        while True:
            ready, _, _ = select.select(keyboard_devices, [], [])

            for device in ready:
                for event in device.read():
                    if (
                        event.type == ecodes.EV_KEY
                        and event.code == ecodes.KEY_S
                        and event.value == 1
                    ):
                        return
    finally:
        for device in keyboard_devices:
            try:
                device.close()
            except Exception:
                pass


def select_region():
    result = {}

    with mss.mss() as sct:
        monitor = sct.monitors[1]
        shot = sct.grab(monitor)

    factor = max(0.0, min(1.0, SELECTION_DARKEN_FACTOR))
    table = bytes(min(255, int(i * factor)) for i in range(256))
    dark_rgb = shot.rgb.translate(table)

    temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    temp_path = temp_file.name
    temp_file.close()
    to_png(dark_rgb, shot.size, output=temp_path)

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)

    screen_left = monitor["left"]
    screen_top = monitor["top"]
    screen_width = monitor["width"]
    screen_height = monitor["height"]

    root.geometry(
        f"{screen_width}x{screen_height}"
        f"+{screen_left}+{screen_top}"
    )

    canvas = tk.Canvas(
        root,
        width=screen_width,
        height=screen_height,
        highlightthickness=0,
        cursor="crosshair",
    )
    canvas.pack(fill="both", expand=True)

    background = tk.PhotoImage(file=temp_path)
    canvas.create_image(0, 0, image=background, anchor="nw")

    start_x = 0
    start_y = 0
    rect = None

    def mouse_down(event):
        nonlocal start_x, start_y, rect
        start_x = event.x_root
        start_y = event.y_root

        if rect is not None:
            canvas.delete(rect)

        rect = canvas.create_rectangle(
            event.x,
            event.y,
            event.x,
            event.y,
            outline="#ff4d4d",
            width=3,
        )

    def mouse_move(event):
        if rect is not None:
            x0 = start_x - root.winfo_rootx()
            y0 = start_y - root.winfo_rooty()
            canvas.coords(rect, x0, y0, event.x, event.y)

    def mouse_up(event):
        x1 = event.x_root
        y1 = event.y_root
        left = min(start_x, x1)
        top = min(start_y, y1)
        width = abs(x1 - start_x)
        height = abs(y1 - start_y)

        if width > 5 and height > 5:
            result["region"] = {
                "left": left,
                "top": top,
                "width": width,
                "height": height,
            }
            root.quit()

    def cancel(_event=None):
        root.quit()

    root.bind("<ButtonPress-1>", mouse_down)
    root.bind("<B1-Motion>", mouse_move)
    root.bind("<ButtonRelease-1>", mouse_up)
    root.bind("<Escape>", cancel)
    root.focus_force()

    try:
        root.mainloop()
    finally:
        root.destroy()
        try:
            os.remove(temp_path)
        except OSError:
            pass

    return result.get("region")


def space_device_loop(device, stop_event):
    try:
        for event in device.read_loop():
            if (
                event.type == ecodes.EV_KEY
                and event.code == ecodes.KEY_SPACE
                and event.value == 1
            ):
                stop_event.set()
                return
    except OSError:
        pass
    except Exception as e:
        print(f"空格键监听设备退出: {device.name}: {e}")


def start_space_listener(stop_event):
    keyboard_devices = []

    for path in list_devices():
        try:
            device = InputDevice(path)
            key_codes = get_codes(device, ecodes.EV_KEY)

            if ecodes.KEY_SPACE in key_codes:
                threading.Thread(
                    target=space_device_loop,
                    args=(device, stop_event),
                    daemon=True,
                ).start()
                keyboard_devices.append(device)
            else:
                device.close()
        except PermissionError:
            pass
        except Exception:
            pass

    if not keyboard_devices:
        raise RuntimeError("没有找到可读取的键盘设备，无法监听空格键")

    return keyboard_devices


def choose_output_path():
    default_name = datetime.now().strftime("%Y%m%d_%H%M%S.png")

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    try:
        return filedialog.asksaveasfilename(
            parent=root,
            title="保存最终长图",
            initialfile=default_name,
            defaultextension=".png",
            filetypes=[("PNG 图片", "*.png"), ("所有文件", "*")],
        )
    finally:
        root.destroy()


def save_final_image(image, path):
    if not path:
        return False

    ok = cv2.imwrite(path, image)
    if not ok:
        raise RuntimeError(f"最终图片保存失败: {path}")

    return True



def build_union_mask_step(union_mask, prev_image, image):
    diff = cv2.absdiff(prev_image, image)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    mask = np.where(gray > MASK_DIFF_THRESHOLD, 255, 0).astype(np.uint8)
    return cv2.bitwise_or(union_mask, mask)


def remove_outliers(union_mask):
    radius = OUTLIER_JOIN_RADIUS
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (radius * 2 + 1, radius * 2 + 1),
    )
    expanded = cv2.dilate(union_mask, kernel)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        expanded,
        connectivity=OUTLIER_CONNECTIVITY,
    )

    if count <= 1:
        raise RuntimeError("没有检测到有效变化区域")

    best_label = None
    best_score = -1

    for label in range(1, count):
        w = stats[label, cv2.CC_STAT_WIDTH]
        h = stats[label, cv2.CC_STAT_HEIGHT]
        area = stats[label, cv2.CC_STAT_AREA]
        score = area * min(w, h)

        if score > best_score:
            best_score = score
            best_label = label

    main_region = np.where(labels == best_label, 255, 0).astype(np.uint8)
    return cv2.bitwise_and(union_mask, main_region)


def get_change_rect(clean_mask):
    points = cv2.findNonZero(clean_mask)

    if points is None:
        raise RuntimeError("去除离群点后没有变化区域")

    x, y, w, h = cv2.boundingRect(points)
    x1 = x
    y1 = y
    x2 = x + w
    y2 = y + h

    r = RECT_EDGE_DILATE_RADIUS
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (r * 2 + 1, r * 2 + 1),
    )
    decision_mask = cv2.dilate(clean_mask, kernel)

    original_w = x2 - x1
    original_h = y2 - y1
    max_trim_x = int(original_w * RECT_EDGE_MAX_TRIM_RATIO)
    max_trim_y = int(original_h * RECT_EDGE_MAX_TRIM_RATIO)
    band_size = RECT_EDGE_BAND
    min_white_ratio = RECT_EDGE_MIN_WHITE_RATIO

    trimmed = 0
    while x2 - x1 > band_size * 2 and trimmed < max_trim_x:
        band = decision_mask[y1:y2, x1:min(x1 + band_size, x2)]
        white_ratio = np.count_nonzero(band) / band.size
        if white_ratio >= min_white_ratio:
            break
        x1 += 1
        trimmed += 1

    trimmed = 0
    while x2 - x1 > band_size * 2 and trimmed < max_trim_x:
        band = decision_mask[y1:y2, max(x1, x2 - band_size):x2]
        white_ratio = np.count_nonzero(band) / band.size
        if white_ratio >= min_white_ratio:
            break
        x2 -= 1
        trimmed += 1

    trimmed = 0
    while y2 - y1 > band_size * 2 and trimmed < max_trim_y:
        band = decision_mask[y1:min(y1 + band_size, y2), x1:x2]
        white_ratio = np.count_nonzero(band) / band.size
        if white_ratio >= min_white_ratio:
            break
        y1 += 1
        trimmed += 1

    trimmed = 0
    while y2 - y1 > band_size * 2 and trimmed < max_trim_y:
        band = decision_mask[max(y1, y2 - band_size):y2, x1:x2]
        white_ratio = np.count_nonzero(band) / band.size
        if white_ratio >= min_white_ratio:
            break
        y2 -= 1
        trimmed += 1

    return x1, y1, x2, y2


def detect_and_match(img1, img2, rect):
    x1, y1, x2, y2 = rect
    crop1 = img1[y1:y2, x1:x2]
    crop2 = img2[y1:y2, x1:x2]
    gray1 = cv2.cvtColor(crop1, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(crop2, cv2.COLOR_BGR2GRAY)

    orb = cv2.ORB_create(
        nfeatures=ORB_NFEATURES,
        scaleFactor=ORB_SCALE_FACTOR,
        nlevels=ORB_NLEVELS,
        edgeThreshold=ORB_EDGE_THRESHOLD,
        firstLevel=ORB_FIRST_LEVEL,
        WTA_K=ORB_WTA_K,
        scoreType=ORB_SCORE_TYPE,
        patchSize=ORB_PATCH_SIZE,
        fastThreshold=ORB_FAST_THRESHOLD,
    )

    kp1, des1 = orb.detectAndCompute(gray1, None)
    kp2, des2 = orb.detectAndCompute(gray2, None)

    if des1 is None or des2 is None:
        raise RuntimeError("变化区域内没有足够 ORB 特征")

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    knn = matcher.knnMatch(des1, des2, k=2)
    matches = []

    for pair in knn:
        if len(pair) < 2:
            continue

        m, n = pair
        if m.distance >= MATCH_RATIO * n.distance:
            continue

        p1 = kp1[m.queryIdx].pt
        p2 = kp2[m.trainIdx].pt
        matches.append(
            {
                "match": m,
                "dx": p2[0] - p1[0],
                "dy": p2[1] - p1[1],
            }
        )

    if len(matches) < MIN_ORB_MATCHES:
        raise RuntimeError(f"ORB 有效匹配点太少: {len(matches)}")

    return matches


def estimate_vertical_shift(matches, crop_width, crop_height):
    x_tolerance = max(X_TOLERANCE_MIN, int(round(crop_width * X_TOLERANCE_RATIO)))

    candidates = [
        m
        for m in matches
        if abs(m["dx"]) <= x_tolerance
        and MIN_VERTICAL_SHIFT <= abs(m["dy"]) < crop_height
    ]

    if len(candidates) < MIN_VERTICAL_MATCHES:
        raise RuntimeError(
            f"符合垂直滚动模型的匹配点太少: {len(candidates)}"
        )

    counter = Counter(
        int(round(m["dy"] / SHIFT_BIN_SIZE)) * SHIFT_BIN_SIZE
        for m in candidates
    )
    centers = counter.most_common(10)
    best_inliers = []
    best_center = None

    for center, _ in centers:
        inliers = [
            m
            for m in candidates
            if abs(m["dy"] - center) <= SHIFT_INLIER_TOLERANCE
        ]
        if len(inliers) > len(best_inliers):
            best_inliers = inliers
            best_center = center

    if len(best_inliers) < MIN_VERTICAL_MATCHES:
        raise RuntimeError("无法得到稳定的垂直位移")

    dy = float(np.median([m["dy"] for m in best_inliers]))
    dx = float(np.median([m["dx"] for m in best_inliers]))

    return {
        "dx": dx,
        "dy": dy,
        "inliers": best_inliers,
        "center": best_center,
    }


class StreamingPanorama:
    def __init__(self, first_image, first_name, rect):
        self.rect = rect
        self.x1, self.y1, self.x2, self.y2 = rect
        self.crop_h = self.y2 - self.y1
        self.crop_w = self.x2 - self.x1

        self.first_image = first_image.copy()
        self.result_dynamic = first_image[
            self.y1:self.y2,
            self.x1:self.x2,
        ].copy()

        self.current_y_float = 0.0
        self.min_y = 0
        self.max_y = self.crop_h

        self.last_valid_image = first_image
        self.last_valid_name = first_name
        self.valid_count = 1

    def process(self, curr_image, curr_name):
        try:
            matches = detect_and_match(
                self.last_valid_image,
                curr_image,
                self.rect,
            )
            info = estimate_vertical_shift(
                matches,
                self.crop_w,
                self.crop_h,
            )
        except RuntimeError as e:
            message = str(e)

            if "符合垂直滚动模型的匹配点太少" in message:
                return "跳过近似重复帧"

            if "变化区域内没有足够 ORB 特征" in message:
                return "跳过无 ORB 特征帧"

            raise

        dy = info["dy"]
        self.current_y_float += -dy
        current_y = int(round(self.current_y_float))
        new_top = current_y
        new_bottom = current_y + self.crop_h

        curr_crop = curr_image[
            self.y1:self.y2,
            self.x1:self.x2,
        ]
        action = "无需增加"

        if new_top < self.min_y:
            extra = self.min_y - new_top

            if extra > self.crop_h:
                raise RuntimeError(
                    f"{self.last_valid_name} -> {curr_name}: "
                    f"顶部增量异常 {extra}px"
                )

            self.result_dynamic = np.vstack(
                [curr_crop[:extra, :], self.result_dynamic]
            )
            self.min_y = new_top
            action = f"顶部增加 {extra}px"

        elif new_bottom > self.max_y:
            extra = new_bottom - self.max_y

            if extra > self.crop_h:
                raise RuntimeError(
                    f"{self.last_valid_name} -> {curr_name}: "
                    f"底部增量异常 {extra}px"
                )

            self.result_dynamic = np.vstack(
                [
                    self.result_dynamic,
                    curr_crop[self.crop_h - extra:, :],
                ]
            )
            self.max_y = new_bottom
            action = f"底部增加 {extra}px"

        self.last_valid_image = curr_image
        self.last_valid_name = curr_name
        self.valid_count += 1

        return (
            f"dy={dy:+.1f}px  {action}  "
            f"窗口y={current_y}  范围=[{self.min_y}, {self.max_y})"
        )


def build_full_panorama(first_image, result_dynamic, rect, min_y, max_y):
    x1, y1, x2, y2 = rect
    h, _ = first_image.shape[:2]
    crop_h = y2 - y1
    dynamic_h = result_dynamic.shape[0]

    global_rows = np.arange(min_y, max_y)
    source_rows = np.clip(global_rows, 0, crop_h - 1) + y1
    middle = first_image[source_rows, :, :].copy()

    if middle.shape[0] != dynamic_h:
        raise RuntimeError(
            f"完整图中间区域高度异常: {middle.shape[0]} != {dynamic_h}"
        )

    middle[:, x1:x2] = result_dynamic
    top_static = first_image[:y1, :].copy()
    bottom_static = first_image[y2:h, :].copy()
    return np.vstack([top_static, middle, bottom_static])



def image_processing_loop(image_queue, result_state):
    startup_images = []
    startup_names = []
    prev_image = None
    union_mask = None
    expected_shape = None
    total_images = 0
    streamer = None
    frozen_clean_mask = None
    frozen_rect = None

    try:
        while True:
            path = image_queue.get()

            if path is None:
                break

            image = cv2.imread(path, cv2.IMREAD_COLOR)

            if image is None:
                raise RuntimeError(f"缓存图片读取失败: {path}")

            if expected_shape is None:
                expected_shape = image.shape
                union_mask = np.zeros(image.shape[:2], dtype=np.uint8)
            elif image.shape != expected_shape:
                raise RuntimeError(
                    f"图片尺寸不一致:\n{path}\n{image.shape} != {expected_shape}"
                )

            total_images += 1
            name = os.path.basename(path)

            if streamer is None:
                if prev_image is not None:
                    union_mask = build_union_mask_step(
                        union_mask,
                        prev_image,
                        image,
                    )

                prev_image = image
                startup_images.append(image)
                startup_names.append(name)

                print(
                    f"[处理 {total_images}] 启动阶段 "
                    f"{total_images}/{MIN_IMAGES_FOR_RECT}"
                )

                if total_images == MIN_IMAGES_FOR_RECT:
                    frozen_clean_mask = remove_outliers(union_mask)
                    frozen_rect = get_change_rect(frozen_clean_mask)
                    x1, y1, x2, y2 = frozen_rect
                    print()
                    print("========== 红框已冻结 ==========")
                    print("x1 =", x1)
                    print("y1 =", y1)
                    print("x2 =", x2)
                    print("y2 =", y2)
                    print("width =", x2 - x1)
                    print("height =", y2 - y1)
                    print("后续图片不会再改变红框。")
                    print()

                    streamer = StreamingPanorama(
                        startup_images[0],
                        startup_names[0],
                        frozen_rect,
                    )

                    for startup_image, startup_name in zip(
                        startup_images[1:],
                        startup_names[1:],
                    ):
                        status = streamer.process(
                            startup_image,
                            startup_name,
                        )
                        print(f"[在线拼接] {startup_name}: {status}")

                    startup_images.clear()
                    startup_names.clear()
                    prev_image = None

                    print()
                    print(
                        "已进入实时拼接阶段，当前范围:",
                        f"[{streamer.min_y}, {streamer.max_y})",
                    )
                    print()

                continue

            status = streamer.process(image, name)
            print(f"[在线拼接] {name}: {status}")

        if total_images < MIN_IMAGES_FOR_RECT:
            if total_images == 0:
                raise RuntimeError("没有获得任何截图")

            print()
            print(
                f"截图只有 {total_images} 张，未达到 {MIN_IMAGES_FOR_RECT} 张，"
                "改用普通离线处理。"
            )

            if total_images == 1:
                frozen_clean_mask = np.zeros(
                    startup_images[0].shape[:2],
                    dtype=np.uint8,
                )
                h, w = startup_images[0].shape[:2]
                frozen_rect = (0, 0, w, h)
            else:
                try:
                    frozen_clean_mask = remove_outliers(union_mask)
                    frozen_rect = get_change_rect(frozen_clean_mask)
                except RuntimeError as e:
                    print(
                        f"短批次无法稳定确定动态窗口: {e}；"
                        "退回整个截图区域。"
                    )
                    frozen_clean_mask = np.zeros(
                        startup_images[0].shape[:2],
                        dtype=np.uint8,
                    )
                    h, w = startup_images[0].shape[:2]
                    frozen_rect = (0, 0, w, h)

            x1, y1, x2, y2 = frozen_rect
            print("离线动态窗口:")
            print("x1 =", x1)
            print("y1 =", y1)
            print("x2 =", x2)
            print("y2 =", y2)
            print("width =", x2 - x1)
            print("height =", y2 - y1)

            streamer = StreamingPanorama(
                startup_images[0],
                startup_names[0],
                frozen_rect,
            )

            for startup_image, startup_name in zip(
                startup_images[1:],
                startup_names[1:],
            ):
                status = streamer.process(
                    startup_image,
                    startup_name,
                )
                print(f"[离线拼接] {startup_name}: {status}")

        if streamer is None:
            raise RuntimeError("动态窗口没有成功初始化")

        result_full = build_full_panorama(
            streamer.first_image,
            streamer.result_dynamic,
            frozen_rect,
            streamer.min_y,
            streamer.max_y,
        )

        print()
        print("========== 处理完成 ==========")
        print(
            "最终动态区域全局范围:",
            f"[{streamer.min_y}, {streamer.max_y})",
        )
        print(
            "完整图最终尺寸:",
            f"{result_full.shape[1]} x {result_full.shape[0]}",
        )
        result_state["image"] = result_full
        result_state["ok"] = True

    except Exception as e:
        result_state["error"] = e


def wheel_device_loop(device, wheel_code):
    global wheel_pending

    try:
        for event in device.read_loop():
            if event.type == ecodes.EV_REL and event.code == wheel_code:
                if event.value != 0:
                    with wheel_lock:
                        wheel_pending += 1
    except OSError:
        pass
    except Exception as e:
        print(f"滚轮设备退出: {device.name}: {e}")


def touch_device_loop(device):
    current_slot = 0
    active_slots = set()
    double_touch = False

    with touch_lock:
        touch_states[device.path] = {
            "active": False,
            "last_motion": 0,
        }

    try:
        for event in device.read_loop():
            if event.type == ecodes.EV_KEY:
                if event.code == ecodes.BTN_TOOL_DOUBLETAP:
                    double_touch = event.value != 0

                    if not double_touch:
                        with touch_lock:
                            touch_states[device.path]["active"] = False

            elif event.type == ecodes.EV_ABS:
                if event.code == ecodes.ABS_MT_SLOT:
                    current_slot = event.value

                elif event.code == ecodes.ABS_MT_TRACKING_ID:
                    if event.value >= 0:
                        active_slots.add(current_slot)
                    else:
                        active_slots.discard(current_slot)

                    active = double_touch or len(active_slots) >= 2
                    with touch_lock:
                        touch_states[device.path]["active"] = active

                elif event.code in (
                    ecodes.ABS_MT_POSITION_X,
                    ecodes.ABS_MT_POSITION_Y,
                    ecodes.ABS_X,
                    ecodes.ABS_Y,
                ):
                    active = double_touch or len(active_slots) >= 2

                    if active:
                        now = time.monotonic()
                        with touch_lock:
                            touch_states[device.path]["active"] = True
                            touch_states[device.path]["last_motion"] = now
    except OSError:
        pass
    except Exception as e:
        print(f"触摸设备退出: {device.name}: {e}")


def start_input_devices():
    devices = []

    for path in list_devices():
        try:
            device = InputDevice(path)
            rel_codes = get_codes(device, ecodes.EV_REL)
            abs_codes = get_codes(device, ecodes.EV_ABS)
            key_codes = get_codes(device, ecodes.EV_KEY)

            is_multitouch = (
                ecodes.ABS_MT_POSITION_X in abs_codes
                and ecodes.ABS_MT_POSITION_Y in abs_codes
            )
            has_doubletap = ecodes.BTN_TOOL_DOUBLETAP in key_codes

            if is_multitouch or has_doubletap:
                print(f"触摸设备: {device.path} | {device.name}")
                threading.Thread(
                    target=touch_device_loop,
                    args=(device,),
                    daemon=True,
                ).start()
                devices.append(device)
                continue

            hi_res = getattr(ecodes, "REL_WHEEL_HI_RES", None)
            wheel_code = None

            if hi_res is not None and hi_res in rel_codes:
                wheel_code = hi_res
            elif ecodes.REL_WHEEL in rel_codes:
                wheel_code = ecodes.REL_WHEEL

            if wheel_code is not None:
                print(f"滚轮设备: {device.path} | {device.name}")
                threading.Thread(
                    target=wheel_device_loop,
                    args=(device, wheel_code),
                    daemon=True,
                ).start()
                devices.append(device)
            else:
                device.close()

        except PermissionError:
            print(f"没有权限读取: {path}")
        except Exception:
            pass

    if not devices:
        raise RuntimeError(
            "没有找到可读取的鼠标/触摸设备，可能没有 /dev/input/event* 权限"
        )

    return devices


def save_image(sct, region, cache_dir, index, reason, image_queue):
    image = sct.grab(region)
    filename = os.path.join(cache_dir, f"{index:06d}_{reason}.png")
    temp_filename = filename + ".tmp"

    to_png(image.rgb, image.size, output=temp_filename)
    os.replace(temp_filename, filename)

    image_queue.put(filename)
    print(f"[截图 {index}] {reason}")


def main():
    global wheel_pending

    wait_for_s()
    region = select_region()

    if not region:
        print("没有选择区域")
        return

    time.sleep(0.2)

    cache_dir = ".scrolling_cache_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(cache_dir, exist_ok=True)

    image_queue = queue.Queue()
    result_state = {}
    processing_thread = threading.Thread(
        target=image_processing_loop,
        args=(image_queue, result_state),
        daemon=False,
    )
    processing_thread.start()

    devices = []
    keyboard_devices = []
    stop_event = threading.Event()
    counter = 0

    try:
        devices = start_input_devices()
        keyboard_devices = start_space_listener(stop_event)

        print()
        print("截图区域:")
        print(region)
        print()
        print("开始监听...")
        print("鼠标滚轮 -> 截 1 帧")
        print("两指滑动 -> 10 FPS")
        print("第 20 张到达后立即确定并冻结动态窗口")
        print("第 21 张开始边截图边实时拼接")
        print("滚动完成后按空格键结束")
        print()

        with mss.mss() as sct:
            counter += 1
            save_image(sct, region, cache_dir, counter, "initial", image_queue)
            next_touch_capture = 0

            while not stop_event.is_set():
                if "error" in result_state:
                    raise result_state["error"]

                now = time.monotonic()
                pending = 0

                with wheel_lock:
                    if wheel_pending > 0:
                        pending = wheel_pending
                        wheel_pending = 0

                for _ in range(pending):
                    if stop_event.is_set():
                        break
                    counter += 1
                    save_image(
                        sct,
                        region,
                        cache_dir,
                        counter,
                        "wheel",
                        image_queue,
                    )

                touch_active = False

                with touch_lock:
                    for state in touch_states.values():
                        if (
                            state["active"]
                            and now - state["last_motion"] <= TOUCH_TIMEOUT
                        ):
                            touch_active = True
                            break

                if (
                    not stop_event.is_set()
                    and touch_active
                    and now >= next_touch_capture
                ):
                    counter += 1
                    save_image(
                        sct,
                        region,
                        cache_dir,
                        counter,
                        "touch",
                        image_queue,
                    )
                    next_touch_capture = now + 1 / FPS

                time.sleep(0.005)

        print()
        print("截图结束，正在完成剩余图片处理...")

        image_queue.put(None)
        processing_thread.join()

        if "error" in result_state:
            raise result_state["error"]

        if not result_state.get("ok"):
            raise RuntimeError("图片处理线程未正常完成")

        output_path = choose_output_path()

        if output_path:
            save_final_image(result_state["image"], output_path)
            print("最终图片已保存:", output_path)
        else:
            print("已取消保存，没有输出图片。")

    finally:
        for device in devices + keyboard_devices:
            try:
                device.close()
            except Exception:
                pass

        if processing_thread.is_alive():
            image_queue.put(None)
            processing_thread.join()

        shutil.rmtree(cache_dir, ignore_errors=True)
        print("缓存图片已删除:", cache_dir)


if __name__ == "__main__":
    main()
