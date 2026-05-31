import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Host dev: localhost. Docker dev (compose override): service names on the compose network.
const apiProxy = process.env.VITE_DEV_API_PROXY ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    // Dev only — production nginx proxies /api (see webapp/nginx.conf).
    proxy: {
      "/api": { target: apiProxy, changeOrigin: true },
    },
    allowedHosts: [".trycloudflare.com"],
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
