# AegisStay 界面交互、黑夜模式与生命周期规范 (ui_and_interaction_spec.md)

> 适用范围：历史专题设计／说明，保留当时描述，不作为当前发布规范或已实现能力证明。当前要求见 [主规格](specification.md)、[验收标准](acceptance_criteria.md)；适用范围见 [历史索引](legacy_specs.md)。

## 1. Streamlit 执行生命周期与对话延迟根治规范

### 1.1 缺陷复盘：为什么“输入第一句不显示，输入 1128 才显示上一句”？
- **Streamlit 单向自顶向下执行流**：
  Streamlit 脚本每次用户交互都是从第 1 行执行至最后一行。
  在原有代码中：
  ```python
  chat = st.container(height=440)
  with chat:
      for message in state.messages:   # [步骤 1] 此时渲染历史消息列表
          st.write(message["content"])

  prompt = st.chat_input("...")        # [步骤 2] 获取用户新输入
  if prompt:
      orchestrator.process(prompt)     # [步骤 3] 追加了新消息，但步骤 1 早已执行完毕！
  ```
  **直接后果**：住客在当前帧输入的内容以及 Agent 生成的新答复，虽然已写入 `state.messages`，但本帧页面在步骤 3 之前就已经画完了，因此**当前对话框是一片空白**。只有等到用户下一次输入 `1128` 时，脚本重新从第 1 行跑到第 35 行，上一轮的内容才会“姗姗来迟”地显示出来。

### 1.2 强制修复标准：即时重跑（Immediate Rerun）
所有前端输入事件在完成状态机处理后，**必须强制显式触发 `st.rerun()`**：
```python
prompt = st.chat_input("例如：1128 能两点退房吗？")
if prompt:
    orchestrator.process(prompt)
    st.rerun()  # 立即重新跑一次脚本，保证当前轮问答 0 延迟立即可见！
```

---

## 2. 黑夜模式（Dark Mode）与日间模式双主题适配规范

### 2.1 缺陷复盘：为什么黑夜模式下 Tab 和文字会“字体隐身”？
在原样式中，硬编码了全局浅色背景：
```css
.stApp { background: #f4f7f5; color: #102a2a; }
```
当用户在右上角菜单切换为 **Dark 模式**时，Streamlit 自带的暗黑引擎会自动将 Tab 栏标题、正文等 CSS 类颜色计算为白色或半透明灰白（`#ffffff` / `rgba(250, 250, 250, 0.6)`）；而因为 `.stApp` 被强制锁死为浅色背景 `#f4f7f5`，**白字写在浅灰底上，对比度几乎为 0，造成严重的“字体隐身”！**

### 2.2 样式重构标准（Theme-Adaptive CSS）
严禁在 CSS 中写死不兼容 Dark 模式的背景色和文本色。必须使用 CSS 变量或针对暗黑模式的自适应媒体查询：

```css
/* 日间模式 (Light Mode) 变量 */
:root {
    --bg-main: #f4f7f5;
    --text-primary: #102a2a;
    --card-bg: #ffffff;
    --card-border: #dce8e1;
    --text-muted: #70817c;
}

/* 黑夜模式 (Dark Mode) 变量 */
@media (prefers-color-scheme: dark), [data-theme="dark"] {
    :root {
        --bg-main: #0e1117;
        --text-primary: #f0f2f6;
        --card-bg: #1a1c24;
        --card-border: #2d3139;
        --text-muted: #9ba3af;
    }
}

/* 应用自适应变量 */
.stApp {
    background-color: var(--bg-main) !important;
    color: var(--text-primary) !important;
}

.room-card, .ticket-card {
    background-color: var(--card-bg) !important;
    border: 1px solid var(--card-border) !important;
    color: var(--text-primary) !important;
}

/* 确保 Tab 标签高对比度可见 */
[data-testid="stTabs"] button {
    color: var(--text-primary) !important;
}
[data-testid="stTabs"] button[aria-selected="true"] {
    color: #27866b !important;
    font-weight: 700;
}
```

---

## 3. 大模型接驳面板设计（侧边栏一键切换云端与规则降级）

### 3.1 业务诉求回应：测试是用大模型还是 if/else？
- **行业真相**：
  - 如果只用 if/else，**那不是 Agent，只是规则脚本**，面对住客各种五花八门的口语无法泛化；
  - 如果只用大模型，**测试成本极高、速度慢（2-5秒/轮）、网络一断全盘崩溃**。
  - 因此，业界标准是**“双轨制”**：平常展示与住客真实对话使用**云端大模型 Function Calling**；CI 自动化测试与 Harness 评测使用**确定性规则降级（0成本、毫秒级响应）**。

### 3.2 侧边栏配置面板规格
在 `app.py` 的侧边栏新增一个折叠面板：`⚙️ 大模型连接配置`：
1. **服务商选择 (Provider Selectbox)**：
   - `SiliconFlow (硅基流动 · 推荐)`
   - `DeepSeek 官方 API`
   - `OpenAI 官方`
   - `本地 Ollama`
   - `纯离线规则模式 (Rule Only)`
2. **API Key 输入框**：`type="password"`，用户可直接贴入自己的 Key。
3. **Base URL 与模型名称**：根据服务商自动填充默认值（如 `https://api.siliconflow.cn/v1` 和 `deepseek-ai/DeepSeek-V3` 或 `Qwen/Qwen2.5-7B-Instruct`）。
4. **运行状态指示灯 (Badge)**：
   - 未配置 Key：显示 `🟡 运行在离线规则降级模式`；
   - 已配置 Key：显示 `🟢 已连接云端大模型 (Function Calling 激活)`。
