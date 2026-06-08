import fs from "fs";
import path from "path";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// Frappe serves the SPA shell at /unity-helpdesk from
// helpdesk/www/unity_helpdesk/index.html, but Vite emits the hashed-asset
// index.html into the public/ outDir. The two must stay in lockstep — if the
// www copy points at an asset hash that a later rebuild replaced, the browser
// 404s the entry bundle and the whole app renders blank.
//
// package.json chains `&& yarn copy-html-entry` after the build to do this, but
// that step is skipped whenever someone runs `vite build` directly (or the
// chain breaks). Doing the copy inside the build's own closeBundle hook makes it
// unconditional: every successful build syncs www, no matter how it was invoked.
const OUT_DIR = path.resolve(__dirname, "../helpdesk/public/unity_helpdesk");
const WWW_INDEX = path.resolve(
  __dirname,
  "../helpdesk/www/unity_helpdesk/index.html"
);

function syncWwwIndexHtml() {
  return {
    name: "unity-helpdesk-sync-www-index",
    closeBundle() {
      // Use console.* rather than the Rollup plugin-context logger: this/info
      // isn't reliably available inside the closeBundle hook across the
      // vite-bundled Rollup versions, and throwing here would fail the build
      // *after* assets are already emitted.
      const src = path.join(OUT_DIR, "index.html");
      if (!fs.existsSync(src)) {
        console.warn(
          `[unity-helpdesk-sync-www-index] index.html not found at ${src}; skipped www sync`
        );
        return;
      }
      fs.mkdirSync(path.dirname(WWW_INDEX), { recursive: true });
      fs.copyFileSync(src, WWW_INDEX);
      console.log(
        `[unity-helpdesk-sync-www-index] synced www entry -> ${WWW_INDEX}`
      );
    },
  };
}

export default defineConfig({
  plugins: [vue(), syncWwwIndexHtml()],
  resolve: {
    alias: {
      "@desk": path.resolve(__dirname, "../desk/src"),
      "@": path.resolve(__dirname, "src"),
      vue: path.resolve(__dirname, "../desk/node_modules/vue"),
      "vue-router": path.resolve(__dirname, "../desk/node_modules/vue-router"),
    },
  },
  build: {
    outDir: "../helpdesk/public/unity_helpdesk",
    emptyOutDir: true,
    target: "es2021",
    sourcemap: true,
  },
});
