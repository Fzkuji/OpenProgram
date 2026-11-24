// Settings > Machines: the form is rendered from each kind's settings schema
// as the worker sends it, the composer offers only machines with a shell that
// are switched on, and secrets go back as the marker when left untouched.
import assert from "node:assert/strict";
import { describe, test } from "node:test";

const form = await import("../../../lib/machines/machine-form.ts");

// Kinds as `Kind.public()` sends them; the names are only test data, the module names none.
const KINDS = [
  { name: "local", connection: "shell", label: "This computer", settings: { type: "object", properties: {} },
    capabilities: [], local_only: true, instance_scope: "machine" },
  { name: "ssh", connection: "shell", label: "SSH host", label_zh: "SSH 主机", capabilities: [], local_only: false, instance_scope: "machine",
    settings: { type: "object", required: ["target"], description: "Uses your ssh config.", "x-zh": { description: "使用你的 ssh 配置。" },
      properties: {
      target: { type: "string", title: "SSH target", pattern: "^[^-\\s]\\S*$", description: "user@host", examples: ["me@devbox"],
        "x-zh": { title: "SSH 目标", description: "用户@主机" } },
      workdir: { type: "string", title: "Remote folder", pattern: "^[/~]", examples: ["/home/me/work"] },
    } } },
  { name: "box", connection: "shell", label: "Box", capabilities: [], local_only: false, instance_scope: "conversation",
    settings: { type: "object", properties: { image: { type: "string", title: "Image", default: "ubuntu:24.04" } } } },
  { name: "desktop", connection: "window", label: "This computer's desktop", settings: { type: "object", properties: {} },
    capabilities: ["tree"], local_only: true, instance_scope: "machine" },
  { name: "desktop", connection: "screen", label: "This computer's desktop", settings: { type: "object", properties: {} },
    capabilities: ["pixels"], local_only: true, instance_scope: "machine" },
  { name: "pixels", connection: "screen", label: "Pixel server", capabilities: ["pixels"], local_only: false, instance_scope: "machine",
    settings: { type: "object", required: ["url"], properties: {
      url: { type: "string", title: "Server address", examples: ["http://192.168.64.2:5000"] },
      password: { type: "string", title: "Password", secret: true },
      port: { type: "integer", title: "Port", default: 5900 },
      shared: { type: "boolean", title: "Shared session" },
      mode: { type: "string", title: "Mode", enum: ["fast", "exact"] },
    } } },
];

const conn = (kind, label, where, extra = {}) => ({ kind, label, where, known: true, ...extra });

describe("which machines are environments", () => {
  const local = { id: "local", name: "This computer", enabled: true, shell: conn("local", "This computer", "This computer") };
  const devbox = { id: "devbox", name: "Dev box", enabled: true, shell: conn("ssh", "SSH host", "me@devbox") };
  const lab = { id: "lab", name: "Lab", enabled: true, screen: conn("pixels", "Pixel server", "http://x") };
  const off = { ...devbox, id: "off", enabled: false };
  const stranger = { id: "odd", name: "Odd", enabled: true, shell: { kind: "teleport", label: "teleport", where: "teleport", known: false } };

  test("the composer lists only machines with a known shell that are switched on, in order", () => {
    assert.deepEqual(form.environmentMachines([local, devbox, lab, off, stranger]).map((m) => m.id), ["local", "devbox"]);
  });

  test("a machine that cannot be the default environment says why", () => {
    assert.equal(form.notAnEnvironment(devbox), "");
    assert.equal(form.notAnEnvironment(lab), "no_shell");
    assert.equal(form.notAnEnvironment(off), "off");
    assert.equal(form.notAnEnvironment(stranger), "unknown_shell");
  });

  test("a connection whose kind is unknown marks the machine as not editable", () => {
    assert.equal(form.hasUnknownConnection(stranger), true);
    assert.equal(form.hasUnknownConnection(devbox), false);
  });

  test("one line per connection reads <label> · <where>, the label in the page's language", () => {
    assert.equal(form.connectionSummary(devbox.shell, KINDS, "en", "shell"), "SSH host · me@devbox");
    assert.equal(form.connectionSummary(devbox.shell, KINDS, "zh", "shell"), "SSH 主机 · me@devbox");
    // A kind without Chinese text, or one this installation does not know, keeps its English label.
    assert.equal(form.connectionSummary(lab.screen, KINDS, "zh", "screen"), "Pixel server · http://x");
    assert.equal(form.connectionSummary(stranger.shell, KINDS, "zh", "shell"), "teleport · teleport");
    assert.equal(form.connectionSummary(conn("x", "Viewer", "")), "Viewer");
    assert.deepEqual(form.machineConnections({ ...devbox, screen: lab.screen }).map(([name]) => name), ["shell", "screen"]);
  });
});

