<template>
  <div class="ai-chat-container">
    <van-nav-bar title="AI问答" fixed>
      <template #left>
        <van-icon name="wap-nav" size="20" @click="showSessions = true" />
      </template>
      <template #right>
        <van-icon name="plus" size="20" @click="handleCreateSession" />
      </template>
    </van-nav-bar>

    <!-- 会话列表侧边栏 -->
    <van-popup
      v-model:show="showSessions"
      position="left"
      :style="{ width: '75%', height: '100%' }"
    >
      <div class="session-panel">
        <div class="session-header">
          <span>对话列表</span>
          <van-button size="small" type="primary" icon="plus" @click="handleCreateSession">
            新对话
          </van-button>
        </div>
        <div class="session-list" v-if="sessions.length">
          <div
            v-for="s in sessions"
            :key="s.id"
            :class="['session-item', s.id === currentSessionId ? 'session-active' : '']"
            @click="switchSession(s.id)"
          >
            <van-icon name="chat-o" class="session-icon" />
            <div class="session-info">
              <div class="session-title">{{ s.title }}</div>
              <div class="session-time">{{ s.updatedAt }}</div>
            </div>
            <van-icon
              name="delete-o"
              class="session-delete"
              @click.stop="confirmDeleteSession(s.id)"
            />
          </div>
        </div>
        <van-empty v-else description="暂无对话，点击右上角 + 新建" />
      </div>
    </van-popup>

    <div class="chat-content">
      <div class="messages-container" ref="messagesContainer">
        <div
          v-for="(message, index) in messages"
          :key="index"
          :class="['message', message.role === 'user' ? 'user-message' : 'ai-message']"
        >
          <div class="message-content">
            <div v-if="message.role === 'assistant' && message.content === ''" class="typing-indicator">
              <span></span>
              <span></span>
              <span></span>
            </div>
            <div v-else v-html="formatMessage(message.content)"></div>
          </div>
        </div>
      </div>

      <div class="input-container">
        <van-field
          v-model="userInput"
          rows="1"
          autosize
          type="textarea"
          placeholder="问我新闻资讯、或让我生成阅读报告..."
          class="chat-input"
          @keypress.enter.prevent="sendMessage"
        />
        <van-button
          type="primary"
          class="send-button"
          :disabled="isLoading || !userInput.trim()"
          @click="sendMessage"
        >
          发送
        </van-button>
      </div>
    </div>

    <tab-bar />
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick, watch } from 'vue';
import { useRouter } from 'vue-router';
import TabBar from '../components/TabBar.vue';
import { showToast, showConfirmDialog } from 'vant';
import * as marked from 'marked';
import DOMPurify from 'dompurify';
import { apiConfig } from '../config/api';
import { useUserStore } from '../store/user';
import { useChatStore } from '../store/modules/chat';

const router = useRouter();
const userStore = useUserStore();
const chatStore = useChatStore();

// 聊天消息（进入页面后会从后端加载当前会话的历史记录）
const messages = ref([]);
const userInput = ref('');
const messagesContainer = ref(null);
const isLoading = ref(false);

// 会话管理
const sessions = ref([]);
const currentSessionId = ref(null);
const showSessions = ref(false);

// 默认欢迎语
const WELCOME = '你好！我是新闻助手小闻，可以问我今天有什么新闻，也可以让我根据你的浏览记录生成月度阅读报告～';

// 格式化消息内容（支持Markdown）
const formatMessage = (content) => {
  if (!content) return '';
  // 使用marked解析Markdown，并用DOMPurify清理HTML
  return DOMPurify.sanitize(marked.parse(content));
};

// ==================== 会话管理 ====================

// 加载会话列表
const loadSessions = async () => {
  const res = await chatStore.fetchSessions();
  if (res.success) {
    sessions.value = chatStore.sessions;
  } else {
    showToast(res.message);
  }
};

// 新建会话
const handleCreateSession = async () => {
  if (isLoading.value) return;
  const res = await chatStore.createSession();
  if (res.success) {
    currentSessionId.value = res.sessionId;
    sessions.value = chatStore.sessions;
    messages.value = [{ role: 'assistant', content: WELCOME }];
    showSessions.value = false;
    showToast('新建对话成功');
  } else {
    showToast(res.message);
  }
};

