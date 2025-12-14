# -*- coding: utf-8 -*-
import base64
import re
import urllib
from datetime import datetime
from html import escape

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QUrl, QPoint
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSizePolicy, QApplication
)
from markdown import Markdown
from qfluentwidgets import (
    FluentIcon, ToolTipFilter, TransparentToolButton,
    CardWidget, CaptionLabel, InfoBar, InfoBarPosition
)
from qfluentwidgets.components.widgets.card_widget import CardSeparator, SimpleCardWidget

# 可选：如果你的项目有 ContextRegistry，保留；否则注释
try:
    from app.widgets.side_dock_area.plugins.llm_chatter.context_selector import ContextRegistry
except ImportError:
    ContextRegistry = None

# ======== Markdown 实例 ========
_md_instance = None
ACTION_COLOR_MAP = {
    "jump":   "#FFA500",   # 橙色
    "create": "#9370DB",   # 皇家蓝
    "generate":   "#32CD32",   # 石灰绿
    "ask": "#FF6347",   # 番茄红
    "view":   "#4169E1",   # 中紫色
}
DEFAULT_COLOR = "#888888"  # 未知类型兜底色


def get_markdown_instance():
    global _md_instance
    if _md_instance is None:
        _md_instance = Markdown(
            extensions=['fenced_code', 'nl2br', 'tables'],
            output_format='html5',
            safe=False
        )
    return _md_instance


def _unwrap_code_blocks_with_context_links(md_text: str) -> str:
    """
    如果代码块（```...```）内部包含 [xxx](yyy) 格式的上下文链接，
    则移除 ``` 包裹，使其作为普通 Markdown 段落渲染，
    从而让 [xxx](yyy) 能被正常转换为 context-tag。
    """
    def replacer(match):
        lang_part = match.group(1) or ""
        code_content = match.group(2)
        # 检查是否包含 [xxx](yyy) 模式（允许有空格）
        if re.search(r'\[[^\[\]]+\]\([^)\s]+\)', code_content) and lang_part in ("text"):
            # 包含上下文链接 → 返回未包裹的原始内容（保留语言标识？不保留）
            return code_content
        else:
            # 不包含 → 保留原样
            if lang_part:
                return f'```{lang_part}\n{code_content}```'
            else:
                return f'```\n{code_content}```'

    # 匹配所有 ```...``` 代码块（包括带语言和不带语言的）
    pattern = re.compile(r'```(\w*)\n(.*?)```', re.DOTALL)
    return pattern.sub(replacer, md_text)


