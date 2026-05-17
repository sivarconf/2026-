#include "bsp_gray.h"
#include "main.h"
#include "user_config.h"
#include "stm32f1xx_hal.h"
#include "bsp_delay.h"

/* ============================ 宏定义 ============================ */

/* 灰度传感器命令符（参考手册7.17节） */
#define GRAY_CMD_PING        0xAA    /* ping诊断，返回0x66 */
#define GRAY_RET_PING_OK     0x66    /* ping成功返回值 */
#define GRAY_CMD_DIGITAL     0xDD    /* 读取8路数字量 */
#define GRAY_CMD_ANALOG_ALL  0xB0    /* 连续读取8路模拟量 */
#define GRAY_CMD_ERROR       0xDE    /* 读取错误信息 */
#define GRAY_CMD_VERSION     0xC1    /* 读取固件版本号 */

/* 灰度传感器地址计算（参考手册7.6.1节）
 * 地址组成：高5位S软件地址(默认0b10011) + 2位H硬件地址(AD1/AD0跳线帽)
 * AD1=1, AD0=0 → H=10 → 7bit = 0b1001110 = 0x4E
 * 但gscan实测设备响应地址为0x3C，可能传感器软件地址被改过或扫描偏差
 * gscan扫描时以0x3C作为有效地址继续探测，若不work应尝试手册标准地址0x4E */
#define GRAY_ADDR_7BIT      GRAY_I2C_ADDR          /* 7-bit从机地址，由user_config.h配置 */
#define GRAY_ADDR_WRITE     (uint8_t)(GRAY_ADDR_7BIT << 1)  /* 8bit写地址 */
#define GRAY_ADDR_READ      (uint8_t)((GRAY_ADDR_7BIT << 1) | 0x01) /* 8bit读地址 */

/* I2C软件模拟引脚定义（PA5=SCL, PA4=SDA，与user_config.h保持一致） */
#define GRAY_SCL_PORT       GRAY_SCL_PORT_DEF
#define GRAY_SCL_PIN        GRAY_SCL_PIN_DEF
#define GRAY_SDA_PORT       GRAY_SDA_PORT_DEF
#define GRAY_SDA_PIN        GRAY_SDA_PIN_DEF

/* I2C时序延时（参考32循迹3.5工程huidu.c经验值150us）
 * 灰度传感器内部是低速MCU，时序过快会导致协议状态机错乱
 * 使用DWT->CYCCNT精确微秒延时，72MHz下一个单位=1/72us */
#define I2C_DELAY_150US()   Delay_us(150)
#define I2C_DELAY_100US()   Delay_us(100)
#define I2C_DELAY_50US()    Delay_us(50)
#define I2C_DELAY_20US()    Delay_us(20)
#define I2C_DELAY_10US()    Delay_us(10)
#define I2C_DELAY_1MS()     Delay_ms(1)
#define I2C_DELAY_5MS()     Delay_ms(5)
#define I2C_DELAY_50MS()    Delay_ms(50)
#define I2C_DELAY_100MS()   Delay_ms(100)

/* 重试次数 */
#define GRAY_PING_RETRY_MAX  10
#define GRAY_ADDR_SCAN_MAX   4   /* 最多尝试4个候选地址 */

/* 候选地址列表（参考32循迹3.5工程huidu.c） */
static const uint8_t s_candidate_addrs[GRAY_ADDR_SCAN_MAX] = {
    0x4F,  /* AD1=1 AD0=1 */
    0x4E,  /* AD1=1 AD0=0 (当前跳线帽配置) */
    0x4D,  /* AD1=0 AD0=1 */
    0x4C,  /* AD1=0 AD0=0 (默认) */
};

/* ============================ 私有全局变量 ============================ */

static uint8_t s_active_addr = GRAY_ADDR_7BIT;
static uint8_t s_initialized = 0;

/* ============================ 私有函数声明 ============================ */

static void     _GPIO_Init(void);
static void     _GPIO_SCL_H(void);
static void     _GPIO_SCL_L(void);
static void     _GPIO_SDA_H(void);
static void     _GPIO_SDA_L(void);
static uint8_t  _GPIO_SDA_Read(void);
static void     _GPIO_SDA_Out(void);
static void     _I2C_Start(void);
static void     _I2C_Stop(void);
static void     _I2C_SendByte(uint8_t dat);
static uint8_t  _I2C_RecvByte(void);
static uint8_t  _I2C_WaitAck(void);
static void     _I2C_SendAck(uint8_t ack);
static void     _I2C_ResetBus(void);
static uint8_t  _Ping_WithAddr(uint8_t addr7bit);
static void     _ScanAll_Addrs(void);

