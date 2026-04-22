function _buildFieldsHtml(fn) {
  var params = (fn.params_detail || []).filter(function(p) {
    if (p.name === 'runtime' || p.name === 'callback' || p.name === 'exec_runtime' || p.name === 'review_runtime') return false;
    if (p.hidden) return false;
    return true;
  });

  var fieldsHtml = '';
  for (var i = 0; i < params.length; i++) {
    var p = params[i];
    var typeLabel = p.type ? '<span class="fn-form-label-type">' + escHtml(p.type) + '</span>' : '';
    var reqLabel = p.required
      ? '<span class="fn-form-label-required">*</span>'
      : '<span class="fn-form-label-optional">optional</span>';
    var descSpan = p.description
      ? '<span class="fn-form-label-desc">' + escHtml(p.description) + '</span>'
      : '';

    var placeholder = p.placeholder || '';
    if (!placeholder && p.default && p.default !== 'None' && !(p.default + '').startsWith('_')) {
      placeholder = 'default: ' + p.default;
    }

    var isBool = p.type === 'bool' || p.type === 'boolean';
    var isMultiline = p.multiline !== undefined ? p.multiline : (!isBool && (p.type === 'str' || p.type === 'string' || !p.type));
    var inputTag;
    var defaultVal = (p.default || '').replace(/^["']|["']$/g, '');

    if (isBool) {
      var yesActive = (defaultVal === 'True') ? ' active' : '';
      var noActive = (defaultVal === 'False' || !defaultVal) ? ' active' : '';
      inputTag =
        '<div class="fn-form-toggle" id="fnField_' + escAttr(p.name) + '" data-value="' + (defaultVal === 'True' ? 'True' : 'False') + '">' +
          '<button type="button" class="fn-form-toggle-btn' + yesActive + '" onclick="toggleBool(\'' + escAttr(p.name) + '\', \'True\', this)">Yes</button>' +
          '<button type="button" class="fn-form-toggle-btn' + noActive + '" onclick="toggleBool(\'' + escAttr(p.name) + '\', \'False\', this)">No</button>' +
        '</div>';
    } else if (p.options_from === 'functions') {
      var fnOpts = availableFunctions.filter(function(f) {
        var cat = f.category || 'user';
        return cat !== 'meta' && cat !== 'builtin';
      });
      var selectHtml = '<option value="">-- select --</option>';
      for (var j = 0; j < fnOpts.length; j++) {
        selectHtml += '<option value="' + escAttr(fnOpts[j].name) + '">' + escHtml(fnOpts[j].name) + '</option>';
      }
      inputTag = '<select class="fn-form-input fn-form-select" id="fnField_' + escAttr(p.name) + '">' + selectHtml + '</select>';
    } else if (p.options && p.options.length > 0) {
      var chipsHtml = '';
      for (var j = 0; j < p.options.length; j++) {
        var isDefault = (p.options[j] === defaultVal) ? ' active' : '';
        chipsHtml += '<button type="button" class="fn-form-option-chip' + isDefault + '" onclick="selectOption(\'' + escAttr(p.name) + '\', \'' + escAttr(p.options[j]) + '\', this)">' + escHtml(p.options[j]) + '</button>';
      }
      chipsHtml += '<input type="text" class="fn-form-option-custom" placeholder="..." ' +
        'oninput="selectOptionCustom(\'' + escAttr(p.name) + '\', this)">';
      inputTag = '<div class="fn-form-options" id="fnField_' + escAttr(p.name) + '" data-value="' + escAttr(defaultVal) + '">' + chipsHtml + '</div>';
    } else if (isMultiline) {
      inputTag = '<textarea class="fn-form-input fn-form-textarea" id="fnField_' + escAttr(p.name) + '" placeholder="' + escAttr(placeholder) + '" rows="2"></textarea>';
    } else {
      inputTag = '<input class="fn-form-input" id="fnField_' + escAttr(p.name) + '" placeholder="' + escAttr(placeholder) + '">';
    }

    fieldsHtml +=
      '<div class="fn-form-field">' +
        '<div class="fn-form-label">' +
          '<span class="fn-form-label-name">' + escHtml(p.name) + '</span>' +
          typeLabel + reqLabel + descSpan +
        '</div>' +
        inputTag +
      '</div>';
  }

  if (params.length === 0) {
    fieldsHtml = '<div class="fn-form-no-params">No parameters needed — click run to execute</div>';
  }
  return fieldsHtml;
}


// Pin bottom-row to its current screen position during height animations
function _pinBottomRow(bottomRow) {
  if (!bottomRow) return function(){};
  var rect = bottomRow.getBoundingClientRect();
  bottomRow.style.position = 'fixed';
  bottomRow.style.left = rect.left + 'px';
  bottomRow.style.top = rect.top + 'px';
  bottomRow.style.width = rect.width + 'px';
  bottomRow.style.bottom = 'auto';
  bottomRow.style.right = 'auto';
  return function() {
    bottomRow.style.position = '';
    bottomRow.style.left = '';
    bottomRow.style.top = '';
    bottomRow.style.width = '';
    bottomRow.style.bottom = '';
    bottomRow.style.right = '';
  };
}

/**
 * Pin send button at its current visual position (position:fixed) so it
 * stays put while the wrapper height animates. Returns an unpin function
 * that uses FLIP to smoothly animate the button to its CSS-determined
 * final position.
 */
/* Send button animation handled purely by CSS transition on `bottom`.
   Wrapper bottom edge is fixed, so bottom-based positioning is stable. */

function _buildFormHtml(fn, fieldsHtml) {
  // No footer — .input-bottom-row stays as permanent element in wrapper
  var workdirHtml = (typeof buildWorkdirField === 'function') ? buildWorkdirField() : '';
  return '<div class="fn-form-header">' +
    '<div class="fn-form-title">' +
      '<span class="fn-form-name"><span style="color:var(--text-secondary);font-weight:400">function </span>' + escHtml(fn.name) + '</span>' +
      '<span class="fn-form-desc">' + escHtml(fn.description || '') + '</span>' +
    '</div>' +
    '<button class="fn-form-close" type="button" onclick="closeFnForm()" onmousedown="event.preventDefault()" tabindex="-1" title="Close" aria-label="Close">' +
      '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">' +
        '<path d="M4 4L12 12M12 4L4 12" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>' +
      '</svg>' +
    '</button>' +
  '</div>' +
  '<div class="fn-form-body">' + workdirHtml + fieldsHtml + '</div>';
}

function _showFnFormSwitch(fn, wrapper, sendBtn) {
  var heightBefore = wrapper.offsetHeight;

  // Build new content HTML first
  var fieldsHtml = _buildFieldsHtml(fn);
  var formHtml = _buildFormHtml(fn, fieldsHtml);

  // Measure target height with a hidden clone
  var measure = wrapper.cloneNode(false);
  measure.style.cssText = 'position:absolute;visibility:hidden;pointer-events:none;width:' + wrapper.offsetWidth + 'px;height:auto;overflow:visible;';
  var sendClone = document.getElementById('sendBtn').cloneNode(true);
  var stopClone = document.getElementById('stopBtn').cloneNode(true);
  var bottomRow = wrapper.querySelector('.input-bottom-row');
  measure.appendChild(sendClone);
  measure.appendChild(stopClone);
  measure.insertAdjacentHTML('beforeend', formHtml);
  if (bottomRow) measure.appendChild(bottomRow.cloneNode(true));
  wrapper.parentNode.appendChild(measure);
  var heightAfter = measure.offsetHeight;
  measure.remove();

  // Lock current height, pin bottom-row and send button
  wrapper.style.height = heightBefore + 'px';
  wrapper.style.overflow = 'hidden';
  wrapper.style.transition = 'none';
  var unpinBottomRow = _pinBottomRow(bottomRow);


  // Swap content (insert before bottomRow)
  var oldParts = wrapper.querySelectorAll('.fn-form-header, .fn-form-body');
  for (var i = 0; i < oldParts.length; i++) oldParts[i].remove();
  var temp = document.createElement('div');
  temp.innerHTML = formHtml;
  while (temp.firstChild) wrapper.insertBefore(temp.firstChild, bottomRow);

  wrapper.dataset.fnName = fn.name;
  sendBtn.setAttribute('onclick', "submitFnForm('" + escAttr(fn.name) + "')");
  if (typeof buildThinkingMenu === 'function') buildThinkingMenu();
  if (typeof initWorkdirField === 'function') initWorkdirField(fn.name);

  // Animate to target height
  requestAnimationFrame(function() {
    wrapper.style.transition = 'height 0.25s cubic-bezier(0.25, 0.1, 0.25, 1)';
    wrapper.style.height = heightAfter + 'px';
    wrapper.addEventListener('transitionend', function handler(e) {
      if (e.target !== wrapper || e.propertyName !== 'height') return;
      wrapper.style.height = '';
      wrapper.style.overflow = '';
      wrapper.style.transition = '';
      unpinBottomRow();

      wrapper.removeEventListener('transitionend', handler);
    });
  });

  // Setup textarea auto-resize
  setTimeout(function() {
    var textareas = wrapper.querySelectorAll('.fn-form-textarea');
    for (var i = 0; i < textareas.length; i++) {
      textareas[i].addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 160) + 'px';
      });
    }
  }, 50);
}