# ======== Web 专用：代码块增强（使用 Pygments + 完整 CSS）========
def _wrap_code_blocks_with_copy_button_web(html: str) -> str:
    def replacer(match):
        lang = (match.group(1) or "").replace("language-", "").strip()
        code_content_raw = match.group(2) or ""

        try:
            copy_text = code_content_raw.replace("&lt;", "<") \
                .replace("&gt;", ">") \
                .replace("&amp;", "&") \
                .replace("&#39;", "'") \
                .replace("&quot;", '"')
        except:
            copy_text = code_content_raw

        b64_copy = base64.b64encode(copy_text.encode('utf-8')).decode('ascii')

        # —————— 关键：我们自己生成表格，不依赖 Pygments 行号 ——————
        try:
            from pygments import highlight
            from pygments.lexers import get_lexer_by_name, TextLexer
            from pygments.formatters import HtmlFormatter

            lexer = get_lexer_by_name(lang, stripall=False) if lang else TextLexer()
            formatter = HtmlFormatter(
                style='dracula',
                linenos=False,
                noclasses=True,
                cssclass='code-block',
                prestyles='margin:0; padding:0; background:transparent; font-family: Consolas, monospace; font-size:13px; color:#D4D4D4;'
            )
            highlighted_code = highlight(copy_text, lexer, formatter)
        except Exception:
            highlighted_code = f'<pre style="margin:0; padding:0; background:transparent; font-family: Consolas, monospace; font-size:13px; color:#D4D4D4;">{escape(copy_text)}</pre>'

        # —————— 手动构造带行号的表格 ——————
        lines = copy_text.splitlines() or [""]
        max_line = len(str(len(lines)))
        line_numbers_html = "\n".join(
            f'<td class="lineno" data-line="{i + 1}">{str(i + 1).rjust(max_line)}</td>'
            for i in range(len(lines))
        )
        try:
            import re as preg
            pre_match = preg.search(r'<pre[^>]*>(.*?)</pre>', highlighted_code, preg.DOTALL)
            if pre_match:
                inner_html = pre_match.group(1)
                code_lines = inner_html.split('\n')
                if len(code_lines) < len(lines):
                    code_lines.extend([''] * (len(lines) - len(code_lines)))
            else:
                code_lines = [escape(line) for line in lines]
        except:
            code_lines = [escape(line) for line in lines]

        code_lines_html = "\n".join(f'<td class="code-line">{line}</td>' for line in code_lines)
        table_rows = "\n".join(
            f'<tr>{line_numbers_html.splitlines()[i]}{code_lines_html.splitlines()[i]}</tr>'
            for i in range(len(lines))
        )

        table_html = f'''
        <table class="code-table">
            <tbody>
                {table_rows}
            </tbody>
        </table>
        '''

        return f'''
        <div style="
            position: relative;
            margin: 16px 0;
            background: #1E1E1E;
            border: 1px solid #3A3F47;
            border-radius: 6px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
            font-family: Consolas, monospace;
            font-size: 13px;
        ">
            <!-- 顶部工具栏区域（固定，不滚动） -->
            <div style="
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 6px 8px;
                height: 28px;
                background: rgba(30,30,30,0.8);
                border-bottom: 1px solid #333;
            ">
                <!-- 左侧：语言标签 -->
                {f'<span style="color: #FFA500; font-size: 13px; font-weight: bold;">{lang}</span>' if lang else '<span style="color: #888;">Plain Text</span>'}

                <!-- 右侧：按钮组 -->
                <div style="display: flex; gap: 15px; align-items: center; padding-right: 4px;">
                    <button type="button" data-action="insert" data-copy="{b64_copy}" style="
                        width: 28px;
                        height: 28px;
                        background: transparent;
                        border: none;
                        cursor: pointer;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        padding: 0;
                        border-radius: 4px;
                    " title="插入代码">
                        <img src="qrc:/icons/插入.svg" style="width:20px; height:20px; pointer-events: none;" />
                    </button>
                    <button type="button" data-action="create" data-copy="{b64_copy}" style="
                        width: 28px;
                        height: 28px;
                        background: transparent;
                        border: none;
                        cursor: pointer;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        padding: 0;
                        border-radius: 4px;
                    " title="新建组件">
                        <img src="qrc:/icons/新建.svg" style="width:20px; height:20px; pointer-events: none;" />
                    </button>
                    <button type="button" data-action="copy" data-copy="{b64_copy}" style="
                        width: 28px;
                        height: 28px;
                        background: transparent;
                        border: none;
                        cursor: pointer;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        padding: 0;
                        border-radius: 4px;
                    " title="复制代码">
                        <img src="qrc:/icons/复制.svg" style="width:20px; height:20px; pointer-events: none;" />
                    </button>
                </div>
            </div>

            <!-- 可横向滚动的代码区域（仅此处滚动） -->
            <div style="
                padding: 8px 10px;
                overflow-x: auto;
                overflow-y: hidden;
                scrollbar-width: thin;
                -ms-overflow-style: -ms-autohiding-scrollbar;
            ">
                {table_html}
            </div>
        </div>
        '''
    pattern = r'<pre><code(?:\s+class="([^"]*)")?>(.*?)</code></pre>'
    return re.sub(pattern, replacer, html, flags=re.DOTALL)

# ======== 辅助函数（保持不变）========
def _sanitize_incomplete_markdown(md_text: str) -> str:
    if not md_text.strip():
        return md_text
    if md_text.count('```') % 2 == 1:
        md_text += '\n```'
    if not md_text.endswith('\n'):
        md_text += '\n'
    return md_text


