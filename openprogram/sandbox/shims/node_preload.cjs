"use strict";

// Convenience interception only. NODE_OPTIONS and this preload can be bypassed.
const childProcess = require("node:child_process");
const fs = require("node:fs");
const fsp = require("node:fs/promises");

const python = process.env.OPENPROGRAM_DELETE_PYTHON;
const enabled = python && process.env.OPENPROGRAM_RECOVERABLE_TRASH;

function remove(path, expect, missingOk = false) {
  if (typeof path !== "string") {
    const error = new TypeError("recoverable deletion only accepts string paths");
    error.code = "ERR_INVALID_ARG_TYPE";
    throw error;
  }
  const args = [
    "-m", "openprogram.sandbox.recoverable_delete", "delete", path, expect,
  ];
  if (missingOk) args.push("missing-ok");
  const env = { ...process.env, OPENPROGRAM_DELETE_HELPER: "1" };
  const result = childProcess.spawnSync(python, args, { encoding: "utf8", env });
  if (result.status !== 0) {
    const error = new Error((result.stderr || "recoverable deletion failed").trim());
    error.code = result.status === 2 ? "EINVAL" : "EIO";
    throw error;
  }
}

function callbackDelete(path, expect, missingOk, callback) {
  let error = null;
  try { remove(path, expect, missingOk); } catch (caught) { error = caught; }
  process.nextTick(callback, error);
}

if (enabled) {
  fs.unlinkSync = (path) => remove(path, "file");
  fs.unlink = (path, callback) => callbackDelete(path, "file", false, callback);
  fs.rmSync = (path, options = {}) => remove(path, options.recursive ? "any" : "file", !!options.force);
  fs.rm = (path, options, callback) => {
    if (typeof options === "function") { callback = options; options = {}; }
    options ||= {};
    callbackDelete(path, options.recursive ? "any" : "file", !!options.force, callback);
  };
  fs.rmdirSync = (path, options = {}) => remove(path, options.recursive ? "tree" : "empty_directory");
  fs.rmdir = (path, options, callback) => {
    if (typeof options === "function") { callback = options; options = {}; }
    options ||= {};
    callbackDelete(path, options.recursive ? "tree" : "empty_directory", false, callback);
  };

  const promiseMethods = {
    unlink: async (path) => remove(path, "file"),
    rm: async (path, options = {}) => remove(path, options.recursive ? "any" : "file", !!options.force),
    rmdir: async (path, options = {}) => remove(path, options.recursive ? "tree" : "empty_directory"),
  };
  Object.assign(fsp, promiseMethods);
  Object.assign(fs.promises, promiseMethods);
}
