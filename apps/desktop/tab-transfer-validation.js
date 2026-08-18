const { Buffer } = require("buffer");

function isPlainObject(value) {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function boundedString(value, field, maxBytes, required = false) {
  if (value === undefined || value === null) {
    if (required) throw new TypeError(`${field} is required`);
    return;
  }
  if (
    typeof value !== "string"
    || (required && !value)
    || Buffer.byteLength(value, "utf8") > maxBytes
  ) {
    throw new TypeError(`${field} is invalid or too large`);
  }
}

function serializedBytes(value, field) {
  let encoded;
  try {
    encoded = JSON.stringify(value);
  } catch (_error) {
    throw new TypeError(`${field} must be serializable`);
  }
  if (encoded === undefined) throw new TypeError(`${field} must be serializable`);
  return Buffer.byteLength(encoded, "utf8");
}

const TRANSFER_PAYLOAD_MAX_BYTES = 20 * 1024 * 1024;
const FILE_DRAFT_MAX_COUNT = 3;
const FILE_DRAFT_MAX_BYTES = 2 * 1024 * 1024;
const FILE_DRAFTS_MAX_TOTAL_BYTES = 6 * 1024 * 1024;

function optionalBoolean(value, field) {
  if (value === undefined) return;
  if (typeof value !== "boolean") throw new TypeError(`${field} must be boolean`);
  return value;
}

function normalizedComposerSettings(value, field) {
  if (value === undefined) return;
  if (!isPlainObject(value)) throw new TypeError(`${field} must be an object`);
  const normalized = {};
  for (const key of ["thinking", "permission_mode"]) {
    boundedString(value[key], `${field}.${key}`, 16 * 1024);
    if (value[key] !== undefined && value[key] !== null) normalized[key] = value[key];
  }
  for (const key of ["tools", "webSearch", "fast", "unattended"]) {
    const item = optionalBoolean(value[key], `${field}.${key}`);
    if (item !== undefined) normalized[key] = item;
  }
  return normalized;
}

function normalizedDraftChannelChoice(value, field) {
  if (value === undefined) return;
  if (!isPlainObject(value)) throw new TypeError(`${field} must be an object`);
  const normalized = {};
  for (const key of ["channel", "account_id"]) {
    boundedString(value[key], `${field}.${key}`, 16 * 1024);
    if (value[key] !== undefined) normalized[key] = value[key];
  }
  return normalized;
}

function uniqueBoundedIds(value, field, { min = 0, max = 3 } = {}) {
  if (!Array.isArray(value) || value.length < min || value.length > max) {
    throw new TypeError(`${field} must contain ${min}-${max} ids`);
  }
  const ids = [];
  const seen = new Set();
  for (const id of value) {
    boundedString(id, field, 4 * 1024, true);
    if (seen.has(id)) throw new TypeError(`${field} contains duplicate ids`);
    seen.add(id);
    ids.push(id);
  }
  return ids;
}

function validateTransferPayload(ctx, value) {
  if (!ctx || !isPlainObject(value)) {
    throw new TypeError("Transfer payload must be an object");
  }
  if (serializedBytes(value, "Transfer payload") > TRANSFER_PAYLOAD_MAX_BYTES) {
    throw new TypeError("Transfer payload is too large");
  }
  if (!Array.isArray(value.tabs) || value.tabs.length < 1 || value.tabs.length > 3) {
    throw new TypeError("Transfer payload requires one to three tabs");
  }

  const validKinds = new Set(["session", "file", "web", "ntp"]);
  const tabs = [];
  const ids = [];
  const seen = new Set();
  for (const tab of value.tabs) {
    if (!isPlainObject(tab) || !validKinds.has(tab.kind)) {
      throw new TypeError("Transfer payload contains an invalid tab");
    }
    boundedString(tab.id, "tab.id", 4 * 1024, true);
    boundedString(tab.title, "tab.title", 4 * 1024);
    for (const field of ["url", "path", "projectId", "sessionId"]) {
      boundedString(tab[field], `tab.${field}`, 16 * 1024);
    }
    if (seen.has(tab.id)) throw new TypeError("Transfer tab ids must be unique");
    seen.add(tab.id);
    ids.push(tab.id);
    const normalized = { id: tab.id, kind: tab.kind };
    if (tab.title !== undefined && tab.title !== null) normalized.title = tab.title;
    for (const field of ["url", "path", "projectId", "sessionId"]) {
      if (tab[field] !== undefined && tab[field] !== null) normalized[field] = tab[field];
    }
    for (const field of ["draft", "dirty"]) {
      const item = optionalBoolean(tab[field], `tab.${field}`);
      if (item !== undefined) normalized[field] = item;
    }
    tabs.push(normalized);
  }

  if (!isPlainObject(value.source)) {
    throw new TypeError("Transfer payload requires source metadata");
  }
  const sourceKind = value.source.kind;
  if (!new Set(["tab", "segment", "group"]).has(sourceKind)) {
    throw new TypeError("Transfer source kind is invalid");
  }
  const source = { windowId: ctx.id, kind: sourceKind };
  if (sourceKind === "tab" && ids.length !== 1) {
    throw new TypeError("A normal tab transfer contains exactly one tab");
  }
  if (sourceKind === "segment") {
    if (ids.length !== 1) {
      throw new TypeError("A segment transfer contains exactly one tab");
    }
    boundedString(value.source.groupId, "source.groupId", 4 * 1024, true);
    if (!Number.isInteger(value.source.memberIndex) || value.source.memberIndex < 0) {
      throw new TypeError("Segment memberIndex is invalid");
    }
  }
  if (sourceKind === "group") {
    boundedString(value.source.groupId, "source.groupId", 4 * 1024, true);
  }
  if (sourceKind === "segment" || sourceKind === "group") {
    const memberIds = uniqueBoundedIds(value.source.memberIds, "source.memberIds", {
      min: 2,
      max: 3,
    });
    const visibleIds = uniqueBoundedIds(value.source.visibleIds, "source.visibleIds", {
      min: 1,
      max: 2,
    });
    boundedString(value.source.focusedId, "source.focusedId", 4 * 1024, true);
    if (visibleIds.some((id) => !memberIds.includes(id))) {
      throw new TypeError("Visible group ids must be members");
    }
    if (!visibleIds.includes(value.source.focusedId)) {
      throw new TypeError("Focused group id must be visible");
    }
    if (sourceKind === "group") {
      if (
        memberIds.length !== ids.length
        || memberIds.some((id, index) => id !== ids[index])
      ) {
        throw new TypeError("Group metadata must match transferred tabs");
      }
    } else if (
      value.source.memberIndex >= memberIds.length
      || memberIds[value.source.memberIndex] !== ids[0]
    ) {
      throw new TypeError("Segment metadata must identify the transferred tab");
    }
    source.groupId = value.source.groupId;
    source.memberIds = memberIds;
    source.visibleIds = visibleIds;
    source.focusedId = value.source.focusedId;
    if (sourceKind === "segment") source.memberIndex = value.source.memberIndex;
  }

  const rawFileDrafts = value.fileDrafts ?? [];
  if (!Array.isArray(rawFileDrafts) || rawFileDrafts.length > FILE_DRAFT_MAX_COUNT) {
    throw new TypeError("fileDrafts must contain at most three entries");
  }
  const fileDrafts = [];
  const fileDraftKeys = new Set();
  let fileDraftBytes = 0;
  for (const draft of rawFileDrafts) {
    if (!isPlainObject(draft)) throw new TypeError("Invalid file draft");
    boundedString(draft.key, "fileDraft.key", 16 * 1024, true);
    if (fileDraftKeys.has(draft.key)) throw new TypeError("Duplicate file draft key");
    fileDraftKeys.add(draft.key);
    let normalizedValue;
    if (typeof draft.value === "string") {
      normalizedValue = draft.value;
    } else if (isPlainObject(draft.value)) {
      if (
        typeof draft.value.draft !== "string"
        || typeof draft.value.baselineContent !== "string"
        || !Number.isFinite(draft.value.baselineMtime)
      ) {
        throw new TypeError("Invalid fileDraft.value");
      }
      normalizedValue = {
        draft: draft.value.draft,
        baselineContent: draft.value.baselineContent,
        baselineMtime: draft.value.baselineMtime,
      };
    } else {
      throw new TypeError("Invalid fileDraft.value");
    }
    const draftBytes = typeof normalizedValue === "string"
      ? Buffer.byteLength(normalizedValue, "utf8")
      : serializedBytes(normalizedValue, "fileDraft.value");
    if (draftBytes > FILE_DRAFT_MAX_BYTES) {
      throw new TypeError("fileDraft.value is too large");
    }
    fileDraftBytes += serializedBytes(normalizedValue, "fileDraft.value");
    if (fileDraftBytes > FILE_DRAFTS_MAX_TOTAL_BYTES) {
      throw new TypeError("fileDrafts are too large");
    }
    fileDrafts.push({ key: draft.key, value: normalizedValue });
  }

  const rawChats = value.chats ?? [];
  if (!Array.isArray(rawChats) || rawChats.length > 3) {
    throw new TypeError("chats must be an array with at most three entries");
  }
  const chats = [];
  for (const chat of rawChats) {
    if (!isPlainObject(chat)) throw new TypeError("Invalid chat transfer state");
    boundedString(chat.chatKey, "chat.chatKey", 16 * 1024, true);
    boundedString(chat.pendingProjectId, "chat.pendingProjectId", 16 * 1024);
    for (const field of ["composerDraft", "activeComposerInput"]) {
      boundedString(chat[field], `chat.${field}`, 2 * 1024 * 1024);
    }
    const normalized = { chatKey: chat.chatKey };
    for (const field of ["composerDraft", "activeComposerInput", "pendingProjectId"]) {
      if (chat[field] !== undefined && chat[field] !== null) normalized[field] = chat[field];
    }
    for (const field of ["composerSettings", "activeComposerSettings"]) {
      const item = normalizedComposerSettings(chat[field], `chat.${field}`);
      if (item !== undefined) normalized[field] = item;
    }
    const choice = normalizedDraftChannelChoice(
      chat.draftChannelChoice,
      "chat.draftChannelChoice",
    );
    if (choice !== undefined) normalized.draftChannelChoice = choice;
    const wasActive = optionalBoolean(chat.wasActive, "chat.wasActive");
    if (wasActive !== undefined) normalized.wasActive = wasActive;
    chats.push(normalized);
  }

  const records = [];
  for (const tab of tabs) {
    if (tab.kind !== "web") continue;
    const record = ctx.views.get(tab.id);
    if (!record) continue;
    if (record.ownerId !== ctx.id) {
      throw new TypeError("Native web view is owned by another window");
    }
    records.push(record);
  }

  const payload = { tabs, source, fileDrafts, chats };
  return { payload, records };
}

module.exports = { validateTransferPayload };