def _render_think_block(content: str, completed: bool = True) -> str:
    content = (content.replace("&", "&amp;")
               .replace("<", "&lt;")
               .replace(">", "&gt;")
               .replace('"', "&quot;"))
    status_text = "💡 思考过程" if completed else "🧠 正在思考..."

    open_attr = ' open' if not completed else ''

    return f'''
<details{open_attr} class="think-block" style="
    margin: 12px 0;
    background: #252D38;
    border: 1px solid #3A3F47;
    border-radius: 8px;
    padding: 12px;
    font-size: 13px;
    color: #CCCCCC;
">
    <summary style="
        cursor: pointer;
        color: #FFA500;
        font-weight: bold;
        list-style: none;
        outline: none;
    ">{status_text}</summary>
    <div style="margin-top: 8px; white-space: pre-wrap;">{content}</div>
</details>
'''


def _inject_think_cards(md_text: str, completed: bool = True) -> str:
    parts = []
    i = 0
    while i < len(md_text):
        start_idx = md_text.find("<think>", i)
        if start_idx == -1:
            parts.append(md_text[i:])
            break
        parts.append(md_text[i:start_idx])
        end_idx = md_text.find("</think>", start_idx + len("<think>"))
        if end_idx != -1:
            content = md_text[start_idx + len("<think>"):end_idx]
            parts.append(_render_think_block(content, completed=True))
            i = end_idx + len("</think>")
        else:
            content = md_text[start_idx + len("<think>"):]
            parts.append(_render_think_block(content, completed=False))
            i = len(md_text)
    return ''.join(parts)


def _inject_context_links(md_text: str) -> str:
    """
    将 [content](action) 转为可点击的 <span class="context-tag"> 标签
    不再使用 <a>，避免链接行为和渲染异常
    """
    def replacer(match):
        content = match.group(1)  # 如 "数据加载器"
        action = match.group(2)   # 如 "jump"

        # 安全编码，防止 XSS 或 JS 注入
        import urllib.parse
        encoded_content = urllib.parse.quote(content, safe='')
        encoded_action = urllib.parse.quote(action, safe='')
        return (
            f'<span class="context-tag" '
            f'data-type="{action}" '
            f'data-content="{encoded_content}" '
            f'data-action="{encoded_action}">'
            f'{escape(content)}'
            f'</span>'
        )

    return re.sub(r'`*\[([^\[\]]+?)\]\(([^)\s]+)\)`*', replacer, md_text)

# ======== 自定义 WebEnginePage：监听 console.log ========
class ConsoleMonitorPage(QWebEnginePage):
    codeActionRequested = pyqtSignal(str, str)  # (code: str, action: str)
    contextActionRequested = pyqtSignal(str, str)  # (type, content, action)
    heightReported = pyqtSignal(int)

    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        msg = message.strip()
        if msg.startswith("pywebview_action:"):
            if msg.startswith("pywebview_action:context|||"):
                try:
                    parts = msg.split("|||")
                    if len(parts) == 3:
                        _, raw_content, raw_action = parts
                        content = urllib.parse.unquote(raw_content)
                        action = urllib.parse.unquote(raw_action)
                        self.contextActionRequested.emit(content, action)
                except Exception:
                    pass
            elif msg.count(":") == 2:
                # 处理 copy/insert/create 等旧格式
                _, action, b64_payload = msg.split(":")
                try:
                    text = base64.b64decode(b64_payload).decode('utf-8')
                    self.codeActionRequested.emit(text, action)
                except Exception:
                    pass
        elif msg.startswith("pywebview_height:"):
            try:
                h = int(msg[len("pywebview_height:"):])
                self.heightReported.emit(h)
            except ValueError:
                pass