describe("the form follows the kind's schema", () => {
  test("kinds only this computer has are offered only for it", () => {
    assert.deepEqual(form.kindOptions(KINDS, "shell", false).map((k) => k.name), ["ssh", "box"]);
    assert.deepEqual(form.kindOptions(KINDS, "screen", true).map((k) => k.name), ["desktop", "pixels"]);
  });

  test("each property becomes a field with its title, hint, example, default and rules", () => {
    const fields = form.settingFields(form.findKind(KINDS, "screen", "pixels"));
    assert.deepEqual(fields.map((f) => [f.key, f.type]), [
      ["url", "text"], ["password", "text"], ["port", "integer"], ["shared", "boolean"], ["mode", "choice"],
    ]);
    const [url, password, port, , mode] = fields;
    assert.equal(url.label, "Server address");
    assert.equal(url.placeholder, "http://192.168.64.2:5000");
    assert.equal(url.required, true);
    assert.equal(password.secret, true);
    assert.equal(port.default, "5900");
    assert.deepEqual(mode.choices, ["fast", "exact"]);
    const target = form.settingFields(form.findKind(KINDS, "shell", "ssh"))[0];
    assert.equal(target.hint, "user@host");
    assert.equal(target.pattern, "^[^-\\s]\\S*$");
  });

  test("in Chinese a kind, its picker hint and its fields read the Chinese the worker sends", () => {
    const ssh = form.findKind(KINDS, "shell", "ssh");
    assert.equal(form.kindLabel(ssh, "zh"), "SSH 主机");
    assert.equal(form.kindLabel(ssh, "en"), "SSH host");
    assert.equal(form.kindHint(ssh, "zh"), "使用你的 ssh 配置。");
    assert.equal(form.kindHint(ssh, "en"), "Uses your ssh config.");
    const [target, workdir] = form.settingFields(ssh, "zh");
    assert.deepEqual([target.label, target.hint], ["SSH 目标", "用户@主机"]);
    assert.equal(workdir.label, "Remote folder");
    assert.equal(form.settingFields(ssh, "en")[0].label, "SSH target");
    const draft = { ...form.emptyDraft(), id: "devbox", shell: { kind: "ssh", values: {}, held: [] } };
    assert.equal(form.draftProblems(draft, KINDS, [], "zh")[0].label, "SSH 目标");
  });

  test("picking a kind fills its defaults", () => {
    assert.deepEqual(form.connectionDraft(KINDS, "shell", "box").values, { image: "ubuntu:24.04" });
    assert.deepEqual(form.connectionDraft(KINDS, "screen", "pixels").values, { port: "5900", shared: false });
    assert.equal(form.connectionDraft(KINDS, "shell", "").kind, "");
  });

  test("required, pattern, number and a free id are checked before sending", () => {
    const draft = { ...form.emptyDraft(), id: "devbox", shell: { kind: "ssh", values: { target: "-oProxy", workdir: "rel" }, held: [] } };
    assert.deepEqual(form.draftProblems(draft, KINDS, ["local", "devbox"]).map((p) => [p.field, p.problem]), [
      ["id", "taken"], ["shell.target", "pattern"], ["shell.workdir", "pattern"],
    ]);
    const empty = { ...form.emptyDraft(), id: "lab" };
    assert.deepEqual(form.draftProblems(empty, KINDS, []).map((p) => p.field), ["connections"]);
    const screen = { ...form.emptyDraft(), id: "lab", screen: { kind: "pixels", values: { url: "", port: "59.5" }, held: [] } };
    assert.deepEqual(form.draftProblems(screen, KINDS, []).map((p) => [p.field, p.problem]), [
      ["screen.url", "required"], ["screen.port", "integer"],
    ]);
  });

  test("a new machine is sent as its connections, settings typed by the schema", () => {
    const draft = { ...form.emptyDraft(), id: " Lab ", name: " Lab VM ",
      screen: { kind: "pixels", values: { url: " http://10.0.0.2:5000 ", password: "pw", port: "5901", shared: true, mode: "" }, held: [] } };
    assert.deepEqual(form.draftPayload(draft, KINDS), {
      id: "lab", name: "Lab VM",
      screen: { kind: "pixels", url: "http://10.0.0.2:5000", password: "pw", port: 5901, shared: true },
    });
  });

  test("a new machine may leave its id to the worker, which makes one from the name", () => {
    const shell = { kind: "ssh", values: { target: "me@devbox" }, held: [] };
    const named = { ...form.emptyDraft(), name: "Dev box", shell };
    assert.deepEqual(form.draftProblems(named, KINDS, ["local"]), []);
    assert.deepEqual(form.draftPayload(named, KINDS), { name: "Dev box", shell: { kind: "ssh", target: "me@devbox" } });
    const nameless = { ...form.emptyDraft(), shell };
    assert.deepEqual(form.draftProblems(nameless, KINDS, []).map((p) => [p.field, p.problem]), [["id", "required"]]);
  });
});

