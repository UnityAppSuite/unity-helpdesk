import path from "path";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
      vue: path.resolve(__dirname, "../desk/node_modules/vue"),
      "vue-router": path.resolve(__dirname, "../desk/node_modules/vue-router"),
    },
  },
  build: {
    outDir: "../helpdesk/public/unity_desk",
    emptyOutDir: true,
    target: "es2021",
    sourcemap: true,
  },
});