function showFnForm(fn) {
  var wrapper = document.querySelector('.input-wrapper');
  if (!wrapper) return;

  // Save only the swappable content (sendBtn, stopBtn, input-bottom-row stay in wrapper)
  var sendBtn = document.getElementById('sendBtn');
  var stopBtn = document.getElementById('stopBtn');
  var bottomRow = wrapper.querySelector('.input-bottom-row');
  if (!_fnFormActive) {
    // Save children except permanent elements
    _inputContentOriginal = [];
    var children = wrapper.children;
    for (var i = 0; i < children.length; i++) {
      if (children[i] !== sendBtn && children[i] !== stopBtn && children[i] !== bottomRow) {
        _inputContentOriginal.push(children[i]);
      }
    }
  } else {
    // Already have a form open — switch content in-place
    _showFnFormSwitch(fn, wrapper, sendBtn);
    return;
  }
  _fnFormActive = true;

  // --- Hide welcome examples with height collapse ---
  var examples = document.getElementById('welcomeExamples');
  if (examples) {
    var exH = examples.offsetHeight;
    examples.style.height = exH + 'px';
    examples.style.overflow = 'hidden';
    examples.style.opacity = '1';
    examples.style.pointerEvents = 'none';
    requestAnimationFrame(function() {
      examples.style.transition = 'opacity 0.15s ease, height 0.25s cubic-bezier(0.25, 0.1, 0.25, 1)';
      examples.style.opacity = '0';
      examples.style.height = '0px';
      examples.style.padding = '0 24px';
    });
  }

  // --- Capture before state ---
  var wrapperBefore = wrapper.getBoundingClientRect();

  // --- Build form HTML ---
  var fieldsHtml = _buildFieldsHtml(fn);

  // --- Replace content (keep sendBtn, stopBtn, bottomRow) ---
  wrapper.style.height = wrapperBefore.height + 'px';
  wrapper.style.overflow = 'hidden';

  // Remove old content (not permanent elements)
  _inputContentOriginal.forEach(function(el) { el.remove(); });

  // Build form content as DOM (inserted before bottomRow which stays)
  var formHtml = _buildFormHtml(fn, fieldsHtml);
  var temp = document.createElement('div');
  temp.innerHTML = formHtml;
  while (temp.firstChild) wrapper.insertBefore(temp.firstChild, bottomRow);

  // --- Freeze send button + context stats before class change ---
  var sendBtnBottom = parseFloat(getComputedStyle(sendBtn).bottom);
  sendBtn.style.transition = 'none';
  sendBtn.style.bottom = sendBtnBottom + 'px';
  var ctxStats = wrapper.querySelector('.context-stats-label');
  if (ctxStats) { ctxStats.style.transition = 'none'; ctxStats.style.marginRight = '0'; }
  void sendBtn.offsetHeight;

  wrapper.className = 'input-wrapper fn-form-mode';
  wrapper.dataset.fnName = fn.name;
  sendBtn.setAttribute('onclick', "submitFnForm('" + escAttr(fn.name) + "')");
  sendBtn.title = 'Run';
  if (typeof buildThinkingMenu === 'function') buildThinkingMenu();

  // --- Set initial opacity for fade-in ---
  var formHeader = wrapper.querySelector('.fn-form-header');
  var formBody = wrapper.querySelector('.fn-form-body');
  if (formHeader) formHeader.style.opacity = '0';
  if (formBody) formBody.style.opacity = '0';
  // Bottom separator starts transparent, fades in with header
  if (bottomRow) { bottomRow.style.transition = 'none'; bottomRow.style.borderTopColor = 'transparent'; void bottomRow.offsetHeight; }

  // --- Measure target height ---
  var wrapperAfterHeight = wrapper.scrollHeight;

  // --- Pin bottom-row so it doesn't move during animation ---
  var unpinBottomRow = _pinBottomRow(bottomRow);

  // --- Release send button + context stats: animate simultaneously ---
  sendBtn.style.transition = '';
  sendBtn.style.bottom = '';
  if (ctxStats) { ctxStats.style.transition = ''; ctxStats.style.marginRight = ''; }

  // --- Single rAF: animate height + fade in content ---
  requestAnimationFrame(function() {
    wrapper.style.transition = 'height 0.3s cubic-bezier(0.25, 0.1, 0.25, 1)';
    wrapper.style.height = wrapperAfterHeight + 'px';

    if (formHeader) { formHeader.style.transition = 'opacity 0.25s ease 0.1s'; formHeader.style.opacity = '1'; }
    if (formBody) { formBody.style.transition = 'opacity 0.25s ease 0.15s'; formBody.style.opacity = '1'; }
    if (bottomRow) { bottomRow.style.transition = 'border-color 0.25s ease 0.1s'; bottomRow.style.borderTopColor = ''; }

    wrapper.addEventListener('transitionend', function handler(e) {
      if (e.target !== wrapper || e.propertyName !== 'height') return;
      wrapper.style.height = '';
      wrapper.style.overflow = '';
      wrapper.style.transition = '';
      unpinBottomRow();

      wrapper.removeEventListener('transitionend', handler);
    });
  });

  // --- Setup textarea auto-resize ---
  setTimeout(function() {
    var textareas = wrapper.querySelectorAll('.fn-form-textarea');
    for (var i = 0; i < textareas.length; i++) {
      textareas[i].addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 160) + 'px';
      });
    }
  }, 50);

  // --- Prefill workdir from server (remembered per conversation+function) ---
  if (typeof initWorkdirField === 'function') initWorkdirField(fn.name);
}

