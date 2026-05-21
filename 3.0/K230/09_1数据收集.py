# 扑克牌识别训练数据收集
# 使用 media.sensor 方式（兼容 CanMV K230 v1.6）
# 适配 ST7701 MIPI LCD (640x480)

import time
import os
import sys

from media.sensor import *
from media.display import *
from media.media import *
from time import ticks_ms, ticks_diff
from machine import FPIOA
from ybUtils.YbKey import YbKey

sensor = None

try:

    print("poker_card_data_collection")
    fpioa = FPIOA()
    fpioa.set_function(53, FPIOA.GPIO53)

    key = YbKey()

    # ========== 关键修改区域 ==========
    # 1. 统一变量，避免手误
    # K230摄像头宽度必须是16的倍数，640是安全的
    SCREEN_WIDTH = 640
    SCREEN_HEIGHT = 480

    # 2. 初始化摄像头，指定好最大能力
    sensor = Sensor(width=SCREEN_WIDTH, height=SCREEN_HEIGHT)
    sensor.reset()

    # 3. 设置帧大小
    sensor.set_framesize(width=SCREEN_WIDTH, height=SCREEN_HEIGHT)
    sensor.set_pixformat(Sensor.RGB565)

    # 4. 初始化显示，严格对齐
    # ST7701是MIPI驱动，需要指定to_ide=False（或者直接去掉）
    Display.init(Display.ST7701, width=SCREEN_WIDTH, height=SCREEN_HEIGHT)

    # 初始化媒体管理器
    MediaManager.init()
    # 启动 sensor
    sensor.run()

    # ========== 目标扑克牌类别定义 ==========
    # 注意：必须与 deploy_config.json 中的 categories 顺序一致
    # 方块A、梅花A、2、3、K、黑桃5~10
    class_lst = [
        "D_A",     # 方块A
        "C_A",     # 梅花A
        "C_2",     # 梅花2
        "C_3",     # 梅花3
        "C_K",     # 梅花K
        "S_5",     # 黑桃5
        "S_6",     # 黑桃6
        "S_7",     # 黑桃7
        "S_8",     # 黑桃8
        "S_9",     # 黑桃9
        "S_10",    # 黑桃10
    ]

    save_folder = "/data/data/images/"
    prefix = "poker_"
    BATCH_COUNT = 1     # 每种扑克收集1个批次
    BATCH_SIZE = 100    # 每批次100张照片

    # 收集状态变量
    class_id = 0       # 当前类别索引
    batch = 1           # 当前批次 (1 ~ 3)
    counter = 0         # 当前批次已收集数量
    collecting = False  # 正在收集标志
    done = False        # 全班完成标志

    # 收集 BLANK 时建议：摄像头对着赛道/地面/无牌区域，采集各种光照下的背景
    # 收集扑克牌时：确保牌正面清晰，角度/距离/光照多样化

    # 按键去抖（初始化时读取按键当前状态，避免开机误触发）
    last_key_state = key.value()
    last_key_time = 0
    KEY_DEBOUNCE_MS = 300

    fps = 0
    fps_start = 0
    fps_count = 0

    while True:
        os.exitpoint()
        img = sensor.snapshot(chn=CAM_CHN_ID_0)

        now_ms = ticks_ms()
        if fps_start == 0:
            fps_start = now_ms
        fps_count += 1
        if ticks_diff(now_ms, fps_start) >= 1000:
            fps = fps_count * 1000 // ticks_diff(now_ms, fps_start)
            fps_count = 0
            fps_start = now_ms

        # 按键边沿检测（消抖）
        if key.value() == 1 and last_key_state == 0 and (now_ms - last_key_time) > KEY_DEBOUNCE_MS:
            last_key_state = 1
            last_key_time = now_ms

            if not done and not collecting:
                # 创建类别文件夹（如果已存在则跳过）
                class_path = save_folder + class_lst[class_id]
                try:
                    os.stat(class_path)
                except OSError:
                    os.mkdir(class_path)
                # 开始收集
                collecting = True
                counter = 0
        elif key.value() == 0:
            last_key_state = 0

        # 每帧都保存一张图片（收集时）—— 独立于按键检测，每帧执行
        if collecting and not done:
            counter += 1
            file_name = "{}{}_B{}_{}.jpg".format(prefix, class_lst[class_id], batch, counter)
            file_path = class_path + "/" + file_name
            with open(file_path, 'wb') as f:
                f.write(img.compress(95))
            print("img saved to \"{}\"".format(file_path))

            # 本批次完成
            if counter >= BATCH_SIZE:
                collecting = False
                batch += 1
                if batch > BATCH_COUNT:
                    batch = 1
                    class_id += 1
                    if class_id >= len(class_lst):
                        done = True
                        class_id = len(class_lst) - 1

        # ========== LCD 显示 ==========
        img.draw_string_advanced(10, 10, 28, "FPS: {}".format(fps), (0, 255, 0))

        if done:
            img.draw_string_advanced(10, 50, 40, "ALL {} CARDS DONE!".format(len(class_lst)), (0, 255, 255))
            img.draw_string_advanced(10, 100, 30, "Total: {} images".format(len(class_lst) * BATCH_COUNT * BATCH_SIZE), (255, 255, 0))
        elif collecting:
            img.draw_string_advanced(10, 50, 40, "[{}/{}] {}  Batch:{}  {}/{}".format(
                class_id + 1, len(class_lst), class_lst[class_id], batch, counter, BATCH_SIZE), (0, 255, 0))
        else:
            img.draw_string_advanced(10, 50, 40, "[{}/{}] {}  Batch: {}/{}".format(
                class_id + 1, len(class_lst), class_lst[class_id], batch, BATCH_COUNT), (200, 200, 200))
            if class_lst[class_id] == "BLANK":
                img.draw_string_advanced(10, 100, 28, "Press KEY: aim at track/no-card area", (255, 200, 0))
            else:
                img.draw_string_advanced(10, 100, 28, "Press KEY: aim at poker card", (255, 200, 0))

        img.compressed_for_ide()
        Display.show_image(img, x=0, y=0, layer=Display.LAYER_OSD0)

except KeyboardInterrupt as e:
    print("用户停止: ", e)
except BaseException as e:
    print(f"异常: {e}")
finally:
    if isinstance(sensor, Sensor):
        sensor.stop()
    Display.deinit()
    os.exitpoint(os.EXITPOINT_ENABLE_SLEEP)
    time.sleep_ms(100)
    MediaManager.deinit()
