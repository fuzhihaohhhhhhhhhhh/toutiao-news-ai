/**
 * 全局 axios 拦截器
 *
 * 1) 请求拦截：自动带上 `Authorization: Bearer <token>`
 *    —— 各 store 不必再手写请求头（user.js 以前写的是不带 Bearer 的裸 token）
 * 2) 响应拦截：统一处理 **401 登录态失效**
 *    —— 清空本地登录态 → 提示 → 跳转登录页，并记住原页面，登录后跳回来
 *
 * 为什么必须有这个文件：
 *   token 有效期 7 天（crud/users.py::create_token）。过期后，所有需要登录的接口
 *   都会返回 401。此前只有 AIChat.vue 的流式请求（走原生 fetch）处理了 401，
 *   而 axios 发起的请求（新建会话 / 收藏 / 历史 / 个人信息）只会把错误吞成
 *   `{success:false, message}`，**既不清 token 也不跳登录**。
 *   结果：页面仍显示"已登录"，用户反复看到同一句报错却不知道要重新登录，
 *   应用进入"看着没坏、但什么都做不了"的死局。
 */
import axios from 'axios';
import { showToast } from 'vant';

import router from '../router';
import { useUserStore } from '../store/user';

// 这两个接口用 401 表示"用户名或密码错误"，属于业务校验失败，
// 不是登录态失效 —— 必须排除，否则在登录页输错密码会被当成"登录已过期"。
const CREDENTIAL_PATHS = ['/api/user/login', '/api/user/register'];

// ── 请求拦截：统一注入 token ──
axios.interceptors.request.use((config) => {
  const userStore = useUserStore();
  if (userStore.token) {
    config.headers.Authorization = `Bearer ${userStore.token}`;
  }
  return config;
});

// ── 响应拦截：统一处理 401 ──
axios.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status;
    const url = error.config?.url || '';
    const isCredentialRequest = CREDENTIAL_PATHS.some((path) => url.includes(path));

    if (status === 401 && !isCredentialRequest) {
      const userStore = useUserStore();
      // 清掉失效的 token，避免它被持久化后每次刷新又带上来
      userStore.logout();
      showToast('登录已过期，请重新登录');

      const current = router.currentRoute.value;
      if (current.path !== '/login') {
        router.replace({ path: '/login', query: { redirect: current.fullPath } });
      }
    }

    // 继续把错误抛给调用方，保持各 store 原有的 try/catch 逻辑不变
    return Promise.reject(error);
  }
);

export default axios;