function closeFnForm() {
  if (!_fnFormActive) return;
  var wrapper = document.querySelector('.input-wrapper');
  if (!wrapper) return;

  // Blur whatever's focused inside the form first so the wrapper's
  // :focus-within ring drops immediately (instead of lingering until the form
  // is removed from DOM). Visually: clicking X reads as "click outside".
  var active = document.activeElement;
  if (active && wrapper.contains(active) && typeof active.blur === 'function') {
    active.blur();
  }
  var sendBtn = document.getElementById('sendBtn');
  var stopBtn = document.getElementById('stopBtn');

  // Measure target height (include permanent bottomRow)
  var bottomRow = wrapper.querySelector('.input-bottom-row');
  var measure = wrapper.cloneNode(false);
  measure.className = 'input-wrapper';
  _inputContentOriginal.forEach(function(el) { measure.appendChild(el.cloneNode(true)); });
  if (bottomRow) measure.appendChild(bottomRow.cloneNode(true));
  measure.style.cssText = 'position:absolute;visibility:hidden;pointer-events:none;width:' + wrapper.offsetWidth + 'px';
  wrapper.parentNode.appendChild(measure);
  var targetHeight = measure.offsetHeight;
  wrapper.parentNode.removeChild(measure);

  // Step 1: Fade out form content + bottom separator
  var formParts = wrapper.querySelectorAll('.fn-form-header, .fn-form-body');
  formParts.forEach(function(el) {
    el.style.transition = 'opacity 0.12s ease';
    el.style.opacity = '0';
  });
  if (bottomRow) {
    bottomRow.style.transition = 'border-color 0.12s ease';
    bottomRow.style.borderTopColor = 'transparent';
  }

  // Step 2: Lock height, pin bottom-row, then shrink
  var heightBefore = wrapper.offsetHeight;
  wrapper.style.height = heightBefore + 'px';
  wrapper.style.overflow = 'hidden';
  var unpinBottomRow = _pinBottomRow(bottomRow);

  // Start send button + context stats moving simultaneously with height shrink.
  var sendBtnTargetBottom = targetHeight - 42;
  sendBtn.style.bottom = sendBtnTargetBottom + 'px';
  var ctxStats = wrapper.querySelector('.context-stats-label');
  if (ctxStats) ctxStats.style.marginRight = '0';

  requestAnimationFrame(function() {
    wrapper.style.transition = 'height 0.3s cubic-bezier(0.25, 0.1, 0.25, 1)';
    wrapper.style.height = targetHeight + 'px';

    // Show welcome examples
    var examples = document.getElementById('welcomeExamples');
    if (examples) {
      examples.style.transition = 'none';
      examples.style.height = '';
      examples.style.padding = '';
      examples.style.overflow = 'hidden';
      examples.style.opacity = '0';
      examples.style.pointerEvents = '';
      var naturalH = examples.scrollHeight;
      examples.style.height = '0px';
      examples.style.padding = '0 24px';
      requestAnimationFrame(function() {
        examples.style.transition = 'opacity 0.2s ease 0.1s, height 0.3s cubic-bezier(0.25, 0.1, 0.25, 1), padding 0.3s ease';
        examples.style.opacity = '1';
        examples.style.height = naturalH + 'px';
        examples.style.padding = '';
        examples.addEventListener('transitionend', function handler(e) {
          if (e.propertyName !== 'height') return;
          examples.style.height = '';
          examples.style.overflow = '';
          examples.style.transition = '';
          examples.removeEventListener('transitionend', handler);
        });
      });
    }

    // Step 3: After shrink, swap content (keep buttons)
    wrapper.addEventListener('transitionend', function handler(e) {
      if (e.target !== wrapper || e.propertyName !== 'height') return;
      wrapper.removeEventListener('transitionend', handler);

      wrapper.style.height = '';
      wrapper.style.overflow = '';
      wrapper.style.transition = '';
      unpinBottomRow();



      // Remove form content (not permanent elements)
      var toRemove = wrapper.querySelectorAll('.fn-form-header, .fn-form-body');
      toRemove.forEach(function(el) { el.remove(); });

      // Restore original content (before bottomRow)
      var br = wrapper.querySelector('.input-bottom-row');
      _inputContentOriginal.forEach(function(el) { wrapper.insertBefore(el, br); });

      wrapper.className = 'input-wrapper';
      _fnFormActive = false;
      delete wrapper.dataset.fnName;
      // Clear inline overrides from close animation
      var br2 = wrapper.querySelector('.input-bottom-row');
      if (br2) { br2.style.borderTopColor = ''; br2.style.transition = ''; }
      var ctx2 = wrapper.querySelector('.context-stats-label');
      if (ctx2) ctx2.style.marginRight = '';

      // Restore send button for chat mode
      sendBtn.style.bottom = ''; // clear inline, let CSS take over
      sendBtn.setAttribute('onclick', 'onSendBtnClick()');
      sendBtn.title = 'Send message';

      // Re-bind
      var chatInput = document.getElementById('chatInput');
      if (chatInput) {
        chatInput.addEventListener('keydown', function(e) {
          if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
        });
        chatInput.addEventListener('input', function() { autoResize(chatInput); });
      }
      if (typeof buildThinkingMenu === 'function') buildThinkingMenu();
    });
  });
}

