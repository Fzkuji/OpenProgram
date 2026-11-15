import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import ts from "typescript";

registerHooks({
  resolve(specifier, context, nextResolve) {
    // The avatar package's index also exports the picker; these tests need the picture alone.
    if (specifier === "@/components/avatar") return nextResolve(new URL("../../components/avatar/avatar.tsx", import.meta.url).href, context);
    if (specifier === "@/lib/session-store") {
      return { url: `data:text/javascript,${encodeURIComponent("export const useSessionStore = (pick) => pick(globalThis.__sessions ?? { composerSettingsBySession: {}, conversations: {}, boundAgentBySession: {} });")}`, shortCircuit: true };
    }
    if (specifier === "@/lib/agents/agents-query") {
      return { url: `data:text/javascript,${encodeURIComponent("export const useAgents = () => ({ data: globalThis.__agents });")}`, shortCircuit: true };
    }
    if (specifier.startsWith("@/")) return nextResolve(new URL(`../../${specifier.slice(2)}.ts`, import.meta.url).href, context);
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) return nextResolve(`${specifier}.ts`, context);
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (!url.endsWith(".tsx")) return nextLoad(url, context);
    const source = ts.transpileModule(readFileSync(new URL(url), "utf8"), {
      compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    return { format: "module", shortCircuit: true, source };
  },
});

const {
  BOT_SHAPES, BOT_COLORS, BOT_COLOR_IDS, BOT_EXTENTS, BOT_MAX_SIDE, BOT_OPTICAL_SIDE, DEFAULT_BOT_SHAPE, botFit, botMarkFit, isBotShape, isBotColor,
} = await import("../../components/avatar/bot/catalog.ts");
const { botLookFor } = await import("../../components/avatar/bot/identity.ts");
const { loadBotRuntime, loadedBotRuntime } = await import("../../components/avatar/bot/runtime.ts");
const { agentAvatarConfig, agentCharacterShape, AGENT_AVATAR_UPLOAD_MAX_BYTES } = await import("../../lib/agents/agent-avatar.ts");
const { Avatar } = await import("../../components/avatar/avatar.tsx");
const { AgentAvatar, AgentAvatarById, useConversationAgentShape } = await import("../../components/agents/agent-avatar.tsx");
const { createElement } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { renderAvatarSvg, validateAvatar } = await import("open-bot-avatar");
// The package exports no path helpers; the file is the renderer's own geometry.
const { bounds, flattenPath, parsePath } = await import(new URL("../../../../node_modules/open-bot-avatar/dist/path.js", import.meta.url).href);

const BUILT_IN_AGENTS = [
  "main", "butler", "utility", "planner", "decision", "chief-of-staff", "spec-writer", "bug-reproducer",
  "pr-producer", "pr-verifier", "research-desk", "inbox-triage", "career-scout", "knowledge-keeper", "writer",
];

test("the renderer loads on first use and is shared afterwards", async () => {
  assert.equal(loadedBotRuntime(), undefined);
  const pending = loadBotRuntime();
  assert.equal(loadBotRuntime(), pending);
  const runtime = await pending;
  assert.equal(loadedBotRuntime(), runtime);
  assert.equal(await loadBotRuntime(), runtime);
});

test("every shape on offer has its own valid drawing", async () => {
  const runtime = await loadBotRuntime();
  assert.equal(new Set(BOT_SHAPES).size, BOT_SHAPES.length);
  for (const shape of BOT_SHAPES) {
    const definition = runtime.definition(shape);
    assert.equal(definition.id, shape);
    assert.equal(validateAvatar(definition).ok, true, shape);
    assert.match(renderAvatarSvg(definition, { color: BOT_COLORS.blue }), /^<svg[\s\S]*<\/svg>$/, shape);
  }
});

test("each character's extent table matches its drawing", async () => {
  const runtime = await loadBotRuntime();
  assert.deepEqual(Object.keys(BOT_EXTENTS).sort(), [...BOT_SHAPES].sort());
  for (const shape of BOT_SHAPES) {
    const drawn = bounds(flattenPath(parsePath(runtime.definition(shape).body.path)));
    const [x0, y0, x1, y1] = BOT_EXTENTS[shape];
    for (const [table, actual] of [[x0, drawn.minX], [y0, drawn.minY], [x1, drawn.maxX], [y1, drawn.maxY]]) {
      assert.ok(Math.abs(table - actual) <= 0.5, `${shape}: ${table} vs ${actual}`);
    }
  }
});

test("every character fills its slot about as much as an icon, centred", () => {
  for (const shape of BOT_SHAPES) {
    const { scale, left, top } = botFit(shape);
    const [x0, y0, x1, y1] = BOT_EXTENTS[shape];
    const width = (scale * (x1 - x0)) / 100;
    const height = (scale * (y1 - y0)) / 100;
    assert.ok(Math.max(width, height) <= BOT_MAX_SIDE + 1e-9, shape);
    assert.ok(Math.sqrt(width * height) <= BOT_OPTICAL_SIDE + 1e-9, shape);
    // Short of the mean only where the longest side is at its limit.
    assert.ok(Math.sqrt(width * height) > BOT_OPTICAL_SIDE - 1e-9 || Math.max(width, height) > BOT_MAX_SIDE - 1e-9, shape);
    assert.ok(Math.abs(left + (scale * x0) / 100 + width / 2 - 0.5) < 1e-9, shape);
    assert.ok(Math.abs(top + (scale * y0) / 100 + height / 2 - 0.5) < 1e-9, shape);
  }
  // Main's cloud, short and wide, is drawn larger than its box, not as a 13 x 8px strip.
  const cloud = botFit("cloud");
  assert.ok(cloud.scale * 0.82 * 16 >= 14.5 && cloud.scale * 0.52 * 16 >= 9);
  assert.deepEqual(botFit("hologram"), botFit(DEFAULT_BOT_SHAPE));
});

test("a shape that is not on offer draws the default one", async () => {
  const runtime = await loadBotRuntime();
  assert.equal(runtime.definition("hologram").id, DEFAULT_BOT_SHAPE);
  assert.equal(isBotShape("hologram"), false);
  assert.equal(isBotShape("rocket"), true);
});

test("colours are named, and each name is one fixed hex colour", () => {
  assert.ok(BOT_COLOR_IDS.length >= 6);
  for (const id of BOT_COLOR_IDS) assert.match(BOT_COLORS[id], /^#[0-9a-f]{6}$/);
  assert.equal(new Set(Object.values(BOT_COLORS)).size, BOT_COLOR_IDS.length);
  assert.equal(isBotColor("teal"), true);
  assert.equal(isBotColor("#14b8a6"), false);
  assert.equal(isBotColor("toString"), false);
});

test("an id always gets the same character, and ids spread over the catalogue", () => {
  const ids = Array.from({ length: 400 }, (_, index) => `agent-${index}`);
  for (const id of ids) {
    const look = botLookFor(id);
    assert.deepEqual(botLookFor(id), look);
    assert.ok(isBotShape(look.shape) && isBotColor(look.color), id);
  }
  assert.equal(new Set(ids.map((id) => botLookFor(id).shape)).size, BOT_SHAPES.length);
  assert.equal(new Set(ids.map((id) => botLookFor(id).color)).size, BOT_COLOR_IDS.length);
  // An id named like an Object member is still just an id.
  for (const id of ["constructor", "toString", "__proto__"]) assert.ok(isBotShape(botLookFor(id).shape));
});

test("the built-in Agents each wear a character of their own", () => {
  const looks = BUILT_IN_AGENTS.map((id) => botLookFor(id));
  for (const look of looks) assert.ok(isBotShape(look.shape) && isBotColor(look.color));
  assert.equal(new Set(looks.map((look) => look.shape)).size, BUILT_IN_AGENTS.length);
  assert.deepEqual(botLookFor("main"), { shape: "cloud", color: "blue" });
});

test("an Agent without an avatar is pictured by its id", () => {
  assert.deepEqual(agentAvatarConfig({ id: "scout" }), { kind: "bot", seed: "scout" });
  assert.deepEqual(agentAvatarConfig({ id: "scout", avatar: {} }), { kind: "bot", seed: "scout" });
  assert.deepEqual(agentAvatarConfig({ id: "scout", avatar: null }), { kind: "bot", seed: "scout" });
  // An upload whose picture is gone has nothing to show.
  assert.deepEqual(agentAvatarConfig({ id: "scout", avatar: { kind: "upload" } }), { kind: "bot", seed: "scout" });
});

test("a saved avatar is shown as saved, seeded by the Agent's id where it has no seed", () => {
  assert.deepEqual(
    agentAvatarConfig({ id: "scout", avatar: { kind: "bot", shape: "ghost", color: "pink" } }),
    { kind: "bot", shape: "ghost", color: "pink", seed: "scout" },
  );
  assert.deepEqual(
    agentAvatarConfig({ id: "scout", avatar: { kind: "dicebear", style: "bottts", seed: "Atlas" } }),
    { kind: "dicebear", style: "bottts", seed: "Atlas" },
  );
  const picture = { kind: "upload", file: "data:image/png;base64,AA" };
  assert.deepEqual(agentAvatarConfig({ id: "scout", avatar: picture }), picture);
  assert.equal(AGENT_AVATAR_UPLOAD_MAX_BYTES, 256 * 1024);
});

const markup = (props) => renderToStaticMarkup(createElement(Avatar, props));

test("a character avatar renders its own host at the asked size, not the generated picture", () => {
  const html = markup({ name: "Scout", size: 32, config: { kind: "bot", shape: "rocket", color: "green" } });
  assert.match(html, /^<span[^>]*data-bot-shape="rocket"/);
  assert.match(html, /width:32px;height:32px/);
  assert.doesNotMatch(html, /<img|border-radius/);
  // Until the renderer has prepared its shape (and on the server) the
  // character is its flat glyph in its own colours, in the body's box.
  const standIn = html.match(/<svg viewBox="0 0 100 100" style="([^"]*)" aria-hidden="true" data-bot-stand-in="">(.*?)<\/svg>/);
  assert.ok(standIn, html);
  assert.match(standIn[2], new RegExp(`fill="${BOT_COLORS.green}"`));
  assert.match(standIn[2], /fill="#ffffff"/);
  assert.doesNotMatch(html, /data-bot-live/);
  assert.match(markup({ name: "Scout", live: true, config: { kind: "bot", shape: "rocket" } }), /data-bot-live=""/);
  // The renderer draws into the body's box: scaled and placed by botFit.
  const fit = botFit("rocket");
  const body = html.match(/<span style="([^"]*)" data-bot-body="">/);
  assert.ok(body, html);
  assert.equal(body[1], `position:absolute;left:${fit.left * 32}px;top:${fit.top * 32}px;width:${fit.scale * 32}px;height:${fit.scale * 32}px`);
});

