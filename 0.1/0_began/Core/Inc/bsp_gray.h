#ifndef __BSP_GRAY_H
#define __BSP_GRAY_H

#include <stdint.h>

/* ============================ 初始化 ============================ */

/* 初始化灰度传感器I2C通信
 * 执行GPIO初始化、总线复位、地址探测
 * 必须在首次使用灰度功能前调用 */
void BSP_Gray_Init(void);

/* 等待传感器就绪（内部会反复Ping）
 * timeout_ms: 超时时间（毫秒）
 * 返回: 1=就绪, 0=超时 */
uint8_t BSP_Gray_WaitReady(uint16_t timeout_ms);

/* ============================ 核心读取 ============================ */

/* 读取8路灰度数字量（最常用）
 * digital: 指向返回数据的指针，成功时存储8bit结果
 *   bit0 = 第1路, bit1 = 第2路, ..., bit7 = 第8路
 *   1 = 检测到白场, 0 = 检测到黑场
 * 返回: 0=成功, 1=失败 */
uint8_t BSP_Gray_GetDigital(uint8_t *digital);

/* 连续读取8路模拟量
 * buf: 指向8字节缓冲区的指针
 *   buf[0]=第1路, buf[1]=第2路, ..., buf[7]=第8路
 * 返回: 0=成功, 1=失败 */
uint8_t BSP_Gray_GetAllAnalog(uint8_t *buf);

/* 获取指定bit（第position路的状态）
 * grayValue: 数字量原始值
 * position:  0~7（0=第1路，7=第8路）
 * 返回: 1=白场, 0=黑场 */
uint8_t BSP_Gray_GetBit(uint8_t grayValue, uint8_t position);

/* ============================ 诊断功能 ============================ */

/* Ping诊断：发送0xAA，应返回0x66
 * 返回: 1=Ping成功（设备在线）, 0=失败 */
uint8_t BSP_Gray_Ping(void);

/* 读取错误信息（参考手册7.14节）
 * error: 指向返回数据的指针
 *   bit0=对管过曝, bit1=按键短路, bit2~7=保留
 * 返回: 0=成功, 1=失败 */
uint8_t BSP_Gray_GetError(uint8_t *error);

/* 读取固件版本号（参考手册7.16节）
 * version: 指向返回数据的指针
 * 返回: 0=成功, 1=失败 */
uint8_t BSP_Gray_GetVersion(uint8_t *version);

/* ============================ 总线操作 ============================ */

/* 扫描I2C总线，查找所有设备地址
 * addr_buf: 存储地址的缓冲区（传NULL可只计数）
 * max_count: 最大存储数量
 * 返回: 发现的设备数量 */
uint8_t BSP_Gray_I2CScan(uint8_t *addr_buf, uint8_t max_count);

/* 复位I2C总线（解除锁死状态）
 * 发送9个SCL时钟脉冲强制释放从机
 * 返回: 0 */
uint8_t BSP_Gray_ResetBus(void);

/* ============================ 调试辅助 ============================ */

/* 获取当前活动地址 */
uint8_t BSP_Gray_GetActiveAddr(void);

/* ============================ BitBang兼容接口 ============================ */

/* 软件I2C扫描（bus参数为扩展预留） */
uint8_t BSP_Gray_BitBangScan(uint8_t bus, uint8_t *addr_buf, uint8_t max_count);

/* 软件I2C Ping指定地址
 * bus: 总线号（预留，当前只用默认总线）
 * addr: 7bit I2C地址
 * ret: 指向返回值的指针（成功=0x66）
 * 返回: 0=成功, 1=失败 */
uint8_t BSP_Gray_BitBangPing(uint8_t bus, uint8_t addr, uint8_t *ret);

#endif
