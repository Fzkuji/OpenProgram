// Settings > Devices: a device has one connection, rendered from its method's
// settings schema as the worker sends it; the composer offers only devices
// commands run on that are switched on, and secrets go back as the marker
// when left untouched.
import assert from "node:assert/strict";
import { describe, test } from "node:test";

const form = await import("../../../lib/machines/machine-form.ts");

// Methods as `Kind.public()` sends them; the names are only test data, the module names none.
const KINDS = [
  { name: "local", label: "This computer", settings: { type: "object", properties: {} }, local_only: true, paired: false },
  { name: "ssh", label: "SSH host", label_zh: "SSH 主机", local_only: false, paired: false,
    settings: { type: "object", required: ["target"], description: "Uses your ssh config.", "x-zh": { description: "使用你的 ssh 配置。" },
      properties: {
      target: { type: "string", title: "SSH target", pattern: "^[^-\\s]\\S*$", description: "user@host", examples: ["me@devbox"],
        "x-zh": { title: "SSH 目标", description: "用户@主机" } },
      workdir: { type: "string", title: "Remote folder", pattern: "^[/~]", examples: ["/home/me/work"] },
    } } },
  { name: "box", label: "Box", local_only: false, paired: false,
    settings: { type: "object", properties: { image: { type: "string", title: "Image", default: "ubuntu:24.04" } } } },
  { name: "paired", label: "Paired OpenProgram", local_only: false, paired: true,
    settings: { type: "object", properties: { url: { type: "string" } } } },
  { name: "pixels", label: "Pixel server", local_only: false, paired: false,
    settings: { type: "object", required: ["url"], properties: {
      url: { type: "string", title: "Server address", examples: ["http://192.168.64.2:5000"] },
      password: { type: "string", title: "Password", secret: true },
      port: { type: "integer", title: "Port", default: 5900 },
      shared: { type: "boolean", title: "Shared session" },
      mode: { type: "string", title: "Mode", enum: ["fast", "exact"] },
    } } },
];

const conn = (kind, label, where, extra = {}) => ({ kind, label, where, known: true, ...extra });
const SHELL = { remote_files: false, instance_scope: "machine" };

describe("which devices are environments", () => {
  const local = { id: "local", name: "This computer", enabled: true, connection: conn("local", "This computer", ""), shell: SHELL };
  const devbox = { id: "devbox", name: "Dev box", enabled: true, connection: conn("ssh", "SSH host", "me@devbox"),
    shell: { remote_files: true, instance_scope: "machine" } };
  const lab = { id: "lab", name: "Lab", enabled: true, connection: conn("pixels", "Pixel server", "http://x") };
  const off = { ...devbox, id: "off", enabled: false };
  const stranger = { id: "odd", name: "Odd", enabled: true, connection: { kind: "teleport", label: "teleport", where: "teleport", known: false } };

  test("the composer lists only devices commands run on that are switched on, in order", () => {
    assert.deepEqual(form.environmentMachines([local, devbox, lab, off, stranger]).map((m) => m.id), ["local", "devbox"]);
  });

  test("a device that cannot hold a project says why", () => {
    assert.equal(form.notAnEnvironment(devbox), "");
    assert.equal(form.notAnEnvironment(lab), "no_shell");
    assert.equal(form.notAnEnvironment(off), "off");
    assert.equal(form.notAnEnvironment(stranger), "unknown_shell");
  });

  test("a connection whose method is unknown marks the device as not editable", () => {
    assert.equal(form.hasUnknownConnection(stranger), true);
    assert.equal(form.hasUnknownConnection(devbox), false);
  });

  test("another computer's OpenProgram is told by its connection method", () => {
    assert.equal(form.isRemoteOpenProgram({ ...lab, connection: conn("openprogram", "OpenProgram", "http://x") }), true);
    assert.equal(form.isRemoteOpenProgram(devbox), false);
  });

  test("the connection reads <label> · <where>, the label in the page's language", () => {
    assert.equal(form.connectionSummary(devbox.connection, KINDS, "en"), "SSH host · me@devbox");
    assert.equal(form.connectionSummary(devbox.connection, KINDS, "zh"), "SSH 主机 · me@devbox");
    // A method without Chinese text, or one this installation does not know, keeps its English label.
    assert.equal(form.connectionSummary(lab.connection, KINDS, "zh"), "Pixel server · http://x");
    assert.equal(form.connectionSummary(stranger.connection, KINDS, "zh"), "teleport · teleport");
    assert.equal(form.connectionSummary(conn("x", "Viewer", "")), "Viewer");
  });
});

