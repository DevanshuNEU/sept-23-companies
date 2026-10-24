// Evaluates the old dashboard's data.js in an empty sandbox and prints what it defines as JSON.
//
//   node data_js_dump.mjs <path to data.js> [timeout in ms]
//
// The file is run with only `window` defined, so it has no require, process or fetch. Code generation from strings
// is off, so `this.constructor.constructor('return process')()` does not get out either. This script only reads the
// one file it is given and prints to stdout: it writes no file and opens no connection.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const path = process.argv[2];
const timeout = Number(process.argv[3] || 5000);
if (!path) {
  console.error("usage: node data_js_dump.mjs <path to data.js> [timeout in ms]");
  process.exit(2);
}

const window = {};
vm.runInNewContext(readFileSync(path, "utf8"), { window }, {
  filename: "data.js",
  timeout,
  contextCodeGeneration: { strings: false, wasm: false },
});

const { COMPANIES, RULED_OUT, META, REFERENCE } = window;
process.stdout.write(JSON.stringify({ COMPANIES, RULED_OUT, META, REFERENCE }));