// 切换会话（加载该会话历史消息）
const switchSession = async (sessionId) => {
  if (sessionId === currentSessionId.value) {
    showSessions.value = false;
    return;
  }
  const res = await chatStore.fetchMessages(sessionId);
  if (res.success) {
    currentSessionId.value = sessionId;
    messages.value = res.list.length
      ? res.list
      : [{ role: 'assistant', content: WELCOME }];
    showSessions.value = false;
  } else {
    showToast(res.message);
  }
};

// 删除会话（二次确认）
const confirmDeleteSession = async (sessionId) => {
  try {
    await showConfirmDialog({
      title: '删除对话',
      message: '删除后该对话的所有聊天记录将无法恢复，确定删除吗？',
    });
  } catch {
    return; // 用户取消
  }
  const res = await chatStore.deleteSession(sessionId);
  if (res.success) {
    sessions.value = chatStore.sessions;
    showToast('删除成功');
    // 删的是当前会话 → 自动切到列表第一个，没有则显示欢迎语
    if (sessionId === currentSessionId.value) {
      currentSessionId.value = null;
      if (sessions.value.length) {
        await switchSession(sessions.value[0].id);
      } else {
        messages.value = [{ role: 'assistant', content: WELCOME }];
      }
    }
  } else {
    showToast(res.message);
  }
};

// ==================== 发送消息（SSE 流式）====================

const sendMessage = async () => {
  if (!userInput.value.trim() || isLoading.value) return;

  // 未登录拦截
  if (!userStore.token) {
    showToast('请先登录');
    router.push('/login');
    return;
  }

  // 没有当前会话时，自动创建一个
  if (!currentSessionId.value) {
    const res = await chatStore.createSession();
    if (!res.success) {
      showToast(res.message);
      return;
    }
    currentSessionId.value = res.sessionId;
    sessions.value = chatStore.sessions;
  }

  // 添加用户消息 + AI 消息占位（打字指示器）
  const userMessage = userInput.value.trim();
  messages.value.push({ role: 'user', content: userMessage });
  userInput.value = '';
  messages.value.push({ role: 'assistant', content: '' });

  await nextTick();
  scrollToBottom();

  isLoading.value = true;
  try {
    await fetchAIResponse(userMessage);
  } catch (error) {
    console.error('发送消息失败:', error);
    // 更新最后一条 AI 消息为错误信息
    messages.value[messages.value.length - 1].content = `出错了：${error.message || '请检查网络连接'}`;
  } finally {
    isLoading.value = false;
    await nextTick();
    scrollToBottom();
  }
};

// 调用后端 SSE 流式接口，逐 token 渲染 AI 回复
const fetchAIResponse = async (userMessage) => {
  const response = await fetch(`${apiConfig.baseURL}/api/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${userStore.token}`
    },
    body: JSON.stringify({
      sessionId: currentSessionId.value,
      message: userMessage
    })
  });

  // HTTP 层错误（401 未登录 / 404 会话不存在 / 429 限流等）
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    if (response.status === 401) {
      showToast('登录已过期，请重新登录');
      userStore.logout();
      router.push('/login');
      throw new Error('登录已过期');
    }
    throw new Error(err.detail || `请求失败 (${response.status})`);
  }

  // 解析 SSE 流：事件以空行（\n\n）分隔，格式 data: {"type":...}
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let aiResponse = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split('\n\n');
    buffer = events.pop() || ''; // 最后一段可能不完整，留到下次

    for (const event of events) {
      const line = event.trim();
      if (!line.startsWith('data: ')) continue;
      try {
        const evt = JSON.parse(line.slice(6));
        if (evt.type === 'content') {
          aiResponse += evt.content;
          // 实时更新最后一条 AI 消息
          messages.value[messages.value.length - 1].content = aiResponse;
          await nextTick();
          scrollToBottom();
        } else if (evt.type === 'error') {
          aiResponse += `\n\n⚠️ ${evt.message}`;
          messages.value[messages.value.length - 1].content = aiResponse;
        }
        // done 事件：流自然结束，无需处理
      } catch (e) {
        console.error('解析 SSE 数据失败:', e);
      }
    }
  }

  // 流结束但没有任何内容
  if (!aiResponse) {
    messages.value[messages.value.length - 1].content = '抱歉，我暂时无法生成回复，请稍后再试。';
  }
};

// ==================== 工具函数 ====================

