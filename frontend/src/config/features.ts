/**
 * 功能开关 —— 集中管理「产品暂时不对外暴露」的能力。
 *
 * 背景：VidDigest 不做会员制，但后端 VIP 判定与 Stripe 支付代码**保留不删**
 * （is_vip_active / api_payment.py / orders 表），便于将来恢复。
 * 这里只负责关掉用户可见的入口，不动业务逻辑。
 *
 * 恢复会员制时：把 MEMBERSHIP_ENABLED 改回 true 即可，前端入口自动回来。
 * 后端从未被改动，无需同步。
 */
export const MEMBERSHIP_ENABLED = false
