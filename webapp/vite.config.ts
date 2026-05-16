import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Host dev: localhost. Docker dev (compose override): service names on the compose network.
const apiProxy = process.env.VITE_DEV_API_PROXY ?? "http://localhost:8000";
const novncProxy = process.env.VITE_DEV_NOVNC_PROXY ?? "http://localhost:6080";

export default defineConfig({
  plugins: [react()],
  server: {
    // Dev only — production nginx proxies /api, /novnc, /websockify (see webapp/nginx.conf).
    proxy: {
      "/api": { target: apiProxy, changeOrigin: true },
      "/novnc": { target: novncProxy, changeOrigin: true, ws: true },
      "/websockify": { target: novncProxy, changeOrigin: true, ws: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
