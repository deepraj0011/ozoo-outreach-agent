import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const target = "http://127.0.0.1:8000";
const outDir = fileURLToPath(new URL("../src/outreach/webapp", import.meta.url));

// The build output is not committed: keep a .gitignore in it that ignores everything else.
function keepGitignore(): Plugin {
  return {
    name: "keep-gitignore",
    apply: "build",
    closeBundle() {
      writeFileSync(`${outDir}/.gitignore`, "*\n!.gitignore\n");
    },
  };
}

export default defineConfig({
  base: "/app/",
  plugins: [react(), keepGitignore()],
  build: { outDir, emptyOutDir: true, chunkSizeWarningLimit: 1500 },
  server: {
    port: 5173,
    proxy: {
      "/demo": { target, changeOrigin: false },
      "/img": { target, changeOrigin: false },
    },
  },
});