/* ============================ GPIO底层操作 ============================ */

/* PA5/PA4初始化为开漏输出 */
static void _GPIO_Init(void)
{
    __HAL_RCC_GPIOA_CLK_ENABLE();

    GPIO_InitTypeDef GPIO_InitStruct = {0};
    GPIO_InitStruct.Mode  = GPIO_MODE_OUTPUT_OD;
    GPIO_InitStruct.Pull  = GPIO_NOPULL;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
    GPIO_InitStruct.Pin   = GRAY_SCL_PIN | GRAY_SDA_PIN;
    HAL_GPIO_Init(GRAY_SDA_PORT, &GPIO_InitStruct);

    HAL_GPIO_WritePin(GRAY_SCL_PORT, GRAY_SCL_PIN, GPIO_PIN_SET);
    HAL_GPIO_WritePin(GRAY_SDA_PORT, GRAY_SDA_PIN, GPIO_PIN_SET);
}

static void _GPIO_SCL_H(void)
{
    HAL_GPIO_WritePin(GRAY_SCL_PORT, GRAY_SCL_PIN, GPIO_PIN_SET);
    I2C_DELAY_150US();
}

static void _GPIO_SCL_L(void)
{
    HAL_GPIO_WritePin(GRAY_SCL_PORT, GRAY_SCL_PIN, GPIO_PIN_RESET);
    I2C_DELAY_150US();
}

static void _GPIO_SDA_H(void)
{
    HAL_GPIO_WritePin(GRAY_SDA_PORT, GRAY_SDA_PIN, GPIO_PIN_SET);
    I2C_DELAY_150US();
}

static void _GPIO_SDA_L(void)
{
    HAL_GPIO_WritePin(GRAY_SDA_PORT, GRAY_SDA_PIN, GPIO_PIN_RESET);
    I2C_DELAY_150US();
}

/* SDA读取：切换到输入模式(上拉)，延时稳定后读取，再切回开漏输出 */
static uint8_t _GPIO_SDA_Read(void)
{
    uint8_t bit;

    /* 切换SDA为输入上拉 */
    GPIO_InitTypeDef GPIO_InitStruct = {0};
    GPIO_InitStruct.Mode  = GPIO_MODE_INPUT;
    GPIO_InitStruct.Pull  = GPIO_PULLUP;     /* 手册要求：主机上拉 */
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
    GPIO_InitStruct.Pin   = GRAY_SDA_PIN;
    HAL_GPIO_Init(GRAY_SDA_PORT, &GPIO_InitStruct);

    I2C_DELAY_20US();
    bit = (HAL_GPIO_ReadPin(GRAY_SDA_PORT, GRAY_SDA_PIN) == GPIO_PIN_SET) ? 1 : 0;
    I2C_DELAY_20US();

    /* 切回开漏输出 */
    GPIO_InitStruct.Mode  = GPIO_MODE_OUTPUT_OD;
    GPIO_InitStruct.Pull  = GPIO_NOPULL;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
    GPIO_InitStruct.Pin   = GRAY_SDA_PIN;
    HAL_GPIO_Init(GRAY_SDA_PORT, &GPIO_InitStruct);

    return bit;
}

static void _GPIO_SDA_Out(void)
{
    GPIO_InitTypeDef GPIO_InitStruct = {0};
    GPIO_InitStruct.Mode  = GPIO_MODE_OUTPUT_OD;
    GPIO_InitStruct.Pull  = GPIO_NOPULL;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
    GPIO_InitStruct.Pin   = GRAY_SDA_PIN;
    HAL_GPIO_Init(GRAY_SDA_PORT, &GPIO_InitStruct);
}

/* ============================ I2C时序 ============================ */

/* I2C START：SDA从高到低时SCL为高 */
static void _I2C_Start(void)
{
    _GPIO_SDA_H();
    _GPIO_SCL_H();
    I2C_DELAY_150US();
    _GPIO_SDA_L();
    I2C_DELAY_150US();
    _GPIO_SCL_L();
}

/* I2C STOP：SCL为低时SDA从低到高 */
static void _I2C_Stop(void)
{
    _GPIO_SDA_L();
    _GPIO_SCL_L();
    I2C_DELAY_150US();
    _GPIO_SCL_H();
    I2C_DELAY_150US();
    _GPIO_SDA_H();
    I2C_DELAY_150US();
}

