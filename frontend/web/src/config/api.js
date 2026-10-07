/**
 * API配置文件
 * 包含API基础URL配置
 *
 * 说明：
 * - AI 聊天已改为走后端智能体接口（/api/chat/*），模型调用全部在服务端完成
 * - 前端不再需要任何 AI 供应商的 API Key（密钥只保存在后端 .env）
 */

// API基础URL配置
export const apiConfig = {
  // 后端API基础URL
  baseURL: 'http://127.0.0.1:8000',
}
