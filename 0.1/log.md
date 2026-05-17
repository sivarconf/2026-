# 代码修改日志

## 2026-05-09

### 第五阶段：开环低速循迹

#### 新增文件

**`Core/Inc/app_line.h` / `Core/Src/app_line.c`**
- 灰度传感器黑线循迹模块，基于 PD 控制算法
- 8路灰度权重设计：`-40,-20,-10,-5,+5,+10,+20,+40`，对应 bit0(最左)~bit7(最右)
- 黑线检测：`digital` 中 bit=0 表示检测到黑场
- **PD控制公式**：`pwm_out = Kp*error + Kd*(error - last_error)`
  - `error > 0`（偏左）→ 减少左PWM、增加右PWM → 右转修正
  - `error < 0`（偏右）→ 减少右PWM、增加左PWM → 左转修正
- **丢线保护策略**（按 `LINE_TASK_PERIOD_MS=5ms` 周期）：
  - `<150ms`：保持上一周期 error 值，直行碾过
  - `150~500ms`：降速 70%~50% 找线
  - `>600ms`：停车，进入 `APP_STATE_ERROR`
- **默认参数**：`base_pwm=600, Kp=20, Kd=0`（说明书建议起步值）
- **I2C失败保护**：读取失败时立即停车

#### 修改文件

**`app_control.c/h`**
- `App_Control_Init()` 中调用 `App_Line_Init()`
- `App_Control_Task()` 中 `APP_STATE_RUNNING` 时执行 `App_Line_Task()`
- 新增 `App_Control_SetLineParams()` 接口

**`app_debug.c`**
- 新增串口命令：
  - `line <base> <kp> <kd>`：设置循迹参数（例：`line 600 20 0`）
  - `lineq`：查询当前循迹参数
  - `linedbg`：打印灰度digital值、error、状态

**`user_main.c`**
- K2 启动时同时调用 `App_Line_Init()` 重置循迹状态
- K2 支持从 `APP_STATE_ERROR` 恢复重新启动

**`Makefile`**
- 源文件列表加入 `Core/Src/app_line.c`

#### 调参指南（按说明书8.5节）

1. 先只调 `Kp`，每次 `+10`，直到直线轻微摆动
2. 再加 `Kd`，每次 `+10`，直到摆动减小或消除
3. 最后逐步提高 `base_pwm`，不要一开始就追求速度

#### 操作流程

1. 小车架空，按 K2 启动（进入 RUNNING 状态）
2. 灰度传感器读取 digital 值，`bit=0` 为黑场
3. 用 `linedbg` 查看 error 值是否与偏移方向一致
4. 放上赛道低速测试，依次调参

### OLED显示系统开发

- 新增三个 OLED 页面，周期 100ms（`OLED_TASK_PERIOD_MS`），K1 按键循环切换。
- `bsp_oled.h` 新增 `OLED_Page_t` 枚举（`STANDBY/RUN/DEBUG`）、`BSP_OLED_SwitchPage()`、`BSP_OLED_GetPage()`。
- **待机页**（`OLED_PAGE_STANDBY`）：
  - 显示标题、分隔线、当前模式（`IDLE/BASIC/MOTOR/ENC/RUN/ERROR/DONE`）、K2:START/K1:PAGE、BAT:OK。
  - K2 按键从 READY/FINISHED 状态启动系统，进入 RUNNING 并切换到运行页；K4 按键停止运行并返回待机页。
- **运行页**（`OLED_PAGE_RUN`）：
  - L/R 编码器增量、ERR 灰度错误码、左右 PWM 占空比、LOST 丢线状态（二进制+十六进制）。
  - 灰度 8 路模拟量（6x8 小字体，前 4 路第 5 行，后 4 路第 6 行）。
- **调试页**（`OLED_PAGE_DEBUG`）：
  - 左/右编码器累计值、K1-K4 按键实时状态、灰度 I2C ping 状态、固件版本、系统状态。
- `bsp_motor.c` 新增 `BSP_Motor_GetLeftPWM()` / `BSP_Motor_GetRightPWM()`，保存每次 Set 的 pwm 值供 OLED 运行页读取。
- `user_main.c` 主循环接管页面切换逻辑（K1 切页，K2 启动，K4 停止）。
- 编译通过：RAM 10.7%（2188B/20KB），Flash 33.8%（22120B/64KB）。

## 2026-05-08

- 检查并整理 `0_began` STM32 工程结构。
- 修正 `PB4/PB5` 为模拟高阻，避免干扰 I2C 共享网络。
- 将 TIM3/TIM4 编码器滤波统一为 `IC1Filter=8`、`IC2Filter=8`。
- 补齐 STM32 分层软件骨架：`user_main`、`user_config`、BSP 层、APP 层基础文件。
- 让 `main.c` 统一调用 `User_App_Init()` / `User_App_Loop()`。
- 将新源文件加入 `Makefile`，方便后续直接编译。
- 为 PlatformIO 的 ST-LINK 上传加入 `CPUTAPID 0x2ba01477` 兼容参数，处理 OpenOCD 识别码不匹配问题。
- 根据实测 OpenOCD 输出，修正 PlatformIO 上传参数为 `CPUTAPID 0x1ba01477`，避免 `UNEXPECTED idcode`。

