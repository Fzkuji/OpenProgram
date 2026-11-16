module.exports = async function checkUnloadConfirmation(t, dialog) {
  const win = t.fakeWindow(951);
  const ctx = t.registerContext("unload-window", win);
  const record = t.hooks.ensureView(ctx, "unload-page", "https://site.test/");
  const controlled = t.generatedNativeRecords.at(-1);
  controlled.controls[0].resolve();
  await record.navigation?.promise;
  const original = dialog.showMessageBoxSync;
  const prompts = [];
  let choice = 0;
  let duringPrompt = () => {};
  dialog.showMessageBoxSync = (owner, options) => {
    prompts.push({ owner, options });
    duringPrompt();
    return choice;
  };
  function requestUnload() {
    let allowed = false;
    controlled.emitWebContents("will-prevent-unload", { preventDefault() { allowed = true; } });
    return allowed;
  }
  const focusedBefore = t.focusedWindow;
  try {
    // Nobody is looking at the page: an Agent's navigation goes on without a
    // dialog that would bring the App in front of what the user is doing.
    t.assert.equal(requestUnload(), true, "an unwatched page leaves without asking");
    t.assert.equal(prompts.length, 0, "an unwatched page shows no confirmation");
    record.nativeVisible = true;
    t.focusedWindow = win;
    t.assert.equal(requestUnload(), false, "Stay must preserve the page");
    t.assert.equal(prompts.length, 1, "blocked navigation must show a confirmation");
    t.assert.strictEqual(prompts[0].owner, win);
    t.assert.equal(prompts[0].options.defaultId, 0);
    t.assert.equal(prompts[0].options.cancelId, 0);
    choice = 1;
    t.assert.equal(requestUnload(), true, "only explicit Leave may permit unload");
    choice = -1;
    t.assert.equal(requestUnload(), false, "dismissal must preserve the page");
    choice = 1;
    duringPrompt = () => t.assert.equal(requestUnload(), false, "no nested confirmation");
    t.assert.equal(requestUnload(), true);
    t.assert.equal(prompts.length, 4);
    duringPrompt = () => { throw new Error("dialog unavailable"); };
    t.assert.equal(requestUnload(), false, "dialog failure must preserve the page");
    duringPrompt = () => {};
    const nextWin = t.fakeWindow(952);
    const nextCtx = t.registerContext("unload-next-window", nextWin);
    ctx.views.delete(record.id);
    record.ownerId = nextCtx.id;
    nextCtx.views.set(record.id, record);
    t.focusedWindow = nextWin;
    t.assert.equal(requestUnload(), true);
    t.assert.strictEqual(prompts.at(-1).owner, nextWin, "confirmation follows current ownership");
    duringPrompt = () => nextCtx.views.delete(record.id);
    t.assert.equal(requestUnload(), false, "a stale dialog cannot authorize a detached page");
    const count = prompts.length;
    t.assert.equal(requestUnload(), false);
    t.assert.equal(prompts.length, count, "no dialog without an owner");
    t.hooks.windows.delete(nextCtx.id);
  } finally {
    t.focusedWindow = focusedBefore;
    dialog.showMessageBoxSync = original;
    controlled.emitWebContents("destroyed");
    t.hooks.windows.delete(ctx.id);
  }
};