/* 发送一个字节（高位在前） */
static void _I2C_SendByte(uint8_t dat)
{
    _GPIO_SDA_Out();

    for (uint8_t i = 0; i < 8; i++) {
        _GPIO_SCL_L();
        I2C_DELAY_50US();

        if (dat & 0x80)
            _GPIO_SDA_H();
        else
            _GPIO_SDA_L();
        I2C_DELAY_50US();

        _GPIO_SCL_H();
        I2C_DELAY_150US();
        _GPIO_SCL_L();
        I2C_DELAY_50US();

        dat <<= 1;
    }
}

/* 接收一个字节（高位在前） */
static uint8_t _I2C_RecvByte(void)
{
    uint8_t dat = 0;

    _GPIO_SDA_H();
    I2C_DELAY_50US();

    for (uint8_t i = 0; i < 8; i++) {
        _GPIO_SCL_L();
        I2C_DELAY_150US();
        _GPIO_SCL_H();
        I2C_DELAY_50US();

        dat <<= 1;
        if (_GPIO_SDA_Read())
            dat |= 0x01;

        I2C_DELAY_100US();
    }

    _GPIO_SCL_L();
    I2C_DELAY_50US();

    return dat;
}

/* 接收ACK：SCL高电平期间采样SDA，0=ACK(从机拉低), 1=NACK */
static uint8_t _I2C_WaitAck(void)
{
    uint8_t ack;

    _GPIO_SDA_H();
    I2C_DELAY_50US();
    _GPIO_SCL_H();
    I2C_DELAY_50US();

    ack = _GPIO_SDA_Read();

    _GPIO_SCL_L();
    I2C_DELAY_50US();

    return ack;
}

/* 发送ACK：SCL高电平后从机采样SDA */
static void _I2C_SendAck(uint8_t ack)
{
    if (ack)
        _GPIO_SDA_H();
    else
        _GPIO_SDA_L();
    I2C_DELAY_50US();

    _GPIO_SCL_H();
    I2C_DELAY_150US();
    _GPIO_SCL_L();
    I2C_DELAY_50US();
}

/* 复位I2C总线：发送9个SCL时钟脉冲释放被锁死的从机 */
static void _I2C_ResetBus(void)
{
    _GPIO_SDA_H();
    _GPIO_SCL_H();

    for (uint8_t i = 0; i < 9; i++) {
        _GPIO_SCL_L();
        I2C_DELAY_150US();
        _GPIO_SCL_H();
        I2C_DELAY_150US();
    }

    _I2C_Stop();
    I2C_DELAY_50MS();
}

/* ============================ 地址探测 ============================ */

/* 尝试单个地址是否有ACK响应 */
static uint8_t _I2C_TryAddr(uint8_t addr7bit)
{
    _I2C_Start();
    _I2C_SendByte((uint8_t)(addr7bit << 1));

    if (_I2C_WaitAck() == 0) {
        _I2C_Stop();
        return 1;
    }

    _I2C_Stop();
    return 0;
}

/* 使用指定地址执行ping命令（写0xAA + Repeated START + 读返回） */
static uint8_t _Ping_WithAddr(uint8_t addr7bit)
{
    uint8_t retry;
    uint8_t response;

    for (retry = 0; retry < 3; retry++) {
        _I2C_Start();
        _I2C_SendByte((uint8_t)(addr7bit << 1));  /* 写 */

        if (_I2C_WaitAck() != 0) {
            _I2C_Stop();
            I2C_DELAY_5MS();
            continue;
        }

        _I2C_SendByte(GRAY_CMD_PING);

        if (_I2C_WaitAck() != 0) {
            _I2C_Stop();
            I2C_DELAY_5MS();
            continue;
        }

        _I2C_Stop();
        I2C_DELAY_5MS();

        /* Repeated START，切换为读 */
        _I2C_Start();
        _I2C_SendByte((uint8_t)((addr7bit << 1) | 0x01));  /* 读 */

        if (_I2C_WaitAck() != 0) {
            _I2C_Stop();
            I2C_DELAY_5MS();
            continue;
        }

        response = _I2C_RecvByte();
        _I2C_SendAck(1);  /* 发送NACK */
        _I2C_Stop();

        if (response == GRAY_RET_PING_OK)
            return 1;
    }

    return 0;
}

/* 扫描所有候选地址，找到有效设备 */
static void _ScanAll_Addrs(void)
{
    uint8_t i;

    /* 按候选地址顺序探测 */
    for (i = 0; i < GRAY_ADDR_SCAN_MAX; i++) {
        if (_Ping_WithAddr(s_candidate_addrs[i])) {
            s_active_addr = s_candidate_addrs[i];
            return;
        }
    }
}

