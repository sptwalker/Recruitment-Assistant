import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 后端 API 前缀经 dev 代理转发到 FastAPI（localhost:8000），前端与 API 同源，
// 免去 CORS / 跨站 cookie 问题。生产由 nginx 做同样反代（见 deploy/）。
const API_PREFIXES = ["/auth", "/candidates", "/jobs", "/logs", "/healthz"];

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      API_PREFIXES.map((p) => [p, { target: "http://localhost:8000", changeOrigin: true }])
    ),
  },
});