// 滚动到底部
const scrollToBottom = () => {
  if (messagesContainer.value) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
  }
};

// 监听消息变化，自动滚动
watch(messages, () => {
  nextTick(scrollToBottom);
}, { deep: true });

// 组件挂载：检查登录 → 加载会话列表 → 默认进入最近的会话
onMounted(async () => {
  scrollToBottom();
  if (!userStore.token) {
    messages.value = [{ role: 'assistant', content: '请先登录后再使用 AI 问答功能～' }];
    return;
  }
  await loadSessions();
  if (sessions.value.length) {
    // 会话列表按最近活跃倒序，默认进入第一个
    await switchSession(sessions.value[0].id);
  } else {
    // 首次使用，静默创建第一个会话
    const res = await chatStore.createSession();
    if (res.success) {
      currentSessionId.value = res.sessionId;
      sessions.value = chatStore.sessions;
    }
    messages.value = [{ role: 'assistant', content: WELCOME }];
  }
});
</script>

<style scoped>
.ai-chat-container {
  display: flex;
  flex-direction: column;
  height: 100vh;
  padding-top: 46px;
  padding-bottom: 50px;
  box-sizing: border-box;
}

.chat-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 10px;
}

.message {
  margin-bottom: 10px;
  max-width: 80%;
}

.user-message {
  margin-left: auto;
}

.ai-message {
  margin-right: auto;
}

.message-content {
  padding: 10px;
  border-radius: 10px;
  word-break: break-word;
}

.user-message .message-content {
  background-color: #007aff;
  color: white;
}

.ai-message .message-content {
  background-color: #f2f2f2;
  color: #333;
}

.input-container {
  display: flex;
  padding: 10px;
  border-top: 1px solid #eee;
  background-color: #fff;
}

.chat-input {
  flex: 1;
  margin-right: 10px;
}

.send-button {
  align-self: flex-end;
}

/* ==================== 会话侧边栏 ==================== */
.session-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: #f7f8fa;
}

.session-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 12px;
  font-size: 16px;
  font-weight: bold;
  background-color: #fff;
  border-bottom: 1px solid #eee;
}

.session-list {
  flex: 1;
  overflow-y: auto;
}

.session-item {
  display: flex;
  align-items: center;
  padding: 12px;
  background-color: #fff;
  border-bottom: 1px solid #f0f0f0;
  cursor: pointer;
}

.session-item.session-active {
  background-color: #e8f1ff;
}

.session-icon {
  font-size: 20px;
  margin-right: 10px;
  color: #1989fa;
}

.session-info {
  flex: 1;
  min-width: 0;
}

.session-title {
  font-size: 14px;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-time {
  font-size: 12px;
  color: #999;
  margin-top: 2px;
}

.session-delete {
  font-size: 18px;
  color: #c8c9cc;
  padding: 6px;
  margin-left: 8px;
}

.session-delete:active {
  color: #ee0a24;
}

/* Markdown 样式 */
.message-content pre {
  background-color: #f8f8f8;
  padding: 10px;
  border-radius: 5px;
  overflow-x: auto;
}

.message-content code {
  background-color: rgba(0, 0, 0, 0.05);
  padding: 2px 4px;
  border-radius: 3px;
}

.message-content img {
  max-width: 100%;
}

/* 打字指示器 */
.typing-indicator {
  display: flex;
  padding: 5px;
}

.typing-indicator span {
  height: 8px;
  width: 8px;
  background-color: #999;
  border-radius: 50%;
  margin: 0 2px;
  display: inline-block;
  animation: bounce 1.5s infinite ease-in-out;
}

.typing-indicator span:nth-child(2) {
  animation-delay: 0.2s;
}

.typing-indicator span:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes bounce {
  0%, 60%, 100% {
    transform: translateY(0);
  }
  30% {
    transform: translateY(-5px);
  }
}

/* Markdown样式 */
:deep(pre) {
  background-color: #f0f0f0;
  padding: 10px;
  border-radius: 4px;
  overflow-x: auto;
}

:deep(code) {
  font-family: monospace;
  background-color: #f0f0f0;
  padding: 2px 4px;
  border-radius: 4px;
}

:deep(p) {
  margin: 8px 0;
}

:deep(ul), :deep(ol) {
  padding-left: 20px;
}

:deep(a) {
  color: #1989fa;
  text-decoration: none;
}
</style>