test("a character in a control is placed by the brand-mark rule", () => {
  const html = renderToStaticMarkup(createElement(AgentAvatar, { agent: { id: "a", avatar: { kind: "bot", shape: "cloud" } }, size: 16, fit: "mark" }));
  const fit = botMarkFit("cloud");
  assert.notDeepEqual(fit, botFit("cloud"));
  const body = html.match(/<span style="([^"]*)" data-bot-body="">/);
  assert.ok(body, html);
  assert.equal(body[1], `position:absolute;left:${fit.left * 16}px;top:${fit.top * 16}px;width:${fit.scale * 16}px;height:${fit.scale * 16}px`);
});

test("a character avatar without a usable shape takes the one its seed gives", () => {
  const derived = botLookFor("pr-verifier").shape;
  assert.match(markup({ name: "Verifier", config: { kind: "bot", seed: "pr-verifier" } }), new RegExp(`data-bot-shape="${derived}"`));
  assert.match(markup({ name: "Verifier", config: { kind: "bot", seed: "pr-verifier", shape: "hologram" } }), new RegExp(`data-bot-shape="${derived}"`));
  // No seed: the name stands in.
  assert.match(markup({ name: "pr-verifier", config: { kind: "bot" } }), new RegExp(`data-bot-shape="${derived}"`));
});