/* ============================ 公共API实现 ============================ */

/* 获取当前活动地址（调试用） */
uint8_t BSP_Gray_GetActiveAddr(void)
{
    return s_active_addr;
}

/* 初始化灰度传感器：
 * 1. 初始化GPIO为开漏
 * 2. 复位I2C总线
 * 3. 等待传感器上电完成
 * 4. 探测有效I2C地址 */
void BSP_Gray_Init(void)
{
    if (s_initialized)
        return;

    _GPIO_Init();

    /* 发送18个SCL时钟脉冲复位总线（参考32循迹3.5工程经验值） */
    for (uint8_t i = 0; i < 18; i++) {
        _GPIO_SCL_L();
        I2C_DELAY_150US();
        _GPIO_SCL_H();
        I2C_DELAY_150US();
    }

    _I2C_Stop();
    I2C_DELAY_100MS();

    /* 等待传感器初始化完成（参考手册：传感器上电到正常工作需要时间） */
    for (uint8_t retry = 0; retry < 20; retry++) {
        _ScanAll_Addrs();
        if (s_active_addr != 0)
            break;
        I2C_DELAY_50MS();
    }

    s_initialized = 1;
}

/* 等待传感器就绪（ping超时等待） */
uint8_t BSP_Gray_WaitReady(uint16_t timeout_ms)
{
    uint32_t start = HAL_GetTick();

    while ((HAL_GetTick() - start) < timeout_ms) {
        if (BSP_Gray_Ping())
            return 1;
        I2C_DELAY_5MS();
    }

    return 0;
}

/* Ping诊断：向传感器发送0xAA，应返回0x66 */
uint8_t BSP_Gray_Ping(void)
{
    uint8_t retry;

    if (!s_initialized)
        BSP_Gray_Init();

    for (retry = 0; retry < GRAY_PING_RETRY_MAX; retry++) {
        if (_Ping_WithAddr(s_active_addr))
            return 1;
        I2C_DELAY_5MS();
    }

    return 0;
}

/* 读取8路数字量（参考手册7.7节）
 * 返回值：0=成功，1=失败
 * 成功时*digital中为8bit数据，每bit对应一路
 * bit0=第1路，bit1=第2路，...，bit7=第8路 */
uint8_t BSP_Gray_GetDigital(uint8_t *digital)
{
    if (!s_initialized)
        BSP_Gray_Init();

    if (digital == NULL)
        return 1;

    /* 方法1（标准命令+数据）：START→写地址→0xDD→Repeated START→读地址→数据→STOP */
    _I2C_Start();
    _I2C_SendByte((uint8_t)(s_active_addr << 1));  /* 写 */
    if (_I2C_WaitAck() != 0) {
        _I2C_Stop();
        return 1;
    }

    _I2C_SendByte(GRAY_CMD_DIGITAL);
    if (_I2C_WaitAck() != 0) {
        _I2C_Stop();
        return 1;
    }

    _I2C_Stop();
    I2C_DELAY_5MS();

    /* Repeated START，切换为读 */
    _I2C_Start();
    _I2C_SendByte((uint8_t)((s_active_addr << 1) | 0x01));  /* 读 */
    if (_I2C_WaitAck() != 0) {
        _I2C_Stop();
        return 1;
    }

    *digital = _I2C_RecvByte();
    _I2C_SendAck(1);  /* NACK表示结束 */
    _I2C_Stop();

    return 0;
}

/* 连续读取8路模拟量（参考手册7.9节）
 * 返回值：0=成功，1=失败
 * buf[0]=第1路，buf[1]=第2路，...，buf[7]=第8路 */
uint8_t BSP_Gray_GetAllAnalog(uint8_t *buf)
{
    if (!s_initialized)
        BSP_Gray_Init();

    if (buf == NULL)
        return 1;

    _I2C_Start();
    _I2C_SendByte((uint8_t)(s_active_addr << 1));  /* 写 */
    if (_I2C_WaitAck() != 0) {
        _I2C_Stop();
        return 1;
    }

    _I2C_SendByte(GRAY_CMD_ANALOG_ALL);
    if (_I2C_WaitAck() != 0) {
        _I2C_Stop();
        return 1;
    }

    _I2C_Stop();
    I2C_DELAY_5MS();

    /* Repeated START */
    _I2C_Start();
    _I2C_SendByte((uint8_t)((s_active_addr << 1) | 0x01));  /* 读 */
    if (_I2C_WaitAck() != 0) {
        _I2C_Stop();
        return 1;
    }

    for (uint8_t i = 0; i < 8; i++) {
        buf[i] = _I2C_RecvByte();
        _I2C_SendAck((i + 1U) < 8U);  /* 前7个ACK，最后NACK */
    }

    _I2C_Stop();

    return 0;
}