- 修正右轮电机接线顺序：交换 `user_config.h` 中 `R_IN1_PIN`（PB15）和 `R_IN2_PIN`（PB14）。原接线导致 K3（PB10）应使右轮正转但实际反转，K4（PB11）应使右轮反转但实际正转。交换后：IN1=PB15，IN2=PB14，与代码中 `pwm>0 -> IN1=HIGH/IN2=LOW`（正转）逻辑一致。

- 新增编码器测试功能：创建 `app_encoder_test.c/.h`，实现 `ENCODER_TEST` 状态。测试电机前进 1.5 秒后，通过串口输出左右轮 delta 值及方向判定（OK/REVERSE/ZERO）。`user_config.h` 中 `LEFT_ENCODER_DIR=-1`、`RIGHT_ENCODER_DIR=1`。实测左轮编码器计数方向与电机方向相反（K1 正转得 -96），改为 `-1` 后左轮 delta 方向与电机方向一致；右轮编码器方向正确，保持 `1`。

- 扩展 `app_debug.c` 串口命令支持 `enc L|R|B`（编码器测试）、`encq`（实时查询）、`motor L|R <pwm>`、`stop`、`state`。`app_state.h` 新增 `APP_STATE_MOTOR_TEST` 和 `APP_STATE_ENCODER_TEST` 两个状态。
- 补齐 `app_debug.c` 中 `App_Debug_Printf` 实现，使用 UART 发送格式化调试输出，解决链接报错。

## 2026-05-09

### 灰度传感器驱动开发

- 完善灰度传感器驱动 `bsp_gray.c`：实现 `BSP_Gray_Ping()`（ping 诊断 0xAA→0x66）、`BSP_Gray_GetDigital()`（数字量 0xDD，返回 8bit，每 bit 对应一路）、`BSP_Gray_GetAllAnalog()`（连续模拟量 0xB0，一次读 8 路）。`user_config.h` 新增 `GRAY_I2C_ADDR=0x4E`（跳线帽 AD1=1 AD0=0，对应写地址 0x9C）。

### 引脚分配修正（重要）

**第一阶段修正（引脚从 PB8/PB9 改到 PA4/PA5）**：
`gscan` 扫到的 0x3C 是 OLED（通过硬件 I2C1 在 PB8/PB9），不是灰度传感器。硬件 I2C1 已占用 PB8/PB9 用于 OLED，软件模拟 I2C 灰度必须使用独立的 PA4/PA5 引脚。修正 `user_config.h` 中灰度引脚为 PA4/PA5。

**第二阶段修正（SCL/SDA 线序）**：
参考工程 `huidu.c` 定义 `SCL_PIN=GPIO_Pin_4, SDA_PIN=GPIO_Pin_5`，但实测正确接法为 **PA5=SCL, PA4=SDA**（当时反接时有 ACK 响应，正接无响应）。可能参考工程用了杜邦线交叉连接。已改正。

### 软件模拟 I2C 架构

- 完全重构 `bsp_gray.c` 为纯软件模拟 I2C，参考 `32循迹3.5` 工程 `huidu.c` 的成熟实现。关键决策：
  - 删除了所有 `HAL_I2C_*` 调用
  - 手动控制 GPIO（开漏输出+上拉输入切换）作为软件 I2C 总线
  - 参考旧项目经验，I2C 延时使用 `Delay_us(150)`（SCL 周期约 600µs，~1.6kHz），与灰度传感器低速内部 MCU 匹配
  - ACK 采样在 SCL 高电平期间，SDA 读取前先释放总线（上拉输入）
  - 标准 I2C 流程：START→addr+write→ACK→cmd→ACK→Repeated START→addr+read→ACK→data→NACK→STOP

### 灰度驱动最终状态（验证通过）

- `gscan` 在地址 0x4E 找到设备，ping 返回 0x66 OK
- `gray` 命令输出完整：`version=0x36`、`error=0x00`、`digital=0xC0`（bit7=1 对应第8路白场，其余黑场）、`analog[1-8]` 正常读取
- **注意**：更换灰度传感器后需要重新验证地址（不同传感器跳线帽配置可能不同）

### 代码整理

- 删除 `app_debug.c` 中冗余调试命令：`gbbscan`、`gready`、`pingraw`、`gaddr`、`i2creset`、`i2c <addr>`
- 删除 `bsp_gray.c` 中不再需要的 `s_last_ack` 调试变量及 `BSP_Gray_GetLastAck()` 函数
- 清理 `_I2C_TryAddr` 保留在 `BSP_Gray_I2CScan()` 中仍被使用

### 最终接线对应

| 总线 | SCL | SDA | 用途 |
|------|------|------|------|
| **灰度传感器**（软件模拟 I2C） | **PA5** | **PA4** | 灰度 8 路传感器 |
| OLED（硬件 I2C1） | PB8 | PB9 | OLED 显示屏 |
