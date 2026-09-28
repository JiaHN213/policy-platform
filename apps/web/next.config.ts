import type { NextConfig } from "next";

const config: NextConfig = {
  output: "standalone",
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  // Django's login URLs retain trailing slashes behind the same-origin proxy.
  skipTrailingSlashRedirect: true,
  async rewrites() {
    const backend = process.env.API_INTERNAL_URL || "http://127.0.0.1:8000";
    return [
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
      { source: "/django-admin/:path*", destination: `${backend}/django-admin/:path*/` },
      { source: "/static/:path*", destination: `${backend}/static/:path*` },
    ];
  },
};
export default config;
