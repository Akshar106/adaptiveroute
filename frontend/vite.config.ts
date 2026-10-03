import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

// In production FastAPI serves frontend/dist from the same origin as the API.
// In development the Vite server proxies API paths to the local backend
// (override with API_PROXY_TARGET=http://localhost:8001 npm run dev).
export default defineConfig(({ mode }) => {
  const backend = loadEnv(mode, ".", "API_PROXY_TARGET").API_PROXY_TARGET ?? "http://localhost:8000";
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/v1": backend,
        "/healthz": backend,
        "/readyz": backend,
        "/docs": backend,
        "/openapi.json": backend, // loaded by the Swagger UI at /docs
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
    },
  };
});
