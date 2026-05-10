// ===== Utility / Helper Functions =====

function escHtml(s) {
  if (typeof s !== 'string') s = String(s || '');
  var div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

function escAttr(s) {
  if (typeof s !== 'string') s = String(s || '');
  return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function truncate(s, len) {
  if (!s) return '';
  return s.length > len ? s.slice(0, len - 3) + '...' : s;
}

function renderMd(s) {
  if (typeof s !== 'string') s = String(s || '');
  if (typeof marked !== 'undefined') {
    // Protect LaTeX from Markdown parser
    var mathBlocks = [];
    // $$...$$ (display)
    s = s.replace(/\$\$([\s\S]*?)\$\$/g, function(m) { mathBlocks.push(m); return '%%MATH' + (mathBlocks.length - 1) + '%%'; });
    // \[...\] (display)
    s = s.replace(/\\\[([\s\S]*?)\\\]/g, function(m) { mathBlocks.push(m); return '%%MATH' + (mathBlocks.length - 1) + '%%'; });
    // \(...\) (inline)
    s = s.replace(/\\\(([\s\S]*?)\\\)/g, function(m) { mathBlocks.push(m); return '%%MATH' + (mathBlocks.length - 1) + '%%'; });
    // $...$ (inline, single line only)
    s = s.replace(/\$([^\$\n]+?)\$/g, function(m) { mathBlocks.push(m); return '%%MATH' + (mathBlocks.length - 1) + '%%'; });

    var html = marked.parse(s, { breaks: true });

    // Restore LaTeX blocks
    for (var i = 0; i < mathBlocks.length; i++) {
      html = html.replace('%%MATH' + i + '%%', mathBlocks[i]);
    }
    return '<span class="md-rendered">' + html + '</span>';
  }
  return '<pre>' + escHtml(s) + '</pre>';
}

function renderMathInChat() {
  if (typeof renderMathInElement === 'undefined') return;
  document.querySelectorAll('.md-rendered').forEach(function(el) {
    if (el.dataset.mathRendered) return;
    renderMathInElement(el, {
      delimiters: [
        { left: '$$', right: '$$', display: true },
        { left: '$', right: '$', display: false },
        { left: '\\[', right: '\\]', display: true },
        { left: '\\(', right: '\\)', display: false },
      ],
      throwOnError: false,
    });
    el.dataset.mathRendered = '1';
  });
}

// "Stick to bottom" follows the last message's tail through streaming
// updates. User-initiated scroll-up detaches; scrolling back near the
// bottom re-attaches. The chat-messages container has a large
// padding-bottom (40vh) for reading comfort once the conversation is
// idle, but during streaming we anchor on the last message's actual
// bottom edge so the streaming text stays visible instead of getting
// pushed up while empty padding fills the viewport.
var _stickToBottom = true;
window.__stickToBottom = function () { return _stickToBottom; };

function _setupStickToBottomListener() {
  if (window.__stickListenerInstalled) return;
  var area = document.getElementById('chatArea');
  if (!area) return;
  area.addEventListener('scroll', function () {
    var distFromBottom = area.scrollHeight - area.scrollTop - area.clientHeight;
    // Within 60px of the bottom = stick. Anything further = detach.
    _stickToBottom = distFromBottom < 60;
  }, { passive: true });
  window.__stickListenerInstalled = true;
}

function scrollToBottom(opts) {
  renderMathInChat();
  var area = document.getElementById('chatArea');
  var messages = document.getElementById('chatMessages');
  if (!area || !messages) return;
  _setupStickToBottomListener();
  // ``force: true`` overrides the stick-to-bottom check (used after
  // load_session to land at the latest turn even if the user was
  // scrolled up in the previous session).
  var force = !!(opts && opts.force);
  if (!force && !_stickToBottom) return;
  requestAnimationFrame(function () {
    // Anchor the last message's bottom to the viewport bottom (with a
    // 16px breathing margin) instead of scrolling to the padding edge,
    // so streaming text doesn't get hidden above the 40vh empty pad.
    var bubbles = messages.querySelectorAll('.message');
    var last = bubbles.length ? bubbles[bubbles.length - 1] : null;
    if (!last) {
      area.scrollTop = area.scrollHeight;
      return;
    }
    var areaRect = area.getBoundingClientRect();
    var msgRect = last.getBoundingClientRect();
    var delta = msgRect.bottom - areaRect.bottom + 16;
    if (delta > 0) area.scrollTop += delta;
    // Clamp: never scroll past natural max.
    if (area.scrollTop > area.scrollHeight - area.clientHeight) {
      area.scrollTop = area.scrollHeight - area.clientHeight;
    }
  });
}

function appendToChat(el) {
  var container = document.getElementById('chatMessages');
  if (!container) return;
  container.appendChild(el);
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 200) + 'px';
}

function setWelcomeVisible(show) {
  var w = document.getElementById('welcomeScreen');
  var ex = document.getElementById('welcomeExamples');
  var cm = document.getElementById('chatMessages');
  var ca = document.getElementById('chatArea');
  if (w) w.style.display = show ? '' : 'none';
  if (ex) ex.style.display = show ? '' : 'none';
  if (cm) cm.style.paddingBottom = show ? '0' : '';
  if (ca) ca.classList.toggle('welcome-visible', !!show);
}

function addSystemMessage(text) {
  var container = document.getElementById('chatMessages');
  if (!container) return;
  var welcome = document.getElementById('welcomeScreen');
  if (welcome && welcome.style.display !== 'none') return;
  var div = document.createElement('div');
  div.className = 'system-message';
  div.textContent = text;
  appendToChat(div);
  scrollToBottom();
}

function parseRunCommandForDisplay(text) {
  var t = text.trim();
  var match = t.match(/^(?:run\s+)(\S+)\s*(.*)/i);
  if (match) return { funcName: match[1], params: match[2] || '' };
  var match2 = t.match(/^(create|fix)\s+(.*)/i);
  if (match2) return { funcName: match2[1], params: match2[2] || '' };
  return { funcName: t, params: '' };
}

function fmtTokenNum(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'm';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
  return n;
}

function _isClaudeProvider(p) {
  return p === 'claude_code' || p === 'claude-code' || p === 'anthropic';
}
function _isCodexProvider(p) {
  return p === 'openai-codex' || p === 'codex' || p === 'codex-cli';
}

function _buildUsageText(usage, provider) {
  if (!usage || (!usage.input_tokens && !usage.output_tokens && !usage.cache_read && !usage.cache_create)) return '';
  // Backend normalizes `input_tokens` to new (non-cached) tokens for every
  // provider — matches the Anthropic convention. Full context size is the
  // sum of new + cache read + cache write.
  var base = usage.input_tokens || 0;
  var cached = usage.cache_read || 0;
  var cacheWrite = usage.cache_create || 0;
  var total = base + cached + cacheWrite;
  var outTok = usage.output_tokens || 0;

  var short = fmtTokenNum(total) + ' in · ' + fmtTokenNum(outTok) + ' out';
  var detail = [];
  if (base > 0) detail.push(fmtTokenNum(base) + ' base');
  if (cacheWrite > 0) detail.push(fmtTokenNum(cacheWrite) + ' write');
  if (cached > 0) detail.push(fmtTokenNum(cached) + ' hit');
  detail.push(fmtTokenNum(outTok) + ' out');
  return { text: short, tooltip: detail.join(' · ') };
}

function _getExecProvider() {
  return (typeof _agentSettings !== 'undefined' && _agentSettings.exec && _agentSettings.exec.provider) || '';
}

function formatUsageBadge(usage) {
  var result = _buildUsageText(usage, _getExecProvider());
  if (!result) return '';
  var t = typeof result === 'string' ? result : result.text;
  var tip = typeof result === 'object' && result.tooltip ? ' title="' + escAttr(result.tooltip) + '"' : '';
  return '<span style="font-size:10px;color:var(--text-muted);font-family:var(--font-mono);margin-left:auto;padding-left:8px"' + tip + '>' + escHtml(t) + '</span>';
}

function formatUsageFooterLabel(usage) {
  var result = _buildUsageText(usage, _getExecProvider());
  if (!result) return '';
  var t = typeof result === 'string' ? result : result.text;
  var tip = typeof result === 'object' && result.tooltip ? ' title="' + escAttr(result.tooltip) + '"' : '';
  return '<span class="usage-footer-label"' + tip + '>' + escHtml(t) + '</span>';
}

function formatProviderLabel(info) {
  if (!info || !info.provider) return 'No provider';
  var parts = [info.provider];
  if (info.type) parts.push(info.type);
  if (info.model) parts.push(info.model);
  return parts.join(' \u00b7 ');
}

function formatProgramResultContent(output) {
  if (output == null) return '';
  if (typeof output === 'string') return output;
  if (typeof output !== 'object') return String(output);

  if (typeof output.final_state === 'string' && output.final_state.trim()) {
    return output.final_state;
  }
  if (typeof output.output === 'string' && output.output.trim()) {
    return output.output;
  }
  if (typeof output.reasoning === 'string' && output.reasoning.trim()) {
    return output.reasoning;
  }
  if (Array.isArray(output.history) && output.history.length > 0) {
    var last = output.history[output.history.length - 1] || {};
    if (typeof last.output === 'string' && last.output.trim()) {
      return last.output;
    }
    if (typeof last.reasoning === 'string' && last.reasoning.trim()) {
      return last.reasoning;
    }
  }
  if (typeof output.action === 'string' && output.action) {
    var summary = output.action;
    if (typeof output.target === 'string' && output.target.trim()) {
      summary += ': ' + output.target.trim();
    }
    return summary;
  }

  try {
    return JSON.stringify(output, null, 2);
  } catch (e) {
    return String(output);
  }
}

function highlightPython(code) {
  var lines = code.split('\n');
  return lines.map(function(line, i) {
    var num = '<span class="line-num">' + (i+1) + '</span>';
    var hl = escHtml(line);
    var tokens = [];
    hl = hl.replace(/("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"[^"]*"|\'[^\']*\'|#.*$)/gm, function(m) {
      var idx = tokens.length;
      var cls = m.startsWith('#') ? 'syn-comment' : 'syn-string';
      tokens.push('<span class="' + cls + '">' + m + '</span>');
      return '\x00TOK' + idx + '\x00';
    });
    hl = hl.replace(/\b(from|import|def|class|return|if|else|elif|for|while|try|except|finally|with|as|raise|yield|pass|break|continue|and|or|not|in|is|lambda|True|False|None)\b/g, '<span class="syn-keyword">$1</span>');
    hl = hl.replace(/^(\s*)(@\w+)/gm, '$1<span class="syn-decorator">$2</span>');
    hl = hl.replace(/\b(\d+\.?\d*)\b/g, '<span class="syn-number">$1</span>');
    hl = hl.replace(/\b(self)\b/g, '<span class="syn-self">$1</span>');
    hl = hl.replace(/\x00TOK(\d+)\x00/g, function(_, idx) { return tokens[idx]; });
    return num + hl;
  }).join('\n');
}
