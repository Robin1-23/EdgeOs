import type { NextConfig } from "next";

// The browser calls /api/* on this origin; Next forwards it to FastAPI (no CORS, one origin).
const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
  },
};

export default nextConfig;
