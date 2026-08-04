// esbuild config for the trainer bundle. Banner marks the output as generated.
// This is an .mjs file, so it MUST use ESM `export default`, not `module.exports`
// (a CommonJS `module.exports` in an .mjs throws at load). (Fable review.)
export default {
  banner: {
    js: "/* Built artifact — do not edit. Single source of truth: trainer.src.mjs\n * (UI/effects) + ../sudo/craft.sudo (kernel, transpiled via sudoc). */",
  },
};
