/**
 * AI 聊天会话 store
 *
 * 封装会话管理接口（新建/列表/删除/历史消息），
 * SSE 流式发消息因需要 fetch 逐段解析，放在 AIChat.vue 组件内处理。
 *
 * 后端响应结构：
 * - 成功：{ code: 200, message, data }
 * - 失败（HTTPException）：{ detail: "错误信息" }
 */
import { defineStore } from 'pinia';
import axios from 'axios';
import { apiConfig } from '../../config/api';
import { useUserStore } from '../user';

export const useChatStore = defineStore('chat', {
  state: () => ({
    sessions: [],          // 会话列表 [{id, title, createdAt, updatedAt}]
    currentSessionId: null // 当前会话ID
  }),

  getters: {
    getSessions: (state) => state.sessions,
    getCurrentSessionId: (state) => state.currentSessionId
  },

  actions: {
    // 统一构造鉴权请求头
    authHeaders() {
      const userStore = useUserStore();
      return { Authorization: `Bearer ${userStore.token}` };
    },

    // 统一提取错误信息（兼容 HTTPException 的 detail 和普通 message）
    _errMsg(error, fallback) {
      return error.response?.data?.detail
        || error.response?.data?.message
        || fallback;
    },

    // 新建会话（成功后自动设为当前会话并刷新列表）
    async createSession() {
      try {
        const response = await axios.post(
          `${apiConfig.baseURL}/api/chat/session`,
          {},
          { headers: this.authHeaders() }
        );
        if (response.data && response.data.code === 200) {
          const { sessionId, title } = response.data.data;
          this.currentSessionId = sessionId;
          await this.fetchSessions();
          return { success: true, sessionId, title };
        }
        return { success: false, message: response.data.message || '新建对话失败' };
      } catch (error) {
        console.error('新建对话失败:', error);
        return { success: false, message: this._errMsg(error, '新建对话失败，请稍后再试') };
      }
    },

    // 获取会话列表（按最近活跃倒序）
    async fetchSessions() {
      try {
        const response = await axios.get(
          `${apiConfig.baseURL}/api/chat/sessions`,
          { headers: this.authHeaders() }
        );
        if (response.data && response.data.code === 200) {
          this.sessions = response.data.data.list || [];
          return { success: true };
        }
        return { success: false, message: response.data.message || '获取对话列表失败' };
      } catch (error) {
        console.error('获取对话列表失败:', error);
        return { success: false, message: this._errMsg(error, '获取对话列表失败') };
      }
    },

    // 删除会话（后端会同时删数据库 + 缓存）
    async deleteSession(sessionId) {
      try {
        const response = await axios.delete(
          `${apiConfig.baseURL}/api/chat/session/${sessionId}`,
          { headers: this.authHeaders() }
        );
        if (response.data && response.data.code === 200) {
          // 本地同步：从列表移除，若删的是当前会话则清空当前会话
          this.sessions = this.sessions.filter(s => s.id !== sessionId);
          if (this.currentSessionId === sessionId) {
            this.currentSessionId = null;
          }
          return { success: true };
        }
        return { success: false, message: response.data.message || '删除对话失败' };
      } catch (error) {
        console.error('删除对话失败:', error);
        return { success: false, message: this._errMsg(error, '删除对话失败，请稍后再试') };
      }
    },

    // 获取某会话的历史消息 [{role, content}]
    async fetchMessages(sessionId) {
      try {
        const response = await axios.get(
          `${apiConfig.baseURL}/api/chat/session/${sessionId}/messages`,
          { headers: this.authHeaders() }
        );
        if (response.data && response.data.code === 200) {
          return { success: true, list: response.data.data.list || [] };
        }
        return { success: false, message: response.data.message || '获取历史消息失败' };
      } catch (error) {
        console.error('获取历史消息失败:', error);
        return { success: false, message: this._errMsg(error, '获取历史消息失败') };
      }
    }
  }
});