function toggleBool(paramName, value, btnEl) {
  var container = document.getElementById('fnField_' + paramName);
  if (!container) return;
  container.dataset.value = value;
  var btns = container.querySelectorAll('.fn-form-toggle-btn');
  for (var i = 0; i < btns.length; i++) btns[i].classList.remove('active');
  btnEl.classList.add('active');
}

function selectOption(paramName, value, chipEl) {
  var container = document.getElementById('fnField_' + paramName);
  if (!container) return;
  container.dataset.value = value;
  var chips = container.querySelectorAll('.fn-form-option-chip');
  for (var i = 0; i < chips.length; i++) chips[i].classList.remove('active');
  chipEl.classList.add('active');
  var customInput = container.querySelector('.fn-form-option-custom');
  if (customInput) customInput.value = '';
}

function selectOptionCustom(paramName, inputEl) {
  var container = document.getElementById('fnField_' + paramName);
  if (!container) return;
  var val = inputEl.value.trim();
  if (val) {
    container.dataset.value = val;
    var chips = container.querySelectorAll('.fn-form-option-chip');
    for (var i = 0; i < chips.length; i++) chips[i].classList.remove('active');
  }
}

function submitFnForm(fnName) {
  if (isRunning) return;
  var fn = availableFunctions.find(function(f) { return f.name === fnName; });
  if (!fn) return;

  // work_dir is always required — it's a runtime-level setting, not a param.
  var workdirEl = document.getElementById('fnField_work_dir');
  var workdirVal = workdirEl ? workdirEl.value.trim() : '';
  if (!workdirVal) {
    if (workdirEl) {
      workdirEl.classList.add('workdir-input-error');
      workdirEl.focus();
    }
    return;
  }

  var params = (fn.params_detail || []).filter(function(p) {
    if (p.name === 'runtime' || p.name === 'callback' || p.name === 'exec_runtime' || p.name === 'review_runtime') return false;
    if (p.hidden) return false;
    return true;
  });

  var parts = ['run', fnName];
  for (var i = 0; i < params.length; i++) {
    var p = params[i];
    var el = document.getElementById('fnField_' + p.name);
    if (!el) continue;

    var val;
    if (el.dataset.value !== undefined) {
      val = el.dataset.value;
    } else {
      val = el.value.trim();
    }

    if (!val && !p.required) continue;
    if (!val && p.required) {
      el.style.borderColor = 'var(--accent-red)';
      if (el.focus) el.focus();
      return;
    }
    if (val.indexOf(' ') !== -1 || val.indexOf('"') !== -1) {
      parts.push(p.name + '=' + JSON.stringify(val));
    } else {
      parts.push(p.name + '=' + val);
    }
  }

  // Append work_dir last so user-facing command text keeps function params first.
  if (workdirVal.indexOf(' ') !== -1 || workdirVal.indexOf('"') !== -1) {
    parts.push('work_dir=' + JSON.stringify(workdirVal));
  } else {
    parts.push('work_dir=' + workdirVal);
  }

  var command = parts.join(' ');
  closeFnForm();
  sendMessage(command);
}

// ===== Sidebar section toggles (shared across all pages) =====