describe("the form follows the method's schema", () => {
  test("only methods set up by hand are offered: not this computer, not a paired one", () => {
    assert.deepEqual(form.kindOptions(KINDS).map((k) => k.name), ["ssh", "box", "pixels"]);
  });

  test("each property becomes a field with its title, hint, example, default and rules", () => {
    const fields = form.settingFields(form.findKind(KINDS, "pixels"));
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
    const target = form.settingFields(form.findKind(KINDS, "ssh"))[0];
    assert.equal(target.hint, "user@host");
    assert.equal(target.pattern, "^[^-\\s]\\S*$");
  });

  test("in Chinese a method, its picker hint and its fields read the Chinese the worker sends", () => {
    const ssh = form.findKind(KINDS, "ssh");
    assert.equal(form.kindLabel(ssh, "zh"), "SSH 主机");
    assert.equal(form.kindLabel(ssh, "en"), "SSH host");
    assert.equal(form.kindHint(ssh, "zh"), "使用你的 ssh 配置。");
    assert.equal(form.kindHint(ssh, "en"), "Uses your ssh config.");
    const [target, workdir] = form.settingFields(ssh, "zh");
    assert.deepEqual([target.label, target.hint], ["SSH 目标", "用户@主机"]);
    assert.equal(workdir.label, "Remote folder");
    assert.equal(form.settingFields(ssh, "en")[0].label, "SSH target");
    const draft = { ...form.emptyDraft(), id: "devbox", connection: { kind: "ssh", values: {}, held: [] } };
    assert.equal(form.draftProblems(draft, KINDS, [], "zh")[0].label, "SSH 目标");
  });

  test("picking a method fills its defaults", () => {
    assert.deepEqual(form.connectionDraft(KINDS, "box").values, { image: "ubuntu:24.04" });
    assert.deepEqual(form.connectionDraft(KINDS, "pixels").values, { port: "5900", shared: false });
    assert.equal(form.connectionDraft(KINDS, "").kind, "");
  });

  test("required, pattern, number and a free id are checked before sending", () => {
    const draft = { ...form.emptyDraft(), id: "devbox", connection: { kind: "ssh", values: { target: "-oProxy", workdir: "rel" }, held: [] } };
    assert.deepEqual(form.draftProblems(draft, KINDS, ["local", "devbox"]).map((p) => [p.field, p.problem]), [
      ["id", "taken"], ["connection.target", "pattern"], ["connection.workdir", "pattern"],
    ]);
    const empty = { ...form.emptyDraft(), id: "lab" };
    assert.deepEqual(form.draftProblems(empty, KINDS, []).map((p) => p.field), ["connection"]);
    const pixels = { ...form.emptyDraft(), id: "lab", connection: { kind: "pixels", values: { url: "", port: "59.5" }, held: [] } };
    assert.deepEqual(form.draftProblems(pixels, KINDS, []).map((p) => [p.field, p.problem]), [
      ["connection.url", "required"], ["connection.port", "integer"],
    ]);
  });

  test("a new device is sent as its connection, settings typed by the schema", () => {
    const draft = { ...form.emptyDraft(), id: " Lab ", name: " Lab VM ",
      connection: { kind: "pixels", values: { url: " http://10.0.0.2:5000 ", password: "pw", port: "5901", shared: true, mode: "" }, held: [] } };
    assert.deepEqual(form.draftPayload(draft, KINDS), {
      id: "lab", name: "Lab VM",
      connection: { kind: "pixels", url: "http://10.0.0.2:5000", password: "pw", port: 5901, shared: true },
    });
  });

  test("a new device may leave its id to the worker, which makes one from the name", () => {
    const connection = { kind: "ssh", values: { target: "me@devbox" }, held: [] };
    const named = { ...form.emptyDraft(), name: "Dev box", connection };
    assert.deepEqual(form.draftProblems(named, KINDS, ["local"]), []);
    assert.deepEqual(form.draftPayload(named, KINDS), { name: "Dev box", connection: { kind: "ssh", target: "me@devbox" } });
    const nameless = { ...form.emptyDraft(), connection };
    assert.deepEqual(form.draftProblems(nameless, KINDS, []).map((p) => [p.field, p.problem]), [["id", "required"]]);
  });
});

describe("editing", () => {
  const stored = {
    id: "lab", name: "Lab VM", enabled: false,
    connection: { kind: "pixels", url: "http://10.0.0.2:5000", password: { secret: true }, port: 5900,
      label: "Pixel server", where: "Pixel server at http://10.0.0.2:5000", known: true },
  };

  test("a stored device fills the form; its secret is held, never shown", () => {
    const draft = form.draftFromMachine(stored);
    assert.equal(draft.editing, true);
    assert.deepEqual(draft.connection, { kind: "pixels", values: { url: "http://10.0.0.2:5000", port: "5900" }, held: ["password"] });
  });

  test("an untouched secret goes back as the marker; a switched-off device stays off", () => {
    const draft = form.draftFromMachine(stored);
    assert.deepEqual(form.draftPayload(draft, KINDS), {
      id: "lab", name: "Lab VM", enabled: false,
      connection: { kind: "pixels", url: "http://10.0.0.2:5000", password: { secret: true }, port: 5900 },
    });
    const typed = { ...draft, connection: { ...draft.connection, values: { ...draft.connection.values, password: "new" } } };
    assert.equal(form.draftPayload(typed, KINDS).connection.password, "new");
  });

  test("Save sends the switch as the row has it now, not as it was when Edit was pressed", () => {
    const draft = form.draftFromMachine(stored);
    assert.equal(form.draftPayload(form.withEnabled(draft, [{ ...stored, enabled: true }]), KINDS).enabled, undefined);
    const on = form.draftFromMachine({ ...stored, enabled: true });
    assert.equal(form.draftPayload(form.withEnabled(on, [stored]), KINDS).enabled, false);
    // A new device has no row yet.
    assert.equal(form.withEnabled({ ...form.emptyDraft(), id: "lab" }, [stored]).enabled, true);
  });

  test("a held secret satisfies a required field", () => {
    const kinds = KINDS.map((k) => k.name === "pixels"
      ? { ...k, settings: { ...k.settings, required: ["url", "password"] } } : k);
    assert.deepEqual(form.draftProblems(form.draftFromMachine(stored), kinds, ["lab"]), []);
  });
});