describe("editing", () => {
  const stored = {
    id: "lab", name: "Lab VM", enabled: false,
    screen: { kind: "pixels", url: "http://10.0.0.2:5000", password: { secret: true }, port: 5900,
      label: "Pixel server", where: "Pixel server at http://10.0.0.2:5000", known: true },
  };

  test("a stored machine fills the form; its secret is held, never shown", () => {
    const draft = form.draftFromMachine(stored);
    assert.equal(draft.editing, true);
    assert.equal(draft.local, false);
    assert.deepEqual(draft.screen, { kind: "pixels", values: { url: "http://10.0.0.2:5000", port: "5900" }, held: ["password"] });
    assert.equal(draft.shell.kind, "");
  });

  test("an untouched secret goes back as the marker; a switched-off machine stays off", () => {
    const draft = form.draftFromMachine(stored);
    assert.deepEqual(form.draftPayload(draft, KINDS), {
      id: "lab", name: "Lab VM", enabled: false,
      screen: { kind: "pixels", url: "http://10.0.0.2:5000", password: { secret: true }, port: 5900 },
    });
    const typed = { ...draft, screen: { ...draft.screen, values: { ...draft.screen.values, password: "new" } } };
    assert.equal(form.draftPayload(typed, KINDS).screen.password, "new");
  });

  test("Save sends the switch as the row has it now, not as it was when Edit was pressed", () => {
    const draft = form.draftFromMachine(stored);
    assert.equal(form.draftPayload(form.withEnabled(draft, [{ ...stored, enabled: true }]), KINDS).enabled, undefined);
    const on = form.draftFromMachine({ ...stored, enabled: true });
    assert.equal(form.draftPayload(form.withEnabled(on, [stored]), KINDS).enabled, false);
    // A new machine has no row yet.
    assert.equal(form.withEnabled({ ...form.emptyDraft(), id: "lab" }, [stored]).enabled, true);
  });

  test("a held secret satisfies a required field", () => {
    const kinds = KINDS.map((k) => k.name === "pixels"
      ? { ...k, settings: { ...k.settings, required: ["url", "password"] } } : k);
    assert.deepEqual(form.draftProblems(form.draftFromMachine(stored), kinds, ["lab"]), []);
  });

  test("this computer edits only its window, screen and browser", () => {
    const local = {
      id: "local", name: "This computer", enabled: true,
      shell: conn("local", "This computer", "This computer"),
      window: conn("desktop", "This computer's desktop", "This computer's desktop"),
    };
    const draft = form.draftFromMachine(local);
    assert.equal(draft.local, true);
    assert.deepEqual(form.draftProblems(draft, KINDS, ["local"]), []);
    assert.deepEqual(form.draftPayload(draft, KINDS),
      { id: "local", window: { kind: "desktop" }, screen: null, browser: null });
  });

  test("each connection offers only the kinds that serve it", () => {
    assert.deepEqual(form.CONNECTIONS, ["shell", "window", "screen", "browser"]);
    assert.deepEqual(form.kindOptions(KINDS, "window", true).map((k) => k.name), ["desktop"]);
    assert.deepEqual(form.kindOptions(KINDS, "window", false).map((k) => k.name), []);
    assert.deepEqual(form.kindOptions(KINDS, "screen", false).map((k) => k.name), ["pixels"]);
  });
});