# ======== 核心：CodeWebViewer（基于 QWebEngineView）========
class CodeWebViewer(QWebEngineView):
    contentHeightChanged = pyqtSignal(int)
    codeActionRequested = pyqtSignal(str, str)  # (code, action)
    contextActionRequested = pyqtSignal(str, str)  # (type, content, action)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._markdown_text = ""
        self._streaming = True
        self._html_timer = None
        self._completed = False
        self._resize_timer = None  # 用于 debounce 的定时器
        # 使用自定义 Page 以捕获 console.log
        self._page = ConsoleMonitorPage(self)
        self.setPage(self._page)

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.page().setBackgroundColor(Qt.transparent)
        self.setContextMenuPolicy(Qt.NoContextMenu)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.setMinimumHeight(1)

        # 连接信号
        self._page.codeActionRequested.connect(self.codeActionRequested.emit)
        self._page.contextActionRequested.connect(self.contextActionRequested.emit)
        self._page.heightReported.connect(self._on_js_height_reported)

        self.loadFinished.connect(self._on_load_finished)

    def _on_load_finished(self, ok: bool):
        if ok:
            QTimer.singleShot(100, self._request_content_height)

    def _on_js_height_reported(self, height: int):
        self.contentHeightChanged.emit(height)

    def _render(self):

        def _generate_context_tag_css():
            css_rules = []
            for act_type, color in ACTION_COLOR_MAP.items():
                css_rules.append(
                    f'.context-tag[data-type="{act_type}"] {{ '
                    f'background: {color}20; '  # 20 = 12.5% 透明度（十六进制后加 20）
                    f'border-color: {color}; '
                    f'color: {color}; '
                    f'}}\n'
                    f'.context-tag[data-type="{act_type}"]:hover {{ '
                    f'background: {color}40; '  # 40 ≈ 25% 透明度
                    f'border-color: {color}aa; '  # 加亮一点
                    f'transform: translateY(-1px); '
                    f'}}'
                )
            # 默认兜底
            css_rules.append(
                f'.context-tag[data-type="other"], .context-tag:not([data-type]) {{ '
                f'background: {DEFAULT_COLOR}20; '
                f'border-color: {DEFAULT_COLOR}; '
                f'color: {DEFAULT_COLOR}; '
                f'}}'
            )
            return "\n".join(css_rules)

        if not self._markdown_text.strip():
            html_body = ""
        else:
            safe_md = _sanitize_incomplete_markdown(self._markdown_text)
            safe_md = _unwrap_code_blocks_with_context_links(safe_md)
            safe_md = _inject_context_links(safe_md)
            processed_md = _inject_think_cards(safe_md, completed=self._completed)

            try:
                md = get_markdown_instance()
                md.reset()
                html_body = md.convert(processed_md)
                html_body = _wrap_code_blocks_with_copy_button_web(html_body)
            except Exception:
                html_body = (self._markdown_text
                             .replace('&', '&amp;')
                             .replace('<', '&lt;')
                             .replace('>', '&gt;')
                             .replace('\n', '<br>'))

        full_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                html, body {{
                    background: transparent !important;
                    color: white;
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
                    font-size: 14px;
                    line-height: 1.5;
                    margin: 0;
                    padding: 4px 0;
                    overflow: hidden;
                    height: auto;
                    min-height: 1px;
                }}
                body > * {{
                    max-width: 100%;
                    overflow-wrap: break-word;
                }}
                .context-tag {{
                    display: inline-block;
                    padding: 2px 6px;
                    margin: 0 2px;
                    border: 1px solid;
                    border-radius: 4px;
                    font-size: 13px;
                    font-weight: 500;
                    cursor: pointer;
                    user-select: none;
                    transition: all 0.2s ease;
                    /* 基础样式，具体颜色由 data-type 覆盖 */
                }}
                /* 动态生成的类型专属样式 */
                {_generate_context_tag_css()}
                .context-tag:hover {{
                    background: rgba(255, 165, 0, 0.3);
                    border-color: #FFB733;
                    transform: translateY(-1px);
                }}
                pre, code {{
                    white-space: pre-wrap;
                    word-break: break-all;
                }}
                details {{
                    margin: 12px 0;
                    background: #252D38;
                    border: 1px solid #3A3F47;
                    border-radius: 8px;
                    padding: 12px;
                    font-size: 13px;
                    color: #CCCCCC;
                }}
                summary {{
                    color: #FFA500;
                    font-weight: bold;
                    cursor: pointer;
                    outline: none;
                    list-style: none;
                }}
                button[data-copy] {{
                    z-index: 10;
                }}
                .code-table {{
                    border-collapse: collapse;
                    width: auto;
                    min-width: 100%;
                    white-space: nowrap;
                    margin: 0;
                    font-family: Consolas, monospace;
                    font-size: 13px;
                    color: #D4D4D4;
                }}
                .code-table td {{
                    padding: 0;
                    vertical-align: top;
                    border: none;
                }}
                .code-table .lineno {{
                    user-select: none;
                    width: 28px !important;          /* ← 固定宽度 */
                    -webkit-user-select: none;
                    color: #666 !important;
                    padding-right: 4px !important;
                    border-right: 1px solid #444444 !important;
                    text-align: right;
                    white-space: nowrap;
                    min-width: 2.2em;
                }}
                .code-table .code-line {{
                    white-space: pre;
                    padding-left: 8px;
                    background: transparent !important;
                }}
                [style*="overflow-x: auto"]::-webkit-scrollbar {{
                    height: 10px;
                }}
                [style*="overflow-x: auto"]::-webkit-scrollbar-track {{
                    background: #252526;
                    border-radius: 5px;
                }}
                [style*="overflow-x: auto"]::-webkit-scrollbar-thumb {{
                    background: #454545;
                    border-radius: 5px;
                    border: 1px solid #3c3c3c;
                }}
                [style*="overflow-x: auto"]::-webkit-scrollbar-thumb:hover {{
                    background: #5a5a5a;
                }}
            </style>
        </head>
        <body>
            {html_body}
            <script>
                document.addEventListener('click', function(e) {{
                    const btn = e.target.closest('button[data-action]');
                    if (btn) {{
                        e.preventDefault();
                        const action = btn.getAttribute('data-action');
                        const b64 = btn.getAttribute('data-copy');
                        const text = atob(b64);
                        if (navigator.clipboard && action === 'copy') {{
                            navigator.clipboard.writeText(text).catch(() => {{
                                console.log('pywebview_action:copy:' + b64);
                            }});
                        }} else {{
                            console.log('pywebview_action:' + action + ':' + b64);
                        }}
                    }}
                }});
                document.addEventListener('click', function(e) {{
                    const tag = e.target.closest('.context-tag');
                    if (tag) {{
                        e.preventDefault();
                        const content = tag.getAttribute('data-content');
                        const action = tag.getAttribute('data-action');
                        if (content && action) {{
                            console.log('pywebview_action:context|||' + content + '|||' + action);
                        }}
                    }}
                }});
                function reportHeight() {{
                    const h = document.body.scrollHeight;
                    console.log('pywebview_height:' + h);
                }}
            
                document.addEventListener('DOMContentLoaded', function() {{
                    setTimeout(reportHeight, 100);
                    document.querySelectorAll('details.think-block').forEach(el => {{
                        el.addEventListener('toggle', () => setTimeout(reportHeight, 20));
                    }});
                }});
                if (window.ResizeObserver) {{
                    const resizeObserver = new ResizeObserver(() => {{
                        // 延迟一点，等 relayout 完成
                        setTimeout(reportHeight, 30);
                    }});
                    resizeObserver.observe(document.body);
                }} else {{
                    // 降级：监听 window resize（不够精确，但兼容旧版）
                    window.addEventListener('resize', () => setTimeout(reportHeight, 100));
                }}
                window.pywebview = {{
                    reportHeight: reportHeight
                }};
            </script>
        </body>
        </html>
        """
        self.setHtml(full_html, QUrl(""))
        QTimer.singleShot(100, self._request_content_height)

    def _request_content_height(self):
        self.page().runJavaScript("reportHeight();")

    def append_chunk(self, text: str):
        if not text:
            return
        self._markdown_text += text
        self._schedule_render()

    def finish_streaming(self):
        self._streaming = False
        self._completed = True
        self._render()

    def _schedule_render(self):
        if self._html_timer is None:
            self._html_timer = QTimer()
            self._html_timer.setSingleShot(True)
            self._html_timer.timeout.connect(self._render)
        if not self._html_timer.isActive():
            self._html_timer.start(80)

    def get_plain_text(self) -> str:
        return self._markdown_text

    def _handle_navigation(self, url: QUrl, _type, _is_main_frame):
        scheme = url.scheme()
        if scheme == "context":
            self.contextLinkClicked.emit(url.host())
            return False
        return True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 取消之前的定时器（关键！实现 debounce）
        if self._resize_timer:
            self._resize_timer.stop()
            self._resize_timer.deleteLater()
        # 创建新定时器
        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._request_content_height)
        self._resize_timer.start(10)  # 80ms 足够响应拖拽结束

    def wheelEvent(self, event: QWheelEvent):
        # 获取滚动条（向上找 QScrollArea）
        scroll_area = self.parent().parent.chat_scroll_area
        if scroll_area:
            vbar = scroll_area.verticalScrollBar()
            if vbar and vbar.minimum() != vbar.maximum():
                # 让外部 ScrollArea 滚动
                delta = event.angleDelta().y()
                vbar.setValue(vbar.value() - delta // 2)
                event.accept()  # 标记事件已处理
                return

        super().wheelEvent(event)


# ======== MessageCard（适配 WebViewer）========
class TagWidget(CardWidget):
    closed = pyqtSignal(str)
    doubleClicked = pyqtSignal(str)

    def __init__(self, key: str, text: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.setFixedHeight(24)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(6)

        self.label = CaptionLabel(text, self)
        layout.addWidget(self.label)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.doubleClicked.emit(self.key)
        super().mouseDoubleClickEvent(event)

class MessageCard(SimpleCardWidget):
    deleteRequested = pyqtSignal()
    regenerateRequested = pyqtSignal()
    actionRequested = pyqtSignal(str, str)  # (code, action)
    contextActionRequested = pyqtSignal(str, str)

    def __init__(self, role: str, timestamp: str = None, parent=None, tag_params: dict = None):
        super().__init__(parent)
        self.parent = parent
        self.role = role
        self.context_tags = tag_params or {}
        self.timestamp = timestamp or datetime.now().strftime('%H:%M')
        self.setup_ui()

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(2)
        main_layout.setSizeConstraint(QVBoxLayout.SetMinAndMaxSize)  # 关键！

        top_layout = QHBoxLayout()
        top_layout.setSpacing(6)

        if self.role == "user":
            avatar_text = "👤"
            avatar_color = "#63B3ED"
            name = "用户"
            name_color = "#63B3ED"
            bg_color = "#2A2A2A"
        else:
            avatar_text = "🤖"
            avatar_color = "#FFA500"
            name = "大模型助手"
            name_color = "#FFA500"
            bg_color = "#1E293B"

        avatar_label = QLabel(avatar_text, self)
        avatar_label.setStyleSheet(f"font-size: 20px; font-weight: bold; color: {avatar_color};")
        avatar_label.setFixedSize(28, 28)
        avatar_label.setAlignment(Qt.AlignCenter)

        name_label = QLabel(name, self)
        name_label.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {name_color};")

        top_layout.addWidget(avatar_label)
        top_layout.addWidget(name_label)

        if self.role == "assistant":
            time_label = QLabel(self.timestamp, self)
            time_label.setStyleSheet("font-size: 12px; color: #B0B0B0;")
            top_layout.addWidget(time_label)

        top_layout.addStretch()

        button_container = QWidget(self)
        button_layout = QHBoxLayout(button_container)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(4)

        if self.role == "assistant":
            btn_specs = [
                (FluentIcon.COPY, "复制", lambda: self.actionRequested.emit(self.content_widget.get_plain_text(), "copy")),
                (FluentIcon.SYNC, "重新生成", self.regenerateRequested.emit)
            ]
        elif self.role == "user":
            btn_specs = [
                (FluentIcon.COPY, "复制", lambda: self.actionRequested.emit(self.content_widget.get_plain_text(), "copy")),
                (FluentIcon.DELETE, "删除", self.deleteRequested.emit),
            ]
        else:
            btn_specs = []

        for icon, tooltip, slot in btn_specs:
            btn = TransparentToolButton(icon, self)
            btn.setToolTip(tooltip)
            btn.clicked.connect(slot)
            btn.setFixedSize(24, 24)
            btn.installEventFilter(ToolTipFilter(btn))
            button_layout.addWidget(btn)

        top_layout.addWidget(button_container)
        main_layout.addLayout(top_layout)
        main_layout.addWidget(CardSeparator(self))

        if self.role == "user" and self.context_tags:
            tags_container = QWidget(self)
            tags_container.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Minimum)
            tags_layout = QHBoxLayout(tags_container)
            tags_layout.setContentsMargins(0, 0, 0, 0)
            tags_layout.setSpacing(4)

            for key, (name, content, callback_params, is_image) in self.context_tags.items():
                tag = TagWidget(key, name)
                tag.doubleClicked.connect(lambda k=key, t=tag: self._on_context_link_clicked(k, t))
                tags_layout.addWidget(tag)
            tags_layout.addStretch()
            main_layout.addWidget(tags_container)
            main_layout.addWidget(CardSeparator(self))

        self.content_widget = CodeWebViewer(self)
        self.content_widget.contextActionRequested.connect(self.contextActionRequested.emit)
        self.content_widget.contentHeightChanged.connect(self._on_content_height_changed)
        self.content_widget.codeActionRequested.connect(
            lambda code, action: QTimer.singleShot(200, lambda: self._on_code_action(code, action))
        )
        main_layout.addWidget(self.content_widget)
        main_layout.addWidget(CardSeparator(self))

        self.setStyleSheet(f"""
            CardWidget {{
                background-color: {bg_color};
                border: 1px solid {'#4A5568' if self.role == 'user' else '#334155'};
                border-radius: 8px;
            }}
        """)

    def _on_code_action(self, code: str, action: str):
        if action == "copy":
            QApplication.clipboard().setText(code)
            InfoBar.success(
                title='已复制',
                content='代码已复制到剪贴板',
                isClosable=True,
                position=InfoBarPosition.TOP_RIGHT,
                duration=2000,
                parent=self.parent
            )
            self.actionRequested.emit(code, action)

        elif action == "insert":
            self.actionRequested.emit(code, action)

        elif action == "create":
            self.actionRequested.emit(code, action)

    def _on_context_link_clicked(self, tool_key: str, tag: TagWidget):
        if tool_key in self.context_tags:
            name, content, callback_params, is_image = self.context_tags[tool_key]
            executor = self.parent.homepage.context_register.get_executor(tool_key)
            if executor:
                executor(callback_params, tag)

    def _on_content_height_changed(self, height):
        self.content_widget.setMinimumHeight(max(1, height))
        self.updateGeometry()
        QTimer.singleShot(20, lambda: self.parentWidget().updateGeometry() if self.parentWidget() else None)

    def update_content(self, new_content: str):
        self.content_widget.append_chunk(new_content)

    def finish_streaming(self):
        self.content_widget.finish_streaming()

    def wheelEvent(self, event: QWheelEvent):
        # 获取滚动条（向上找 QScrollArea）
        scroll_area = self.parent.chat_scroll_area
        if scroll_area:
            vbar = scroll_area.verticalScrollBar()
            if vbar and vbar.minimum() != vbar.maximum():
                # 让外部 ScrollArea 滚动
                delta = event.angleDelta().y()
                vbar.setValue(vbar.value() - delta // 2)
                event.accept()  # 标记事件已处理
                return

        super().wheelEvent(event)


def create_welcome_card(parent=None) -> MessageCard:
    welcome_md = """\
### 👋 你好！我是你的画布开发智能助手

我已为你准备好以下能力，助你高效构建与调试画布：
  
- **🔗 上下文增强**  
  可动态插入画布节点、组件信息、全局变量等上下文（点击下方 `+` 选择插入）。
  
- **⚡ 上下文联动**  
  点击带链接的名称即可触发交互逻辑：
  - **跳转节点**：`[节点名](jump)` → 定位到画布中对应节点  
  - **创建组件**：`[组件名](create)` → 在画布中生成新组件节点  
  - **生成代码**：`[组件名](generate)` → 跳转至组件开发界面并自动生成代码  

---

### 💬 快速开始：点击下方问题直接提问

- [帮我分析当前画布功能是否合理？](ask)  
- [结合组件库，帮我完善当前画布：列出需新增的组件，如有前置节点需说明具体位置，如何连接，参数如何设置；若组件库缺失，也请说明需生成的新组件。](ask)  
- [帮我审查当前组件代码，指出潜在问题并提供优化建议。](ask)

"""

    card = MessageCard(role="welcome", timestamp="就绪", parent=parent)
    card.update_content(welcome_md)
    card.finish_streaming()
    return card