test("an Agent's character is alive wherever it stands for the Agent, unless a caller turns it off", () => {
  const agent = { id: "scout", name: "Scout", avatar: { kind: "bot", shape: "ghost", color: "pink" } };
  const alive = renderToStaticMarkup(createElement(AgentAvatar, { agent, size: 16 }));
  assert.match(alive, /data-bot-shape="ghost"/);
  assert.match(alive, /data-bot-live=""/);
  assert.doesNotMatch(renderToStaticMarkup(createElement(AgentAvatar, { agent, size: 16, live: false })), /data-bot-live/);
});

test("a place that holds only an id shows the saved avatar once the list has it, the id's character until then", () => {
  globalThis.__agents = undefined;
  const derived = botLookFor("scout").shape;
  assert.match(renderToStaticMarkup(createElement(AgentAvatarById, { agentId: "scout", size: 16 })), new RegExp(`data-bot-shape="${derived}"[^>]*data-bot-live=""|data-bot-live=""[^>]*data-bot-shape="${derived}"`));
  globalThis.__agents = [{ id: "scout", name: "Scout", avatar: { kind: "bot", shape: "ghost", color: "pink" } }];
  assert.match(renderToStaticMarkup(createElement(AgentAvatarById, { agentId: "scout", size: 16 })), /data-bot-shape="ghost"/);
  assert.match(renderToStaticMarkup(createElement(AgentAvatarById, { agentId: "other", size: 16 })), new RegExp(`data-bot-shape="${botLookFor("other").shape}"`));
});

