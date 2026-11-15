"use strict";
const theme = new URLSearchParams(location.search).get("theme");
if (theme === "dark" || theme === "light") document.documentElement.dataset.theme = theme;

const shell = window.openprogramShell;
const notice = document.getElementById("notice");
const list = document.getElementById("versions");

const TEXT = {
  failed: {
    title: "OpenProgram can't start right now",
    copy: "The newest version and the one before it both failed to start, so OpenProgram stopped trying. Your conversations and files are safe.",
  },
  menu: {
    title: "Go back to a previous version",
    copy: "OpenProgram is running, but you can switch to a version kept on this computer if something isn't working.",
  },
  shift: {
    title: "Go back to a previous version",
    copy: "You held Shift while opening OpenProgram, so it stopped here. Pick a version kept on this computer, or pick the current one to carry on as usual.",
  },
};

function say(message) {
  notice.hidden = !message;
  notice.textContent = message || "";
}

function when(text) {
  const moment = new Date(text);
  if (Number.isNaN(moment.getTime())) return "";
  const day = moment.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const time = moment.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return `${day}, ${time}`;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function setBusy(busy) {
  for (const button of document.querySelectorAll("button")) button.disabled = busy;
}

function versionRow(version, reason) {
  const row = element("div", "row");
  const content = element("div", "field-content");
  const title = element("div", "field-title", `Version ${version.number}`);
  if (version.current && reason === "menu") title.append(element("span", "badge badge-default", "Running now"));
  else if (version.failed) title.append(element("span", "badge badge-destructive", "Didn't start"));
  else if (version.current) title.append(element("span", "badge badge-default", "Current"));
  content.append(title);
  content.append(element("div", "field-desc", [(version.summary || "").replace(/[.\s]+$/, ""), when(version.builtAt)].filter(Boolean).join(". ")));
  row.append(content);
  if (version.current && reason === "menu") return row;
  const controls = element("div", "controls");
  const button = element("button", "btn btn-elevated", "Use this version");
  button.type = "button";
  button.addEventListener("click", async () => {
    setBusy(true);
    say("");
    button.textContent = "Switching...";
    const result = await shell.useVersion(version.id);
    if (!result.ok) {
      say(result.message);
      button.textContent = "Use this version";
      setBusy(false);
    }
  });
  controls.append(button);
  row.append(controls);
  return row;
}

async function load() {
  const state = await shell.state();
  const text = TEXT[state.reason] || TEXT.failed;
  document.getElementById("title").textContent = text.title;
  document.getElementById("copy").textContent = text.copy;
  document.getElementById("log-path").textContent = state.logPath;
  list.replaceChildren(...state.versions.map((version) => versionRow(version, state.reason)));
  if (!state.versions.length) {
    const row = element("div", "row");
    row.append(element("div", "field-desc", "No versions this App can start are kept on this computer."));
    list.replaceChildren(row);
  }
}

const copyLog = document.getElementById("copy-log");
copyLog.addEventListener("click", async () => {
  await shell.copyLogPath();
  copyLog.textContent = "Copied";
  setTimeout(() => { copyLog.textContent = "Copy path"; }, 1500);
});

const restore = document.getElementById("restore");
restore.addEventListener("click", async () => {
  setBusy(true);
  say("");
  restore.textContent = "Restoring...";
  const result = await shell.restoreBuiltIn();
  say(result.message);
  if (!result.ok) {
    restore.textContent = "Restore";
    setBusy(false);
  }
});

void load();
