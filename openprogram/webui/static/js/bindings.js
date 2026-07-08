// bindings.js — legacy shim.
//
// Earlier versions attached/detached channels directly to
// conversations. The new multi-agent model routes messages at the
// binding layer (channel, account, peer) → agent, not per-conv. All
// the old UI lives in agents.js now. We keep this file only so the
// <script src=".../bindings.js"> tag in index.html doesn't 404 until
// the HTML is updated; the functions below are stubs kept to prevent
// ReferenceErrors from any stale caller that still references them.

function renderChannelBadge() { /* removed: no conv-level binding */ }
function handleChannelBindingChanged() { /* removed */ }
function openChannelBindingMenu() {
  alert('Channel bindings moved to `openprogram channels bindings`. ' +
        'Each binding routes (channel, account, peer) → agent.');
}