/* 读取错误信息（参考手册7.14节） */
uint8_t BSP_Gray_GetError(uint8_t *error)
{
    if (!s_initialized)
        BSP_Gray_Init();

    if (error == NULL)
        return 1;

    _I2C_Start();
    _I2C_SendByte((uint8_t)(s_active_addr << 1));
    if (_I2C_WaitAck() != 0) {
        _I2C_Stop();
        return 1;
    }

    _I2C_SendByte(GRAY_CMD_ERROR);
    if (_I2C_WaitAck() != 0) {
        _I2C_Stop();
        return 1;
    }

    _I2C_Stop();
    I2C_DELAY_5MS();

    _I2C_Start();
    _I2C_SendByte((uint8_t)((s_active_addr << 1) | 0x01));
    if (_I2C_WaitAck() != 0) {
        _I2C_Stop();
        return 1;
    }

    *error = _I2C_RecvByte();
    _I2C_SendAck(1);
    _I2C_Stop();

    return 0;
}

/* 读取固件版本号（参考手册7.16节） */
uint8_t BSP_Gray_GetVersion(uint8_t *version)
{
    if (!s_initialized)
        BSP_Gray_Init();

    if (version == NULL)
        return 1;

    _I2C_Start();
    _I2C_SendByte((uint8_t)(s_active_addr << 1));
    if (_I2C_WaitAck() != 0) {
        _I2C_Stop();
        return 1;
    }

    _I2C_SendByte(GRAY_CMD_VERSION);
    if (_I2C_WaitAck() != 0) {
        _I2C_Stop();
        return 1;
    }

    _I2C_Stop();
    I2C_DELAY_5MS();

    _I2C_Start();
    _I2C_SendByte((uint8_t)((s_active_addr << 1) | 0x01));
    if (_I2C_WaitAck() != 0) {
        _I2C_Stop();
        return 1;
    }

    *version = _I2C_RecvByte();
    _I2C_SendAck(1);
    _I2C_Stop();

    return 0;
}

/* I2C总线扫描：遍历0x01~0x7F，找所有ACK响应的设备 */
uint8_t BSP_Gray_I2CScan(uint8_t *addr_buf, uint8_t max_count)
{
    uint8_t count = 0;

    if (!s_initialized)
        _GPIO_Init();

    for (uint8_t addr = 1; addr < 128; addr++) {
        if (_I2C_TryAddr(addr)) {
            if (addr_buf != NULL && count < max_count)
                addr_buf[count] = addr;
            count++;
        }
        I2C_DELAY_1MS();
    }

    return count;
}

/* 重置I2C总线（对外接口） */
uint8_t BSP_Gray_ResetBus(void)
{
    if (!s_initialized)
        _GPIO_Init();

    _I2C_ResetBus();
    return 0;
}

/* BitBang扫描接口（多总线支持，为兼容旧接口） */
uint8_t BSP_Gray_BitBangScan(uint8_t bus, uint8_t *addr_buf, uint8_t max_count)
{
    (void)bus;
    return BSP_Gray_I2CScan(addr_buf, max_count);
}

/* BitBang Ping接口（多总线支持，为兼容旧接口） */
uint8_t BSP_Gray_BitBangPing(uint8_t bus, uint8_t addr7bit, uint8_t *ret)
{
    (void)bus;

    if (ret == NULL)
        return 1;

    if (!s_initialized)
        _GPIO_Init();

    /* 临时切换到指定地址ping */
    uint8_t saved_addr = s_active_addr;
    s_active_addr = addr7bit;
    *ret = _Ping_WithAddr(addr7bit) ? GRAY_RET_PING_OK : 0;
    s_active_addr = saved_addr;

    return (*ret == GRAY_RET_PING_OK) ? 0 : 1;
}

/* 读取指定bit（第position路的状态，0~7）
 * grayValue: 数字量原始值
 * position:  0=第1路, 1=第2路, ..., 7=第8路
 * 返回值: 1=白, 0=黑（参考手册7.7.1节输出逻辑）
 * 注意：此函数从数字量的角度读取，与LineTracking.c中黑=0的约定一致 */
uint8_t BSP_Gray_GetBit(uint8_t grayValue, uint8_t position)
{
    if (position > 7)
        position = 7;

    return (grayValue & (1 << position)) ? 1 : 0;
}
