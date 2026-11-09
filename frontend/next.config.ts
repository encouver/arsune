import type { NextConfig } from "next";

const api = (process.env.ARSUNE_API_URL || "http://127.0.0.1:8540").replace(
  /\/$/,
  "",
);

const swarm = (process.env.ARSUNE_SWARM_URL || "http://127.0.0.1:8590").replace(
  /\/$/,
  "",
);

const config: NextConfig = {
  output: "standalone",
  experimental: {
    // JSON can expand escaped source text to six times its original size.
    proxyClientMaxBodySize: 60 * 1024 * 1024 + 64 * 1024,
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${api}/api/:path*` },
      { source: "/swarm-state", destination: `${swarm}/state` },
    ];
  },
};

export default config;
