import type { NextConfig } from "next";

const api = (process.env.ARSUNE_API_URL || "http://127.0.0.1:8540").replace(
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
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
};

export default config;
