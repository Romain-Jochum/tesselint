// Vite config (docs/ui/DESIGN.md §5). `npm run build` passes --outDir ../src/tesselint/server/static.
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const API = "http://127.0.0.1:8765"; // `tesselint serve` default (DESIGN §4.1)

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // LocalGuard checks Host and Origin (DESIGN §4.1), so both are rewritten to the server's.
      // POSTs also need the token that `serve` injects into its own index.html.
      "/api": { target: API, changeOrigin: true, headers: { origin: API } },
    },
  },
  build: {
    sourcemap: false,
    assetsDir: "assets",
  },
});
