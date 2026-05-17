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
      // Strip the /novnc prefix so websockify (which serves /usr/share/novnc
      // at the root) finds vnc.html. Production nginx achieves the same via
      // `proxy_pass http://novnc:6080/;` with a trailing slash.
      "/novnc": {
        target: novncProxy,
        changeOrigin: true,
        ws: true,
        rewrite: (p) => p.replace(/^\/novnc/, ""),
      },
      "/websockify": { target: novncProxy, changeOrigin: true, ws: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
