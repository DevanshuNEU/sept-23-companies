// Evaluates the old dashboard's data.js in an empty sandbox and prints what it defines as JSON.
//
//   node data_js_dump.mjs <path to data.js> [timeout in ms]
//
// The file is run with only `window` defined, so it has no require, process or fetch. `window` is created inside
// the sandbox and code generation from strings is off, so `this.constructor.constructor('return process')()` and
// `window.constructor.constructor('return process')()` do not get out either. This script only reads the one file
// it is given and prints to stdout: it writes no file and opens no connection.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const path = process.argv[2];
const timeout = Number(process.argv[3] || 5000);
if (!path) {
  console.error("usage: node data_js_dump.mjs <path to data.js> [timeout in ms]");
  process.exit(2);
}

const options = { filename: "data.js", timeout };
const context = vm.createContext({}, { codeGeneration: { strings: false, wasm: false } });
vm.runInContext("var window = {};", context); // made inside the sandbox, so its constructor is the sandbox's own
vm.runInContext(readFileSync(path, "utf8"), context, options);
process.stdout.write(vm.runInContext(
  "JSON.stringify({ COMPANIES: window.COMPANIES, RULED_OUT: window.RULED_OUT, META: window.META, " +
    "REFERENCE: window.REFERENCE })",
  context,
  { filename: "dump.js", timeout },
));