test("a live character looks around on its own and never follows the pointer", () => {
  const source = readFileSync(new URL("../../components/avatar/bot/bot-avatar.ts", import.meta.url), "utf8");
  assert.match(source, /followPointer: false/);
  // Its look comes from its own wander, never from the pointer.
  assert.match(source, /startWander\(/);
  assert.doesNotMatch(source, /pointerGaze|pointermove|getBoundingClientRect/);
});

test("a flat glyph stands for an Agent by its saved character, else by the one its id gives", () => {
  assert.equal(agentCharacterShape({ id: "scout", avatar: { kind: "bot", shape: "ghost", color: "pink" } }), "ghost");
  assert.equal(agentCharacterShape({ id: "scout" }), botLookFor("scout").shape);
  // A picture is not a character: the id's character stands in.
  assert.equal(agentCharacterShape({ id: "scout", avatar: { kind: "upload", file: "data:image/png;base64,AA" } }), botLookFor("scout").shape);
  assert.equal(agentCharacterShape({ id: "scout", avatar: { kind: "bot", shape: "hologram" } }), botLookFor("scout").shape);
});

test("a conversation's character is its Agent's, else the default Agent's", () => {
  const shapeOf = (sessionId) => {
    let shape;
    function Probe() { shape = useConversationAgentShape(sessionId); return null; }
    renderToStaticMarkup(createElement(Probe));
    return shape;
  };
  globalThis.__agents = [
    { id: "main", default: true, avatar: { kind: "bot", shape: "rocket" } },
    { id: "scout", avatar: { kind: "bot", shape: "ghost" } },
  ];
  globalThis.__sessions = { composerSettingsBySession: {}, conversations: { s1: { agent_id: "scout" } }, boundAgentBySession: { s2: "other" } };
  assert.equal(shapeOf("s1"), "ghost");
  assert.equal(shapeOf("s2"), botLookFor("other").shape);
  assert.equal(shapeOf("s3"), "rocket");
  globalThis.__agents = undefined;
  assert.equal(shapeOf(null), botLookFor("main").shape);
  globalThis.__sessions = undefined;
});
