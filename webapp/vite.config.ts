import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Host dev: localhost. Docker dev (compose override): service names on the compose network.
const apiProxy = process.env.VITE_DEV_API_PROXY ?? "http://localhost:2706";
const basicAuthUser = process.env.BASIC_AUTH_USER;
const basicAuthPass = process.env.BASIC_AUTH_PASS;

const basicAuthPlugin =
  basicAuthUser && basicAuthPass
    ? {
        name: "basic-auth",
        configureServer(server: import("vite").ViteDevServer) {
          const expected =
            "Basic " +
            Buffer.from(`${basicAuthUser}:${basicAuthPass}`).toString("base64");
          server.middlewares.use((req, res, next) => {
            // Signed download links sent to Telegram have to open on a phone
            // that has no credentials; the HMAC in the path is the
            // authentication there. Same exemption as the /api/public/
            // location block in webapp/nginx.conf, which serves prod.
            if (req.url?.startsWith("/api/public/")) return next();
            if (req.headers.authorization === expected) return next();
            res.setHeader("WWW-Authenticate", 'Basic realm="Restricted"');
            res.statusCode = 401;
            res.end("Unauthorized");
          });
        },
      }
    : null;

export default defineConfig({
  plugins: [react(), ...(basicAuthPlugin ? [basicAuthPlugin] : [])],
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
